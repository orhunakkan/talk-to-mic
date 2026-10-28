import sqlite3

import pytest

from talk_to_mic.store import Stats, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def test_history_round_trip_and_search(store):
    a = store.add_history("dictate", "slack.exe", "general", "um hello team", "Hello team.", 2.0, created_at=100)
    b = store.add_history("ask", "chrome.exe", "Docs", "make it shorter", "Short.", 1.5, audio=b"RIFF", created_at=200)
    entries = store.search_history()
    assert [e.id for e in entries] == [b, a]  # newest first
    assert entries[0].has_audio and not entries[1].has_audio
    assert entries[1].word_count == 3  # words spoken
    assert [e.id for e in store.search_history("hel")] == [a]  # prefix match on raw and final text
    assert [e.id for e in store.search_history("short")] == [b]
    assert store.search_history("", mode="ask")[0].id == b
    assert store.search_history('"unbalanced (query*') == []  # no FTS syntax errors
    assert store.history_audio(b) == b"RIFF"
    store.delete_history(a)
    assert [e.id for e in store.search_history("hello")] == []


def test_prune_and_clear(store):
    store.add_history("dictate", "", "", "old", "old", 1, created_at=1_000)
    store.add_history("dictate", "", "", "new", "new", 1, created_at=1_000_000)
    assert store.prune_history(0) == 0
    assert store.prune_history(1, now=1_000_000 + 3600) == 1
    assert [e.raw_text for e in store.search_history()] == ["new"]
    store.clear_history()
    assert store.search_history() == []


def test_stats(store):
    store.add_history("dictate", "", "", "one two three four", "x", 6.0, created_at=50)
    store.add_history("translate", "", "", "five six", "y", 6.0, created_at=150)
    assert store.stats() == Stats(words=6, dictations=2, audio_s=12.0)
    assert store.stats(since=100) == Stats(words=2, dictations=1, audio_s=6.0)
    stats = Stats(words=400, dictations=10, audio_s=180)
    assert stats.speaking_wpm == pytest.approx(133.3, abs=0.1)
    assert stats.minutes_saved(40) == pytest.approx(7.0)  # 10 min typing - 3 min speaking
    assert Stats(10, 1, 600).minutes_saved(40) == 0  # never negative


def test_dictionary(store):
    first = store.add_term("kubectl", "cube cuttle, kube control")
    store.add_term("Orhun", [], source="history")
    assert [e.term for e in store.dictionary()] == ["Orhun", "kubectl"]  # newest first
    assert store.add_term("KUBECTL", "cube cuddle") == first  # same term (case-insensitive) is updated
    entry = next(e for e in store.dictionary() if e.id == first)
    assert entry.sounds_like == ["cube cuddle"]
    with pytest.raises(ValueError):
        store.add_term("  ")
    with pytest.raises(ValueError):
        store.update_term(first, "orhun", "")
    store.update_term(first, "kubectl", "")
    store.delete_term(first)
    assert [e.term for e in store.dictionary()] == ["Orhun"]


def test_history_language_and_upgrade_from_version_1(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)  # a version 1 history table, without the language column
    db.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, created_at REAL NOT NULL, mode TEXT NOT NULL,"
               " process_name TEXT NOT NULL DEFAULT '', window_title TEXT NOT NULL DEFAULT '',"
               " raw_text TEXT NOT NULL DEFAULT '', final_text TEXT NOT NULL DEFAULT '',"
               " duration_s REAL NOT NULL DEFAULT 0, word_count INTEGER NOT NULL DEFAULT 0, audio BLOB)")
    db.execute("INSERT INTO history (created_at, mode) VALUES (1, 'dictate')")
    db.commit()
    db.close()
    store = Store(path)
    store.add_history("dictate", "", "", "merhaba", "Merhaba.", 1, created_at=2, language="tr")
    assert [e.language for e in store.search_history()] == ["tr", ""]
    store.close()
