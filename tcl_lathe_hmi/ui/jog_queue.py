from __future__ import annotations

from kivy.graphics import Color, Rectangle
from kivy.uix.widget import Widget

from tcl_lathe_hmi.ui.controls import BLUE


class JogQueueBar(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.progress = 0.0
        with self.canvas.before:
            Color(0.10, 0.11, 0.12, 1)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
            Color(*BLUE)
            self._fill_rect = Rectangle(pos=self.pos, size=(0, self.height))
        self.bind(pos=self._update_rects, size=self._update_rects)

    def set_progress(self, progress: float) -> None:
        self.progress = max(0.0, min(1.0, progress))
        self._update_rects()

    def _update_rects(self, *_args) -> None:
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size
        self._fill_rect.pos = self.pos
        self._fill_rect.size = (self.width * self.progress, self.height)
