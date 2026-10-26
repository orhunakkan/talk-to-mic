"""The main window: Home (statistics), History, Dictionary and Settings."""

from __future__ import annotations

import copy
import datetime as dt
import logging
import os
import sys
import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..cleanup import EFFORT_LEVELS, check_api_key
from ..config import CONFIG_PATH, Config
from ..hotkey import COMBO_KEYS, HOTKEYS, parse_chord
from ..native import credentials
from ..store import HistoryEntry
from ..tray import open_file as tray_open_file

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)

PAGES = ("home", "history", "dictionary", "settings")
MODE_LABELS = {"dictate": "Dictate", "ask": "Ask", "translate": "Translate"}
CLAUDE_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
LANGUAGES = ["English", "Turkish", "German", "French", "Spanish", "Italian", "Portuguese", "Dutch",
             "Russian", "Arabic", "Chinese (Simplified)", "Japanese", "Korean"]


# --- helpers -------------------------------------------------------------------

def key_label(name: str) -> str:
    """"right_shift" -> "Right Shift", "f13" -> "F13"."""
    return " ".join(part.upper() if part[:1] == "f" and part[1:].isdigit() else part.capitalize()
                    for part in name.split("_"))


def chord_label(text: str) -> str:
    return " + ".join(key_label(p.strip()) for p in text.split("+") if p.strip())


