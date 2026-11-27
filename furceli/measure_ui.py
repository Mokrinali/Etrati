"""The scale dialog and the list of measurements (design canvas "ETRATI: მასშტაბით გაზომვა")."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QRadioButton, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import measure, theme

UNIT_NAMES = [("m", "მეტრი (მ, მ²)"), ("cm", "სანტიმეტრი (სმ, სმ²)"), ("mm", "მილიმეტრი (მმ, მმ²)")]
KIND_NAMES = {"distance": "სიგრძე", "path": "ტეხილი", "area": "ფართობი"}
DRAW = 2  # ScaleDialog.exec() result: the person wants to draw the known length first


def _title(text):
    t = QLabel(text)
    t.setObjectName("fieldTitle")
    return t


class ScaleDialog(QDialog):
    """A scale as a ratio (1:100) or from a known length drawn on the drawing."""

    def __init__(self, page, total, current=None, drawn=None, parent=None):
        """current: the page's scale or None; drawn: the length drawn for calibrating, in
        points of the page (opens on that tab)."""
        super().__init__(parent)
        self.setWindowTitle("მასშტაბის დაყენება")
        self._page, self._total, self._drawn = page, total, drawn
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        self.tabs = QTabWidget()
        ratio = QWidget()
        rl = QVBoxLayout(ratio)
        rl.addWidget(_title("მასშტაბი"))
        row = QHBoxLayout()
        row.addWidget(QLabel("1 :"))
        self.ratio = QSpinBox()
        self.ratio.setRange(1, 1_000_000)
        self.ratio.setValue(100)
        if current and not drawn and current.get("label", "").startswith("1:"):
            try:
                self.ratio.setValue(int(current["label"][2:]))
            except ValueError:
                pass
        row.addWidget(self.ratio)
        row.addStretch(1)
        rl.addLayout(row)
        hint = QLabel("CAD-იდან (AutoCAD, Revit, ArchiCAD) მასშტაბით დაბეჭდილი ნახაზისთვის.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        rl.addWidget(hint)
        rl.addStretch(1)
        known = QWidget()
        kl = QVBoxLayout(known)
        kl.addWidget(_title("დახაზული ხაზის ნამდვილი სიგრძე"))
        row = QHBoxLayout()
        self.real = QDoubleSpinBox()
        self.real.setDecimals(3)
        self.real.setRange(0.001, 1_000_000)
        self.real.setValue(1.0)
        self.real_unit = QComboBox()
        for key, name in UNIT_NAMES:
            self.real_unit.addItem(measure.UNITS[key][1], key)
        self.draw = QPushButton("ხაზის დახატვა" if drawn is None else "ხაზის თავიდან დახატვა")
        self.draw.clicked.connect(lambda: self.done(DRAW))
        row.addWidget(self.real)
        row.addWidget(self.real_unit)
        row.addWidget(self.draw)
        row.addStretch(1)
        kl.addLayout(row)
        self.drawn_label = QLabel()
        self.drawn_label.setObjectName("muted")
        self.drawn_label.setWordWrap(True)
        kl.addWidget(self.drawn_label)
        tip = QLabel("ავლეთ ხაზი ელემენტზე, რომლის ზომაც იცით (მაგ. კედელი, რომელზეც „6.00“ წერია). "
                     "რაც გრძელია ხაზი, მით ზუსტია მასშტაბი.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        kl.addWidget(tip)
        kl.addStretch(1)
        self.tabs.addTab(ratio, "მასშტაბით (1:100)")
        self.tabs.addTab(known, "ცნობილი ზომით")
        lay.addWidget(self.tabs)
        grid = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(_title("ერთეული"))
        self.unit = QComboBox()
        for key, name in UNIT_NAMES:
            self.unit.addItem(name, key)
        left.addWidget(self.unit)
        right.addWidget(_title("სიზუსტე"))
        self.digits = QComboBox()
        for d in range(4):
            self.digits.addItem("0" if d == 0 else "0." + "0" * d, d)
        self.digits.setCurrentIndex(2)
        right.addWidget(self.digits)
        grid.addLayout(left, 1)
        grid.addLayout(right, 1)
        lay.addLayout(grid)
        if current:
            self.unit.setCurrentIndex([k for k, _ in UNIT_NAMES].index(current["unit"]))
            self.digits.setCurrentIndex(int(current["digits"]))
        lay.addWidget(_title("რომელ გვერდზე"))
        pages = QHBoxLayout()
        self.this_page = QRadioButton(f"ამ გვერდზე ({page + 1})")
        self.all_pages = QRadioButton("ყველა გვერდზე")
        group = QButtonGroup(self)
        for b in (self.this_page, self.all_pages):
            group.addButton(b)
            pages.addWidget(b)
        pages.addStretch(1)
        self.this_page.setChecked(True)
        lay.addLayout(pages)
        note = QLabel("მასშტაბი ფაილში შეინახება: ხელახლა გახსნისას, Acrobat-სა და Bluebeam-ში იგივე "
                      "იქნება. ამ გვერდების გაზომვები ახალ მასშტაბზე გადაითვლება.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        from .forms import _buttons
        self._box = _buttons(self, "დაყენება", self._check)
        lay.addWidget(self._box)
        self.real.valueChanged.connect(self._show)
        self.real_unit.currentIndexChanged.connect(self._show)
        if drawn is not None:
            self.tabs.setCurrentIndex(1)
            self.real.setFocus()
            self.real.selectAll()
        self._show()
        self.setMinimumWidth(480)

    def _show(self, *_):
        if self._drawn is None:
            self.drawn_label.setText("ჯერ დახაზეთ ხაზი ნახაზზე: „ხაზის დახატვა“.")
            return
        paper_mm = self._drawn * measure.PAPER * 1000
        f = self._known_factor()
        self.drawn_label.setText(f"დახაზული: {paper_mm:.1f} მმ ქაღალდზე. გამოვიდა: 1 მმ ქაღალდზე = "
                                 f"{f / measure.PAPER:.1f} მმ ნამდვილში (≈ 1:{round(f / measure.PAPER)})")

    def _known_factor(self):
        metres = self.real.value() / measure.UNITS[self.real_unit.currentData()][0]
        return metres / self._drawn

    def result_scale(self):
        """(factor, unit, digits, label, pages)."""
        if self.tabs.currentIndex() == 0:
            n = self.ratio.value()
            factor, label = measure.PAPER * n, f"1:{n}"
        else:
            factor = self._known_factor()
            label = f"≈ 1:{round(factor / measure.PAPER)}"
        pages = list(range(self._total)) if self.all_pages.isChecked() else [self._page]
        return factor, self.unit.currentData(), self.digits.currentData(), label, pages

    def _check(self):
        if self.tabs.currentIndex() == 1 and (self._drawn is None or self._drawn < 1):
            QMessageBox.warning(self, self.windowTitle(), "ჯერ დახაზეთ ხაზი ნახაზზე: „ხაზის დახატვა“.")
            return
        self.accept()


class MeasureList(QDialog):
    """Every measurement of the file, its totals, and a copy for Excel."""

    def __init__(self, items, go=None, parent=None):
        """items: the engine's measures(); go(page): shows a page."""
        super().__init__(parent)
        self.setWindowTitle("გაზომვები")
        self._items, self._go = items, go
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        hint = QLabel("ორჯერ დაწკაპუნება — გვერდზე გადასვლა. შენიშვნა ემატება მარჯვენა ღილაკით "
                      "გაზომვაზე → „შენიშვნა…“.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.table = QTableWidget(len(items), 4)
        self.table.setHorizontalHeaderLabels(["გვერდი", "ტიპი", "შენიშვნა", "მნიშვნელობა"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for r, m in enumerate(items):
            value = m["text"].split(": ", 1)[1] if m["note"] else m["text"]
            for c, text in enumerate((str(m["page"] + 1), KIND_NAMES[m["kind"]], m["note"], value)):
                cell = QTableWidgetItem(text)
                if c == 3:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, cell)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open)
        lay.addWidget(self.table, 1)
        self.totals = QLabel(self.total_text())
        self.totals.setWordWrap(True)
        lay.addWidget(self.totals)
        bb = QDialogButtonBox()
        self.excel = bb.addButton("Excel-ში შენახვა…", QDialogButtonBox.ButtonRole.ActionRole)
        self.excel.clicked.connect(self.save_excel)
        self.excel.setEnabled(bool(items))
        close = bb.addButton("დახურვა", QDialogButtonBox.ButtonRole.AcceptRole)
        theme.variant(close, "primary")
        close.clicked.connect(self.accept)
        lay.addWidget(bb)
        self.resize(620, 420)

    def totals_by_unit(self):
        """{(kind group, unit): total}: areas apart from lengths, each unit apart."""
        out = {}
        for m in self._items:
            group = "area" if m["kind"] == "area" else "length"
            key = (group, m["unit"], m["paper"])
            out[key] = out.get(key, 0.0) + m["value"]
        return out

    def total_text(self):
        if not self._items:
            return "გაზომვები ჯერ არ არის."
        parts = []
        digits = 2
        for (group, unit, paper), value in sorted(self.totals_by_unit().items()):
            _, u, sq = measure.UNITS[unit]
            name = "ფართობი" if group == "area" else "სიგრძე და ტეხილი"
            parts.append(f"{name}: {value:.{digits}f} {sq if group == 'area' else u}"
                         + (" (ქაღალდზე)" if paper else ""))
        return "ჯამი — " + " · ".join(parts)

    def _open(self, row, _col):
        if self._go:
            self._go(self._items[row]["page"])

    def save_excel(self, path=None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "გაზომვები", "გაზომვები.xlsx", "Excel (*.xlsx)")
        if not path:
            return False
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "გაზომვები"
        ws.append(["გვერდი", "ტიპი", "შენიშვნა", "მნიშვნელობა", "ერთეული", "პერიმეტრი"])
        for m in self._items:
            _, u, sq = measure.UNITS[m["unit"]]
            ws.append([m["page"] + 1, KIND_NAMES[m["kind"]], m["note"], round(m["value"], 4),
                       (sq if m["kind"] == "area" else u) + (" (ქაღალდზე)" if m["paper"] else ""),
                       round(m["perimeter"], 4) if m["perimeter"] is not None else None])
        try:
            wb.save(path)
        except OSError as e:
            QMessageBox.warning(self, self.windowTitle(), f"ფაილი ვერ შეინახა: {e}")
            return False
        return True
