from __future__ import annotations

from typing import Protocol

from .state import MachineState


class BackendError(RuntimeError):
    """Base class for machine backend failures."""


class BackendConnectionError(BackendError):
    """Raised when a backend cannot connect or loses connection."""


class CommandRejectedError(BackendError):
    """Raised when a command is not safe to issue right now."""


class MachineBackend(Protocol):
    name: str

    def connect(self) -> None:
        ...

    def disconnect(self) -> None:
        ...

    def poll(self) -> MachineState:
        ...

    def jog_delta(
        self,
        *,
        x_mm: float = 0.0,
        z_mm: float = 0.0,
        mode: str = "feed",
        feed: int = 100,
        slew: int = 61,
    ) -> None:
        ...

    def set_spindle(
        self,
        *,
        on: bool,
        rpm: float = 0.0,
        forward: bool = True,
    ) -> None:
        ...

    def select_tool(
        self,
        *,
        current_station: int,
        target_station: int,
        slew: int = 61,
    ) -> bool:
        ...

    def wait_idle(self, timeout_ms: int | None = None) -> None:
        ...