def format_minutes(minutes: float) -> str:
    minutes = round(minutes)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min" if minutes % 60 else f"{minutes // 60} h"


def format_when(timestamp: float, now: float | None = None) -> str:
    when = dt.datetime.fromtimestamp(timestamp)
    today = dt.datetime.fromtimestamp(now or time.time()).date()
    if when.date() == today:
        return when.strftime("%H:%M")
    if when.date() == today - dt.timedelta(days=1):
        return "Yesterday " + when.strftime("%H:%M")
    return when.strftime("%d %b %Y %H:%M")


def start_of_today(now: float | None = None) -> float:
    d = dt.datetime.fromtimestamp(now or time.time())
    return dt.datetime(d.year, d.month, d.day).timestamp()


def app_name(process_name: str) -> str:
    """"OUTLOOK.EXE" -> "OUTLOOK"."""
    return process_name[:-4] if process_name.lower().endswith(".exe") else process_name or "?"


def one_line(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def heading(text: str, size_delta: float = 7) -> QLabel:
    label = QLabel(text)
    font = QFont(label.font())
    font.setPointSizeF(font.pointSizeF() + size_delta)
    font.setWeight(QFont.Weight.DemiBold)
    label.setFont(font)
    return label


def muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)  # follows theme changes
    return label


def item(text: str, data=None) -> QTableWidgetItem:
    cell = QTableWidgetItem(text)
    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if data is not None:
        cell.setData(Qt.ItemDataRole.UserRole, data)
    return cell


def make_table(headers: list[str], stretch_column: int) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    for col in range(len(headers)):
        mode = QHeaderView.ResizeMode.Stretch if col == stretch_column else QHeaderView.ResizeMode.ResizeToContents
        header.setSectionResizeMode(col, mode)
    return table


# --- Home ----------------------------------------------------------------------

class StatCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("statCard")
        self.setStyleSheet("#statCard { background: palette(base); border: 1px solid palette(midlight);"
                           " border-radius: 10px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(2)
        layout.addWidget(muted(title))
        self.words = heading("0", 12)
        layout.addWidget(self.words)
        layout.addWidget(muted("words dictated"))
        layout.addSpacing(6)
        self.detail = QLabel()
        layout.addWidget(self.detail)
        self.saved = QLabel()
        layout.addWidget(self.saved)

    def set_values(self, words: int, dictations: int, minutes_saved: float) -> None:
        self.words.setText(f"{words:,}")
        self.detail.setText(f"{dictations:,} dictation{'s' if dictations != 1 else ''}")
        self.saved.setText(f"≈ {format_minutes(minutes_saved)} saved")


class HomePage(QWidget):
    def __init__(self, app: App, open_entry):
        super().__init__()
        self.app = app
        self._open_entry = open_entry
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(heading("Home"))
        self.shortcuts = muted()
        layout.addWidget(self.shortcuts)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("background: rgba(245,166,35,0.18); border-radius: 8px; padding: 8px 12px;")
        layout.addWidget(self.notice)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.cards = {key: StatCard(title) for key, title in
                      (("today", "Today"), ("week", "Last 7 days"), ("all", "All time"))}
        for card in self.cards.values():
            cards.addWidget(card)
        layout.addLayout(cards)
        self.speed = muted()
        layout.addWidget(self.speed)

        layout.addSpacing(6)
        layout.addWidget(heading("Recent", 2))
        self.recent = make_table(["When", "App", "Mode", "Text"], 3)
        self.recent.itemDoubleClicked.connect(self._open)
        layout.addWidget(self.recent, 1)

    def refresh(self) -> None:
        cfg = self.app.cfg
        parts = [f"Hold {key_label(cfg.hotkey.key)} to dictate"]
        if cfg.hotkey.ask_combo:
            parts.append(f"add {key_label(cfg.hotkey.ask_combo)} to Ask Anything")
        if cfg.hotkey.translate_combo:
            parts.append(f"add {key_label(cfg.hotkey.translate_combo)} to translate into "
                         f"{cfg.translation.target_language}")
        if cfg.hotkey.paste_last:
            parts.append(f"{chord_label(cfg.hotkey.paste_last)} pastes the last result")
        self.shortcuts.setText(" · ".join(parts) + ".")

        notices = []
        if self.app.cleaner.client is None:
            notices.append("Claude is not set up, so raw transcripts are pasted and Ask Anything and "
                           "Translate are unavailable. Add an API key in Settings.")
        if not cfg.history.enabled:
            notices.append("History is off, so new dictations are not counted here.")
        if self.app.store is None:
            notices.append("The history database could not be opened; see the log.")
        self.notice.setText(" ".join(notices))
        self.notice.setVisible(bool(notices))

        store = self.app.store
        if store is None:
            return
        now = time.time()
        wpm = cfg.ui.typing_wpm
        periods = {"today": start_of_today(now), "week": now - 7 * 86400, "all": None}
        for key, since in periods.items():
            stats = store.stats(since)
            self.cards[key].set_values(stats.words, stats.dictations, stats.minutes_saved(wpm))
        overall = store.stats()
        self.speed.setText(
            f"You speak at about {overall.speaking_wpm:.0f} words per minute; time saved compares that "
            f"with typing at {wpm} words per minute." if overall.audio_s else
            "Your statistics appear here after your first dictation."
        )
        entries = store.search_history(limit=8)
        self.recent.setRowCount(len(entries))
        for row, e in enumerate(entries):
            self.recent.setItem(row, 0, item(format_when(e.created_at, now), e.id))
            self.recent.setItem(row, 1, item(app_name(e.process_name)))
            self.recent.setItem(row, 2, item(MODE_LABELS.get(e.mode, e.mode)))
            self.recent.setItem(row, 3, item(one_line(e.final_text or e.raw_text)))

    def _open(self, cell: QTableWidgetItem) -> None:
        entry_id = self.recent.item(cell.row(), 0).data(Qt.ItemDataRole.UserRole)
        self._open_entry(entry_id)


# --- Dictionary ----------------------------------------------------------------

class TermDialog(QDialog):
    def __init__(self, parent: QWidget, title: str, term: str = "", sounds_like: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        form = QFormLayout(self)
        self.term = QLineEdit(term)
        self.term.setPlaceholderText("e.g. kubectl")
        self.sounds_like = QLineEdit(sounds_like)
        self.sounds_like.setPlaceholderText("optional, comma separated, e.g. cube cuttle, kube control")
        form.addRow("Spelling", self.term)
        form.addRow("Sounds like", self.sounds_like)
        form.addRow(muted("The speech model is primed with the spelling, and Claude replaces "
                          "the sounds-like phrases with it."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        if not self.term.text().strip():
            self.term.setFocus()
            return
        self.accept()


class DictionaryPage(QWidget):
    def __init__(self, app: App):
        super().__init__()
        self.app = app
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(heading("Dictionary"))
        layout.addWidget(muted("Names, jargon and spellings talk-to-mic should always get right. "
                               "They are used by the speech model and by Claude in every mode."))
        buttons = QHBoxLayout()
        add = QPushButton("Add word…")
        add.clicked.connect(lambda: self.add())
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(self.edit)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete)
        for b in (add, self.edit_button, self.delete_button):
            buttons.addWidget(b)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.table = make_table(["Spelling", "Sounds like", "Added from"], 1)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _: self.edit())
        layout.addWidget(self.table, 1)
        self.empty = muted()
        layout.addWidget(self.empty)

    def refresh(self) -> None:
        store = self.app.store
        entries = store.dictionary() if store else []
        config_terms = [v for v in self.app.cfg.transcription.vocabulary if v.strip()]
        self.table.setRowCount(len(entries) + len(config_terms))
        for row, e in enumerate(entries):
            self.table.setItem(row, 0, item(e.term, e.id))
            self.table.setItem(row, 1, item(", ".join(e.sounds_like)))
            self.table.setItem(row, 2, item({"history": "History", "manual": "You"}.get(e.source, e.source)))
        for offset, term in enumerate(config_terms):
            row = len(entries) + offset
            self.table.setItem(row, 0, item(term, None))
            self.table.setItem(row, 1, item(""))
            self.table.setItem(row, 2, item("config.toml (edit the file to change)"))
        if store is None:
            self.empty.setText("The dictionary database could not be opened; see the log.")
        else:
            self.empty.setText("" if entries or config_terms else "No words yet. Add names and terms "
                               "the speech model gets wrong, or add them from the History page.")
        self._selection_changed()

    def _selected_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _selection_changed(self) -> None:
        editable = self._selected_id() is not None
        self.edit_button.setEnabled(editable)
        self.delete_button.setEnabled(editable)

    def add(self, term: str = "", source: str = "manual") -> None:
        if self.app.store is None:
            return
        dialog = TermDialog(self, "Add to dictionary", term)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.app.store.add_term(dialog.term.text(), dialog.sounds_like.text(), source)
            self.refresh()

    def edit(self) -> None:
        entry_id = self._selected_id()
        if entry_id is None:
            return
        row = self.table.selectionModel().selectedRows()[0].row()
        dialog = TermDialog(self, "Edit word", self.table.item(row, 0).text(), self.table.item(row, 1).text())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.app.store.update_term(entry_id, dialog.term.text(), dialog.sounds_like.text())
            except ValueError as exc:
                QMessageBox.warning(self, "Dictionary", str(exc))
            self.refresh()

    def delete(self) -> None:
        entry_id = self._selected_id()
        if entry_id is not None:
            self.app.store.delete_term(entry_id)
            self.refresh()


# --- History -------------------------------------------------------------------

class HistoryPage(QWidget):
    def __init__(self, app: App, add_term):
        super().__init__()
        self.app = app
        self._add_term = add_term
        self._entries: list[HistoryEntry] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(heading("History"))
        self.notice = muted()
        layout.addWidget(self.notice)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search history")
        self.search.setClearButtonEnabled(True)
        self.mode = QComboBox()
        self.mode.addItem("All modes", "")
        for key, label in MODE_LABELS.items():
            self.mode.addItem(label, key)
        clear = QPushButton("Clear history…")
        clear.clicked.connect(self._clear)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.mode)
        bar.addWidget(clear)
        layout.addLayout(bar)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        self.mode.currentIndexChanged.connect(lambda _: self.refresh())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = make_table(["When", "App", "Mode", "Text"], 3)
        self.table.itemSelectionChanged.connect(self._show_selected)
        splitter.addWidget(self.table)

        detail = QWidget()
        d = QVBoxLayout(detail)
        d.setContentsMargins(12, 0, 0, 0)
        self.meta = muted()
        d.addWidget(self.meta)
        d.addWidget(QLabel("Result"))
        self.final = QPlainTextEdit()
        self.final.setReadOnly(True)
        d.addWidget(self.final, 3)
        d.addWidget(QLabel("What you said"))
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        d.addWidget(self.raw, 2)
        actions = QGridLayout()
        self.copy_button = QPushButton("Copy result")
        self.copy_button.clicked.connect(self._copy)
        self.term_button = QPushButton("Add to dictionary…")
        self.term_button.setToolTip("Select a word in the result first to prefill it")
        self.term_button.clicked.connect(self._add_to_dictionary)
        self.audio_button = QPushButton("Save audio…")
        self.audio_button.clicked.connect(self._save_audio)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete)
        actions.addWidget(self.copy_button, 0, 0)
        actions.addWidget(self.term_button, 0, 1)
        actions.addWidget(self.audio_button, 1, 0)
        actions.addWidget(self.delete_button, 1, 1)
        d.addLayout(actions)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self._show_selected()

    def refresh(self, select_id: int | None = None) -> None:
        store = self.app.store
        if store is None:
            self.notice.setText("The history database could not be opened; see the log.")
            return
        notes = [] if self.app.cfg.history.enabled else ["History is off: new dictations are not saved "
                                                         "(turn it on in Settings)."]
        if self.app.cfg.history.retention_days:
            notes.append(f"Entries older than {self.app.cfg.history.retention_days} days are deleted.")
        self.notice.setText(" ".join(notes) or "Stored only on this computer.")
        current = select_id if select_id is not None else self._selected_entry_id()
        self._entries = store.search_history(self.search.text(), self.mode.currentData() or "")
        now = time.time()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._entries))
        for row, e in enumerate(self._entries):
            self.table.setItem(row, 0, item(format_when(e.created_at, now), e.id))
            self.table.setItem(row, 1, item(app_name(e.process_name)))
            self.table.setItem(row, 2, item(MODE_LABELS.get(e.mode, e.mode)))
            self.table.setItem(row, 3, item(one_line(e.final_text or e.raw_text)))
            if e.id == current:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self._show_selected()

    def _selected_entry_id(self) -> int | None:
        entry = self._selected()
        return entry.id if entry else None

    def _selected(self) -> HistoryEntry | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._entries):
            return None
        return self._entries[rows[0].row()]

    def _show_selected(self) -> None:
        entry = self._selected()
        for button in (self.copy_button, self.term_button, self.delete_button):
            button.setEnabled(entry is not None)
        self.audio_button.setEnabled(bool(entry and entry.has_audio))
        if entry is None:
            self.meta.setText("Select an entry to see it in full.")
            self.final.clear()
            self.raw.clear()
            return
        when = dt.datetime.fromtimestamp(entry.created_at).strftime("%d %b %Y, %H:%M:%S")
        where = entry.process_name or "unknown app"
        if entry.window_title:
            where += f" ({one_line(entry.window_title, 60)})"
        self.meta.setText(f"{MODE_LABELS.get(entry.mode, entry.mode)} · {when} · {where} · "
                          f"{entry.duration_s:.1f} s · {entry.word_count} words")
        self.final.setPlainText(entry.final_text)
        self.raw.setPlainText(entry.raw_text)

    def _copy(self) -> None:
        entry = self._selected()
        if entry:
            QGuiApplication.clipboard().setText(entry.final_text or entry.raw_text)

    def _add_to_dictionary(self) -> None:
        selected = self.final.textCursor().selectedText() or self.raw.textCursor().selectedText()
        self._add_term(selected.strip(), "history")

    def _save_audio(self) -> None:
        entry = self._selected()
        audio = self.app.store.history_audio(entry.id) if entry else None
        if not audio:
            return
        name = dt.datetime.fromtimestamp(entry.created_at).strftime("talk-to-mic-%Y%m%d-%H%M%S.wav")
        path, _ = QFileDialog.getSaveFileName(self, "Save audio", os.path.join(os.path.expanduser("~"), name),
                                              "WAV audio (*.wav)")
        if path:
            with open(path, "wb") as f:
                f.write(audio)

    def _delete(self) -> None:
        entry = self._selected()
        if entry:
            self.app.store.delete_history(entry.id)
            self.refresh()

    def _clear(self) -> None:
        if self.app.store is None:
            return
        answer = QMessageBox.question(self, "Clear history", "Delete every history entry? This can't be undone.")
        if answer == QMessageBox.StandardButton.Yes:
            self.app.store.clear_history()
            self.refresh()

    def select(self, entry_id: int) -> None:
        self.search.clear()
        self.mode.setCurrentIndex(0)
        self.refresh(select_id=entry_id)


