from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from kivy.clock import Clock


DEFAULT_RELEASE_DEBOUNCE_S = 0.08
DEFAULT_STUCK_RELEASE_S = 0.45


def configure_touch_release(widget, *, recover_stuck: bool = True) -> None:
    """Make touch buttons tolerate slight finger drift before touch-up."""
    if hasattr(widget, "always_release"):
        widget.always_release = True
    if recover_stuck:
        widget._stuck_release_seconds = DEFAULT_STUCK_RELEASE_S


def bind_release(
    widget,
    callback: Callable[..., object],
    *,
    debounce_seconds: float = DEFAULT_RELEASE_DEBOUNCE_S,
) -> None:
    wrapped = debounced(callback, seconds=debounce_seconds)
    release_event = None
    press_sequence = 0
    fired_sequence = -1

    def fire_once(instance):
        nonlocal fired_sequence
        if fired_sequence == press_sequence:
            return None
        fired_sequence = press_sequence
        return wrapped(instance)

    def recover_release(instance, sequence):
        nonlocal release_event
        release_event = None
        if sequence != press_sequence or fired_sequence == sequence:
            return None
        if getattr(instance, "state", None) != "down":
            return None
        release = getattr(instance, "_do_release", None)
        if callable(release):
            release()
        elif hasattr(instance, "state"):
            instance.state = "normal"
        return fire_once(instance)

    def on_press(instance):
        nonlocal press_sequence, release_event
        press_sequence += 1
        timeout = getattr(instance, "_stuck_release_seconds", None)
        if timeout is None:
            return None
        if release_event is not None:
            release_event.cancel()
        sequence = press_sequence
        release_event = Clock.schedule_once(
            lambda _dt: recover_release(instance, sequence),
            timeout,
        )
        return None

    def on_release(instance):
        nonlocal release_event
        if release_event is not None:
            release_event.cancel()
            release_event = None
        return fire_once(instance)

    widget.bind(on_press=on_press, on_release=on_release)


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
