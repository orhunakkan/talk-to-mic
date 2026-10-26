"""Drives KeyboardHook's Win32 callback with fake events to check that every detector sees them."""

import ctypes

from talk_to_mic.hotkey import (
    LLKHF_INJECTED,
    WM_KEYDOWN,
    WM_KEYUP,
    KBDLLHOOKSTRUCT,
    Action,
    KeyboardHook,
    build_detectors,
)

F13, SPACE, LCTRL, RSHIFT, KEY_V = 0x7C, 0x20, 0xA2, 0xA1, 0x56


def event(hook: KeyboardHook, vk: int, down: bool, injected: bool = False) -> int:
    info = KBDLLHOOKSTRUCT(vkCode=vk, flags=LLKHF_INJECTED if injected else 0)
    return hook._callback(0, WM_KEYDOWN if down else WM_KEYUP, ctypes.addressof(info))


def test_callback_routes_to_all_detectors():
    actions = []
    hook = KeyboardHook(build_detectors("f13", 0, "space", "right_shift", "left_ctrl+right_shift+v"), actions.append)
    assert event(hook, F13, True) == 1  # F13 is swallowed (block mode)
    assert event(hook, SPACE, True) == 1  # Ask combo swallowed
    assert event(hook, SPACE, False) == 1
    assert event(hook, F13, False) == 1
    event(hook, LCTRL, True)
    event(hook, RSHIFT, True)
    assert event(hook, KEY_V, True) == 1  # paste-last chord swallowed
    assert event(hook, KEY_V, False) == 1
    event(hook, RSHIFT, False)
    event(hook, LCTRL, False)
    assert actions == [Action.START, Action.ASK, Action.FINISH, Action.PASTE_LAST]


def test_callback_ignores_injected_and_survives_detector_errors():
    actions = []

    class Broken:
        def feed(self, *args):
            raise RuntimeError("boom")

    hook = KeyboardHook([Broken()], actions.append)
    event(hook, KEY_V, True)  # must not raise into Windows
    hook.detectors = build_detectors("f13", 0)
    event(hook, F13, True, injected=True)
    assert actions == []
