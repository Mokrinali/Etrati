"""Batch processing: the same steps on many files. Originals are never changed."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QProgressDialog, QPushButton,
                               QVBoxLayout)

from . import files
from .client import EngineClient, TaskError, run_task
from .dialogs import PDF_FILTER, error_text
from .forms import CompressDialog, OcrDialog


class BatchDialog(QDialog):
    OUTPUTS = [("PDF", "pdf"), ("Word", "docx"), ("Excel", "xlsx"), ("ტექსტი", "txt"),
               ("HTML", "html")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("სერიული დამუშავება")
        self.resize(560, 480)
        self.report: list[tuple[str, str]] = []
        self._bar = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("შედეგები ცალკე საქაღალდეში ინახება, ორიგინალები არ იცვლება."))
        self.list = QListWidget()
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, slot in (("ფაილების დამატება…", self.add), ("ამოღება", self.remove)):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.ocr = QCheckBox("ტექსტის ამოცნობა (OCR):")
        self.lang = QComboBox()
        self.lang.addItems([x[0] for x in OcrDialog.LANGS])
        self.squeeze = QCheckBox("შემცირება:")
        self.level = QComboBox()
        self.level.addItems([x[0] for x in CompressDialog.LEVELS])
        self.level.setCurrentIndex(1)
        self.turn = QCheckBox("მობრუნება:")
        self.angle = QComboBox()
        self.angle.addItems(["90° მარჯვნივ", "180°", "90° მარცხნივ"])
        self.mark = QCheckBox("წყლის ნიშანი:")
        self.mark_text = QLineEdit("ნიმუში")
        self.lock = QCheckBox("გახსნის პაროლი:")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.clean = QCheckBox("ფარული მონაცემების წაშლა")
        for a, b in ((self.ocr, self.lang), (self.squeeze, self.level), (self.turn, self.angle),
                     (self.mark, self.mark_text), (self.lock, self.password), (self.clean, None)):
            r = QHBoxLayout()
            r.addWidget(a)
            if b is not None:
                r.addWidget(b, 1)
            lay.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel("შედეგის ტიპი:"))
        self.kind = QComboBox()
        self.kind.addItems([x[0] for x in self.OUTPUTS])
        r.addWidget(self.kind, 1)
        lay.addLayout(r)
        r = QHBoxLayout()
        r.addStretch()
        go, close = QPushButton("დაწყება…"), QPushButton("დახურვა")
        go.clicked.connect(self.start)
        close.clicked.connect(self.reject)
        r.addWidget(go)
        r.addWidget(close)
        lay.addLayout(r)

    def add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "ფაილები", "", PDF_FILTER)
        self.list.addItems(paths)

    def remove(self):
        if self.list.currentRow() >= 0:
            self.list.takeItem(self.list.currentRow())

    def paths(self):
        return [self.list.item(i).text() for i in range(self.list.count())]

    def steps(self, pages):
        out = []
        if self.turn.isChecked():
            out.append(("rotate", list(range(pages)), (90, 180, -90)[self.angle.currentIndex()]))
        if self.mark.isChecked() and self.mark_text.text().strip():
            out.append(("watermark", list(range(pages)), self.mark_text.text().strip(),
                        {"size": 72, "color": (.8, 0, 0), "opacity": .25, "angle": 45}))
        if self.clean.isChecked():
            out.append(("scrub", {}))
        if self.squeeze.isChecked():
            _, dpi, q = CompressDialog.LEVELS[self.level.currentIndex()]
            out.append(("compress", {"dpi": dpi, "quality": q}))
        if self.lock.isChecked():
            out.append(("protect", {"mode": "set", "user": self.password.text(), "owner": "",
                                    "perms": dict.fromkeys(("print", "copy", "modify", "annotate"),
                                                           True)}))
        return out

    def start(self):
        if not self.list.count():
            return
        if self.lock.isChecked() and not self.password.text():
            QMessageBox.warning(self, self.windowTitle(), "ჩაწერეთ პაროლი.")
            return
        folder = QFileDialog.getExistingDirectory(self, "სად შევინახო შედეგები")
        if folder:
            self.run(folder)
            good = sum(1 for _, e in self.report if not e)
            bad = [f"• {os.path.basename(p)}: {e}" for p, e in self.report if e]
            QMessageBox.information(self, self.windowTitle(), f"დამუშავდა {good} ფაილი."
                                    + ("\n\nვერ დამუშავდა:\n" + "\n".join(bad[:15]) if bad else ""))

    def run(self, folder):
        self.report = []
        kind = self.OUTPUTS[self.kind.currentIndex()][1]
        paths = self.paths()
        bar = QProgressDialog("მუშავდება…", "გაუქმება", 0, len(paths), self)
        bar.setWindowModality(Qt.WindowModality.WindowModal)
        bar.setMinimumDuration(0)
        self._bar = bar
        for k, path in enumerate(paths):
            bar.setValue(k)
            bar.setLabelText(os.path.basename(path))
            if bar.wasCanceled():
                break
            client = EngineClient(self)  # a fresh engine: one bad file cannot spoil the rest
            try:
                self._one(client, path, folder, kind)
                self.report.append((path, ""))
            except TaskError as e:
                self.report.append((path, error_text(str(e))))
            except OSError:
                self.report.append((path, error_text("no_access")))
            finally:
                client.shutdown()
        bar.close()

    def _one(self, client, path, folder, kind):
        info = run_task(client, "open", (path, ""), "", None, allow=self._bar)
        if not info["can_modify"] or (kind != "pdf" and not info["can_copy"]):
            raise TaskError("not_allowed")
        n = len(info["sizes"])
        if self.ocr.isChecked():
            lang = OcrDialog.LANGS[self.lang.currentIndex()][1]
            layer = {}
            for p in range(n):
                words = run_task(client, "ocr_words", (p, lang, False), "", None, allow=self._bar)
                if words:
                    layer[p] = words
            if layer:
                run_task(client, "apply", (("textlayer", layer),), "", None, allow=self._bar)
        for op in self.steps(n):
            run_task(client, "apply", (op,), "", None, allow=self._bar)
        stem = os.path.splitext(os.path.basename(path))[0]
        out = files.unique_path(os.path.join(folder, f"{stem}.{kind}"))
        tmp = files.unique_path(os.path.join(folder, f".furceli-{os.getpid()}.{kind}"))
        try:
            if kind == "pdf":
                run_task(client, "save", (tmp,), "", None, allow=self._bar)
            else:
                run_task(client, "export_as", (kind, tmp, list(range(n))), "", None, allow=self._bar)
            os.replace(tmp, out)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
