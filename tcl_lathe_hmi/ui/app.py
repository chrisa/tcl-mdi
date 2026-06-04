from __future__ import annotations

from dataclasses import replace
from typing import Callable

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

from tcl_lathe_hmi.backends import create_backend
from tcl_lathe_hmi.config import JOG_INCREMENTS_MM, MachineConfig
from tcl_lathe_hmi.machine import MachineService, MachineState


BG = (0.07, 0.08, 0.09, 1)
PANEL = (0.12, 0.13, 0.14, 1)
PANEL_ALT = (0.16, 0.16, 0.17, 1)
TEXT = (0.93, 0.94, 0.92, 1)
MUTED = (0.62, 0.66, 0.68, 1)
GREEN = (0.18, 0.56, 0.34, 1)
BLUE = (0.16, 0.36, 0.62, 1)
AMBER = (0.78, 0.52, 0.14, 1)
RED = (0.64, 0.18, 0.18, 1)
BUTTON = (0.24, 0.25, 0.27, 1)


class TclLatheHmiApp(App):
    title = "TCL Lathe HMI"

    def __init__(self, *, backend_name: str = "sim", **kwargs):
        super().__init__(**kwargs)
        self.machine_config = MachineConfig()
        self.backend_name = backend_name
        self.service = MachineService(create_backend(backend_name, self.machine_config))
        self.panel: ManualPanel | None = None
        self._poll_event = None

    def build(self):
        self.panel = ManualPanel(
            service=self.service,
            config=self.machine_config,
            initial_backend=self.backend_name,
            on_backend_change=self.switch_backend,
        )
        if self.backend_name == "sim":
            self.service.connect()
        self.panel.refresh(self.service.state)
        self._poll_event = Clock.schedule_interval(
            self._poll,
            self.machine_config.ui_poll_interval_s,
        )
        return self.panel

    def on_stop(self):
        if self._poll_event is not None:
            self._poll_event.cancel()
        self.service.disconnect()

    def switch_backend(self, backend_name: str) -> None:
        self.backend_name = backend_name
        self.service.set_backend(create_backend(backend_name, self.machine_config))
        if backend_name == "sim":
            self.service.connect()
        if self.panel is not None:
            self.panel.refresh(self.service.state)

    def _poll(self, _dt):
        if self.panel is None:
            return
        state = self.service.poll()
        self.panel.refresh(state)


