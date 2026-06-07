from __future__ import annotations

from dataclasses import dataclass, replace

from tcl_lathe_hmi.machine.state import MachineState
from tcl_lathe_hmi.tools import TURRET_STATIONS, ToolManager, ToolRecord


@dataclass(frozen=True)
class ToolChangeResolution:
    tool: ToolRecord
    station: int | None
    manual_required: bool
    message: str


class ToolChangeCoordinator:
    def __init__(self, tools: ToolManager):
        self.tools = tools

    def resolve_request(
        self,
        *,
        tool_number: int,
        requested_station: int | None,
        context: str,
    ) -> ToolChangeResolution:
        tool = self.tools.require_tool(tool_number)
        assigned_station = self.tools.station_for_tool(tool_number)

        if requested_station is not None:
            if not self.is_turret_station(requested_station):
                return self._manual(
                    tool,
                    requested_station,
                    f"{context}: T{tool_number} requested P{requested_station}; "
                    "confirm manual tool change",
                )
            if assigned_station != requested_station:
                assigned = "--" if assigned_station is None else str(assigned_station)
                return self._manual(
                    tool,
                    requested_station,
                    f"{context}: T{tool_number} is assigned to P{assigned}, "
                    f"not requested P{requested_station}; confirm manual tool change "
                    "or update the Tools tab",
                )

        target_station = requested_station if requested_station is not None else assigned_station
        if not self.is_turret_station(target_station):
            return self._manual(
                tool,
                target_station,
                f"{context}: T{tool_number} is not assigned to turret station "
                f"1..{TURRET_STATIONS}; confirm manual tool change",
            )

        assert target_station is not None
        return ToolChangeResolution(
            tool=tool,
            station=target_station,
            manual_required=False,
            message=f"{context}: T{tool_number} station P{target_station}",
        )

    def resolve_confirmation(
        self,
        *,
        tool_number: int,
        station: int | None,
    ) -> ToolChangeResolution:
        tool = self.tools.require_tool(tool_number)
        active_station = (
            station
            if self.is_turret_station(station)
            else self.tools.station_for_tool(tool_number)
        )
        if not self.is_turret_station(active_station):
            active_station = None
        return ToolChangeResolution(
            tool=tool,
            station=active_station,
            manual_required=False,
            message=(
                f"Active tool T{tool.tool_number}"
                + (f" station {active_station}" if active_station is not None else "")
            ),
        )

    def apply_active_tool(
        self,
        state: MachineState,
        resolution: ToolChangeResolution,
    ) -> MachineState:
        return replace(
            state,
            active_tool=resolution.tool.tool_number,
            turret_station=resolution.station,
            tool_x_offset_mm=resolution.tool.x_offset_mm,
            tool_z_offset_mm=resolution.tool.z_offset_mm,
            pending_tool=None,
            pending_turret_station=None,
            status_message=resolution.message,
        )

    def refresh_active_tool_state(self, state: MachineState) -> MachineState:
        active = self.tools.get_tool(state.active_tool)
        if active is None:
            return state
        return replace(
            state,
            tool_x_offset_mm=active.x_offset_mm,
            tool_z_offset_mm=active.z_offset_mm,
        )

    @staticmethod
    def is_turret_station(station: int | None) -> bool:
        return station is not None and 1 <= station <= TURRET_STATIONS

    @staticmethod
    def _manual(
        tool: ToolRecord,
        station: int | None,
        message: str,
    ) -> ToolChangeResolution:
        return ToolChangeResolution(
            tool=tool,
            station=station,
            manual_required=True,
            message=message,
        )
