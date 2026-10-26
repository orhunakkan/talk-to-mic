"""The Linux counterpart of win32.py: the same functions, for X11 sessions, built on python-xlib.

Keystrokes are injected through XTEST, which the keyboard hook (hotkey_x11.py) sees as injected.
Wayland sessions are not supported: they don't let apps watch the keyboard or type into other apps.
"""

from __future__ import annotations

import fcntl
import logging
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from Xlib import XK, X, display, protocol
from Xlib.error import ConnectionClosedError, XError
from Xlib.ext import xtest

log = logging.getLogger(__name__)

_ERRORS = (XError, ConnectionClosedError, OSError)

# --- Key codes -------------------------------------------------------------------
XK.load_keysym_group("xkb")  # ISO_Level3_Shift, ISO_Left_Tab
# hotkey.py's detectors work with Windows virtual-key codes; these map X keysyms onto them.

KEYSYM_VKS: dict[int, int] = {
    XK.XK_Shift_L: 0xA0,
    XK.XK_Shift_R: 0xA1,
    XK.XK_Control_L: 0xA2,
    XK.XK_Control_R: 0xA3,
    XK.XK_Alt_L: 0xA4,
    XK.XK_Meta_L: 0xA4,
    XK.XK_Alt_R: 0xA5,
    XK.XK_Meta_R: 0xA5,
    XK.XK_ISO_Level3_Shift: 0xA5,  # Right Alt is AltGr on many layouts
    XK.XK_Super_L: 0x5B,
    XK.XK_Super_R: 0x5C,
    XK.XK_Caps_Lock: 0x14,
    XK.XK_Scroll_Lock: 0x91,
    XK.XK_Pause: 0x13,
    XK.XK_space: 0x20,
    XK.XK_Tab: 0x09,
    XK.XK_ISO_Left_Tab: 0x09,
    XK.XK_Return: 0x0D,
    XK.XK_KP_Enter: 0x0D,
    **{XK.XK_a + i: 0x41 + i for i in range(26)},
    **{XK.XK_0 + i: 0x30 + i for i in range(10)},
    **{XK.XK_F1 + i: 0x70 + i for i in range(24)},
}
# The keysym to inject or grab for each virtual key (the first one listed above wins).
VK_KEYSYMS: dict[int, int] = {}
for _keysym, _vk in KEYSYM_VKS.items():
    VK_KEYSYMS.setdefault(_vk, _keysym)

# Many layouts give the F13-F24 keys XF86 media keysyms; their keycodes are fixed under evdev.
F13_KEYCODE, F13_VK = 191, 0x7C
OTHER_VK = 0xFF  # any other key: not a modifier, so it interrupts a hold like on Windows


def vk_for_keycode(d: display.Display, keycode: int) -> int:
    vk = KEYSYM_VKS.get(d.keycode_to_keysym(keycode, 0))
    if vk is None and F13_KEYCODE <= keycode < F13_KEYCODE + 12:
        vk = F13_VK + keycode - F13_KEYCODE
    return OTHER_VK if vk is None else vk


def keycode_for_vk(d: display.Display, vk: int) -> int:
    """0 if the keyboard layout has no key for vk."""
    keycode = d.keysym_to_keycode(VK_KEYSYMS[vk]) if vk in VK_KEYSYMS else 0
    if not keycode and F13_VK <= vk < F13_VK + 12:
        keycode = F13_KEYCODE + vk - F13_VK
    return keycode


# --- One shared connection ---------------------------------------------------------
# python-xlib connections aren't thread-safe; the keyboard hook has its own.

_lock = threading.RLock()
_display: display.Display | None = None


@contextmanager
def _x():
    global _display
    with _lock:
        if _display is None:
            _display = display.Display()
        try:
            yield _display
        except (ConnectionClosedError, OSError):
            _display = None  # reconnect next time
            raise


# --- Injected keystrokes ----------------------------------------------------------

def send_keys(events: list[tuple[int, bool]]) -> bool:
    """Inject (keysym, is_up) key events in order. Returns False if they could not be sent."""
    try:
        with _x() as d:
            keycodes = [d.keysym_to_keycode(keysym) for keysym, _ in events]
            if not all(keycodes):
                return False
            for keycode, (_, up) in zip(keycodes, events):
                xtest.fake_input(d, X.KeyRelease if up else X.KeyPress, keycode)
            d.sync()
        return True
    except _ERRORS:
        log.warning("Could not send keystrokes", exc_info=True)
        return False


def _chord(*keysyms: int) -> list[tuple[int, bool]]:
    return [(k, False) for k in keysyms] + [(k, True) for k in reversed(keysyms)]


def send_ctrl_v() -> bool:
    return send_keys(_chord(XK.XK_Control_L, XK.XK_v))


def send_ctrl_c() -> bool:
    return send_keys(_chord(XK.XK_Control_L, XK.XK_c))


def send_ctrl_shift_v() -> bool:
    """Paste in terminals, where Ctrl+V types a literal ^V."""
    return send_keys(_chord(XK.XK_Control_L, XK.XK_Shift_L, XK.XK_v))


def send_ctrl_shift_c() -> bool:
    """Copy in terminals, where Ctrl+C interrupts the running command."""
    return send_keys(_chord(XK.XK_Control_L, XK.XK_Shift_L, XK.XK_c))


