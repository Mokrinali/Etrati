"""The bar over a chosen picture and the "on other pages too" dialog (HANDOFF.md, item 7;
the look: design canvas "ETRATI: სურათი და თარიღი")."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDoubleSpinBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QRadioButton, QToolButton, QVBoxLayout)

from . import files, theme

MM = 25.4 / 72  # millimetres in a point


class ObjectBar(QFrame):
    """Size in millimetres (its proportion kept while the lock is on), quarter turns,
    "on other pages too…", delete. Values are points; the bar shows millimetres."""

    sized = Signal(float, float)  # new width and height, points
    turned = Signal(int)          # degrees, clockwise as shown
    copy = Signal()
    delete = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("objectBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)
        self._w, self._h = self._spin("სიგანე, მმ"), self._spin("სიმაღლე, მმ")
        self.lock = QToolButton()
        self.lock.setCheckable(True)
        self.lock.setChecked(True)
        self.lock.setIcon(theme.icon("lock"))
        self.lock.setToolTip("პროპორციის დაცვა")
        self.lock.setAccessibleName("პროპორციის დაცვა")
        lay.addWidget(QLabel("სიგ."))
        lay.addWidget(self._w)
        lay.addWidget(self.lock)
        lay.addWidget(QLabel("სიმ."))
        lay.addWidget(self._h)
        lay.addWidget(QLabel("მმ"))
        lay.addWidget(self._line())
        for deg, icon, tip in ((-90, "rotate-page-left", "მობრუნება მარცხნივ"),
                               (90, "rotate-page-right", "მობრუნება მარჯვნივ")):
            b = QToolButton()
            b.setIcon(theme.icon(icon))
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.clicked.connect(lambda _=False, d=deg: self.turned.emit(d))
            lay.addWidget(b)
        lay.addWidget(self._line())
        self.copy_btn = QPushButton("სხვა გვერდებზეც…")
        self.copy_btn.clicked.connect(self.copy.emit)
        lay.addWidget(self.copy_btn)
        self.del_btn = QToolButton()
        self.del_btn.setIcon(theme.icon("delete"))
        self.del_btn.setToolTip("წაშლა")
        self.del_btn.setAccessibleName("წაშლა")
        self.del_btn.clicked.connect(self.delete.emit)
        lay.addWidget(self.del_btn)
        self._ratio = 1.0
        self._w.editingFinished.connect(lambda: self._edited(self._w))
        self._h.editingFinished.connect(lambda: self._edited(self._h))
        self.hide()

    @staticmethod
    def _spin(name):
        s = QDoubleSpinBox()
        s.setDecimals(1)
        s.setRange(1.0, 2000.0)
        s.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        s.setFixedWidth(58)
        s.setAccessibleName(name)
        s.setKeyboardTracking(False)
        return s

    @staticmethod
    def _line():
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setObjectName("objectBarLine")
        return f

    def show_size(self, w_pt, h_pt):
        self._ratio = h_pt / w_pt if w_pt else 1.0
        for s, v in ((self._w, w_pt * MM), (self._h, h_pt * MM)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        self._shown = (round(w_pt * MM, 1), round(h_pt * MM, 1))

    def _edited(self, which):
        w, h = self._w.value(), self._h.value()
        if self.lock.isChecked():
            if which is self._w:
                h = w * self._ratio
            else:
                w = h / self._ratio
        if (round(w, 1), round(h, 1)) != getattr(self, "_shown", None):
            self.sized.emit(w / MM, h / MM)


class CopyDialog(QDialog):
    """Which pages, and from which point of the page the place is measured."""

    POINTS = [(0, 0), (.5, 0), (1, 0), (0, .5), (.5, .5), (1, .5), (0, 1), (.5, 1), (1, 1)]
    NAMES = ["ზედა მარცხენა", "ზედა შუა", "ზედა მარჯვენა", "შუა მარცხენა", "ცენტრი",
             "შუა მარჯვენა", "ქვედა მარცხენა", "ქვედა შუა", "ქვედა მარჯვენა"]

    def __init__(self, page, total, auto, what="სურათი", parent=None):
        """page: the page it is on (0-based); auto: the nearest corner, (x, y) fractions."""
        super().__init__(parent)
        self.setWindowTitle(f"{what} სხვა გვერდებზეც")
        self._page, self._total = page, total
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        note = QLabel(f"ყველა არჩეულ გვერდზე {what.lower()} ჩაჯდება იმავე ადგილას და იმავე ზომით. "
                      "შემდეგ თითოეული ცალკე იცვლება.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        lay.addWidget(self._title("რომელ გვერდებზე"))
        self.only = QRadioButton(f"მხოლოდ ეს გვერდი ({page + 1})")
        self.all = QRadioButton(f"ყველა გვერდი ({total})")
        self.some = QRadioButton("გვერდები:")
        self.range = QLineEdit()
        self.range.setPlaceholderText("მაგ.: 2-5, 8")
        self.range.textEdited.connect(lambda _: self.some.setChecked(True))
        group = QButtonGroup(self)
        for b in (self.only, self.all, self.some):
            group.addButton(b)
            lay.addWidget(b)
            b.toggled.connect(self._label)
        row = QHBoxLayout()
        row.setContentsMargins(24, 0, 0, 0)
        row.addWidget(self.range)
        lay.addLayout(row)
        lay.addWidget(self._title("რომელი წერტილიდან ითვლება ადგილი"))
        grid = QGridLayout()
        grid.setSpacing(4)
        self._points = QButtonGroup(self)
        for i, (pt, name) in enumerate(zip(self.POINTS, self.NAMES)):
            b = QToolButton()
            b.setCheckable(True)
            b.setFixedSize(30, 26)
            b.setToolTip(name)
            b.setAccessibleName(name)
            b.setObjectName("anchor")
            self._points.addButton(b, i)
            grid.addWidget(b, i // 3, i % 3)
        self._points.button(self.POINTS.index(tuple(auto))).setChecked(True)
        anchors = QHBoxLayout()
        anchors.addLayout(grid)
        hint = QLabel("ავტომატურად არჩეულია ყველაზე ახლო კუთხე. სხვა ზომის გვერდზე მანძილი "
                      "ამ წერტილიდან იგივე რჩება, ზომაც იგივე.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        anchors.addWidget(hint, 1)
        lay.addLayout(anchors)
        from .forms import _buttons
        self._box = _buttons(self, "ჩასმა", self._check)
        lay.addWidget(self._box)
        self.all.setChecked(True)
        self.setMinimumWidth(420)

    @staticmethod
    def _title(text):
        t = QLabel(text)
        t.setObjectName("fieldTitle")
        return t

    def _label(self):
        from PySide6.QtWidgets import QDialogButtonBox
        n = len(self.pages(quiet=True) or ())
        self._box.button(QDialogButtonBox.StandardButton.Ok).setText(
            f"ჩასმა კიდევ {n} გვერდზე" if n else "კარგი")

    def pages(self, quiet=False):
        """The pages to put it on, without its own page; None when the range is wrong."""
        if self.only.isChecked():
            return []
        if self.all.isChecked():
            chosen = list(range(self._total))
        else:
            try:
                chosen = files.parse_group(self.range.text(), self._total)
            except ValueError as e:
                if not quiet:
                    QMessageBox.warning(self, self.windowTitle(), str(e))
                return None
        return [p for p in chosen if p != self._page]

    def anchor(self):
        return self.POINTS[self._points.checkedId()]

    def _check(self):
        got = self.pages()
        if got is None:
            return
        if self.some.isChecked() and not self.range.text().strip():
            QMessageBox.warning(self, self.windowTitle(), "ჩაწერეთ გვერდები, მაგ.: 2-5, 8")
            return
        self.accept()
