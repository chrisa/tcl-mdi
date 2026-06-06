from __future__ import annotations

from pathlib import Path

import pytest

from kivy.clock import Clock

from tcl_lathe_hmi.backends.sim import SimBackend
from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.machine.service import MachineService
from tcl_lathe_hmi.ui.widgets import bind_release

pytest.importorskip("kivy")

from tcl_lathe_hmi.ui.app import ManualPanel, action_button, toggle_button
from tcl_lathe_hmi.ui.keypad import NumberEntryButton


def _manual_panel(tmp_path: Path) -> ManualPanel:
    config = MachineConfig()
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
