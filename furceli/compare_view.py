"""ჩართალა: two drawings side by side, what changed between them, moving together.

The old file is on the left, the new one on the right; each has its own engine and is only
read. The pictures are compared in the new one's engine (Features.visual_diff), the words too
(Features.compare); merge_changes() makes one list of both.
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QProgressDialog, QPushButton, QSlider, QSplitter, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from . import files, theme
from .banner import Banner
from .client import P_OPEN, EngineClient, TaskError, run_task
from .dialogs import PDF_FILTER, error_text
from .linked import Linker
from .thumbs import Thumbs
from .view import PageView

CONTENT = ("tile", "chars", "links", "widgets")
TEXT_KINDS = {"replace": "შეიცვალა", "delete": "წაიშალა", "insert": "დაემატა"}
PIXEL_KINDS = {"changed": "შეიცვალა", "removed": "წაიშალა", "added": "დაემატა"}
# Sensitivity 1 (scans: dust and a wobbly scanner are no change) .. 5 (every small line).
# tolerance: px a line may move and stay the same; min_px: smaller is noise; join: px between
# pieces of one change (Features.visual_diff, 100 dpi)
SENSITIVITY = {1: {"tolerance": 4, "min_px": 80, "join": 10},
               2: {"tolerance": 3, "min_px": 40, "join": 9},
               3: {"tolerance": 2, "min_px": 12, "join": 8},
               4: {"tolerance": 1, "min_px": 6, "join": 6},
               5: {"tolerance": 1, "min_px": 3, "join": 5}}


# ---------------------------------------------------------------------------
# one list of changes
# ---------------------------------------------------------------------------
def _cut(t):
    return t if len(t) < 120 else t[:117] + "…"


def _union(rects):
    rects = [r for r in rects if r]
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def _covers(words, frame, grow=2.0):
    """Do the words' rectangle (points) cover most of a pixel frame?"""
    if not words or not frame:
        return False
    x0, y0, x1, y1 = words[0] - grow, words[1] - grow, words[2] + grow, words[3] + grow
    w = min(x1, frame[2]) - max(x0, frame[0])
    h = min(y1, frame[3]) - max(y0, frame[1])
    area = max((frame[2] - frame[0]) * (frame[3] - frame[1]), 1e-6)
    return w > 0 and h > 0 and w * h >= 0.6 * area


def merge_changes(text, regions, pairs):
    """One entry per change. A word change and the pixels it moved are the same change.

    text: Features.compare()["changes"]; regions: {(old page, new page): visual_diff regions};
    pairs: [(old page, new page)]. Entry: {"pair", "kind", "text", "old": [rects],
    "new": [rects]}; a page of a pair may be None.
    """
    to_new = {po: pn for po, pn in pairs}
    to_old = {pn: po for po, pn in pairs}
    entries = []
    for c in text:
        olds = [p for p, _ in c["old_at"]]
        news = [p for p, _ in c["new_at"]]
        pn = news[0] if news else to_new.get(olds[0]) if olds else c["page"]
        po = olds[0] if olds else to_old.get(pn)
        if (po, pn) not in to_new.items():
            continue  # a page left out of the comparison ("—"), or words moved between pages
        old_t, new_t = _cut(c["old"]), _cut(c["new"])
        words_old = _union([r for p, r in c["old_at"] if p == po])
        words_new = _union([r for p, r in c["new_at"] if p == pn])
        entries.append({
            "pair": (po, pn), "kind": c["kind"],
            "text": {"replace": f"„{old_t}“ → „{new_t}“", "delete": f"„{old_t}“",
                     "insert": f"„{new_t}“"}[c["kind"]],
            "old": [words_old] if words_old else [], "new": [words_new] if words_new else [],
            "_words": (words_old, words_new), "_pixels": False})
    for pair in pairs:
        for g in regions.get(pair, ()):
            home = next((e for e in entries if "_words" in e and e["pair"] == pair and (
                _covers(e["_words"][1], g["new"]) or _covers(e["_words"][0], g["old"]))), None)
            if home is None:
                kind = "changed" if g["old"] and g["new"] else "removed" if g["old"] else "added"
                entries.append({"pair": pair, "kind": kind, "text": "",
                                "old": [g["old"]] if g["old"] else [],
                                "new": [g["new"]] if g["new"] else [], "_pixels": True})
                continue
            if not home["_pixels"]:  # the frames of what really changed replace the words
                home["old"], home["new"], home["_pixels"] = [], [], True
            if g["old"]:
                home["old"].append(g["old"])
            if g["new"]:
                home["new"].append(g["new"])

    def where(e):
        po, pn = e["pair"]
        r = (e["new"] or e["old"] or [(0, 0, 0, 0)])[0]
        return (pn if pn is not None else po if po is not None else 0, r[1], r[0])
    entries.sort(key=where)
    for e in entries:
        e.pop("_words", None)
        e.pop("_pixels", None)
    return entries


