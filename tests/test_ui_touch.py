from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("kivy")

from kivy.clock import Clock

from tcl_lathe_hmi.backends.sim import SimBackend
from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.gcode import ToolChangeAction
from tcl_lathe_hmi.machine.service import MachineService
from tcl_lathe_hmi.tools import ToolRecord
from tcl_lathe_hmi.ui.widgets import bind_release

from tcl_lathe_hmi.ui import app as app_module
from tcl_lathe_hmi.ui.app import ManualPanel, action_button, toggle_button
from tcl_lathe_hmi.ui.keypad import NumberEntryButton


def _manual_panel(tmp_path: Path, *, config: MachineConfig | None = None) -> ManualPanel:
    config = config or MachineConfig()
    service = MachineService(
        SimBackend(config),
        config=config,
        settings_path=tmp_path / "machine_state.json",
    )
    service.connect()
    return ManualPanel(
        service=service,
        config=config,
        initial_backend="sim",
        on_backend_change=lambda _backend: None,
    )


def _find_button(widget, text: str):
    if getattr(widget, "text", None) == text:
        return widget
    for child in getattr(widget, "children", []):
        found = _find_button(child, text)
        if found is not None:
            return found
    return None


def test_action_buttons_force_release_for_touchscreens():
    button = action_button("Run", (0.2, 0.4, 0.6, 1))

    assert button.always_release is True
    assert button._stuck_release_seconds > 0


def test_number_entry_buttons_force_release_for_touchscreens():
    button = NumberEntryButton(text="1.0")

    assert button.always_release is True
    assert button._stuck_release_seconds > 0


def test_toggle_buttons_always_release_without_forced_reset():
    button = toggle_button("Feed", group="mode")

    assert button.always_release is True
    assert not hasattr(button, "_stuck_release_seconds")


def test_stuck_action_button_recovers_and_fires_once():
    button = action_button("Run", (0.2, 0.4, 0.6, 1))
    button._stuck_release_seconds = 0
    calls: list[str] = []
    bind_release(button, lambda *_: calls.append("run"))

    button.state = "down"
    button.dispatch("on_press")
    Clock.tick()

    assert button.state == "normal"
    assert calls == ["run"]

    button.dispatch("on_release")

    assert calls == ["run"]


def test_default_jog_accumulate_delay_is_half_second():
    assert MachineConfig().jog_accumulate_delay_s == 0.5


def test_app_start_schedules_window_maximise(monkeypatch):
    calls: list[str] = []
    scheduled_once: list[tuple[Callable[[float], object], float]] = []
    fake_window_module = types.ModuleType("kivy.core.window")
    cast(Any, fake_window_module).Window = types.SimpleNamespace(
        maximize=lambda: calls.append("maximize"),
    )

    def fake_schedule_once(callback, timeout):
        scheduled_once.append((callback, timeout))

    monkeypatch.setitem(sys.modules, "kivy.core.window", fake_window_module)
    monkeypatch.setattr(app_module.Clock, "schedule_once", fake_schedule_once)
    app = app_module.TclLatheHmiApp.__new__(app_module.TclLatheHmiApp)
    app.start_maximised = True

    app_module.TclLatheHmiApp.on_start(app)

    assert scheduled_once == [
        (
            app._maximise_startup_window,
            attempt * app_module.START_MAXIMISE_RETRY_INTERVAL_S,
        )
        for attempt in range(app_module.START_MAXIMISE_RETRIES)
    ]

    callback, _timeout = scheduled_once[0]
    callback(0)

    assert calls == ["maximize"]


def test_manual_panel_shows_accumulated_jog_indicator_after_second_tap(tmp_path: Path):
    panel = _manual_panel(tmp_path)

    panel._jog(x_sign=1.0)
    assert panel.jog_queue_container is not None
    assert panel.jog_queue_bar is not None
    assert panel.jog_queue_length_label is not None
    assert panel.jog_queue_container.opacity == 0.0

    panel._jog(x_sign=1.0)
    second_tap_progress = panel.jog_queue_bar.progress
    assert panel.jog_queue_container.opacity == 1.0
    assert second_tap_progress > 0.0
    assert panel.jog_queue_length_label.text == "X +0.200 mm"

    panel._jog(x_sign=1.0)
    assert panel.jog_queue_bar.progress > second_tap_progress
    assert panel.jog_queue_length_label.text == "X +0.300 mm"

    if panel.queued_jog_event is not None:
        panel.queued_jog_event.cancel()
        panel.queued_jog_event = None
    panel._flush_queued_jog(0)

    assert panel.jog_queue_container.opacity == 0.0
    assert panel.jog_queue_bar.progress == 0.0
    assert panel.jog_queue_length_label.text == ""
    panel.cancel_scheduled_events()


