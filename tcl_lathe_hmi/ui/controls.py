from __future__ import annotations

from collections.abc import Callable

from kivy.graphics import Color, Rectangle
from kivy.uix.button import Button
from kivy.uix.codeinput import CodeInput
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

from tcl_lathe_hmi.gcode import LinuxCncGCodeLexer, TclGCodeStyle
from tcl_lathe_hmi.machine import MachineState
from tcl_lathe_hmi.ui.keypad import NumberEntryButton
from tcl_lathe_hmi.ui.widgets import configure_touch_release


BG = (0.07, 0.08, 0.09, 1)
PANEL = (0.12, 0.13, 0.14, 1)
PANEL_ALT = (0.16, 0.16, 0.17, 1)
TEXT = (0.93, 0.94, 0.92, 1)
MUTED = (0.62, 0.66, 0.68, 1)
GREEN = (0.18, 0.56, 0.34, 1)
BLUE = (0.16, 0.36, 0.62, 1)
AMBER = (0.78, 0.52, 0.14, 1)
RED = (0.64, 0.18, 0.18, 1)
BUTTON = (0.24, 0.25, 0.27, 1)
THREAD = (0.20, 0.70, 0.86, 1)
THREAD_TOOLPATH = (0.95, 0.42, 0.30, 0.72)


def paint(widget, color) -> None:
    with widget.canvas.before:
        Color(*color)
        rect = Rectangle(pos=widget.pos, size=widget.size)

    def update_rect(instance, *_args):
        rect.pos = instance.pos
        rect.size = instance.size

    widget.bind(pos=update_rect, size=update_rect)


def action_button(text: str, color, *, width: int | None = None) -> Button:
    kwargs = {}
    if width is not None:
        kwargs = {"size_hint_x": None, "width": width}
    button = Button(
        text=text,
        font_size=22,
        bold=True,
        color=TEXT,
        background_normal="",
        background_color=color,
        **kwargs,
    )
    configure_touch_release(button)
    return button


def jog_button(text: str) -> Button:
    return action_button(text, BLUE)


def toggle_button(text: str, *, group: str) -> ToggleButton:
    button = ToggleButton(
        text=text,
        group=group,
        allow_no_selection=False,
        font_size=20,
        bold=True,
        color=TEXT,
        background_normal="",
        background_down="",
        background_color=BUTTON,
    )
    configure_touch_release(button, recover_stuck=False)
    return button


def numeric_input(
    text: str,
    *,
    width: int | None = None,
    integer: bool = False,
    title_text: str = "Number",
    on_value: Callable[[float | int], None] | None = None,
) -> NumberEntryButton:
    kwargs = {}
    if width is not None:
        kwargs = {"size_hint_x": None, "width": width}
    return NumberEntryButton(
        text=text,
        integer=integer,
        title_text=title_text,
        on_value=on_value,
        font_size=28,
        **kwargs,
    )


def field_input(
    text: str,
    *,
    integer: bool = False,
    title_text: str = "Number",
    on_value: Callable[[float | int], None] | None = None,
) -> NumberEntryButton:
    return NumberEntryButton(
        text=text,
        integer=integer,
        title_text=title_text,
        on_value=on_value,
        font_size=20,
    )


def text_field(text: str) -> TextInput:
    return TextInput(
        text=text,
        multiline=False,
        font_size=20,
        foreground_color=TEXT,
        background_color=(0.06, 0.06, 0.06, 1),
        cursor_color=TEXT,
        padding=(8, 8, 8, 8),
    )


def gcode_input(
    *,
    text: str,
    multiline: bool,
    font_size: int,
    padding: tuple[int, int, int, int] | None = None,
    **kwargs,
) -> CodeInput:
    if padding is None:
        padding = (8, 8, 8, 8)
    return CodeInput(
        text=text,
        lexer=LinuxCncGCodeLexer(),
        style=TclGCodeStyle,
        multiline=multiline,
        do_wrap=False,
        font_size=font_size,
        foreground_color=TEXT,
        background_color=(0.05, 0.05, 0.05, 1),
        cursor_color=TEXT,
        padding=padding,
        **kwargs,
    )


def status_text(text: str) -> Label:
    label = Label(
        text=text,
        color=MUTED,
        font_size=18,
        halign="left",
        valign="middle",
        size_hint_y=None,
        height=42,
    )
    label.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
    return label


def axis_label(text: str, *, width: int) -> Label:
    label = Label(text=text, color=TEXT, font_size=42, bold=True, size_hint_x=None, width=width)
    paint(label, BLUE if text in {"X", "Z"} else PANEL_ALT)
    return label


def section_label(text: str) -> Label:
    return Label(text=text, color=TEXT, font_size=24, bold=True, size_hint_y=None, height=38)


def status_color(state: MachineState):
    if state.error:
        return RED
    if not state.connected:
        return MUTED
    if state.busy:
        return AMBER
    return GREEN


def backend_label(backend_name: str) -> str:
    return "FRED USB" if backend_name == "fred" else "Simulator"
