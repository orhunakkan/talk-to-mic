import json
from types import SimpleNamespace

import pytest

from talk_to_mic.native import credentials
from talk_to_mic.cleanup import (
    AskError,
    Cleaner,
    Term,
    build_ask_message,
    build_user_message,
    parse_ask_response,
)
from talk_to_mic.config import CleanupConfig, TranscriptionConfig
from talk_to_mic.store import Store
from talk_to_mic.transcriber import FasterWhisperTranscriber, Transcript, pick_language, whisper_prompt
from talk_to_mic.vocabulary import claude_terms, whisper_terms
from talk_to_mic.native import ForegroundApp


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_client(text, stop_reason="end_turn"):
    messages = FakeMessages(SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]))
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages)), messages


def test_dictionary_block_only_when_terms_given():
    terms = [Term("kubectl", ("cube cuttle",)), Term("Orhun")]
    msg = build_user_message("hello", None, terms)
    assert "<dictionary>\nkubectl (may be heard as: cube cuttle)\nOrhun\n</dictionary>" in msg
    assert build_user_message("hello", None, []) == build_user_message("hello", None) == "<transcript>\nhello\n</transcript>"


def test_clean_sends_dictionary():
    client, messages = fake_client("Run kubectl.")
    out = Cleaner(CleanupConfig(), client=client).clean("run cube cuttle", None, [Term("kubectl", ("cube cuttle",))])
    assert out == "Run kubectl." and "<dictionary>" in messages.calls[0]["messages"][0]["content"]


def test_translate_returns_translation_or_none():
    client, messages = fake_client("Hello everyone.")
    cleaner = Cleaner(CleanupConfig(), client=client)
    assert cleaner.translate("herkese merhaba", "English", ForegroundApp("slack.exe", "x")) == "Hello everyone."
    assert "<target_language>English</target_language>" in messages.calls[0]["messages"][0]["content"]
    client, _ = fake_client("", stop_reason="refusal")
    cleaner = Cleaner(CleanupConfig(), client=client)
    assert cleaner.translate("x", "English") is None and cleaner.last_error


def test_translate_and_ask_work_with_cleanup_toggled_off():
    client, _ = fake_client(json.dumps({"kind": "answer", "text": "42"}))
    cleaner = Cleaner(CleanupConfig(enabled=False), client=client)
    assert cleaner.ask("what is six times seven").text == "42"


def test_ask_requests_json_schema_and_parses_kinds():
    client, messages = fake_client(json.dumps({"kind": "replace", "text": "Shorter."}))
    result = Cleaner(CleanupConfig(), client=client).ask("make this shorter", "A long sentence here.")
    assert (result.kind, result.text) == ("replace", "Shorter.")
    call = messages.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "medium"  # low is raised for Ask Anything
    assert "<selected_text>\nA long sentence here.\n</selected_text>" in call["messages"][0]["content"]


def test_parse_ask_response_adjusts_kind_to_selection():
    assert parse_ask_response('{"kind": "replace", "text": "x"}', "").kind == "insert"
    assert parse_ask_response('{"kind": "insert", "text": "x"}', "selected").kind == "answer"
    assert parse_ask_response('{"kind": "answer", "text": " x "}', "").text == "x"
    for bad in ("not json", '{"kind": "explode", "text": "x"}', '{"text": "x"}'):
        with pytest.raises(AskError):
            parse_ask_response(bad, "")


def test_ask_without_claude_or_on_failure_raises():
    cleaner = Cleaner(CleanupConfig(), client=SimpleNamespace())
    cleaner.client = None
    cleaner.disabled_reason = "No Claude credentials"
    with pytest.raises(AskError, match="needs Claude"):
        cleaner.ask("hi")
    client, _ = fake_client("", stop_reason="refusal")
    with pytest.raises(AskError, match="declined"):
        Cleaner(CleanupConfig(), client=client).ask("hi")


