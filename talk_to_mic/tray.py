"""System tray icon whose colour shows what the app is doing.

Runs on Qt's main-thread event loop. set_status/notify/stop may be called from any thread:
they are forwarded to the main thread through queued signals.
"""

from __future__ import annotations

import os
import sys
from enum import Enum
from typing import Callable

from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .config import CONFIG_PATH, LOG_PATH


class Status(Enum):
    LOADING = ("Loading speech model…", (120, 120, 120))
    IDLE = ("Ready", (52, 120, 246))
    RECORDING = ("Listening…", (229, 57, 53))
    PROCESSING = ("Transcribing…", (245, 166, 35))

    def __init__(self, label: str, color: tuple[int, int, int]):
        self.label = label
        self.color = color


def _icon_image(color: tuple[int, int, int], size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64  # coordinates below are on a 64 px grid
    d.ellipse((2 * s, 2 * s, 62 * s, 62 * s), fill=color)
    white = (255, 255, 255)
    w = round(4 * s)
    d.rounded_rectangle((24 * s, 12 * s, 40 * s, 38 * s), radius=8 * s, fill=white)  # mic capsule
    d.arc((17 * s, 22 * s, 47 * s, 46 * s), start=0, end=180, fill=white, width=w)  # holder
    d.line((32 * s, 46 * s, 32 * s, 53 * s), fill=white, width=w)  # stand
    d.line((24 * s, 53 * s, 40 * s, 53 * s), fill=white, width=w)  # base
    return img


def _pixmap(img: Image.Image) -> QPixmap:
    data = img.tobytes("raw", "RGBA")
    return QPixmap.fromImage(QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888).copy())


def make_icon(color: tuple[int, int, int]) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(_pixmap(_icon_image(color, size)))
    return icon


def open_file(path) -> None:
    """Opens path in the app the system uses for its file type."""
    if sys.platform == "win32":
        os.startfile(path)
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class Tray(QObject):
    _status_changed = Signal(object)
    _notified = Signal(str)
    _stop_requested = Signal()

    def __init__(self, hotkey_name: str, cleanup_enabled: Callable[[], bool], toggle_cleanup: Callable[[], None],
                 on_quit: Callable[[], None], on_open: Callable[[], None] | None = None,
                 on_copy_last: Callable[[], None] | None = None):
        super().__init__()
        self.hotkey_name = hotkey_name
        self.status = Status.LOADING
        self._icons = {s: make_icon(s.color) for s in Status}
        self._cleanup_enabled = cleanup_enabled

        menu = QMenu()
        self._status_action = menu.addAction("")
        self._status_action.setEnabled(False)
        if on_open:
            menu.addAction("Open talk-to-mic…", on_open)
        if on_copy_last:
            menu.addAction("Copy last transcript", on_copy_last)
        self._cleanup_action = menu.addAction("Clean up with Claude")
        self._cleanup_action.setCheckable(True)
        self._cleanup_action.triggered.connect(lambda: toggle_cleanup())
        menu.addSeparator()
        menu.addAction("Open settings", lambda: open_file(CONFIG_PATH))
        menu.addAction("Open log", lambda: open_file(LOG_PATH))
        menu.addAction("Quit", on_quit)
        menu.aboutToShow.connect(self._refresh_menu)
        self._menu = menu  # QSystemTrayIcon does not take ownership

        self.icon = QSystemTrayIcon(self._icons[self.status])
        self.icon.setContextMenu(menu)
        self.icon.setToolTip(self._title())
        if on_open:
            self.icon.activated.connect(
                lambda reason: on_open() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
            )
        self._refresh_menu()

        self._status_changed.connect(self._apply_status)
        self._notified.connect(self._show_message)
        self._stop_requested.connect(self._stop)

    def _title(self) -> str:
        return f"talk-to-mic: {self.status.label}"

    def _refresh_menu(self) -> None:
        self._status_action.setText(f"{self.status.label}  (hold {self.hotkey_name})")
        self._cleanup_action.setChecked(bool(self._cleanup_enabled()))

    def set_status(self, status: Status) -> None:
        self._status_changed.emit(status)

    def _apply_status(self, status: Status) -> None:
        self.status = status
        self.icon.setIcon(self._icons[status])
        self.icon.setToolTip(self._title())
        self._refresh_menu()

    def notify(self, message: str) -> None:
        self._notified.emit(message)

    def _show_message(self, message: str) -> None:
        try:
            self.icon.showMessage("talk-to-mic", message, QSystemTrayIcon.MessageIcon.Information, 6000)
        except Exception:
            pass

    def run(self, setup: Callable[["Tray"], None]) -> None:
        self.icon.show()
        setup(self)
        QApplication.instance().exec()

    def stop(self) -> None:
        self._stop_requested.emit()

    def _stop(self) -> None:
        self.icon.hide()
        QApplication.instance().quit()
