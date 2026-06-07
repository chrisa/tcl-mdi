from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

from tcl_lathe_hmi.config import JOG_INCREMENTS_MM, MachineConfig
from tcl_lathe_hmi.machine import MachineService, MachineState
from tcl_lathe_hmi.ui.controls import (
    AMBER,
    BG,
    BLUE,
    BUTTON,
    GREEN,
    MUTED,
    PANEL,
    PANEL_ALT,
    RED,
    TEXT,
    action_button,
    backend_label,
    field_input,
    jog_button,
    numeric_input,
    paint,
    section_label,
    status_color,
    toggle_button,
)
from tcl_lathe_hmi.ui.dro import MachineReadouts
from tcl_lathe_hmi.ui.form_values import parse_number
from tcl_lathe_hmi.ui.jog_queue import JogQueueBar
from tcl_lathe_hmi.ui.keypad import NumberEntryButton
from tcl_lathe_hmi.ui.panels.cam import CamPanel
from tcl_lathe_hmi.ui.panels.program import ProgramPanel
from tcl_lathe_hmi.ui.panels.setup import SetupPanel
from tcl_lathe_hmi.ui.panels.tools import ToolsPanel
from tcl_lathe_hmi.ui.widgets import bind_release


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
        self.selected_tool_position: int | None = None
        self.tool_position_buttons: dict[int, Button] = {}
        self.manual_set_current_button: Button | None = None
        self.manual_change_button: Button | None = None
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
        self._tool_button_flash_event = None
        self._tool_button_flash_phase = 0
        self._tool_button_flash_station: int | None = None
        self._tool_button_flash_action: str | None = None
        self._tool_button_flash_ticks_remaining = 0

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
        if self._tool_button_flash_event is not None:
            self._tool_button_flash_event.cancel()
            self._tool_button_flash_event = None

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

        self.readouts = MachineReadouts(size_hint_x=1, size_hint_y=0.66)
        panel.add_widget(self.readouts)

        spindle = self._build_spindle_controls()
        spindle.size_hint_y = 0.34
        panel.add_widget(spindle)
        return panel

    def _build_manual_work(self) -> BoxLayout:
        panel = BoxLayout(orientation="vertical", spacing=10)
        paint(panel, PANEL)
        panel.add_widget(self._build_jog_settings())
        panel.add_widget(self._build_jog_buttons())
        panel.add_widget(self._build_toolchanger_controls())
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

        self.custom_increment_button = toggle_button("Custom", group="jog_increment")
        bind_release(self.custom_increment_button, self._set_custom_increment)
        self.custom_increment_button.bind(state=lambda button, _state: self._style_toggle(button))
        self._style_toggle(self.custom_increment_button)
        self.jog_increment_buttons.append(self.custom_increment_button)
        self.command_widgets.append(self.custom_increment_button)
        increments.add_widget(self.custom_increment_button)

        self.custom_increment_input = numeric_input(
            f"{self.custom_increment_mm:0.3f}",
            width=150,
            title_text="Jog Distance",
            on_value=self._custom_increment_changed,
        )
        self.command_widgets.append(self.custom_increment_input)
        increments.add_widget(self.custom_increment_input)
        increments.add_widget(Label(text="mm", color=MUTED, font_size=24, size_hint_x=None, width=48))

        box.add_widget(increments)

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
        row.add_widget(rapid)
        row.add_widget(feed)

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

        fields = GridLayout(cols=8, spacing=6, size_hint_y=None, height=46)

        for pos in range(1, 9):
            pos_select = action_button(f"P{pos}", BLUE)
            bind_release(pos_select, lambda b, i=pos: self._manual_select_position(b, i))
            self.tool_position_buttons[pos] = pos_select
            self.command_widgets.append(pos_select)
            fields.add_widget(pos_select)
        box.add_widget(fields)

        row = BoxLayout(orientation="horizontal", spacing=8)
        self.manual_set_current_button = action_button("Set Current", BLUE)
        self.manual_change_button = action_button("Change", GREEN)
        bind_release(self.manual_set_current_button, lambda *_: self._manual_set_current_station())
        bind_release(self.manual_change_button, lambda *_: self._manual_change_tool())
        row.add_widget(self.manual_set_current_button)
        row.add_widget(self.manual_change_button)
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
                self.manual_teach_diameter_input,
                self.manual_set_current_button,
                self.manual_change_button,
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

        self.readouts.refresh(state)

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
            self._refresh_manual_toolchanger_buttons(state)
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

    def _manual_select_position(self, button: Button, pos: int) -> None:
        if not 1 <= pos <= 8:
            return
        if self._tool_button_flash_station is not None:
            self._stop_manual_tool_button_flash(clear_selection=False)
        self.selected_tool_position = pos
        self.refresh(self.service.state)

    def _manual_set_current_station(self) -> None:
        if self.selected_tool_position is None:
            self._set_status("Select a turret station first", flash=True)
            return
        station = self.selected_tool_position
        ok = self.service.set_turret_station(station)
        self._set_status(self.service.state.status_message, flash=not ok)
        if ok:
            self._start_manual_tool_button_flash(station=station, action="set_current", ticks=4)
            self._sync_tools_panel_from_service()

    def _manual_change_tool(self) -> None:
        if self.selected_tool_position is None:
            self._set_status("Select a turret station first", flash=True)
            return
        station = self.selected_tool_position
        tool_number = self.service.tool_for_station(station)
        if tool_number is None:
            self._set_status(
                f"No tool is assigned to P{station}; use Set Current or assign it on the Tools tab",
                flash=True,
            )
            return

        pending_tool = self.service.state.pending_tool
        pending_station = self.service.state.pending_turret_station
        ok = self.service.change_tool(
            tool_number,
            station=station,
            context="Manual toolchanger",
        )
        if ok and pending_tool is not None:
            self.service.state = replace(
                self.service.state,
                pending_tool=pending_tool,
                pending_turret_station=pending_station,
            )
        self._set_status(self.service.state.status_message, flash=not ok)
        if ok:
            self._start_manual_tool_button_flash(
                station=station,
                action="change",
                ticks=0,
            )
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
        self.tools_panel.refresh_tools(load_fields=True)
        self.tools_panel._load_tool_fields(self.service.state.active_tool)
        self.tools_panel.refresh(self.service.state)

    def _set_status(self, message: str, *, flash: bool = False) -> None:
        self.service.state = replace(self.service.state, status_message=message)
        if flash:
            self._flash_status_indicator()
        self.refresh(self.service.state)

    def _refresh_manual_toolchanger_buttons(self, state: MachineState) -> None:
        flash_station = self._tool_button_flash_station
        flash_phase = self._tool_button_flash_phase
        for station, button in self.tool_position_buttons.items():
            button.background_color = self._manual_tool_button_color(
                state,
                station,
                flashing=flash_station == station,
                flash_phase=flash_phase,
            )

        if self.manual_set_current_button is not None:
            self.manual_set_current_button.disabled = False
        if self.manual_change_button is not None:
            self.manual_change_button.disabled = False

    def _manual_tool_button_color(
        self,
        state: MachineState,
        station: int,
        *,
        flashing: bool,
        flash_phase: int,
    ):
        if flashing:
            return AMBER if flash_phase % 2 == 0 else BLUE
        if self.selected_tool_position == station:
            return AMBER
        if state.turret_station == station:
            return GREEN
        if (
            self.selected_tool_position is None
            and state.pending_tool is not None
            and state.pending_turret_station == station
        ):
            return AMBER
        return BLUE

    def _start_manual_tool_button_flash(
        self,
        *,
        station: int,
        action: str,
        ticks: int,
    ) -> None:
        if self._tool_button_flash_event is not None:
            self._tool_button_flash_event.cancel()
        self._tool_button_flash_station = station
        self._tool_button_flash_action = action
        self._tool_button_flash_ticks_remaining = ticks
        self._tool_button_flash_phase = 0
        self._tool_button_flash_event = Clock.schedule_interval(
            self._manual_tool_button_flash_tick,
            0.15,
        )
        self._refresh_manual_toolchanger_buttons(self.service.state)

    def _manual_tool_button_flash_tick(self, _dt):
        self._tool_button_flash_phase += 1
        if self._tool_button_flash_action == "change":
            if not self.service.state.busy:
                self._stop_manual_tool_button_flash(clear_selection=True)
                return False
        elif self._tool_button_flash_ticks_remaining > 0:
            self._tool_button_flash_ticks_remaining -= 1
        else:
            self._stop_manual_tool_button_flash(clear_selection=True)
            return False
        self._refresh_manual_toolchanger_buttons(self.service.state)
        return True

    def _stop_manual_tool_button_flash(self, *, clear_selection: bool) -> None:
        if self._tool_button_flash_event is not None:
            self._tool_button_flash_event.cancel()
            self._tool_button_flash_event = None
        self._tool_button_flash_phase = 0
        self._tool_button_flash_station = None
        self._tool_button_flash_action = None
        self._tool_button_flash_ticks_remaining = 0
        if clear_selection:
            self.selected_tool_position = None
        self._refresh_manual_toolchanger_buttons(self.service.state)

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
