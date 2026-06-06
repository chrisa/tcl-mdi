from __future__ import annotations

from pathlib import Path

import pytest

from tcl_lathe_hmi.backends.sim import SimBackend
from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.machine.service import MachineService

pytest.importorskip("kivy")

from tcl_lathe_hmi.ui.app import ManualPanel, action_button
from tcl_lathe_hmi.ui.keypad import NumberEntryButton


def test_action_buttons_force_release_for_touchscreens():
    button = action_button("Run", (0.2, 0.4, 0.6, 1))

    assert button.always_release is True


def test_number_entry_buttons_force_release_for_touchscreens():
    button = NumberEntryButton(text="1.0")

    assert button.always_release is True


def test_default_jog_accumulate_delay_is_half_second():
    assert MachineConfig().jog_accumulate_delay_s == 0.5


def test_manual_panel_shows_accumulated_jog_indicator_after_second_tap(tmp_path: Path):
    config = MachineConfig()
    service = MachineService(
        SimBackend(config),
        config=config,
        settings_path=tmp_path / "machine_state.json",
    )
    service.connect()
    panel = ManualPanel(
        service=service,
        config=config,
        initial_backend="sim",
        on_backend_change=lambda _backend: None,
    )

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
