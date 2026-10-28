from types import SimpleNamespace

import anthropic
import httpx2

from talk_to_mic.cleanup import Cleaner, build_user_message
from talk_to_mic.config import DEFAULT_CONFIG_TEXT, CleanupConfig, parse_config
from talk_to_mic.native import ForegroundApp


def test_default_config_text_matches_dataclass_defaults():
    cfg = parse_config(DEFAULT_CONFIG_TEXT)
    assert cfg.hotkey.key == "right_alt"
    assert cfg.transcription.engine == "faster_whisper"
    assert cfg.transcription.languages == []
    assert cfg.cleanup.model == "claude-haiku-5-5"
    assert cfg.paste.restore_clipboard is True


def test_bad_values_fall_back_to_defaults():
    cfg = parse_config('[hotkey]\nmin_hold_ms = "soon"\nbogus = 1\n[cleanup]\ntimeout_seconds = 5\n')
    assert cfg.hotkey.min_hold_ms == 250
    assert cfg.cleanup.timeout_seconds == 5


def test_user_message_wraps_transcript_and_context():
    msg = build_user_message("hello there", ForegroundApp("slack.exe", "general - Slack"))
    assert "process: slack.exe" in msg and "<transcript>\nhello there\n</transcript>" in msg
    assert "destination_app" not in build_user_message("hi", None)


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_client(result):
    messages = FakeMessages(result)
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages)), messages


def response(text, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


def test_clean_returns_text_and_requests_fallbacks():
    client, messages = fake_client(response("Hello, world."))
    cfg = CleanupConfig(model="claude-opus-5-5")
    out = Cleaner(cfg, client=client).clean("um hello world", ForegroundApp("notepad.exe", "x"))
    assert out == "Hello, world."
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"] == {"effort": "low"}


def test_models_without_fallbacks_use_plain_endpoint():
    client, messages = fake_client(response("ok"))
    Cleaner(CleanupConfig(model="claude-haiku-5-5"), client=client).clean("ok")
    assert "fallbacks" not in messages.calls[0]


def test_refusal_and_errors_return_raw_transcript():
    client, _ = fake_client(response("", stop_reason="refusal"))
    assert Cleaner(CleanupConfig(), client=client).clean("raw words") == "raw words"

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    client, _ = fake_client(anthropic.APIConnectionError(request=request))
    assert Cleaner(CleanupConfig(), client=client).clean("raw words") == "raw words"


def test_disabled_cleanup_skips_api():
    client, messages = fake_client(response("never"))
    assert Cleaner(CleanupConfig(enabled=False), client=client).clean("raw") == "raw"
    assert messages.calls == []


def test_missing_credentials_disable_cleanup(monkeypatch, tmp_path):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path))  # no `ant auth login` profile here
    cleaner = Cleaner(CleanupConfig())
    assert not cleaner.available and cleaner.disabled_reason
    assert cleaner.clean("raw words") == "raw words"
