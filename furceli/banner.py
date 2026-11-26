"""A message over the document (design/components/Banner). It never blocks anything."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from . import theme

KINDS = {"info": ("info", 0xE946), "success": ("success", 0xE73E),
         "warning": ("warning", 0xE7BA), "danger": ("danger", 0xEA39)}


class Banner(QFrame):
    """kind: info, success, warning or danger. Text: what happened and what did not change."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        self.kind, self._action = "info", None
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.PlainText)  # messages may quote file contents
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.link = QPushButton()
        self.link.setProperty("variant", "link")
        self.link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link.clicked.connect(self._act)
        self.close_btn = QPushButton()
        self.close_btn.setProperty("variant", "ghost")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setToolTip("დახურვა")
        self.close_btn.setAccessibleName("დახურვა")
        self.close_btn.clicked.connect(self.clear)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(8)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.link, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.hide()

    def message(self) -> str:
        return "" if self.isHidden() else self.text.text()

    def show_message(self, kind, text, action_text="", action=None):
        self.kind = kind if kind in KINDS else "info"
        self.setProperty("kind", self.kind)
        self.text.setText(text)
        self._action = action
        self.link.setText(action_text)
        self.link.setVisible(bool(action_text and action))
        self.recolor()
        self.style().unpolish(self)
        self.style().polish(self)
        self.show()

    def clear(self):
        self.text.setText("")
        self._action = None
        self.hide()

    def _act(self):
        if self._action:
            act, self._action = self._action, None
            self.clear()
            act()

    def recolor(self):
        from .window import glyph
        tok, code = KINDS[self.kind]
        self.icon.setPixmap(glyph(code, theme.color(tok)).pixmap(QSize(16, 16),
                                                                 self.devicePixelRatioF()))
        close = glyph(0xE711, theme.color("ink-muted"))
        self.close_btn.setIcon(close)
        self.close_btn.setIconSize(QSize(12, 12))
