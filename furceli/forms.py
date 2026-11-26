"""Dialogs for comments, content, security, OCR, signatures and comparison."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

from . import files, theme
from .client import TaskError, run_task
from .dialogs import error_text


def _rgb(c: QColor):
    return (round(c.redF(), 4), round(c.greenF(), 4), round(c.blueF(), 4))


def _buttons(dlg, ok="კარგი", check=None, kind="primary"):
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                          | QDialogButtonBox.StandardButton.Cancel)
    bb.button(QDialogButtonBox.StandardButton.Ok).setText(ok)
    theme.variant(bb.button(QDialogButtonBox.StandardButton.Ok), kind)
    bb.button(QDialogButtonBox.StandardButton.Cancel).setText("გაუქმება")
    bb.accepted.connect(check or dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


class ColorButton(QPushButton):
    def __init__(self, color=(0, 0, 0)):
        super().__init__()
        self._c = QColor.fromRgbF(*color)
        self.setFixedWidth(60)
        self.clicked.connect(self._pick)
        self._paint()

    def _paint(self):
        pix = QPixmap(36, 14)
        pix.fill(self._c)
        self.setIcon(pix)

    def _pick(self):
        c = QColorDialog.getColor(self._c, self, "ფერი")
        if c.isValid():
            self._c = c
            self._paint()

    def value(self):
        return _rgb(self._c)


class PagesMixin:
    """Adds a 'which pages' line and validates it."""

    def add_pages(self, form, n):
        self._n = n
        self._pages = QLineEdit()
        self._pages.setPlaceholderText("ცარიელი = ყველა. მაგ.: 1-3, 7")
        form.addRow("გვერდები:", self._pages)

    def pages_text(self):
        return self._pages.text().strip()

    def check_pages(self):
        text = self.pages_text()
        if text:
            try:
                files.parse_group(text, self._n)
            except ValueError as e:
                QMessageBox.warning(self, self.windowTitle(), str(e))
                return False
        return True

    def try_accept(self):
        if self.check_pages():
            self.accept()


class TextDialog(QDialog):
    def __init__(self, parent=None, color=(0, 0, 0)):
        super().__init__(parent)
        self.setWindowTitle("ტექსტის დამატება")
        lay = QVBoxLayout(self)
        self._text = QPlainTextEdit()
        lay.addWidget(self._text)
        form = QFormLayout()
        self._size = QDoubleSpinBox()
        self._size.setRange(4, 200)
        self._size.setValue(12)
        self._color = ColorButton(color)
        self._bold = QCheckBox("მუქი")
        self._kind = QComboBox()
        self._kind.addItems(["გვერდის ტექსტად", "კომენტარად (მერე შეიცვლება)"])
        form.addRow("ზომა:", self._size)
        form.addRow("ფერი:", self._color)
        form.addRow("", self._bold)
        form.addRow("როგორ:", self._kind)
        lay.addLayout(form)
        lay.addWidget(_buttons(self, "დამატება"))

    def text(self):
        return self._text.toPlainText()

    def size(self):  # noqa: A003 - shadows QWidget.size on purpose of brevity
        return self._size.value()

    def color(self):
        return self._color.value()

    def bold(self):
        return self._bold.isChecked()

    def as_comment(self):
        return self._kind.currentIndex() == 1


class LineDialog(QDialog):
    def __init__(self, line, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ტექსტის შეცვლა")
        self.setMinimumWidth(520)
        self._line = line
        lay = QVBoxLayout(self)
        self._text = QLineEdit(line["text"])
        lay.addWidget(self._text)
        form = QFormLayout()
        self._size = QDoubleSpinBox()
        self._size.setRange(.5, 2000)
        self._size.setValue(round(line["size"], 2))
        self._shown = self._size.value()
        self._color = ColorButton(line["color"])
        self._bold, self._italic = QCheckBox("მუქი"), QCheckBox("დახრილი")
        self._bold.setChecked(line["bold"])
        self._italic.setChecked(line["italic"])
        form.addRow("ზომა:", self._size)
        form.addRow("ფერი:", self._color)
        form.addRow("", self._bold)
        form.addRow("", self._italic)
        lay.addLayout(form)
        note = QLabel("იცვლება მხოლოდ ეს სტრიქონი; აბზაცი თავიდან არ გადაეწყობა. ფონტი მსგავსით იცვლება. "
                      "ცარიელი ტექსტი სტრიქონს წაშლის.")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addWidget(_buttons(self, "შეცვლა"))
        self._start = self.style_()

    def text(self):
        return self._text.text()

    def style_(self):
        size = self._size.value()
        return {"size": self._line["size"] if size == self._shown else size,
                "color": self._color.value(),
                "bold": self._bold.isChecked(), "italic": self._italic.isChecked(),
                "serif": self._line["serif"], "mono": self._line["mono"]}

    def style_changed(self):
        return self.style_() != self._start


class FieldDialog(QDialog):
    KINDS = [("text", "ტექსტი"), ("area", "მრავალსტრიქონიანი ტექსტი"), ("check", "მოსანიშნი"),
             ("combo", "ჩამოსაშლელი სია"), ("list", "სია"), ("sign", "ხელმოწერა")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ფორმის ველი")
        form = QFormLayout(self)
        self._name = QLineEdit()
        self._kind = QComboBox()
        self._kind.addItems([t for _, t in self.KINDS])
        self._choices = QPlainTextEdit()
        self._choices.setPlaceholderText("ვარიანტები, თითო სტრიქონზე")
        self._choices.setFixedHeight(90)
        form.addRow("სახელი:", self._name)
        form.addRow("ტიპი:", self._kind)
        form.addRow("ვარიანტები:", self._choices)
        form.addRow(_buttons(self, "შექმნა", self._check))

    def _check(self):
        kind = self.KINDS[self._kind.currentIndex()][0]
        if not self._name.text().strip():
            QMessageBox.warning(self, self.windowTitle(), "მიუთითეთ ველის სახელი.")
        elif kind in ("combo", "list") and not self._items():
            QMessageBox.warning(self, self.windowTitle(), "ჩაწერეთ ვარიანტები.")
        else:
            self.accept()

    def _items(self):
        return [t.strip() for t in self._choices.toPlainText().splitlines() if t.strip()]

    def result_op(self):
        kind = self.KINDS[self._kind.currentIndex()][0]
        opts = {"choices": self._items()}
        if kind == "area":
            kind, opts = "text", {"multiline": True}
        return kind, self._name.text().strip().replace(".", "_"), opts


class WatermarkDialog(PagesMixin, QDialog):
    def __init__(self, n, parent=None):
        super().__init__(parent)
        self.setWindowTitle("წყლის ნიშანი")
        form = QFormLayout(self)
        self._text = QLineEdit("ნიმუში")
        self._size = QSpinBox()
        self._size.setRange(8, 300)
        self._size.setValue(72)
        self._angle = QSpinBox()
        self._angle.setRange(-90, 90)
        self._angle.setValue(45)
        self._opacity = QSpinBox()
        self._opacity.setRange(5, 100)
        self._opacity.setValue(25)
        self._opacity.setSuffix(" %")
        self._color = ColorButton((.8, 0, 0))
        self._bold = QCheckBox("მუქი")
        self._under = QCheckBox("ტექსტის ქვეშ")
        for t, w in (("ტექსტი:", self._text), ("ზომა:", self._size), ("კუთხე:", self._angle),
                     ("გამჭვირვალობა:", self._opacity), ("ფერი:", self._color), ("", self._bold),
                     ("", self._under)):
            form.addRow(t, w)
        self.add_pages(form, n)
        form.addRow(_buttons(self, "დამატება", self.try_accept))

    def try_accept(self):
        if not self._text.text().strip():
            QMessageBox.warning(self, self.windowTitle(), "ჩაწერეთ ტექსტი.")
        elif self.check_pages():
            self.accept()

    def text(self):
        return self._text.text().strip()

    def options(self):
        return {"size": self._size.value(), "angle": self._angle.value(),
                "opacity": self._opacity.value() / 100, "color": self._color.value(),
                "bold": self._bold.isChecked(), "over": not self._under.isChecked()}


class HeaderDialog(PagesMixin, QDialog):
    PLACES = [("tl", "ზემოთ მარცხნივ"), ("tc", "ზემოთ შუაში"), ("tr", "ზემოთ მარჯვნივ"),
              ("bl", "ქვემოთ მარცხნივ"), ("bc", "ქვემოთ შუაში"), ("br", "ქვემოთ მარჯვნივ")]

    def __init__(self, n, bates=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bates ნუმერაცია" if bates else "კოლონტიტული და ნუმერაცია")
        form = QFormLayout(self)
        self._texts = {}
        for key, name in self.PLACES:
            self._texts[key] = QLineEdit()
            form.addRow(name + ":", self._texts[key])
        self._texts["br" if bates else "bc"].setText("{bates}" if bates else "{n} / {N}")
        form.addRow(QLabel("{n} გვერდის ნომერი · {N} სულ · {date} თარიღი · {bates} Bates ნომერი"))
        self._size = QDoubleSpinBox()
        self._size.setRange(5, 40)
        self._size.setValue(10)
        self._margin = QSpinBox()
        self._margin.setRange(5, 150)
        self._margin.setValue(30)
        self._color = ColorButton((0, 0, 0))
        self._first = QSpinBox()
        self._first.setRange(0, 99999)
        self._first.setValue(1)
        self._prefix = QLineEdit()
        self._start = QSpinBox()
        self._start.setRange(0, 99999999)
        self._start.setValue(1)
        self._digits = QSpinBox()
        self._digits.setRange(1, 12)
        self._digits.setValue(6)
        for t, w in (("ზომა:", self._size), ("დაშორება კიდიდან:", self._margin),
                     ("ფერი:", self._color), ("პირველი ნომერი:", self._first)):
            form.addRow(t, w)
        if bates:
            for t, w in (("Bates პრეფიქსი:", self._prefix), ("Bates საწყისი:", self._start),
                         ("ციფრების რაოდენობა:", self._digits)):
                form.addRow(t, w)
        self.add_pages(form, n)
        form.addRow(_buttons(self, "დამატება", self.try_accept))

    def try_accept(self):
        if not any(w.text().strip() for w in self._texts.values()):
            QMessageBox.warning(self, self.windowTitle(), "ჩაწერეთ ტექსტი ერთ ადგილზე მაინც.")
        else:
            super().try_accept()

    def options(self):
        return {"texts": {k: w.text() for k, w in self._texts.items()},
                "size": self._size.value(), "margin": self._margin.value(),
                "color": self._color.value(), "first": self._first.value(),
                "prefix": self._prefix.text(), "start": self._start.value(),
                "digits": self._digits.value(), "date": time.strftime("%d.%m.%Y")}


class ScrubDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ფარული მონაცემების წაშლა")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("რა წაიშალოს:"))
        self._boxes = {}
        for key, text, on in (("metadata", "ავტორი, სათაური, კომენტარების ავტორები და სხვა თვისებები", True),
                              ("files", "მიმაგრებული ფაილები", True),
                              ("javascript", "JavaScript", True),
                              ("comments", "ყველა კომენტარი", False),
                              ("hidden", "უხილავი ტექსტი და გამორთული ფენები (OCR ტექსტიც წაიშლება)",
                               False)):
            self._boxes[key] = QCheckBox(text)
            self._boxes[key].setChecked(on)
            lay.addWidget(self._boxes[key])
        note = QLabel("ფოტოებიდან (JPEG) კამერისა და ადგილის მონაცემებიც იშლება; სხვა ფორმატის სურათებში "
                      "ისინი რჩება. სარეზერვო ასლებში ეს ყველაფერი რჩება. შენახვის შემდეგ პროგრამა შემოგთავაზებთ მათ წაშლას.")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addWidget(_buttons(self, "წაშლა", kind="danger"))

    def options(self):
        return {k: b.isChecked() for k, b in self._boxes.items()}


class ProtectDialog(QDialog):
    def __init__(self, protected, parent=None):
        super().__init__(parent)
        self.setWindowTitle("პაროლი და შეზღუდვები")
        self._protected = protected
        form = QFormLayout(self)
        self._mode = QComboBox()
        self._mode.addItems(["პაროლის დაყენება"] + (["დაცვის მოხსნა"] if protected else []))
        form.addRow("", self._mode)
        self._auth = QLineEdit()
        self._auth.setEchoMode(QLineEdit.EchoMode.Password)
        if protected:
            form.addRow("ამჟამინდელი მფლობელის პაროლი:", self._auth)
        self._user, self._user2, self._owner = QLineEdit(), QLineEdit(), QLineEdit()
        for w in (self._user, self._user2, self._owner):
            w.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("გახსნის პაროლი:", self._user)
        form.addRow("გაიმეორეთ:", self._user2)
        form.addRow("მფლობელის პაროლი (შეზღუდვებისთვის):", self._owner)
        self._owner2 = QLineEdit()
        self._owner2.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("გაიმეორეთ:", self._owner2)
        self._perms = {}
        for key, text in (("print", "ბეჭდვა"), ("copy", "კოპირება"),
                          ("annotate", "კომენტარი და ფორმის შევსება"), ("modify", "შეცვლა")):
            self._perms[key] = QCheckBox(text)
            self._perms[key].setChecked(True)
            form.addRow("ნებადართულია:" if key == "print" else "", self._perms[key])
        note = QLabel("დამახსოვრეთ პაროლი: მისი აღდგენა შეუძლებელია. დაშიფვრა: AES-256. ძველი, უპაროლო "
                      "სარეზერვო ასლები შენახვის შემდეგაც რჩება; პროგრამა მათ წაშლას შემოგთავაზებთ.")
        note.setWordWrap(True)
        form.addRow(note)
        form.addRow(_buttons(self, "კარგი", self._check))

    def _check(self):
        if self._mode.currentIndex() == 0:
            if self._owner.text() != self._owner2.text():
                QMessageBox.warning(self, self.windowTitle(), "მფლობელის პაროლები არ ემთხვევა.")
                return
            if self._user.text() != self._user2.text():
                QMessageBox.warning(self, self.windowTitle(), "პაროლები არ ემთხვევა.")
                return
            limited = not all(b.isChecked() for b in self._perms.values())
            if not self._user.text() and not self._owner.text():
                QMessageBox.warning(self, self.windowTitle(), "მიუთითეთ მინიმუმ ერთი პაროლი.")
                return
            if limited and (not self._owner.text() or self._owner.text() == self._user.text()):
                QMessageBox.warning(self, self.windowTitle(), "შეზღუდვებს გახსნის პაროლისგან "
                                    "განსხვავებული მფლობელის პაროლი სჭირდება.")
                return
        self.accept()

    def options(self):
        return {"mode": "none" if self._mode.currentIndex() else "set", "auth": self._auth.text(),
                "user": self._user.text(), "owner": self._owner.text(),
                "perms": {k: b.isChecked() for k, b in self._perms.items()}}


class CompressDialog(QDialog):
    LEVELS = [("მაღალი ხარისხი (200 dpi)", 200, 85), ("საშუალო (150 dpi)", 150, 75),
              ("მცირე ზომა (100 dpi)", 100, 60), ("მინიმალური (72 dpi)", 72, 45)]

    def __init__(self, size, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ფაილის შემცირება")
        form = QFormLayout(self)
        form.addRow(QLabel(f"ახლანდელი ზომა: {size / 1048576:.2f} მბ"))
        self._level = QComboBox()
        self._level.addItems([x[0] for x in self.LEVELS])
        self._level.setCurrentIndex(1)
        self._gray = QCheckBox("სურათები შავ-თეთრად")
        form.addRow("დონე:", self._level)
        form.addRow("", self._gray)
        form.addRow(_buttons(self, "შემცირება"))

    def options(self):
        _, dpi, q = self.LEVELS[self._level.currentIndex()]
        return {"dpi": dpi, "quality": q, "gray": self._gray.isChecked()}


class OcrDialog(PagesMixin, QDialog):
    LANGS = [("ქართული + ინგლისური", "kat+eng"), ("ქართული", "kat"), ("ინგლისური", "eng"),
             ("რუსული", "rus"), ("ქართული + რუსული", "kat+rus"),
             ("ქართული + ინგლისური + რუსული", "kat+eng+rus")]

    def __init__(self, n, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ტექსტის ამოცნობა (OCR)")
        form = QFormLayout(self)
        self._lang = QComboBox()
        self._lang.addItems([x[0] for x in self.LANGS])
        form.addRow("ენა:", self._lang)
        self.add_pages(form, n)
        self._force = QCheckBox("იმ გვერდებზეც, სადაც ტექსტი უკვე არის")
        form.addRow("", self._force)
        note = QLabel("ამოცნობილი ტექსტი უხილავად ედება სურათს: გვერდის იერი არ იცვლება.")
        note.setWordWrap(True)
        form.addRow(note)
        form.addRow(_buttons(self, "დაწყება", self.try_accept))

    def languages(self):
        return self.LANGS[self._lang.currentIndex()][1]

    def force(self):
        return self._force.isChecked()

    def pages(self):
        text = self.pages_text()
        got = files.parse_group(text, self._n) if text else range(self._n)
        return sorted(set(got))


class _Canvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(520, 180)
        self.strokes = []

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.white)
        self.draw(p, 1)

    def draw(self, p, k):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.color("ink-signature"), 2.6 * k, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        for s in self.strokes:
            for a, b in zip(s, s[1:]):
                p.drawLine(a * k, b * k)

    def mousePressEvent(self, e):
        self.strokes.append([e.position()])

    def mouseMoveEvent(self, e):
        if self.strokes:
            self.strokes[-1].append(e.position())
            self.update()

    def image(self):
        pts = [q for s in self.strokes for q in s]
        if len(pts) < 2:
            return None
        k = 4
        img = QImage(self.width() * k, self.height() * k, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        self.draw(p, k)
        p.end()
        xs, ys = [q.x() for q in pts], [q.y() for q in pts]
        r = QRectF(QPointF(min(xs) - 6, min(ys) - 6), QPointF(max(xs) + 6, max(ys) + 6))
        box = QRectF(r.x() * k, r.y() * k, r.width() * k, r.height() * k).toRect()
        return img.copy(box.intersected(img.rect()))


class SignaturePad(QDialog):
    """A signature as a picture: drawn, typed, or taken from a file."""

    def __init__(self, parent=None, folder=None):
        super().__init__(parent)
        self.setWindowTitle("ხელმოწერის სურათი")
        self.path = None
        self._folder = folder
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.canvas = _Canvas()
        draw = QWidget()
        v = QVBoxLayout(draw)
        v.addWidget(self.canvas)
        clear = QPushButton("გასუფთავება")
        clear.clicked.connect(lambda: (self.canvas.strokes.clear(), self.canvas.update()))
        v.addWidget(clear)
        self.tabs.addTab(draw, "დახატვა")
        typed = QWidget()
        v = QVBoxLayout(typed)
        self.typed = QLineEdit()
        self.typed.setPlaceholderText("სახელი და გვარი")
        v.addWidget(self.typed)
        v.addStretch()
        self.tabs.addTab(typed, "აკრეფა")
        pick = QWidget()
        v = QVBoxLayout(pick)
        self.file = QLineEdit()
        b = QPushButton("ფაილის არჩევა…")
        b.clicked.connect(self._choose)
        v.addWidget(self.file)
        v.addWidget(b)
        v.addStretch()
        self.tabs.addTab(pick, "სურათი")
        lay.addWidget(self.tabs)
        note = QLabel("ეს მხოლოდ სურათია. იურიდიული ძალის ციფრული ხელმოწერისთვის გამოიყენეთ სერტიფიკატი.")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addWidget(_buttons(self, "ჩასმა", self._check))

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "სურათი", "",
                                              "სურათები (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.file.setText(path)

    def _check(self):
        i = self.tabs.currentIndex()
        if i == 2:
            if os.path.isfile(self.file.text()):
                self.path = self.file.text()
                self.accept()
            else:
                QMessageBox.warning(self, self.windowTitle(), "აირჩიეთ სურათის ფაილი.")
            return
        img = self.canvas.image() if i == 0 else self._typed_image()
        if img is None:
            QMessageBox.warning(self, self.windowTitle(),
                                "ჯერ დახატეთ ხელმოწერა." if i == 0 else "ჩაწერეთ სახელი და გვარი.")
            return
        # inside the session folder, which is removed with the tab
        self.path = files.unique_path(os.path.join(self._folder or files.new_session(),
                                                   "signature.png"))
        img.save(self.path)
        self.accept()

    def _typed_image(self):
        text = self.typed.text().strip()
        if not text:
            return None
        font = QFont()
        font.setFamilies(["Segoe Script", "Sylfaen"])
        font.setItalic(True)
        font.setPixelSize(120)
        from PySide6.QtGui import QFontMetrics
        m = QFontMetrics(font)
        img = QImage(m.horizontalAdvance(text) + 80, m.height() + 40, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(font)
        p.setPen(theme.color("ink-signature"))
        p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, text)
        p.end()
        return img


class SignDialog(QDialog):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ციფრული ხელმოწერა")
        self.client = client
        form = QFormLayout(self)
        row = QHBoxLayout()
        self._pfx = QLineEdit()
        pick = QPushButton("…")
        pick.setFixedWidth(34)
        pick.clicked.connect(self._choose)
        new = QPushButton("ახლის შექმნა…")
        new.clicked.connect(self._create)
        row.addWidget(self._pfx)
        row.addWidget(pick)
        row.addWidget(new)
        form.addRow("სერტიფიკატი (.pfx/.p12):", row)
        self._pw = QLineEdit()
        self._pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("სერტიფიკატის პაროლი:", self._pw)
        self._reason, self._place = QLineEdit(), QLineEdit()
        form.addRow("მიზეზი:", self._reason)
        form.addRow("ადგილი:", self._place)
        note = QLabel("თვითხელმოწერილი სერტიფიკატი ადასტურებს, რომ ფაილი არ შეცვლილა, მაგრამ პიროვნებას "
                      "არავინ ადასტურებს და კვალიფიციურ ხელმოწერას არ ცვლის.")
        note.setWordWrap(True)
        form.addRow(note)
        form.addRow(_buttons(self, "ხელმოწერა", self._check))

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "სერტიფიკატი", "", "სერტიფიკატი (*.pfx *.p12)")
        if path:
            self._pfx.setText(path)

    def _create(self):
        name, ok = QInputDialog.getText(self, "ახალი სერტიფიკატი", "სახელი და გვარი:")
        if not ok or not name.strip():
            return
        pw, ok = QInputDialog.getText(self, "ახალი სერტიფიკატი", "პაროლი (მინ. 6 სიმბოლო):",
                                      QLineEdit.EchoMode.Password)
        if not ok:
            return
        if len(pw) < 6:
            QMessageBox.warning(self, self.windowTitle(), "პაროლი მინიმუმ 6 სიმბოლო უნდა იყოს.")
            return
        again, ok = QInputDialog.getText(self, "ახალი სერტიფიკატი", "გაიმეორეთ პაროლი:",
                                         QLineEdit.EchoMode.Password)
        if not ok:
            return
        if again != pw:
            QMessageBox.warning(self, self.windowTitle(), "პაროლები არ ემთხვევა.")
            return
        out, _ = QFileDialog.getSaveFileName(self, "სად შევინახო", files.unique_path(
            os.path.expanduser(f"~/{files.safe_name(name)}.pfx")), "სერტიფიკატი (*.pfx)")
        if not out:
            return
        tmp = files.temp_name(out)
        try:
            run_task(self.client, "make_certificate", (tmp, pw, name.strip()), "იქმნება…", self)
            files.put_in_place(tmp, out)  # a certificate that was there is kept as a backup
        except (TaskError, OSError) as e:
            QMessageBox.warning(self, self.windowTitle(), "სერტიფიკატი ვერ შეიქმნა. " + error_text(
                str(e) if isinstance(e, TaskError) else "no_access"))
            return
        finally:
            files.discard(tmp)
        self._pfx.setText(out)
        self._pw.setText(pw)

    def _check(self):
        if os.path.isfile(self._pfx.text()):
            self.accept()
        else:
            QMessageBox.warning(self, self.windowTitle(), "აირჩიეთ სერტიფიკატის ფაილი.")

    def pfx(self):
        return self._pfx.text()

    def password(self):
        return self._pw.text()

    def options(self):
        return {"reason": self._reason.text().strip(), "location": self._place.text().strip()}


class ChoicesDialog(QDialog):
    """Several choices of one list field."""

    def __init__(self, title, items, chosen, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("აირჩიეთ ერთი ან რამდენიმე:"))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for t in items:
            self.list.addItem(t)
            self.list.item(self.list.count() - 1).setSelected(t in chosen)
        lay.addWidget(self.list)
        lay.addWidget(_buttons(self))

    def chosen(self):
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).isSelected()]


class VerifyDialog(QDialog):
    def __init__(self, rows, dirty, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ხელმოწერების შემოწმება")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        if dirty:
            lay.addWidget(QLabel("შემოწმდა შენახული ფაილი, შეუნახავი ცვლილებების გარეშე."))
        if not rows:
            lay.addWidget(QLabel("ამ ფაილში ციფრული ხელმოწერა არ არის."))
        for r in rows:
            lay.addWidget(self._row(r))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def describe(r):
        if r["note"]:
            return [f"⚠ ხელმოწერა ვერ შემოწმდა ({r['note']})"]
        out = ["✔ ხელმოწერილი ნაწილი არ შეცვლილა" if r["intact"]
               else "✖ ფაილი ხელმოწერის შემდეგ შეიცვალა ან ხელმოწერა არასწორია"]
        if r["intact"]:
            out.append("✔ სერტიფიკატი ამ კომპიუტერის სანდო სიიდანაა (გაუქმება არ შემოწმებულა)" if r["trusted"]
                       else "⚠ ხელმომწერის ვინაობა დადასტურებული არ არის"
                       + (" (თვითხელმოწერილი სერტიფიკატი)" if r.get("self_signed") else ""))
            if r.get("modified_badly"):
                out.append("✖ ხელმოწერის შემდეგ აკრძალული ცვლილებაა შეტანილი")
            elif r.get("changed_later"):
                out.append("⚠ ხელმოწერის შემდეგ ფაილს რაღაც დაემატა")
        return out

    @staticmethod
    def _row(r):
        when = [r["time"] + (" (დროის შტამპით)" if r.get("time_proved") else
                             " (ხელმომწერის კომპიუტერის დრო, დადასტურებული არ არის)")] if r["time"] else []
        lab = QLabel("\n".join([r["signer"] or r["field"]] + when + VerifyDialog.describe(r)))
        lab.setTextFormat(Qt.TextFormat.PlainText)
        lab.setWordWrap(True)
        lab.setFrameShape(QLabel.Shape.StyledPanel)
        lab.setMargin(8)
        return lab


class CompareDialog(QDialog):
    chosen = Signal(int, object)

    def __init__(self, old, new, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("შედარების შედეგი")
        self.resize(460, 560)
        self.result_ = result
        lay = QVBoxLayout(self)
        head = QLabel(f"ძველი: {old} ({result['pages'][0]} გვ.)\nახალი: {new} "
                      f"({result['pages'][1]} გვ.)\nტექსტის ცვლილება: {len(result['changes'])}, "
                      f"სურათის ცვლილება: {len(result['pictures'])} გვერდზე")
        head.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(head)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        for c in result["changes"]:
            mark = {"replace": "შეიცვალა", "delete": "წაიშალა", "insert": "დაემატა"}[c["kind"]]
            old_t, new_t = self._cut(c["old"]), self._cut(c["new"])
            text = {"replace": f"„{old_t}“ → „{new_t}“", "delete": f"„{old_t}“",
                    "insert": f"„{new_t}“"}[c["kind"]]
            it = QListWidgetItem(f"გვ. {c['page'] + 1} · {mark}: {text}")
            at = c["new_at"][0] if c["new_at"] else (c["page"], None)
            it.setData(Qt.ItemDataRole.UserRole, (at[0], at[1]))
            self.list.addItem(it)
        for p in result["pictures"]:
            it = QListWidgetItem(f"გვ. {p + 1} · გამოსახულება განსხვავდება")
            it.setData(Qt.ItemDataRole.UserRole, (p, None))
            self.list.addItem(it)
        if not self.list.count():
            self.list.addItem("განსხვავება ვერ ვიპოვე")
        self.list.itemClicked.connect(self._pick)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        save = QPushButton("ანგარიშის შენახვა…")
        save.clicked.connect(self._save)
        close = QPushButton("დახურვა")
        close.clicked.connect(self.reject)
        row.addWidget(save)
        row.addStretch()
        row.addWidget(close)
        lay.addLayout(row)

    @staticmethod
    def _cut(t):
        return t if len(t) < 160 else t[:157] + "…"

    def _pick(self, it):
        data = it.data(Qt.ItemDataRole.UserRole)
        if data:
            self.chosen.emit(data[0], data[1])

    def _save(self):
        out, _ = QFileDialog.getSaveFileName(self, "ანგარიში", "შედარება.txt", "ტექსტი (*.txt)")
        if out:
            try:
                with open(out, "w", encoding="utf-8-sig") as f:
                    f.write("\n".join(self.list.item(i).text() for i in range(self.list.count())))
            except OSError:
                QMessageBox.warning(self, self.windowTitle(), error_text("no_access"))