MODIFIER_KEYS = (
    XK.XK_Shift_L, XK.XK_Shift_R, XK.XK_Control_L, XK.XK_Control_R, XK.XK_Alt_L, XK.XK_Alt_R,
    XK.XK_ISO_Level3_Shift, XK.XK_Super_L, XK.XK_Super_R,
)


def key_down(keysym: int) -> bool:
    with _x() as d:
        keymap = d.query_keymap()
        return any(keymap[code // 8] & (1 << code % 8) for code, _ in d.keysym_to_keycodes(keysym))


def wait_keys_released(vks=MODIFIER_KEYS, timeout_s: float = 1.0) -> bool:
    """Wait until none of vks (keysyms here) is physically held, so injected Ctrl+V isn't read as
    e.g. Ctrl+Shift+V. Returns False if a key was still down at the timeout."""
    deadline = time.monotonic() + timeout_s
    while any(key_down(vk) for vk in vks):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


# --- Foreground window -------------------------------------------------------


@dataclass
class ForegroundApp:
    process_name: str
    window_title: str
    hwnd: int = 0  # the X window id


# Programs that run another program; their script's name (/proc/PID/comm) says more.
_INTERPRETERS = ("python", "perl", "ruby", "node", "java", "bash", "sh", "dash")


def _process_name(pid: int) -> str:
    try:
        name = Path(os.readlink(f"/proc/{pid}/exe")).name
    except OSError:
        name = ""
    if not name or name.startswith(_INTERPRETERS):
        try:
            name = Path(f"/proc/{pid}/comm").read_text().strip() or name
        except OSError:
            pass
    return name


def _property(d: display.Display, win, name: str):
    prop = win.get_full_property(d.intern_atom(name), X.AnyPropertyType)
    return prop.value if prop else None


def foreground_app() -> ForegroundApp:
    try:
        with _x() as d:
            active = _property(d, d.screen().root, "_NET_ACTIVE_WINDOW")
            hwnd = int(active[0]) if active is not None and len(active) else 0
            if not hwnd:
                return ForegroundApp("", "")
            win = d.create_resource_object("window", hwnd)
            title = _property(d, win, "_NET_WM_NAME")
            title = title.decode("utf-8", "replace") if isinstance(title, bytes) else (win.get_wm_name() or "")
            pid = _property(d, win, "_NET_WM_PID")
            wm_class = win.get_wm_class()
    except _ERRORS:
        log.debug("Could not read the active window", exc_info=True)
        return ForegroundApp("", "")
    name = _process_name(int(pid[0])) if pid is not None and len(pid) else ""
    return ForegroundApp(name or (wm_class[0] if wm_class else ""), title, hwnd)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) in physical pixels, or None."""
    if not hwnd:
        return None
    try:
        with _x() as d:
            win = d.create_resource_object("window", hwnd)
            geometry = win.get_geometry()
            origin = d.screen().root.translate_coords(win, 0, 0)
    except _ERRORS:
        return None
    return origin.x, origin.y, origin.x + geometry.width, origin.y + geometry.height


def activate_window(hwnd: int) -> bool:
    """Bring hwnd back to the foreground (e.g. after the user clicked one of our windows)."""
    if not hwnd:
        return False
    try:
        with _x() as d:
            root = d.screen().root
            atom = d.intern_atom("_NET_ACTIVE_WINDOW")
            # Source 2 ("pager") asks the window manager to honour the request rather than flash the window.
            event = protocol.event.ClientMessage(
                window=d.create_resource_object("window", hwnd), client_type=atom,
                data=(32, [2, X.CurrentTime, 0, 0, 0]),
            )
            root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            d.sync()
    except _ERRORS:
        return False
    deadline = time.monotonic() + 0.5
    while foreground_app().hwnd != hwnd:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


# --- Overlay windows -----------------------------------------------------------


def make_overlay(hwnd: int, click_through: bool) -> None:
    """Nothing to do on X11: Qt's WindowDoesNotAcceptFocus and WindowTransparentForInput flags
    already keep the window from taking focus or clicks."""


# --- Single instance ---------------------------------------------------------


def _runtime_dir() -> Path:
    path = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/talk-to-mic-{os.getuid()}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def acquire_single_instance(name: str = "talk-to-mic"):
    """Returns a lock file to keep open, or None if another instance is running."""
    lock = open(_runtime_dir() / f"{name}.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


# A second launch connects to this socket to ask the running instance to show its window.
OPEN_EVENT = "talk-to-mic-open.sock"


def create_open_event(name: str = OPEN_EVENT) -> socket.socket | None:
    path = _runtime_dir() / name
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        path.unlink(missing_ok=True)  # left over from a crash; we hold the single-instance lock
        sock.bind(str(path))
        sock.listen(4)
    except OSError:
        log.warning("Could not listen on %s; a second launch won't open the window", path, exc_info=True)
        sock.close()
        return None
    return sock


def signal_open_event(name: str = OPEN_EVENT) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(_runtime_dir() / name))
        return True
    except OSError:
        return False


def wait_event(handle: socket.socket, timeout_ms: int | None = None) -> bool:
    handle.settimeout(None if timeout_ms is None else timeout_ms / 1000)
    try:
        conn, _ = handle.accept()
    except OSError:
        return False
    conn.close()
    return True
