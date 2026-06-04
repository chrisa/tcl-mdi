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
    MoveAction,
    PreviewPath,
    build_preview,
    parse_gcode,
)
from tcl_lathe_hmi.machine import MachineService, MachineState
from tcl_lathe_hmi.tools import ToolRecord, ToolTable
from tcl_lathe_hmi.ui.keypad import NumberEntryButton


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
        self.service = MachineService(
            create_backend(backend_name, self.machine_config),
            config=self.machine_config,
            settings_path=Path.home() / ".config" / "tcl-lathe-hmi" / "machine_state.json",
        )
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
        self.command_widgets: list[Button | ToggleButton | TextInput | NumberEntryButton] = []
        self.jog_increment_buttons: list[ToggleButton] = []
        self.jog_mode_buttons: list[ToggleButton] = []
        self.current_view = "manual"
        self.manual_work: BoxLayout | None = None
        self.program_panel: ProgramPanel | None = None
        self.tools_panel: ToolsPanel | None = None
        self.setup_panel: SetupPanel | None = None
        self.work_container: BoxLayout | None = None
        self._status_flash_event = None
        self._status_flash_phase = 0
        self._status_flash_active = False

        paint(self, BG)
        self._build(initial_backend)

    def _build(self, initial_backend: str) -> None:
        self.add_widget(self._build_status_bar(initial_backend))

        body = BoxLayout(orientation="horizontal", spacing=10)
        body.add_widget(self._build_persistent_machine_panel())

        self.work_container = BoxLayout(orientation="vertical", size_hint_x=0.62)
        self.manual_work = self._build_manual_work()
        self.program_panel = ProgramPanel(service=self.service, config=self.config)
        self.tools_panel = ToolsPanel(service=self.service)
        self.setup_panel = SetupPanel(service=self.service)
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
            btn.bind(state=lambda button, _state: self._style_toggle(button))
            self._style_toggle(btn)
            self.jog_increment_buttons.append(btn)
            self.command_widgets.append(btn)
            increments.add_widget(btn)
        box.add_widget(increments)

        row = BoxLayout(orientation="horizontal", spacing=8)
        feed = toggle_button("Feed", group="jog_mode")
        feed.state = "down"
        feed.bind(on_release=lambda button: self._set_jog_mode(button, "feed"))
        feed.bind(state=lambda button, _state: self._style_toggle(button))
        rapid = toggle_button("Rapid", group="jog_mode")
        rapid.bind(on_release=lambda button: self._set_jog_mode(button, "rapid"))
        rapid.bind(state=lambda button, _state: self._style_toggle(button))
        self.jog_mode_buttons.extend([feed, rapid])
        self._style_toggle(feed)
        self._style_toggle(rapid)
        self.command_widgets.extend([feed, rapid])
        row.add_widget(feed)
        row.add_widget(rapid)

        self.feed_input = numeric_input(
            str(self.config.jog_feed),
            width=120,
            integer=True,
            title_text="Feed Rate",
        )
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
        self.rpm_input = numeric_input(
            str(int(self.config.default_spindle_rpm)),
            integer=True,
            title_text="Spindle RPM",
        )
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

        tools = action_button("Tools", BUTTON)
        tools.bind(on_release=lambda *_: self._show_view("tools"))
        nav.add_widget(tools)

        setup = action_button("Setup", BUTTON)
        setup.bind(on_release=lambda *_: self._show_view("setup"))
        nav.add_widget(setup)
        return nav

    def refresh(self, state: MachineState | None = None) -> None:
        state = state or self.service.state
        self.status_label.text = state.controller_label
        self.message_label.text = state.status_message
        self.connect_button.text = "Clear Error" if state.error else ("Disconnect" if state.connected else "Connect")
        self.connect_button.background_color = RED if state.error else (AMBER if state.connected else BLUE)

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
        self.rpm_detail.text = f"{state.spindle.direction_label}\nS {state.spindle.target_rpm:0.0f}\n{speed_label}"
        self.rpm_detail.color = GREEN if state.spindle.at_speed else AMBER

        station = "--" if state.turret_station is None else str(state.turret_station)
        pending = "" if state.pending_tool is None else f" -> T{state.pending_tool}"
        self.tool_label.text = f"T{state.active_tool} P{station}{pending}"
        self.home_label.text = f"HOME {'X' if state.homed_x else '-'}{'Z' if state.homed_z else '-'}"
        self.home_label.color = GREEN if state.homed_x and state.homed_z else MUTED

        if not self._status_flash_active:
            self.status_label.color = status_color(state)

        if self.program_panel is not None:
            self.program_panel.refresh(state)
        if self.tools_panel is not None:
            self.tools_panel.refresh(state)
        if self.setup_panel is not None:
            self.setup_panel.refresh(state)

    def _show_view(self, view_name: str) -> None:
        if (
            self.work_container is None
            or self.manual_work is None
            or self.program_panel is None
            or self.tools_panel is None
            or self.setup_panel is None
        ):
            return
        self.work_container.clear_widgets()
        if view_name == "program":
            self.work_container.add_widget(self.program_panel)
            self.current_view = "program"
        elif view_name == "tools":
            self.work_container.add_widget(self.tools_panel)
            self.current_view = "tools"
        elif view_name == "setup":
            self.work_container.add_widget(self.setup_panel)
            self.current_view = "setup"
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

    def _style_toggle(self, button: ToggleButton) -> None:
        if button.state == "down":
            button.background_color = GREEN
            button.color = TEXT
        else:
            button.background_color = BUTTON
            button.color = TEXT

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
            self._set_status(self.service.state.status_message, flash=True)
        self.refresh(self.service.state)

    def _spindle(self, *, on: bool, forward: bool) -> None:
        rpm = parse_number(self.rpm_input.text, self.config.default_spindle_rpm)
        ok = self.service.set_spindle(on=on, rpm=rpm, forward=forward)
        if not ok:
            self._set_status(self.service.state.status_message, flash=True)
        self.refresh(self.service.state)

    def _set_status(self, message: str, *, flash: bool = False) -> None:
        self.service.state = replace(self.service.state, status_message=message)
        if flash:
            self._flash_status_indicator()
        self.refresh(self.service.state)

    def _flash_status_indicator(self) -> None:
        if self._status_flash_event is not None:
            self._status_flash_event.cancel()
        self._status_flash_phase = 0
        self._status_flash_active = True
        self._status_flash_event = Clock.schedule_interval(self._status_flash_tick, 0.12)
        self._status_flash_tick(0)

    def _status_flash_tick(self, _dt):
        if self._status_flash_phase >= 6:
            self._status_flash_active = False
            self._status_flash_event = None
            self.status_label.color = status_color(self.service.state)
            return False
        self.status_label.color = RED if self._status_flash_phase % 2 == 0 else status_color(self.service.state)
        self._status_flash_phase += 1
        return True


