from __future__ import annotations

from dataclasses import replace

from tcl_lathe_hmi.gcode import (
    CanonicalAction,
    MessageAction,
    MoveAction,
    SpindleAction,
    ToolChangeAction,
)

from .backend import BackendError, CommandRejectedError, MachineBackend
from .state import MachineState


class MachineService:
    """Backend-neutral command gate used by the UI."""

    def __init__(self, backend: MachineBackend):
        self.backend = backend
        self.state = MachineState(status_message=f"{backend.name}: disconnected")

    def set_backend(self, backend: MachineBackend) -> MachineState:
        try:
            self.backend.disconnect()
        except BackendError:
            pass
        self.backend = backend
        self.state = MachineState(status_message=f"{backend.name}: disconnected")
        return self.state

    def connect(self) -> MachineState:
        try:
            self.backend.connect()
            self.state = self.backend.poll()
        except BackendError as exc:
            self.state = replace(
                self.state,
                connected=False,
                busy=False,
                error=True,
                error_message=str(exc),
                status_message=str(exc),
            )
        return self.state

    def disconnect(self) -> MachineState:
        try:
            self.backend.disconnect()
        finally:
            self.state = MachineState(status_message=f"{self.backend.name}: disconnected")
        return self.state

    def poll(self) -> MachineState:
        if not self.state.connected and not self.state.error:
            return self.state
        try:
            self.state = self.backend.poll()
        except BackendError as exc:
            self.state = replace(
                self.state,
                connected=False,
                busy=False,
                error=True,
                error_message=str(exc),
                status_message=str(exc),
            )
        return self.state

    def clear_error(self) -> MachineState:
        if self.state.error:
            self.state = replace(
                self.state,
                error=False,
                error_message="",
                status_message=f"{self.backend.name}: disconnected",
            )
        return self.state

    def jog_delta(
        self,
        *,
        x_mm: float = 0.0,
        z_mm: float = 0.0,
        mode: str = "feed",
        feed: int = 100,
        slew: int = 61,
    ) -> bool:
        if not self.state.can_accept_commands:
            self._mark_rejected("Machine is not ready for jog")
            return False
        try:
            self.backend.jog_delta(
                x_mm=x_mm,
                z_mm=z_mm,
                mode=mode,
                feed=feed,
                slew=slew,
            )
            self.state = self.backend.poll()
            return True
        except CommandRejectedError as exc:
            self._mark_rejected(str(exc))
            return False
        except BackendError as exc:
            self._mark_error(str(exc))
            return False

    def set_spindle(self, *, on: bool, rpm: float = 0.0, forward: bool = True) -> bool:
        if not self.state.can_accept_commands:
            self._mark_rejected("Machine is not ready for spindle command")
            return False
        try:
            self.backend.set_spindle(on=on, rpm=rpm, forward=forward)
            self.state = self.backend.poll()
            return True
        except CommandRejectedError as exc:
            self._mark_rejected(str(exc))
            return False
        except BackendError as exc:
            self._mark_error(str(exc))
            return False

    def execute_action(
        self,
        action: CanonicalAction,
        *,
        default_feed: int = 100,
        default_slew: int = 61,
    ) -> bool:
        if isinstance(action, MoveAction):
            return self._execute_move_action(
                action,
                default_feed=default_feed,
                default_slew=default_slew,
            )
        if isinstance(action, SpindleAction):
            return self.set_spindle(on=action.on, rpm=action.rpm, forward=action.forward)
        if isinstance(action, ToolChangeAction):
            tool = "unknown" if action.tool_number is None else str(action.tool_number)
            station = (
                "unknown"
                if action.turret_station is None
                else str(action.turret_station)
            )
            self._mark_rejected(
                f"Tool change requested at line {action.line_number}: "
                f"T{tool} station {station}; manual flow is not implemented yet"
            )
            return False
        if isinstance(action, MessageAction):
            self._mark_rejected(action.message)
            return False
        self._mark_rejected(f"Unsupported action at line {getattr(action, 'line_number', '?')}")
        return False

    def _execute_move_action(
        self,
        action: MoveAction,
        *,
        default_feed: int,
        default_slew: int,
    ) -> bool:
        dx = action.target_x_mm - self.state.x_mm
        dz = action.target_z_mm - self.state.z_mm
        if dx == 0.0 and dz == 0.0:
            self.state = replace(
                self.state,
                status_message=f"Line {action.line_number}: already at target",
            )
            return True
        return self.jog_delta(
            x_mm=dx,
            z_mm=dz,
            mode=action.mode,
            feed=int(action.feed or default_feed),
            slew=default_slew,
        )

    def _mark_rejected(self, message: str) -> None:
        self.state = replace(self.state, status_message=message)

    def _mark_error(self, message: str) -> None:
        self.state = replace(
            self.state,
            connected=False,
            busy=False,
            error=True,
            error_message=message,
            status_message=message,
        )
