from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.widget import Widget

from tcl_lathe_hmi.backends import create_backend
from tcl_lathe_hmi.config import JOG_INCREMENTS_MM, MachineConfig
from tcl_lathe_hmi.gcode import (
    CanonicalAction,
    GCodeParseError,
    PreviewPath,
    build_preview,
    parse_gcode,
)
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
        self.current_view = "manual"
        self.manual_work: BoxLayout | None = None
        self.program_panel: ProgramPanel | None = None
        self.work_container: BoxLayout | None = None

        paint(self, BG)
        self._build(initial_backend)

    def _build(self, initial_backend: str) -> None:
        self.add_widget(self._build_status_bar(initial_backend))

        body = BoxLayout(orientation="horizontal", spacing=10)
        body.add_widget(self._build_persistent_machine_panel())

        self.work_container = BoxLayout(orientation="vertical", size_hint_x=0.62)
        self.manual_work = self._build_manual_work()
        self.program_panel = ProgramPanel(service=self.service, config=self.config)
        self.work_container.add_widget(self.manual_work)
        body.add_widget(self.work_container)
        self.add_widget(body)

        self.add_widget(self._build_nav_bar())

    def _build_persistent_machine_panel(self) -> BoxLayout:
        panel = BoxLayout(orientation="vertical", spacing=10, size_hint_x=0.38)
        paint(panel, PANEL)

        readouts = self._build_readouts()
        readouts.size_hint_x = 1
        readouts.size_hint_y = 0.66
        panel.add_widget(readouts)

        spindle = self._build_spindle_controls()
        spindle.size_hint_y = 0.34
        panel.add_widget(spindle)
        return panel

    def _build_manual_work(self) -> BoxLayout:
        panel = BoxLayout(orientation="vertical", spacing=10)
        paint(panel, PANEL)
        panel.add_widget(self._build_jog_settings())
        panel.add_widget(self._build_jog_buttons())
        return panel

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
        manual = action_button("Manual", BLUE)
        manual.bind(on_release=lambda *_: self._show_view("manual"))
        nav.add_widget(manual)

        program = action_button("MDI / Program", BUTTON)
        program.bind(on_release=lambda *_: self._show_view("program"))
        nav.add_widget(program)

        for label in ("Tools", "Setup"):
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

        if self.program_panel is not None:
            self.program_panel.refresh(state)

    def _show_view(self, view_name: str) -> None:
        if self.work_container is None or self.manual_work is None or self.program_panel is None:
            return
        self.work_container.clear_widgets()
        if view_name == "program":
            self.work_container.add_widget(self.program_panel)
            self.current_view = "program"
        else:
            self.work_container.add_widget(self.manual_work)
            self.current_view = "manual"
        self.refresh(self.service.state)

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


