"""Hold-to-talk hotkey via a low-level keyboard hook (WH_KEYBOARD_LL on Windows, XInput2 on X11).

The decision logic lives in HoldDetector, which has no Win32 dependency so it can be
unit tested. KeyboardHook (hotkey_win32.py or hotkey_x11.py) runs the hook on its own thread
and must handle each event quickly, so it only forwards events to a callback.
Key codes are Windows virtual-key codes on both platforms; x11.py translates X keys to them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum

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


def __getattr__(name: str):
    """KeyboardHook (and on Windows the Win32 hook's constants) come from the OS hook module, which
    is imported on first use because it builds on the detectors above."""
    if sys.platform == "win32":
        from . import hotkey_win32 as hook
    else:
        from . import hotkey_x11 as hook
    try:
        return getattr(hook, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