def describe(e):
    po, pn = e["pair"]
    pages = (f"გვ. {pn + 1}" if po == pn or po is None else
             f"გვ. {po + 1}" if pn is None else f"გვ. {po + 1} → {pn + 1}")
    kind = TEXT_KINDS.get(e["kind"]) or PIXEL_KINDS[e["kind"]]
    return f"{pages} · {kind}" + (f": {e['text']}" if e["text"] else "")


# ---------------------------------------------------------------------------
# choosing the files
# ---------------------------------------------------------------------------
class CompareFilesDialog(QDialog):
    """Two files: the one sent (old) and the one that came back (new)."""

    def __init__(self, new="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("ორი ფაილის შედარება")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        form = theme.form_layout()
        self.old_edit, self.new_edit = QLineEdit(), QLineEdit(new)
        for edit, label in ((self.old_edit, "ძველი ფაილი (რაც გაგზავნეთ)"),
                            (self.new_edit, "ახალი ფაილი (რაც დაგიბრუნეს)")):
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            pick = QPushButton("არჩევა…")
            pick.clicked.connect(lambda *_, e=edit: self._pick(e))
            row.addWidget(pick)
            edit.textChanged.connect(self._check)
            form.addRow(label, row)
        lay.addLayout(form)
        hint = QLabel("შედარება არც ერთ ფაილს არ ცვლის.")
        hint.setProperty("role", "muted")
        lay.addWidget(hint)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = theme.variant(QPushButton("გაუქმება"), "ghost")
        cancel.clicked.connect(self.reject)
        self.go = theme.variant(QPushButton("შედარება"), "primary")
        self.go.clicked.connect(self.accept)
        self.go.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(self.go)
        lay.addLayout(buttons)
        self._check()

    def _pick(self, edit):
        start = edit.text() or self.new_edit.text() or self.old_edit.text()
        path, _ = QFileDialog.getOpenFileName(self, "ფაილის არჩევა", os.path.dirname(start),
                                              PDF_FILTER)
        if path:
            edit.setText(path)

    def paths(self):
        return self.old_edit.text().strip(), self.new_edit.text().strip()

    def _check(self):
        old, new = self.paths()
        ok = (os.path.isfile(old) and os.path.isfile(new)
              and os.path.normcase(os.path.abspath(old)) != os.path.normcase(os.path.abspath(new)))
        self.go.setEnabled(ok)
        self.go.setToolTip("" if ok else "აირჩიეთ ორი განსხვავებული ფაილი")

    @staticmethod
    def ask(parent):
        """(old, new), or None."""
        doc = getattr(parent, "doc", None)
        dlg = CompareFilesDialog(doc.path if doc is not None else "", parent)
        return dlg.paths() if dlg.exec() else None


# ---------------------------------------------------------------------------
# one side
# ---------------------------------------------------------------------------
class Pane(QWidget):
    """One drawing, only read: its own engine and page view."""

    ready = Signal()

    def __init__(self, path, title, parent=None):
        super().__init__(parent)
        self.path, self.password = path, ""
        self.info, self.loaded, self.error = {}, False, ""
        self.client = EngineClient(self)
        self.client.done.connect(self._done)
        self.client.failed.connect(self._failed)
        self.thumbs = Thumbs()
        self.view = PageView(self.client, self.thumbs)
        head = QLabel(title)
        head.setTextFormat(Qt.TextFormat.PlainText)
        head.setProperty("role", "muted")
        head.setToolTip(path)
        head.setContentsMargins(8, 4, 8, 4)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(head)
        lay.addWidget(self.view, 1)
        self._open()

    def _open(self):
        self.client.submit(("open",), P_OPEN, "open", self.path, self.password)

    def _done(self, key, value):
        if key[0] == "open":
            self.info, self.loaded = value, True
            self.view.set_document(value["sizes"], value["can_copy"])
            self.ready.emit()
        elif key[0] in CONTENT:
            self.view.on_done(key, value)

    def _failed(self, key, code):
        if key[0] == "open":
            if code in ("needs_password", "wrong_password"):
                text = ("პაროლი არასწორია. სცადეთ ხელახლა:" if code == "wrong_password"
                        else "ეს ფაილი პაროლით არის დაცული. შეიყვანეთ პაროლი:")
                pw, ok = QInputDialog.getText(self, os.path.basename(self.path), text,
                                              QLineEdit.EchoMode.Password)
                if ok and pw:
                    self.password = pw
                    self._open()
                    return
                code = "cancelled"
            self.error = code
            self.ready.emit()
        elif key[0] in CONTENT:
            self.view.on_failed(key)

    def close_pane(self):
        self.client.shutdown(wait=False)


# ---------------------------------------------------------------------------
# the comparison tab
# ---------------------------------------------------------------------------
class CompareView(QWidget):
    """A tab of the main window. mode is "side" now; an overlay may come later."""

    def __init__(self, old, new, parent=None):
        super().__init__(parent)
        self.old_path, self.new_path = os.path.abspath(old), os.path.abspath(new)
        self.title = f"{os.path.basename(old)} ↔ {os.path.basename(new)}"
        self.mode = "side"             # an overlay may come later (CHARTALA.md)
        self.opts: dict = dict(SENSITIVITY[3])  # for Features.visual_diff
        self.pairs: list = []          # (old page, new page)
        self.manual: dict = {}         # pair -> two pairs of points, aligned by hand
        self.results: dict = {}        # pair -> visual_diff result
        self.text: list = []           # Features.compare changes, kept for a report later
        self.entries: list = []
        self.finished = False
        self.linked = True
        self.aligning = False
        self._clicks: list = []        # (side, page, point) while aligning by hand
        self._running = False          # run() is on: another one waits for its turn
        self._closed = False           # the tab went while a comparison was running
        self._again = QTimer(self)     # a setting changed: compare again, once it rests
        self._again.setSingleShot(True)
        self._again.setInterval(400)
        self._again.timeout.connect(self.run)
        self.banner = Banner()
        self.old = Pane(self.old_path, f"ძველი: {os.path.basename(old)}")
        self.new = Pane(self.new_path, f"ახალი: {os.path.basename(new)}")

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        for icon, tip, slot in (("zoom-out", "დაპატარავება", lambda: self._zoom(1 / 1.2)),
                                ("zoom-in", "გადიდება", lambda: self._zoom(1.2)),
                                ("fit-width", "სიგანეზე მორგება", lambda: self._fit("width")),
                                ("fit-page", "მთელი გვერდი", lambda: self._fit("page"))):
            b = QToolButton()
            b.setIcon(theme.icon(icon))
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.setAutoRaise(True)
            b.clicked.connect(lambda *_, s=slot: s())
            bar.addWidget(b)
        bar.addSpacing(12)
        self.align_btn = QToolButton()
        self.align_btn.setText("ხელით გასწორება…")
        self.align_btn.setToolTip("ორივე ნახაზზე ორი ერთნაირი წერტილის მითითება")
        self.align_btn.setAutoRaise(True)
        self.align_btn.clicked.connect(self.start_align)
        bar.addWidget(self.align_btn)
        self.pairs_btn = QToolButton()
        self.pairs_btn.setText("გვერდების წყვილები")
        self.pairs_btn.setCheckable(True)
        self.pairs_btn.setAutoRaise(True)
        self.pairs_btn.toggled.connect(lambda on: self.pairs_box.setVisible(on))
        bar.addWidget(self.pairs_btn)
        self.auto_btn = QToolButton()
        self.auto_btn.setText("ავტომატური გასწორება")
        self.auto_btn.setToolTip("ხელით მითითებული წერტილების მოხსნა")
        self.auto_btn.setAutoRaise(True)
        self.auto_btn.clicked.connect(self.forget_manual)
        self.auto_btn.hide()
        bar.addWidget(self.auto_btn)
        bar.addStretch()

        side = QWidget()
        col = QVBoxLayout(side)
        col.setContentsMargins(8, 8, 8, 8)
        head = QLabel("ცვლილებები")
        head.setProperty("role", "muted")
        col.addWidget(head)
        self.note = QLabel("ნახაზები იხსნება…")
        self.note.setWordWrap(True)
        self.note.setProperty("role", "muted")
        col.addWidget(self.note)
        sens = QLabel("მგრძნობელობა")
        sens.setProperty("role", "muted")
        col.addWidget(sens)
        self.sensitivity = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity.setRange(1, 5)
        self.sensitivity.setValue(3)
        self.sensitivity.setPageStep(1)
        self.sensitivity.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.sensitivity.setToolTip("დაბალი: სკანის ხმაური არ ჩანს. "
                                    "მაღალი: ჩანს ყველა წვრილი ხაზი.")
        self.sensitivity.valueChanged.connect(self._sensitivity)
        col.addWidget(self.sensitivity)
        ends = QHBoxLayout()
        for text in ("დაბალი (სკანი)", "მაღალი"):
            lab = QLabel(text)
            lab.setProperty("role", "muted")
            ends.addWidget(lab)
            if text != "მაღალი":
                ends.addStretch()
        col.addLayout(ends)
        self.pairs_box = QWidget()
        pv = QVBoxLayout(self.pairs_box)
        pv.setContentsMargins(0, 4, 0, 4)
        head2 = QLabel("გვერდების წყვილები")
        head2.setProperty("role", "muted")
        pv.addWidget(head2)
        self.pairs_table = QTableWidget(0, 2)
        self.pairs_table.setHorizontalHeaderLabels(["ძველი", "ახალი"])
        self.pairs_table.verticalHeader().hide()
        self.pairs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pairs_table.setMaximumHeight(180)
        pv.addWidget(self.pairs_table)
        self.pairs_box.hide()
        col.addWidget(self.pairs_box)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._show)
        self.list.itemClicked.connect(lambda *_: self._show(self.list.currentRow()))
        col.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.prev_btn, self.next_btn = QPushButton("წინა"), QPushButton("შემდეგი")
        self.prev_btn.setIcon(theme.icon("chevron-left"))
        self.next_btn.setIcon(theme.icon("chevron-right"))
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn.clicked.connect(lambda: self.step(1))
        row.addWidget(self.prev_btn)
        row.addWidget(self.next_btn)
        col.addLayout(row)
        side.setMinimumWidth(theme.size("sidebar"))

        self.splitter = QSplitter()
        self.splitter.addWidget(self.old)
        self.splitter.addWidget(self.new)
        self.splitter.addWidget(side)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.banner)
        lay.addLayout(bar)
        lay.addWidget(self.splitter, 1)
        self.link = Linker(self.old.view, self.new.view, self._place, self._scale, self)
        self.old.ready.connect(self._ready)
        self.new.ready.connect(self._ready)
        for side in ("old", "new"):
            getattr(self, side).view.toolDone.connect(
                lambda tool, pno, data, s=side: self._point(s, tool, pno, data))
        self._told = ""                # what the banner says about the pages themselves

    # -- opening and comparing ----------------------------------------------
    def _ready(self):
        if self.old.error or self.new.error:
            if not self.finished:
                code = self.old.error or self.new.error
                if code != "cancelled":
                    self.banner.show_message("danger", error_text(code))
                self.note.setText("შედარება ვერ მოხერხდა. ფაილები უცვლელია.")
                self.finished = True
            return
        if not (self.old.loaded and self.new.loaded) or self.pairs:
            return
        n_old, n_new = len(self.old.info["sizes"]), len(self.new.info["sizes"])
        self.pairs = [(i, i) for i in range(min(n_old, n_new))]
        self._fill_pairs()
        if n_old != n_new:
            self._told = (f"გვერდების რაოდენობა განსხვავდება: ძველში {n_old}, ახალში {n_new}. "
                          "გვერდები ნომრით დაწყვილდა; წყვილის შეცვლა შეიძლება გვერდით.")
            self.pairs_btn.setChecked(True)
        QTimer.singleShot(0, self.run)

    # -- settings that compare again ---------------------------------------------
    def _schedule(self):
        """Compare again once the setting rests; until then the old result is out of date."""
        self.finished = False
        self._again.start()

    def forget_manual(self):
        """Points given by hand are taken back: the pages are aligned by themselves again."""
        self.manual.clear()
        self.run()

    def _sensitivity(self, level):
        self.opts.update(SENSITIVITY[level])
        self._schedule()

    def _fill_pairs(self):
        n_new = len(self.new.info["sizes"])
        chosen = dict(self.pairs)
        t = self.pairs_table
        t.setRowCount(len(self.old.info["sizes"]))
        for po in range(t.rowCount()):
            item = QTableWidgetItem(str(po + 1))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setFont(theme.mono_font())
            t.setItem(po, 0, item)
            combo = QComboBox()
            combo.addItems(["—"] + [str(p + 1) for p in range(n_new)])
            combo.setFont(theme.mono_font())
            combo.setCurrentIndex(chosen[po] + 1 if po in chosen else 0)
            combo.currentIndexChanged.connect(lambda i, po=po: self._pair_chosen(po, i - 1))
            t.setCellWidget(po, 1, combo)

    def pair_combo(self, po):
        return self.pairs_table.cellWidget(po, 1)

    def _pair_chosen(self, po, pn):
        """Old page po is now new page pn (or nothing, -1); one new page has one partner."""
        if pn >= 0:
            for other in range(self.pairs_table.rowCount()):
                c = self.pair_combo(other)
                if other != po and c.currentIndex() == pn + 1:
                    c.blockSignals(True)
                    c.setCurrentIndex(0)
                    c.blockSignals(False)
        self.pairs = [(row, self.pair_combo(row).currentIndex() - 1)
                      for row in range(self.pairs_table.rowCount())
                      if self.pair_combo(row).currentIndex() > 0]
        self._schedule()

    # -- aligning by hand ---------------------------------------------------------
    STEPS = ("ძველ ნახაზზე დააწკაპუნეთ პირველ წერტილზე (მაგალითად, ღერძების გადაკვეთაზე).",
             "ახლა ახალ ნახაზზე დააწკაპუნეთ იმავე წერტილზე.",
             "ძველ ნახაზზე დააწკაპუნეთ მეორე წერტილზე, პირველისგან შორს.",
             "ახალ ნახაზზე დააწკაპუნეთ იმავე მეორე წერტილზე.")

    def start_align(self):
        if not self.pairs:
            return
        self.aligning, self._clicks = True, []
        for v in (self.old.view, self.new.view):
            v.set_tool("point")
        self._tell_step()

    def _tell_step(self):
        self.banner.show_message("info", self.STEPS[len(self._clicks)] + " გაუქმება: Esc")

    def cancel_align(self, why=""):
        self.aligning, self._clicks = False, []
        for v in (self.old.view, self.new.view):
            v.set_tool("select")
        self._paint(self.list.currentRow())
        if why:
            self.banner.show_message("warning", why)
        else:
            self._tell()

    def _point(self, side, tool, pno, data):
        if not self.aligning:
            return
        if tool == "cancel":
            self.cancel_align()
            return
        if tool != "point" or side != ("old", "new")[len(self._clicks) % 2]:
            return  # the other drawing's turn
        self._clicks.append((side, pno, data["point"]))
        marks = {}
        for s, p, (x, y) in self._clicks:
            marks.setdefault((s, p), []).append(((x - 6, y - 6, x + 6, y + 6), "mark-crop", True))
        self.old.view.frames = {p: m for (s, p), m in marks.items() if s == "old"}
        self.new.view.frames = {p: m for (s, p), m in marks.items() if s == "new"}
        self.update_views()
        if len(self._clicks) < 4:
            self._tell_step()
            return
        (_, po, o1), (_, pn, n1), (_, po2, o2), (_, pn2, n2) = self._clicks
        if (po, pn) != (po2, pn2):
            self.cancel_align("ორივე წერტილი ერთსა და იმავე გვერდზე უნდა იყოს. სცადეთ თავიდან.")
            return
        if abs(complex(*o1) - complex(*o2)) < 10 or abs(complex(*n1) - complex(*n2)) < 10:
            self.cancel_align("წერტილები ერთმანეთთან ძალიან ახლოსაა. "
                              "აირჩიეთ შორს მდებარე წერტილები.")
            return
        if (po, pn) not in self.pairs:  # pointed at pages that were not a pair: now they are
            self.pairs = sorted([p for p in self.pairs if p[0] != po and p[1] != pn] + [(po, pn)])
            self._fill_pairs()
        self.manual[(po, pn)] = [[o1, n1], [o2, n2]]
        self.aligning, self._clicks = False, []
        for v in (self.old.view, self.new.view):
            v.set_tool("select")
        self.run()

    def _tell(self):
        """What the banner says when nothing is going on."""
        failed = sorted(pn for (po, pn), r in self.results.items() if not r.get("ok", True)
                        and (po, pn) not in self.manual)
        text = self._told
        if failed:
            text = (text + " " if text else "") + (
                f"ნახაზები ავტომატურად ვერ გასწორდა: გვ. {files.describe(failed)}. "
                "გაასწორეთ ხელით: მიუთითეთ ორივე ნახაზზე ორი ერთნაირი წერტილი.")
        if not text:
            self.banner.clear()
        elif failed:
            self.banner.show_message("warning", text, "ხელით გასწორება…", self.start_align)
        else:
            self.banner.show_message("warning", text)

    def run(self):
        """Compare every pair of pages, then the words. Neither file is changed.

        The window breathes between two pages: a setting changed just then asks for another
        comparison, which waits for this one to end instead of running inside it."""
        if self._running:
            self._again.start()
            return
        self._again.stop()
        if self.aligning:
            self.cancel_align()
        self._running = True
        try:
            self._compare()
        finally:
            self._running = False
        if not self._closed:
            self.finished = not self._again.isActive()

    def _compare(self):
        self.finished = False
        bar = QProgressDialog("ნახაზები დარდება…", "გაუქმება", 0, len(self.pairs) + 1, self)
        bar.setWindowTitle("შედარება")
        bar.setWindowModality(Qt.WindowModality.WindowModal)
        bar.setMinimumDuration(400)
        results, text, code = {}, [], ""
        for k, (po, pn) in enumerate(self.pairs):
            bar.setValue(k)
            bar.setLabelText(f"ნახაზები დარდება: გვერდი {k + 1} / {len(self.pairs)}")
            if bar.wasCanceled():
                code = "cancelled"
                break
            try:
                results[(po, pn)] = run_task(self.new.client, "visual_diff", (
                    self.old_path, self.old.password, po, pn, self._opts((po, pn))), "", None,
                    allow=bar)
            except TaskError as e:
                code = str(e)
                break
            if self._closed:
                break
        if not code:
            bar.setValue(len(self.pairs))
            bar.setLabelText("ტექსტი დარდება…")
            if bar.wasCanceled():
                code = "cancelled"
            else:
                try:
                    text = run_task(self.new.client, "compare", (
                        self.old_path, self.old.password, False), "", None, allow=bar)["changes"]
                except TaskError as e:
                    code = str(e)
        bar.close()
        bar.deleteLater()
        if self._closed or code == "closed":
            return  # the tab is gone: nothing to show, nowhere to show it
        if code:
            self.entries = []
            self._fill()
            self.note.setText("შედარება გაუქმდა. ფაილები უცვლელია." if code == "cancelled"
                              else "შედარება ვერ მოხერხდა. ფაილები უცვლელია.")
            if code != "cancelled":
                self.banner.show_message("danger", error_text(code))
            return
        self.results, self.text = results, text
        self.entries = merge_changes(text, {p: r["regions"] for p, r in results.items()},
                                     self.pairs)
        self._fill()
        more = sum(r.get("more", 0) for r in results.values())
        self.note.setText((f"ნაპოვნია {len(self.entries)} ცვლილება" if self.entries
                           else "განსხვავება ვერ ვიპოვე")
                          + (f"; კიდევ {more} პატარა ადგილი არ ჩანს" if more else ""))
        self.auto_btn.setVisible(bool(self.manual))
        self._tell()

    def _opts(self, pair):
        """What visual_diff gets for one pair: aligned by hand, if it was."""
        o = dict(self.opts)
        if pair in self.manual:
            o["points"] = self.manual[pair]
        return o

    def _fill(self):
        self.list.blockSignals(True)
        self.list.clear()
        for e in self.entries:
            self.list.addItem(QListWidgetItem(describe(e)))
        self.list.blockSignals(False)
        self._paint(-1)

    def _paint(self, current):
        old, new = {}, {}
        for i, e in enumerate(self.entries):
            po, pn = e["pair"]
            for r in e["old"]:
                old.setdefault(po, []).append((r, "mark-diff-old", i == current))
            for r in e["new"]:
                new.setdefault(pn, []).append((r, "mark-diff-new", i == current))
        self.old.view.frames, self.new.view.frames = old, new
        self.update_views()

    def update_views(self):
        for v in (self.old.view, self.new.view):
            v.viewport().update()

    # -- going to a change ----------------------------------------------------
    def step(self, delta):
        n = len(self.entries)
        if n:
            row = self.list.currentRow()
            self.list.setCurrentRow((row + delta) % n if row >= 0 else (0 if delta > 0 else n - 1))

    def _show(self, row):
        if not 0 <= row < len(self.entries):
            return
        e = self.entries[row]
        self._paint(row)
        po, pn = e["pair"]
        old_r, new_r = _union(e["old"]), _union(e["new"])
        if new_r is None and old_r is not None and po is not None:
            new_r, pn = self._mapped(self.old.view, self.new.view, po, old_r) or (None, pn)
        if old_r is None and new_r is not None and pn is not None:
            old_r, po = self._mapped(self.new.view, self.old.view, pn, new_r) or (None, po)
        with self.link.paused():
            for view, pno, r in ((self.old.view, po, old_r), (self.new.view, pn, new_r)):
                if pno is None:
                    continue
                if r is None:
                    view.go_to(pno)
                else:
                    view.show_rect(pno, r)

    def _mapped(self, src, dst, pno, rect):
        """A rectangle of one side where it is on the other: (rect, page) or None."""
        corners = [self._place(src, dst, pno, QPointF(x, y))
                   for x in (rect[0], rect[2]) for y in (rect[1], rect[3])]
        if any(c is None for c in corners):
            return None
        xs, ys = [c[1].x() for c in corners], [c[1].y() for c in corners]
        return (min(xs), min(ys), max(xs), max(ys)), corners[0][0]

    # -- moving together --------------------------------------------------------
    def _matrix(self, pair):
        r = self.results.get(pair)
        return r["matrix"] if r else [[1, 0, 0], [0, 1, 0]]

    def _place(self, src, dst, pno, pt):
        forward = src is self.old.view
        pair = next((p for p in self.pairs if p[0 if forward else 1] == pno), None)
        if pair is None:
            return None
        (a, b, c), (d, e, f) = self._matrix(pair)
        x, y = pt.x(), pt.y()
        if forward:
            return pair[1], QPointF(a * x + b * y + c, d * x + e * y + f)
        det = a * e - b * d or 1.0
        x, y = x - c, y - f
        return pair[0], QPointF((e * x - b * y) / det, (a * y - d * x) / det)

    def _scale(self, src, dst):
        """How much dst is zoomed against src for one physical size on the screen. The matrix
        is old -> new: a new drawing s times bigger in points is zoomed s times smaller."""
        pno = src.page
        forward = src is self.old.view
        pair = next((p for p in self.pairs if p[0 if forward else 1] == pno), None)
        if pair is None:
            return 1.0
        (a, b, _), (d, e, _) = self._matrix(pair)
        s = math.sqrt(abs(a * e - b * d)) or 1.0
        return 1 / s if forward else s

    def set_linked(self, on):
        self.linked = on
        self.link.set_on(on, self._focused())

    def _focused(self):
        return self.new.view if self.new.view.hasFocus() else self.old.view

    def _zoom(self, factor):
        v = self._focused()
        v.set_zoom(v.zoom * factor)

    def _fit(self, how):
        v = self._focused()
        v.set_fit(how)
        if self.linked:
            self.link.follow(v, self.new.view if v is self.old.view else self.old.view)

    # -- the window ----------------------------------------------------------
    def recolor(self):
        self.banner.recolor()
        self.update_views()

    def keyPressEvent(self, e):
        """Esc stops aligning by hand wherever the focus is (the list, a button, a pane)."""
        if e.key() == Qt.Key.Key_Escape and self.aligning:
            self.cancel_align()
            return
        super().keyPressEvent(e)

    def close_document(self):
        self._closed = True
        self._again.stop()
        self.link.close()
        self.old.close_pane()
        self.new.close_pane()