class ProgramPanel(BoxLayout):
    def __init__(self, *, service: MachineService, config: MachineConfig, **kwargs):
        super().__init__(orientation="horizontal", spacing=10, **kwargs)
        self.service = service
        self.config = config
        self.actions: list[CanonicalAction] = []
        self.running = False
        self.execution_index = 0
        self.waiting_for_idle = False
        self.history: list[str] = []

        paint(self, PANEL)
        self._build()

    def _build(self) -> None:
        editor_side = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.52)
        paint(editor_side, PANEL_ALT)
        editor_side.add_widget(section_label("MDI / Program"))

        mdi_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=54)
        self.mdi_input = TextInput(
            text="G91 G1 X0.1 F100",
            multiline=False,
            font_size=22,
            foreground_color=TEXT,
            background_color=(0.06, 0.06, 0.06, 1),
            cursor_color=TEXT,
            padding=(8, 10, 8, 8),
        )
        run_mdi = action_button("Run MDI", GREEN, width=130)
        run_mdi.bind(on_release=lambda *_: self._run_mdi())
        mdi_row.add_widget(self.mdi_input)
        mdi_row.add_widget(run_mdi)
        editor_side.add_widget(mdi_row)

        self.history_label = Label(
            text="MDI history: --",
            color=MUTED,
            font_size=16,
            size_hint_y=None,
            height=28,
            halign="left",
        )
        self.history_label.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
        editor_side.add_widget(self.history_label)

        self.editor = TextInput(
            text=(
                "G21 G90 G18\n"
                "S1200 M3\n"
                "G0 X1.0 Z0.0\n"
                "G1 X1.5 Z-5.0 F100\n"
                "M5\n"
            ),
            multiline=True,
            font_size=18,
            foreground_color=TEXT,
            background_color=(0.05, 0.05, 0.05, 1),
            cursor_color=TEXT,
        )
        editor_side.add_widget(self.editor)

        file_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=54)
        self.path_input = TextInput(
            text="program.ngc",
            multiline=False,
            font_size=20,
            foreground_color=TEXT,
            background_color=(0.06, 0.06, 0.06, 1),
            cursor_color=TEXT,
            padding=(8, 10, 8, 8),
        )
        load = action_button("Load", BUTTON, width=90)
        save = action_button("Save", BUTTON, width=90)
        load.bind(on_release=lambda *_: self._load_program())
        save.bind(on_release=lambda *_: self._save_program())
        file_row.add_widget(self.path_input)
        file_row.add_widget(load)
        file_row.add_widget(save)
        editor_side.add_widget(file_row)
        self.add_widget(editor_side)

        preview_side = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.48)
        paint(preview_side, PANEL_ALT)

        top_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=54)
        parse_btn = action_button("Parse / Preview", BLUE)
        run_btn = action_button("Run Program", GREEN)
        stop_btn = action_button("Stop", RED, width=100)
        parse_btn.bind(on_release=lambda *_: self._parse_and_preview())
        run_btn.bind(on_release=lambda *_: self._run_program())
        stop_btn.bind(on_release=lambda *_: self._stop_program("Program stopped"))
        top_row.add_widget(parse_btn)
        top_row.add_widget(run_btn)
        top_row.add_widget(stop_btn)
        preview_side.add_widget(top_row)

        self.preview = PreviewCanvas()
        preview_side.add_widget(self.preview)

        self.program_status = Label(
            text="No program parsed",
            color=MUTED,
            font_size=18,
            size_hint_y=None,
            height=84,
            halign="left",
            valign="middle",
        )
        self.program_status.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
        preview_side.add_widget(self.program_status)
        self.add_widget(preview_side)

    def refresh(self, state: MachineState) -> None:
        if self.running:
            self._advance_execution(state)

    def _parse_and_preview(self) -> bool:
        try:
            result = parse_gcode(
                self.editor.text,
                start_x_mm=self.service.state.x_mm,
                start_z_mm=self.service.state.z_mm,
            )
        except GCodeParseError as exc:
            self.actions = []
            self.preview.set_preview(None)
            self.program_status.text = f"Parse error: {exc}"
            self.program_status.color = RED
            return False

        self.actions = result.actions
        preview_path = build_preview(
            self.actions,
            start_x_mm=self.service.state.x_mm,
            start_z_mm=self.service.state.z_mm,
        )
        self.preview.set_preview(preview_path)
        move_count = len(preview_path.segments)
        self.program_status.text = (
            f"Parsed {len(self.actions)} action(s), {move_count} move(s). "
            f"End X {result.final_x_mm:+0.3f} Z {result.final_z_mm:+0.3f}"
        )
        self.program_status.color = TEXT
        return True

    def _run_mdi(self) -> None:
        line = self.mdi_input.text.strip()
        if not line:
            self.program_status.text = "MDI is empty"
            self.program_status.color = AMBER
            return
        self.history.insert(0, line)
        self.history = self.history[:4]
        self.history_label.text = "MDI history: " + " | ".join(self.history)
        self._start_actions_from_text(line, label="MDI")

    def _run_program(self) -> None:
        self._start_actions_from_text(self.editor.text, label="Program")

    def _start_actions_from_text(self, text: str, *, label: str) -> None:
        if self.running:
            self.program_status.text = "Program is already running"
            self.program_status.color = AMBER
            return
        try:
            result = parse_gcode(
                text,
                start_x_mm=self.service.state.x_mm,
                start_z_mm=self.service.state.z_mm,
            )
        except GCodeParseError as exc:
            self.actions = []
            self.preview.set_preview(None)
            self.program_status.text = f"Parse error: {exc}"
            self.program_status.color = RED
            return
        if not result.actions:
            self.program_status.text = f"{label}: no executable actions"
            self.program_status.color = AMBER
            return
        self.actions = result.actions
        self.preview.set_preview(
            build_preview(
                self.actions,
                start_x_mm=self.service.state.x_mm,
                start_z_mm=self.service.state.z_mm,
            )
        )
        self.running = True
        self.execution_index = 0
        self.waiting_for_idle = False
        self.program_status.text = f"{label}: running {len(self.actions)} action(s)"
        self.program_status.color = GREEN
        self._advance_execution(self.service.state)

    def _advance_execution(self, state: MachineState) -> None:
        if state.error or not state.connected:
            self._stop_program("Program stopped: machine unavailable")
            return
        if state.busy:
            self.waiting_for_idle = True
            return
        if self.waiting_for_idle:
            self.execution_index += 1
            self.waiting_for_idle = False

        while self.running and self.execution_index < len(self.actions):
            action = self.actions[self.execution_index]
            ok = self.service.execute_action(
                action,
                default_feed=self.config.jog_feed,
                default_slew=self.config.jog_slew,
            )
            self.program_status.text = (
                f"Line {getattr(action, 'line_number', '?')}: "
                f"{self.execution_index + 1}/{len(self.actions)}"
            )
            if not ok:
                self._stop_program(self.service.state.status_message)
                return
            if self.service.state.busy:
                self.waiting_for_idle = True
                return
            self.execution_index += 1

        if self.running and self.execution_index >= len(self.actions):
            self.running = False
            self.program_status.text = "Program complete"
            self.program_status.color = GREEN

    def _stop_program(self, message: str) -> None:
        self.running = False
        self.waiting_for_idle = False
        self.program_status.text = message
        self.program_status.color = AMBER if "stopped" in message.lower() else RED

    def _load_program(self) -> None:
        path = Path(self.path_input.text).expanduser()
        try:
            self.editor.text = path.read_text()
        except OSError as exc:
            self.program_status.text = f"Load failed: {exc}"
            self.program_status.color = RED
            return
        self.program_status.text = f"Loaded {path}"
        self.program_status.color = TEXT
        self._parse_and_preview()

    def _save_program(self) -> None:
        path = Path(self.path_input.text).expanduser()
        try:
            path.write_text(self.editor.text)
        except OSError as exc:
            self.program_status.text = f"Save failed: {exc}"
            self.program_status.color = RED
            return
        self.program_status.text = f"Saved {path}"
        self.program_status.color = TEXT


