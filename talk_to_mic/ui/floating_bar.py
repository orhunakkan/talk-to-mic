"""The pill near the bottom of the screen that shows the mode and a level meter while listening,
then a spinner while the recording is processed.

It can never take focus or receive clicks, so the paste always lands in the user's window.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Callable

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import win32
from ..hotkey import Intent
from ..tray import Status
from .screens import available_rect, bottom_center, target_screen

MARGIN = 28  # px above the taskbar
BARS = 18

INTENT_COLORS = {
    Intent.DICTATE: QColor(229, 57, 53),
    Intent.ASK: QColor(139, 92, 246),
    Intent.TRANSLATE: QColor(20, 184, 166),
}
PROCESSING_COLOR = QColor(245, 166, 35)


def level_to_height(rms: float) -> float:
    """Microphone RMS -> 0..1 on a log scale (-55 dBFS is silent, -10 dBFS is full)."""
    if rms <= 0:
        return 0.0
    return min(1.0, max(0.0, (20 * math.log10(rms) + 55) / 45))


def intent_label(intent: Intent, target_language: str) -> str:
    if intent is Intent.ASK:
        return "Ask Anything"
    if intent is Intent.TRANSLATE:
        return f"Translate → {target_language}"
    return "Listening"


def busy_label(intent: Intent) -> str:
    return {Intent.ASK: "Thinking…", Intent.TRANSLATE: "Translating…"}.get(intent, "Transcribing…")


class FloatingBar(QWidget):
    HEIGHT = 42

    def __init__(self, level: Callable[[], float], target_language: Callable[[], str]):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._level = level
        self._target_language = target_language
        self._levels: deque[float] = deque([0.0] * BARS, maxlen=BARS)
        self._busy = False
        self._phase = 0
        self._color = INTENT_COLORS[Intent.DICTATE]
        self._label = ""
        self._font = QFont(self.font())
        self._font.setPointSizeF(10)
        self._font.setWeight(QFont.Weight.DemiBold)
        self._metrics = QFontMetrics(self._font)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        win32.make_overlay(int(self.winId()), click_through=True)

    def set_state(self, status: Status, intent: Intent, hwnd: int) -> None:
        if status is Status.RECORDING:
            busy, label, color = False, intent_label(intent, self._target_language()), INTENT_COLORS[intent]
        elif status is Status.PROCESSING:
            busy, label, color = True, busy_label(intent), PROCESSING_COLOR
        else:
            self._timer.stop()
            self.hide()
            return
        if not busy and (self._busy or not self.isVisible()):
            self._levels.extend([0.0] * BARS)
        self._busy, self._label, self._color = busy, label, color
        width = 96 + self._metrics.horizontalAdvance(label) + (BARS * 5 if not busy else 24)
        self.resize(width, self.HEIGHT)
        if not self.isVisible():
            screen = target_screen(hwnd)
            self.move(*bottom_center(available_rect(screen), (width, self.HEIGHT), MARGIN))
            self.show()
        self._timer.start()
        self.update()

    def _tick(self) -> None:
        if self._busy:
            self._phase = (self._phase + 1) % 360
        else:
            self._levels.append(level_to_height(self._level()))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(24, 24, 27, 235))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        h = r.height()
        dot = 10
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(18, (h - dot) / 2 + 1, dot, dot))

        p.setFont(self._font)
        p.setPen(QColor(245, 245, 247))
        text_x = 18 + dot + 10
        text_w = self._metrics.horizontalAdvance(self._label) + 8
        p.drawText(QRectF(text_x, 0, text_w, h + 2), Qt.AlignmentFlag.AlignVCenter, self._label)

        right = r.right() - 18
        if self._busy:
            size = 16
            arc = QRectF(right - size, (h - size) / 2 + 1, size, size)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 50), 2.5))
            p.drawEllipse(arc)
            pen = QPen(self._color, 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(arc, -self._phase * 16 * 8, 100 * 16)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(245, 245, 247))
            max_h, x = h - 18, right - BARS * 5 + 2
            for value in self._levels:
                bar_h = max(3.0, value * max_h)
                p.drawRoundedRect(QRectF(x, (h - bar_h) / 2 + 1, 3, bar_h), 1.5, 1.5)
                x += 5
        p.end()
