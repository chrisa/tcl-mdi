from __future__ import annotations

from collections.abc import Callable

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.togglebutton import ToggleButton

from tcl_lathe_hmi.cam import (
    CamGenerationError,
    HoleSpec,
    LatheCamJob,
    StockSpec,
    TaperSpec,
    ThreadSpec,
    TurningSpec,
    build_part_outline,
    generate_cam_program,
)
from tcl_lathe_hmi.gcode import CanonicalAction, GCodeParseError, build_preview, parse_gcode
from tcl_lathe_hmi.gcode.validation import preview_limit_error
from tcl_lathe_hmi.machine import MachineService, MachineState
from tcl_lathe_hmi.ui.canvases import PartIsoCanvas, PreviewCanvas
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
    gcode_input,
    paint,
    section_label,
    status_text,
    text_field,
)
from tcl_lathe_hmi.ui.form_values import parse_number
from tcl_lathe_hmi.ui.keypad import NumberEntryButton
from tcl_lathe_hmi.ui.widgets import bind_release, configure_touch_release


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
        return preview_limit_error(
            actions,
            start_x_mm=self.service.state.work_x_mm,
            start_z_mm=self.service.state.work_z_mm,
            limits_error_for_work_target=self.service.limits_error_for_work_target,
            context="CAM preview",
        )

    def _set_status(self, message: str, color) -> None:
        self.cam_status.text = message
        self.cam_status.color = color
