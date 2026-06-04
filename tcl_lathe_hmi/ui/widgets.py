from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from kivy.clock import Clock
from kivy.uix.button import Button
from kivy.uix.togglebutton import ToggleButton


class DebouncedReleaseMixin:
    debounce_seconds = 0.08

    def bind(self, **kwargs):
        callback = kwargs.get("on_release")
        if callback is not None:
            kwargs = dict(kwargs)
            kwargs["on_release"] = self._debounced_release_callback(callback)
        return super().bind(**kwargs)

    def _debounced_release_callback(self, callback: Callable):
        last_release_at = -999.0

        @wraps(callback)
        def wrapped(*args, **kwargs):
            nonlocal last_release_at
            now = Clock.get_time()
            if now - last_release_at < self.debounce_seconds:
                return None
            last_release_at = now
            return callback(*args, **kwargs)

        return wrapped


class DebouncedButton(DebouncedReleaseMixin, Button):
    pass


class DebouncedToggleButton(DebouncedReleaseMixin, ToggleButton):
    pass
