from __future__ import annotations

import importlib
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.machine import (
    BackendConnectionError,
    BackendError,
    CommandRejectedError,
    MachineState,
    SpindleState,
)


FredClientFactory = Callable[..., Any]


class FredBackend:
    name = "fred"

    def __init__(
        self,
        config: MachineConfig | None = None,
        *,
        client_factory: FredClientFactory | None = None,
    ):
        self.config = config or MachineConfig()
        self._client_factory = client_factory
        self._client: Any | None = None
        self._state = MachineState(status_message="fred: disconnected")
        self._commanded_spindle = SpindleState()
        self._pending_completion: str | None = None
        self._pending_motion_target_mm: tuple[float, float] | None = None
        self._pending_snapshot_generation: int | None = None
        self._pending_tool_dwell_s = 0.0
        self._pending_tool_complete_at: float | None = None
        self._latest_snapshot_generation: int | None = None

    def connect(self) -> None:
        self.disconnect()
        try:
            factory = self._client_factory or _load_fred_client()
            self._client = factory(
                self.config.usb_vid,
                self.config.usb_pid,
                timeout_ms=self.config.usb_timeout_ms,
                x_counts_per_mm=self.config.x_counts_per_mm,
                z_counts_per_mm=self.config.z_counts_per_mm,
            )
            self._client.enable_polling(
                period_ms=self.config.fred_poll_period_ms,
                rpm_service="remote",
            )
        except Exception as exc:  # The native client raises its own exception types.
            self._client = None
            raise BackendConnectionError(f"fred: USB connect failed: {exc}") from exc

        self._state = replace(
            self._state,
            connected=True,
            busy=False,
            error=False,
            error_message="",
            status_message="fred: connected",
        )

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.disable_polling()
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._state = MachineState(status_message="fred: disconnected")
        self._commanded_spindle = SpindleState()
        self._clear_pending_completion()
        self._latest_snapshot_generation = None

    def poll(self) -> MachineState:
        client = self._require_client()
        try:
            snapshot = client.refresh(timeout_ms=0)
            if snapshot is None:
                snapshot = client.latest_snapshot()
            status = client.controller_status()
        except Exception as exc:
            self.disconnect()
            raise BackendConnectionError(f"fred: communication failed: {exc}") from exc

        state = self._state
        if snapshot is not None:
            self._latest_snapshot_generation = _snapshot_generation(snapshot)
            state = _apply_snapshot(state, snapshot, self._commanded_spindle, self.config)

        controller_busy = not bool(status.get("idle", True))
        error = bool(status.get("error", False))
        if error:
            self._clear_pending_completion()
            message = "fred: controller error"
            busy = False
        else:
            pending_busy, pending_message = self._pending_completion_status(
                state,
                controller_busy=controller_busy,
            )
            busy = controller_busy or pending_busy
            if controller_busy:
                message = "fred: busy"
            elif pending_message is not None:
                message = pending_message
            else:
                message = "fred: idle"

        self._state = replace(
            state,
            connected=True,
            busy=busy,
            error=error,
            error_message=message if error else "",
            status_message=message,
        )
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
        client = self._require_ready()
        target = (self._state.x_mm + x_mm, self._state.z_mm + z_mm)
        baseline_generation = self._latest_snapshot_generation
        if mode == "rapid":
            command_active = client.rapid_move_delta(
                x_mm=x_mm,
                z_mm=z_mm,
                slew=slew,
                wait=False,
            )
        elif mode == "feed":
            command_active = client.feed_move_delta(
                x_mm=x_mm,
                z_mm=z_mm,
                feed=feed,
                slew=slew,
                wait=False,
            )
        else:
            raise CommandRejectedError(f"fred: unsupported jog mode: {mode}")

        if command_active:
            self._set_pending_motion_completion(target, baseline_generation)
        self._state = replace(
            self._state,
            busy=bool(command_active),
            status_message="fred: jog queued",
        )

    def set_spindle(self, *, on: bool, rpm: float = 0.0, forward: bool = True) -> None:
        client = self._require_ready()
        target = max(0.0, float(rpm)) if on else 0.0
        baseline_generation = self._latest_snapshot_generation
        try:
            command_active = client.set_spindle(
                on=on,
                rpm=target,
                forward=forward,
                wait=False,
            )
        except Exception as exc:
            raise BackendError(f"fred: spindle command failed: {exc}") from exc

        actual_rpm = self._state.spindle.actual_rpm
        self._commanded_spindle = SpindleState(
            commanded_on=on,
            forward=forward,
            target_rpm=target,
            actual_rpm=actual_rpm,
            at_speed=_spindle_at_speed(on, target, actual_rpm, self.config),
        )
        if not self._commanded_spindle.at_speed:
            self._set_pending_spindle_completion(baseline_generation)
        self._state = replace(
            self._state,
            spindle=self._commanded_spindle,
            busy=bool(command_active) or self._pending_completion == "spindle",
            status_message="fred: spindle command queued",
        )

    def select_tool(
        self,
        *,
        current_station: int,
        target_station: int,
        slew: int = 61,
    ) -> bool:
        client = self._require_ready()
        _validate_station(current_station, "current station")
        _validate_station(target_station, "target station")
        try:
            command_active = client.change_tool(
                current_station=current_station,
                target_station=target_station,
                slew=slew,
                wait=False,
            )
        except Exception as exc:
            raise BackendError(f"fred: toolchanger command failed: {exc}") from exc

        if command_active:
            self._set_pending_tool_completion(_turret_step_count(current_station, target_station))
        self._state = replace(
            self._state,
            busy=bool(command_active),
            status_message=(
                f"fred: toolchanger P{current_station}->P{target_station} queued"
                if command_active
                else f"fred: toolchanger already at P{target_station}"
            ),
        )
        return bool(command_active)

    def wait_idle(self, timeout_ms: int | None = None) -> None:
        deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000.0

        while True:
            try:
                state = self.poll()
            except Exception as exc:
                raise BackendError(f"fred: timeout waiting for idle: {exc}") from exc
            if not state.busy:
                return
            if deadline is not None and time.monotonic() >= deadline:
                raise BackendError("fred: timeout waiting for idle")
            time.sleep(min(0.02, max(0.001, self.config.fred_poll_period_ms / 1000.0)))

    def _require_client(self) -> Any:
        if self._client is None:
            raise BackendConnectionError("fred: not connected")
        return self._client

    def _require_ready(self) -> Any:
        client = self._require_client()
        state = self.poll()
        if state.error:
            raise CommandRejectedError(state.error_message or "fred: controller error")
        if state.busy:
            raise CommandRejectedError("fred: controller busy")
        return client

    def _set_pending_motion_completion(
        self,
        target_mm: tuple[float, float],
        baseline_generation: int | None,
    ) -> None:
        self._pending_completion = "motion"
        self._pending_motion_target_mm = target_mm
        self._pending_snapshot_generation = baseline_generation

    def _set_pending_spindle_completion(self, baseline_generation: int | None) -> None:
        self._pending_completion = "spindle"
        self._pending_motion_target_mm = None
        self._pending_snapshot_generation = baseline_generation

    def _set_pending_tool_completion(self, station_count: int) -> None:
        self._pending_completion = "tool"
        self._pending_motion_target_mm = None
        self._pending_snapshot_generation = None
        dwell_per_station = max(0.0, self.config.fred_tool_station_dwell_s)
        self._pending_tool_dwell_s = dwell_per_station * station_count
        self._pending_tool_complete_at = None

    def _clear_pending_completion(self) -> None:
        self._pending_completion = None
        self._pending_motion_target_mm = None
        self._pending_snapshot_generation = None
        self._pending_tool_dwell_s = 0.0
        self._pending_tool_complete_at = None

    def _pending_completion_status(
        self,
        state: MachineState,
        *,
        controller_busy: bool,
    ) -> tuple[bool, str | None]:
        if self._pending_completion is None or controller_busy:
            return False, None

        if self._pending_completion == "motion":
            if self._pending_motion_target_mm is None:
                self._clear_pending_completion()
                return False, None
            if not self._has_fresh_snapshot_since_pending_started():
                return True, "fred: waiting for motion feedback"
            if not _motion_at_target(
                state,
                self._pending_motion_target_mm,
                self.config.fred_motion_settle_tolerance_mm,
            ):
                return True, "fred: settling to target"
            self._clear_pending_completion()
            return False, None

        if self._pending_completion == "spindle":
            if not self._has_fresh_snapshot_since_pending_started():
                return True, "fred: waiting for spindle feedback"
            if not state.spindle.at_speed:
                return True, "fred: spindle ramping to speed"
            self._clear_pending_completion()
            return False, None

        if self._pending_completion == "tool":
            if self._pending_tool_dwell_s <= 0.0:
                self._clear_pending_completion()
                return False, None
            now = time.monotonic()
            if self._pending_tool_complete_at is None:
                self._pending_tool_complete_at = now + self._pending_tool_dwell_s
            if now < self._pending_tool_complete_at:
                return True, "fred: toolchanger settling"
            self._clear_pending_completion()
            return False, None

        self._clear_pending_completion()
        return False, None

    def _has_fresh_snapshot_since_pending_started(self) -> bool:
        if self._pending_snapshot_generation is None or self._latest_snapshot_generation is None:
            return True
        return _generation_after(
            self._latest_snapshot_generation,
            self._pending_snapshot_generation,
        )


