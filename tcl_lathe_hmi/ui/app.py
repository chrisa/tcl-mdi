from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
from typing import Callable

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Line, Mesh, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.codeinput import CodeInput
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.widget import Widget

from tcl_lathe_hmi.backends import create_backend
from tcl_lathe_hmi.cam import (
    CamGenerationError,
    CamSolidError,
    CamValidationError,
    HoleSpec,
    LatheCamJob,
    StockSpec,
    TaperSpec,
    ThreadSpec,
    TurningSpec,
    build_part_mesh,
    build_part_outline,
    generate_cam_program,
)
from tcl_lathe_hmi.config import JOG_INCREMENTS_MM, MachineConfig
from tcl_lathe_hmi.gcode import (
    CanonicalAction,
    GCodeParseError,
    LinuxCncGCodeLexer,
    MoveAction,
    PreviewSegment,
    PreviewPath,
    TclGCodeStyle,
    ToolChangeAction,
    build_preview,
    parse_gcode,
)
from tcl_lathe_hmi.machine import MachineService, MachineState
from tcl_lathe_hmi.tools import ToolRecord, ToolTable
from tcl_lathe_hmi.ui.keypad import NumberEntryButton
from tcl_lathe_hmi.ui.widgets import bind_release, configure_touch_release


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
THREAD = (0.20, 0.70, 0.86, 1)
THREAD_TOOLPATH = (0.95, 0.42, 0.30, 0.72)


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
        if self.panel is not None:
            self.panel.cancel_scheduled_events()
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
        self.custom_increment_mm = self.increment_mm
        self.use_custom_increment = False
        self.jog_mode = "feed"
        self.queued_jog_x_mm = 0.0
        self.queued_jog_z_mm = 0.0
        self.queued_jog_tap_count = 0
        self.queued_jog_event = None
        self.jog_queue_container: BoxLayout | None = None
        self.jog_queue_bar: JogQueueBar | None = None
        self.jog_queue_length_label: Label | None = None
        self.command_widgets: list[Button | ToggleButton | TextInput | NumberEntryButton] = []
        self.jog_increment_buttons: list[ToggleButton] = []
        self.jog_mode_buttons: list[ToggleButton] = []
        self.current_view = "manual"
        self.manual_work: BoxLayout | None = None
        self.program_panel: ProgramPanel | None = None
        self.cam_panel: CamPanel | None = None
        self.tools_panel: ToolsPanel | None = None
        self.setup_panel: SetupPanel | None = None
        self.body: BoxLayout | None = None
        self.machine_panel: BoxLayout | None = None
        self.work_container: BoxLayout | None = None
        self.nav_buttons: dict[str, Button] = {}
        self._status_flash_event = None
        self._status_flash_phase = 0
        self._status_flash_active = False

        paint(self, BG)
        self._build(initial_backend)

    def cancel_scheduled_events(self) -> None:
        if self.queued_jog_event is not None:
            self.queued_jog_event.cancel()
            self.queued_jog_event = None
        self._clear_queued_jog()
        if self._status_flash_event is not None:
            self._status_flash_event.cancel()
            self._status_flash_event = None
        self._status_flash_active = False

    def _build(self, initial_backend: str) -> None:
        self.add_widget(self._build_status_bar(initial_backend))

        self.body = BoxLayout(orientation="horizontal", spacing=10)
        self.machine_panel = self._build_persistent_machine_panel()
        self.body.add_widget(self.machine_panel)

        self.work_container = BoxLayout(orientation="vertical", size_hint_x=0.62)
        self.manual_work = self._build_manual_work()
        self.program_panel = ProgramPanel(service=self.service, config=self.config)
        self.cam_panel = CamPanel(
            service=self.service,
            on_program_ready=self._load_cam_program,
        )
        self.tools_panel = ToolsPanel(service=self.service)
        self.setup_panel = SetupPanel(service=self.service)
        self.work_container.add_widget(self.manual_work)
        self.body.add_widget(self.work_container)
        self.add_widget(self.body)

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
        panel.add_widget(self._build_toolchanger_controls())
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
        bind_release(self.connect_button, lambda *_: self._toggle_connection())
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

        self.jog_queue_container = BoxLayout(
            orientation="horizontal",
            spacing=8,
            size_hint_x=None,
            width=430,
            opacity=0.0,
        )
        self.jog_queue_container.add_widget(
            Label(
                text="JOG",
                color=MUTED,
                font_size=18,
                size_hint_x=None,
                width=48,
            )
        )
        self.jog_queue_bar = JogQueueBar(size_hint_x=None, width=270, size_hint_y=None, height=20)
        self.jog_queue_container.add_widget(self.jog_queue_bar)
        self.jog_queue_length_label = Label(
            text="",
            color=TEXT,
            font_size=18,
            halign="left",
            valign="middle",
            size_hint_x=None,
            width=96,
        )
        self.jog_queue_length_label.bind(
            size=lambda widget, *_: setattr(widget, "text_size", widget.size)
        )
        self.jog_queue_container.add_widget(self.jog_queue_length_label)
        bar.add_widget(self.jog_queue_container)

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
        box = BoxLayout(orientation="vertical", spacing=8, size_hint_y=0.24)
        paint(box, PANEL_ALT)

        box.add_widget(section_label("Jog"))

        increments = BoxLayout(orientation="horizontal", spacing=6)
        for index, inc in enumerate(JOG_INCREMENTS_MM):
            btn = toggle_button(f"{inc:0.3f}", group="jog_increment")
            if index == 1:
                btn.state = "down"
            bind_release(btn, lambda button, value=inc: self._set_increment(button, value))
            btn.bind(state=lambda button, _state: self._style_toggle(button))
            self._style_toggle(btn)
            self.jog_increment_buttons.append(btn)
            self.command_widgets.append(btn)
            increments.add_widget(btn)
        box.add_widget(increments)

        custom_row = BoxLayout(orientation="horizontal", spacing=8)
        self.custom_increment_button = toggle_button("Custom", group="jog_increment")
        bind_release(self.custom_increment_button, self._set_custom_increment)
        self.custom_increment_button.bind(state=lambda button, _state: self._style_toggle(button))
        self._style_toggle(self.custom_increment_button)
        self.jog_increment_buttons.append(self.custom_increment_button)
        self.command_widgets.append(self.custom_increment_button)
        custom_row.add_widget(self.custom_increment_button)

        self.custom_increment_input = numeric_input(
            f"{self.custom_increment_mm:0.3f}",
            width=150,
            title_text="Jog Distance",
            on_value=self._custom_increment_changed,
        )
        self.command_widgets.append(self.custom_increment_input)
        custom_row.add_widget(Label(text="mm", color=MUTED, font_size=24, size_hint_x=None, width=48))
        custom_row.add_widget(self.custom_increment_input)
        box.add_widget(custom_row)

        row = BoxLayout(orientation="horizontal", spacing=8)
        feed = toggle_button("Feed", group="jog_mode")
        feed.state = "down"
        bind_release(feed, lambda button: self._set_jog_mode(button, "feed"))
        feed.bind(state=lambda button, _state: self._style_toggle(button))
        rapid = toggle_button("Rapid", group="jog_mode")
        bind_release(rapid, lambda button: self._set_jog_mode(button, "rapid"))
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
        grid = GridLayout(cols=3, rows=3, spacing=8, size_hint_y=0.32)
        paint(grid, PANEL_ALT)

        x_plus = jog_button("X+")
        z_minus = jog_button("Z-")
        cancel = action_button("CANCEL", AMBER)
        z_plus = jog_button("Z+")
        x_minus = jog_button("X-")
        buttons: list[Button | None] = [
            None,
            x_plus,
            None,
            z_minus,
            cancel,
            z_plus,
            None,
            x_minus,
            None,
        ]

        for btn in buttons:
            if btn is None:
                spacer = Label(text="")
                grid.add_widget(spacer)
                continue
            self.command_widgets.append(btn)
            grid.add_widget(btn)

        bind_release(x_plus, lambda *_: self._jog(x_sign=1.0))
        bind_release(z_minus, lambda *_: self._jog(z_sign=-1.0))
        bind_release(cancel, lambda *_: self._cancel_queued_jog())
        bind_release(z_plus, lambda *_: self._jog(z_sign=1.0))
        bind_release(x_minus, lambda *_: self._jog(x_sign=-1.0))
        return grid

    def _build_toolchanger_controls(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", spacing=8, size_hint_y=0.36)
        paint(box, PANEL_ALT)
        box.add_widget(section_label("Toolchanger"))

        self.manual_tool_status = Label(
            text="Current P--",
            color=MUTED,
            font_size=18,
            halign="left",
            valign="middle",
            size_hint_y=None,
            height=30,
        )
        self.manual_tool_status.bind(
            size=lambda widget, *_: setattr(widget, "text_size", widget.size)
        )
        box.add_widget(self.manual_tool_status)

        fields = GridLayout(cols=6, spacing=6, size_hint_y=None, height=46)
        self.manual_current_station_input = field_input("1", integer=True, title_text="Current Station")
        self.manual_tool_input = field_input("1", integer=True, title_text="Tool Number")
        self.manual_target_station_input = field_input("1", integer=True, title_text="Target Station")
        for label, widget in (
            ("Now P", self.manual_current_station_input),
            ("Tool T", self.manual_tool_input),
            ("Go P", self.manual_target_station_input),
        ):
            fields.add_widget(Label(text=label, color=MUTED, font_size=18))
            fields.add_widget(widget)
        box.add_widget(fields)

        row = BoxLayout(orientation="horizontal", spacing=8)
        set_current = action_button("Set Current", BLUE)
        change = action_button("Change", GREEN)
        confirm = action_button("Confirm", AMBER)
        bind_release(set_current, lambda *_: self._manual_set_current_station())
        bind_release(change, lambda *_: self._manual_change_tool())
        bind_release(confirm, lambda *_: self._manual_confirm_pending_tool())
        row.add_widget(set_current)
        row.add_widget(change)
        row.add_widget(confirm)
        box.add_widget(row)

        teach_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        self.manual_teach_diameter_input = field_input("0.0", title_text="Measured Diameter")
        teach_z = action_button("Teach Z0", BLUE)
        teach_x = action_button("Teach X Dia", BLUE)
        bind_release(teach_z, lambda *_: self._manual_teach_z0())
        bind_release(teach_x, lambda *_: self._manual_teach_x_diameter())
        teach_row.add_widget(self.manual_teach_diameter_input)
        teach_row.add_widget(teach_z)
        teach_row.add_widget(teach_x)
        box.add_widget(teach_row)

        self.command_widgets.extend(
            [
                self.manual_current_station_input,
                self.manual_tool_input,
                self.manual_target_station_input,
                self.manual_teach_diameter_input,
                set_current,
                change,
                confirm,
                teach_z,
                teach_x,
            ]
        )
        return box

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
        bind_release(forward, lambda *_: self._spindle(on=True, forward=True))
        bind_release(reverse, lambda *_: self._spindle(on=True, forward=False))
        bind_release(stop, lambda *_: self._spindle(on=False, forward=True))
        self.command_widgets.extend([forward, reverse, stop])
        row.add_widget(forward)
        row.add_widget(reverse)
        row.add_widget(stop)
        box.add_widget(row)
        return box

    def _build_nav_bar(self) -> BoxLayout:
        nav = BoxLayout(orientation="horizontal", size_hint_y=None, height=64, spacing=8)
        paint(nav, PANEL)
        self.nav_buttons = {}
        for view_name, label in (
            ("manual", "Manual"),
            ("program", "MDI / Program"),
            ("cam", "CAM"),
            ("tools", "Tools"),
            ("setup", "Setup"),
        ):
            button = action_button(label, BUTTON)
            bind_release(button, lambda *_args, view=view_name: self._show_view(view))
            self.nav_buttons[view_name] = button
            nav.add_widget(button)
        self._style_nav_buttons()
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
        if hasattr(self, "manual_tool_status"):
            pending_text = (
                ""
                if state.pending_tool is None
                else (
                    f"  Pending T{state.pending_tool} P"
                    + (
                        "--"
                        if state.pending_turret_station is None
                        else str(state.pending_turret_station)
                    )
                )
            )
            self.manual_tool_status.text = (
                f"Active T{state.active_tool} P{station}  "
                f"Xoff {state.tool_x_offset_mm:+0.3f}  Zoff {state.tool_z_offset_mm:+0.3f}"
                f"{pending_text}"
            )
        self.home_label.text = f"HOME {'X' if state.homed_x else '-'}{'Z' if state.homed_z else '-'}"
        self.home_label.color = GREEN if state.homed_x and state.homed_z else MUTED

        if not self._status_flash_active:
            self.status_label.color = status_color(state)

        if self.program_panel is not None:
            self.program_panel.refresh(state)
        if self.cam_panel is not None:
            self.cam_panel.refresh(state)
        if self.tools_panel is not None:
            self.tools_panel.refresh(state)
        if self.setup_panel is not None:
            self.setup_panel.refresh(state)

    def _show_view(self, view_name: str) -> None:
        if (
            self.work_container is None
            or self.manual_work is None
            or self.program_panel is None
            or self.cam_panel is None
            or self.tools_panel is None
            or self.setup_panel is None
            or self.body is None
            or self.machine_panel is None
        ):
            return
        self.work_container.clear_widgets()
        if view_name == "program":
            self.work_container.add_widget(self.program_panel)
            self.current_view = "program"
        elif view_name == "cam":
            self.work_container.add_widget(self.cam_panel)
            self.current_view = "cam"
        elif view_name == "tools":
            self.work_container.add_widget(self.tools_panel)
            self.current_view = "tools"
        elif view_name == "setup":
            self.work_container.add_widget(self.setup_panel)
            self.current_view = "setup"
        else:
            self.work_container.add_widget(self.manual_work)
            self.current_view = "manual"
        self._set_machine_panel_visible(self.current_view in {"manual", "program", "tools"})
        self._style_nav_buttons()
        self.refresh(self.service.state)

    def _style_nav_buttons(self) -> None:
        for view_name, button in self.nav_buttons.items():
            button.background_color = BLUE if view_name == self.current_view else BUTTON
            button.color = TEXT

    def _set_machine_panel_visible(self, visible: bool) -> None:
        if self.body is None or self.machine_panel is None or self.work_container is None:
            return
        self.body.clear_widgets()
        if visible:
            self.machine_panel.size_hint_x = 0.38
            self.work_container.size_hint_x = 0.62
            self.body.add_widget(self.machine_panel)
            self.body.add_widget(self.work_container)
        else:
            self.work_container.size_hint_x = 1.0
            self.body.add_widget(self.work_container)

    def _load_cam_program(self, gcode: str, *, run: bool = False) -> None:
        if self.program_panel is None:
            return
        self.program_panel.load_generated_program(gcode, label="CAM")
        self._show_view("program")
        if run:
            self.program_panel.run_loaded_program(label="CAM")

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
            self._select_jog_increment_button(button)
            self.increment_mm = value
            self.use_custom_increment = False

    def _set_custom_increment(self, button: ToggleButton) -> None:
        if button.state == "down":
            self._select_jog_increment_button(button)
            self.use_custom_increment = True
            self.custom_increment_mm = self._current_custom_increment()

    def _custom_increment_changed(self, value: float | int) -> None:
        self.custom_increment_mm = abs(float(value))
        self._select_jog_increment_button(self.custom_increment_button)
        self.use_custom_increment = True

    def _select_jog_increment_button(self, selected: ToggleButton) -> None:
        for button in self.jog_increment_buttons:
            button.state = "down" if button is selected else "normal"

    def _current_custom_increment(self) -> float:
        value = abs(parse_number(self.custom_increment_input.text, self.custom_increment_mm))
        if value == 0.0:
            return self.custom_increment_mm
        return value

    def _current_jog_increment(self) -> float:
        if not self.use_custom_increment:
            return self.increment_mm
        self.custom_increment_mm = self._current_custom_increment()
        return self.custom_increment_mm

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
        increment_mm = self._current_jog_increment()
        self.queued_jog_x_mm += x_sign * increment_mm
        self.queued_jog_z_mm += z_sign * increment_mm
        self.queued_jog_tap_count += 1
        if self.queued_jog_event is not None:
            self.queued_jog_event.cancel()
        self.queued_jog_event = Clock.schedule_once(
            self._flush_queued_jog,
            self.config.jog_accumulate_delay_s,
        )
        self._update_queued_jog_indicator()
        self._set_status(
            f"Queued jog X {self.queued_jog_x_mm:+0.3f} Z {self.queued_jog_z_mm:+0.3f}"
        )

    def _flush_queued_jog(self, _dt) -> None:
        x_mm = self.queued_jog_x_mm
        z_mm = self.queued_jog_z_mm
        self.queued_jog_event = None
        self._clear_queued_jog()

        if abs(x_mm) < 1e-9 and abs(z_mm) < 1e-9:
            self._set_status("Queued jog cancelled")
            return

        feed = int(parse_number(self.feed_input.text, self.config.jog_feed))
        ok = self.service.jog_delta(
            x_mm=x_mm,
            z_mm=z_mm,
            mode=self.jog_mode,
            feed=feed,
            slew=self.config.jog_slew,
        )
        if not ok:
            self._set_status(self.service.state.status_message, flash=True)
        else:
            self._set_status(f"Jog sent X {x_mm:+0.3f} Z {z_mm:+0.3f}")
        self.refresh(self.service.state)

    def _cancel_queued_jog(self) -> None:
        if self.queued_jog_event is not None:
            self.queued_jog_event.cancel()
            self.queued_jog_event = None
            self._clear_queued_jog()
            self._set_status("Queued jog cancelled")
            return
        self._clear_queued_jog()
        self._set_status("No queued jog to cancel")

    def _spindle(self, *, on: bool, forward: bool) -> None:
        rpm = parse_number(self.rpm_input.text, self.config.default_spindle_rpm)
        ok = self.service.set_spindle(on=on, rpm=rpm, forward=forward)
        if not ok:
            self._set_status(self.service.state.status_message, flash=True)
        self.refresh(self.service.state)

    def _manual_set_current_station(self) -> None:
        try:
            station = optional_int(self.manual_current_station_input.text)
        except ValueError:
            self._set_status("Invalid current turret station", flash=True)
            return
        ok = self.service.set_turret_station(station)
        self._set_status(self.service.state.status_message, flash=not ok)

    def _manual_change_tool(self) -> None:
        try:
            tool_number = int(parse_number(self.manual_tool_input.text, -1))
            target_station = optional_int(self.manual_target_station_input.text)
        except ValueError:
            self._set_status("Invalid toolchanger input", flash=True)
            return
        if tool_number < 0:
            self._set_status("Tool number must be non-negative", flash=True)
            return
        ok = self.service.change_tool(
            tool_number,
            station=target_station,
            context="Manual toolchanger",
        )
        self._set_status(self.service.state.status_message, flash=not ok)
        self._sync_tools_panel_from_service()

    def _manual_confirm_pending_tool(self) -> None:
        state = self.service.state
        if state.pending_tool is None:
            self._set_status("No pending tool change", flash=True)
            return
        ok = self.service.confirm_tool_change(state.pending_tool, state.pending_turret_station)
        self._set_status(self.service.state.status_message, flash=not ok)
        self._sync_tools_panel_from_service()

    def _manual_teach_z0(self) -> None:
        ok = self.service.teach_tool_z(0.0)
        self._set_status(self.service.state.status_message, flash=not ok)
        self._sync_tools_panel_from_service()

    def _manual_teach_x_diameter(self) -> None:
        diameter = parse_number(self.manual_teach_diameter_input.text, -1.0)
        ok = self.service.teach_tool_x(diameter)
        self._set_status(self.service.state.status_message, flash=not ok)
        self._sync_tools_panel_from_service()

    def _sync_tools_panel_from_service(self) -> None:
        if self.tools_panel is None:
            return
        self.tools_panel._export_to_editor()
        self.tools_panel._load_tool_fields(self.service.state.active_tool)
        self.tools_panel.refresh(self.service.state)

    def _set_status(self, message: str, *, flash: bool = False) -> None:
        self.service.state = replace(self.service.state, status_message=message)
        if flash:
            self._flash_status_indicator()
        self.refresh(self.service.state)

    def _clear_queued_jog(self) -> None:
        self.queued_jog_x_mm = 0.0
        self.queued_jog_z_mm = 0.0
        self.queued_jog_tap_count = 0
        self._update_queued_jog_indicator()

    def _update_queued_jog_indicator(self) -> None:
        if (
            self.jog_queue_container is None
            or self.jog_queue_bar is None
            or self.jog_queue_length_label is None
        ):
            return

        visible = self.queued_jog_tap_count >= 2
        self.jog_queue_container.opacity = 1.0 if visible else 0.0
        if not visible:
            self.jog_queue_bar.set_progress(0.0)
            self.jog_queue_length_label.text = ""
            return

        self.jog_queue_bar.set_progress(min(1.0, (self.queued_jog_tap_count - 1) / 8.0))
        self.jog_queue_length_label.text = self._format_queued_jog_length()

    def _format_queued_jog_length(self) -> str:
        parts: list[str] = []
        if abs(self.queued_jog_x_mm) >= 1e-9:
            parts.append(f"X {self.queued_jog_x_mm:+0.3f}")
        if abs(self.queued_jog_z_mm) >= 1e-9:
            parts.append(f"Z {self.queued_jog_z_mm:+0.3f}")
        if not parts:
            return ""
        return "  ".join(parts) + " mm"

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
        self.highlight_editor_lines = False
        self.history: list[str] = []

        paint(self, PANEL)
        self._build()

    def _build(self) -> None:
        editor_side = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.52)
        paint(editor_side, PANEL_ALT)
        editor_side.add_widget(section_label("MDI / Program"))

        mdi_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=54)
        self.mdi_input = gcode_input(
            text="G91 G1 X0.1 F100",
            multiline=False,
            font_size=22,
            padding=(8, 10, 8, 8),
        )
        run_mdi = action_button("Run MDI", GREEN, width=130)
        bind_release(run_mdi, lambda *_: self._run_mdi())
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

        self.setup_status = Label(
            text="Setup T0 P--  Xoff +0.000  Zoff +0.000",
            color=MUTED,
            font_size=16,
            size_hint_y=None,
            height=30,
            halign="left",
            valign="middle",
        )
        self.setup_status.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
        editor_side.add_widget(self.setup_status)

        self.editor = gcode_input(
            text=(
                "G21 G90 G18\n"
                "S1200 M3\n"
                "G0 X1.0 Z0.0\n"
                "G1 X1.5 Z-5.0 F100\n"
                "M5\n"
            ),
            multiline=True,
            font_size=18,
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
        bind_release(load, lambda *_: self._load_program())
        bind_release(save, lambda *_: self._save_program())
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
        self.confirm_tool_button = action_button("Confirm Tool", AMBER, width=160)
        stop_btn = action_button("Stop", RED, width=100)
        bind_release(parse_btn, lambda *_: self._parse_and_preview())
        bind_release(run_btn, lambda *_: self._run_program())
        bind_release(self.confirm_tool_button, lambda *_: self._confirm_pending_tool())
        bind_release(stop_btn, lambda *_: self._stop_program("Program stopped"))
        top_row.add_widget(parse_btn)
        top_row.add_widget(run_btn)
        top_row.add_widget(self.confirm_tool_button)
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
        self._refresh_tool_confirm_button(state)
        self._refresh_setup_status(state)
        self.preview.set_tool_position(x_mm=state.work_x_mm, z_mm=state.work_z_mm)
        if self.running:
            self._advance_execution(state)

    def load_generated_program(self, text: str, *, label: str) -> None:
        self.editor.text = text
        self.path_input.text = f"{label.lower()}_generated.ngc"
        self.program_status.text = f"{label}: loaded generated program"
        self.program_status.color = TEXT
        self._clear_line_highlight()
        self._parse_and_preview()

    def run_loaded_program(self, *, label: str) -> None:
        self._start_actions_from_text(self.editor.text, label=label, highlight_editor=True)

    def _refresh_tool_confirm_button(self, state: MachineState) -> None:
        if not hasattr(self, "confirm_tool_button"):
            return
        if state.pending_tool is None:
            self.confirm_tool_button.text = "Confirm Tool"
            self.confirm_tool_button.disabled = True
            self.confirm_tool_button.background_color = BUTTON
            return
        station = (
            ""
            if state.pending_turret_station is None
            else f" P{state.pending_turret_station}"
        )
        self.confirm_tool_button.text = f"Confirm T{state.pending_tool}{station}"
        self.confirm_tool_button.disabled = False
        self.confirm_tool_button.background_color = AMBER

    def _refresh_setup_status(self, state: MachineState) -> None:
        if not hasattr(self, "setup_status"):
            return
        station = "--" if state.turret_station is None else str(state.turret_station)
        warning = (
            "  OFFSETS ZERO"
            if state.active_tool > 0
            and abs(state.tool_x_offset_mm) < 1e-9
            and abs(state.tool_z_offset_mm) < 1e-9
            else ""
        )
        self.setup_status.text = (
            f"Setup T{state.active_tool} P{station}  "
            f"Xoff {state.tool_x_offset_mm:+0.3f}  Zoff {state.tool_z_offset_mm:+0.3f}"
            f"{warning}"
        )
        self.setup_status.color = AMBER if warning else MUTED

    def _confirm_pending_tool(self) -> None:
        state = self.service.state
        if state.pending_tool is None:
            self.program_status.text = "No pending tool change"
            self.program_status.color = AMBER
            return
        if self.service.confirm_tool_change(state.pending_tool, state.pending_turret_station):
            self.program_status.text = self.service.state.status_message
            self.program_status.color = GREEN
        else:
            self.program_status.text = self.service.state.status_message
            self.program_status.color = RED
        self._refresh_tool_confirm_button(self.service.state)

    def _highlight_action_line(self, action: CanonicalAction) -> None:
        if not self.highlight_editor_lines:
            return
        line_number = getattr(action, "line_number", 0)
        if line_number <= 0:
            return
        self._highlight_editor_line(line_number)

    def _highlight_editor_line(self, line_number: int) -> None:
        lines = self.editor.text.splitlines(keepends=True)
        if not lines or line_number > len(lines):
            self._clear_line_highlight()
            return
        start = sum(len(line) for line in lines[: line_number - 1])
        line_text = lines[line_number - 1]
        end = start + len(line_text.rstrip("\r\n"))
        if end <= start:
            end = min(len(self.editor.text), start + len(line_text))
        try:
            self.editor.select_text(start, end)
            self.editor.cursor = (0, max(0, line_number - 1))
        except Exception:
            return

    def _clear_line_highlight(self) -> None:
        try:
            self.editor.cancel_selection()
        except Exception:
            return

    def _parse_and_preview(self) -> bool:
        self._clear_line_highlight()
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
        warning = self._tool_offset_warning(self.actions)
        if warning:
            self.program_status.text += f"\n{warning}"
            self.program_status.color = AMBER
        else:
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
        self._start_actions_from_text(line, label="MDI", highlight_editor=False)

    def _run_program(self) -> None:
        self._start_actions_from_text(self.editor.text, label="Program", highlight_editor=True)

    def _start_actions_from_text(
        self,
        text: str,
        *,
        label: str,
        highlight_editor: bool,
    ) -> None:
        if self.running:
            self.program_status.text = "Program is already running"
            self.program_status.color = AMBER
            return
        self._clear_line_highlight()
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
        self.highlight_editor_lines = highlight_editor
        warning = self._tool_offset_warning(self.actions)
        self.program_status.text = f"{label}: running {len(self.actions)} action(s)"
        if warning:
            self.program_status.text += f"\n{warning}"
            self.program_status.color = AMBER
        else:
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
            self._highlight_action_line(action)
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
                    self._refresh_tool_confirm_button(self.service.state)
                    return
                self._stop_program(self.service.state.status_message)
                return
            if self.service.state.busy:
                self.waiting_for_idle = True
                return
            self.execution_index += 1

        if self.running and self.execution_index >= len(self.actions):
            self.running = False
            self.highlight_editor_lines = False
            self._clear_line_highlight()
            self.program_status.text = "Program complete"
            self.program_status.color = GREEN

    def _stop_program(self, message: str) -> None:
        self.running = False
        self.waiting_for_idle = False
        self.waiting_for_tool = False
        self.highlight_editor_lines = False
        self._clear_line_highlight()
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
        preview = build_preview(
            actions,
            start_x_mm=self.service.state.work_x_mm,
            start_z_mm=self.service.state.work_z_mm,
        )
        for segment in preview.segments:
            error = self.service.limits_error_for_work_target(
                segment.end_x_mm,
                segment.end_z_mm,
                f"Preview line {segment.line_number}",
            )
            if error is not None:
                return error
        return None

    def _tool_offset_warning(self, actions: list[CanonicalAction]) -> str:
        zero_offset_tools: list[int] = []
        missing_tools: list[int] = []
        seen: set[int] = set()
        for action in actions:
            if not isinstance(action, ToolChangeAction) or action.tool_number is None:
                continue
            tool_number = action.tool_number
            if tool_number in seen:
                continue
            seen.add(tool_number)
            tool = self.service.tool_table.get(tool_number)
            if tool is None:
                missing_tools.append(tool_number)
            elif abs(tool.x_offset_mm) < 1e-9 and abs(tool.z_offset_mm) < 1e-9:
                zero_offset_tools.append(tool_number)
        parts: list[str] = []
        if missing_tools:
            parts.append("missing table row " + ", ".join(f"T{tool}" for tool in missing_tools))
        if zero_offset_tools:
            parts.append("zero offsets " + ", ".join(f"T{tool}" for tool in zero_offset_tools))
        return "Tool setup warning: " + "; ".join(parts) if parts else ""


class CamPanel(BoxLayout):
    def __init__(
        self,
        *,
        service: MachineService,
        on_program_ready: Callable[..., None],
        **kwargs,
    ):
        super().__init__(orientation="horizontal", spacing=10, **kwargs)
        self.service = service
        self.on_program_ready = on_program_ready
        self.generated_gcode = ""
        self.face_enabled = False
        self.rough_enabled = False
        self.finish_enabled = False
        self.taper_enabled = False
        self.center_enabled = False
        self.drill_enabled = False
        self.bore_enabled = False
        self.thread_external_enabled = False
        self.thread_internal_enabled = False
        self.thread_taper_enabled = False

        paint(self, PANEL)
        self._build()

    def _build(self) -> None:
        form_side = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.48)
        paint(form_side, PANEL_ALT)
        form_side.add_widget(section_label("CAM"))

        form_side.add_widget(self._build_stock_box())
        form_side.add_widget(self._build_cam_tab_bar())
        self.cam_tab_container = BoxLayout(orientation="vertical")
        self.turning_panel = self._build_turning_tab()
        self.hole_panel = self._build_hole_tab()
        self.thread_panel = self._build_thread_tab()
        self.cam_tab_container.add_widget(self.turning_panel)
        form_side.add_widget(self.cam_tab_container)

        action_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=58)
        generate = action_button("Generate", BLUE)
        load = action_button("Load to MDI", GREEN)
        run = action_button("Run", AMBER, width=100)
        bind_release(generate, lambda *_: self._generate())
        bind_release(load, lambda *_: self._load_to_mdi())
        bind_release(run, lambda *_: self._run())
        action_row.add_widget(generate)
        action_row.add_widget(load)
        action_row.add_widget(run)
        form_side.add_widget(action_row)

        self.cam_status = status_text("CAM ready")
        form_side.add_widget(self.cam_status)
        self.add_widget(form_side)

        preview_side = BoxLayout(orientation="vertical", spacing=8, size_hint_x=0.52)
        paint(preview_side, PANEL_ALT)
        preview_side.add_widget(section_label("3D Part / Toolpath"))
        preview_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=0.58)
        self.part_view = PartIsoCanvas(size_hint_x=0.48)
        preview_row.add_widget(self.part_view)
        self.preview = PreviewCanvas(size_hint_x=0.52)
        preview_row.add_widget(self.preview)
        preview_side.add_widget(preview_row)
        self.gcode_editor = gcode_input(
            text="",
            multiline=True,
            font_size=16,
            size_hint_y=0.42,
        )
        preview_side.add_widget(self.gcode_editor)
        self.add_widget(preview_side)
        self._update_part_preview(stale=False)

    def _build_stock_box(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", spacing=6, size_hint_y=None, height=132)
        paint(box, PANEL)
        box.add_widget(section_label("Stock"))

        stock_grid = GridLayout(cols=4, spacing=6)
        self.stock_diameter_input = self._cam_field("20.0", title_text="Stock Diameter")
        self.stock_length_input = self._cam_field("60.0", title_text="Stock Length")
        self.face_allowance_input = self._cam_field("1.0", title_text="Face Allowance")
        self.clearance_input = self._cam_field("3.0", title_text="Clearance")
        self._add_labeled(stock_grid, "Dia", self.stock_diameter_input)
        self._add_labeled(stock_grid, "Len", self.stock_length_input)
        self._add_labeled(stock_grid, "Face", self.face_allowance_input)
        self._add_labeled(stock_grid, "Clear", self.clearance_input)
        box.add_widget(stock_grid)
        return box

    def _build_cam_tab_bar(self) -> BoxLayout:
        tab_row = BoxLayout(orientation="horizontal", spacing=6, size_hint_y=None, height=52)
        self.cam_tab_buttons: dict[str, ToggleButton] = {}
        for name, label in (("turning", "Face/Rough"), ("hole", "Drill/Bore"), ("thread", "Thread")):
            button = ToggleButton(
                text=label,
                group="cam_tab",
                allow_no_selection=False,
                font_size=18,
                bold=True,
                color=TEXT,
                background_normal="",
                background_down="",
                background_color=BUTTON,
            )
            configure_touch_release(button, recover_stuck=False)
            if name == "turning":
                button.state = "down"
            bind_release(button, lambda btn, tab=name: self._show_cam_tab(tab))
            button.bind(state=lambda btn, _state: self._style_tab(btn))
            self._style_tab(button)
            self.cam_tab_buttons[name] = button
            tab_row.add_widget(button)
        return tab_row

    def _build_turning_tab(self) -> BoxLayout:
        tab = BoxLayout(orientation="vertical", spacing=8)
        paint(tab, PANEL)

        turn_grid = GridLayout(cols=4, spacing=6, size_hint_y=None, height=184)
        self.target_diameter_input = self._cam_field("16.0", title_text="Target Diameter")
        self.target_length_input = self._cam_field("60.0", title_text="Target Length")
        self.stock_leave_input = self._cam_field("0.5", title_text="Stock To Leave")
        self.stepover_input = self._cam_field("0.5", title_text="Turn Stepover")
        self.rough_feed_input = self._cam_field("80", title_text="Rough Feed")
        self.finish_feed_input = self._cam_field("40", title_text="Finish Feed")
        self.turn_rpm_input = self._cam_field("1200", title_text="Turning RPM")
        self.turn_tool_input = self._cam_field("1", integer=True, title_text="Turning Tool")
        self._add_labeled(turn_grid, "Target", self.target_diameter_input)
        self._add_labeled(turn_grid, "Length", self.target_length_input)
        self._add_labeled(turn_grid, "Leave", self.stock_leave_input)
        self._add_labeled(turn_grid, "Step", self.stepover_input)
        self._add_labeled(turn_grid, "Rough F", self.rough_feed_input)
        self._add_labeled(turn_grid, "Finish F", self.finish_feed_input)
        self._add_labeled(turn_grid, "RPM", self.turn_rpm_input)
        self._add_labeled(turn_grid, "Tool", self.turn_tool_input)
        tab.add_widget(turn_grid)

        flag_row = BoxLayout(orientation="horizontal", spacing=6, size_hint_y=None, height=52)
        for label, attr in (
            ("Face", "face_enabled"),
            ("Rough", "rough_enabled"),
            ("Finish", "finish_enabled"),
            ("Taper", "taper_enabled"),
        ):
            flag_row.add_widget(self._flag_button(label, attr))
        tab.add_widget(flag_row)

        self.tool_string_input = text_field("DCMT070204R")
        self.tool_string_input.bind(on_text_validate=lambda *_: self._cam_input_changed())
        tool_row = BoxLayout(orientation="horizontal", spacing=6, size_hint_y=None, height=42)
        tool_row.add_widget(Label(text="Insert", color=MUTED, font_size=17, size_hint_x=None, width=78))
        tool_row.add_widget(self.tool_string_input)
        tab.add_widget(tool_row)

        taper_grid = GridLayout(cols=4, spacing=6, size_hint_y=None, height=96)
        self.taper_start_diameter_input = self._cam_field("16.0", title_text="Taper Start Diameter")
        self.taper_end_diameter_input = self._cam_field("12.0", title_text="Taper End Diameter")
        self.taper_start_z_input = self._cam_field("0.0", title_text="Taper Start Z")
        self.taper_end_z_input = self._cam_field("-40.0", title_text="Taper End Z")
        self._add_labeled(taper_grid, "Dia A", self.taper_start_diameter_input)
        self._add_labeled(taper_grid, "Dia B", self.taper_end_diameter_input)
        self._add_labeled(taper_grid, "Z A", self.taper_start_z_input)
        self._add_labeled(taper_grid, "Z B", self.taper_end_z_input)
        tab.add_widget(taper_grid)
        return tab

    def _build_hole_tab(self) -> BoxLayout:
        tab = BoxLayout(orientation="vertical", spacing=8)
        paint(tab, PANEL)

        hole_flag_row = BoxLayout(orientation="horizontal", spacing=6, size_hint_y=None, height=52)
        for label, attr in (
            ("Center", "center_enabled"),
            ("Drill", "drill_enabled"),
            ("Bore", "bore_enabled"),
        ):
            hole_flag_row.add_widget(self._flag_button(label, attr))
        tab.add_widget(hole_flag_row)

        hole_grid = GridLayout(cols=4, spacing=6, size_hint_y=None, height=184)
        self.center_depth_input = self._cam_field("2.0", title_text="Center Drill Depth")
        self.drill_diameter_input = self._cam_field("6.0", title_text="Drill Diameter")
        self.drill_depth_input = self._cam_field("30.0", title_text="Drill Depth")
        self.bore_diameter_input = self._cam_field("10.0", title_text="Bore Diameter")
        self.bore_depth_input = self._cam_field("25.0", title_text="Bore Depth")
        self.boring_step_input = self._cam_field("0.5", title_text="Boring Stepover")
        self.hole_rpm_input = self._cam_field("1000", title_text="Hole RPM")
        self.boring_tool_input = self._cam_field("4", integer=True, title_text="Boring Tool")
        self._add_labeled(hole_grid, "Center Z", self.center_depth_input)
        self._add_labeled(hole_grid, "Drill dia", self.drill_diameter_input)
        self._add_labeled(hole_grid, "Drill Z", self.drill_depth_input)
        self._add_labeled(hole_grid, "Bore dia", self.bore_diameter_input)
        self._add_labeled(hole_grid, "Bore Z", self.bore_depth_input)
        self._add_labeled(hole_grid, "Bore step", self.boring_step_input)
        self._add_labeled(hole_grid, "RPM", self.hole_rpm_input)
        self._add_labeled(hole_grid, "Bore tool", self.boring_tool_input)
        tab.add_widget(hole_grid)
        return tab

    def _build_thread_tab(self) -> BoxLayout:
        tab = BoxLayout(orientation="vertical", spacing=8)
        paint(tab, PANEL)

        flag_row = BoxLayout(orientation="horizontal", spacing=6, size_hint_y=None, height=52)
        flag_row.add_widget(self._flag_button("External", "thread_external_enabled"))
        internal = self._flag_button("Internal", "thread_internal_enabled")
        internal.disabled = True
        internal.color = MUTED
        taper = self._flag_button("Taper", "thread_taper_enabled")
        taper.disabled = True
        taper.color = MUTED
        flag_row.add_widget(internal)
        flag_row.add_widget(taper)
        tab.add_widget(flag_row)

        thread_grid = GridLayout(cols=4, spacing=6, size_hint_y=None, height=230)
        self.thread_major_input = self._cam_field("16.0", title_text="Thread Major Diameter")
        self.thread_pitch_input = self._cam_field("1.0", title_text="Thread Pitch")
        self.thread_length_input = self._cam_field("20.0", title_text="Thread Length")
        self.thread_depth_input = self._cam_field("1.23", title_text="Thread Depth On Diameter")
        self.thread_passes_input = self._cam_field("10", integer=True, title_text="Thread Passes")
        self.thread_spring_input = self._cam_field("1", integer=True, title_text="Spring Passes")
        self.thread_start_z_input = self._cam_field("0.0", title_text="Thread Start Z")
        self.thread_clearance_input = self._cam_field("3.0", title_text="Thread Clearance")
        self.thread_rpm_input = self._cam_field("300", title_text="Thread RPM")
        self.thread_tool_input = self._cam_field("6", integer=True, title_text="Thread Tool")
        self._add_labeled(thread_grid, "Major", self.thread_major_input)
        self._add_labeled(thread_grid, "Pitch", self.thread_pitch_input)
        self._add_labeled(thread_grid, "Length", self.thread_length_input)
        self._add_labeled(thread_grid, "Depth", self.thread_depth_input)
        self._add_labeled(thread_grid, "Passes", self.thread_passes_input)
        self._add_labeled(thread_grid, "Spring", self.thread_spring_input)
        self._add_labeled(thread_grid, "Start Z", self.thread_start_z_input)
        self._add_labeled(thread_grid, "Clear", self.thread_clearance_input)
        self._add_labeled(thread_grid, "RPM", self.thread_rpm_input)
        self._add_labeled(thread_grid, "Tool", self.thread_tool_input)
        tab.add_widget(thread_grid)
        return tab

    def refresh(self, state: MachineState) -> None:
        self.preview.set_tool_position(x_mm=state.work_x_mm, z_mm=state.work_z_mm)

    def _cam_field(
        self,
        text: str,
        *,
        integer: bool = False,
        title_text: str = "Number",
    ) -> NumberEntryButton:
        return field_input(
            text,
            integer=integer,
            title_text=title_text,
            on_value=lambda _value: self._cam_input_changed(),
        )

    def _add_labeled(self, grid: GridLayout, label: str, widget) -> None:
        grid.add_widget(Label(text=label, color=MUTED, font_size=17))
        grid.add_widget(widget)

    def _show_cam_tab(self, tab_name: str) -> None:
        self.cam_tab_container.clear_widgets()
        if tab_name == "hole":
            self.cam_tab_container.add_widget(self.hole_panel)
        elif tab_name == "thread":
            self.cam_tab_container.add_widget(self.thread_panel)
        else:
            self.cam_tab_container.add_widget(self.turning_panel)

    def _style_tab(self, button: ToggleButton) -> None:
        button.background_color = BLUE if button.state == "down" else BUTTON

    def _flag_button(self, label: str, attr: str) -> ToggleButton:
        button = ToggleButton(
            text=label,
            allow_no_selection=True,
            font_size=18,
            bold=True,
            color=TEXT,
            background_normal="",
            background_down="",
            background_color=BUTTON,
        )
        configure_touch_release(button, recover_stuck=False)
        button.state = "down" if getattr(self, attr) else "normal"
        self._style_flag(button)
        bind_release(button, lambda btn: self._set_flag(attr, btn))
        button.bind(state=lambda btn, _state: self._style_flag(btn))
        return button

    def _set_flag(self, attr: str, button: ToggleButton) -> None:
        setattr(self, attr, button.state == "down")
        self._cam_input_changed()

    def _style_flag(self, button: ToggleButton) -> None:
        button.background_color = GREEN if button.state == "down" else BUTTON

    def _cam_input_changed(self) -> None:
        self.generated_gcode = ""
        if hasattr(self, "gcode_editor"):
            self.gcode_editor.text = ""
        self._update_part_preview(stale=True)

    def _update_part_preview(self, *, stale: bool, set_status: bool = True) -> bool:
        if not hasattr(self, "preview"):
            return False
        try:
            job = self._job_from_fields()
            outline = build_part_outline(job)
        except ValueError as exc:
            self.preview.set_preview(None, part_outline=None)
            if set_status:
                self._set_status(str(exc), RED)
            return False
        self.preview.set_preview(None, part_outline=outline)
        solid_error = self.part_view.set_job(job) if hasattr(self, "part_view") else ""
        if set_status:
            if solid_error:
                self._set_status(solid_error, RED)
            else:
                message = "Part view updated; generate toolpath/G-code" if stale else "Part view ready"
                self._set_status(message, TEXT)
        return not solid_error

    def _generate(self) -> bool:
        try:
            job = self._job_from_fields()
            program = generate_cam_program(job)
            result = parse_gcode(
                program.gcode,
                start_x_mm=self.service.state.work_x_mm,
                start_z_mm=self.service.state.work_z_mm,
            )
        except (ValueError, CamGenerationError, GCodeParseError) as exc:
            self.generated_gcode = ""
            self.gcode_editor.text = ""
            self._update_part_preview(stale=False, set_status=False)
            self._set_status(str(exc), RED)
            return False

        limit_error = self._preview_limit_error(result.actions)
        if limit_error is not None:
            self.generated_gcode = ""
            self.gcode_editor.text = program.gcode
            self.preview.set_preview(None, part_outline=program.part_outline)
            self._set_status(limit_error, RED)
            return False

        preview_path = build_preview(
            result.actions,
            start_x_mm=self.service.state.work_x_mm,
            start_z_mm=self.service.state.work_z_mm,
        )
        self.generated_gcode = program.gcode
        self.gcode_editor.text = program.gcode
        self.preview.set_preview(preview_path, part_outline=program.part_outline)
        if hasattr(self, "part_view"):
            self.part_view.set_job(job)
        self._set_status(
            f"Generated {len(result.actions)} action(s), {len(preview_path.segments)} move(s)",
            TEXT,
        )
        return True

    def _load_to_mdi(self) -> None:
        if self.generated_gcode or self._generate():
            self.on_program_ready(self.generated_gcode, run=False)

    def _run(self) -> None:
        if self.generated_gcode or self._generate():
            self.on_program_ready(self.generated_gcode, run=True)

    def _job_from_fields(self) -> LatheCamJob:
        turning_tool = int(parse_number(self.turn_tool_input.text, 1))
        boring_tool = int(parse_number(self.boring_tool_input.text, 4))
        thread_tool = int(parse_number(self.thread_tool_input.text, 6))
        return LatheCamJob(
            stock=StockSpec(
                diameter_mm=parse_number(self.stock_diameter_input.text, 20.0),
                length_mm=parse_number(self.stock_length_input.text, 60.0),
                face_allowance_mm=parse_number(self.face_allowance_input.text, 1.0),
                clearance_mm=parse_number(self.clearance_input.text, 3.0),
            ),
            turning=TurningSpec(
                enabled=self.face_enabled or self.rough_enabled or self.finish_enabled or self.taper_enabled,
                face=self.face_enabled,
                rough=self.rough_enabled,
                finish=self.finish_enabled,
                target_diameter_mm=parse_number(self.target_diameter_input.text, 16.0),
                target_length_mm=parse_number(self.target_length_input.text, 60.0),
                stock_to_leave_mm=parse_number(self.stock_leave_input.text, 0.5),
                step_over_mm=parse_number(self.stepover_input.text, 0.5),
                rough_feed=parse_number(self.rough_feed_input.text, 80.0),
                finish_feed=parse_number(self.finish_feed_input.text, 40.0),
                spindle_rpm=parse_number(self.turn_rpm_input.text, 1200.0),
                tool_number=turning_tool,
                station=turning_tool,
                tool_string=self.tool_string_input.text.strip() or "DCMT070204R",
            ),
            taper=TaperSpec(
                enabled=self.taper_enabled,
                start_diameter_mm=parse_number(self.taper_start_diameter_input.text, 16.0),
                end_diameter_mm=parse_number(self.taper_end_diameter_input.text, 12.0),
                start_z_mm=parse_number(self.taper_start_z_input.text, 0.0),
                end_z_mm=parse_number(self.taper_end_z_input.text, -40.0),
            ),
            hole=HoleSpec(
                center_drill=self.center_enabled,
                drill=self.drill_enabled,
                bore=self.bore_enabled,
                center_depth_mm=parse_number(self.center_depth_input.text, 2.0),
                drill_diameter_mm=parse_number(self.drill_diameter_input.text, 6.0),
                drill_depth_mm=parse_number(self.drill_depth_input.text, 30.0),
                bore_diameter_mm=parse_number(self.bore_diameter_input.text, 10.0),
                bore_depth_mm=parse_number(self.bore_depth_input.text, 25.0),
                boring_step_over_mm=parse_number(self.boring_step_input.text, 0.5),
                spindle_rpm=parse_number(self.hole_rpm_input.text, 1000.0),
                boring_tool_number=boring_tool,
                boring_station=boring_tool,
            ),
            thread=ThreadSpec(
                external=self.thread_external_enabled,
                internal=self.thread_internal_enabled,
                taper=self.thread_taper_enabled,
                major_diameter_mm=parse_number(self.thread_major_input.text, 16.0),
                pitch_mm=parse_number(self.thread_pitch_input.text, 1.0),
                length_mm=parse_number(self.thread_length_input.text, 20.0),
                depth_mm=parse_number(self.thread_depth_input.text, 1.23),
                passes=int(parse_number(self.thread_passes_input.text, 10)),
                spring_passes=int(parse_number(self.thread_spring_input.text, 1)),
                start_z_mm=parse_number(self.thread_start_z_input.text, 0.0),
                clearance_mm=parse_number(self.thread_clearance_input.text, 3.0),
                spindle_rpm=parse_number(self.thread_rpm_input.text, 300.0),
                tool_number=thread_tool,
                station=thread_tool,
            ),
        )

    def _preview_limit_error(self, actions: list[CanonicalAction]) -> str | None:
        preview = build_preview(
            actions,
            start_x_mm=self.service.state.work_x_mm,
            start_z_mm=self.service.state.work_z_mm,
        )
        for segment in preview.segments:
            error = self.service.limits_error_for_work_target(
                segment.end_x_mm,
                segment.end_z_mm,
                f"CAM preview line {segment.line_number}",
            )
            if error is not None:
                return error
        return None

    def _set_status(self, message: str, color) -> None:
        self.cam_status.text = message
        self.cam_status.color = color


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


class PartIsoCanvas(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mesh = None
        self.job: LatheCamJob | None = None
        self.error_message = ""
        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())

    def set_job(self, job: LatheCamJob) -> str:
        try:
            self.mesh = build_part_mesh(job)
            self.job = job
            self.error_message = ""
        except (CamSolidError, CamValidationError) as exc:
            self.mesh = None
            self.job = None
            self.error_message = str(exc)
        self._redraw()
        return self.error_message

    def _redraw(self) -> None:
        self.canvas.clear()
        with self.canvas:
            Color(0.045, 0.047, 0.05, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0.24, 0.25, 0.27, 1)
            Line(rectangle=(self.x + 8, self.y + 8, max(0, self.width - 16), max(0, self.height - 16)), width=1)

            if self.mesh is None:
                return

            vertices = self.mesh.vertices
            faces = self.mesh.faces
            if len(vertices) == 0 or len(faces) == 0:
                return

            projected = [self._project(vertex) for vertex in vertices]
            min_x = min(point[0] for point in projected)
            max_x = max(point[0] for point in projected)
            min_y = min(point[1] for point in projected)
            max_y = max(point[1] for point in projected)
            if max_x - min_x <= 1e-9 or max_y - min_y <= 1e-9:
                return

            pad = 20
            draw_w = max(1.0, self.width - 2 * pad)
            draw_h = max(1.0, self.height - 2 * pad)
            scale = min(draw_w / (max_x - min_x), draw_h / (max_y - min_y))
            offset_x = self.x + pad + (draw_w - (max_x - min_x) * scale) / 2.0
            offset_y = self.y + pad + (draw_h - (max_y - min_y) * scale) / 2.0

            def screen_point(vertex_index: int) -> tuple[float, float]:
                px, py, _depth = projected[vertex_index]
                return (
                    offset_x + (px - min_x) * scale,
                    offset_y + (py - min_y) * scale,
                )

            face_items = []
            normals = self.mesh.face_normals
            for face_index, face in enumerate(faces):
                points = [screen_point(int(vertex_index)) for vertex_index in face]
                if _triangle_area(points) < 0.25:
                    continue
                depth = sum(projected[int(vertex_index)][2] for vertex_index in face) / 3.0
                normal = normals[face_index] if face_index < len(normals) else (0.0, 0.0, 1.0)
                shade = self._face_shade(normal)
                face_items.append((depth, points, shade))

            for _depth, points, shade in sorted(face_items, key=lambda item: item[0]):
                Color(0.50 * shade, 0.58 * shade, 0.64 * shade, 1)
                Mesh(
                    vertices=[
                        points[0][0],
                        points[0][1],
                        0,
                        0,
                        points[1][0],
                        points[1][1],
                        0,
                        0,
                        points[2][0],
                        points[2][1],
                        0,
                        0,
                    ],
                    indices=[0, 1, 2],
                    mode="triangles",
                )

            self._draw_reference_edges(projected, vertices)
            self._draw_thread_overlay(projected)

    @staticmethod
    def _project(vertex) -> tuple[float, float, float]:
        lathe_z = float(vertex[0])
        radial_y = float(vertex[1])
        radial_x = float(vertex[2])
        recede = -lathe_z
        projected_x = recede * 0.86 + radial_y * 0.36
        projected_y = radial_x * 0.94 + radial_y * 0.26 + recede * 0.14
        depth = lathe_z * 1.0 + radial_y * 0.42 + radial_x * 0.12
        return projected_x, projected_y, depth

    @staticmethod
    def _face_shade(normal) -> float:
        axial = float(normal[0])
        radial_y = float(normal[1])
        radial_x = float(normal[2])
        if axial > 0.55:
            return 0.95
        if axial < -0.55:
            return 0.50
        return max(0.62, min(0.84, 0.72 + radial_x * 0.10 + radial_y * 0.05))

    def _draw_reference_edges(self, projected, vertices) -> None:
        stations = self._ring_stations(vertices)
        if not stations:
            return

        front_z = max(stations)
        back_z = min(stations)
        front_outer = stations[front_z]
        front_inner = self._front_inner_radius()

        Color(0.78, 0.86, 0.91, 1)
        self._draw_ring(front_z, front_outer, width=2.6)
        if front_inner > 0.0:
            Color(0.035, 0.04, 0.045, 1)
            self._fill_ring_disc(front_z, front_inner)
            Color(0.86, 0.70, 0.38, 1)
            self._draw_ring(front_z, front_inner, width=2.2)

        Color(0.38, 0.45, 0.50, 0.78)
        self._draw_ring(back_z, stations[back_z], width=1.4)

        for angle, width in ((math.pi / 2.0, 1.9), (-math.pi / 2.0, 1.5)):
            points: list[float] = []
            for z_mm in sorted(stations, reverse=True):
                radius = stations[z_mm]
                px, py, _depth = self._project(
                    (
                        z_mm,
                        radius * math.cos(angle),
                        radius * math.sin(angle),
                    )
                )
                screen_x, screen_y = self._screen_from_projected(px, py, projected)
                points.extend([screen_x, screen_y])
            Color(0.70, 0.78, 0.83, 0.92)
            Line(points=points, width=width)

    def _draw_thread_overlay(self, projected) -> None:
        if self.job is None or not self.job.thread.external:
            return
        thread = self.job.thread
        if thread.pitch_mm <= 0.0 or thread.length_mm <= 0.0 or thread.major_diameter_mm <= 0.0:
            return

        major_radius = thread.major_diameter_mm / 2.0
        minor_radius = max(0.0, (thread.major_diameter_mm - thread.depth_mm) / 2.0)
        if major_radius <= 0.0 or minor_radius <= 0.0 or minor_radius >= major_radius:
            return

        turns = max(1.0, thread.length_mm / thread.pitch_mm)
        steps = max(24, min(720, math.ceil(turns * 28)))
        Color(*THREAD)
        self._draw_thread_helix(
            thread.start_z_mm,
            thread.length_mm,
            thread.pitch_mm,
            major_radius,
            steps,
            projected,
            phase=0.0,
            width=1.6,
        )
        Color(0.10, 0.38, 0.48, 0.78)
        self._draw_thread_helix(
            thread.start_z_mm - thread.pitch_mm / 2.0,
            max(0.0, thread.length_mm - thread.pitch_mm / 2.0),
            thread.pitch_mm,
            minor_radius,
            steps,
            projected,
            phase=math.pi,
            width=1.0,
        )

    def _draw_thread_helix(
        self,
        start_z: float,
        length: float,
        pitch: float,
        radius: float,
        steps: int,
        projected,
        *,
        phase: float,
        width: float,
    ) -> None:
        if length <= 0.0 or pitch <= 0.0:
            return
        points: list[float] = []
        for index in range(steps + 1):
            fraction = index / steps
            z_mm = start_z - length * fraction
            angle = phase + math.tau * (length * fraction / pitch)
            px, py, _depth = self._project(
                (
                    z_mm,
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                )
            )
            screen_x, screen_y = self._screen_from_projected(px, py, projected)
            points.extend([screen_x, screen_y])
        if len(points) >= 4:
            Line(points=points, width=width)

    def _ring_stations(self, vertices) -> dict[float, float]:
        stations: dict[float, float] = {}
        for vertex in vertices:
            z_mm = round(float(vertex[0]), 6)
            radius = math.hypot(float(vertex[1]), float(vertex[2]))
            stations[z_mm] = max(stations.get(z_mm, 0.0), radius)
        return stations

    def _front_inner_radius(self) -> float:
        if self.job is None:
            return 0.0
        if self.job.hole.bore:
            return self.job.hole.bore_diameter_mm / 2.0
        if self.job.hole.drill:
            return self.job.hole.drill_diameter_mm / 2.0
        if self.job.hole.center_drill:
            return min(self.job.hole.drill_diameter_mm / 2.0, self.job.stock.diameter_mm / 8.0)
        return 0.0

    def _draw_ring(self, z_mm: float, radius: float, *, width: float) -> None:
        points: list[float] = []
        for index in range(65):
            angle = math.tau * index / 64
            px, py, _depth = self._project(
                (
                    z_mm,
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                )
            )
            screen_x, screen_y = self._screen_from_projected(px, py)
            points.extend([screen_x, screen_y])
        Line(points=points, width=width)

    def _fill_ring_disc(self, z_mm: float, radius: float) -> None:
        center_x, center_y = self._screen_from_projected(*self._project((z_mm, 0.0, 0.0))[:2])
        vertices = [center_x, center_y, 0, 0]
        indices: list[int] = []
        for index in range(64):
            angle = math.tau * index / 64
            px, py, _depth = self._project(
                (
                    z_mm,
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                )
            )
            screen_x, screen_y = self._screen_from_projected(px, py)
            vertices.extend([screen_x, screen_y, 0, 0])
            if index < 63:
                indices.extend([0, index + 1, index + 2])
            else:
                indices.extend([0, index + 1, 1])
        Mesh(vertices=vertices, indices=indices, mode="triangles")

    def _screen_from_projected(
        self,
        px: float,
        py: float,
        projected: list[tuple[float, float, float]] | None = None,
    ) -> tuple[float, float]:
        if projected is None:
            if self.mesh is None:
                return self.x, self.y
            projected = [self._project(vertex) for vertex in self.mesh.vertices]
        min_x = min(point[0] for point in projected)
        max_x = max(point[0] for point in projected)
        min_y = min(point[1] for point in projected)
        max_y = max(point[1] for point in projected)
        pad = 20
        draw_w = max(1.0, self.width - 2 * pad)
        draw_h = max(1.0, self.height - 2 * pad)
        scale = min(draw_w / max(1e-9, max_x - min_x), draw_h / max(1e-9, max_y - min_y))
        offset_x = self.x + pad + (draw_w - (max_x - min_x) * scale) / 2.0
        offset_y = self.y + pad + (draw_h - (max_y - min_y) * scale) / 2.0
        return (
            offset_x + (px - min_x) * scale,
            offset_y + (py - min_y) * scale,
        )


class PreviewCanvas(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.preview_path: PreviewPath | None = None
        self.part_outline: list[PreviewSegment] = []
        self.tool_x_mm: float | None = None
        self.tool_z_mm: float | None = None
        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())

    def set_preview(
        self,
        preview_path: PreviewPath | None,
        *,
        part_outline: list[PreviewSegment] | None = None,
    ) -> None:
        self.preview_path = preview_path
        self.part_outline = part_outline or []
        self._redraw()

    def set_tool_position(self, *, x_mm: float, z_mm: float) -> None:
        self.tool_x_mm = x_mm
        self.tool_z_mm = z_mm
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.clear()
        with self.canvas:
            Color(0.05, 0.05, 0.05, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0.24, 0.25, 0.27, 1)
            Line(rectangle=(self.x + 8, self.y + 8, max(0, self.width - 16), max(0, self.height - 16)), width=1)

            has_tool = self.tool_x_mm is not None and self.tool_z_mm is not None
            if (
                (self.preview_path is None or not self.preview_path.segments)
                and not self.part_outline
                and not has_tool
            ):
                return

            all_segments = []
            if self.preview_path is not None:
                all_segments.extend(self.preview_path.segments)
            all_segments.extend(self.part_outline)
            xs = [value for segment in all_segments for value in (segment.start_x_mm, segment.end_x_mm)]
            zs = [value for segment in all_segments for value in (segment.start_z_mm, segment.end_z_mm)]
            if has_tool:
                xs.append(self.tool_x_mm)
                zs.append(self.tool_z_mm)
            min_z, max_z = min(zs), max(zs)
            min_x, max_x = min(xs), max(xs)
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

            for segment in self.part_outline:
                if segment.mode == "thread":
                    Color(*THREAD)
                    width = 1.8
                elif segment.mode == "hole":
                    Color(0.65, 0.48, 0.22, 1)
                    width = 2.4
                else:
                    Color(0.42, 0.55, 0.66, 1)
                    width = 2.4
                x0, y0 = map_point(segment.start_x_mm, segment.start_z_mm)
                x1, y1 = map_point(segment.end_x_mm, segment.end_z_mm)
                Line(points=[x0, y0, x1, y1], width=width)

            if self.preview_path is None:
                self._draw_tool_marker(map_point)
                return

            for segment in self.preview_path.segments:
                if segment.mode == "thread":
                    Color(*THREAD_TOOLPATH)
                    width = 0.9
                elif segment.mode == "rapid":
                    Color(*AMBER)
                    width = 0.8
                else:
                    Color(*GREEN)
                    width = 1.1
                x0, y0 = map_point(segment.start_x_mm, segment.start_z_mm)
                x1, y1 = map_point(segment.end_x_mm, segment.end_z_mm)
                Line(points=[x0, y0, x1, y1], width=width)

            self._draw_tool_marker(map_point)

    def _draw_tool_marker(self, map_point: Callable[[float, float], tuple[float, float]]) -> None:
        if self.tool_x_mm is None or self.tool_z_mm is None:
            return
        tool_x, tool_y = map_point(self.tool_x_mm, self.tool_z_mm)
        size = max(8.0, min(16.0, min(self.width, self.height) * 0.035))
        Color(0.96, 0.88, 0.22, 1)
        base_y = tool_y - size * 1.72
        Mesh(
            vertices=[
                tool_x,
                tool_y,
                0,
                0,
                tool_x - size * 0.86,
                base_y,
                0,
                0,
                tool_x + size * 0.86,
                base_y,
                0,
                0,
            ],
            indices=[0, 1, 2],
            mode="triangles",
        )
        Color(0.08, 0.08, 0.06, 1)
        Line(
            points=[
                tool_x,
                tool_y,
                tool_x - size * 0.86,
                base_y,
                tool_x + size * 0.86,
                base_y,
                tool_x,
                tool_y,
            ],
            width=1.4,
        )


class JogQueueBar(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.progress = 0.0
        with self.canvas.before:
            Color(0.10, 0.11, 0.12, 1)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
            Color(*BLUE)
            self._fill_rect = Rectangle(pos=self.pos, size=(0, self.height))
        self.bind(pos=self._update_rects, size=self._update_rects)

    def set_progress(self, progress: float) -> None:
        self.progress = max(0.0, min(1.0, progress))
        self._update_rects()

    def _update_rects(self, *_args) -> None:
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size
        self._fill_rect.pos = self.pos
        self._fill_rect.size = (self.width * self.progress, self.height)


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
    button = Button(
        text=text,
        font_size=22,
        bold=True,
        color=TEXT,
        background_normal="",
        background_color=color,
        **kwargs,
    )
    configure_touch_release(button)
    return button


def jog_button(text: str) -> Button:
    return action_button(text, BLUE)


def toggle_button(text: str, *, group: str) -> ToggleButton:
    button = ToggleButton(
        text=text,
        group=group,
        allow_no_selection=False,
        font_size=20,
        bold=True,
        color=TEXT,
        background_normal="",
        background_down="",
        background_color=BUTTON,
    )
    configure_touch_release(button, recover_stuck=False)
    return button


def numeric_input(
    text: str,
    *,
    width: int | None = None,
    integer: bool = False,
    title_text: str = "Number",
    on_value: Callable[[float | int], None] | None = None,
) -> NumberEntryButton:
    kwargs = {}
    if width is not None:
        kwargs = {"size_hint_x": None, "width": width}
    return NumberEntryButton(
        text=text,
        integer=integer,
        title_text=title_text,
        on_value=on_value,
        font_size=28,
        **kwargs,
    )


def field_input(
    text: str,
    *,
    integer: bool = False,
    title_text: str = "Number",
    on_value: Callable[[float | int], None] | None = None,
) -> NumberEntryButton:
    return NumberEntryButton(
        text=text,
        integer=integer,
        title_text=title_text,
        on_value=on_value,
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


def gcode_input(
    *,
    text: str,
    multiline: bool,
    font_size: int,
    padding: tuple[int, int, int, int] | None = None,
    **kwargs,
) -> CodeInput:
    if padding is None:
        padding = (8, 8, 8, 8)
    return CodeInput(
        text=text,
        lexer=LinuxCncGCodeLexer(),
        style=TclGCodeStyle,
        multiline=multiline,
        do_wrap=False,
        font_size=font_size,
        foreground_color=TEXT,
        background_color=(0.05, 0.05, 0.05, 1),
        cursor_color=TEXT,
        padding=padding,
        **kwargs,
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


def _triangle_area(points: list[tuple[float, float]]) -> float:
    return abs(
        (
            points[0][0] * (points[1][1] - points[2][1])
            + points[1][0] * (points[2][1] - points[0][1])
            + points[2][0] * (points[0][1] - points[1][1])
        )
        / 2.0
    )
