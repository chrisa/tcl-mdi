from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MoveAction:
    line_number: int
    mode: Literal["rapid", "feed"]
    target_x_mm: float
    target_z_mm: float
    feed: float | None = None
    source: str = ""


@dataclass(frozen=True)
class SpindleAction:
    line_number: int
    on: bool
    forward: bool = True
    rpm: float = 0.0
    source: str = ""


@dataclass(frozen=True)
class ToolChangeAction:
    line_number: int
    tool_number: int | None = None
    turret_station: int | None = None
    source: str = ""


@dataclass(frozen=True)
class MessageAction:
    line_number: int
    message: str
    source: str = ""


CanonicalAction = MoveAction | SpindleAction | ToolChangeAction | MessageAction
