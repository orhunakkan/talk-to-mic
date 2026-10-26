"""User settings, stored as TOML in %APPDATA%\\talk-to-mic\\config.toml (~/.config/talk-to-mic/ on Linux)."""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

# APPDATA is always set on Windows; elsewhere it still wins, so tests can point it at a scratch folder.
APP_DIR = Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "talk-to-mic"
CONFIG_PATH = APP_DIR / "config.toml"
LOG_PATH = APP_DIR / "talk-to-mic.log"
DB_PATH = APP_DIR / "talk-to-mic.db"

DEFAULT_CONFIG_TEXT = """\
# talk-to-mic settings. Restart the app (tray menu > Quit, then start it again) after editing.
# Changes saved from the Settings page of the talk-to-mic window apply immediately.

[hotkey]
# Hold this key to dictate, release to paste.
# One of: right_alt, right_ctrl, caps_lock, scroll_lock, pause, f13 ... f24
key = "right_alt"
# Taps shorter than this are ignored (milliseconds).
min_hold_ms = 250
# Press one of these while holding the dictation key to switch modes; "" turns the mode off.
# One of: space, right_shift, left_shift, right_ctrl, left_ctrl, tab, enter
# Ask Anything: speak an instruction or question about the selected text.
ask_combo = "space"
# Translate: dictate and paste the translation (see [translation]).
translate_combo = "right_shift"
# Pastes the last result again; "" turns it off. Modifiers + one letter, digit or F-key.
paste_last = "left_ctrl+right_shift+v"

[audio]
# Substring of the microphone name to use; empty = Windows default input device.
input_device = ""
# Short start/stop tones.
beep = true
# Recording is cut off after this many seconds.
max_seconds = 300

[transcription]
# "faster_whisper" runs Whisper locally on the CPU.
# "whisper_server" sends audio to a local whisper.cpp server (e.g. a Vulkan build for AMD GPUs).
engine = "faster_whisper"
# faster_whisper model: tiny, base, small, medium, large-v3-turbo, large-v3 (bigger = more accurate, slower).
model = "small"
# Two-letter language code (e.g. "en", "tr"); empty = auto-detect.
language = ""
beam_size = 5
# Names, jargon and spellings Whisper should prefer.
vocabulary = []
# whisper.cpp server endpoint, used when engine = "whisper_server".
server_url = "http://127.0.0.1:8178/inference"

[cleanup]
# Send the raw transcript to Claude to remove filler words and fix punctuation.
# Needs ANTHROPIC_API_KEY in the environment; without it the raw transcript is pasted.
enabled = true
model = "claude-haiku-5-5"
# low | medium | high - lower is faster.
effort = "low"
# Include the focused app's name and window title so Claude can match its style.
send_app_context = true
timeout_seconds = 20

[paste]
# Put your previous clipboard contents back after pasting.
restore_clipboard = true
restore_delay_ms = 500

[translation]
# Language that Translate mode writes in, e.g. "English", "Turkish", "German".
target_language = "English"

[history]
# Keep a local history of dictations (talk-to-mic.db next to this file).
enabled = true
# Also store the recorded audio with each entry (about 2 MB per minute).
keep_audio = false
# Delete entries older than this many days; 0 keeps them forever.
retention_days = 0

[ui]
# Show a small pill near the bottom of the screen while listening and transcribing.
floating_bar = true
# Your typing speed in words per minute, used for the "time saved" statistic.
typing_wpm = 40
"""


@dataclass
class HotkeyConfig:
    key: str = "right_alt"
    min_hold_ms: int = 250
    ask_combo: str = "space"
    translate_combo: str = "right_shift"
    paste_last: str = "left_ctrl+right_shift+v"


@dataclass
class AudioConfig:
    input_device: str = ""
    beep: bool = True
    max_seconds: int = 300


@dataclass
class TranscriptionConfig:
    engine: str = "faster_whisper"
    model: str = "small"
    language: str = ""
    beam_size: int = 5
    vocabulary: list[str] = field(default_factory=list)
    server_url: str = "http://127.0.0.1:8178/inference"


@dataclass
class CleanupConfig:
    enabled: bool = True
    model: str = "claude-haiku-5-5"
    effort: str = "low"
    send_app_context: bool = True
    timeout_seconds: float = 20


@dataclass
class PasteConfig:
    restore_clipboard: bool = True
    restore_delay_ms: int = 500


@dataclass
class TranslationConfig:
    target_language: str = "English"


@dataclass
class HistoryConfig:
    enabled: bool = True
    keep_audio: bool = False
    retention_days: int = 0


@dataclass
class UiConfig:
    floating_bar: bool = True
    typing_wpm: int = 40


@dataclass
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    paste: PasteConfig = field(default_factory=PasteConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    ui: UiConfig = field(default_factory=UiConfig)


def _apply(section: object, values: dict, name: str) -> None:
    known = {f.name: f for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            log.warning("Ignoring unknown setting [%s] %s", name, key)
            continue
        default = getattr(section, key)
        if isinstance(default, bool) != isinstance(value, bool) or (
            not isinstance(value, type(default)) and not (isinstance(default, float) and isinstance(value, int))
        ):
            log.warning("Ignoring [%s] %s: expected %s, got %r", name, key, type(default).__name__, value)
            continue
        setattr(section, key, value)


def parse_config(text: str) -> Config:
    data = tomllib.loads(text)
    cfg = Config()
    for name, values in data.items():
        section = getattr(cfg, name, None)
        if section is None or not isinstance(values, dict):
            log.warning("Ignoring unknown section [%s]", name)
            continue
        _apply(section, values, name)
    return cfg


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
        log.info("Wrote default settings to %s", path)
    try:
        return parse_config(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        log.error("Could not parse %s (%s); using defaults", path, exc)
        return Config()
