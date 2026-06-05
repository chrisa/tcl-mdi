from __future__ import annotations

import time
from dataclasses import replace

from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.machine import BackendError, CommandRejectedError, MachineState, SpindleState


class SimBackend:
    name = "sim"

    def __init__(self, config: MachineConfig | None = None):
        self.config = config or MachineConfig()
        self._state = MachineState(status_message="sim: disconnected")
        self._busy_until = 0.0
        self._pending_position: tuple[float, float] | None = None
        self._last_poll = time.monotonic()

    def connect(self) -> None:
        self._state = replace(
            self._state,
            connected=True,
            busy=False,
            error=False,
            error_message="",
            status_message="sim: connected",
        )
        self._busy_until = 0.0
        self._pending_position = None
        self._last_poll = time.monotonic()

    def disconnect(self) -> None:
        self._state = MachineState(status_message="sim: disconnected")
        self._busy_until = 0.0
        self._pending_position = None

    def poll(self) -> MachineState:
        now = time.monotonic()
        dt = max(0.0, now - self._last_poll)
        self._last_poll = now

        if not self._state.connected:
            return self._state

        state = self._complete_motion_if_ready(now)
        state = self._ramp_spindle(state, dt)
        self._state = state
        return self._state

    def jog_delta(
        self,
        *,
        x_mm: float = 0.0,
        z_mm: float = 0.0,
        mode: str = "feed",
        feed: int = 100,
        slew: int = 61,
    ) -> None:
        self._require_ready()
        if mode not in {"feed", "rapid"}:
            raise CommandRejectedError(f"unsupported jog mode: {mode}")
        if x_mm == 0.0 and z_mm == 0.0:
            return

        self._pending_position = (self._state.x_mm + x_mm, self._state.z_mm + z_mm)
        self._busy_until = time.monotonic() + self.config.sim_motion_time_s
        self._state = replace(
            self._state,
            busy=True,
            status_message=f"sim: {mode} jog queued",
        )

    def set_spindle(self, *, on: bool, rpm: float = 0.0, forward: bool = True) -> None:
        self._require_ready()
        target_rpm = max(0.0, float(rpm)) if on else 0.0
        spindle = replace(
            self._state.spindle,
            commanded_on=on,
            forward=forward,
            target_rpm=target_rpm,
            at_speed=not on and self._state.spindle.actual_rpm == 0.0,
        )
        self._busy_until = time.monotonic() + self.config.sim_spindle_command_time_s
        self._state = replace(
            self._state,
            spindle=spindle,
            busy=True,
            status_message="sim: spindle command queued",
        )

    def select_tool(
        self,
        *,
        current_station: int,
        target_station: int,
        slew: int = 61,
    ) -> bool:
        self._require_ready()
        _validate_station(current_station, "current station")
        _validate_station(target_station, "target station")
        if current_station == target_station:
            self._state = replace(self._state, status_message="sim: tool already selected")
            return False

        self._busy_until = time.monotonic() + self.config.sim_tool_change_time_s
        self._state = replace(
            self._state,
            busy=True,
            status_message=f"sim: toolchanger P{current_station}->P{target_station} queued",
        )
        return True

    def wait_idle(self, timeout_ms: int | None = None) -> None:
        deadline = None
        if timeout_ms is not None:
            deadline = time.monotonic() + timeout_ms / 1000.0

        while self.poll().busy:
            if deadline is not None and time.monotonic() >= deadline:
                raise BackendError("sim: timeout waiting for idle")
            time.sleep(0.01)

    def force_error(self, message: str = "simulated error") -> None:
        self._state = replace(
            self._state,
            busy=False,
            error=True,
            error_message=message,
            status_message=message,
        )

    def _require_ready(self) -> None:
        if not self._state.connected:
            raise CommandRejectedError("sim: not connected")
        if self._state.error:
            raise CommandRejectedError(f"sim: error: {self._state.error_message}")
        if self.poll().busy:
            raise CommandRejectedError("sim: controller busy")

    def _complete_motion_if_ready(self, now: float) -> MachineState:
        if not self._state.busy or now < self._busy_until:
            return self._state

        state = replace(self._state, busy=False, status_message="sim: idle")
        if self._pending_position is not None:
            x_mm, z_mm = self._pending_position
            state = replace(
                state,
                x_mm=x_mm,
                z_mm=z_mm,
                x_counts=round(x_mm * self.config.x_counts_per_mm),
                z_counts=round(z_mm * self.config.z_counts_per_mm),
            )
            self._pending_position = None
        return state

    def _ramp_spindle(self, state: MachineState, dt: float) -> MachineState:
        spindle = state.spindle
        target = spindle.target_rpm if spindle.commanded_on else 0.0
        actual = spindle.actual_rpm
        step = self.config.sim_spindle_ramp_rpm_per_s * dt

        if actual < target:
            actual = min(target, actual + step)
        elif actual > target:
            actual = max(target, actual - step)

        tolerance = self.config.spindle_at_speed_tolerance_rpm
        at_speed = (not spindle.commanded_on and actual == 0.0) or abs(actual - target) <= tolerance
        return replace(
            state,
            spindle=SpindleState(
                commanded_on=spindle.commanded_on,
                forward=spindle.forward,
                target_rpm=target,
                actual_rpm=actual,
                at_speed=at_speed,
            ),
        )


def _validate_station(station: int, label: str) -> None:
    if not 1 <= station <= 8:
        raise CommandRejectedError(f"{label} must be in range 1..8")
