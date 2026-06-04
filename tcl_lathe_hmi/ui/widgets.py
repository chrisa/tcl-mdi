from __future__ import annotations

from kivy.clock import Clock
from kivy.uix.button import Button
from kivy.uix.togglebutton import ToggleButton


class DebouncedButton(Button):
    debounce_seconds = 0.18

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_touch_down = -999.0

    def on_touch_down(self, touch):
        if self.disabled or not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        now = Clock.get_time()
        if now - self._last_touch_down < self.debounce_seconds:
            return True
        self._last_touch_down = now
        return super().on_touch_down(touch)


class DebouncedToggleButton(ToggleButton):
    debounce_seconds = 0.18

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_touch_down = -999.0

    def on_touch_down(self, touch):
        if self.disabled or not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        now = Clock.get_time()
        if now - self._last_touch_down < self.debounce_seconds:
            return True
        self._last_touch_down = now
        return super().on_touch_down(touch)
