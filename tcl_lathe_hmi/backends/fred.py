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
            state = _apply_snapshot(state, snapshot, self._commanded_spindle, self.config)

        busy = not bool(status.get("idle", True))
        error = bool(status.get("error", False))
        message = "fred: busy" if busy else "fred: idle"
        if error:
            message = "fred: controller error"

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

        self._state = replace(
            self._state,
            busy=bool(command_active),
            status_message="fred: jog queued",
        )

    def set_spindle(self, *, on: bool, rpm: float = 0.0, forward: bool = True) -> None:
        client = self._require_ready()
        target = max(0.0, float(rpm)) if on else 0.0
        try:
            command_active = client.set_spindle(
                on=on,
                rpm=target,
                forward=forward,
                wait=False,
            )
        except Exception as exc:
            raise BackendError(f"fred: spindle command failed: {exc}") from exc

        self._commanded_spindle = replace(
            self._commanded_spindle,
            commanded_on=on,
            forward=forward,
            target_rpm=target,
            at_speed=not on,
        )
        self._state = replace(
            self._state,
            spindle=self._commanded_spindle,
            busy=bool(command_active),
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
        client = self._require_client()
        try:
            client.wait_idle(timeout_ms=timeout_ms)
        except Exception as exc:
            raise BackendError(f"fred: timeout waiting for idle: {exc}") from exc

        self.poll()

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