class ManualPanel(BoxLayout):
    def __init__(
        self,
        *,
        service: MachineService,
        config: MachineConfig,
        initial_backend: str,
        on_backend_change: Callable[[str], None],
        **kwargs,
    ):
        super().__init__(orientation="vertical", spacing=10, padding=10, **kwargs)
        self.service = service
        self.config = config
        self.on_backend_change = on_backend_change
        self.increment_mm = JOG_INCREMENTS_MM[1]
        self.jog_mode = "feed"
        self.command_widgets: list[Button | ToggleButton | TextInput] = []

        paint(self, BG)
        self._build(initial_backend)

    def _build(self, initial_backend: str) -> None:
        self.add_widget(self._build_status_bar(initial_backend))

        body = BoxLayout(orientation="horizontal", spacing=10)
        body.add_widget(self._build_readouts())
        body.add_widget(self._build_controls())
        self.add_widget(body)

        self.add_widget(self._build_nav_bar())

    def _build_status_bar(self, initial_backend: str) -> BoxLayout:
        bar = BoxLayout(orientation="horizontal", size_hint_y=None, height=72, spacing=8)
        paint(bar, PANEL)

        self.backend_spinner = Spinner(
            text=backend_label(initial_backend),
            values=("Simulator", "FRED USB"),
            size_hint_x=None,
            width=170,
            font_size=22,
            background_normal="",
            background_color=BUTTON,
            color=TEXT,
        )
        self.backend_spinner.bind(text=self._backend_selected)
        bar.add_widget(self.backend_spinner)

        self.connect_button = action_button("Connect", BLUE, width=150)
        self.connect_button.bind(on_release=lambda *_: self._toggle_connection())
        bar.add_widget(self.connect_button)

        self.status_label = Label(
            text="DISCONNECTED",
            color=TEXT,
            font_size=28,
            bold=True,
            size_hint_x=0.22,
        )
        bar.add_widget(self.status_label)

        self.message_label = Label(
            text="",
            color=MUTED,
            font_size=18,
            halign="left",
            valign="middle",
        )
        self.message_label.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
        bar.add_widget(self.message_label)

        self.tool_label = Label(text="T0", color=TEXT, font_size=24, size_hint_x=None, width=90)
        bar.add_widget(self.tool_label)

        self.home_label = Label(
            text="HOME --",
            color=MUTED,
            font_size=22,
            size_hint_x=None,
            width=140,
        )
        bar.add_widget(self.home_label)
        return bar

    def _build_readouts(self) -> BoxLayout:
        panel = BoxLayout(orientation="vertical", spacing=10, size_hint_x=0.62)
        paint(panel, PANEL)

        self.x_value, self.x_detail = self._add_dro_row(panel, "X", "mm")
        self.z_value, self.z_detail = self._add_dro_row(panel, "Z", "mm")

        spindle_row = BoxLayout(orientation="horizontal", size_hint_y=0.32, spacing=8)
        paint(spindle_row, PANEL_ALT)
        spindle_row.add_widget(axis_label("RPM", width=130))
        self.rpm_value = Label(text="0", color=TEXT, font_size=74, bold=True, halign="right")
        self.rpm_value.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
        spindle_row.add_widget(self.rpm_value)
        self.rpm_detail = Label(
            text="Stopped",
            color=MUTED,
            font_size=24,
            size_hint_x=None,
            width=240,
        )
        spindle_row.add_widget(self.rpm_detail)
        panel.add_widget(spindle_row)

        return panel

    def _add_dro_row(self, parent: BoxLayout, axis: str, unit: str) -> tuple[Label, Label]:
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

        parent.add_widget(row)
        return value, detail

    def _build_controls(self) -> BoxLayout:
        panel = BoxLayout(orientation="vertical", spacing=10, size_hint_x=0.38)
        paint(panel, PANEL)

        panel.add_widget(self._build_jog_settings())
        panel.add_widget(self._build_jog_buttons())
        panel.add_widget(self._build_spindle_controls())
        return panel

    def _build_jog_settings(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", spacing=8, size_hint_y=0.28)
        paint(box, PANEL_ALT)

        box.add_widget(section_label("Jog"))

        increments = BoxLayout(orientation="horizontal", spacing=6)
        for index, inc in enumerate(JOG_INCREMENTS_MM):
            btn = toggle_button(f"{inc:0.3f}", group="jog_increment")
            if index == 1:
                btn.state = "down"
            btn.bind(on_release=lambda button, value=inc: self._set_increment(button, value))
            self.command_widgets.append(btn)
            increments.add_widget(btn)
        box.add_widget(increments)

        row = BoxLayout(orientation="horizontal", spacing=8)
        feed = toggle_button("Feed", group="jog_mode")
        feed.state = "down"
        feed.bind(on_release=lambda button: self._set_jog_mode(button, "feed"))
        rapid = toggle_button("Rapid", group="jog_mode")
        rapid.bind(on_release=lambda button: self._set_jog_mode(button, "rapid"))
        self.command_widgets.extend([feed, rapid])
        row.add_widget(feed)
        row.add_widget(rapid)

        self.feed_input = numeric_input(str(self.config.jog_feed), width=120)
        self.command_widgets.append(self.feed_input)
        row.add_widget(Label(text="F", color=MUTED, font_size=24, size_hint_x=None, width=30))
        row.add_widget(self.feed_input)
        box.add_widget(row)
        return box

    def _build_jog_buttons(self) -> GridLayout:
        grid = GridLayout(cols=3, rows=3, spacing=8, size_hint_y=0.38)
        paint(grid, PANEL_ALT)

        z_plus = jog_button("Z+")
        x_minus = jog_button("X-")
        stop = action_button("STOP", RED)
        x_plus = jog_button("X+")
        z_minus = jog_button("Z-")
        buttons: list[Button | None] = [
            None,
            z_plus,
            None,
            x_minus,
            stop,
            x_plus,
            None,
            z_minus,
            None,
        ]

        for btn in buttons:
            if btn is None:
                spacer = Label(text="")
                grid.add_widget(spacer)
                continue
            self.command_widgets.append(btn)
            grid.add_widget(btn)

        z_plus.bind(on_release=lambda *_: self._jog(z_sign=1.0))
        x_minus.bind(on_release=lambda *_: self._jog(x_sign=-1.0))
        stop.bind(on_release=lambda *_: self._set_status("Stop requested; no abort primitive yet"))
        x_plus.bind(on_release=lambda *_: self._jog(x_sign=1.0))
        z_minus.bind(on_release=lambda *_: self._jog(z_sign=-1.0))
        return grid

    def _build_spindle_controls(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", spacing=8, size_hint_y=0.34)
        paint(box, PANEL_ALT)
        box.add_widget(section_label("Spindle"))

        target = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=0.36)
        target.add_widget(Label(text="Target", color=MUTED, font_size=24, size_hint_x=0.45))
        self.rpm_input = numeric_input(str(int(self.config.default_spindle_rpm)))
        self.command_widgets.append(self.rpm_input)
        target.add_widget(self.rpm_input)
        box.add_widget(target)

        row = BoxLayout(orientation="horizontal", spacing=8)
        forward = action_button("Forward", GREEN)
        reverse = action_button("Reverse", AMBER)
        stop = action_button("Stop", RED)
        forward.bind(on_release=lambda *_: self._spindle(on=True, forward=True))
        reverse.bind(on_release=lambda *_: self._spindle(on=True, forward=False))
        stop.bind(on_release=lambda *_: self._spindle(on=False, forward=True))
        self.command_widgets.extend([forward, reverse, stop])
        row.add_widget(forward)
        row.add_widget(reverse)
        row.add_widget(stop)
        box.add_widget(row)
        return box

    def _build_nav_bar(self) -> BoxLayout:
        nav = BoxLayout(orientation="horizontal", size_hint_y=None, height=64, spacing=8)
        paint(nav, PANEL)
        nav.add_widget(action_button("Manual", BLUE))
        for label in ("MDI", "Program", "Tools", "Setup"):
            btn = action_button(label, BUTTON)
            btn.disabled = True
            nav.add_widget(btn)
        return nav

    def refresh(self, state: MachineState | None = None) -> None:
        state = state or self.service.state
        self.status_label.text = state.controller_label
        self.status_label.color = status_color(state)
        self.message_label.text = state.status_message
        self.connect_button.text = "Clear Error" if state.error else ("Disconnect" if state.connected else "Connect")
        self.connect_button.background_color = RED if state.error else (AMBER if state.connected else BLUE)

        self.x_value.text = f"{state.x_mm:+0.3f}"
        self.z_value.text = f"{state.z_mm:+0.3f}"
        self.x_detail.text = f"counts {state.x_counts if state.x_counts is not None else '--'}"
        self.z_detail.text = f"counts {state.z_counts if state.z_counts is not None else '--'}"

        self.rpm_value.text = f"{state.spindle.actual_rpm:0.0f}"
        speed_label = "AT SPEED" if state.spindle.at_speed else "RAMP"
        self.rpm_detail.text = f"{state.spindle.direction_label}\nS {state.spindle.target_rpm:0.0f}\n{speed_label}"
        self.rpm_detail.color = GREEN if state.spindle.at_speed else AMBER

        self.tool_label.text = f"T{state.active_tool}"
        self.home_label.text = f"HOME {'X' if state.homed_x else '-'}{'Z' if state.homed_z else '-'}"
        self.home_label.color = GREEN if state.homed_x and state.homed_z else MUTED

        for widget in self.command_widgets:
            widget.disabled = not state.can_accept_commands

    def _backend_selected(self, _spinner: Spinner, label: str) -> None:
        backend = "fred" if label == "FRED USB" else "sim"
        self.on_backend_change(backend)

    def _toggle_connection(self) -> None:
        state = self.service.state
        if state.error:
            self.service.clear_error()
        elif state.connected:
            self.service.disconnect()
        else:
            self.service.connect()
        self.refresh(self.service.state)

    def _set_increment(self, button: ToggleButton, value: float) -> None:
        if button.state == "down":
            self.increment_mm = value

    def _set_jog_mode(self, button: ToggleButton, mode: str) -> None:
        if button.state == "down":
            self.jog_mode = mode

    def _jog(self, *, x_sign: float = 0.0, z_sign: float = 0.0) -> None:
        feed = int(parse_number(self.feed_input.text, self.config.jog_feed))
        ok = self.service.jog_delta(
            x_mm=x_sign * self.increment_mm,
            z_mm=z_sign * self.increment_mm,
            mode=self.jog_mode,
            feed=feed,
            slew=self.config.jog_slew,
        )
        if not ok:
            self._set_status(self.service.state.status_message)
        self.refresh(self.service.state)

    def _spindle(self, *, on: bool, forward: bool) -> None:
        rpm = parse_number(self.rpm_input.text, self.config.default_spindle_rpm)
        ok = self.service.set_spindle(on=on, rpm=rpm, forward=forward)
        if not ok:
            self._set_status(self.service.state.status_message)
        self.refresh(self.service.state)

    def _set_status(self, message: str) -> None:
        self.service.state = replace(self.service.state, status_message=message)
        self.refresh(self.service.state)


def paint(widget, color) -> None:
    with widget.canvas.before:
        Color(*color)
        rect = Rectangle(pos=widget.pos, size=widget.size)

    def update_rect(instance, *_args):
        rect.pos = instance.pos
        rect.size = instance.size

    widget.bind(pos=update_rect, size=update_rect)


def action_button(text: str, color, *, width: int | None = None) -> Button:
    kwargs = {}
    if width is not None:
        kwargs = {"size_hint_x": None, "width": width}
    return Button(
        text=text,
        font_size=22,
        bold=True,
        color=TEXT,
        background_normal="",
        background_color=color,
        **kwargs,
    )


def jog_button(text: str) -> Button:
    return action_button(text, BLUE)


def toggle_button(text: str, *, group: str) -> ToggleButton:
    return ToggleButton(
        text=text,
        group=group,
        font_size=20,
        bold=True,
        color=TEXT,
        background_normal="",
        background_down="",
        background_color=BUTTON,
    )


def numeric_input(text: str, *, width: int | None = None) -> TextInput:
    kwargs = {}
    if width is not None:
        kwargs = {"size_hint_x": None, "width": width}
    return TextInput(
        text=text,
        multiline=False,
        input_filter="float",
        font_size=28,
        halign="right",
        foreground_color=TEXT,
        background_color=(0.06, 0.06, 0.06, 1),
        cursor_color=TEXT,
        padding=(8, 10, 8, 8),
        **kwargs,
    )


def axis_label(text: str, *, width: int) -> Label:
    label = Label(text=text, color=TEXT, font_size=42, bold=True, size_hint_x=None, width=width)
    paint(label, BLUE if text in {"X", "Z"} else PANEL_ALT)
    return label


def section_label(text: str) -> Label:
    return Label(text=text, color=TEXT, font_size=24, bold=True, size_hint_y=None, height=38)


def status_color(state: MachineState):
    if state.error:
        return RED
    if not state.connected:
        return MUTED
    if state.busy:
        return AMBER
    return GREEN


def backend_label(backend_name: str) -> str:
    return "FRED USB" if backend_name == "fred" else "Simulator"


def parse_number(value: str, default: float) -> float:
    try:
        return float(value)
    except ValueError:
        return default
