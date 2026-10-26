from talk_to_mic import paste, win32
from talk_to_mic.hotkey import Intent
from talk_to_mic.ui.floating_bar import busy_label, intent_label, level_to_height
from talk_to_mic.ui.hub import chord_label, format_minutes, format_when, key_label, one_line, start_of_today
from talk_to_mic.ui.screens import bottom_center


def test_bottom_center():
    assert bottom_center((0, 0, 2560, 1392), (200, 40), 28) == (1180, 1324)
    assert bottom_center((-1920, 100, 1920, 1040), (300, 40), 28) == (-1110, 1072)  # monitor left of primary


def test_level_meter_scale():
    assert level_to_height(0) == 0
    assert level_to_height(10 ** (-55 / 20)) == 0
    assert level_to_height(1.0) == 1
    assert 0.4 < level_to_height(0.02) < 0.6


def test_labels():
    assert intent_label(Intent.TRANSLATE, "Turkish") == "Translate → Turkish"
    assert intent_label(Intent.DICTATE, "x") == "Listening"
    assert busy_label(Intent.ASK) == "Thinking…"
    assert key_label("right_shift") == "Right Shift" and key_label("f13") == "F13"
    assert chord_label("left_ctrl+right_shift+v") == "Left Ctrl + Right Shift + V"


def test_formatting():
    assert format_minutes(0.4) == "0 min" and format_minutes(59.6) == "1 h" and format_minutes(135) == "2 h 15 min"
    now = start_of_today(1_760_000_000) + 15 * 3600
    assert format_when(now - 3600, now) == "14:00"
    assert format_when(now - 24 * 3600, now).startswith("Yesterday")
    assert one_line("a\n b   c") == "a b c" and one_line("x" * 200, 10) == "x" * 9 + "…"


def test_copy_selection_skips_terminals(monkeypatch):
    sent = []
    monkeypatch.setattr(win32, "send_ctrl_c", lambda: sent.append(1) or True)
    assert paste.copy_selection("WindowsTerminal.exe") == ""
    assert sent == []
