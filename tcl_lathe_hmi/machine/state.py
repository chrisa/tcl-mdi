from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SpindleState:
    commanded_on: bool = False
    forward: bool = True
    target_rpm: float = 0.0
    actual_rpm: float = 0.0
    at_speed: bool = True

    @property
    def direction_label(self) -> str:
        if not self.commanded_on:
            return "Stopped"
        return "Forward" if self.forward else "Reverse"


@dataclass(frozen=True)
class MachineState:
    x_mm: float = 0.0
    z_mm: float = 0.0
    x_counts: int | None = None
    z_counts: int | None = None

    spindle: SpindleState = field(default_factory=SpindleState)

    connected: bool = False
    busy: bool = False
    error: bool = False
    error_message: str = ""
    status_message: str = "Disconnected"

    homed_x: bool = False
    homed_z: bool = False
    active_tool: int = 0
    turret_station: int | None = None
    work_x_offset_mm: float = 0.0
    work_z_offset_mm: float = 0.0
    tool_x_offset_mm: float = 0.0
    tool_z_offset_mm: float = 0.0
    pending_tool: int | None = None
    pending_turret_station: int | None = None
    display_mode: Literal["work", "machine"] = "work"

    soft_limits_enabled: bool = True
    x_min_limit_mm: float = -100.0
    x_max_limit_mm: float = 100.0
    z_min_limit_mm: float = -100.0
    z_max_limit_mm: float = 100.0

    @property
    def work_x_mm(self) -> float:
        return self.x_mm + self.work_x_offset_mm + self.tool_x_offset_mm

    @property
    def work_z_mm(self) -> float:
        return self.z_mm + self.work_z_offset_mm + self.tool_z_offset_mm

    @property
    def display_x_mm(self) -> float:
        return self.x_mm if self.display_mode == "machine" else self.work_x_mm

    @property
    def display_z_mm(self) -> float:
        return self.z_mm if self.display_mode == "machine" else self.work_z_mm

    @property
    def can_accept_commands(self) -> bool:
        return self.connected and not self.busy and not self.error

    @property
    def controller_label(self) -> str:
        if self.error:
            return "ERROR"
        if not self.connected:
            return "DISCONNECTED"
        if self.busy:
            return "BUSY"
        return "IDLE"
