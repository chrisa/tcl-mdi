from __future__ import annotations

from dataclasses import replace

from tcl_lathe_hmi.gcode import (
    CanonicalAction,
    MessageAction,
    MoveAction,
    SpindleAction,
    ToolChangeAction,
)
from tcl_lathe_hmi.tools import ToolRecord, ToolTable

from .backend import BackendError, CommandRejectedError, MachineBackend
from .state import MachineState


class MachineService:
    """Backend-neutral command gate used by the UI."""

    def __init__(self, backend: MachineBackend):
        self.backend = backend
        self.tool_table = ToolTable([ToolRecord(tool_number=0, station=0)])
        self.state = MachineState(status_message=f"{backend.name}: disconnected")

    def set_backend(self, backend: MachineBackend) -> MachineState:
        tool_state = self._tool_state_kwargs(self.state)
        try:
            self.backend.disconnect()
        except BackendError:
            pass
        self.backend = backend
        self.state = MachineState(
            status_message=f"{backend.name}: disconnected",
            **tool_state,
        )
        return self.state

    def connect(self) -> MachineState:
        try:
            self.backend.connect()
            self.state = self._merge_tool_state(self.backend.poll())
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
        tool_state = self._tool_state_kwargs(self.state)
        try:
            self.backend.disconnect()
        finally:
            self.state = MachineState(
                status_message=f"{self.backend.name}: disconnected",
                **tool_state,
            )
        return self.state

    def poll(self) -> MachineState:
        if not self.state.connected and not self.state.error:
            return self.state
        try:
            self.state = self._merge_tool_state(self.backend.poll())
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
            self.state = self._merge_tool_state(self.backend.poll())
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
            self.state = self._merge_tool_state(self.backend.poll())
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
            return self.request_tool_change(action)
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
        dx = action.target_x_mm - self.state.work_x_mm
        dz = action.target_z_mm - self.state.work_z_mm
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

    def request_tool_change(self, action: ToolChangeAction) -> bool:
        tool_number = action.tool_number
        if tool_number is None:
            self._mark_rejected(f"Line {action.line_number}: tool change missing T word")
            return False
        tool = self.tool_table.ensure_tool(tool_number, action.turret_station)
        station = action.turret_station if action.turret_station is not None else tool.station
        self.state = replace(
            self.state,
            pending_tool=tool.tool_number,
            pending_turret_station=station,
            status_message=(
                f"Confirm manual tool change to T{tool.tool_number}"
                + (f" station {station}" if station is not None else "")
            ),
        )
        return False

    def confirm_tool_change(
        self,
        tool_number: int | None = None,
        station: int | None = None,
    ) -> bool:
        selected_tool = tool_number if tool_number is not None else self.state.pending_tool
        if selected_tool is None:
            self._mark_rejected("No pending tool change to confirm")
            return False
        tool = self.tool_table.ensure_tool(selected_tool, station)
        active_station = station if station is not None else tool.station
        self.state = replace(
            self.state,
            active_tool=tool.tool_number,
            turret_station=active_station,
            tool_x_offset_mm=tool.x_offset_mm,
            tool_z_offset_mm=tool.z_offset_mm,
            pending_tool=None,
            pending_turret_station=None,
            status_message=(
                f"Active tool T{tool.tool_number}"
                + (f" station {active_station}" if active_station is not None else "")
            ),
        )
        return True

    def set_active_tool(self, tool_number: int) -> bool:
        tool = self.tool_table.get(tool_number)
        if tool is None:
            self._mark_rejected(f"T{tool_number} is not in the tool table")
            return False
        return self.confirm_tool_change(tool.tool_number, tool.station)

    def update_tool_table(self, table: ToolTable) -> None:
        self.tool_table = table
        active = self.tool_table.get(self.state.active_tool)
        if active is not None:
            self.state = replace(
                self.state,
                turret_station=active.station,
                tool_x_offset_mm=active.x_offset_mm,
                tool_z_offset_mm=active.z_offset_mm,
            )

    def _merge_tool_state(self, backend_state: MachineState) -> MachineState:
        return replace(
            backend_state,
            active_tool=self.state.active_tool,
            turret_station=self.state.turret_station,
            tool_x_offset_mm=self.state.tool_x_offset_mm,
            tool_z_offset_mm=self.state.tool_z_offset_mm,
            pending_tool=self.state.pending_tool,
            pending_turret_station=self.state.pending_turret_station,
        )

    @staticmethod
    def _tool_state_kwargs(state: MachineState) -> dict[str, object]:
        return {
            "active_tool": state.active_tool,
            "turret_station": state.turret_station,
            "tool_x_offset_mm": state.tool_x_offset_mm,
            "tool_z_offset_mm": state.tool_z_offset_mm,
            "pending_tool": state.pending_tool,
            "pending_turret_station": state.pending_turret_station,
        }

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