def test_manual_jog_buttons_map_x_to_vertical_and_z_to_horizontal(tmp_path: Path):
    panel = _manual_panel(tmp_path)

    x_plus = _find_button(panel, "X+")
    z_plus = _find_button(panel, "Z+")
    assert x_plus is not None
    assert z_plus is not None

    x_plus.dispatch("on_release")
    assert panel.queued_jog_x_mm == pytest.approx(0.1)
    assert panel.queued_jog_z_mm == pytest.approx(0.0)

    panel.cancel_scheduled_events()
    panel._clear_queued_jog()

    z_plus.dispatch("on_release")
    assert panel.queued_jog_x_mm == pytest.approx(0.0)
    assert panel.queued_jog_z_mm == pytest.approx(0.1)

    panel.cancel_scheduled_events()


def test_manual_jog_center_button_cancels_queued_jog_not_motion(tmp_path: Path):
    panel = _manual_panel(tmp_path)

    assert _find_button(panel, "STOP") is None
    cancel = _find_button(panel, "CANCEL")
    assert cancel is not None

    panel._jog(x_sign=1.0)
    assert panel.queued_jog_event is not None
    assert panel.queued_jog_x_mm == pytest.approx(0.1)

    cancel.dispatch("on_release")

    assert panel.queued_jog_event is None
    assert panel.queued_jog_x_mm == pytest.approx(0.0)
    assert panel.queued_jog_z_mm == pytest.approx(0.0)
    panel.cancel_scheduled_events()


def test_custom_jog_distance_selects_only_custom_button(tmp_path: Path):
    panel = _manual_panel(tmp_path)
    fixed_buttons = panel.jog_increment_buttons[:-1]

    assert any(button.state == "down" for button in fixed_buttons)

    panel._custom_increment_changed(0.25)

    assert panel.use_custom_increment is True
    assert panel.custom_increment_mm == pytest.approx(0.25)
    assert panel.custom_increment_button.state == "down"
    assert all(button.state == "normal" for button in fixed_buttons)
    panel.cancel_scheduled_events()


def test_manual_dro_axis_labels_zero_work_axes_independently(tmp_path: Path):
    panel = _manual_panel(tmp_path)
    panel.service.set_work_position(x_mm=12.5, z_mm=-4.0)

    panel._zero_dro_axis("X")

    assert panel.service.state.work_x_mm == pytest.approx(0.0)
    assert panel.service.state.work_z_mm == pytest.approx(-4.0)
    assert panel.service.state.status_message == "X DRO zeroed"

    panel._zero_dro_axis("Z")

    assert panel.service.state.work_x_mm == pytest.approx(0.0)
    assert panel.service.state.work_z_mm == pytest.approx(0.0)
    assert panel.service.state.status_message == "Z DRO zeroed"
    panel.cancel_scheduled_events()


def test_command_gate_panel_tracks_single_step_pending_command(tmp_path: Path):
    panel = _manual_panel(tmp_path)

    assert panel.command_auto_button is not None
    assert panel.command_single_step_button is not None
    assert panel.command_go_button is not None
    assert panel.command_cancel_button is not None
    assert panel.command_current_label is not None
    assert panel.command_next_label is not None

    panel.command_single_step_button.state = "down"
    panel._set_command_mode(panel.command_single_step_button, "single_step")
    assert panel.service.command_status.mode == "single_step"

    assert panel.service.jog_delta(x_mm=0.1, mode="rapid")
    panel.refresh(panel.service.state)

    assert panel.command_go_button.disabled is False
    assert panel.command_cancel_button.disabled is False
    assert panel.command_auto_button.disabled is True
    assert "Awaiting Go" in panel.command_current_label.text

    panel._cancel_pending_command()

    assert panel.command_go_button.disabled is True
    assert panel.command_cancel_button.disabled is True
    assert "Cancelled" in panel.command_current_label.text
    panel.cancel_scheduled_events()


