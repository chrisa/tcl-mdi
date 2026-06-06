from __future__ import annotations

from tcl_lathe_hmi.ui.app import action_button
from tcl_lathe_hmi.ui.keypad import NumberEntryButton


def test_action_buttons_force_release_for_touchscreens():
    button = action_button("Run", (0.2, 0.4, 0.6, 1))

    assert button.always_release is True


def test_number_entry_buttons_force_release_for_touchscreens():
    button = NumberEntryButton(text="1.0")

    assert button.always_release is True