class PreviewCanvas(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.preview_path: PreviewPath | None = None
        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())

    def set_preview(self, preview_path: PreviewPath | None) -> None:
        self.preview_path = preview_path
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.clear()
        with self.canvas:
            Color(0.05, 0.05, 0.05, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0.24, 0.25, 0.27, 1)
            Line(rectangle=(self.x + 8, self.y + 8, max(0, self.width - 16), max(0, self.height - 16)), width=1)

            if self.preview_path is None or not self.preview_path.segments:
                return

            bounds = self.preview_path
            min_z, max_z = bounds.min_z_mm, bounds.max_z_mm
            min_x, max_x = bounds.min_x_mm, bounds.max_x_mm
            if min_z == max_z:
                min_z -= 1.0
                max_z += 1.0
            if min_x == max_x:
                min_x -= 1.0
                max_x += 1.0

            pad = 24
            draw_w = max(1.0, self.width - 2 * pad)
            draw_h = max(1.0, self.height - 2 * pad)

            def map_point(x_mm: float, z_mm: float) -> tuple[float, float]:
                sx = self.x + pad + ((z_mm - min_z) / (max_z - min_z)) * draw_w
                sy = self.y + pad + ((x_mm - min_x) / (max_x - min_x)) * draw_h
                return sx, sy

            Color(0.23, 0.24, 0.25, 1)
            zero_z_x, _ = map_point(0.0, 0.0)
            _, zero_x_y = map_point(0.0, 0.0)
            if self.x + pad <= zero_z_x <= self.x + self.width - pad:
                Line(points=[zero_z_x, self.y + pad, zero_z_x, self.y + self.height - pad], width=1)
            if self.y + pad <= zero_x_y <= self.y + self.height - pad:
                Line(points=[self.x + pad, zero_x_y, self.x + self.width - pad, zero_x_y], width=1)

            for segment in self.preview_path.segments:
                Color(*(AMBER if segment.mode == "rapid" else GREEN))
                x0, y0 = map_point(segment.start_x_mm, segment.start_z_mm)
                x1, y1 = map_point(segment.end_x_mm, segment.end_z_mm)
                Line(points=[x0, y0, x1, y1], width=2.0 if segment.mode == "feed" else 1.2)


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
