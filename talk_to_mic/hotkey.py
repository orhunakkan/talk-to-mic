"""Hold-to-talk hotkey via a low-level keyboard hook (WH_KEYBOARD_LL).

The decision logic lives in HoldDetector, which has no Win32 dependency so it can be
unit tested. KeyboardHook runs the hook on its own thread with a message loop and
must return from each callback quickly, so it only forwards events to a callback.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from . import win32

log = logging.getLogger(__name__)

HOTKEYS: dict[str, int] = {
    "right_alt": 0xA5,
    "right_ctrl": 0xA3,
    "caps_lock": 0x14,
    "scroll_lock": 0x91,
    "pause": 0x13,
    **{f"f{n}": 0x7C + (n - 13) for n in range(13, 25)},
}

MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}
ALT_VKS = {0xA4, 0xA5}

# Keys that can be pressed during a hold to switch to Ask Anything or Translate.
COMBO_KEYS: dict[str, int] = {
    "space": 0x20,
    "right_shift": 0xA1,
    "left_shift": 0xA0,
    "right_ctrl": 0xA3,
    "left_ctrl": 0xA2,
    "tab": 0x09,
    "enter": 0x0D,
}

# Modifiers usable in a chord such as "left_ctrl+right_shift+v".
CHORD_MODIFIERS: dict[str, int] = {
    "left_ctrl": 0xA2,
    "right_ctrl": 0xA3,
    "left_shift": 0xA0,
    "right_shift": 0xA1,
    "left_alt": 0xA4,
    "right_alt": 0xA5,
    "left_win": 0x5B,
    "right_win": 0x5C,
}
CHORD_KEYS: dict[str, int] = {
    **{chr(c): c - 0x20 for c in range(ord("a"), ord("z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
}


class Action(Enum):
    START = "start"  # key went down: begin recording
    FINISH = "finish"  # key released after a real hold: transcribe and paste
    CANCEL = "cancel"  # short tap, or the key was used in a combination: discard
    ASK = "ask"  # Ask Anything combo pressed during the hold
    TRANSLATE = "translate"  # Translate combo pressed during the hold
    PASTE_LAST = "paste_last"  # paste-last chord pressed


class Intent(Enum):
    """What a recording is for."""

    DICTATE = "dictate"
    ASK = "ask"
    TRANSLATE = "translate"


class Mode(Enum):
    PASS = "pass"  # let all key events through (Ctrl/Shift-style keys)
    ALT_MASK = "alt_mask"  # let Alt through but stop it from opening menus on release
    BLOCK = "block"  # swallow the key entirely (F13, Caps Lock, ...)


def mode_for(vk: int) -> Mode:
    if vk in ALT_VKS:
        return Mode.ALT_MASK
    if vk in MODIFIER_VKS:
        return Mode.PASS
    return Mode.BLOCK


@dataclass
class Decision:
    suppress: bool = False
    action: Action | None = None
    release_alt: bool = False  # caller must inject mask key + Alt up in place of the suppressed event


class HoldDetector:
    def __init__(self, vk: int, min_hold_s: float, ask_vk: int | None = None, translate_vk: int | None = None):
        self.vk = vk
        self.mode = mode_for(vk)
        self.min_hold_s = min_hold_s
        self.combos = {k: v for k, v in ((ask_vk, Action.ASK), (translate_vk, Action.TRANSLATE)) if k and k != vk}
        self.held = False
        self.interrupted = False
        self.down_at = 0.0
        self._swallowed: set[int] = set()  # combo keys whose key-down we suppressed

    def feed(self, vk: int, is_down: bool, injected: bool, now: float) -> Decision:
        if injected:
            return Decision()
        if vk == self.vk:
            return self._hotkey(is_down, now)
        # A swallowed combo key stays swallowed until its key-up, even after the hotkey is released
        # (Space usually auto-repeats a moment longer than Right Alt is held).
        if vk in self.combos and (vk in self._swallowed or (is_down and self.held and not self.interrupted)):
            return self._combo(vk, is_down)
        if self.held and is_down and not self.interrupted and vk not in MODIFIER_VKS:
            # Hotkey used as part of a shortcut or AltGr character: stop dictating.
            self.interrupted = True
            return Decision(action=Action.CANCEL)
        return Decision()

    def _hotkey(self, is_down: bool, now: float) -> Decision:
        block = self.mode is Mode.BLOCK
        if is_down:
            if self.held:  # auto-repeat
                return Decision(suppress=block)
            self.held, self.interrupted, self.down_at = True, False, now
            self._swallowed.clear()  # drop any key whose key-up was never seen
            return Decision(suppress=block, action=Action.START)
        if not self.held:
            return Decision(suppress=block)
        self.held = False
        if self.interrupted:
            return Decision(suppress=block)
        if now - self.down_at < self.min_hold_s:
            return Decision(suppress=block, action=Action.CANCEL)
        if self.mode is Mode.ALT_MASK:
            return Decision(suppress=True, action=Action.FINISH, release_alt=True)
        return Decision(suppress=block, action=Action.FINISH)

    def _combo(self, vk: int, is_down: bool) -> Decision:
        # Modifier combos (Shift, Ctrl) pass through like any other modifier; others (Space)
        # are swallowed, including their auto-repeat and the matching key-up.
        swallow = vk not in MODIFIER_VKS
        if not is_down:
            self._swallowed.discard(vk)
            return Decision(suppress=swallow)
        if swallow:
            if vk in self._swallowed:  # auto-repeat
                return Decision(suppress=True)
            self._swallowed.add(vk)
        return Decision(suppress=swallow, action=self.combos[vk])


@dataclass
class Chord:
    modifiers: frozenset[int]
    vk: int


def parse_chord(text: str) -> Chord | None:
    """"left_ctrl+right_shift+v" -> Chord; "" -> None. Raises ValueError for unknown names."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        return None
    *mods, key = parts
    unknown = [m for m in mods if m not in CHORD_MODIFIERS] + ([key] if key not in CHORD_KEYS else [])
    if unknown or not mods:
        raise ValueError(f"Invalid shortcut {text!r}: use modifiers ({', '.join(CHORD_MODIFIERS)}) plus one key")
    return Chord(frozenset(CHORD_MODIFIERS[m] for m in mods), CHORD_KEYS[key])


