from __future__ import annotations

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from tcl_lathe_hmi.machine import MachineService, MachineState
from tcl_lathe_hmi.tools import MAX_TOOL_NUMBER
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
        self.selected_tool = 1
        self.row_widgets: dict[int, tuple[Button, Label, Label, Label, Label]] = {}
        paint(self, PANEL)
        self._build()
        self._load_tool_fields(self.selected_tool)
        self.refresh_tools(load_fields=True)

    def _build(self) -> None:
        table_side = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.62)
        paint(table_side, PANEL_ALT)
        table_side.add_widget(section_label("Tool Table"))

        header = GridLayout(
            cols=5,
            spacing=4,
            size_hint_y=None,
            height=34,
        )
        for text in ("Tool", "Turret P", "X Offset", "Z Offset", "Description"):
            header.add_widget(self._grid_label(text, color=MUTED, bold=True))
        table_side.add_widget(header)

        self.tool_grid = GridLayout(
            cols=5,
            spacing=4,
            size_hint_y=None,
            row_force_default=True,
            row_default_height=44,
        )
        self.tool_grid.bind(minimum_height=self.tool_grid.setter("height"))
        for tool_number in range(1, MAX_TOOL_NUMBER + 1):
            select = action_button(f"T{tool_number}", BUTTON)
            select.font_size = 18
            bind_release(select, lambda *_args, tool=tool_number: self._select_tool(tool))
            station = self._grid_label("--")
            x_offset = self._grid_label("+0.000")
            z_offset = self._grid_label("+0.000")
            description = self._grid_label("")
            description.shorten = True
            description.shorten_from = "right"
            self.row_widgets[tool_number] = (
                select,
                station,
                x_offset,
                z_offset,
                description,
            )
            self.tool_grid.add_widget(select)
            self.tool_grid.add_widget(station)
            self.tool_grid.add_widget(x_offset)
            self.tool_grid.add_widget(z_offset)
            self.tool_grid.add_widget(description)

        scroller = ScrollView()
        scroller.add_widget(self.tool_grid)
        table_side.add_widget(scroller)
        self.add_widget(table_side)

        edit_side = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.38)
        paint(edit_side, PANEL_ALT)
        edit_side.add_widget(section_label("Offsets / Change"))

        self.active_label = status_text("Active T0 P--")
        self.position_label = status_text("Machine X +0.000 Z +0.000  Work X +0.000 Z +0.000")
        self.pending_label = status_text("No pending tool change")
        self.selected_label = status_text("Editing T1")
        edit_side.add_widget(self.active_label)
        edit_side.add_widget(self.position_label)
        edit_side.add_widget(self.pending_label)
        edit_side.add_widget(self.selected_label)

        grid = GridLayout(cols=2, spacing=8, size_hint_y=None, height=214)
        self.station_input = field_input("1", integer=True, title_text="Turret Station")
        self.x_offset_input = field_input("0.0", title_text="X Offset")
        self.z_offset_input = field_input("0.0", title_text="Z Offset")
        self.description_input = text_field("")
        for label, widget in (
            ("Turret P", self.station_input),
            ("X offset", self.x_offset_input),
            ("Z offset", self.z_offset_input),
            ("Description", self.description_input),
        ):
            grid.add_widget(Label(text=label, color=MUTED, font_size=20))
            grid.add_widget(widget)
        edit_side.add_widget(grid)

        row1 = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        save = action_button("Save Tool", BLUE)
        clear_station = action_button("Clear P", AMBER)
        set_active = action_button("Set Active", GREEN)
        bind_release(save, lambda *_: self._save_selected_tool())
        bind_release(clear_station, lambda *_: self._clear_selected_station())
        bind_release(set_active, lambda *_: self._set_active_tool())
        row1.add_widget(save)
        row1.add_widget(clear_station)
        row1.add_widget(set_active)
        edit_side.add_widget(row1)

        teach_inputs = GridLayout(cols=2, spacing=8, size_hint_y=None, height=96)
        self.known_z_input = field_input("0.0", title_text="Known Z")
        self.teach_diameter_input = field_input("0.0", title_text="Measured Diameter")
        for label, widget in (
            ("Known Z", self.known_z_input),
            ("Measured dia", self.teach_diameter_input),
        ):
            teach_inputs.add_widget(Label(text=label, color=MUTED, font_size=20))
            teach_inputs.add_widget(widget)
        edit_side.add_widget(teach_inputs)

        teach_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        teach_z = action_button("Teach Z", BLUE)
        teach_x = action_button("Teach X Dia", BLUE)
        bind_release(teach_z, lambda *_: self._teach_z())
        bind_release(teach_x, lambda *_: self._teach_x_diameter())
        teach_row.add_widget(teach_z)
        teach_row.add_widget(teach_x)
        edit_side.add_widget(teach_row)

        row2 = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        confirm = action_button("Confirm Pending", GREEN)
        bind_release(confirm, lambda *_: self._confirm_pending_tool())
        row2.add_widget(confirm)
        edit_side.add_widget(row2)

        self.tool_status = status_text("Tool setup ready")
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
        self.refresh_tools()

    def refresh_tools(self, *, load_fields: bool = False) -> None:
        self._refresh_tool_grid()
        if load_fields:
            self._load_tool_fields(self.selected_tool)

    def _select_tool(self, tool_number: int) -> None:
        self.selected_tool = tool_number
        self._load_tool_fields(tool_number)
        self._refresh_tool_grid()

    def _save_selected_tool(self) -> None:
        try:
            station = optional_int(self.station_input.text)
            x_offset = parse_number(self.x_offset_input.text, 0.0)
            z_offset = parse_number(self.z_offset_input.text, 0.0)
        except ValueError as exc:
            self._set_status(f"Tool edit failed: {exc}", RED)
            return

        if not self.service.update_tool(
            self.selected_tool,
            station=station,
            x_offset_mm=x_offset,
            z_offset_mm=z_offset,
            description=self.description_input.text,
        ):
            self._set_status(self.service.state.status_message, RED)
            return
        self.refresh_tools(load_fields=True)
        self._set_status(self.service.state.status_message, TEXT)

    def _clear_selected_station(self) -> None:
        if not self.service.assign_tool_station(self.selected_tool, None):
            self._set_status(self.service.state.status_message, RED)
            return
        self.refresh_tools(load_fields=True)
        self._set_status(self.service.state.status_message, TEXT)

    def _set_active_tool(self) -> None:
        if self.service.set_active_tool(self.selected_tool):
            self._set_status(self.service.state.status_message, GREEN)
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
            self._select_tool(self.service.state.active_tool)
        else:
            self._set_status(self.service.state.status_message, RED)
        self.refresh(self.service.state)

    def _teach_z(self) -> None:
        if not self._selected_tool_is_active():
            return
        known_z = parse_number(self.known_z_input.text, 0.0)
        if self.service.teach_tool_z(known_z, self.selected_tool):
            self.refresh_tools(load_fields=True)
            self._set_status(self.service.state.status_message, GREEN)
        else:
            self._set_status(self.service.state.status_message, RED)
        self.refresh(self.service.state)

    def _teach_x_diameter(self) -> None:
        if not self._selected_tool_is_active():
            return
        diameter = parse_number(self.teach_diameter_input.text, -1.0)
        if self.service.teach_tool_x(diameter, self.selected_tool):
            self.refresh_tools(load_fields=True)
            self._set_status(self.service.state.status_message, GREEN)
        else:
            self._set_status(self.service.state.status_message, RED)
        self.refresh(self.service.state)

    def _selected_tool_is_active(self) -> bool:
        if self.service.state.active_tool == self.selected_tool:
            return True
        self._set_status(f"Set T{self.selected_tool} active before teaching offsets", RED)
        return False

    def _load_tool_fields(self, tool_number: int) -> None:
        tool = self.service.tool_table.get(tool_number)
        if tool is None:
            return
        station = self.service.station_for_tool(tool_number)
        self.selected_label.text = f"Editing T{tool_number}"
        self.station_input.text = "" if station is None else str(station)
        self.x_offset_input.text = f"{tool.x_offset_mm:0.3f}"
        self.z_offset_input.text = f"{tool.z_offset_mm:0.3f}"
        self.description_input.text = tool.description

    def _refresh_tool_grid(self) -> None:
        active_tool = self.service.state.active_tool
        for tool in self.service.tool_table.tools:
            select, station_label, x_label, z_label, description_label = self.row_widgets[
                tool.tool_number
            ]
            station = self.service.station_for_tool(tool.tool_number)
            station_label.text = "--" if station is None else f"P{station}"
            x_label.text = f"{tool.x_offset_mm:+0.3f}"
            z_label.text = f"{tool.z_offset_mm:+0.3f}"
            description_label.text = tool.description
            if tool.tool_number == self.selected_tool:
                select.background_color = BLUE
            elif tool.tool_number == active_tool:
                select.background_color = GREEN
            else:
                select.background_color = BUTTON

    @staticmethod
    def _grid_label(text: str, *, color=TEXT, bold: bool = False) -> Label:
        label = Label(
            text=text,
            color=color,
            font_size=18,
            bold=bold,
            halign="left",
            valign="middle",
        )
        label.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
        return label

    def _set_status(self, message: str, color) -> None:
        self.tool_status.text = message
        self.tool_status.color = color
