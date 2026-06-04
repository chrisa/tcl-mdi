from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from kivy.clock import Clock


DEFAULT_RELEASE_DEBOUNCE_S = 0.08


def bind_release(
    widget,
    callback: Callable[..., object],
    *,
    debounce_seconds: float = DEFAULT_RELEASE_DEBOUNCE_S,
) -> None:
    widget.bind(on_release=debounced(callback, seconds=debounce_seconds))


def debounced(callback: Callable[..., object], *, seconds: float) -> Callable[..., object]:
    last_release_at = -999.0

    @wraps(callback)
    def wrapped(*args, **kwargs):
        nonlocal last_release_at
        now = Clock.get_time()
        if now - last_release_at < seconds:
            return None
        last_release_at = now
        return callback(*args, **kwargs)

    return wrapped
