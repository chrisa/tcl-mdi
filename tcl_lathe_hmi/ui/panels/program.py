from __future__ import annotations

from pathlib import Path

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput

from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.gcode import (
    CanonicalAction,
    GCodeParseError,
    build_preview,
    parse_gcode,
)
from tcl_lathe_hmi.gcode.validation import preview_limit_error, tool_offset_warning
from tcl_lathe_hmi.machine import MachineService, MachineState
from tcl_lathe_hmi.ui.canvases import PreviewCanvas
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
    gcode_input,
    paint,
    section_label,
)
from tcl_lathe_hmi.ui.widgets import bind_release


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
        return preview_limit_error(
            actions,
            start_x_mm=self.service.state.work_x_mm,
            start_z_mm=self.service.state.work_z_mm,
            limits_error_for_work_target=self.service.limits_error_for_work_target,
            context="Preview",
        )

    def _tool_offset_warning(self, actions: list[CanonicalAction]) -> str:
        return tool_offset_warning(actions, get_tool=self.service.tool_table.get)
