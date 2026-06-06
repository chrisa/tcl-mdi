from __future__ import annotations

from pathlib import Path

from kivy.app import App
from kivy.clock import Clock

from tcl_lathe_hmi.backends import create_backend
from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.machine import MachineService
from tcl_lathe_hmi.ui.canvases import PartIsoCanvas, PreviewCanvas
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
    THREAD,
    THREAD_TOOLPATH,
    action_button,
    axis_label,
    backend_label,
    field_input,
    gcode_input,
    jog_button,
    numeric_input,
    paint,
    section_label,
    status_color,
    status_text,
    text_field,
    toggle_button,
)
from tcl_lathe_hmi.ui.dro import MachineReadouts
from tcl_lathe_hmi.ui.form_values import optional_int, parse_number
from tcl_lathe_hmi.ui.jog_queue import JogQueueBar
from tcl_lathe_hmi.ui.panels.cam import CamPanel
from tcl_lathe_hmi.ui.panels.manual import ManualPanel
from tcl_lathe_hmi.ui.panels.program import ProgramPanel
from tcl_lathe_hmi.ui.panels.setup import SetupPanel
from tcl_lathe_hmi.ui.panels.tools import ToolsPanel


START_MAXIMISE_RETRY_INTERVAL_S = 0.2
START_MAXIMISE_RETRIES = 10


class TclLatheHmiApp(App):
    title = "TCL Lathe HMI"

    def __init__(
        self, *, backend_name: str = "sim", start_maximised: bool = False, **kwargs
    ):
        super().__init__(**kwargs)
        self.machine_config = MachineConfig()
        self.backend_name = backend_name
        self.start_maximised = start_maximised
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

    def on_start(self):
        if self.start_maximised:
            for attempt in range(START_MAXIMISE_RETRIES):
                Clock.schedule_once(
                    self._maximise_startup_window,
                    attempt * START_MAXIMISE_RETRY_INTERVAL_S,
                )

    def _maximise_startup_window(self, *_args):
        from kivy.core.window import Window

        Window.maximize()

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


__all__ = [
    "AMBER",
    "BG",
    "BLUE",
    "BUTTON",
    "CamPanel",
    "GREEN",
    "JogQueueBar",
    "MUTED",
    "MachineReadouts",
    "ManualPanel",
    "PANEL",
    "PANEL_ALT",
    "PartIsoCanvas",
    "PreviewCanvas",
    "ProgramPanel",
    "RED",
    "SetupPanel",
    "TEXT",
    "THREAD",
    "THREAD_TOOLPATH",
    "TclLatheHmiApp",
    "ToolsPanel",
    "action_button",
    "axis_label",
    "backend_label",
    "field_input",
    "gcode_input",
    "jog_button",
    "numeric_input",
    "optional_int",
    "paint",
    "parse_number",
    "section_label",
    "status_color",
    "status_text",
    "text_field",
    "toggle_button",
]
