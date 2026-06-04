from __future__ import annotations

from dataclasses import replace

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
