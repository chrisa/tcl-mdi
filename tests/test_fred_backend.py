from __future__ import annotations

from tcl_lathe_hmi.backends import fred as fred_module
from tcl_lathe_hmi.backends.fred import FredBackend
from tcl_lathe_hmi.config import MachineConfig


class FakeFredClient:
    instances: list["FakeFredClient"] = []

    def __init__(self, vid, pid, **kwargs):
        self.vid = vid
        self.pid = pid
        self.kwargs = kwargs
        self.polling = False
        self.closed = False
        self.idle = True
        self.error = False
        self.commands: list[tuple[str, dict[str, object]]] = []
        self.generation = 1
        self.snapshot = {
            "x_mm": 1.25,
            "z_mm": -2.5,
            "spindle_rpm": 950.0,
            "x_counts": 125,
            "z_counts": -250,
            "generation": self.generation,
        }
        FakeFredClient.instances.append(self)

    def enable_polling(self, *, period_ms, rpm_service):
        self.polling = True
        self.period_ms = period_ms
        self.rpm_service = rpm_service

    def disable_polling(self):
        self.polling = False

    def close(self):
        self.closed = True

    def refresh(self, timeout_ms=0):
        return self.snapshot

    def latest_snapshot(self):
        return self.snapshot

    def set_snapshot(self, **updates):
        self.generation += 1
        self.snapshot.update(updates)
        self.snapshot["generation"] = self.generation

    def controller_status(self):
        return {"idle": self.idle, "error": self.error}

    def rapid_move_delta(self, **kwargs):
        self.commands.append(("rapid", kwargs))
        self.idle = False
        return True

    def feed_move_delta(self, **kwargs):
        self.commands.append(("feed", kwargs))
        self.idle = False
        return True

    def set_spindle(self, **kwargs):
        self.commands.append(("spindle", kwargs))
        self.idle = False
        return True

    def change_tool(self, **kwargs):
        self.commands.append(("tool", kwargs))
        if kwargs["current_station"] == kwargs["target_station"]:
            return False
        self.idle = False
        return True

    def wait_idle(self, timeout_ms=None):
        self.idle = True


def test_fred_backend_connects_and_polls_snapshot():
    FakeFredClient.instances = []
    config = MachineConfig(fred_poll_period_ms=33)
    backend = FredBackend(config, client_factory=FakeFredClient)

    backend.connect()
    state = backend.poll()
    client = FakeFredClient.instances[-1]

    assert client.polling
    assert client.rpm_service == "remote"
    assert state.connected
    assert state.x_mm == 1.25
    assert state.z_mm == -2.5
    assert state.spindle.actual_rpm == 950.0


def test_fred_backend_waits_for_motion_target_feedback_after_controller_idle():
    FakeFredClient.instances = []
    backend = FredBackend(MachineConfig(), client_factory=FakeFredClient)
    backend.connect()
    client = FakeFredClient.instances[-1]

    backend.jog_delta(x_mm=0.1, z_mm=0.0, mode="feed", feed=120, slew=61)
    assert client.commands[-1] == (
        "feed",
        {"x_mm": 0.1, "z_mm": 0.0, "feed": 120, "slew": 61, "wait": False},
    )
    assert backend.poll().busy

    client.idle = True
    state = backend.poll()
    assert state.busy
    assert state.status_message == "fred: waiting for motion feedback"

    client.set_snapshot(x_mm=1.35, x_counts=135)
    state = backend.poll()
    assert not state.busy
    assert state.x_mm == 1.35


def test_fred_backend_waits_for_spindle_at_speed_after_controller_idle():
    FakeFredClient.instances = []
    backend = FredBackend(MachineConfig(), client_factory=FakeFredClient)
    backend.connect()
    client = FakeFredClient.instances[-1]

    backend.set_spindle(on=True, rpm=1600, forward=False)

    assert client.commands[-1] == (
        "spindle",
        {"on": True, "rpm": 1600.0, "forward": False, "wait": False},
    )
    assert backend.poll().busy

    client.idle = True
    state = backend.poll()
    assert state.busy
    assert state.status_message == "fred: waiting for spindle feedback"

    client.set_snapshot(spindle_rpm=1300.0)
    state = backend.poll()
    assert state.busy
    assert state.status_message == "fred: spindle ramping to speed"

    client.set_snapshot(spindle_rpm=1530.0)
    state = backend.poll()
    assert not state.busy
    assert state.spindle.at_speed


def test_fred_backend_waits_for_toolchanger_station_dwell_after_controller_idle(monkeypatch):
    FakeFredClient.instances = []
    now = 10.0
    monkeypatch.setattr(fred_module.time, "monotonic", lambda: now)
    config = MachineConfig(fred_tool_station_dwell_s=0.5)
    backend = FredBackend(config, client_factory=FakeFredClient)
    backend.connect()
    client = FakeFredClient.instances[-1]

    assert backend.select_tool(current_station=6, target_station=2, slew=61)

    assert client.commands[-1] == (
        "tool",
        {"current_station": 6, "target_station": 2, "slew": 61, "wait": False},
    )
    assert backend.poll().busy

    client.idle = True
    state = backend.poll()
    assert state.busy
    assert state.status_message == "fred: toolchanger settling"

    now = 11.99
    assert backend.poll().busy

    now = 12.0
    assert not backend.poll().busy


def test_fred_backend_skips_toolchanger_for_same_station():
    FakeFredClient.instances = []
    backend = FredBackend(MachineConfig(), client_factory=FakeFredClient)
    backend.connect()
    client = FakeFredClient.instances[-1]

    assert not backend.select_tool(current_station=3, target_station=3, slew=61)
    assert client.commands[-1] == (
        "tool",
        {"current_station": 3, "target_station": 3, "slew": 61, "wait": False},
    )
