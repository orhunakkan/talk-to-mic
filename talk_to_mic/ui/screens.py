"""Placing our pop-up windows on the monitor the user is working on."""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QCursor, QGuiApplication, QScreen

from .. import win32


def target_screen(hwnd: int) -> QScreen:
    """The screen showing most of hwnd (falls back to the screen under the mouse)."""
    rect = win32.window_rect(hwnd)
    if rect:
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        for screen in QGuiApplication.screens():
            # Qt keeps each screen's origin in physical pixels and scales its size by the DPI ratio.
            g, dpr = screen.geometry(), screen.devicePixelRatio()
            if QRect(g.x(), g.y(), round(g.width() * dpr), round(g.height() * dpr)).contains(cx, cy):
                return screen
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def bottom_center(available: tuple[int, int, int, int], size: tuple[int, int], margin: int) -> tuple[int, int]:
    """Top-left corner that centres a window of size horizontally, margin px above the bottom edge."""
    x, y, w, h = available
    return x + (w - size[0]) // 2, y + h - size[1] - margin


def available_rect(screen: QScreen) -> tuple[int, int, int, int]:
    a = screen.availableGeometry()
    return a.x(), a.y(), a.width(), a.height()
