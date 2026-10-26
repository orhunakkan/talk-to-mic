"""The Windows keyboard hook (WH_KEYBOARD_LL) behind hotkey.KeyboardHook.

It runs on its own thread with a message loop and must return from each callback quickly,
so it only feeds the detectors in hotkey.py and forwards their actions to a callback.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable

from . import win32
from .hotkey import Action, HoldDetector

log = logging.getLogger(__name__)

# --- Win32 hook --------------------------------------------------------------

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_QUIT = 0x12
LLKHF_INJECTED = 0x10


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", win32.ULONG_PTR),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(win32.LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32 = win32.user32
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.CallNextHookEx.restype = win32.LRESULT
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
win32.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
win32.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class KeyboardHook:
    def __init__(self, detectors: HoldDetector | list, on_action: Callable[[Action], None]):
        # Every detector sees every event; replace the list (not its items) to reconfigure.
        self.detectors = detectors if isinstance(detectors, list) else [detectors]
        self.on_action = on_action
        self._proc = HOOKPROC(self._callback)  # keep a reference: Windows calls into it
        self._hook = None
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.error: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="keyboard-hook", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self.error:
            raise RuntimeError(self.error)

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(2)

    def _run(self) -> None:
        self._thread_id = win32.kernel32.GetCurrentThreadId()
        self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, win32.kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            self.error = f"SetWindowsHookExW failed (error {ctypes.get_last_error()})"
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            _user32.UnhookWindowsHookEx(self._hook)

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == 0 and w_param in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            injected = bool(info.flags & LLKHF_INJECTED)
            try:
                is_down, now, suppress = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN), time.monotonic(), False
                for detector in self.detectors:
                    decision = detector.feed(info.vkCode, is_down, injected, now)
                    if decision.release_alt:
                        win32.release_alt_without_menu(info.vkCode)
                    if decision.action:
                        self.on_action(decision.action)
                    suppress = suppress or decision.suppress
                if suppress:
                    return 1
            except Exception:  # never let an exception escape into Windows
                log.exception("Keyboard hook callback failed")
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)
