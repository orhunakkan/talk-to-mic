"""Signals the background threads emit; Qt delivers them to slots on the main (UI) thread."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal


@dataclass
class CardContent:
    question: str  # what the user said
    text: str  # Claude's answer, or the error message
    hwnd: int = 0  # window the user was working in, for Insert
    error: bool = False


class UiBridge(QObject):
    state = Signal(object, object, int)  # tray.Status, hotkey.Intent, hwnd of the target window
    card = Signal(object)  # CardContent
    history_changed = Signal()
    open_requested = Signal()  # another launch of talk-to-mic asked us to show the window
