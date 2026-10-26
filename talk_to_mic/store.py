"""Local SQLite database (%APPDATA%\\talk-to-mic\\talk-to-mic.db): dictation history and the
personal dictionary. Nothing in it leaves the machine.

One connection guarded by a lock is shared by the worker thread (writes) and the UI (reads).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    mode TEXT NOT NULL,
    process_name TEXT NOT NULL DEFAULT '',
    window_title TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    final_text TEXT NOT NULL DEFAULT '',
    duration_s REAL NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    audio BLOB
);
CREATE INDEX IF NOT EXISTS history_created_at ON history(created_at);
CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
    raw_text, final_text, content='history', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS history_ai AFTER INSERT ON history BEGIN
    INSERT INTO history_fts(rowid, raw_text, final_text) VALUES (new.id, new.raw_text, new.final_text);
END;
CREATE TRIGGER IF NOT EXISTS history_ad AFTER DELETE ON history BEGIN
    INSERT INTO history_fts(history_fts, rowid, raw_text, final_text)
    VALUES ('delete', old.id, old.raw_text, old.final_text);
END;
CREATE TABLE IF NOT EXISTS dictionary (
    id INTEGER PRIMARY KEY,
    term TEXT NOT NULL UNIQUE COLLATE NOCASE,
    sounds_like TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at REAL NOT NULL
);
"""

MODES = ("dictate", "ask", "translate")


@dataclass
class HistoryEntry:
    id: int
    created_at: float
    mode: str
    process_name: str
    window_title: str
    raw_text: str
    final_text: str
    duration_s: float
    word_count: int
    has_audio: bool


@dataclass
class DictionaryEntry:
    id: int
    term: str
    sounds_like: list[str]
    source: str
    created_at: float


@dataclass
class Stats:
    words: int
    dictations: int
    audio_s: float

    def minutes_saved(self, typing_wpm: int) -> float:
        """Typing time for the same words, minus the time spent speaking them."""
        if typing_wpm <= 0:
            return 0.0
        return max(0.0, self.words / typing_wpm - self.audio_s / 60)

    @property
    def speaking_wpm(self) -> float:
        return self.words / (self.audio_s / 60) if self.audio_s > 0 else 0.0


def count_words(text: str) -> int:
    return len(text.split())


def split_sounds_like(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _fts_query(text: str) -> str:
    # Quote every word and match prefixes, so user input can't produce FTS syntax errors.
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    return " ".join(f'"{w}"*' for w in words)


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _exec(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._db.execute(sql, params).lastrowid

    # --- history -------------------------------------------------------------

    def add_history(self, mode: str, process_name: str, window_title: str, raw_text: str, final_text: str,
                    duration_s: float, audio: bytes | None = None, created_at: float | None = None) -> int:
        return self._exec(
            "INSERT INTO history (created_at, mode, process_name, window_title, raw_text, final_text,"
            " duration_s, word_count, audio) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (created_at or time.time(), mode, process_name, window_title, raw_text, final_text,
             duration_s, count_words(raw_text), audio),
        )

    def search_history(self, query: str = "", mode: str = "", limit: int = 500) -> list[HistoryEntry]:
        where, params = [], []
        fts = _fts_query(query)
        if fts:
            where.append("h.id IN (SELECT rowid FROM history_fts WHERE history_fts MATCH ?)")
            params.append(fts)
        if mode:
            where.append("h.mode = ?")
            params.append(mode)
        sql = (
            "SELECT h.id, h.created_at, h.mode, h.process_name, h.window_title, h.raw_text, h.final_text,"
            " h.duration_s, h.word_count, h.audio IS NOT NULL AS has_audio FROM history h"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY h.created_at DESC, h.id DESC LIMIT ?"
        )
        rows = self._query(sql, (*params, limit))
        return [HistoryEntry(**{k: row[k] for k in row.keys()}) for row in rows]

    def history_audio(self, entry_id: int) -> bytes | None:
        rows = self._query("SELECT audio FROM history WHERE id = ?", (entry_id,))
        return rows[0]["audio"] if rows else None

    def delete_history(self, entry_id: int) -> None:
        self._exec("DELETE FROM history WHERE id = ?", (entry_id,))

    def clear_history(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM history")
            self._db.execute("VACUUM")

    def prune_history(self, retention_days: int, now: float | None = None) -> int:
        if retention_days <= 0:
            return 0
        cutoff = (now or time.time()) - retention_days * 86400
        with self._lock:
            return self._db.execute("DELETE FROM history WHERE created_at < ?", (cutoff,)).rowcount

    def stats(self, since: float | None = None) -> Stats:
        row = self._query(
            "SELECT COALESCE(SUM(word_count), 0) AS words, COUNT(*) AS n, COALESCE(SUM(duration_s), 0) AS secs"
            " FROM history WHERE created_at >= ?",
            (since or 0,),
        )[0]
        return Stats(int(row["words"]), int(row["n"]), float(row["secs"]))

    # --- dictionary ----------------------------------------------------------

    def dictionary(self) -> list[DictionaryEntry]:
        rows = self._query("SELECT * FROM dictionary ORDER BY created_at DESC, id DESC")
        return [
            DictionaryEntry(r["id"], r["term"], split_sounds_like(r["sounds_like"]), r["source"], r["created_at"])
            for r in rows
        ]

    def add_term(self, term: str, sounds_like: list[str] | str = (), source: str = "manual") -> int:
        """Adds a term, or updates its sounds-like list if it is already there."""
        term = term.strip()
        if not term:
            raise ValueError("The term is empty")
        if isinstance(sounds_like, str):
            sounds_like = split_sounds_like(sounds_like)
        joined = ", ".join(sounds_like)
        with self._lock:
            row = self._db.execute("SELECT id FROM dictionary WHERE term = ?", (term,)).fetchone()
            if row:
                self._db.execute("UPDATE dictionary SET term = ?, sounds_like = ? WHERE id = ?", (term, joined, row["id"]))
                return row["id"]
            return self._db.execute(
                "INSERT INTO dictionary (term, sounds_like, source, created_at) VALUES (?, ?, ?, ?)",
                (term, joined, source, time.time()),
            ).lastrowid

    def update_term(self, entry_id: int, term: str, sounds_like: list[str] | str) -> None:
        term = term.strip()
        if not term:
            raise ValueError("The term is empty")
        if isinstance(sounds_like, str):
            sounds_like = split_sounds_like(sounds_like)
        try:
            self._exec("UPDATE dictionary SET term = ?, sounds_like = ? WHERE id = ?",
                       (term, ", ".join(sounds_like), entry_id))
        except sqlite3.IntegrityError:
            raise ValueError(f"{term!r} is already in the dictionary") from None

    def delete_term(self, entry_id: int) -> None:
        self._exec("DELETE FROM dictionary WHERE id = ?", (entry_id,))
