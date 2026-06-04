from __future__ import annotations

from collections.abc import Callable

from kivy.core.window import Window
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup

from tcl_lathe_hmi.ui.widgets import bind_release


TEXT = (0.93, 0.94, 0.92, 1)
MUTED = (0.62, 0.66, 0.68, 1)
BUTTON = (0.24, 0.25, 0.27, 1)
PANEL = (0.12, 0.13, 0.14, 1)
GREEN = (0.18, 0.56, 0.34, 1)
RED = (0.64, 0.18, 0.18, 1)
AMBER = (0.78, 0.52, 0.14, 1)


class NumberEntryButton(Button):
    """Touch-friendly numeric input that opens a modal keypad."""

    value = NumericProperty(0.0)
    integer = BooleanProperty(False)
    title_text = StringProperty("Number")

    def __init__(
        self,
        text: str = "0",
        *,
        integer: bool = False,
        title_text: str = "Number",
        on_value: Callable[[float | int], None] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.integer = integer
        self.title_text = title_text
        self._on_value = on_value
        self.background_normal = ""
        self.background_color = (0.06, 0.06, 0.06, 1)
        self.color = TEXT
        self.font_size = kwargs.get("font_size", 28)
        self.halign = "right"
        self.text = str(text)
        self.value = _coerce_value(self.text, self.integer, 0)
        bind_release(self, lambda *_: self.open_keypad())

    def set_value(self, value: float | int) -> None:
        self.value = int(value) if self.integer else float(value)
        self.text = _format_value(self.value, self.integer)
        if self._on_value is not None:
            self._on_value(int(self.value) if self.integer else float(self.value))

    def open_keypad(self) -> None:
        NumberEntryPopup(
            current_text=self.text,
            integer=self.integer,
            title_text=self.title_text,
            callback=self.set_value,
        ).open()


class NumberEntryPopup(Popup):
    def __init__(
        self,
        *,
        current_text: str,
        integer: bool,
        title_text: str,
        callback: Callable[[float | int], None],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.integer = integer
        self.callback = callback
        self.old_text = current_text or "0"
        self._replace_on_next_digit = True
        self.title = f"{title_text}  Old: {self.old_text}"
        self.size_hint = (0.74, 0.78)
        self.auto_dismiss = False

        layout = BoxLayout(orientation="vertical", spacing=8, padding=8)

        display_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=0.30)
        self.value_label = Label(
            text="",
            color=TEXT,
            font_size=46,
            bold=True,
            halign="right",
            valign="middle",
        )
        self.value_label.bind(
            size=lambda widget, *_: setattr(widget, "text_size", widget.size)
        )
        display_row.add_widget(self.value_label)
        display_row.add_widget(self._button("OLD", self.load_old_value, color=AMBER, width=110))
        layout.add_widget(display_row)

        for labels in (("7", "8", "9", "Back"), ("4", "5", "6", "1/2"), ("1", "2", "3", "+/-")):
            layout.add_widget(self._row(labels))

        bottom = BoxLayout(orientation="horizontal", spacing=8)
        bottom.add_widget(self._button("0", self.add_text))
        bottom.add_widget(self._button("00" if integer else ".", self.add_text if integer else self.dot_key))
        bottom.add_widget(self._button("Cancel", self.cancel, color=RED))
        bottom.add_widget(self._button("OK", self.confirm, color=GREEN))
        layout.add_widget(bottom)

        self.content = layout
        self.load_old_value()
        self.bind(on_open=lambda *_: Window.bind(on_key_down=self._on_keyboard_down))
        self.bind(on_dismiss=lambda *_: Window.unbind(on_key_down=self._on_keyboard_down))

    def _row(self, labels: tuple[str, str, str, str]) -> BoxLayout:
        row = BoxLayout(orientation="horizontal", spacing=8)
        for label in labels:
            handler = {
                "Back": self.delete_text,
                "1/2": self.halve_value,
                "+/-": self.sign_key,
            }.get(label, self.add_text)
            row.add_widget(self._button(label, handler))
        return row

    def _button(
        self,
        text: str,
        handler,
        *,
        color=BUTTON,
        width: int | None = None,
    ) -> Button:
        kwargs = {}
        if width is not None:
            kwargs = {"size_hint_x": None, "width": width}
        button = Button(
            text=text,
            font_size=28,
            bold=True,
            color=TEXT,
            background_normal="",
            background_color=color,
            **kwargs,
        )
        bind_release(button, lambda btn: handler(btn))
        return button

    def _on_keyboard_down(self, _window, keycode, _scancode, text, _modifiers):
        key = keycode if isinstance(keycode, str) else ""
        key_num = keycode if isinstance(keycode, int) else None
        if text in "0123456789":
            self._append_token(text)
        elif text == "." and not self.integer:
            self.dot_key()
        elif text == "-":
            self.sign_key()
        elif key in {"backspace", "delete"} or key_num in {8, 127}:
            self.delete_text()
        elif key in {"enter", "numpadenter"} or key_num in {13, 271}:
            self.confirm()
        elif key == "escape" or key_num == 27:
            self.cancel()
        return True

    def load_old_value(self, *_args) -> None:
        self.value_label.text = self.old_text
        self._replace_on_next_digit = True

    def add_text(self, button: Button) -> None:
        self._append_token(button.text)

    def dot_key(self, *_args) -> None:
        if self._replace_on_next_digit:
            self.value_label.text = "0"
            self._replace_on_next_digit = False
        if "." not in self.value_label.text:
            self.value_label.text += "."

    def sign_key(self, *_args) -> None:
        if self.value_label.text.startswith("-"):
            self.value_label.text = self.value_label.text[1:]
        else:
            self.value_label.text = "-" + self.value_label.text
        self._replace_on_next_digit = False

    def delete_text(self, *_args) -> None:
        self.value_label.text = self.value_label.text[:-1]
        self._replace_on_next_digit = False

    def halve_value(self, *_args) -> None:
        value = _coerce_value(self.old_text, self.integer, 0) / 2
        self.value_label.text = _format_value(int(value) if self.integer else value, self.integer)
        self._replace_on_next_digit = False

    def _append_token(self, token: str) -> None:
        if self._replace_on_next_digit:
            self.value_label.text = ""
            self._replace_on_next_digit = False
        self.value_label.text += token

    def confirm(self, *_args) -> None:
        try:
            value = _coerce_value(self.value_label.text, self.integer, None)
        except ValueError:
            return
        self.callback(value)
        self.dismiss()

    def cancel(self, *_args) -> None:
        self.dismiss()


def _coerce_value(text: str, integer: bool, default):
    stripped = str(text).strip()
    if stripped in {"", "-", ".", "-."}:
        if default is None:
            raise ValueError("empty number")
        return default
    return int(float(stripped)) if integer else float(stripped)


def _format_value(value: float | int, integer: bool) -> str:
    if integer:
        return str(int(value))
    return f"{float(value):g}"
