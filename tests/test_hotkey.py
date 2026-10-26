from talk_to_mic.hotkey import HOTKEYS, Action, HoldDetector

RALT = HOTKEYS["right_alt"]
F13 = HOTKEYS["f13"]
KEY_Q = 0x51
LSHIFT = 0xA0


def press(d: HoldDetector, vk: int, t: float, injected: bool = False):
    return d.feed(vk, True, injected, t)


def release(d: HoldDetector, vk: int, t: float, injected: bool = False):
    return d.feed(vk, False, injected, t)


def test_hold_and_release_finishes_and_masks_alt():
    d = HoldDetector(RALT, 0.25)
    down = press(d, RALT, 0.0)
    assert down.action is Action.START and not down.suppress
    up = release(d, RALT, 1.0)
    assert up.action is Action.FINISH and up.suppress and up.release_alt


def test_short_tap_cancels_and_passes_through():
    d = HoldDetector(RALT, 0.25)
    press(d, RALT, 0.0)
    up = release(d, RALT, 0.1)
    assert up.action is Action.CANCEL and not up.suppress and not up.release_alt


def test_autorepeat_does_not_restart():
    d = HoldDetector(RALT, 0.25)
    press(d, RALT, 0.0)
    assert press(d, RALT, 0.5).action is None
    assert release(d, RALT, 1.0).action is Action.FINISH


def test_altgr_combination_cancels_and_releases_normally():
    d = HoldDetector(RALT, 0.25)
    press(d, RALT, 0.0)
    assert press(d, KEY_Q, 0.4).action is Action.CANCEL  # e.g. AltGr+Q = @ on Turkish Q
    up = release(d, RALT, 1.0)
    assert up.action is None and not up.suppress


def test_other_modifiers_do_not_cancel():
    d = HoldDetector(RALT, 0.25)
    press(d, RALT, 0.0)
    assert press(d, LSHIFT, 0.3).action is None
    assert release(d, RALT, 1.0).action is Action.FINISH


def test_injected_events_are_ignored():
    d = HoldDetector(RALT, 0.25)
    assert press(d, RALT, 0.0, injected=True).action is None
    press(d, RALT, 0.0)
    assert press(d, KEY_Q, 0.3, injected=True).action is None  # our own Ctrl+V etc.
    assert release(d, RALT, 1.0).action is Action.FINISH


def test_block_mode_swallows_key():
    d = HoldDetector(F13, 0.25)
    down = press(d, F13, 0.0)
    assert down.action is Action.START and down.suppress
    up = release(d, F13, 1.0)
    assert up.action is Action.FINISH and up.suppress and not up.release_alt


def test_unrelated_keys_pass_when_idle():
    d = HoldDetector(RALT, 0.25)
    assert press(d, KEY_Q, 0.0).action is None
    assert release(d, KEY_Q, 0.1).action is None
