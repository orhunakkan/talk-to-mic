"""Merges transcription.vocabulary from config.toml with the personal dictionary."""

from __future__ import annotations

import logging

from .cleanup import Term
from .store import Store

log = logging.getLogger(__name__)


def _dictionary(store: Store | None):
    if store is None:
        return []
    try:
        return store.dictionary()
    except Exception:
        log.exception("Could not read the personal dictionary")
        return []


def claude_terms(config_vocabulary: list[str], store: Store | None) -> list[Term]:
    """config.toml entries first, then dictionary entries (newest first), without duplicates."""
    terms, seen = [], set()
    candidates = [Term(v) for v in config_vocabulary] + [Term(e.term, tuple(e.sounds_like)) for e in _dictionary(store)]
    for term in candidates:
        key = term.text.strip().lower()
        if key and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def whisper_terms(config_vocabulary: list[str], store: Store | None) -> list[str]:
    return [t.text for t in claude_terms(config_vocabulary, store)]
