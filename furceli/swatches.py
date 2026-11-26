"""Quick annotation colours on the toolbar (design/components/ColorSwatches).

These colours are written into documents, so they do not change with the theme.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QHBoxLayout, QWidget

from . import theme

NAMES = [("ink-pen", "წითელი"), ("mark-highlight", "ყვითელი"), ("mark-edit", "მწვანე"),
         ("mark-crop", "ლურჯი"), ("ink-signature", "მუქი ლურჯი"), (None, "შავი")]


def colors():
    return [(theme.color(t) if t else QColor(0, 0, 0), name) for t, name in NAMES]


class Swatch(QAbstractButton):
    def __init__(self, color, name):
        super().__init__()
        self.color = color
        self.setCheckable(True)
        self.setToolTip(name)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(30, 30))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)  # 22 px
        if self.isChecked():
            p.setPen(QPen(theme.color("ink"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.adjusted(-3, -3, 3, 3))
        elif self.underMouse():
            p.setPen(QPen(theme.color("line-strong"), 1))
            p.drawEllipse(r.adjusted(-2.5, -2.5, 2.5, 2.5))
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        p.setBrush(self.color)
        p.drawEllipse(r)

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class ColorSwatches(QWidget):
    """Six circles; the chosen one has a ring. picked(QColor) when one is clicked."""

    picked = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(False)  # a colour of one's own leaves no circle chosen
        self.buttons = []
        for color, name in colors():
            b = Swatch(color, name)
            b.clicked.connect(lambda _=False, b=b: self._click(b))
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons.append(b)

    def _click(self, b):
        self.show_color(b.color)
        self.picked.emit(QColor(b.color))

    def show_color(self, color):
        """Rings the circle of this colour, or none when it is a colour of one's own."""
        for b in self.buttons:
            b.setChecked(b.color.rgb() == QColor(color).rgb())
            b.update()