def _load_fred_client() -> FredClientFactory:
    for path in _fred_client_paths():
        if path.exists():
            path_str = str(path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
            break

    try:
        module = importlib.import_module("fred_client")
    except Exception as exc:
        raise BackendConnectionError(
            "fred: could not import fred_client; set TCL_LATHE_FRED_PYTHON "
            "or install the RP2040 FRED Python package"
        ) from exc

    return module.FredUsbClient


def _fred_client_paths() -> list[Path]:
    paths: list[Path] = []
    env_path = os.environ.get("TCL_LATHE_FRED_PYTHON")
    if env_path:
        paths.append(Path(env_path).expanduser())
    workspace_root = Path(__file__).resolve().parents[3]
    paths.append(workspace_root / "tcl202_dis" / "rp2040_fred" / "python")
    return paths


def _apply_snapshot(
    state: MachineState,
    snapshot: dict[str, object],
    commanded_spindle: SpindleState,
    config: MachineConfig,
) -> MachineState:
    actual_rpm = float(snapshot.get("spindle_rpm", state.spindle.actual_rpm))
    target = commanded_spindle.target_rpm
    at_speed = _spindle_at_speed(commanded_spindle.commanded_on, target, actual_rpm, config)
    spindle = replace(commanded_spindle, actual_rpm=actual_rpm, at_speed=at_speed)

    return replace(
        state,
        x_mm=float(snapshot.get("x_mm", state.x_mm)),
        z_mm=float(snapshot.get("z_mm", state.z_mm)),
        x_counts=_optional_int(snapshot.get("x_counts")),
        z_counts=_optional_int(snapshot.get("z_counts")),
        spindle=spindle,
    )


def _motion_at_target(
    state: MachineState,
    target_mm: tuple[float, float],
    tolerance_mm: float,
) -> bool:
    target_x_mm, target_z_mm = target_mm
    return (
        abs(state.x_mm - target_x_mm) <= tolerance_mm
        and abs(state.z_mm - target_z_mm) <= tolerance_mm
    )


def _snapshot_generation(snapshot: dict[str, object]) -> int | None:
    value = snapshot.get("generation")
    if value is None:
        return None
    return int(value)


def _generation_after(current: int, previous: int) -> bool:
    diff = (current - previous) & 0xFFFFFFFF
    return diff != 0 and diff < 0x80000000


def _turret_step_count(current_station: int, target_station: int) -> int:
    if current_station == target_station:
        return 0
    if current_station < target_station:
        return target_station - current_station
    return 8 - (current_station - target_station)


def _spindle_at_speed(
    commanded_on: bool,
    target_rpm: float,
    actual_rpm: float,
    config: MachineConfig,
) -> bool:
    if not commanded_on:
        return abs(actual_rpm) <= config.spindle_at_speed_tolerance_rpm
    if target_rpm <= config.spindle_at_speed_tolerance_rpm:
        return True
    return abs(abs(actual_rpm) - target_rpm) <= config.spindle_at_speed_tolerance_rpm


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _validate_station(station: int, label: str) -> None:
    if not 1 <= station <= 8:
        raise CommandRejectedError(f"{label} must be in range 1..8")
