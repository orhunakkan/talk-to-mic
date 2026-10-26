"""Thin ctypes wrappers for the Win32 calls the app needs."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_C = 0x43
VK_V = 0x56
# Unassigned virtual key; tapping it while Alt is held stops Windows from
# activating a window's menu bar when Alt is released.
VK_MENU_MASK = 0xE8

# Marks input we inject so our own keyboard hook can recognise it.
INJECTED_TAG = 0x7A4C4D21


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

EXTENDED_VKS = {0xA3, 0xA5, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28}


def _key(vk: int, up: bool) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk, dwFlags=flags, dwExtraInfo=INJECTED_TAG)))


def send_keys(events: list[tuple[int, bool]]) -> bool:
    """Inject (vk, is_up) key events in order. Returns False if Windows blocked them."""
    arr = (INPUT * len(events))(*(_key(vk, up) for vk, up in events))
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    return sent == len(events)


def send_ctrl_v() -> bool:
    return send_keys([(VK_CONTROL, False), (VK_V, False), (VK_V, True), (VK_CONTROL, True)])


def send_ctrl_c() -> bool:
    return send_keys([(VK_CONTROL, False), (VK_C, False), (VK_C, True), (VK_CONTROL, True)])


user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

MODIFIER_KEYS = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def wait_keys_released(vks=MODIFIER_KEYS, timeout_s: float = 1.0) -> bool:
    """Wait until none of vks is physically held, so injected Ctrl+V isn't read as e.g. Ctrl+Shift+V.
    Returns False if a key was still down at the timeout."""
    deadline = time.monotonic() + timeout_s
    while any(key_down(vk) for vk in vks):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def release_alt_without_menu(vk_alt: int) -> bool:
    return send_keys([(VK_MENU_MASK, False), (VK_MENU_MASK, True), (vk_alt, True)])


# --- Foreground window -------------------------------------------------------

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass
class ForegroundApp:
    process_name: str
    window_title: str
    hwnd: int = 0


def foreground_app() -> ForegroundApp:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ForegroundApp("", "")
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if handle:
        try:
            size = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                name = Path(path.value).name
        finally:
            kernel32.CloseHandle(handle)
    return ForegroundApp(name, buf.value, hwnd)


user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
SW_RESTORE = 9


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) in physical pixels, or None."""
    rect = wintypes.RECT()
    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def activate_window(hwnd: int) -> bool:
    """Bring hwnd back to the foreground (e.g. after the user clicked one of our windows)."""
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.SetForegroundWindow(hwnd):
        return True
    # Windows only lets the foreground thread hand focus over; borrow its input state.
    current = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    target = user32.GetWindowThreadProcessId(hwnd, None)
    attached = bool(current and current != target and user32.AttachThreadInput(current, target, True))
    try:
        return bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(current, target, False)


# --- Overlay windows -----------------------------------------------------------

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x8000000
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]


def make_overlay(hwnd: int, click_through: bool) -> None:
    """Stop a window from ever taking focus (and optionally from receiving clicks)."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    if click_through:
        style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)


# --- Single instance ---------------------------------------------------------

kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
ERROR_ALREADY_EXISTS = 183


def acquire_single_instance(name: str = "Local\\talk-to-mic") -> wintypes.HANDLE | None:
    """Returns a mutex handle to keep alive, or None if another instance is running."""
    handle = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


# A second launch sets this event to ask the running instance to show its window.
OPEN_EVENT = "Local\\talk-to-mic-open"
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
WAIT_OBJECT_0 = 0


def create_open_event(name: str = OPEN_EVENT) -> wintypes.HANDLE:
    return kernel32.CreateEventW(None, False, False, name)  # auto-reset


def signal_open_event(name: str = OPEN_EVENT) -> bool:
    handle = kernel32.CreateEventW(None, False, False, name)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def wait_event(handle: wintypes.HANDLE, timeout_ms: int = 0xFFFFFFFF) -> bool:
    return kernel32.WaitForSingleObject(handle, timeout_ms) == WAIT_OBJECT_0
