from __future__ import annotations

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label

from tcl_lathe_hmi.machine import MachineService, MachineState
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
)
from tcl_lathe_hmi.ui.form_values import parse_number
from tcl_lathe_hmi.ui.widgets import bind_release


class SetupPanel(BoxLayout):
    def __init__(self, *, service: MachineService, **kwargs):
        super().__init__(orientation="vertical", spacing=10, **kwargs)
        self.service = service
        paint(self, PANEL)
        self._build()
        self._load_limit_fields_from_state()

    def _build(self) -> None:
        top = BoxLayout(orientation="horizontal", spacing=10, size_hint_y=0.36)
        top.add_widget(self._build_coordinates_box())
        top.add_widget(self._build_recovery_box())
        self.add_widget(top)

        bottom = BoxLayout(orientation="horizontal", spacing=10)
        bottom.add_widget(self._build_limits_box())
        bottom.add_widget(self._build_homing_box())
        self.add_widget(bottom)

    def _build_coordinates_box(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", spacing=8)
        paint(box, PANEL_ALT)
        box.add_widget(section_label("Coordinates"))

        mode_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        work = action_button("Work DRO", BLUE)
        machine = action_button("Machine DRO", BUTTON)
        bind_release(work, lambda *_: self._set_display_mode("work"))
        bind_release(machine, lambda *_: self._set_display_mode("machine"))
        mode_row.add_widget(work)
        mode_row.add_widget(machine)
        box.add_widget(mode_row)

        self.coord_status = status_text("Work offsets X +0.000 Z +0.000")
        box.add_widget(self.coord_status)

        set_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        self.set_x_input = field_input("0.0", title_text="Set Current X")
        self.set_z_input = field_input("0.0", title_text="Set Current Z")
        set_x = action_button("Set X", BLUE, width=90)
        set_z = action_button("Set Z", BLUE, width=90)
        bind_release(set_x, lambda *_: self._set_current_axis("X"))
        bind_release(set_z, lambda *_: self._set_current_axis("Z"))
        set_row.add_widget(self.set_x_input)
        set_row.add_widget(set_x)
        set_row.add_widget(self.set_z_input)
        set_row.add_widget(set_z)
        box.add_widget(set_row)

        zero_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        zero_x = action_button("Zero X", GREEN)
        zero_z = action_button("Zero Z", GREEN)
        clear = action_button("Clear Offsets", AMBER)
        bind_release(zero_x, lambda *_: self._zero_axis("X"))
        bind_release(zero_z, lambda *_: self._zero_axis("Z"))
        bind_release(clear, lambda *_: self._clear_offsets())
        zero_row.add_widget(zero_x)
        zero_row.add_widget(zero_z)
        zero_row.add_widget(clear)
        box.add_widget(zero_row)
        return box

    def _build_recovery_box(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", spacing=8)
        paint(box, PANEL_ALT)
        box.add_widget(section_label("Recovery"))
        self.recovery_status = status_text("Controller recovery actions")
        box.add_widget(self.recovery_status)

        row1 = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        clear_error = action_button("Clear Error", AMBER)
        reconnect = action_button("Reconnect", BLUE)
        bind_release(clear_error, lambda *_: self._clear_error())
        bind_release(reconnect, lambda *_: self._reconnect())
        row1.add_widget(clear_error)
        row1.add_widget(reconnect)
        box.add_widget(row1)

        row2 = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        connect = action_button("Connect", GREEN)
        disconnect = action_button("Disconnect", RED)
        bind_release(connect, lambda *_: self._connect())
        bind_release(disconnect, lambda *_: self._disconnect())
        row2.add_widget(connect)
        row2.add_widget(disconnect)
        box.add_widget(row2)
        return box

    def _build_limits_box(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", spacing=8)
        paint(box, PANEL_ALT)
        box.add_widget(section_label("Soft Limits"))

        grid = GridLayout(cols=2, spacing=8, size_hint_y=None, height=220)
        self.x_min_input = field_input("-100.0", title_text="X Min Limit")
        self.x_max_input = field_input("100.0", title_text="X Max Limit")
        self.z_min_input = field_input("-100.0", title_text="Z Min Limit")
        self.z_max_input = field_input("100.0", title_text="Z Max Limit")
        for label, widget in (
            ("X min", self.x_min_input),
            ("X max", self.x_max_input),
            ("Z min", self.z_min_input),
            ("Z max", self.z_max_input),
        ):
            grid.add_widget(Label(text=label, color=MUTED, font_size=20))
            grid.add_widget(widget)
        box.add_widget(grid)

        row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        self.limit_toggle = action_button("Limits On", GREEN)
        apply_limits = action_button("Apply Limits", BLUE)
        bind_release(self.limit_toggle, lambda *_: self._toggle_limits())
        bind_release(apply_limits, lambda *_: self._apply_limits())
        row.add_widget(self.limit_toggle)
        row.add_widget(apply_limits)
        box.add_widget(row)

        self.limit_status = status_text("Soft limits enabled")
        box.add_widget(self.limit_status)
        return box

    def _build_homing_box(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", spacing=8)
        paint(box, PANEL_ALT)
        box.add_widget(section_label("Homing"))
        self.homing_status = status_text("Homing unavailable: FRED/Python does not expose limit switch homing yet")
        box.add_widget(self.homing_status)

        row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        home_x = action_button("Home X", BUTTON)
        home_z = action_button("Home Z", BUTTON)
        home_all = action_button("Home All", BUTTON)
        bind_release(home_x, lambda *_: self._home_axis("X"))
        bind_release(home_z, lambda *_: self._home_axis("Z"))
        bind_release(home_all, lambda *_: self._home_axis("X/Z"))
        row.add_widget(home_x)
        row.add_widget(home_z)
        row.add_widget(home_all)
        box.add_widget(row)
        return box

    def refresh(self, state: MachineState) -> None:
        self.coord_status.text = (
            f"Mode {state.display_mode.upper()}  "
            f"work offset X {state.work_x_offset_mm:+0.3f} Z {state.work_z_offset_mm:+0.3f}"
        )
        self.limit_toggle.text = "Limits On" if state.soft_limits_enabled else "Limits Off"
        self.limit_toggle.background_color = GREEN if state.soft_limits_enabled else AMBER
        self.limit_status.text = (
            f"X {state.x_min_limit_mm:+0.3f}..{state.x_max_limit_mm:+0.3f}  "
            f"Z {state.z_min_limit_mm:+0.3f}..{state.z_max_limit_mm:+0.3f}"
        )
        self.homing_status.text = (
            "Homing unavailable: FRED/Python does not expose limit switch homing yet"
        )
        self.recovery_status.text = state.status_message

    def _load_limit_fields_from_state(self) -> None:
        state = self.service.state
        self.x_min_input.set_value(state.x_min_limit_mm)
        self.x_max_input.set_value(state.x_max_limit_mm)
        self.z_min_input.set_value(state.z_min_limit_mm)
        self.z_max_input.set_value(state.z_max_limit_mm)

    def _set_display_mode(self, mode: str) -> None:
        self.service.set_display_mode(mode)
        self.refresh(self.service.state)

    def _set_current_axis(self, axis: str) -> None:
        if axis == "X":
            self.service.set_work_position(x_mm=parse_number(self.set_x_input.text, 0.0))
        else:
            self.service.set_work_position(z_mm=parse_number(self.set_z_input.text, 0.0))
        self.refresh(self.service.state)

    def _zero_axis(self, axis: str) -> None:
        self.service.zero_work_axis(axis)
        self.refresh(self.service.state)

    def _clear_offsets(self) -> None:
        self.service.clear_work_offsets()
        self.refresh(self.service.state)

    def _toggle_limits(self) -> None:
        self.service.update_soft_limits(enabled=not self.service.state.soft_limits_enabled)
        self.refresh(self.service.state)

    def _apply_limits(self) -> None:
        ok = self.service.update_soft_limits(
            x_min=parse_number(self.x_min_input.text, self.service.state.x_min_limit_mm),
            x_max=parse_number(self.x_max_input.text, self.service.state.x_max_limit_mm),
            z_min=parse_number(self.z_min_input.text, self.service.state.z_min_limit_mm),
            z_max=parse_number(self.z_max_input.text, self.service.state.z_max_limit_mm),
        )
        self.limit_status.color = TEXT if ok else RED
        self.refresh(self.service.state)

    def _home_axis(self, axis: str) -> None:
        self.service.home_axis(axis)
        self.refresh(self.service.state)

    def _clear_error(self) -> None:
        self.service.clear_error()
        self.refresh(self.service.state)

    def _connect(self) -> None:
        self.service.connect()
        self.refresh(self.service.state)

    def _disconnect(self) -> None:
        self.service.disconnect()
        self.refresh(self.service.state)

    def _reconnect(self) -> None:
        self.service.reconnect()
        self.refresh(self.service.state)
