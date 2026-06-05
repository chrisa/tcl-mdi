from __future__ import annotations

from tcl_lathe_hmi.backends.sim import SimBackend
from tcl_lathe_hmi.config import MachineConfig


def test_sim_backend_jog_updates_dro_after_idle():
    config = MachineConfig(sim_motion_time_s=0.01)
    backend = SimBackend(config)

    backend.connect()
    assert backend.poll().connected

    backend.jog_delta(x_mm=0.1, z_mm=-1.0, mode="feed", feed=100, slew=61)
    assert backend.poll().busy

    backend.wait_idle(timeout_ms=500)
    state = backend.poll()

    assert not state.busy
    assert state.x_mm == 0.1
    assert state.z_mm == -1.0
    assert state.x_counts == 10
    assert state.z_counts == -100


def test_sim_backend_spindle_ramps_toward_target():
    config = MachineConfig(
        sim_spindle_command_time_s=0.01,
        sim_spindle_ramp_rpm_per_s=100000.0,
    )
    backend = SimBackend(config)
    backend.connect()

    backend.set_spindle(on=True, rpm=1200, forward=False)
    backend.wait_idle(timeout_ms=500)
    state = backend.poll()

    assert state.spindle.commanded_on
    assert not state.spindle.forward
    assert state.spindle.target_rpm == 1200
    assert state.spindle.actual_rpm > 0

    backend.set_spindle(on=False)
    backend.wait_idle(timeout_ms=500)
    state = backend.poll()

    assert not state.spindle.commanded_on
    assert state.spindle.target_rpm == 0


def test_sim_backend_toolchanger_busy_for_station_change():
    config = MachineConfig(sim_tool_change_time_s=0.01)
    backend = SimBackend(config)
    backend.connect()

    assert backend.select_tool(current_station=1, target_station=4, slew=61)
    assert backend.poll().busy

    backend.wait_idle(timeout_ms=500)
    assert not backend.poll().busy

    assert not backend.select_tool(current_station=4, target_station=4, slew=61)
    assert not backend.poll().busy
