"""Entry point: `pythonw -m talk_to_mic` runs the tray app.

`python -m talk_to_mic --transcribe FILE` transcribes (and cleans up) an audio file and
prints the result, which is handy for checking the speech model and Claude settings.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import APP_DIR, CONFIG_PATH, DB_PATH, LOG_PATH, load_config

# Model downloads work without symlinks on Windows; the warning is just noise.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def setup_logging(verbose: bool) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:  # None under pythonw
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "huggingface_hub", "faster_whisper", "PIL", "tomlkit"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def transcribe_file(path: str) -> int:
    from .audio import load_wav
    from .cleanup import Cleaner
    from .store import Store
    from .transcriber import create_transcriber
    from .vocabulary import claude_terms

    cfg = load_config()
    store = Store(DB_PATH) if DB_PATH.exists() else None
    terms = claude_terms(cfg.transcription.vocabulary, store)
    raw = create_transcriber(cfg.transcription, lambda: [t.text for t in terms]).transcribe(load_wav(path))
    print(f"raw:     {raw}")
    cleaner = Cleaner(cfg.cleanup)
    if cleaner.available:
        print(f"cleaned: {cleaner.clean(raw, dictionary=terms)}")
    else:
        print("cleaned: (Claude cleanup unavailable - set ANTHROPIC_API_KEY)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="talk_to_mic", description="Hold a key, speak, release to paste.")
    parser.add_argument("--transcribe", metavar="FILE", help="transcribe a 16-bit WAV file and print the text")
    parser.add_argument("-v", "--verbose", action="store_true", help="log transcripts (debug level)")
    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.transcribe:
        return transcribe_file(args.transcribe)

    from .app import App
    from .win32 import acquire_single_instance, signal_open_event

    mutex = acquire_single_instance()
    if mutex is None:
        logging.getLogger(__name__).warning("talk-to-mic is already running; showing its window")
        signal_open_event()
        return 1
    first_run = not CONFIG_PATH.exists()
    App(load_config(), first_run=first_run).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
