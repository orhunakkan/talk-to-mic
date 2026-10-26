from talk_to_mic.config import DEFAULT_CONFIG_TEXT, Config, parse_config
from talk_to_mic.settings_store import copy_into, save_config

# config.toml as written by talk-to-mic 1.0.0, with a user comment and an unknown key.
V1_CONFIG = """\
# my own note
[hotkey]
key = "right_alt"
min_hold_ms = 300  # I like it slower

[transcription]
model = "small"
vocabulary = ["Orhun"]
future_option = 1

[cleanup]
enabled = true
"""


def test_default_text_matches_every_default():
    assert parse_config(DEFAULT_CONFIG_TEXT) == Config()


def test_v1_config_loads_with_new_defaults():
    cfg = parse_config(V1_CONFIG)
    assert cfg.hotkey.min_hold_ms == 300 and cfg.hotkey.ask_combo == "space"
    assert cfg.history.enabled and cfg.translation.target_language == "English" and cfg.ui.floating_bar


def test_save_keeps_comments_and_unknown_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(V1_CONFIG, encoding="utf-8")
    cfg = parse_config(V1_CONFIG)
    cfg.hotkey.ask_combo = ""
    cfg.translation.target_language = "Turkish"
    cfg.transcription.vocabulary = ["Orhun", "kubectl"]
    save_config(cfg, path)
    text = path.read_text(encoding="utf-8")
    assert "# my own note" in text and "# I like it slower" in text and "future_option = 1" in text
    assert "# Language that Translate mode writes in" in text  # new sections arrive with their comments
    assert parse_config(text) == cfg


def test_save_creates_missing_file(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    cfg = Config()
    cfg.ui.typing_wpm = 55
    save_config(cfg, path)
    assert parse_config(path.read_text(encoding="utf-8")) == cfg


def test_copy_into_updates_sections_in_place():
    target, source = Config(), Config()
    section = target.cleanup
    source.cleanup.model = "claude-opus-5-5"
    source.transcription.vocabulary = ["a"]
    copy_into(target, source)
    assert section.model == "claude-opus-5-5" and target.cleanup is section
    source.transcription.vocabulary.append("b")
    assert target.transcription.vocabulary == ["a"]  # copied, not shared
