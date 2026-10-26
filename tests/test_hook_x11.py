"""Drives the X11 KeyboardHook with fake raw key events and a fake X display."""

import sys
from types import SimpleNamespace

import pytest

if sys.platform != "linux":
    pytest.skip("X11 keyboard hook (see test_hook_callback.py)", allow_module_level=True)

from Xlib import XK, X  # noqa: E402
from Xlib.ext import xinput  # noqa: E402

from talk_to_mic import hotkey_x11, x11  # noqa: E402
from talk_to_mic.hotkey import Action, KeyboardHook, build_detectors  # noqa: E402

CORE, XTEST, KEYBOARD, KEYBOARD2 = 3, 5, 17, 18  # device ids
KEYCODES = {
    108: XK.XK_Alt_R, 65: XK.XK_space, 37: XK.XK_Control_L, 62: XK.XK_Shift_R, 55: XK.XK_v,
    38: XK.XK_a, 66: XK.XK_Caps_Lock, 191: 0x1008FF81,  # XF86Tools: what many layouts put on F13
}
ALT_R, SPACE, LCTRL, RSHIFT, KEY_V, KEY_A, CAPS, F13, NO_SYMBOL = 108, 65, 37, 62, 55, 38, 66, 191, 8


class FakeDisplay:
    def __init__(self):
        self.calls: list[tuple] = []
        root = SimpleNamespace(
            xinput_grab_keycode=lambda dev, t, keycode, *a: self.calls.append(("grab", dev, keycode))
            or SimpleNamespace(modifiers=[]),
            xinput_ungrab_keycode=lambda dev, keycode, mods: self.calls.append(("ungrab", dev, keycode)),
        )
        self.screen = lambda: SimpleNamespace(root=root)

    def keycode_to_keysym(self, keycode, index):
        return KEYCODES.get(keycode, 0)

    def keysym_to_keycode(self, keysym):
        return next((code for code, sym in KEYCODES.items() if sym == keysym), 0)

    def flush(self):
        pass

    def grabbed(self):
        """(device, keycode) pairs currently grabbed."""
        held = set()
        for kind, dev, keycode in (c for c in self.calls if c[0] in ("grab", "ungrab")):
            (held.add if kind == "grab" else held.discard)((dev, keycode))
        return held

    def taps(self):
        return [c[2] for c in self.calls if c[0] == "fake" and c[1] == X.KeyPress]


@pytest.fixture
def hook(monkeypatch):
    def make(*detector_args):
        actions = []
        h = KeyboardHook(build_detectors(*detector_args), actions.append)
        h._d = h._g = FakeDisplay()
        h._keyboards, h._injected_devices, h._mask_keycode = {KEYBOARD, KEYBOARD2}, {XTEST}, NO_SYMBOL
        monkeypatch.setattr(hotkey_x11.xtest, "fake_input", lambda d, kind, keycode: d.calls.append(("fake", kind, keycode)))
        return h, actions

    return make


def key(h, keycode, down, device=KEYBOARD):
    for dev in (device, CORE):  # the core keyboard repeats every key
        h._handle(SimpleNamespace(
            type=hotkey_x11.GENERIC_EVENT, evtype=xinput.RawKeyPress if down else xinput.RawKeyRelease,
            data=SimpleNamespace(deviceid=dev, detail=keycode, sourceid=device),
        ))


def test_space_is_grabbed_on_every_keyboard_only_during_a_hold(hook):
    h, actions = hook("right_alt", 0, "space", "right_shift")
    key(h, ALT_R, True)
    assert h._d.grabbed() == {(KEYBOARD, SPACE), (KEYBOARD2, SPACE)}  # not Right Shift: modifiers pass
    key(h, SPACE, True)
    key(h, ALT_R, False)
    assert h._d.grabbed()  # Space is still held: its auto-repeat must not reach the app
    key(h, SPACE, False)
    assert h._d.grabbed() == set()
    assert actions == [Action.START, Action.ASK, Action.FINISH]


def test_alt_is_masked_once_the_hold_counts(hook, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(hotkey_x11.time, "monotonic", lambda: clock[0])
    h, actions = hook("right_alt", 250)
    key(h, ALT_R, True)
    h._mask_alt()
    assert h._d.taps() == []  # still a tap, which should open the menu as usual
    clock[0] += 0.3
    h._mask_alt()
    assert h._d.taps() == [NO_SYMBOL]
    key(h, ALT_R, False)
    assert actions == [Action.START, Action.FINISH]


def test_other_keys_cancel_a_hold_and_pass_through(hook):
    h, actions = hook("right_alt", 0, "space")
    key(h, ALT_R, True)
    key(h, KEY_A, True)
    assert actions == [Action.START, Action.CANCEL]
    assert h._d.grabbed() == set() and h._d.taps() == []
    h._mask_alt()
    assert h._d.taps() == []  # no mask for a cancelled hold


def test_injected_keys_are_ignored(hook):
    h, actions = hook("right_alt", 0)
    key(h, ALT_R, True, device=XTEST)
    assert actions == [] and h._d.grabbed() == set()


def test_block_hotkeys_work_and_caps_lock_is_toggled_back(hook):
    h, actions = hook("f13", 0)
    key(h, F13, True)
    key(h, F13, False)
    assert actions == [Action.START, Action.FINISH]
    h, actions = hook("caps_lock", 0)
    key(h, CAPS, True)
    key(h, CAPS, False)
    assert actions == [Action.START, Action.FINISH] and h._d.taps() == [CAPS]


def test_paste_last_key_is_grabbed_only_while_the_chord_modifiers_are_held(hook):
    h, actions = hook("right_alt", 0, "", "", "left_ctrl+right_shift+v")
    key(h, LCTRL, True)
    assert h._d.grabbed() == set()
    key(h, RSHIFT, True)
    assert h._d.grabbed() == {(KEYBOARD, KEY_V), (KEYBOARD2, KEY_V)}
    key(h, KEY_V, True)
    key(h, KEY_V, False)
    key(h, RSHIFT, False)
    assert h._d.grabbed() == set() and actions == [Action.PASTE_LAST]


def test_keycodes_map_to_windows_virtual_keys():
    d = FakeDisplay()
    assert x11.vk_for_keycode(d, ALT_R) == 0xA5
    assert x11.vk_for_keycode(d, F13) == 0x7C  # XF86Tools keysym, F13 keycode
    assert x11.vk_for_keycode(d, KEY_V) == 0x56
    assert x11.vk_for_keycode(d, 999) == x11.OTHER_VK
    assert x11.keycode_for_vk(d, 0xA5) == ALT_R and x11.keycode_for_vk(d, 0x7C) == F13