def test_program_waits_for_single_step_go_before_advancing(tmp_path: Path):
    config = MachineConfig(sim_motion_time_s=0.01)
    panel = _manual_panel(tmp_path, config=config)
    assert panel.program_panel is not None
    program = panel.program_panel
    assert panel.service.set_command_mode("single_step")

    program._start_actions_from_text(
        "G91 G1 X0.1 F100\nG1 Z-0.2 F100\n",
        label="Test",
        highlight_editor=False,
    )

    assert program.running
    assert program.waiting_for_approval
    assert program.execution_index == 0
    assert panel.service.command_status.awaiting_approval
    assert panel.service.state.x_mm == 0.0
    assert "Line 2" in panel.service.command_status.next_label

    assert panel.service.approve_pending_command()
    panel.service.backend.wait_idle(timeout_ms=500)
    panel.service.poll()
    panel.refresh(panel.service.state)

    assert program.running
    assert program.execution_index == 1
    assert program.waiting_for_approval
    assert panel.service.command_status.awaiting_approval
    assert panel.service.state.x_mm == pytest.approx(0.1)
    panel.cancel_scheduled_events()


def test_program_stops_when_single_step_command_is_cancelled(tmp_path: Path):
    panel = _manual_panel(tmp_path)
    assert panel.program_panel is not None
    program = panel.program_panel
    assert panel.service.set_command_mode("single_step")

    program._start_actions_from_text("G91 G1 X0.1 F100\n", label="Test", highlight_editor=False)
    assert program.waiting_for_approval

    assert panel.service.cancel_pending_command()
    panel.refresh(panel.service.state)

    assert not program.running
    assert "command cancelled" in program.program_status.text.lower()
    panel.cancel_scheduled_events()


def test_manual_toolchanger_set_current_flashes_then_syncs_green(tmp_path: Path):
    panel = _manual_panel(tmp_path)
    p3 = panel.tool_position_buttons[3]

    panel._manual_select_position(p3, 3)
    assert p3.background_color == list(app_module.AMBER)

    panel._manual_set_current_station()

    assert panel.service.state.turret_station == 3
    assert panel._tool_button_flash_station == 3
    assert p3.background_color == list(app_module.AMBER)

    while panel._manual_tool_button_flash_tick(0):
        pass

    assert panel.selected_tool_position is None
    assert panel.tool_position_buttons[3].background_color == list(app_module.GREEN)
    panel.cancel_scheduled_events()


def test_manual_toolchanger_change_uses_station_assignment_and_finishes_green(tmp_path: Path):
    config = MachineConfig(sim_tool_change_time_s=0.01)
    panel = _manual_panel(tmp_path, config=config)
    panel.service.upsert_tool(ToolRecord(tool_number=4, x_offset_mm=-1.0, z_offset_mm=2.5))
    assert panel.service.assign_tool_station(4, 2)
    assert panel.service.set_turret_station(1)
    panel.refresh(panel.service.state)

    p2 = panel.tool_position_buttons[2]
    panel._manual_select_position(p2, 2)
    assert p2.background_color == list(app_module.AMBER)

    panel._manual_change_tool()

    assert panel.service.state.active_tool == 4
    assert panel.service.state.turret_station == 2
    assert panel.service.state.busy
    assert panel._tool_button_flash_station == 2

    panel._manual_tool_button_flash_tick(0)
    assert panel.tool_position_buttons[2].background_color == list(app_module.BLUE)

    panel.service.backend.wait_idle(timeout_ms=500)
    panel.service.poll()

    assert panel._manual_tool_button_flash_tick(0) is False
    assert panel.selected_tool_position is None
    assert panel.tool_position_buttons[2].background_color == list(app_module.GREEN)
    panel.cancel_scheduled_events()


def test_manual_toolchanger_change_preserves_program_pending_tool(tmp_path: Path):
    config = MachineConfig(sim_tool_change_time_s=0.01)
    panel = _manual_panel(tmp_path, config=config)
    panel.service.upsert_tool(ToolRecord(tool_number=4))
    assert panel.service.assign_tool_station(4, 2)
    assert panel.service.set_turret_station(1)

    pending = ToolChangeAction(line_number=5, tool_number=9)
    assert not panel.service.execute_action(pending)
    assert panel.service.state.pending_tool == 9

    panel._manual_select_position(panel.tool_position_buttons[2], 2)
    panel._manual_change_tool()

    assert panel.service.state.active_tool == 4
    assert panel.service.state.turret_station == 2
    assert panel.service.state.pending_tool == 9
    assert panel.service.state.pending_turret_station is None
    panel.cancel_scheduled_events()
