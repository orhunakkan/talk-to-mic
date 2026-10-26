"""The Ask Anything card: shows Claude's answer with Copy and Insert buttons.

It appears without taking focus, so the user can keep typing; clicking it activates it.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .bridge import CardContent
from .floating_bar import MARGIN, FloatingBar
from .screens import available_rect, bottom_center, target_screen

WIDTH = 540


class AnswerCard(QWidget):
    def __init__(self, on_insert: Callable[[str, int], None]):
        super().__init__(
            None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Ask Anything")
        self._on_insert = on_insert
        self._content: CardContent | None = None

        frame = QFrame(self)
        frame.setObjectName("card")
        frame.setStyleSheet(
            "#card { background: palette(window); border: 1px solid palette(mid); border-radius: 12px; }"
            "QTextBrowser { background: transparent; border: none; }"
        )
        shadow = QGraphicsDropShadowEffect(frame)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 90))
        frame.setGraphicsEffect(shadow)

        self.title = QLabel("Ask Anything")
        title_font = QFont(self.title.font())
        title_font.setWeight(QFont.Weight.DemiBold)
        title_font.setPointSizeF(title_font.pointSizeF() + 1)
        self.title.setFont(title_font)
        close = QToolButton()
        close.setText("✕")
        close.setAutoRaise(True)
        close.setToolTip("Close (Esc)")
        close.clicked.connect(self.hide)
        header = QHBoxLayout()
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(close)

        self.question = QLabel()
        self.question.setWordWrap(True)
        self.question.setForegroundRole(QPalette.ColorRole.PlaceholderText)  # follows theme changes
        question_font = QFont(self.question.font())
        question_font.setItalic(True)
        self.question.setFont(question_font)
        self.question.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        self.body.setFrameShape(QFrame.Shape.NoFrame)

        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy)
        self.insert_button = QPushButton("Insert")
        self.insert_button.setToolTip("Paste the answer into the window you were working in")
        self.insert_button.clicked.connect(self._insert)
        self.insert_button.setDefault(True)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.hide)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.insert_button)
        buttons.addWidget(close_button)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(18, 12, 12, 14)
        inner.setSpacing(8)
        inner.addLayout(header)
        inner.addWidget(self.question)
        inner.addWidget(self.body)
        inner.addLayout(buttons)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)  # room for the shadow
        outer.addWidget(frame)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.hide)

    def show_content(self, content: CardContent) -> None:
        self._content = content
        self.title.setText("Ask Anything: no answer" if content.error else "Ask Anything")
        self.question.setText(f"“{content.question}”" if content.question else "")
        self.question.setVisible(bool(content.question))
        if content.error:
            self.body.setPlainText(content.text)
        else:
            self.body.setMarkdown(content.text)
        self.copy_button.setText("Copy")
        self.copy_button.setEnabled(not content.error)
        self.insert_button.setEnabled(not content.error and bool(content.hwnd))

        screen = target_screen(content.hwnd)
        available = available_rect(screen)
        self.body.document().setTextWidth(WIDTH - 2 * 16 - 30)
        body_h = int(self.body.document().size().height()) + 12
        max_body = max(120, int(available[3] * 0.5))
        self.body.setFixedHeight(min(max(body_h, 48), max_body))
        self.setFixedWidth(WIDTH)
        self.adjustSize()
        size = (self.width(), self.height())
        self.move(*bottom_center(available, size, MARGIN + FloatingBar.HEIGHT + 4))
        self.show()
        self.raise_()

    def _copy(self) -> None:
        if self._content:
            QGuiApplication.clipboard().setText(self.body.toPlainText())
            self.copy_button.setText("Copied")
            QTimer.singleShot(1500, lambda: self.copy_button.setText("Copy"))

    def _insert(self) -> None:
        if self._content:
            self.hide()
            self._on_insert(self.body.toPlainText(), self._content.hwnd)
