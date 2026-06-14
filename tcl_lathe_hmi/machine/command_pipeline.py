from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


CommandMode = Literal["auto", "single_step"]
CommandOutcome = Literal["idle", "awaiting", "executed", "cancelled", "failed"]


@dataclass(frozen=True)
class CommandPipelineStatus:
    mode: CommandMode
    current_label: str
    next_label: str
    awaiting_approval: bool
    completion_generation: int
    cancel_generation: int
    failure_generation: int
    last_outcome: CommandOutcome


@dataclass(frozen=True)
class CommandSubmission:
    accepted: bool
    executed: bool
    awaiting_approval: bool
    value: bool | None = None
    message: str = ""


@dataclass
class _PendingCommand:
    label: str
    execute: Callable[[], bool]


class CommandPipeline:
    def __init__(self) -> None:
        self.mode: CommandMode = "auto"
        self.current_label = "--"
        self.next_label = "--"
        self.completion_generation = 0
        self.cancel_generation = 0
        self.failure_generation = 0
        self.last_outcome: CommandOutcome = "idle"
        self._pending: _PendingCommand | None = None

    @property
    def awaiting_approval(self) -> bool:
        return self._pending is not None

    @property
    def status(self) -> CommandPipelineStatus:
        return CommandPipelineStatus(
            mode=self.mode,
            current_label=self.current_label,
            next_label=self.next_label,
            awaiting_approval=self.awaiting_approval,
            completion_generation=self.completion_generation,
            cancel_generation=self.cancel_generation,
            failure_generation=self.failure_generation,
            last_outcome=self.last_outcome,
        )

    def set_mode(self, mode: CommandMode) -> bool:
        if mode not in {"auto", "single_step"}:
            raise ValueError(f"unsupported command mode: {mode}")
        if self._pending is not None and mode != self.mode:
            return False
        self.mode = mode
        return True

    def set_next_label(self, label: str | None) -> None:
        self.next_label = label or "--"

    def submit(self, label: str, execute: Callable[[], bool]) -> CommandSubmission:
        if self._pending is not None:
            return CommandSubmission(
                accepted=False,
                executed=False,
                awaiting_approval=True,
                message=f"Waiting for Go/Cancel: {self._pending.label}",
            )

        self.current_label = label
        if self.mode == "single_step":
            self._pending = _PendingCommand(label=label, execute=execute)
            self.last_outcome = "awaiting"
            return CommandSubmission(accepted=True, executed=False, awaiting_approval=True)

        value = self._run_execute(execute)
        return CommandSubmission(
            accepted=True,
            executed=True,
            awaiting_approval=False,
            value=value,
        )

    def approve_pending(self) -> bool:
        if self._pending is None:
            return False
        pending = self._pending
        self._pending = None
        self.current_label = pending.label
        return self._run_execute(pending.execute)

    def cancel_pending(self) -> bool:
        if self._pending is None:
            return False
        label = self._pending.label
        self._pending = None
        self.current_label = f"Cancelled: {label}"
        self.cancel_generation += 1
        self.last_outcome = "cancelled"
        return True

    def clear_pending(self) -> None:
        self._pending = None

    def clear_completed_current(self) -> None:
        if self._pending is None and self.last_outcome == "executed":
            self.current_label = "--"
            self.last_outcome = "idle"

    def _run_execute(self, execute: Callable[[], bool]) -> bool:
        try:
            value = bool(execute())
        except Exception:
            self.failure_generation += 1
            self.last_outcome = "failed"
            raise
        if value:
            self.completion_generation += 1
            self.last_outcome = "executed"
        else:
            self.failure_generation += 1
            self.last_outcome = "failed"
        return value
