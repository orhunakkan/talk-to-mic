"""Microphone capture (16 kHz mono float32, what Whisper expects) and feedback tones."""

from __future__ import annotations

import logging
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000


def find_input_device(name_part: str) -> int | None:
    """Index of the first input device whose name contains name_part; None = system default."""
    if not name_part:
        return None
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and name_part.lower() in device["name"].lower():
            return index
    log.warning("No input device matching %r; using the default microphone", name_part)
    return None


class Recorder:
    def __init__(self, device_name: str = "", max_seconds: int = 300):
        self.device = find_input_device(device_name)
        self.max_samples = max_seconds * SAMPLE_RATE
        self._chunks: list[np.ndarray] = []
        self._samples = 0
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self.level = 0.0  # RMS of the latest audio block, for level meters

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            self._chunks, self._samples = [], 0
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=self.device,
                callback=self._on_audio,
            )
            self._stream.start()

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured audio (empty if nothing was recorded)."""
        with self._lock:
            stream, self._stream = self._stream, None
            if stream is not None:
                stream.stop()
                stream.close()
            chunks, self._chunks = self._chunks, []
            self.level = 0.0
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)[:, 0]

    def _on_audio(self, data: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            log.debug("Audio status: %s", status)
        self.level = float(np.sqrt(np.mean(np.square(data))))
        if self._samples < self.max_samples:
            self._chunks.append(data.copy())
            self._samples += frames


def load_wav(path: str) -> np.ndarray:
    """Read a 16-bit PCM WAV file as 16 kHz mono float32."""
    import wave

    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("Only 16-bit PCM WAV files are supported")
        rate, channels = w.getframerate(), w.getnchannels()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        positions = np.arange(0, samples.size, rate / SAMPLE_RATE)
        samples = np.interp(positions, np.arange(samples.size), samples).astype(np.float32)
    return samples


def _tone(freqs: list[float], seconds: float = 0.06, volume: float = 0.15) -> np.ndarray:
    rate = 44_100
    t = np.arange(int(rate * seconds)) / rate
    wave = np.concatenate([np.sin(2 * np.pi * f * t) for f in freqs])
    fade = np.minimum(1.0, np.minimum(np.arange(wave.size), np.arange(wave.size)[::-1]) / 200)
    return (wave * fade * volume).astype(np.float32)


START_TONE = _tone([660, 880])
STOP_TONE = _tone([880, 660])


def beep(tone: np.ndarray) -> None:
    try:
        sd.play(tone, 44_100, blocking=False)
    except Exception:
        log.debug("Could not play tone", exc_info=True)
