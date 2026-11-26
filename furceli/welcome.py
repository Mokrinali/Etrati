"""The screen shown when no file is open (design/components/WelcomeScreen)."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import theme

LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "furceli.png")
MONTHS = ["იან", "თებ", "მარ", "აპრ", "მაი", "ივნ", "ივლ", "აგვ", "სექ", "ოქტ", "ნოე", "დეკ"]


def font(px, weight=""):
    """Noto Sans Georgian of a size and a weight: "" 400, "Medium" 500, "SemiBold" 600."""
    f = QFont()
    names = [f"Noto Sans Georgian {weight}".strip(), f"Noto Sans {weight}".strip()]
    f.setFamilies(names + ["Noto Sans Georgian", "Segoe UI", "Sylfaen"])
    f.setPixelSize(px)
    return f


def when(t, now=None):
    """დღეს, გუშინ, 22 ნოე or 22 ნოე 2025."""
    if not t:
        return ""
    now = time.localtime(now if now is not None else time.time())
    then = time.localtime(t)
    days = (time.mktime(now[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(then[:3] + (0, 0, 0, 0, 0, -1)))
    days = round(days / 86400)
    if days == 0:
        return "დღეს"
    if days == 1:
        return "გუშინ"
    text = f"{then.tm_mday} {MONTHS[then.tm_mon - 1]}"
    return text if then.tm_year == now.tm_year else f"{text} {then.tm_year}"


def muted(text, px=12):
    lab = QLabel(text)
    lab.setProperty("role", "muted")
    lab.setFont(font(px))
    return lab


class ActionCard(QPushButton):
    """One of the four big actions: a tile with an icon, a name and a hint."""

    def __init__(self, title, hint, icon, main=False):
        super().__init__()
        self.setProperty("variant", "card")
        self.setAccessibleName(title)
        self.setToolTip(title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(196)
        self.setMinimumHeight(128)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)  # one height for all
        self.icon_code, self.main = icon, main
        self.tile = QLabel()
        self.tile.setProperty("role", "tile")
        self.tile.setProperty("main", main)
        self.tile.setFixedSize(36, 36)
        self.tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel(title)
        name.setFont(font(13, "SemiBold"))
        name.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        lay.addWidget(self.tile)
        lay.addWidget(name)
        note = muted(hint)
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch()
        for w in (self.tile, name):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        for w in self.findChildren(QLabel):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.recolor()

    def recolor(self):
        from .window import glyph
        ink = theme.color("on-brand" if self.main else "brand-text")
        self.tile.setPixmap(glyph(self.icon_code, ink).pixmap(QSize(18, 18), self.devicePixelRatioF()))

    def sizeHint(self):
        return QSize(196, max(128, self.layout().sizeHint().height()))


class RecentRow(QWidget):
    def __init__(self, path, opened):
        super().__init__()
        ext = QLabel(os.path.splitext(path)[1][1:].upper()[:4] or "PDF")
        ext.setProperty("role", "fico")
        ext.setFixedSize(28, 34)
        ext.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = font(9, "SemiBold")
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
        ext.setFont(f)
        name = QLabel(os.path.basename(path))
        name.setFont(font(13, "SemiBold"))
        name.setTextFormat(Qt.TextFormat.PlainText)
        folder = muted(os.path.dirname(path))
        folder.setTextFormat(Qt.TextFormat.PlainText)
        for lab in (name, folder):
            lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lab.setToolTip(path)
        meta = QVBoxLayout()
        meta.setSpacing(0)
        meta.addWidget(name)
        meta.addWidget(folder)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)
        lay.addWidget(ext)
        lay.addLayout(meta, 1)
        lay.addWidget(muted(when(opened)))


class WelcomeScreen(QScrollArea):
    """Logo and name, four actions, the files opened last."""

    chosen = Signal(str)

    def __init__(self, actions):
        """actions: [(title, hint, icon, slot)]; the first one is the main action."""
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setObjectName("welcome")
        page = QWidget()
        page.setObjectName("welcome-page")
        outer = QHBoxLayout(page)
        outer.setContentsMargins(48, 48, 48, 48)
        col = QWidget()
        col.setMaximumWidth(860)
        outer.addStretch(1)
        outer.addWidget(col, 100)
        outer.addStretch(1)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(24)
        head = QHBoxLayout()
        head.setSpacing(16)
        self.logo = QLabel()
        self.logo.setFixedSize(56, 56)
        self.logo.setPixmap(QIcon(LOGO).pixmap(QSize(56, 56), self.devicePixelRatioF()))
        self.title = QLabel("ეტრატი")
        self.title.setFont(font(40, "SemiBold"))
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(self.title)
        names.addWidget(muted("გახსენით PDF ფაილი ან ჩააგდეთ ამ ფანჯარაში", 13))
        head.addWidget(self.logo)
        head.addLayout(names)
        head.addStretch()
        lay.addLayout(head)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.cards = []
        for i, (title, hint, icon, slot) in enumerate(actions):
            c = ActionCard(title, hint, icon, main=i == 0)
            c.clicked.connect(lambda *_, s=slot: s())
            cards.addWidget(c)
            self.cards.append(c)
        cards.addStretch()
        lay.addLayout(cards)
        self.recent_label = QLabel("ბოლოს გახსნილი")
        self.recent_label.setFont(font(22, "SemiBold"))
        lay.addWidget(self.recent_label)
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recent")
        self.recent_list.setMaximumWidth(640)
        self.recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_list.setSizeAdjustPolicy(QListWidget.SizeAdjustPolicy.AdjustToContents)
        self.recent_list.setMouseTracking(True)
        self.recent_list.itemClicked.connect(self._open)
        self.recent_list.itemActivated.connect(self._open)
        lay.addWidget(self.recent_list)
        lay.addStretch()
        self.setWidget(page)

    def _open(self, item):
        self.chosen.emit(item.data(Qt.ItemDataRole.UserRole))

    def set_recent(self, paths, times):
        self.recent_list.clear()
        for p in paths:
            row = RecentRow(p, times.get(os.path.normcase(p)))
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, p)
            it.setToolTip(p)
            it.setSizeHint(QSize(0, row.sizeHint().height()))
            self.recent_list.addItem(it)
            self.recent_list.setItemWidget(it, row)
        rows = sum(self.recent_list.sizeHintForRow(i) for i in range(len(paths)))
        self.recent_list.setFixedHeight(rows + 4 if paths else 0)
        self.recent_label.setVisible(bool(paths))
        self.recent_list.setVisible(bool(paths))

    def recolor(self):
        for c in self.cards:
            c.recolor()


class DropFrame(QWidget):
    """Shown over the window while files are dragged onto it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        fill = theme.color("brand-soft")
        fill.setAlpha(170)
        p.setBrush(fill)
        pen = QPen(theme.color("brand"), 2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(box, 12, 12)
        p.setPen(theme.color("brand-text"))
        p.setFont(font(22, "SemiBold"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, "ჩააგდეთ გასახსნელად")
