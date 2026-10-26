"""Linux paste and copy: terminals get Ctrl+Shift+V/C, other apps Ctrl+V/C."""

import sys

import pytest

if sys.platform != "linux":
    pytest.skip("Linux clipboard (see test_ui_helpers.py)", allow_module_level=True)

from talk_to_mic import paste_x11, x11  # noqa: E402


@pytest.fixture
def keys(monkeypatch):
    sent = []
    for name in ("send_ctrl_v", "send_ctrl_c", "send_ctrl_shift_v", "send_ctrl_shift_c"):
        monkeypatch.setattr(x11, name, lambda name=name: sent.append(name) or True)
    monkeypatch.setattr(x11, "wait_keys_released", lambda *a, **k: True)
    monkeypatch.setattr(paste_x11, "save_clipboard", lambda: {})
    monkeypatch.setattr(paste_x11, "restore_clipboard", lambda saved: None)
    monkeypatch.setattr(paste_x11, "set_clipboard_text", lambda text: None)
    return sent


@pytest.mark.parametrize("process, expected", [("gnome-terminal-server", "send_ctrl_shift_v"), ("code", "send_ctrl_v")])
def test_paste_uses_the_terminal_shortcut(keys, monkeypatch, process, expected):
    monkeypatch.setattr(x11, "foreground_app", lambda: x11.ForegroundApp(process, "title"))
    assert paste_x11.paste_text("hi", restore=False)
    assert keys == [expected]


def test_copy_selection_in_a_terminal_uses_ctrl_shift_c(keys):
    assert paste_x11.copy_selection("kitty", timeout_s=0.05) == ""  # nothing was copied
    assert keys == ["send_ctrl_shift_c"]