def test_client_removed_mid_dictation_falls_back_to_raw():
    client, _ = fake_client("never used")
    cleaner = Cleaner(CleanupConfig(), client=client)
    real_send = cleaner._send

    def swap_then_send(*args, **kwargs):
        cleaner.client = None  # Settings removed the key between the availability check and the request
        return real_send(*args, **kwargs)

    cleaner._send = swap_then_send
    assert cleaner.clean("raw words") == "raw words"


def test_ask_message_without_selection():
    msg = build_ask_message("write a haiku", "  ", None)
    assert "(no text selected)" in msg and "<instruction>\nwrite a haiku\n</instruction>" in msg


def test_whisper_prompt_dedupes_and_trims():
    assert whisper_prompt([]) is None
    assert whisper_prompt(["Orhun", "orhun", " kubectl "]) == "Orhun, kubectl"
    long = [f"term{i:03d}" for i in range(200)]
    prompt = whisper_prompt(long, max_chars=60)
    assert len(prompt) <= 60 and prompt.startswith("term000, term001")


def test_pick_language_chooses_likeliest_allowed():
    probs = [("az", 0.5), ("tr", 0.3), ("en", 0.15)]
    assert pick_language(probs, ["en", "tr"]) == "tr"
    assert pick_language(probs, ["en"]) == "en"
    assert pick_language(probs, ["xx"]) is None  # unknown codes leave the choice to Whisper


class FakeWhisper:
    def __init__(self, probs):
        self.probs = probs
        self.model = SimpleNamespace(is_multilingual=True)
        self.languages = []

    def detect_language(self, audio, vad_filter=False):
        return self.probs[0][0], self.probs[0][1], self.probs

    def transcribe(self, audio, language=None, **kwargs):
        self.languages.append(language)
        info = SimpleNamespace(duration=1.0, language=language or self.probs[0][0])
        return [SimpleNamespace(text=" Merhaba ")], info


def fake_transcriber(cfg, probs):
    transcriber = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)  # skip loading a real model
    transcriber.cfg, transcriber.vocabulary, transcriber.model = cfg, (lambda: []), FakeWhisper(probs)
    return transcriber


def test_transcriber_restricts_detection_to_configured_languages():
    probs = [("az", 0.6), ("tr", 0.3), ("en", 0.1)]
    assert fake_transcriber(TranscriptionConfig(languages=["en", " TR "]), probs).transcribe(None) == Transcript("Merhaba", "tr")
    unrestricted = fake_transcriber(TranscriptionConfig(), probs)
    assert unrestricted.transcribe(None).language == "az" and unrestricted.model.languages == [None]
    fixed = fake_transcriber(TranscriptionConfig(language="en", languages=["tr"]), probs)
    assert fixed.transcribe(None).language == "en"  # an explicit language wins


def test_clean_sends_spoken_language():
    client, messages = fake_client("Deploy'u yaptım.")
    Cleaner(CleanupConfig(), client=client).clean("diploy'u yaptım", None, language="tr")
    assert "<spoken_language>tr</spoken_language>" in messages.calls[0]["messages"][0]["content"]
    assert "spoken_language" not in build_user_message("hello", None)


def test_vocabulary_merges_config_then_dictionary(tmp_path):
    store = Store(tmp_path / "v.db")
    store.add_term("kubectl", "cube cuttle")
    store.add_term("Anthropic")
    terms = claude_terms(["Orhun", "anthropic"], store)
    assert [t.text for t in terms] == ["Orhun", "anthropic", "kubectl"]
    assert terms[2].sounds_like == ("cube cuttle",)
    assert whisper_terms([], None) == []
    store.close()


def test_stored_key_is_used_when_no_env_var(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "read_api_key", lambda target=credentials.TARGET: "sk-ant-stored")
    cleaner = Cleaner(CleanupConfig())
    assert cleaner.client.api_key == "sk-ant-stored" and credentials.STORE_NAME in cleaner.key_source


def test_env_var_wins_over_stored_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    monkeypatch.setattr(credentials, "read_api_key", lambda target=credentials.TARGET: "sk-ant-stored")
    cleaner = Cleaner(CleanupConfig())
    assert cleaner.client.api_key == "sk-ant-env" and cleaner.key_source == "ANTHROPIC_API_KEY"