# --- Settings ------------------------------------------------------------------

class _KeyCheck(QObject):
    finished = Signal(str)  # "" = the key works


class SettingsPage(QWidget):
    def __init__(self, app: App, on_saved):
        super().__init__()
        self.app = app
        self._on_saved = on_saved
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 12)
        layout.setSpacing(14)
        layout.addWidget(heading("Settings"))

        # Claude
        claude = QGroupBox("Claude")
        f = QFormLayout(claude)
        self.key_status = QLabel()
        self.key_status.setWordWrap(True)
        f.addRow("API key", self.key_status)
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-ant-…")
        self.key_test = QPushButton("Test")
        self.key_test.clicked.connect(self._test_key)
        self.key_save = QPushButton("Save key")
        self.key_save.clicked.connect(self._save_key)
        self.key_remove = QPushButton("Remove saved key")
        self.key_remove.clicked.connect(self._remove_key)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.key_test)
        key_row.addWidget(self.key_save)
        key_row.addWidget(self.key_remove)
        f.addRow("", key_row)
        self.key_result = muted()
        f.addRow("", self.key_result)
        self.cleanup_enabled = QCheckBox("Clean up dictation with Claude (filler words, punctuation, lists)")
        f.addRow("", self.cleanup_enabled)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(CLAUDE_MODELS)
        f.addRow("Model", self.model)
        self.effort = QComboBox()
        self.effort.addItems(EFFORT_LEVELS)
        f.addRow("Effort", self.effort)
        self.send_context = QCheckBox("Send the focused app's name and window title, to match its style")
        f.addRow("", self.send_context)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(3, 120)
        self.timeout.setSuffix(" s")
        f.addRow("Timeout", self.timeout)
        layout.addWidget(claude)

        # Shortcuts
        keys = QGroupBox("Shortcuts")
        f = QFormLayout(keys)
        self.hotkey = QComboBox()
        for name in HOTKEYS:
            self.hotkey.addItem(key_label(name), name)
        f.addRow("Hold to dictate", self.hotkey)
        self.min_hold = QSpinBox()
        self.min_hold.setRange(50, 2000)
        self.min_hold.setSuffix(" ms")
        f.addRow("Ignore taps shorter than", self.min_hold)
        self.ask_combo = self._combo_box()
        f.addRow("…plus this for Ask Anything", self.ask_combo)
        self.translate_combo = self._combo_box()
        f.addRow("…plus this to translate", self.translate_combo)
        self.paste_last = QLineEdit()
        self.paste_last.setPlaceholderText("e.g. left_ctrl+right_shift+v; empty turns it off")
        f.addRow("Paste last result", self.paste_last)
        f.addRow("", muted("If your keyboard layout types a character with AltGr + Space, set Ask Anything "
                           "to Off or to another key."))
        layout.addWidget(keys)

        # Microphone
        mic = QGroupBox("Microphone and sounds")
        f = QFormLayout(mic)
        self.device = QComboBox()
        f.addRow("Microphone", self.device)
        self.beep = QCheckBox("Play a short tone when recording starts and stops")
        f.addRow("", self.beep)
        self.max_seconds = QSpinBox()
        self.max_seconds.setRange(10, 3600)
        self.max_seconds.setSuffix(" s")
        f.addRow("Longest recording", self.max_seconds)
        layout.addWidget(mic)

        # Speech recognition
        speech = QGroupBox("Speech recognition")
        f = QFormLayout(speech)
        self.engine = QComboBox()
        self.engine.addItem("faster-whisper on this PC's CPU", "faster_whisper")
        self.engine.addItem("whisper.cpp server (GPU builds)", "whisper_server")
        f.addRow("Engine", self.engine)
        self.whisper_model = QComboBox()
        self.whisper_model.setEditable(True)
        self.whisper_model.addItems(WHISPER_MODELS)
        f.addRow("Model", self.whisper_model)
        self.language = QLineEdit()
        self.language.setPlaceholderText("auto-detect (or a code such as en, tr)")
        f.addRow("Language", self.language)
        self.beam = QSpinBox()
        self.beam.setRange(1, 10)
        f.addRow("Beam size", self.beam)
        self.server_url = QLineEdit()
        f.addRow("Server URL", self.server_url)
        layout.addWidget(speech)

        # Translation, history, interface, paste
        other = QGroupBox("Translation, history and display")
        f = QFormLayout(other)
        self.target_language = QComboBox()
        self.target_language.setEditable(True)
        self.target_language.addItems(LANGUAGES)
        f.addRow("Translate into", self.target_language)
        self.history_enabled = QCheckBox("Keep a history of dictations on this computer")
        f.addRow("", self.history_enabled)
        self.keep_audio = QCheckBox("Also keep the recorded audio (about 2 MB per minute)")
        f.addRow("", self.keep_audio)
        self.retention = QSpinBox()
        self.retention.setRange(0, 3650)
        self.retention.setSpecialValueText("Forever")
        self.retention.setSuffix(" days")
        f.addRow("Keep history for", self.retention)
        self.floating_bar = QCheckBox("Show the floating bar while listening and transcribing")
        f.addRow("", self.floating_bar)
        self.typing_wpm = QSpinBox()
        self.typing_wpm.setRange(5, 200)
        self.typing_wpm.setSuffix(" words/min")
        f.addRow("Your typing speed", self.typing_wpm)
        self.restore_clipboard = QCheckBox("Put my previous clipboard back after pasting")
        f.addRow("", self.restore_clipboard)
        self.restore_delay = QSpinBox()
        self.restore_delay.setRange(50, 5000)
        self.restore_delay.setSuffix(" ms")
        f.addRow("Restore the clipboard after", self.restore_delay)
        layout.addWidget(other)
        layout.addStretch()

        footer = QHBoxLayout()
        footer.setContentsMargins(28, 8, 28, 16)
        open_file = QPushButton("Open config.toml")
        open_file.setToolTip("Edit by hand; restart talk-to-mic afterwards")
        open_file.clicked.connect(lambda: tray_open_file(CONFIG_PATH))
        self.saved_label = muted()
        revert = QPushButton("Revert")
        revert.clicked.connect(self.refresh)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self.save)
        footer.addWidget(open_file)
        footer.addWidget(self.saved_label, 1)
        footer.addWidget(revert)
        footer.addWidget(save)
        outer.addLayout(footer)

        self._key_check = _KeyCheck()
        self._key_check.finished.connect(self._key_checked)

    @staticmethod
    def _combo_box() -> QComboBox:
        box = QComboBox()
        box.addItem("Off", "")
        for name in COMBO_KEYS:
            box.addItem(key_label(name), name)
        return box

    @staticmethod
    def _select(box: QComboBox, value: str) -> None:
        index = box.findData(value)
        if index < 0 and box.isEditable():
            box.setEditText(value)
        else:
            box.setCurrentIndex(max(index, 0))

    @staticmethod
    def _select_text(box: QComboBox, value: str) -> None:
        index = box.findText(value)
        if index >= 0:
            box.setCurrentIndex(index)
        else:
            box.setEditText(value)

    def _input_devices(self) -> list[str]:
        try:
            import sounddevice as sd

            names = {d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0}
        except Exception:
            log.exception("Could not list microphones")
            names = set()
        return sorted(names, key=str.lower)

    def refresh(self) -> None:
        cfg = self.app.cfg
        self._refresh_key_status()
        self.cleanup_enabled.setChecked(cfg.cleanup.enabled)
        self._select_text(self.model, cfg.cleanup.model)
        self.effort.setCurrentText(cfg.cleanup.effort)
        self.send_context.setChecked(cfg.cleanup.send_app_context)
        self.timeout.setValue(cfg.cleanup.timeout_seconds)
        self._select(self.hotkey, cfg.hotkey.key)
        self.min_hold.setValue(cfg.hotkey.min_hold_ms)
        self._select(self.ask_combo, cfg.hotkey.ask_combo)
        self._select(self.translate_combo, cfg.hotkey.translate_combo)
        self.paste_last.setText(cfg.hotkey.paste_last)
        self.device.clear()
        self.device.addItem("Windows default microphone" if sys.platform == "win32" else "System default microphone", "")
        for name in self._input_devices():
            self.device.addItem(name, name)
        if cfg.audio.input_device and self.device.findData(cfg.audio.input_device) < 0:
            self.device.addItem(f"{cfg.audio.input_device} (from config.toml)", cfg.audio.input_device)
        self._select(self.device, cfg.audio.input_device)
        self.beep.setChecked(cfg.audio.beep)
        self.max_seconds.setValue(cfg.audio.max_seconds)
        self._select(self.engine, cfg.transcription.engine)
        self._select_text(self.whisper_model, cfg.transcription.model)
        self.language.setText(cfg.transcription.language)
        self.beam.setValue(cfg.transcription.beam_size)
        self.server_url.setText(cfg.transcription.server_url)
        self._select_text(self.target_language, cfg.translation.target_language)
        self.history_enabled.setChecked(cfg.history.enabled)
        self.keep_audio.setChecked(cfg.history.keep_audio)
        self.retention.setValue(cfg.history.retention_days)
        self.floating_bar.setChecked(cfg.ui.floating_bar)
        self.typing_wpm.setValue(cfg.ui.typing_wpm)
        self.restore_clipboard.setChecked(cfg.paste.restore_clipboard)
        self.restore_delay.setValue(cfg.paste.restore_delay_ms)
        self.saved_label.setText("")

    def _refresh_key_status(self) -> None:
        cleaner = self.app.cleaner
        from_env = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if cleaner.client is not None:
            self.key_status.setText(f"✓ Connected using {cleaner.key_source}.")
        else:
            self.key_status.setText(f"✗ {cleaner.disabled_reason or 'Not configured'}.")
        if from_env:
            self.key_edit.setPlaceholderText("Using ANTHROPIC_API_KEY from the environment")
        self.key_edit.setEnabled(not from_env)
        self.key_test.setEnabled(not from_env)
        self.key_save.setEnabled(not from_env)
        self.key_remove.setEnabled(not from_env and cleaner.key_source is not None and credentials.STORE_NAME in cleaner.key_source)

    def _test_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.key_result.setText("Paste a key first.")
            return
        self.key_result.setText("Testing…")
        self.key_test.setEnabled(False)
        model = self.model.currentText().strip() or "claude-haiku-5-5"
        threading.Thread(target=lambda: self._key_check.finished.emit(check_api_key(key, model) or ""),
                         daemon=True).start()

    def _key_checked(self, error: str) -> None:
        self.key_test.setEnabled(True)
        self.key_result.setText(error or "✓ The key works.")

    def _save_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.key_result.setText("Paste a key first.")
            return
        try:
            self.app.set_api_key(key)
        except Exception as exc:
            QMessageBox.warning(self, "API key", f"Could not save the key: {exc}")
            return
        self.key_edit.clear()
        self.key_result.setText(f"Saved in {credentials.STORE_NAME}.")
        self._refresh_key_status()
        self._on_saved()

    def _remove_key(self) -> None:
        self.app.set_api_key(None)
        self.key_result.setText("Removed.")
        self._refresh_key_status()
        self._on_saved()

    def collect(self) -> Config:
        cfg = copy.deepcopy(self.app.cfg)
        cfg.cleanup.enabled = self.cleanup_enabled.isChecked()
        cfg.cleanup.model = self.model.currentText().strip() or cfg.cleanup.model
        cfg.cleanup.effort = self.effort.currentText()
        cfg.cleanup.send_app_context = self.send_context.isChecked()
        cfg.cleanup.timeout_seconds = float(self.timeout.value())
        cfg.hotkey.key = self.hotkey.currentData()
        cfg.hotkey.min_hold_ms = self.min_hold.value()
        cfg.hotkey.ask_combo = self.ask_combo.currentData()
        cfg.hotkey.translate_combo = self.translate_combo.currentData()
        cfg.hotkey.paste_last = self.paste_last.text().strip().lower()
        cfg.audio.input_device = self.device.currentData() or ""
        cfg.audio.beep = self.beep.isChecked()
        cfg.audio.max_seconds = self.max_seconds.value()
        cfg.transcription.engine = self.engine.currentData()
        cfg.transcription.model = self.whisper_model.currentText().strip() or cfg.transcription.model
        cfg.transcription.language = self.language.text().strip().lower()
        cfg.transcription.beam_size = self.beam.value()
        cfg.transcription.server_url = self.server_url.text().strip()
        cfg.translation.target_language = self.target_language.currentText().strip() or "English"
        cfg.history.enabled = self.history_enabled.isChecked()
        cfg.history.keep_audio = self.keep_audio.isChecked()
        cfg.history.retention_days = self.retention.value()
        cfg.ui.floating_bar = self.floating_bar.isChecked()
        cfg.ui.typing_wpm = self.typing_wpm.value()
        cfg.paste.restore_clipboard = self.restore_clipboard.isChecked()
        cfg.paste.restore_delay_ms = self.restore_delay.value()
        return cfg

    def validate(self, cfg: Config) -> str | None:
        h = cfg.hotkey
        if h.ask_combo and h.ask_combo == h.translate_combo:
            return "Ask Anything and Translate need different keys."
        try:
            parse_chord(h.paste_last)
        except ValueError as exc:
            return str(exc)
        return None

    def save(self) -> None:
        cfg = self.collect()
        problem = self.validate(cfg)
        if problem:
            QMessageBox.warning(self, "Settings", problem)
            return
        try:
            self.app.apply_settings(cfg)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Settings", f"Could not save the settings: {exc}")
            return
        self.saved_label.setText("✓ Saved and applied." + (
            " The speech model is reloading." if self.app.transcriber is None else ""))
        self._on_saved()


