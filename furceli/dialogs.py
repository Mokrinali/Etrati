"""Dialogs for page operations: merge, split, crop, insert."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QRadioButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from . import files, theme
from .client import EngineClient, TaskError, run_task

PDF_FILTER = "PDF ფაილები (*.pdf)"
MM = 72 / 25.4

ERRORS = {
    "not_found": "ფაილი ვერ მოიძებნა. შესაძლოა გადაიტანეს ან წაშალეს.",
    "no_access": "ფაილზე წვდომა არ არის. შესაძლოა სხვა პროგრამაში ღიაა ან უფლება არ გაქვთ.",
    "damaged": "ფაილი დაზიანებულია ან PDF არ არის.",
    "crash": "PDF ძრავა გაითიშა. ფაილი, სავარაუდოდ, დაზიანებულია. თქვენი ორიგინალი არ შეცვლილა.",
    "file_changed": "ფაილი დისკზე სხვა პროგრამამ შეცვალა. დახურეთ ეს ჩანართი და თავიდან გახსენით.",
    "already_signed": "ეს ველი უკვე ხელმოწერილია.",
    "scrub_layers": "ფარული ფენის წაშლა ვერ დადასტურდა, ამიტომ არაფერი შევცვალე. სცადეთ ამ პუნქტის "
                    "გარეშე.",
    "engine_dead": "PDF ძრავა ამ ფაილზე რამდენჯერმე გაითიშა. დახურეთ და თავიდან გახსენით.",
    "not_allowed": "ავტორმა ამ ფაილის გვერდების გამოყენება აკრძალა.",
    "no_pages": "დოკუმენტში ერთი გვერდი მაინც უნდა დარჩეს.",
    "crop_too_small": "ჩამოჭრის შემდეგ გვერდი ძალიან პატარა დარჩებოდა.",
    "copy_failed": "გვერდების კოპირება ვერ მოხერხდა. არაფერი შეცვლილა.",
    "save_failed": "შენახული ფაილი შემოწმებას ვერ გადის, ამიტომ ორიგინალი არ შევცვალე.",
    "scrub_failed": "JavaScript-ის სრულად წაშლა ვერ დადასტურდა, ამიტომ არაფერი შევცვალე.",
    "scrub_changed": "უხილავი ტექსტის წაშლა გვერდის იერს შეცვლიდა, ამიტომ არაფერი შევცვალე. "
                     "სცადეთ ამ პუნქტის გარეშე.",
    "needs_helper": "პროგრამის შიდა შეცდომა: Office-ის ან HTML-ის ფაილი დამხმარის გარეშე გადაიგზავნა.",
    "outside": "პროგრამის შიდა შეცდომა: ფაილი დაუშვებელ ადგილას იწერებოდა და შეჩერდა.",
    "untrusted": "ამ ფაილში გარე ბმულებს საიმედოდ ვერ ვჭრი, ამიტომ არ გავხსენი. გახსენით Office-ში, "
                 "შეამოწმეთ და შეინახეთ ახალ ფორმატში (.docx, .xlsx, .pptx).",
    "has_text": "PDF-ს ტექსტი უკვე აქვს; გასუფთავება არ სჭირდება.",
    "too_big": "სურათი ზედმეტად დიდია.",
    "cancelled": "ოპერაცია გაუქმდა. ფაილები უცვლელია.",
    "closed": "ოპერაცია შეწყდა, რადგან დოკუმენტი დაიხურა.",
    "stale": "დოკუმენტი ამასობაში შეიცვალა. სცადეთ ხელახლა.",
    "erase_shared": "ეს ნახაზის ნაწილი ფაილში რამდენიმე ადგილას იხატება; მისი წაშლა ყველგან წაშლიდა, ამიტომ არ წავშალე.",
    "replace_collateral": "ამ ტექსტის შეცვლა სხვა ტექსტს დააზიანებდა, ამიტომ არ შევცვალე.",
    "slanted": "დახრილად დაწერილი ტექსტის შეცვლა არ შემიძლია.",
    "paragraph_overflow": "ახალი ტექსტი აბზაცის ადგილს არ ეტევა, ამიტომ არაფერი შევცვალე. "
                          "შეამოკლეთ ტექსტი ან შეამცირეთ ასოების ზომა.",
    "no_room": "ტექსტი ამ ჩარჩოში არ ეტევა. დახატეთ უფრო დიდი ჩარჩო.",
    "bad_image": "ეს სურათი ვერ წავიკითხე.",
    "image_complex": "ეს სურათი ისეა ჩასმული (ფორმის შიგნით ან დახრილად), რომ მისი უსაფრთხოდ "
                     "შეცვლა ვერ ხერხდება.",
    "image_shared": "ეს სურათი ფაილში რამდენიმე ადგილას ჩანს; ჩანაცვლება ყველა მათგანს შეცვლიდა. "
                    "გადატანა და წაშლა ცალ-ცალკე შეიძლება.",
    "no_font": "ამ სიმბოლოებისთვის ფონტი ვერ ვიპოვე.",
    "read_only": "ეს ველი მხოლოდ წასაკითხია.",
    "name_taken": "ასეთი სახელის ველი უკვე არსებობს.",
    "cannot_move": "ამ ტიპის კომენტარის გადატანა არ შემიძლია.",
    "redact_failed": "ტექსტის სრულად წაშლა ვერ დადასტურდა, ამიტომ არაფერი შევცვალე.",
    "owner_needed": "საჭიროა მფლობელის (შეზღუდვების) პაროლი.",
    "no_language": "OCR-ის ენის ფაილი ვერ ვიპოვე (data/tessdata).",
    "bad_certificate": "სერტიფიკატი ვერ გაიხსნა. შეამოწმეთ ფაილი და პაროლი.",
    "no_office": "ამ ფაილისთვის საჭირო Office პროგრამა ვერ გავუშვი.",
    "no_edge": "HTML-ის კონვერტაციას Microsoft Edge სჭირდება.",
    "convert_failed": "ფაილი ვერ გარდაიქმნა. შესაძლოა დაზიანებული ან პაროლით დაცულია.",
    "bad_type": "ამ ტიპის ფაილს ვერ გარდავქმნი.",
    "needs_password": "ფაილი პაროლით არის დაცული.",
    "protection_lost": "შენახვისას ფაილის პაროლი დაიკარგებოდა, ამიტომ შენახვა შევაჩერე.",
}


def error_text(code: str) -> str:
    if code.startswith("sign_failed"):
        return "ხელმოწერა ვერ მოხერხდა."
    return ERRORS.get(code, f"ოპერაცია ვერ შესრულდა ({code}).")


def inspect_file(client, path, parent):
    """(page count, password, protected) of a PDF, asking for its password. None = gave up."""
    password, wrong = "", False
    while True:
        try:
            info = run_task(client, "inspect", (path, password), "ფაილი მოწმდება…", parent)
            return info["pages"], password, info["protected"]
        except TaskError as e:
            if str(e) not in ("needs_password", "wrong_password"):
                QMessageBox.warning(parent, os.path.basename(path), error_text(str(e)))
                return None
            wrong = str(e) == "wrong_password"
        text = "პაროლი არასწორია. სცადეთ ხელახლა:" if wrong else "ფაილი დაცულია. შეიყვანეთ პაროლი:"
        password, ok = QInputDialog.getText(parent, os.path.basename(path), text,
                                            QLineEdit.EchoMode.Password)
        if not ok or not password:
            return None


def warn_unprotected(parent) -> bool:
    return QMessageBox.question(
        parent, "პაროლი",
        "საწყისი ფაილი დაცულია, ახალი ფაილი კი პაროლისა და შეზღუდვების გარეშე შეიქმნება.\n\n"
        "გავაგრძელო?") == QMessageBox.StandardButton.Yes


class MergeDialog(QDialog):
    def __init__(self, parent=None, paths=()):
        super().__init__(parent)
        self.setWindowTitle("ფაილების გაერთიანება")
        self.resize(680, 440)
        self.client = EngineClient(self)
        self.result_path = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("ფაილები ამ თანმიმდევრობით გაერთიანდება. საწყისი ფაილები არ იცვლება."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ფაილი", "სულ", "გვერდები (ცარიელი = ყველა)"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        for text, slot in (("დამატება…", self.add), ("ამოღება", self.remove),
                           ("ზემოთ", lambda: self.shift(-1)), ("ქვემოთ", lambda: self.shift(1))):
            b = QPushButton(text)
            if text in ("ამოღება", "ზემოთ", "ქვემოთ"):
                theme.variant(b, "ghost")
            if text in ("ზემოთ", "ქვემოთ"):
                b.setText("")
                b.setIcon(theme.icon("arrow-up" if text == "ზემოთ" else "arrow-down"))
                b.setToolTip(text)
                b.setAccessibleName(text)
            b.setAutoDefault(False)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.marks = QCheckBox("ყოველ ფაილს დაემატოს სანიშნე მისი სახელით")
        self.marks.setChecked(True)
        lay.addWidget(self.marks)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        theme.variant(self.ok, "primary")
        self.ok.setText("გაერთიანება…")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("გაუქმება")
        bb.accepted.connect(self.merge)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._sync()
        for p in paths:
            self.add_path(p)

    def _sync(self):
        self.ok.setEnabled(self.table.rowCount() > 0)

    def add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "ფაილების დამატება", "", PDF_FILTER)
        for p in paths:
            self.add_path(p)

    def add_path(self, path):
        got = inspect_file(self.client, path, self)
        if got is None:
            return
        pages, password, protected = got
        r = self.table.rowCount()
        self.table.insertRow(r)
        name = QTableWidgetItem(os.path.basename(path))
        name.setToolTip(path)
        name.setData(Qt.ItemDataRole.UserRole, (path, password, pages, protected))
        count = QTableWidgetItem(str(pages))
        for it in (name, count):
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, 0, name)
        self.table.setItem(r, 1, count)
        self.table.setItem(r, 2, QTableWidgetItem(""))
        self._sync()

    def remove(self):
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())
        self._sync()

    def shift(self, d):
        r = self.table.currentRow()
        to = r + d
        if r < 0 or not 0 <= to < self.table.rowCount():
            return
        for c in range(3):
            a, b = self.table.takeItem(r, c), self.table.takeItem(to, c)
            self.table.setItem(to, c, a)
            self.table.setItem(r, c, b)
        self.table.selectRow(to)

    def items(self):
        out = []
        for r in range(self.table.rowCount()):
            path, password, n, _ = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            text = self.table.item(r, 2).text().strip()
            try:
                pages = files.parse_group(text, n) if text else None
            except ValueError as e:
                raise ValueError(f"{os.path.basename(path)}: {e}")
            out.append((path, password, pages, os.path.splitext(os.path.basename(path))[0]))
        return out

    def merge(self):
        try:
            items = self.items()
        except ValueError as e:
            QMessageBox.warning(self, "გაერთიანება", str(e))
            return
        if any(self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)[3]
               for r in range(self.table.rowCount())) and not warn_unprotected(self):
            return
        start = os.path.join(os.path.dirname(items[0][0]), "გაერთიანებული.pdf")
        out, _ = QFileDialog.getSaveFileName(self, "სად შევინახო", files.unique_path(start),
                                             PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out = files.unique_path(out + ".pdf")
        if any(os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(i[0]))
               for i in items):
            QMessageBox.warning(self, "გაერთიანება", "აირჩიეთ სხვა სახელი: ეს ერთ-ერთი საწყისი ფაილია.")
            return
        tmp = files.outbox(out)
        try:
            run_task(self.client, "merge", (items, self.marks.isChecked(), tmp),
                     "ფაილები ერთიანდება…", self)
            files.put_in_place(tmp, out)
        except (TaskError, OSError) as e:
            QMessageBox.warning(self, "გაერთიანება", error_text(
                str(e) if isinstance(e, TaskError) else "no_access"))
            return
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.result_path = out
        self.accept()

    def done(self, code):
        self.client.shutdown()
        super().done(code)


class SplitDialog(QDialog):
    """Chooses how to split; parts() gives [(pages, file name), ...]."""

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ფაილის დაყოფა")
        self.setMinimumWidth(460)
        self.n = len(doc.sizes)
        self.stem = os.path.splitext(os.path.basename(doc.path))[0]
        self.toc = [(t[1], t[2]) for t in doc.info["toc"] if t[0] == 1 and t[2] >= 0]
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"სულ {self.n} გვერდია. შეიქმნება ახალი ფაილები, ეს ფაილი არ იცვლება."))
        self.group = QButtonGroup(self)
        self.every = QSpinBox()
        self.every.setRange(1, max(self.n, 1))
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText("მაგ.: 1-3, 4-10, 11-")
        for i, (text, w) in enumerate((("ყოველ ამდენ გვერდში:", self.every),
                                       ("ამ ნაწილებად:", self.ranges),
                                       (f"მთავარი სანიშნეების მიხედვით ({len(self.toc)})", None))):
            row = QHBoxLayout()
            b = QRadioButton(text)
            self.group.addButton(b, i)
            row.addWidget(b)
            if w is not None:
                row.addWidget(w)
                sig = w.valueChanged if isinstance(w, QSpinBox) else w.textEdited
                sig.connect(lambda *_, b=b: (b.setChecked(True), self._sync()))
            lay.addLayout(row)
        self.group.button(2).setEnabled(len(self.toc) > 1)
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(lambda *_: self._sync())
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        theme.variant(self.ok, "primary")
        self.ok.setText("საქაღალდის არჩევა…")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("გაუქმება")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._sync()

    def parts(self):
        mode = self.group.checkedId()
        if mode == 0:
            k = self.every.value()
            groups = [list(range(i, min(i + k, self.n))) for i in range(0, self.n, k)]
        elif mode == 1:
            groups = files.parse_parts(self.ranges.text(), self.n)
        else:
            starts, titles = [], []
            for title, page in sorted(self.toc, key=lambda t: t[1]):
                if page not in starts:
                    starts.append(page)
                    titles.append(title)
            out = []
            if starts[0] > 0:
                out.append((list(range(0, starts[0])), f"{self.stem} - 00.pdf"))
            for i, (a, title) in enumerate(zip(starts, titles)):
                b = starts[i + 1] if i + 1 < len(starts) else self.n
                out.append((list(range(a, b)), f"{i + 1:02d} - {files.safe_name(title)}.pdf"))
            return out
        width = len(str(len(groups)))
        return [(g, f"{self.stem} - {i + 1:0{width}d} (გვ. {files.describe(g)}).pdf")
                for i, g in enumerate(groups)]

    def _sync(self):
        try:
            parts = self.parts()
            self.summary.setText(f"შეიქმნება {len(parts)} ფაილი. პირველი: {parts[0][1]}")
            self.ok.setEnabled(True)
        except ValueError as e:
            self.summary.setText(str(e))
            self.ok.setEnabled(False)


class CropDialog(QDialog):
    def __init__(self, size, margins, selected, total, parent=None):
        super().__init__(parent)
        self.setWindowTitle("გვერდის ჩამოჭრა")
        w, h = size
        lay = QVBoxLayout(self)
        form = theme.form_layout()
        self.boxes = []
        for text, value, limit in (("მარცხნიდან", margins[0], w), ("ზემოდან", margins[1], h),
                                   ("მარჯვნიდან", margins[2], w), ("ქვემოდან", margins[3], h)):
            b = QDoubleSpinBox()
            b.setRange(0, limit / MM)
            b.setDecimals(1)
            b.setSuffix(" მმ")
            b.setValue(value / MM)
            self.boxes.append(b)
            form.addRow(text + ":", b)
        lay.addLayout(form)
        self.scope = QButtonGroup(self)
        for i, text in enumerate((f"მონიშნული გვერდები ({files.describe(selected)})",
                                  f"ყველა გვერდი ({total})")):
            b = QRadioButton(text)
            b.setChecked(i == 0)
            self.scope.addButton(b, i)
            lay.addWidget(b)
        note = QLabel("ყურადღება: ჩამოჭრილი ნაწილი იმალება, მაგრამ ფაილიდან არ იშლება. "
                      "საიდუმლო ინფორმაციის წასაშლელად ჩამოჭრა არ გამოდგება.")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("ჩამოჭრა")
        theme.variant(bb.button(QDialogButtonBox.StandardButton.Ok), "primary")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("გაუქმება")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def margins(self):
        return tuple(b.value() * MM for b in self.boxes)

    def all_pages(self):
        return self.scope.checkedId() == 1


class InsertDialog(QDialog):
    """Which pages of another file, and where."""

    def __init__(self, name, pages, current, total, parent=None):
        super().__init__(parent)
        self.setWindowTitle("გვერდების ჩასმა ფაილიდან")
        self.n = pages
        form = theme.form_layout(self)
        form.addRow(QLabel(f"{name} — {pages} გვერდი"))
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText("ცარიელი = ყველა. მაგ.: 1-3, 7")
        form.addRow("რომელი გვერდები:", self.ranges)
        self.after = QSpinBox()
        self.after.setRange(0, total)
        self.after.setValue(current)
        self.after.setSpecialValueText("თავში")
        form.addRow("რომელი გვერდის შემდეგ:", self.after)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("ჩასმა")
        theme.variant(bb.button(QDialogButtonBox.StandardButton.Ok), "primary")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("გაუქმება")
        bb.accepted.connect(self._check)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def pages(self):
        text = self.ranges.text().strip()
        return files.parse_group(text, self.n) if text else None

    def _check(self):
        try:
            self.pages()
        except ValueError as e:
            QMessageBox.warning(self, self.windowTitle(), str(e))
            return
        self.accept()
