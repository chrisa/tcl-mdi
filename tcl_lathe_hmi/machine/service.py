from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.gcode import (
    CanonicalAction,
    DwellAction,
    MessageAction,
    MoveAction,
    SpindleAction,
    ThreadSyncAction,
    ToolChangeAction,
)
from tcl_lathe_hmi.tools import ToolRecord, ToolTable, sample_tool_table

from .backend import BackendError, CommandRejectedError, MachineBackend
from .state import MachineState


class MachineService:
    """Backend-neutral command gate used by the UI."""

    def __init__(
        self,
        backend: MachineBackend,
        config: MachineConfig | None = None,
        settings_path: str | Path | None = None,
    ):
        self.config = config or MachineConfig()
        self.settings_path = Path(settings_path).expanduser() if settings_path is not None else None
        self.tool_table_path = (
            self.settings_path.parent / "lathe.tbl" if self.settings_path is not None else None
        )
        self.backend = backend
        self.tool_table = sample_tool_table()
        self.state = MachineState(
            status_message=f"{backend.name}: disconnected",
            soft_limits_enabled=self.config.soft_limits_enabled,
            x_min_limit_mm=self.config.x_min_limit_mm,
            x_max_limit_mm=self.config.x_max_limit_mm,
            z_min_limit_mm=self.config.z_min_limit_mm,
            z_max_limit_mm=self.config.z_max_limit_mm,
        )
        self.load_settings()
        if self.load_tool_table():
            self._refresh_active_tool_offsets_from_table()

    def set_backend(self, backend: MachineBackend) -> MachineState:
        preserved_state = self._preserved_state_kwargs(self.state)
        try:
            self.backend.disconnect()
        except BackendError:
            pass
        self.backend = backend
        self.state = MachineState(
            status_message=f"{backend.name}: disconnected",
            **preserved_state,
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
        preserved_state = self._preserved_state_kwargs(self.state)
        try:
            self.backend.disconnect()
        finally:
            self.state = MachineState(
                status_message=f"{self.backend.name}: disconnected",
                **preserved_state,
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
            target_x = self.state.x_mm + x_mm
            target_z = self.state.z_mm + z_mm
            limit_error = self._limits_error_for_target(target_x, target_z, "Jog")
            if limit_error is not None:
                self._mark_rejected(limit_error)
                return False
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
        if isinstance(action, DwellAction):
            return self._execute_dwell_action(action)
        if isinstance(action, ThreadSyncAction):
            return self._execute_thread_sync_action(
                action,
                default_slew=default_slew,
            )
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
        target_machine_x = self.state.x_mm + dx
        target_machine_z = self.state.z_mm + dz
        limit_error = self._limits_error_for_target(
            target_machine_x,
            target_machine_z,
            f"Line {action.line_number}",
        )
        if limit_error is not None:
            self._mark_rejected(limit_error)
            return False
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

    def _execute_dwell_action(self, action: DwellAction) -> bool:
        if not self.state.can_accept_commands:
            self._mark_rejected("Machine is not ready for dwell")
            return False
        try:
            self.backend.dwell(seconds=action.seconds)
            self.state = self._merge_tool_state(self.backend.poll())
            return True
        except CommandRejectedError as exc:
            self._mark_rejected(str(exc))
            return False
        except BackendError as exc:
            self._mark_error(str(exc))
            return False

    def _execute_thread_sync_action(
        self,
        action: ThreadSyncAction,
        *,
        default_slew: int,
    ) -> bool:
        if not self.state.can_accept_commands:
            self._mark_rejected("Machine is not ready for thread sync move")
            return False

        dz = action.target_z_mm - self.state.work_z_mm
        target_machine_z = self.state.z_mm + dz
        limit_error = self._limits_error_for_target(
            self.state.x_mm,
            target_machine_z,
            f"Line {action.line_number}",
        )
        if limit_error is not None:
            self._mark_rejected(limit_error)
            return False

        if dz == 0.0:
            self.state = replace(
                self.state,
                status_message=f"Line {action.line_number}: already at thread target",
            )
            return True

        try:
            self.backend.thread_sync_move(
                z_mm=dz,
                pitch=action.pitch_mm,
                slew=default_slew,
            )
            self.state = self._merge_tool_state(self.backend.poll())
            return True
        except CommandRejectedError as exc:
            self._mark_rejected(str(exc))
            return False
        except BackendError as exc:
            self._mark_error(str(exc))
            return False

    def request_tool_change(self, action: ToolChangeAction) -> bool:
        tool_number = action.tool_number
        if tool_number is None:
            self._mark_rejected(f"Line {action.line_number}: tool change missing T word")
            return False
        requested_station = action.turret_station
        tool = self.tool_table.ensure_tool(
            tool_number,
            requested_station if self._is_turret_station(requested_station) else None,
        )
        station = requested_station if requested_station is not None else tool.station
        if not self._is_turret_station(station):
            self._mark_pending_tool(
                tool.tool_number,
                station,
                (
                    f"Line {action.line_number}: T{tool.tool_number} is not assigned "
                    "to turret station 1..8; confirm manual tool change"
                ),
            )
            return False
        if self.state.turret_station is not None:
            return self.change_tool(
                tool.tool_number,
                station=station,
                context=f"Line {action.line_number}",
            )
        self._mark_rejected(
            f"Line {action.line_number}: current turret station unknown; "
            "set current station on the Manual tab before automatic tool changes"
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
        tool = self.tool_table.ensure_tool(
            selected_tool,
            station if self._is_turret_station(station) else None,
        )
        active_station = (
            station
            if self._is_turret_station(station)
            else (tool.station if self._is_turret_station(tool.station) else None)
        )
        self._apply_active_tool(tool, active_station)
        return True

    def change_tool(
        self,
        tool_number: int,
        station: int | None = None,
        *,
        context: str = "Tool change",
    ) -> bool:
        if not self.state.can_accept_commands:
            self._mark_rejected("Machine is not ready for toolchanger command")
            return False
        tool = self.tool_table.ensure_tool(
            tool_number,
            station if self._is_turret_station(station) else None,
        )
        target_station = station if station is not None else tool.station
        if not self._is_turret_station(target_station):
            self._mark_pending_tool(
                tool.tool_number,
                target_station,
                (
                    f"{context}: T{tool.tool_number} is not assigned to turret station 1..8; "
                    "confirm manual tool change"
                ),
            )
            return False
        current_station = self.state.turret_station
        if current_station is None:
            self._mark_rejected(
                f"{context}: current turret station unknown; "
                "set current station on the Manual tab before automatic tool changes"
            )
            return False
        if not self._valid_station(current_station, "current station"):
            return False

        try:
            self.backend.select_tool(
                current_station=current_station,
                target_station=target_station,
                slew=self.config.jog_slew,
            )
            self._apply_active_tool(tool, target_station)
            self.state = self._merge_tool_state(self.backend.poll())
            self.save_settings()
            return True
        except CommandRejectedError as exc:
            self._mark_rejected(str(exc))
            return False
        except BackendError as exc:
            self._mark_error(str(exc))
            return False

    def set_turret_station(self, station: int | None) -> bool:
        if station is not None and not self._valid_station(station, "current station"):
            return False
        self.state = replace(
            self.state,
            turret_station=station,
            status_message=(
                "Current turret station cleared"
                if station is None
                else f"Current turret station set to P{station}"
            ),
        )
        self.save_settings()
        return True

    def set_active_tool(self, tool_number: int) -> bool:
        tool = self.tool_table.get(tool_number)
        if tool is None:
            self._mark_rejected(f"T{tool_number} is not in the tool table")
            return False
        return self.confirm_tool_change(tool.tool_number, tool.station)

    def upsert_tool(self, tool: ToolRecord) -> bool:
        self.tool_table.upsert(tool)
        if tool.tool_number == self.state.active_tool:
            self._apply_active_tool(
                tool,
                tool.station if self._is_turret_station(tool.station) else None,
            )
        if not self.save_tool_table():
            return False
        self.state = replace(self.state, status_message=f"Saved {tool.display_name}")
        return True

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
            self.save_settings()
        self.save_tool_table()

    def teach_tool_z(self, known_z_mm: float = 0.0, tool_number: int | None = None) -> bool:
        selected_tool = self._selected_tool_number(tool_number)
        if selected_tool is None:
            return False
        offset = known_z_mm - self.state.z_mm - self.state.work_z_offset_mm
        return self._teach_tool_offset(selected_tool, z_offset_mm=offset)

    def teach_tool_x(self, diameter_mm: float, tool_number: int | None = None) -> bool:
        selected_tool = self._selected_tool_number(tool_number)
        if selected_tool is None:
            return False
        if diameter_mm < 0.0:
            self._mark_rejected("Measured diameter cannot be negative")
            return False
        offset = diameter_mm - self.state.x_mm - self.state.work_x_offset_mm
        return self._teach_tool_offset(selected_tool, x_offset_mm=offset)

    def set_display_mode(self, mode: str) -> None:
        if mode not in {"work", "machine"}:
            raise ValueError(f"unsupported display mode: {mode}")
        self.state = replace(self.state, display_mode=mode)
        self.save_settings()

    def set_work_position(
        self,
        *,
        x_mm: float | None = None,
        z_mm: float | None = None,
    ) -> None:
        kwargs: dict[str, float] = {}
        if x_mm is not None:
            kwargs["work_x_offset_mm"] = x_mm - self.state.x_mm - self.state.tool_x_offset_mm
        if z_mm is not None:
            kwargs["work_z_offset_mm"] = z_mm - self.state.z_mm - self.state.tool_z_offset_mm
        if kwargs:
            self.state = replace(self.state, **kwargs, status_message="Work offset updated")
            self.save_settings()

    def zero_work_axis(self, axis: str) -> bool:
        normalized = axis.strip().upper()
        if normalized == "X":
            self.set_work_position(x_mm=0.0)
            return True
        if normalized == "Z":
            self.set_work_position(z_mm=0.0)
            return True
        self._mark_rejected(f"Unsupported work offset axis: {axis}")
        return False

    def clear_work_offsets(self) -> None:
        self.state = replace(
            self.state,
            work_x_offset_mm=0.0,
            work_z_offset_mm=0.0,
            status_message="Work offsets cleared",
        )
        self.save_settings()

    def update_soft_limits(
        self,
        *,
        enabled: bool | None = None,
        x_min: float | None = None,
        x_max: float | None = None,
        z_min: float | None = None,
        z_max: float | None = None,
    ) -> bool:
        next_enabled = self.state.soft_limits_enabled if enabled is None else enabled
        next_x_min = self.state.x_min_limit_mm if x_min is None else x_min
        next_x_max = self.state.x_max_limit_mm if x_max is None else x_max
        next_z_min = self.state.z_min_limit_mm if z_min is None else z_min
        next_z_max = self.state.z_max_limit_mm if z_max is None else z_max
        if next_x_min > next_x_max:
            self._mark_rejected("X soft-limit min cannot exceed max")
            return False
        if next_z_min > next_z_max:
            self._mark_rejected("Z soft-limit min cannot exceed max")
            return False
        self.state = replace(
            self.state,
            soft_limits_enabled=next_enabled,
            x_min_limit_mm=next_x_min,
            x_max_limit_mm=next_x_max,
            z_min_limit_mm=next_z_min,
            z_max_limit_mm=next_z_max,
            status_message="Soft limits updated",
        )
        self.save_settings()
        return True

    def home_axis(self, axis: str) -> bool:
        self._mark_rejected(
            f"Homing {axis.upper()} is unavailable: FRED/Python homing primitives are not implemented yet"
        )
        return False

    def reconnect(self) -> MachineState:
        self.disconnect()
        return self.connect()

    def load_settings(self) -> None:
        if self.settings_path is None or not self.settings_path.exists():
            return
        try:
            data = json.loads(self.settings_path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        allowed = {
            "display_mode",
            "active_tool",
            "turret_station",
            "work_x_offset_mm",
            "work_z_offset_mm",
            "tool_x_offset_mm",
            "tool_z_offset_mm",
            "soft_limits_enabled",
            "x_min_limit_mm",
            "x_max_limit_mm",
            "z_min_limit_mm",
            "z_max_limit_mm",
        }
        kwargs = {key: data[key] for key in allowed if key in data}
        if kwargs:
            self.state = replace(self.state, **kwargs)

    def load_tool_table(self) -> bool:
        if self.tool_table_path is None or not self.tool_table_path.exists():
            return False
        try:
            self.tool_table = ToolTable.load(self.tool_table_path)
            return True
        except (OSError, ValueError) as exc:
            self.state = replace(
                self.state,
                status_message=f"Could not load tool table from {self.tool_table_path}: {exc}",
            )
            return False

    def save_tool_table(self) -> bool:
        if self.tool_table_path is None:
            return True
        try:
            self.tool_table_path.parent.mkdir(parents=True, exist_ok=True)
            self.tool_table.save(self.tool_table_path)
            return True
        except OSError:
            self.state = replace(
                self.state,
                status_message=f"Could not save tool table to {self.tool_table_path}",
            )
            return False

    def save_settings(self) -> None:
        if self.settings_path is None:
            return
        data = {
            "display_mode": self.state.display_mode,
            "active_tool": self.state.active_tool,
            "turret_station": self.state.turret_station,
            "work_x_offset_mm": self.state.work_x_offset_mm,
            "work_z_offset_mm": self.state.work_z_offset_mm,
            "tool_x_offset_mm": self.state.tool_x_offset_mm,
            "tool_z_offset_mm": self.state.tool_z_offset_mm,
            "soft_limits_enabled": self.state.soft_limits_enabled,
            "x_min_limit_mm": self.state.x_min_limit_mm,
            "x_max_limit_mm": self.state.x_max_limit_mm,
            "z_min_limit_mm": self.state.z_min_limit_mm,
            "z_max_limit_mm": self.state.z_max_limit_mm,
        }
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        except OSError:
            self.state = replace(
                self.state,
                status_message=f"Could not save settings to {self.settings_path}",
            )

    def limits_error_for_work_target(
        self,
        target_work_x_mm: float,
        target_work_z_mm: float,
        context: str,
    ) -> str | None:
        target_machine_x = (
            target_work_x_mm
            - self.state.work_x_offset_mm
            - self.state.tool_x_offset_mm
        )
        target_machine_z = (
            target_work_z_mm
            - self.state.work_z_offset_mm
            - self.state.tool_z_offset_mm
        )
        return self._limits_error_for_target(target_machine_x, target_machine_z, context)

    def _merge_tool_state(self, backend_state: MachineState) -> MachineState:
        return replace(
            backend_state,
            active_tool=self.state.active_tool,
            turret_station=self.state.turret_station,
            work_x_offset_mm=self.state.work_x_offset_mm,
            work_z_offset_mm=self.state.work_z_offset_mm,
            tool_x_offset_mm=self.state.tool_x_offset_mm,
            tool_z_offset_mm=self.state.tool_z_offset_mm,
            pending_tool=self.state.pending_tool,
            pending_turret_station=self.state.pending_turret_station,
            display_mode=self.state.display_mode,
            soft_limits_enabled=self.state.soft_limits_enabled,
            x_min_limit_mm=self.state.x_min_limit_mm,
            x_max_limit_mm=self.state.x_max_limit_mm,
            z_min_limit_mm=self.state.z_min_limit_mm,
            z_max_limit_mm=self.state.z_max_limit_mm,
        )

    @staticmethod
    def _preserved_state_kwargs(state: MachineState) -> dict[str, object]:
        return {
            "active_tool": state.active_tool,
            "turret_station": state.turret_station,
            "work_x_offset_mm": state.work_x_offset_mm,
            "work_z_offset_mm": state.work_z_offset_mm,
            "tool_x_offset_mm": state.tool_x_offset_mm,
            "tool_z_offset_mm": state.tool_z_offset_mm,
            "pending_tool": state.pending_tool,
            "pending_turret_station": state.pending_turret_station,
            "display_mode": state.display_mode,
            "soft_limits_enabled": state.soft_limits_enabled,
            "x_min_limit_mm": state.x_min_limit_mm,
            "x_max_limit_mm": state.x_max_limit_mm,
            "z_min_limit_mm": state.z_min_limit_mm,
            "z_max_limit_mm": state.z_max_limit_mm,
        }

    def _limits_error_for_target(
        self,
        target_x_mm: float,
        target_z_mm: float,
        context: str,
    ) -> str | None:
        if not self.state.soft_limits_enabled:
            return None
        if not (self.state.x_min_limit_mm <= target_x_mm <= self.state.x_max_limit_mm):
            return (
                f"{context}: X target {target_x_mm:+0.3f} outside soft limits "
                f"{self.state.x_min_limit_mm:+0.3f}..{self.state.x_max_limit_mm:+0.3f}"
            )
        if not (self.state.z_min_limit_mm <= target_z_mm <= self.state.z_max_limit_mm):
            return (
                f"{context}: Z target {target_z_mm:+0.3f} outside soft limits "
                f"{self.state.z_min_limit_mm:+0.3f}..{self.state.z_max_limit_mm:+0.3f}"
            )
        return None

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

    def _refresh_active_tool_offsets_from_table(self) -> None:
        active = self.tool_table.get(self.state.active_tool)
        if active is None:
            return
        self.state = replace(
            self.state,
            turret_station=(
                active.station
                if self._is_turret_station(active.station)
                else self.state.turret_station
            ),
            tool_x_offset_mm=active.x_offset_mm,
            tool_z_offset_mm=active.z_offset_mm,
        )

    def _apply_active_tool(self, tool: ToolRecord, station: int | None) -> None:
        self.state = replace(
            self.state,
            active_tool=tool.tool_number,
            turret_station=station,
            tool_x_offset_mm=tool.x_offset_mm,
            tool_z_offset_mm=tool.z_offset_mm,
            pending_tool=None,
            pending_turret_station=None,
            status_message=(
                f"Active tool T{tool.tool_number}"
                + (f" station {station}" if station is not None else "")
            ),
        )
        self.save_settings()

    def _selected_tool_number(self, tool_number: int | None) -> int | None:
        selected_tool = self.state.active_tool if tool_number is None else tool_number
        if selected_tool <= 0:
            self._mark_rejected("No active tool selected for touch-off")
            return None
        return selected_tool

    def _teach_tool_offset(
        self,
        tool_number: int,
        *,
        x_offset_mm: float | None = None,
        z_offset_mm: float | None = None,
    ) -> bool:
        tool = self.tool_table.ensure_tool(tool_number)
        updated = replace(
            tool,
            x_offset_mm=tool.x_offset_mm if x_offset_mm is None else x_offset_mm,
            z_offset_mm=tool.z_offset_mm if z_offset_mm is None else z_offset_mm,
        )
        self.tool_table.upsert(updated)
        if tool_number == self.state.active_tool:
            self.state = replace(
                self.state,
                tool_x_offset_mm=updated.x_offset_mm,
                tool_z_offset_mm=updated.z_offset_mm,
            )
        if not self.save_tool_table():
            return False
        self.save_settings()
        axis = "X" if x_offset_mm is not None else "Z"
        self.state = replace(
            self.state,
            status_message=(
                f"T{tool_number} {axis} offset taught: "
                f"X {updated.x_offset_mm:+0.3f} Z {updated.z_offset_mm:+0.3f}"
            ),
        )
        return True

    def _mark_pending_tool(
        self,
        tool_number: int,
        station: int | None,
        message: str,
    ) -> None:
        self.state = replace(
            self.state,
            pending_tool=tool_number,
            pending_turret_station=station,
            status_message=message,
        )

    def _valid_station(self, station: int, label: str) -> bool:
        if not 1 <= station <= 8:
            self._mark_rejected(f"{label} must be in range 1..8")
            return False
        return True

    @staticmethod
    def _is_turret_station(station: int | None) -> bool:
        return station is not None and 1 <= station <= 8
