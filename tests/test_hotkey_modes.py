import pytest

from talk_to_mic.hotkey import (
    COMBO_KEYS,
    HOTKEYS,
    Action,
    ChordDetector,
    HoldDetector,
    build_detectors,
    parse_chord,
)

RALT = HOTKEYS["right_alt"]
SPACE = COMBO_KEYS["space"]
RSHIFT = COMBO_KEYS["right_shift"]
LSHIFT = 0xA0
LCTRL = 0xA2
KEY_Q = 0x51
KEY_V = 0x56


def press(d, vk, t, injected=False):
    return d.feed(vk, True, injected, t)


def release(d, vk, t, injected=False):
    return d.feed(vk, False, injected, t)


def detector():
    return HoldDetector(RALT, 0.25, ask_vk=SPACE, translate_vk=RSHIFT)


def test_space_during_hold_switches_to_ask_and_is_swallowed():
    d = detector()
    press(d, RALT, 0.0)
    down = press(d, SPACE, 0.3)
    assert down.action is Action.ASK and down.suppress
    repeat = press(d, SPACE, 0.4)
    assert repeat.action is None and repeat.suppress
    up = release(d, RALT, 1.0)
    assert up.action is Action.FINISH and up.release_alt
    assert release(d, SPACE, 1.1).suppress  # its key-up is swallowed too


def test_space_auto_repeat_after_release_stays_swallowed():
    d = detector()
    press(d, RALT, 0.0)
    press(d, SPACE, 0.3)
    assert release(d, RALT, 1.0).action is Action.FINISH
    late_repeat = press(d, SPACE, 1.03)  # Space is still held for a moment
    assert late_repeat.suppress and late_repeat.action is None
    assert release(d, SPACE, 1.1).suppress
    normal = press(d, SPACE, 2.0)  # a later, ordinary Space reaches the app
    assert not normal.suppress and normal.action is None


def test_right_shift_switches_to_translate_and_passes_through():
    d = detector()
    press(d, RALT, 0.0)
    down = press(d, RSHIFT, 0.3)
    assert down.action is Action.TRANSLATE and not down.suppress
    assert not release(d, RSHIFT, 0.5).suppress
    assert release(d, RALT, 1.0).action is Action.FINISH


def test_left_shift_still_does_nothing_and_q_still_cancels():
    d = detector()
    press(d, RALT, 0.0)
    assert press(d, LSHIFT, 0.2).action is None
    assert press(d, KEY_Q, 0.3).action is Action.CANCEL
    assert release(d, RALT, 1.0).action is None


def test_space_after_altgr_cancel_is_left_alone():
    d = detector()
    press(d, RALT, 0.0)
    press(d, KEY_Q, 0.2)  # AltGr character: dictation cancelled
    space = press(d, SPACE, 0.3)
    assert space.action is None and not space.suppress


def test_space_without_hold_passes_through():
    d = detector()
    down = press(d, SPACE, 0.0)
    up = release(d, SPACE, 0.1)
    assert down.action is None and not down.suppress and not up.suppress


def test_combos_disabled_behave_like_1_0():
    d = HoldDetector(RALT, 0.25)
    press(d, RALT, 0.0)
    assert press(d, SPACE, 0.3).action is Action.CANCEL  # Space is an ordinary key again
    assert press(d, RSHIFT, 0.4).action is None


def test_combo_same_as_hotkey_is_ignored():
    d = HoldDetector(HOTKEYS["right_ctrl"], 0.25, ask_vk=COMBO_KEYS["right_ctrl"])
    assert d.combos == {}


def test_parse_chord():
    chord = parse_chord("left_ctrl+right_shift+v")
    assert chord.vk == KEY_V and chord.modifiers == {LCTRL, RSHIFT}
    assert parse_chord("") is None
    assert parse_chord(" Left_Ctrl + F5 ").vk == 0x74
    for bad in ("v", "left_ctrl+nope", "hyper+v"):
        with pytest.raises(ValueError):
            parse_chord(bad)


def test_chord_fires_once_and_swallows_the_key():
    d = ChordDetector(parse_chord("left_ctrl+right_shift+v"), Action.PASTE_LAST)
    press(d, LCTRL, 0.0)
    assert press(d, KEY_V, 0.1).action is None  # Shift not held yet: plain Ctrl+V
    release(d, KEY_V, 0.15)
    press(d, RSHIFT, 0.2)
    down = press(d, KEY_V, 0.3)
    assert down.action is Action.PASTE_LAST and down.suppress
    repeat = press(d, KEY_V, 0.35)
    assert repeat.action is None and repeat.suppress
    assert release(d, KEY_V, 0.4).suppress
    release(d, RSHIFT, 0.5)
    assert press(d, KEY_V, 0.6).action is None


def test_chord_ignores_injected_keys():
    d = ChordDetector(parse_chord("left_ctrl+right_shift+v"), Action.PASTE_LAST)
    press(d, LCTRL, 0.0, injected=True)
    press(d, RSHIFT, 0.0, injected=True)
    assert press(d, KEY_V, 0.1).action is None


def test_build_detectors():
    detectors = build_detectors("right_alt", 250, "space", "right_shift", "left_ctrl+right_shift+v")
    assert isinstance(detectors[0], HoldDetector) and isinstance(detectors[1], ChordDetector)
    assert len(build_detectors("right_alt", 250)) == 1
    with pytest.raises(ValueError):
        build_detectors("right_alt", 250, "nope")
    with pytest.raises(ValueError):
        build_detectors("left_alt", 250)
