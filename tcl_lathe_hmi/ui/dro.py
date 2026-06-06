from __future__ import annotations

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

from tcl_lathe_hmi.machine import MachineState
from tcl_lathe_hmi.ui.controls import (
    AMBER,
    GREEN,
    MUTED,
    PANEL,
    PANEL_ALT,
    TEXT,
    axis_label,
    paint,
)


class MachineReadouts(BoxLayout):
    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_x", 0.62)
        super().__init__(orientation="vertical", spacing=10, **kwargs)
        paint(self, PANEL)

        self.x_value, self.x_detail = self._add_dro_row("X", "mm")
        self.z_value, self.z_detail = self._add_dro_row("Z", "mm")
        self.rpm_value, self.rpm_detail = self._add_spindle_row()

    def refresh(self, state: MachineState) -> None:
        self.x_value.text = f"{state.display_x_mm:+0.3f}"
        self.z_value.text = f"{state.display_z_mm:+0.3f}"
        self.x_detail.text = (
            f"{state.display_mode.upper()}  machine {state.x_mm:+0.3f}\n"
            f"work {state.work_x_offset_mm:+0.3f}  tool {state.tool_x_offset_mm:+0.3f}\n"
            f"counts {state.x_counts if state.x_counts is not None else '--'}"
        )
        self.z_detail.text = (
            f"{state.display_mode.upper()}  machine {state.z_mm:+0.3f}\n"
            f"work {state.work_z_offset_mm:+0.3f}  tool {state.tool_z_offset_mm:+0.3f}\n"
            f"counts {state.z_counts if state.z_counts is not None else '--'}"
        )

        self.rpm_value.text = f"{state.spindle.actual_rpm:0.0f}"
        speed_label = "AT SPEED" if state.spindle.at_speed else "RAMP"
        self.rpm_detail.text = (
            f"{state.spindle.direction_label}\n"
            f"S {state.spindle.target_rpm:0.0f}\n"
            f"{speed_label}"
        )
        self.rpm_detail.color = GREEN if state.spindle.at_speed else AMBER

    def _add_dro_row(self, axis: str, unit: str) -> tuple[Label, Label]:
        row = BoxLayout(orientation="horizontal", size_hint_y=0.34, spacing=8)
        paint(row, PANEL_ALT)
        row.add_widget(axis_label(axis, width=130))

        value = Label(text="+0.000", color=TEXT, font_size=92, bold=True, halign="right")
        value.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
        row.add_widget(value)

        side = BoxLayout(orientation="vertical", size_hint_x=None, width=190)
        side.add_widget(Label(text=unit, color=MUTED, font_size=28))
        detail = Label(text="counts --", color=MUTED, font_size=20)
        side.add_widget(detail)
        row.add_widget(side)

        self.add_widget(row)
        return value, detail

    def _add_spindle_row(self) -> tuple[Label, Label]:
        row = BoxLayout(orientation="horizontal", size_hint_y=0.32, spacing=8)
        paint(row, PANEL_ALT)
        row.add_widget(axis_label("RPM", width=130))

        value = Label(text="0", color=TEXT, font_size=74, bold=True, halign="right")
        value.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
        row.add_widget(value)

        detail = Label(
            text="Stopped",
            color=MUTED,
            font_size=24,
            size_hint_x=None,
            width=240,
        )
        row.add_widget(detail)

        self.add_widget(row)
        return value, detail