class ChordDetector:
    """Fires once when chord.vk goes down while all of chord.modifiers are held."""

    def __init__(self, chord: Chord, action: Action):
        self.chord = chord
        self.action = action
        self.down: set[int] = set()
        self._swallowing = False

    def feed(self, vk: int, is_down: bool, injected: bool, now: float) -> Decision:
        if injected:
            return Decision()
        if vk in self.chord.modifiers:
            (self.down.add if is_down else self.down.discard)(vk)
            return Decision()
        if vk != self.chord.vk:
            return Decision()
        if is_down:
            if self._swallowing:  # auto-repeat
                return Decision(suppress=True)
            if self.down >= self.chord.modifiers:
                self._swallowing = True
                return Decision(suppress=True, action=self.action)
            return Decision()
        if self._swallowing:
            self._swallowing = False
            return Decision(suppress=True)
        return Decision()


def build_detectors(key: str, min_hold_ms: int, ask_combo: str = "", translate_combo: str = "",
                    paste_last: str = "") -> list:
    """Detectors for the hook from config values. Raises ValueError for unknown key names."""
    if key not in HOTKEYS:
        raise ValueError(f"Unknown hotkey {key!r}; choose one of: {', '.join(HOTKEYS)}")
    combos = []
    for name in (ask_combo, translate_combo):
        if name and name not in COMBO_KEYS:
            raise ValueError(f"Unknown combo key {name!r}; choose one of: {', '.join(COMBO_KEYS)}")
        combos.append(COMBO_KEYS.get(name))
    detectors: list = [HoldDetector(HOTKEYS[key], min_hold_ms / 1000, *combos)]
    chord = parse_chord(paste_last)
    if chord:
        detectors.append(ChordDetector(chord, Action.PASTE_LAST))
    return detectors


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
