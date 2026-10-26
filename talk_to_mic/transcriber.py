"""Speech-to-text engines: local faster-whisper (CPU) or a whisper.cpp server (GPU builds)."""

from __future__ import annotations

import io
import json
import logging
import uuid
import urllib.request
import wave
from typing import Callable, Protocol

import numpy as np

from .audio import SAMPLE_RATE
from .config import TranscriptionConfig

log = logging.getLogger(__name__)

# Recordings quieter than this peak level are treated as silence; Whisper tends to
# invent text ("Thank you.") for silent input.
SILENCE_PEAK = 0.01

# Whisper reads at most ~224 tokens of prompt; about 600 characters stays safely inside that.
PROMPT_MAX_CHARS = 600

VocabularyProvider = Callable[[], list[str]]


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> str: ...


def is_silent(audio: np.ndarray) -> bool:
    return audio.size == 0 or float(np.max(np.abs(audio))) < SILENCE_PEAK


def whisper_prompt(terms: list[str], max_chars: int = PROMPT_MAX_CHARS) -> str | None:
    """Comma-separated spellings for Whisper's initial prompt, in priority order, trimmed to fit."""
    seen, kept, length = set(), [], 0
    for term in terms:
        term = term.strip()
        if not term or term.lower() in seen:
            continue
        extra = len(term) + (2 if kept else 0)
        if length + extra > max_chars:
            break
        seen.add(term.lower())
        kept.append(term)
        length += extra
    return ", ".join(kept) or None


class FasterWhisperTranscriber:
    def __init__(self, cfg: TranscriptionConfig, vocabulary: VocabularyProvider | None = None):
        from faster_whisper import WhisperModel

        self.cfg = cfg
        self.vocabulary = vocabulary or (lambda: cfg.vocabulary)
        try:  # use the cached copy without touching the network
            self.model = WhisperModel(cfg.model, device="cpu", compute_type="int8", local_files_only=True)
        except Exception:
            log.info("Downloading Whisper model %r (one-time)", cfg.model)
            self.model = WhisperModel(cfg.model, device="cpu", compute_type="int8")
        log.info("Whisper model %r loaded on CPU", cfg.model)

    def transcribe(self, audio: np.ndarray) -> str:
        segments, info = self.model.transcribe(
            audio,
            language=self.cfg.language or None,
            beam_size=self.cfg.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=whisper_prompt(self.vocabulary()),
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        log.info("Transcribed %.1fs of %s audio", info.duration, info.language)
        return text


class WhisperServerTranscriber:
    """Client for whisper.cpp's `whisper-server` /inference endpoint."""

    def __init__(self, cfg: TranscriptionConfig, vocabulary: VocabularyProvider | None = None):
        self.cfg = cfg
        self.vocabulary = vocabulary or (lambda: cfg.vocabulary)

    def transcribe(self, audio: np.ndarray) -> str:
        boundary = uuid.uuid4().hex
        fields = {"response_format": "json", "temperature": "0.0"}
        if self.cfg.language:
            fields["language"] = self.cfg.language
        if prompt := whisper_prompt(self.vocabulary()):
            fields["prompt"] = prompt
        body = io.BytesIO()
        for name, value in fields.items():
            body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        body.write(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n".encode()
        )
        body.write(to_wav(audio))
        body.write(f"\r\n--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            self.cfg.server_url,
            data=body.getvalue(),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())["text"].strip()


def to_wav(audio: np.ndarray) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def create_transcriber(cfg: TranscriptionConfig, vocabulary: VocabularyProvider | None = None) -> Transcriber:
    """vocabulary returns the spellings to prime Whisper with (default: cfg.vocabulary)."""
    if cfg.engine == "whisper_server":
        return WhisperServerTranscriber(cfg, vocabulary)
    if cfg.engine != "faster_whisper":
        log.warning("Unknown transcription engine %r; using faster_whisper", cfg.engine)
    return FasterWhisperTranscriber(cfg, vocabulary)
