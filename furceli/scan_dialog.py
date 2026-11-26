"""Window for cleaning photos and scans into a searchable PDF."""
from __future__ import annotations

import os
import shutil

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressDialog,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from . import files, theme
from .client import EngineClient, TaskError, run_task
from .convert_names import IMAGES
from .dialogs import PDF_FILTER, error_text, inspect_file, warn_unprotected
from .forms import OcrDialog

MODES = [("ფერადი (ბეჭდებისთვის)", "color"), ("ნაცრისფერი", "gray"), ("შავ-თეთრი", "bw"),
         ("ორიგინალი, მხოლოდ გასწორება", "original")]
FULL = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
P_LOOK, P_FIND = 10, 30


def to_image(v) -> QImage:
    w, h, data = v
    return QImage(data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


def convex(pts) -> bool:
    """Clockwise and without a dent: the only shape a sheet can have."""
    for i in range(4):
        (ax, ay), (bx, by), (cx, cy) = pts[i - 2], pts[i - 1], pts[i]
        if (bx - ax) * (cy - by) - (by - ay) * (cx - bx) < 1e-4:
            return False
    return True


def _same(a, b) -> bool:
    """Do two names mean one file, however they are spelled?"""
    if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


class CornerView(QWidget):
    """The original picture with the sheet's corners to drag."""

    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(260, 300)
        self.setMouseTracking(True)
        self.image, self.corners = None, FULL
        self._drag, self._old = None, None

    def set(self, image, corners):
        self.image, self.corners = image, tuple(corners or FULL)
        self._drag = None
        self.update()

    def _box(self):
        if self.image is None:
            return QRectF()
        w, h = self.image.width(), self.image.height()
        k = min((self.width() - 24) / w, (self.height() - 24) / h)
        return QRectF((self.width() - w * k) / 2, (self.height() - h * k) / 2, w * k, h * k)

    def _pts(self):
        b = self._box()
        return [QPointF(b.x() + x * b.width(), b.y() + y * b.height()) for x, y in self.corners]

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.color("canvas"))
        if self.image is None:
            p.setPen(theme.color("ink-muted"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "დაამატეთ ფოტო, სკანი ან PDF")
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawImage(self._box(), self.image)
        pts = self._pts()
        p.setPen(QPen(theme.color("mark-crop"), 2))
        p.setBrush(theme.color("mark-crop-fill"))
        p.drawPolygon(QPolygonF(pts))
        p.setBrush(theme.color("paper"))
        for i, q in enumerate(pts):
            p.drawEllipse(q, 9 if i == self._drag else 7, 9 if i == self._drag else 7)

    def _near(self, pos):
        d = [(q - pos).manhattanLength() for q in self._pts()]
        i = min(range(4), key=d.__getitem__)
        return i if d[i] < 34 else None

    def mousePressEvent(self, e):
        if self.image is not None and e.button() == Qt.MouseButton.LeftButton:
            self._drag, self._old = self._near(e.position()), self.corners
            self.update()

    def mouseMoveEvent(self, e):
        if self.image is None:
            return
        if self._drag is None:
            self.setCursor(Qt.CursorShape.SizeAllCursor if self._near(e.position()) is not None
                           else Qt.CursorShape.ArrowCursor)
            return
        b = self._box()
        x = min(max((e.position().x() - b.x()) / b.width(), 0.0), 1.0)
        y = min(max((e.position().y() - b.y()) / b.height(), 0.0), 1.0)
        pts = list(self.corners)
        pts[self._drag] = (x, y)
        self.corners = tuple(pts)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is None or e.button() != Qt.MouseButton.LeftButton:
            return
        self._drag = None
        if not convex(self.corners):
            self.corners = self._old  # crossed sides: take it back
        elif self.corners != self._old:
            self.changed.emit(self.corners)
        self.update()


class ResultView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(260, 300)
        self.image, self.note = None, ""

    def set(self, image, note=""):
        self.image, self.note = image, note
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.color("canvas"))
        if self.image is None:
            p.setPen(theme.color("ink-muted"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.note)
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.image.width(), self.image.height()
        k = min((self.width() - 24) / w, (self.height() - 24) / h)
        box = QRectF((self.width() - w * k) / 2, (self.height() - h * k) / 2, w * k, h * k)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for grow, alpha in ((4, 10), (2, 18), (1, 30)):  # shadow-page, roughly
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(box.adjusted(-grow, -grow + 2, grow, grow + 2), 3, 3)
        p.drawImage(box, self.image)


class ScanDialog(QDialog):
    def __init__(self, parent=None, paths=()):
        super().__init__(parent)
        self.setWindowTitle("სკანის გასუფთავება")
        self.resize(1120, 720)
        self.result_path, self.words = None, 0
        self.pages: list[dict] = []
        self._ids = 0
        self.client = EngineClient(self)
        self.client.done.connect(self._done)
        self.client.failed.connect(self._failed)
        self.list = QListWidget()
        self.list.setIconSize(QSize(64, 84))
        self.list.currentRowChanged.connect(lambda *_: self._show())
        self.before, self.after = CornerView(), ResultView()
        self.before.changed.connect(self._corners_moved)
        left = QWidget()
        v = QVBoxLayout(left)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.list)
        for row in ((("დამატება…", self.add), ("ამოღება", self.remove)),
                    (("ზემოთ", lambda: self.shift(-1)), ("ქვემოთ", lambda: self.shift(1)))):
            h = QHBoxLayout()
            for text, slot in row:
                h.addWidget(self._button(text, slot))
            v.addLayout(h)
        split = QSplitter()
        for w, title in ((left, None), (self.before, "ორიგინალი: კუთხეები გადაათრიეთ"),
                         (self.after, "შედეგი")):
            if title:
                box = QWidget()
                b = QVBoxLayout(box)
                b.setContentsMargins(4, 0, 4, 0)
                b.addWidget(QLabel(title))
                b.addWidget(w, 1)
                w = box
            split.addWidget(w)
        split.setSizes([200, 450, 450])
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        tools = QHBoxLayout()
        self.auto = self._button("კუთხეები ავტომატურად", self.find_again)
        self.full = self._button("მთელი კადრი", lambda: self._set(corners=None))
        self.left = self._button("მარცხნივ მობრუნება", lambda: self._turn(-1))
        self.right = self._button("მარჯვნივ მობრუნება", lambda: self._turn(1))
        self.deskew = QCheckBox("სტრიქონების გასწორება")
        self.deskew.setChecked(True)
        self.deskew.toggled.connect(lambda on: self._set(deskew=on))
        for w in (self.auto, self.full, self.left, self.right, self.deskew):
            tools.addWidget(w)
        tools.addStretch()
        lay.addLayout(tools)
        opts = QHBoxLayout()
        self.mode, self.lang = QComboBox(), QComboBox()
        self.mode.addItems([m[0] for m in MODES])
        self.lang.addItems([x[0] for x in OcrDialog.LANGS] + ["ტექსტის ამოცნობის გარეშე"])
        self.mode.currentIndexChanged.connect(lambda *_: self._mode_changed())
        for text, w in (("რეჟიმი:", self.mode), ("ტექსტის ენა:", self.lang)):
            opts.addWidget(QLabel(text))
            opts.addWidget(w)
        opts.addStretch()
        self.go = theme.variant(self._button("PDF-ის შექმნა…", self.create), "primary")
        opts.addWidget(self._button("დახურვა", self.reject))
        opts.addWidget(self.go)  # the main action last
        lay.addLayout(opts)
        self.note = QLabel("საწყისი ფაილები არ იცვლება. ყველაფერი ამ კომპიუტერზე სრულდება.")
        self.note.setProperty("role", "muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._ask_preview)
        self._sync()
        if paths:
            QTimer.singleShot(0, lambda: self.add_paths(paths))

    def _button(self, text, slot):
        b = QPushButton(text)
        icons = {"ზემოთ": "arrow-up", "ქვემოთ": "arrow-down",
                 "მარცხნივ მობრუნება": "rotate-page-left", "მარჯვნივ მობრუნება": "rotate-page-right"}
        if text in icons:
            b.setText("")
            b.setIcon(theme.icon(icons[text]))
            b.setToolTip(text)
            b.setAccessibleName(text)
        if text in ("დახურვა", "ამოღება", "ზემოთ", "ქვემოთ"):
            theme.variant(b, "ghost")
        b.setAutoDefault(False)
        b.clicked.connect(lambda *_: slot())
        return b

    # -- pages -------------------------------------------------------------
    @property
    def current(self):
        r = self.list.currentRow()
        return self.pages[r] if 0 <= r < len(self.pages) else None

    def languages(self):
        i = self.lang.currentIndex()
        return OcrDialog.LANGS[i][1] if i < len(OcrDialog.LANGS) else ""

    def mode_key(self):
        return MODES[self.mode.currentIndex()][1]

    def add(self):
        kinds = " ".join("*" + e for e in sorted(IMAGES)) + " *.pdf"
        paths, _ = QFileDialog.getOpenFileNames(self, "ფოტოები, სკანები, PDF", "", f"ფაილები ({kinds})")
        self.add_paths(paths)

    def add_paths(self, paths):
        for path in paths:
            password, text = "", False
            try:
                if path.lower().endswith(".pdf"):
                    got = inspect_file(self.client, path, self)
                    if got is None:
                        continue
                    password = got[1]
                    if got[2] and not warn_unprotected(self):
                        continue
                info = run_task(self.client, "scan_count", (path, password), "იხსნება…", self)
                n, text = info["pages"], info["text"]
            except TaskError as e:
                QMessageBox.warning(self, os.path.basename(path), error_text(str(e)))
                continue
            if text and QMessageBox.question(
                    self, os.path.basename(path), "ამ PDF-ს ტექსტი უკვე აქვს. გასუფთავება გვერდებს სურათად "
                    "აქცევს და ტექსტს თავიდან ამოიცნობს, რაც ნაკლებად ზუსტია. მაინც დავამატო?"
                    ) != QMessageBox.StandardButton.Yes:
                continue
            for i in range(n):
                self._ids += 1
                page = {"id": self._ids, "path": path, "index": i, "password": password,
                        "o": {"corners": None, "turn": 0, "deskew": True}, "found": False,
                        "before": None, "after": None, "error": "", "note": "",
                        "seen": "",       # the settings the preview shows
                        "again": False}   # only the corners are being looked for
                self.pages.append(page)
                name = os.path.basename(path) + (f"  ({i + 1})" if n > 1 else "")
                self.list.addItem(QListWidgetItem(name))
                self.client.submit(("find", page["id"]), P_FIND, "scan_analyse", path, i, password,
                                   self.languages() or "kat+eng")
        if self.pages and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self._sync()

    def remove(self):
        r = self.list.currentRow()
        if r >= 0:
            gone = self.pages.pop(r)
            self.client.cancel(lambda k: k[0] in ("find", "look") and k[1] == gone["id"])
            self.list.takeItem(r)
            self._show()
        self._sync()

    def shift(self, d):
        r, to = self.list.currentRow(), self.list.currentRow() + d
        if r < 0 or not 0 <= to < len(self.pages):
            return
        self.pages[r], self.pages[to] = self.pages[to], self.pages[r]
        it = self.list.takeItem(r)
        self.list.insertItem(to, it)
        self.list.setCurrentRow(to)

    def _by_id(self, i):
        return next((p for p in self.pages if p["id"] == i), None)

    def _sync(self):
        on = self.current is not None and self.current["found"]
        for w in (self.auto, self.full, self.left, self.right, self.deskew):
            w.setEnabled(on)
        self.before.setEnabled(on)
        self.go.setEnabled(bool(self.pages) and all(p["found"] for p in self.pages))
        waiting = sum(not p["found"] for p in self.pages)
        self.go.setText(f"მუშავდება… ({waiting})" if waiting else "PDF-ის შექმნა…")

    # -- engine answers ----------------------------------------------------
    def _done(self, key, value):
        page = self._by_id(key[1]) if key[0] in ("find", "look") else None
        if page is None:
            return
        if key[0] == "find":
            page["o"]["corners"] = value["corners"]
            if not page["again"]:  # a turn the user has set stays
                page["o"]["turn"] = value["turn"]
            page["found"], page["error"], page["again"] = True, "", False
            if page is self.current:
                self._show()
            else:
                self._look(page, P_FIND + 5)
            self._sync()
        elif key[2] == self._stamp(page):
            page["before"], page["after"] = (to_image(v) for v in value)
            page["seen"], page["note"] = key[2], ""
            row = self.pages.index(page)
            self.list.item(row).setIcon(QIcon(QPixmap.fromImage(page["after"])))
            if page is self.current:
                self._paint(page)

    def _failed(self, key, code):
        page = self._by_id(key[1]) if key[0] in ("find", "look") else None
        if page is None:
            return
        if key[0] == "look":  # one preview: other settings, or another try, may well work
            if key[2] == self._stamp(page) and code != "cancelled":
                page["note"] = error_text(code)
        elif page["again"]:
            page["found"], page["again"], page["note"] = True, False, error_text(code)
        else:
            page["found"], page["error"] = True, error_text(code)
        if page is self.current:
            self._paint(page)
        self._sync()

    def _stamp(self, page):
        return repr(sorted(page["o"].items())) + self.mode_key()

    def _look(self, page, priority=P_LOOK):
        if page["found"] and not page["error"]:
            self.client.cancel(lambda k: k[0] == "look" and k[1] == page["id"])
            self.client.submit(("look", page["id"], self._stamp(page)), priority, "scan_preview",
                               page["path"], page["index"], dict(page["o"], mode=self.mode_key()),
                               page["password"])

    def _show(self):
        page = self.current
        self._sync()
        if page is None:
            self.before.set(None, None)
            self.after.set(None)
            return
        self.deskew.blockSignals(True)
        self.deskew.setChecked(page["o"]["deskew"])
        self.deskew.blockSignals(False)
        self._paint(page)
        self._timer.start()

    def _paint(self, page):
        self.before.set(page["before"], page["o"]["corners"])
        bad = page["error"] or page["note"]
        self.after.set(page["after"] if not bad else None, bad or ("მუშავდება…" if not page["after"] else ""))

    def _ask_preview(self):
        if self.current:
            self._look(self.current)

    # -- changes -----------------------------------------------------------
    def _set(self, **o):
        page = self.current
        if page:
            page["o"].update(o)
            page["note"] = ""
            self._paint(page)
            self._timer.start()

    def _corners_moved(self, corners):
        self._set(corners=corners)

    def _turn(self, d):
        if self.current:
            self._set(turn=(self.current["o"]["turn"] + d) % 4)

    def find_again(self):
        page = self.current
        if page:
            page["again"] = page["again"] or not page["error"]
            page["found"] = False
            self.client.submit(("find", page["id"]), P_LOOK, "scan_analyse", page["path"],
                               page["index"], page["password"], self.languages() or "kat+eng")
            self._sync()

    def _mode_changed(self):
        for p in self.pages:
            p["after"] = None if p is not self.current else p["after"]
        for p in self.pages:
            self._look(p, P_LOOK if p is self.current else P_FIND + 5)

    # -- making the file ---------------------------------------------------
    def create(self):
        good = [p for p in self.pages if not p["error"]]
        bad = len(self.pages) - len(good)
        if not good:
            QMessageBox.warning(self, self.windowTitle(), "დასამუშავებელი გვერდი არ არის.")
            return
        if bad and QMessageBox.question(
                self, self.windowTitle(), f"{bad} გვერდი ვერ იკითხება და გამოტოვდება. გავაგრძელო?"
                ) != QMessageBox.StandardButton.Yes:
            return
        stem = os.path.splitext(good[0]["path"])[0]
        out, _ = QFileDialog.getSaveFileName(self, "სად შევინახო", files.unique_path(
            stem + " (სუფთა).pdf"), PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out = files.unique_path(out + ".pdf")
        if any(_same(p["path"], out) for p in self.pages):
            QMessageBox.warning(self, self.windowTitle(), "აირჩიეთ სხვა სახელი: ეს საწყისი ფაილია.")
            return
        self.client.cancel(lambda k: k[0] in ("look", "find"))
        bar = QProgressDialog("გვერდები მუშავდება…", "გაუქმება", 0, len(good) + 1, self)
        bar.setWindowModality(Qt.WindowModality.WindowModal)
        bar.setMinimumDuration(0)
        folder, tmp = files.new_session(), files.temp_name(out)
        self.words = 0
        try:
            parts = []
            for k, p in enumerate(good):
                bar.setValue(k)
                bar.setLabelText(f"გვერდი {k + 1} / {len(good)}")
                if bar.wasCanceled():
                    return
                part = os.path.join(folder, f"{k}.pdf")
                o = dict(p["o"], mode=self.mode_key(), languages=self.languages())
                self.words += run_task(self.client, "scan_build", (
                    p["path"], p["index"], o, part, p["password"]), "", None, allow=bar)
                parts.append(part)
            if bar.wasCanceled():
                return
            bar.setValue(len(good))
            run_task(self.client, "scan_join", (parts, tmp, os.path.basename(stem)), "", None,
                     allow=bar)
            if bar.wasCanceled():
                return
            files.put_in_place(tmp, out)
            self.result_path = out
        except (TaskError, OSError) as e:
            bar.close()
            if str(e) == "cancelled":
                return
            QMessageBox.warning(self, self.windowTitle(), error_text(
                str(e) if isinstance(e, TaskError) else "no_access"))
            return
        finally:
            bar.close()
            bar.deleteLater()
            shutil.rmtree(folder, ignore_errors=True)
            files.discard(tmp)
            for p in self.pages:  # the previews that were put off
                if p["seen"] != self._stamp(p) and not self.result_path:
                    self._look(p, P_FIND + 5)
        self.accept()

    def done(self, code):
        self._timer.stop()
        self.client.shutdown()
        super().done(code)