class ProgramPanel(BoxLayout):
    def __init__(self, *, service: MachineService, config: MachineConfig, **kwargs):
        super().__init__(orientation="horizontal", spacing=10, **kwargs)
        self.service = service
        self.config = config
        self.actions: list[CanonicalAction] = []
        self.running = False
        self.execution_index = 0
        self.waiting_for_idle = False
        self.waiting_for_tool = False
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
                start_x_mm=self.service.state.work_x_mm,
                start_z_mm=self.service.state.work_z_mm,
            )
        except GCodeParseError as exc:
            self.actions = []
            self.preview.set_preview(None)
            self.program_status.text = f"Parse error: {exc}"
            self.program_status.color = RED
            return False

        self.actions = result.actions
        limit_error = self._preview_limit_error(self.actions)
        if limit_error is not None:
            self.preview.set_preview(None)
            self.program_status.text = limit_error
            self.program_status.color = RED
            return False
        preview_path = build_preview(
            self.actions,
            start_x_mm=self.service.state.work_x_mm,
            start_z_mm=self.service.state.work_z_mm,
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
                start_x_mm=self.service.state.work_x_mm,
                start_z_mm=self.service.state.work_z_mm,
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
        limit_error = self._preview_limit_error(result.actions)
        if limit_error is not None:
            self.actions = []
            self.preview.set_preview(None)
            self.program_status.text = limit_error
            self.program_status.color = RED
            return
        self.actions = result.actions
        self.preview.set_preview(
            build_preview(
                self.actions,
                start_x_mm=self.service.state.work_x_mm,
                start_z_mm=self.service.state.work_z_mm,
            )
        )
        self.running = True
        self.execution_index = 0
        self.waiting_for_idle = False
        self.waiting_for_tool = False
        self.program_status.text = f"{label}: running {len(self.actions)} action(s)"
        self.program_status.color = GREEN
        self._advance_execution(self.service.state)

    def _advance_execution(self, state: MachineState) -> None:
        if state.error or not state.connected:
            self._stop_program("Program stopped: machine unavailable")
            return
        if self.waiting_for_tool:
            if state.pending_tool is not None:
                self.program_status.text = (
                    f"Waiting for tool confirmation: T{state.pending_tool}"
                    + (
                        ""
                        if state.pending_turret_station is None
                        else f" station {state.pending_turret_station}"
                    )
                )
                self.program_status.color = AMBER
                return
            self.execution_index += 1
            self.waiting_for_tool = False
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
                if self.service.state.pending_tool is not None:
                    self.waiting_for_tool = True
                    self.program_status.text = self.service.state.status_message
                    self.program_status.color = AMBER
                    return
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
        self.waiting_for_tool = False
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

    def _preview_limit_error(self, actions: list[CanonicalAction]) -> str | None:
        for action in actions:
            if isinstance(action, MoveAction):
                error = self.service.limits_error_for_work_target(
                    action.target_x_mm,
                    action.target_z_mm,
                    f"Preview line {action.line_number}",
                )
                if error is not None:
                    return error
        return None


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
        load.bind(on_release=lambda *_: self._load_table())
        save.bind(on_release=lambda *_: self._save_table())
        import_btn.bind(on_release=lambda *_: self._import_from_editor())
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
        self.pending_label = status_text("No pending tool change")
        edit_side.add_widget(self.active_label)
        edit_side.add_widget(self.pending_label)

        grid = GridLayout(cols=2, spacing=8, size_hint_y=None, height=270)
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

        row1 = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        upsert = action_button("Upsert Tool", BLUE)
        set_active = action_button("Set Active", GREEN)
        upsert.bind(on_release=lambda *_: self._upsert_tool())
        set_active.bind(on_release=lambda *_: self._set_active_tool())
        row1.add_widget(upsert)
        row1.add_widget(set_active)
        edit_side.add_widget(row1)

        row2 = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        confirm = action_button("Confirm Pending", GREEN)
        auto = action_button("Auto Turret N/A", BUTTON)
        auto.disabled = True
        confirm.bind(on_release=lambda *_: self._confirm_pending_tool())
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
            self.service.tool_table.upsert(tool)
        except ValueError as exc:
            self._set_status(f"Tool edit failed: {exc}", RED)
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

    def _set_status(self, message: str, color) -> None:
        self.tool_status.text = message
        self.tool_status.color = color


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
        work.bind(on_release=lambda *_: self._set_display_mode("work"))
        machine.bind(on_release=lambda *_: self._set_display_mode("machine"))
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
        set_x.bind(on_release=lambda *_: self._set_current_axis("X"))
        set_z.bind(on_release=lambda *_: self._set_current_axis("Z"))
        set_row.add_widget(self.set_x_input)
        set_row.add_widget(set_x)
        set_row.add_widget(self.set_z_input)
        set_row.add_widget(set_z)
        box.add_widget(set_row)

        zero_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        zero_x = action_button("Zero X", GREEN)
        zero_z = action_button("Zero Z", GREEN)
        clear = action_button("Clear Offsets", AMBER)
        zero_x.bind(on_release=lambda *_: self._zero_axis("X"))
        zero_z.bind(on_release=lambda *_: self._zero_axis("Z"))
        clear.bind(on_release=lambda *_: self._clear_offsets())
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
        clear_error.bind(on_release=lambda *_: self._clear_error())
        reconnect.bind(on_release=lambda *_: self._reconnect())
        row1.add_widget(clear_error)
        row1.add_widget(reconnect)
        box.add_widget(row1)

        row2 = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        connect = action_button("Connect", GREEN)
        disconnect = action_button("Disconnect", RED)
        connect.bind(on_release=lambda *_: self._connect())
        disconnect.bind(on_release=lambda *_: self._disconnect())
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
        self.limit_toggle.bind(on_release=lambda *_: self._toggle_limits())
        apply_limits.bind(on_release=lambda *_: self._apply_limits())
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
        home_x.bind(on_release=lambda *_: self._home_axis("X"))
        home_z.bind(on_release=lambda *_: self._home_axis("Z"))
        home_all.bind(on_release=lambda *_: self._home_axis("X/Z"))
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


def numeric_input(
    text: str,
    *,
    width: int | None = None,
    integer: bool = False,
    title_text: str = "Number",
) -> NumberEntryButton:
    kwargs = {}
    if width is not None:
        kwargs = {"size_hint_x": None, "width": width}
    return NumberEntryButton(
        text=text,
        integer=integer,
        title_text=title_text,
        font_size=28,
        **kwargs,
    )


def field_input(
    text: str,
    *,
    integer: bool = False,
    title_text: str = "Number",
) -> NumberEntryButton:
    return NumberEntryButton(
        text=text,
        integer=integer,
        title_text=title_text,
        font_size=20,
    )


def text_field(text: str) -> TextInput:
    return TextInput(
        text=text,
        multiline=False,
        font_size=20,
        foreground_color=TEXT,
        background_color=(0.06, 0.06, 0.06, 1),
        cursor_color=TEXT,
        padding=(8, 8, 8, 8),
    )


def status_text(text: str) -> Label:
    label = Label(
        text=text,
        color=MUTED,
        font_size=18,
        halign="left",
        valign="middle",
        size_hint_y=None,
        height=42,
    )
    label.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
    return label


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


def optional_int(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    return int(float(text))