# --- Window --------------------------------------------------------------------

class Hub(QMainWindow):
    def __init__(self, app: App):
        super().__init__()
        self.app = app
        self.setWindowTitle("talk-to-mic")
        self.resize(1000, 680)
        self.setMinimumSize(820, 560)

        self.nav = QListWidget()
        self.nav.setFixedWidth(180)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setStyleSheet(
            "QListWidget { background: transparent; padding-top: 16px; outline: 0; }"
            "QListWidget::item { padding: 9px 14px; border: none; border-radius: 6px; margin: 1px 8px; }"
            "QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }"
            "QListWidget::item:hover:!selected { background: palette(midlight); }"
        )
        for title in ("Home", "History", "Dictionary", "Settings"):
            self.nav.addItem(QListWidgetItem(title))

        self.home = HomePage(app, self._open_entry)
        self.dictionary = DictionaryPage(app)
        self.history = HistoryPage(app, self.dictionary.add)
        self.settings = SettingsPage(app, self._refresh_all)
        self.stack = QStackedWidget()
        for page in (self.home, self.history, self.dictionary, self.settings):
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self._show_row)

        side = QWidget()
        s = QVBoxLayout(side)
        s.setContentsMargins(0, 0, 0, 12)
        s.addWidget(self.nav, 1)
        version = muted(f"talk-to-mic {__version__}")
        version.setContentsMargins(22, 0, 0, 0)
        s.addWidget(version)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        root = QWidget()
        r = QHBoxLayout(root)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(0)
        r.addWidget(side)
        r.addWidget(divider)
        r.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        side.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        self.nav.setCurrentRow(0)
        app.bridge.history_changed.connect(self._history_changed)

    def open(self, page: str | None = None) -> None:
        if page in PAGES:
            self.nav.setCurrentRow(PAGES.index(page))
        else:
            self._show_row(self.nav.currentRow())
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _show_row(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        page = self.stack.currentWidget()
        try:
            page.refresh()
        except Exception:
            log.exception("Could not refresh the %s page", PAGES[row])

    def _refresh_all(self) -> None:
        self.home.refresh()
        self.dictionary.refresh()

    def _history_changed(self) -> None:
        if self.isVisible() and self.stack.currentWidget() in (self.home, self.history):
            self.stack.currentWidget().refresh()

    def _open_entry(self, entry_id: int) -> None:
        self.nav.setCurrentRow(PAGES.index("history"))
        self.history.select(entry_id)
