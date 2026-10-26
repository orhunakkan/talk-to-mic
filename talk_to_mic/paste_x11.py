"""The Linux counterpart of paste.py: types text into the focused app by putting it on the
clipboard, pressing Ctrl+V, then putting the user's previous clipboard contents back. Also reads
the selection the same way, with Ctrl+C.

On X11 the clipboard's owner has to hand its contents to every app that pastes, so the clipboard
is Qt's, on the main thread; the worker threads pass their clipboard work over to it.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from typing import Callable

from PySide6.QtCore import QByteArray, QCoreApplication, QMimeData, QObject, QThread, Signal
from PySide6.QtGui import QGuiApplication

from . import x11

log = logging.getLogger(__name__)

# Asks clipboard managers (Klipper, and others that follow it) to keep our temporary text out of history.
_SECRET_HINT = ("x-kde-passwordManagerHint", b"secret")

_clipboard_lock = threading.Lock()

# These paste with Ctrl+Shift+V and copy with Ctrl+Shift+C; Ctrl+C interrupts the running command.
TERMINALS = {
    "gnome-terminal-server", "gnome-terminal", "mate-terminal", "xfce4-terminal", "konsole", "kitty",
    "alacritty", "wezterm-gui", "tilix", "terminator", "guake", "tilda", "xterm", "uxterm", "urxvt",
    "rxvt", "lxterminal", "qterminal", "sakura", "terminology", "st", "foot", "ghostty", "ptyxis",
    "kgx", "cool-retro-term", "warp", "x-terminal-emulator",
}


class _MainThread(QObject):
    """Runs functions on the Qt main thread and counts clipboard changes."""

    _call = Signal(object)

    def __init__(self):
        super().__init__()
        self.changes = 0
        self._call.connect(self._run)
        self._watching = False

    def run(self, fn: Callable, timeout_s: float = 5.0):
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() == app.thread():
            return fn()
        future: Future = Future()
        self._call.emit((fn, future))
        return future.result(timeout_s)

    def _run(self, item) -> None:
        fn, future = item
        try:
            future.set_result(fn())
        except Exception as exc:
            future.set_exception(exc)

    def watch(self) -> None:
        if not self._watching:
            self._watching = True
            QGuiApplication.clipboard().dataChanged.connect(self._changed)

    def _changed(self) -> None:
        self.changes += 1


_main: _MainThread | None = None


def _on_main(fn: Callable):
    global _main
    if _main is None:
        _main = _MainThread()
        app = QCoreApplication.instance()
        if app is not None:
            _main.moveToThread(app.thread())
        _main.run(_main.watch)
    return _main.run(fn)


def save_clipboard() -> dict[str, bytes]:
    def save() -> dict[str, bytes]:
        data = QGuiApplication.clipboard().mimeData()
        if data is None:
            return {}
        # Only MIME types: the rest are X protocol targets (TARGETS, MULTIPLE...), and asking for
        # MULTIPLE without parameters stalls until Qt's 5 s timeout.
        saved = {fmt: bytes(data.data(fmt).data()) for fmt in data.formats() if "/" in fmt}
        return {fmt: value for fmt, value in saved.items() if value}

    return _on_main(save)


def restore_clipboard(saved: dict[str, bytes]) -> None:
    def restore() -> None:
        if not saved:
            QGuiApplication.clipboard().clear()
            return
        data = QMimeData()
        for fmt, value in saved.items():
            data.setData(fmt, QByteArray(value))
        QGuiApplication.clipboard().setMimeData(data)

    _on_main(restore)


def set_clipboard_text(text: str) -> None:
    def put() -> None:
        data = QMimeData()
        data.setText(text)
        data.setData(_SECRET_HINT[0], QByteArray(_SECRET_HINT[1]))
        QGuiApplication.clipboard().setMimeData(data)

    _on_main(put)


def _clipboard_changes() -> int:
    return _main.changes if _main else 0


def in_terminal(process_name: str) -> bool:
    return process_name.lower() in TERMINALS


def paste_text(text: str, restore: bool = True, restore_delay_s: float = 0.5) -> bool:
    # Several threads paste or copy; each must finish its save -> use -> restore cycle alone.
    with _clipboard_lock:
        return _paste_text(text, restore, restore_delay_s)


def _paste_text(text: str, restore: bool, restore_delay_s: float) -> bool:
    saved = None
    if restore:
        try:
            saved = save_clipboard()
        except Exception:
            log.warning("Could not read the clipboard; it will not be restored", exc_info=True)
    set_clipboard_text(text)
    x11.wait_keys_released()  # a still-held Shift would turn this into Ctrl+Shift+V
    terminal = in_terminal(x11.foreground_app().process_name)
    ok = x11.send_ctrl_shift_v() if terminal else x11.send_ctrl_v()
    if not ok:
        # Leave the text on the clipboard so the user can paste it by hand.
        log.warning("Could not send the paste keystroke")
    if saved is not None and ok:
        # The target app asks for the clipboard contents some time after receiving Ctrl+V.
        time.sleep(restore_delay_s)
        try:
            restore_clipboard(saved)
        except Exception:
            log.warning("Could not restore the previous clipboard contents", exc_info=True)
    return ok


def copy_selection(process_name: str = "", timeout_s: float = 1.0) -> str:
    """The focused app's selected text ("" if none), read with Ctrl+C; the clipboard is put back."""
    with _clipboard_lock:
        return _copy_selection(in_terminal(process_name), timeout_s)


def _copy_selection(terminal: bool, timeout_s: float) -> str:
    saved = None
    try:
        saved = save_clipboard()
    except Exception:
        log.warning("Could not read the clipboard; it will not be restored", exc_info=True)
    before = _clipboard_changes()
    x11.wait_keys_released()
    if not (x11.send_ctrl_shift_c() if terminal else x11.send_ctrl_c()):
        log.warning("Could not send the copy keystroke")
        return ""
    deadline = time.monotonic() + timeout_s
    while _clipboard_changes() == before:
        if time.monotonic() >= deadline:
            # Nothing selected, or an unusually slow app: if the copy still lands, undo it.
            time.sleep(0.3)
            if _clipboard_changes() != before and saved is not None:
                try:
                    restore_clipboard(saved)
                except Exception:
                    log.warning("Could not restore the previous clipboard contents", exc_info=True)
            return ""
        time.sleep(0.01)
    text = ""
    try:
        text = _on_main(lambda: QGuiApplication.clipboard().text())
    except Exception:
        log.warning("Could not read the copied selection", exc_info=True)
    if saved is not None:
        try:
            restore_clipboard(saved)
        except Exception:
            log.warning("Could not restore the previous clipboard contents", exc_info=True)
    return text if text.strip() else ""
