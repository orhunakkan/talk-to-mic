"""Types text into the focused app: put it on the clipboard, press Ctrl+V, then put
the user's previous clipboard contents back. Also reads the selection the same way, with Ctrl+C."""

from __future__ import annotations

import logging
import threading
import time

import pywintypes
import win32clipboard as wc

from . import win32

log = logging.getLogger(__name__)

# Formats backed by GDI or owner-drawn handles, not memory blocks; they can't be copied
# as bytes. Windows re-synthesizes CF_BITMAP and friends from CF_DIB when restored.
_HANDLE_FORMATS = {
    wc.CF_BITMAP,
    wc.CF_METAFILEPICT,
    wc.CF_PALETTE,
    wc.CF_ENHMETAFILE,
    wc.CF_OWNERDISPLAY,
    wc.CF_DSPBITMAP,
    wc.CF_DSPMETAFILEPICT,
    wc.CF_DSPENHMETAFILE,
}

# Keep our temporary clipboard text out of Win+V history and clipboard sync.
_EXCLUDE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory")

_clipboard_lock = threading.Lock()

# Ctrl+C interrupts the running command in these instead of copying, so never send it there.
TERMINALS = {
    "windowsterminal.exe", "wt.exe", "openconsole.exe", "conhost.exe", "cmd.exe", "powershell.exe",
    "pwsh.exe", "mintty.exe", "alacritty.exe", "wezterm-gui.exe", "kitty.exe", "putty.exe",
}


def _open_clipboard(retries: int = 20) -> None:
    for _ in range(retries):
        try:
            wc.OpenClipboard()
            return
        except pywintypes.error:
            time.sleep(0.02)  # another app is holding it
    wc.OpenClipboard()


def save_clipboard() -> dict[int, object]:
    saved: dict[int, object] = {}
    _open_clipboard()
    try:
        fmt = wc.EnumClipboardFormats(0)
        while fmt:
            if fmt not in _HANDLE_FORMATS:
                try:
                    saved[fmt] = wc.GetClipboardData(fmt)
                except (pywintypes.error, TypeError):
                    pass
            fmt = wc.EnumClipboardFormats(fmt)
    finally:
        wc.CloseClipboard()
    return saved


def restore_clipboard(saved: dict[int, object]) -> None:
    _open_clipboard()
    try:
        wc.EmptyClipboard()
        for fmt, data in saved.items():
            try:
                wc.SetClipboardData(fmt, data)
            except (pywintypes.error, TypeError):
                log.debug("Could not restore clipboard format %s", fmt)
    finally:
        wc.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    _open_clipboard()
    try:
        wc.EmptyClipboard()
        wc.SetClipboardData(wc.CF_UNICODETEXT, text)
        wc.SetClipboardData(wc.RegisterClipboardFormat(_EXCLUDE_FORMATS[0]), b"\0\0\0\0")
        wc.SetClipboardData(wc.RegisterClipboardFormat(_EXCLUDE_FORMATS[1]), b"\0\0\0\0")
    finally:
        wc.CloseClipboard()


def paste_text(text: str, restore: bool = True, restore_delay_s: float = 0.5) -> bool:
    # Several threads paste or copy; each must finish its save -> use -> restore cycle alone.
    with _clipboard_lock:
        return _paste_text(text, restore, restore_delay_s)


def _paste_text(text: str, restore: bool, restore_delay_s: float) -> bool:
    saved = None
    if restore:
        try:
            saved = save_clipboard()
        except pywintypes.error:
            log.warning("Could not read the clipboard; it will not be restored", exc_info=True)
    set_clipboard_text(text)
    win32.wait_keys_released()  # a still-held Shift would turn this into Ctrl+Shift+V
    ok = win32.send_ctrl_v()
    if not ok:
        # Leave the text on the clipboard so the user can paste it by hand.
        log.warning("Windows blocked the paste keystroke (is the focused app running as administrator?)")
    if saved is not None and ok:
        # The target app reads the clipboard asynchronously after receiving Ctrl+V.
        time.sleep(restore_delay_s)
        try:
            restore_clipboard(saved)
        except pywintypes.error:
            log.warning("Could not restore the previous clipboard contents", exc_info=True)
    return ok


def copy_selection(process_name: str = "", timeout_s: float = 1.0) -> str:
    """The focused app's selected text ("" if none), read with Ctrl+C; the clipboard is put back."""
    if process_name.lower() in TERMINALS:
        log.info("Not reading the selection in %s (Ctrl+C would interrupt it)", process_name)
        return ""
    with _clipboard_lock:
        return _copy_selection(timeout_s)


def _copy_selection(timeout_s: float) -> str:
    saved = None
    try:
        saved = save_clipboard()
    except pywintypes.error:
        log.warning("Could not read the clipboard; it will not be restored", exc_info=True)
    before = wc.GetClipboardSequenceNumber()
    win32.wait_keys_released()
    if not win32.send_ctrl_c():
        log.warning("Windows blocked the copy keystroke (is the focused app running as administrator?)")
        return ""
    deadline = time.monotonic() + timeout_s
    while wc.GetClipboardSequenceNumber() == before:
        if time.monotonic() >= deadline:
            # Nothing selected, or an unusually slow app: if the copy still lands, undo it.
            time.sleep(0.3)
            if wc.GetClipboardSequenceNumber() != before and saved is not None:
                try:
                    restore_clipboard(saved)
                except pywintypes.error:
                    log.warning("Could not restore the previous clipboard contents", exc_info=True)
            return ""
        time.sleep(0.01)
    text = ""
    try:
        _open_clipboard()
        try:
            if wc.IsClipboardFormatAvailable(wc.CF_UNICODETEXT):
                text = wc.GetClipboardData(wc.CF_UNICODETEXT)
        finally:
            wc.CloseClipboard()
    except pywintypes.error:
        log.warning("Could not read the copied selection", exc_info=True)
    if saved is not None:
        try:
            restore_clipboard(saved)
        except pywintypes.error:
            log.warning("Could not restore the previous clipboard contents", exc_info=True)
    return text if text.strip() else ""
