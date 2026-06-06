from __future__ import annotations

from pathlib import Path

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput

from tcl_lathe_hmi.machine import MachineService, MachineState
from tcl_lathe_hmi.tools import ToolRecord, ToolTable
from tcl_lathe_hmi.ui.controls import (
    AMBER,
    BLUE,
    BUTTON,
    GREEN,
    MUTED,
    PANEL,
    PANEL_ALT,
    RED,
    TEXT,
    action_button,
    field_input,
    paint,
    section_label,
    status_text,
    text_field,
)
from tcl_lathe_hmi.ui.form_values import optional_int, parse_number
from tcl_lathe_hmi.ui.widgets import bind_release


class ToolsPanel(BoxLayout):
    def __init__(self, *, service: MachineService, **kwargs):
        super().__init__(orientation="horizontal", spacing=10, **kwargs)
        self.service = service
        paint(self, PANEL)
        self._build()
        self._export_to_editor()

    def _build(self) -> None:
        table_side = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.56)
        paint(table_side, PANEL_ALT)
        table_side.add_widget(section_label("Tool Table"))

        self.table_editor = TextInput(
            text="",
            multiline=True,
            font_size=17,
            foreground_color=TEXT,
            background_color=(0.05, 0.05, 0.05, 1),
            cursor_color=TEXT,
        )
        table_side.add_widget(self.table_editor)

        path_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=54)
        self.tool_path_input = TextInput(
            text="lathe.tbl",
            multiline=False,
            font_size=20,
            foreground_color=TEXT,
            background_color=(0.06, 0.06, 0.06, 1),
            cursor_color=TEXT,
            padding=(8, 10, 8, 8),
        )
        load = action_button("Load", BUTTON, width=90)
        save = action_button("Save", BUTTON, width=90)
        import_btn = action_button("Import Text", BLUE, width=140)
        bind_release(load, lambda *_: self._load_table())
        bind_release(save, lambda *_: self._save_table())
        bind_release(import_btn, lambda *_: self._import_from_editor())
        path_row.add_widget(self.tool_path_input)
        path_row.add_widget(load)
        path_row.add_widget(save)
        path_row.add_widget(import_btn)
        table_side.add_widget(path_row)
        self.add_widget(table_side)

        edit_side = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.44)
        paint(edit_side, PANEL_ALT)
        edit_side.add_widget(section_label("Offsets / Change"))

        self.active_label = status_text("Active T0 P--")
        self.position_label = status_text("Machine X +0.000 Z +0.000  Work X +0.000 Z +0.000")
        self.pending_label = status_text("No pending tool change")
        edit_side.add_widget(self.active_label)
        edit_side.add_widget(self.position_label)
        edit_side.add_widget(self.pending_label)

        grid = GridLayout(cols=2, spacing=8, size_hint_y=None, height=240)
        self.tool_input = field_input("1", integer=True, title_text="Tool")
        self.station_input = field_input("1", integer=True, title_text="Station")
        self.x_offset_input = field_input("0.0", title_text="X Offset")
        self.z_offset_input = field_input("0.0", title_text="Z Offset")
        self.diameter_input = field_input("0.0", title_text="Diameter")
        self.comment_input = text_field("")
        for label, widget in (
            ("Tool T", self.tool_input),
            ("Station P", self.station_input),
            ("X offset", self.x_offset_input),
            ("Z offset", self.z_offset_input),
            ("Diameter D", self.diameter_input),
            ("Comment", self.comment_input),
        ):
            grid.add_widget(Label(text=label, color=MUTED, font_size=20))
            grid.add_widget(widget)
        edit_side.add_widget(grid)

        teach_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        self.teach_diameter_input = field_input("0.0", title_text="Measured Diameter")
        teach_z = action_button("Teach Z0", BLUE)
        teach_x = action_button("Teach X Dia", BLUE)
        bind_release(teach_z, lambda *_: self._teach_z0())
        bind_release(teach_x, lambda *_: self._teach_x_diameter())
        teach_row.add_widget(self.teach_diameter_input)
        teach_row.add_widget(teach_z)
        teach_row.add_widget(teach_x)
        edit_side.add_widget(teach_row)

        row1 = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        upsert = action_button("Upsert Tool", BLUE)
        set_active = action_button("Set Active", GREEN)
        bind_release(upsert, lambda *_: self._upsert_tool())
        bind_release(set_active, lambda *_: self._set_active_tool())
        row1.add_widget(upsert)
        row1.add_widget(set_active)
        edit_side.add_widget(row1)

        row2 = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        confirm = action_button("Confirm Pending", GREEN)
        auto = action_button("Use Manual Tab", BUTTON)
        auto.disabled = True
        bind_release(confirm, lambda *_: self._confirm_pending_tool())
        row2.add_widget(confirm)
        row2.add_widget(auto)
        edit_side.add_widget(row2)

        self.tool_status = status_text("Tool table ready")
        edit_side.add_widget(self.tool_status)
        self.add_widget(edit_side)

    def refresh(self, state: MachineState) -> None:
        station = "--" if state.turret_station is None else str(state.turret_station)
        self.active_label.text = (
            f"Active T{state.active_tool} P{station}  "
            f"Xoff {state.tool_x_offset_mm:+0.3f}  Zoff {state.tool_z_offset_mm:+0.3f}"
        )
        self.position_label.text = (
            f"Machine X {state.x_mm:+0.3f} Z {state.z_mm:+0.3f}  "
            f"Work X {state.work_x_mm:+0.3f} Z {state.work_z_mm:+0.3f}"
        )
        if state.pending_tool is None:
            self.pending_label.text = "No pending tool change"
            self.pending_label.color = MUTED
        else:
            pending_station = (
                "--"
                if state.pending_turret_station is None
                else str(state.pending_turret_station)
            )
            self.pending_label.text = f"Pending T{state.pending_tool} P{pending_station}"
            self.pending_label.color = AMBER

    def _upsert_tool(self) -> None:
        try:
            tool = self._tool_from_fields()
        except ValueError as exc:
            self._set_status(f"Tool edit failed: {exc}", RED)
            return
        if not self.service.upsert_tool(tool):
            self._set_status(self.service.state.status_message, RED)
            return
        self._export_to_editor()
        self._set_status(f"Saved {tool.display_name}", TEXT)

    def _set_active_tool(self) -> None:
        try:
            tool_number = int(parse_number(self.tool_input.text, -1))
        except ValueError:
            self._set_status("Invalid tool number", RED)
            return
        if self.service.set_active_tool(tool_number):
            self._set_status(self.service.state.status_message, GREEN)
            self._load_tool_fields(tool_number)
        else:
            self._set_status(self.service.state.status_message, RED)
        self.refresh(self.service.state)

    def _confirm_pending_tool(self) -> None:
        state = self.service.state
        if state.pending_tool is None:
            self._set_status("No pending tool change", AMBER)
            return
        if self.service.confirm_tool_change(state.pending_tool, state.pending_turret_station):
            self._set_status(self.service.state.status_message, GREEN)
            self._load_tool_fields(self.service.state.active_tool)
        else:
            self._set_status(self.service.state.status_message, RED)
        self.refresh(self.service.state)

    def _import_from_editor(self) -> bool:
        try:
            table = ToolTable.from_linuxcnc(self.table_editor.text)
        except ValueError as exc:
            self._set_status(f"Import failed: {exc}", RED)
            return False
        self.service.update_tool_table(table)
        self._export_to_editor()
        self._set_status(f"Imported {len(table.tools)} tool(s)", TEXT)
        self._load_tool_fields(self.service.state.active_tool)
        self.refresh(self.service.state)
        return True

    def _export_to_editor(self) -> None:
        self.table_editor.text = self.service.tool_table.export_linuxcnc()

    def _load_table(self) -> None:
        path = Path(self.tool_path_input.text).expanduser()
        try:
            table = ToolTable.load(path)
        except (OSError, ValueError) as exc:
            self._set_status(f"Load failed: {exc}", RED)
            return
        self.service.update_tool_table(table)
        self._export_to_editor()
        self._set_status(f"Loaded {path}", TEXT)
        self._load_tool_fields(self.service.state.active_tool)
        self.refresh(self.service.state)

    def _save_table(self) -> None:
        if not self._import_from_editor():
            return
        path = Path(self.tool_path_input.text).expanduser()
        try:
            self.service.tool_table.save(path)
        except OSError as exc:
            self._set_status(f"Save failed: {exc}", RED)
            return
        self._set_status(f"Saved {path}", TEXT)

    def _teach_z0(self) -> None:
        if self.service.teach_tool_z(0.0):
            self._export_to_editor()
            self._load_tool_fields(self.service.state.active_tool)
            self._set_status(self.service.state.status_message, GREEN)
        else:
            self._set_status(self.service.state.status_message, RED)
        self.refresh(self.service.state)

    def _teach_x_diameter(self) -> None:
        diameter = parse_number(self.teach_diameter_input.text, -1.0)
        if self.service.teach_tool_x(diameter):
            self._export_to_editor()
            self._load_tool_fields(self.service.state.active_tool)
            self._set_status(self.service.state.status_message, GREEN)
        else:
            self._set_status(self.service.state.status_message, RED)
        self.refresh(self.service.state)

    def _tool_from_fields(self) -> ToolRecord:
        tool_number = int(parse_number(self.tool_input.text, -1))
        if tool_number < 0:
            raise ValueError("tool number must be non-negative")
        station = optional_int(self.station_input.text)
        return ToolRecord(
            tool_number=tool_number,
            station=station,
            x_offset_mm=parse_number(self.x_offset_input.text, 0.0),
            z_offset_mm=parse_number(self.z_offset_input.text, 0.0),
            diameter_mm=parse_number(self.diameter_input.text, 0.0),
            comment=self.comment_input.text.strip(),
        )

    def _load_tool_fields(self, tool_number: int) -> None:
        tool = self.service.tool_table.get(tool_number)
        if tool is None:
            return
        self.tool_input.text = str(tool.tool_number)
        self.station_input.text = "" if tool.station is None else str(tool.station)
        self.x_offset_input.text = f"{tool.x_offset_mm:0.3f}"
        self.z_offset_input.text = f"{tool.z_offset_mm:0.3f}"
        self.diameter_input.text = f"{tool.diameter_mm:0.3f}"
        self.comment_input.text = tool.comment

    def _set_status(self, message: str, color) -> None:
        self.tool_status.text = message
        self.tool_status.color = color
