"""One open document: page view, sidebar, page organiser, editing and saving."""
from __future__ import annotations

import bisect
import html
import os
import shutil

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter,
                               QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import files, theme
from .client import (P_OPEN, P_SEARCH, P_THUMB, EngineClient, TaskError, _blocking, run_task,
                     run_thread)
from .dialogs import (PDF_FILTER, CropDialog, InsertDialog, SplitDialog, error_text, inspect_file,
                      warn_unprotected)
from .engine import IN_MEMORY_LIMIT
from .banner import Banner
from .thumbs import RENDER_H, RENDER_W, ThumbList, Thumbs
from .linked import Linker
from .tools import CommentsPanel, ToolsMixin
from .view import PageView, to_image

MAX_LISTED = 2000  # search results shown in the list; all of them are highlighted
CONTENT = ("tile", "chars", "links", "widgets", "thumb", "search", "comments")


class DocView(ToolsMixin, QWidget):
    ready = Signal()
    closed = Signal(str)     # could not be opened; text to show (may be empty)
    status = Signal(str)
    changed = Signal()       # edited, saved, or mode switched: refresh the window
    pageMenu = Signal(QPoint)
    openRequested = Signal(str)
    pageChanged = Signal(int)   # of the pane being worked in
    zoomChanged = Signal(float)
    hovered = Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.sizes, self.labels, self.info = [], [], {}
        self.loaded = False
        self.start_page = 0
        self._password = ""
        self._gen = 0
        self._found: list = []        # (page, index) in page order
        self._left = 0
        # editing
        self.ops: list = []
        self._redo: list = []
        self._saved_ops: list = []
        self._busy = False
        self._base = None             # snapshot of the original, ops replay on it
        self._tmp = None
        self._seq = 0
        self._select_after = None
        self._overwrite_ok = False
        self._saving = False
        self.broken = False           # the edited document could not be rebuilt
        self._tries = 0
        self._base_flags: dict = {}
        self._holds: list = []        # open handles: nobody can delete our working files
        self._base_password = ""      # opens the snapshot, whatever password is set later
        self._base_protected = False
        self._backups: list = []      # made while this tab was open
        self._bad_thumbs: set = set()

        self.thumbs = Thumbs()
        self.client = EngineClient(self)
        self.client.done.connect(self._done)
        self.client.failed.connect(self._failed)
        self._views: list = []
        self._active = None
        self.linked = True            # two panes move together (ჩართალა)
        self._link = None
        self._pane(PageView(self.client, self.thumbs))
        self._build_sidebar()
        self._build_organizer()
        self.panes = QSplitter()      # one pane, or two of the same document
        self.panes.setChildrenCollapsible(False)
        self.panes.addWidget(self.view)
        self.center = QStackedWidget()
        self.center.addWidget(self.panes)
        self.center.addWidget(self.organizer)
        self.banner = Banner()
        right = QWidget()
        col = QVBoxLayout(right)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.banner)
        col.addWidget(self.center, 1)
        self.splitter = QSplitter()
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([250, 900])
        self.loading = QLabel("იხსნება…")
        self.loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.loading)
        lay.addWidget(self.splitter)
        self.splitter.hide()
        self._open()

    # -- panes (ჩართალა) ----------------------------------------------------
    @property
    def view(self) -> PageView:
        """The pane being worked in: the last one clicked, drawn in or focused."""
        return self._active

    @property
    def views(self) -> list:
        return list(self._views)

    def _pane(self, v):
        self._views.append(v)
        self._active = self._active or v

        def mine(fn):  # the pane that sent it is the one worked in, focus or not
            return lambda *a: (self._activate(v), fn(*a))
        v.activated.connect(lambda: self._activate(v))
        v.toolDone.connect(mine(lambda *a: self.tool_done(*a)))
        v.clicked.connect(mine(lambda *a: self.field_clicked(*a)))
        v.contextAt.connect(mine(lambda *a: self.view_menu(*a)))
        v.cropDrawn.connect(mine(lambda *a: self._crop_drawn(*a)))
        v.objectsWanted.connect(lambda p: self._load_objects(v, p))
        v.objectChanged.connect(mine(lambda *a: self._object_moved(*a)))
        v.objectDeleted.connect(mine(lambda *a: self._object_deleted(*a)))
        v.objectTurned.connect(mine(lambda *a: self._object_turned(*a)))
        v.objectCopy.connect(mine(lambda *a: self._object_copy(*a)))
        v.snapsWanted.connect(lambda p: self._load_snaps(v, p))
        v.uriActivated.connect(self._open_uri)
        v.pageChanged.connect(lambda p: v is self._active and (
            self._page_changed(p), self.pageChanged.emit(p)))
        v.zoomChanged.connect(lambda z: v is self._active and self.zoomChanged.emit(z))
        v.hovered.connect(lambda t: v is self._active and self.hovered.emit(t))
        return v

    def _activate(self, v):
        if v is not self._active and v in self._views:
            self._active = v
            self._page_changed(v.page)
            self.changed.emit()

    def update_views(self):
        for v in self._views:
            v.viewport().update()

    @property
    def split_now(self):
        return len(self._views) > 1

    def split_panes(self, orientation):
        """The same document in a second pane, beside (Horizontal) or under (Vertical)."""
        if not self.loaded:
            return
        self.organize(False)
        self.panes.setOrientation(orientation)
        if self.split_now:
            self.changed.emit()
            return
        first = self._views[0]
        v = self._pane(PageView(self.client, self.thumbs, shared=first.shared))
        v.rev, v.frozen = first.rev, first.frozen
        v.zoom, v.fit, v.rotation, v.two_up = first.zoom, first.fit, first.rotation, first.two_up
        v.set_tool(first.tool)
        self.panes.addWidget(v)
        half = max(self.panes.width(), 2) // 2
        self.panes.setSizes([half, half])
        v.set_document(self.sizes, first.can_copy)
        v.go_to(first.page)
        self.linked = False             # split: each pane on its own until asked otherwise
        self._link = Linker(first, v, parent=self)
        self._link.on = self.linked
        # once laid out, the new pane comes to the first: unless turned apart meanwhile
        QTimer.singleShot(0, lambda: self._link and self.linked and self._link.set_on(True, first))
        self.changed.emit()

    def unsplit(self):
        if not self.split_now:
            return
        keep = self._active             # the pane worked in stays, with its place
        v = next(x for x in self._views if x is not keep)
        self._views.remove(v)           # keep is the first one now: it moves what is shared
        keep.shared.views.remove(v)
        if self._link is not None:
            self._link.close()
            self._link.deleteLater()
            self._link = None
        keep.setFocus()
        v.hide()
        v.setParent(None)
        v.deleteLater()
        self.changed.emit()

    def set_linked(self, on):
        self.linked = on
        if self._link is not None:
            self._link.set_on(on, self.view)

    # -- opening -----------------------------------------------------------
    def _open(self):
        self.client.submit(("open",), P_OPEN, "open", self.path, self._password)

    def _opened(self, info):
        self.info, self.sizes, self.labels = info, info["sizes"], info["labels"]
        self.loaded = True
        for lst in (self.list, self.grid):
            lst.model_.reset()
        self._fill_toc(info["toc"])
        self.loading.hide()
        self.splitter.show()
        for v in self._views:
            v.set_document(self.sizes, info["can_copy"])
        if self.start_page:
            QTimer.singleShot(0, lambda: self.view.go_to(self.start_page))
        self.view.setFocus()
        self.ready.emit()
        if info["repaired"]:
            self.notify("warning", "ფაილი დაზიანებული იყო და გასწორებული სახით გაიხსნა. "
                                   "ორიგინალი არ შეცვლილა.")

    def notify(self, kind, text, action_text="", action=None):
        """A message over the document that stays until it is closed; also in the status bar."""
        self.banner.show_message(kind, text, action_text, action)
        self.status.emit(text)

    def _ask_password(self, wrong):
        text = ("პაროლი არასწორია. სცადეთ ხელახლა:" if wrong
                else "ეს ფაილი პაროლით არის დაცული. შეიყვანეთ პაროლი:")
        pw, ok = QInputDialog.getText(self, os.path.basename(self.path), text,
                                      QLineEdit.EchoMode.Password)
        if ok and pw:
            self._password = pw
            self._open()
        else:
            self.closed.emit("")

    # -- engine results ----------------------------------------------------
    def _done(self, key, value):
        kind = key[0]
        if kind == "open":
            self._opened(value)
        elif kind == "thumb":
            if key[2] == self.view.rev:
                self.thumbs.put(key[1], to_image(value))
                for lst in (self.list, self.grid):
                    lst.model_.refresh(key[1])
        elif kind == "search":
            if key[1] == self._gen:
                self._add_hits(key[2], value)
        elif kind in ("tile", "chars", "links", "widgets"):
            for v in self._views:  # one answer for every pane that wants it
                v.on_done(key, value)
        elif kind == "comments":
            if key[1] == self.view.rev:
                self.comments.show_list(value)
        elif kind in ("edit", "undo", "redo", "restore"):
            self._edit_done(kind, value)

    def _failed(self, key, code):
        kind = key[0]
        if kind == "open":
            if code in ("needs_password", "wrong_password"):
                self._ask_password(code == "wrong_password")
            else:
                self.closed.emit(error_text(code))
        elif kind == "thumb":
            self._bad_thumbs.add(key)
        elif kind == "search":
            if key[1] == self._gen:
                self._add_hits(key[2], [])
        elif kind in ("tile", "chars", "links", "widgets"):
            for v in self._views:
                v.on_failed(key)
        elif kind in ("edit", "undo", "redo", "restore"):
            self._edit_failed(kind, code)
        if kind in CONTENT and code in ("crash", "engine_dead"):
            self.notify("danger", error_text(code))
        elif code == "file_changed" and not self.broken:
            self.broken = True  # what is shown and what is on the disk no longer agree
            self.notify("danger", error_text(code))
            self.changed.emit()

    def close_document(self):
        tmp, self._tmp = self._tmp, None
        # The engine may still be busy: it ends on its own, and its working files go after it.
        self.client.shutdown(wait=False, after=(
            lambda: shutil.rmtree(tmp, ignore_errors=True)) if tmp else None)
        for f in self._holds:
            f.close()
        self._holds.clear()
        if getattr(self, "ai", None) is not None:
            self.ai.reset()

    # -- layout ------------------------------------------------------------
    def _build_sidebar(self):
        self.sidebar = QTabWidget()
        self.sidebar.setObjectName("sidebar")  # segmented look: assets/etrati.qss
        self.sidebar.tabBar().setDrawBase(False)
        self.list = ThumbList(self, 120, 160)
        self.list.clicked.connect(self._thumb_clicked)
        self._wire(self.list)
        self.sidebar.addTab(self.list, "გვერდები")

        self.toc = QTreeWidget()
        self.toc.setHeaderHidden(True)
        self.toc.itemClicked.connect(self._toc_clicked)
        self.toc.itemActivated.connect(self._toc_clicked)
        self.sidebar.addTab(self.toc, "სანიშნეები")

        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 8, 8, 8)
        self.query = QLineEdit()
        self.query.setPlaceholderText("საძიებო სიტყვა")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self._enter)
        self.query.textChanged.connect(lambda *_: self._search_timer.start())
        v.addWidget(self.query)
        row = QHBoxLayout()
        self.prev_btn, self.next_btn = QPushButton(), QPushButton()
        for b, name, tip in ((self.prev_btn, "chevron-left", "წინა შედეგი (Shift+F3)"),
                             (self.next_btn, "chevron-right", "შემდეგი შედეგი (F3)")):
            b.setIcon(theme.icon(name))
            b.setToolTip(tip)
            b.setAccessibleName(tip)
        self.prev_btn.clicked.connect(lambda: self.next_hit(-1))
        self.next_btn.clicked.connect(lambda: self.next_hit(1))
        row.addWidget(self.prev_btn)
        row.addWidget(self.next_btn)
        v.addLayout(row)
        self.found_label = QLabel("")
        self.found_label.setWordWrap(True)
        v.addWidget(self.found_label)
        self.results = QListWidget()
        self.results.setWordWrap(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.itemClicked.connect(
            lambda it: self._select_hit(it.data(Qt.ItemDataRole.UserRole)))
        v.addWidget(self.results)
        self.sidebar.addTab(box, "ძიება")
        self.comments = CommentsPanel(self)
        self.sidebar.addTab(self.comments, "კომენტარები")
        self.sidebar.currentChanged.connect(lambda *_: self.load_comments())
        self.sidebar.setMinimumWidth(self.sidebar.tabBar().sizeHint().width() + 12)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350)
        self._search_timer.timeout.connect(self.start_search)

    def _build_organizer(self):
        self.organizer = QWidget()
        v = QVBoxLayout(self.organizer)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.org_bar = QHBoxLayout()
        self.org_bar.setContentsMargins(8, 6, 8, 6)
        v.addLayout(self.org_bar)
        self.grid = ThumbList(self, RENDER_W, RENDER_H)
        self.grid.doubleClicked.connect(lambda i: (self.organize(False), self.view.go_to(i.row())))
        self._wire(self.grid)
        v.addWidget(self.grid)

    def _wire(self, lst):
        lst.moved.connect(self.move_pages)
        lst.filesDropped.connect(self._files_dropped)
        lst.deletePressed.connect(self.delete_pages)
        lst.menuRequested.connect(self.pageMenu)
        lst.verticalScrollBar().valueChanged.connect(self._drop_far_thumbs)

    @property
    def organizing(self):
        return self.center.currentIndex() == 1

    def organize(self, on):
        if on == self.organizing:
            return
        self.center.setCurrentIndex(1 if on else 0)
        if on:
            self.view.cancel_crop()
            self._was_side = self.sidebar.isVisible()
            self.sidebar.hide()
            self.grid.select([self.view.page])
            self.grid.scrollTo(self.grid.model_.index(self.view.page))
            self.grid.setFocus()
        else:
            self.sidebar.setVisible(self._was_side)
            self.view.setFocus()
        self.changed.emit()

    def _thumb_clicked(self, index):
        mods = QGuiApplication.keyboardModifiers()
        if not mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self.view.go_to(index.row())

    def page_label(self, pno):
        lab = self.labels[pno] if self.labels else ""
        return f"{lab} ({pno + 1})" if lab and lab != str(pno + 1) else str(pno + 1)

    def request_thumb(self, pno):
        key = ("thumb", pno, self.view.rev)
        if self._busy or key in self._bad_thumbs:
            return
        w, h = self.sizes[pno]
        k = min(RENDER_W / w, RENDER_H / h) * max(self.devicePixelRatioF(), 1.0)
        self.client.submit(key, P_THUMB, "render", pno, k, 0)

    def _drop_far_thumbs(self):
        a, b = (self.grid if self.organizing else self.list).visible_rows()
        self.client.cancel(lambda k: k[0] == "thumb" and not a - 15 <= k[1] <= b + 15)

    def _page_changed(self, pno):
        self.list.follow(pno)

    def _fill_toc(self, toc):
        self.toc.clear()
        stack: list = []
        for level, title, page, y in toc:
            while stack and stack[-1][0] >= level:
                stack.pop()
            item = QTreeWidgetItem([" ".join(title.split()) or "—"])
            item.setData(0, Qt.ItemDataRole.UserRole, (page, y))
            item.setToolTip(0, html.escape(title))
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.toc.addTopLevelItem(item)
            stack.append((level, item))
        if len(toc) <= 60:
            self.toc.expandAll()
        self.sidebar.setTabEnabled(1, bool(toc))
        self.sidebar.setTabToolTip(1, "" if toc else "ამ ფაილს სანიშნეები არ აქვს")

    def _toc_clicked(self, item, *_):
        page, y = item.data(0, Qt.ItemDataRole.UserRole)
        if page >= 0:
            self.organize(False)
            self.view.go_to(page, y)

    # -- search ------------------------------------------------------------
    def open_search(self):
        self.organize(False)
        self.sidebar.show()
        self.sidebar.setCurrentIndex(2)
        sel = self.view.selected_text()
        if sel and "\n" not in sel and len(sel) < 80:
            self.query.setText(sel)
        self.query.setFocus()
        self.query.selectAll()

    def _enter(self):
        if self._search_timer.isActive():
            self._search_timer.stop()
            self.start_search()
        else:
            back = QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
            self.next_hit(-1 if back else 1)

    def start_search(self, jump=True):
        self._gen += 1
        self._jump = jump
        self.client.cancel(lambda k: k[0] == "search")
        self._found, self.view.hits, self.view.current_hit = [], {}, None
        self.results.clear()
        self.update_views()
        text = " ".join(self.query.text().split())
        if len(text) < 2 or not self.loaded:
            self.found_label.setText("" if not text else "შეიყვანეთ მინიმუმ 2 სიმბოლო")
            self._left = 0
            return
        n = len(self.sizes)
        self._left = n
        self.found_label.setText("იძებნება…")
        start = self.view.page
        for i in range(n):  # from the page being read, then wrap around
            p = (start + i) % n
            self.client.submit(("search", self._gen, p), P_SEARCH, "search", p, text)

    def _add_hits(self, pno, hits):
        self._left -= 1
        if hits:
            self.view.hits[pno] = hits
            at = bisect.bisect_left(self._found, (pno, 0))
            self._found[at:at] = [(pno, i) for i in range(len(hits))]
            for i, (_, snippet) in enumerate(hits):
                if at + i >= MAX_LISTED:
                    break
                it = QListWidgetItem(f"გვ. {pno + 1}:  {snippet}")
                it.setData(Qt.ItemDataRole.UserRole, (pno, i))
                self.results.insertItem(at + i, it)
            while self.results.count() > MAX_LISTED:
                self.results.takeItem(MAX_LISTED)
            if self.view.current_hit is None and self._jump:
                self._select_hit((pno, 0))
            self.update_views()
        n = len(self._found)
        if self._left > 0:
            self.found_label.setText(f"იძებნება… ნაპოვნია {n}")
        else:
            more = f" (სიაში პირველი {MAX_LISTED})" if n > MAX_LISTED else ""
            self.found_label.setText(f"ნაპოვნია {n}{more}" if n else
                                     "ვერ მოიძებნა. თუ ფაილი დასკანერებულია, მასში ტექსტი არ არის.")

    def next_hit(self, delta):
        if not self._found:
            return
        self.organize(False)
        cur = self.view.current_hit
        i = bisect.bisect_left(self._found, cur) if cur else -1
        if not (cur and i < len(self._found) and self._found[i] == cur):
            i = -1 if delta > 0 else 0
        self._select_hit(self._found[(i + delta) % len(self._found)])

    def _select_hit(self, hit):
        hit = tuple(hit)
        self.view.current_hit = hit
        row = bisect.bisect_left(self._found, hit)
        self.results.setCurrentRow(row if row < self.results.count() else -1)
        self.view.show_rect(hit[0], self.view.hits[hit[0]][hit[1]][0])
        self.update_views()  # the current hit is marked in every pane

    # -- editing -----------------------------------------------------------
    @property
    def can_edit(self):
        return self.loaded and not self._busy and not self.broken and self.info["can_assemble"]

    NEEDS = {"annot": "can_annotate", "annot_del": "can_annotate", "annot_text": "can_annotate",
             "annot_move": "can_annotate", "fill": "can_fill", "protect": None,
             "measure": "can_annotate", "measure_note": "can_annotate"}

    def allowed(self, op_name):
        pages = ("rotate", "order", "duplicate", "insert", "blank", "crop")
        need = "can_assemble" if op_name in pages else self.NEEDS.get(op_name, "can_modify")
        return (self.loaded and not self._busy and not self.broken
                and (need is None or self.info[need]))

    @property
    def dirty(self):
        return self.ops != self._saved_ops

    @property
    def can_undo(self):
        return self.loaded and not self._busy and not self.broken and bool(self.ops)

    @property
    def can_redo(self):
        return self.loaded and not self._busy and not self.broken and bool(self._redo)

    def selected_pages(self) -> list[int]:
        if self.organizing:
            return self.grid.selected()
        if self.sidebar.isVisible() and self.sidebar.currentIndex() == 0:
            return self.list.selected() or [self.view.page]
        return [self.view.page]

    def _session(self):
        if self._tmp is None:
            self._tmp = files.new_session()
        return self._tmp

    def keep(self, path, name):
        """Copy a file into the session and hold it open, so that undo can rely on it."""
        copy = files.unique_path(os.path.join(self._session(), name))
        if os.path.getsize(path) > 20 * 1024 * 1024:
            run_thread(lambda: shutil.copyfile(path, copy), "ფაილი მზადდება…", self)
        else:
            shutil.copyfile(path, copy)
        self._holds.append(open(copy, "rb"))  # Windows does not delete open files
        return copy

    def _assets_present(self, ops):
        paths = [self._base] + [op[1] for op in ops if op[0] == "insert"]
        paths += [op[3] for op in ops if op[0] == "image"]
        paths += [op[4] for op in ops if op[0] == "image_swap"]
        return all(p and os.path.isfile(p) for p in paths)

    def _ensure_base(self) -> bool:
        """Snapshot the original once; undo and crash recovery replay on it."""
        if self._base:
            return True
        try:
            st = os.stat(self.path)
            if (st.st_mtime_ns, st.st_size) != tuple(self.info["stamp"]):
                QMessageBox.warning(self, "რედაქტირება",
                                    "ფაილი დისკზე შეიცვალა მას შემდეგ, რაც გახსენით. "
                                    "დახურეთ და თავიდან გახსენით.")
                return False
            self._busy = True
            try:
                base = self.keep(self.path, "base.pdf")
            finally:
                self._busy = False
        except OSError:
            QMessageBox.warning(self, "რედაქტირება",
                                "საწყისი ფაილის ასლი ვერ შეიქმნა, ამიტომ რედაქტირება არ დაიწყო.")
            return False
        self._base = base
        self._base_password = self._password
        self._base_protected = self.info["protected"]
        self._base_flags = {k: self.info[k] for k in self.FLAGS}
        return True

    FLAGS = ("can_print", "can_copy", "can_assemble", "can_annotate", "can_fill", "can_modify")

    def edit(self, op, select_after=None) -> bool:
        if not self.loaded or self._busy or self.broken:
            return False
        if not self.allowed(op[0]):
            self.status.emit("ავტორმა ამ ფაილის ასეთი ცვლილება აკრძალა.")
            return False
        if not self._ensure_base():
            return False
        self._select_after = select_after
        self._op = op
        if self.info["bytes"] > IN_MEMORY_LIMIT and not self.ops:
            # The engine still reads the original from disk: move it to the snapshot.
            self._start("edit", "replay", self._base, self._base_password, [op])
        else:
            self._start("edit", "apply", op)
        return True

    def undo(self):
        if self.can_undo:
            if not self._assets_present(self.ops[:-1]):
                QMessageBox.warning(self, "გაუქმება", "გასაუქმებლად საჭირო დროებითი ფაილი წაშლილია, "
                                    "ამიტომ არაფერი შევცვალე. დოკუმენტი უცვლელია, შეგიძლიათ შეინახოთ.")
                return
            self._select_after = None
            self._start("undo", "replay", self._base, self._base_password, self.ops[:-1])

    def redo(self):
        if self.can_redo:
            self._select_after = None
            self._start("redo", "apply", self._redo[-1])

    def _start(self, kind, op, *args):
        self._busy = True
        # The engine's side notes the success itself, so a crash right after it loses nothing.
        self.client.recover_with(self._base, self._base_password, self.ops)
        for v in self._views:
            v.frozen = True
        self.client.cancel(lambda k: k[0] in CONTENT)
        self._seq += 1
        self.client.submit((kind, self._seq), P_OPEN, op, *args)
        self.changed.emit()

    def _edit_done(self, kind, info):
        self._tries = 0
        if kind == "edit":
            self.ops.append(self._op)
            self._redo.clear()
        elif kind == "undo":
            self._redo.append(self.ops.pop())
        elif kind == "redo":
            self.ops.append(self._redo.pop())
        self.client.recover_with(self._base, self._base_password, self.ops)
        self._show_changed(info, kind)

    def _edit_failed(self, kind, code):
        if code == "closed":
            return
        if kind == "restore":
            # A crash or a slow disk may pass: the edits are still listed here.
            self._tries += 1
            again = self._tries <= 2 or QMessageBox.question(
                self, "რედაქტირება", "დოკუმენტის აღდგენა ვერ მოხერხდა. თქვენი ცვლილებები ჯერ არ დაკარგულა. "
                "თავიდან ვცადო?") == QMessageBox.StandardButton.Yes
            if again and self._assets_present(self.ops):
                self._start("restore", "replay", self._base, self._base_password, self.ops)
                return
            # The engine holds no document now. Saving would write the wrong thing.
            self.broken = True
            self._busy = False
            self.changed.emit()
            QMessageBox.critical(
                self, "რედაქტირება", "დოკუმენტის აღდგენა ვერ მოხერხდა, ამიტომ შენახვა გამორთულია, რომ ფაილი "
                "არ დაზიანდეს. დისკზე ფაილი უცვლელია. დახურეთ ეს ჩანართი და თავიდან გახსენით; ძველი ვერსიები "
                "სარეზერვო ასლების საქაღალდეშია.")
            return
        self._after = ""
        QMessageBox.warning(self, "რედაქტირება", error_text(code))
        # whatever was half done is thrown away
        self._start("restore", "replay", self._base, self._base_password, self.ops)

    _after = ""  # status text for when the running edit succeeds

    MOVES = ("rotate", "crop", "order", "insert", "blank", "duplicate")

    def _show_changed(self, info, kind="edit"):
        mapping = info["mapping"]
        kept = [] if mapping is None else [
            mapping.index(p) for p in (self.grid if self.organizing else self.list).selected()
            if p in mapping]
        self.info.update(sizes=info["sizes"], labels=info["labels"], toc=info["toc"],
                         is_form=info.get("is_form", False))
        self.sizes, self.labels = info["sizes"], info["labels"]
        self.thumbs.remap(mapping)
        self._bad_thumbs.clear()
        self._busy = False
        for v in self._views:
            v.frozen = False
            v.cancel_crop()  # the pages it was meant for may be others now
        self._moving = None
        pos = self.grid.verticalScrollBar().value()
        last = self.ops[-1][0] if self.ops and kind in ("edit", "redo") else ""
        odd = sum(n for what, n in info.get("notes") or () if what == "photos")
        if odd and kind == "edit":
            QMessageBox.information(
                self, "ფარული მონაცემები", f"{odd} ფოტოს ფორმატი უჩვეულოა, ამიტომ კამერისა და ადგილის მონაცემები "
                "მათში შეიძლება დარჩა.")
        turned = [p for what, p in info.get("notes") or () if what == "picture"]
        if turned and kind in ("edit", "redo"):
            QMessageBox.information(
                self, "დაფარვა", f"გვერდ(ებ)ზე {files.describe(turned)} სიტყვის ცალკე ამოჭრა დანარჩენ ტექსტს დაძრავდა. "
                "ამიტომ ეს გვერდები სურათად იქცა: დაფარული ტექსტი აღარ არსებობს, დანარჩენი კი ისევ "
                "იძებნება და კოპირდება. ასეთ გვერდზე ტექსტის შეცვლა ვეღარ მოხერხდება.")
        said = [x for what, x in info.get("notes") or () if what == "mentions"]
        if said and kind == "edit":
            QMessageBox.information(
                self, "დაფარვა", "დაფარული ტექსტი კიდევ გვხვდება გვერდების გარეთ:\n\n• "
                + "\n• ".join(said[0][:12]) + ("\n• …" if len(said[0]) > 12 else "")
                + "\n\nმათ წასაშლელად გამოიყენეთ „ფარული მონაცემების წაშლა“.")
        after, self._after = self._after, ""
        if after and kind == "edit":  # told only now, when it has really happened
            self.status.emit(after)
        if last == "redact":
            self.view.marks = {}
        first = self._views[0]
        for v in self._views:  # what they share moves once, with the first
            v.reload(self.sizes, mapping, still=bool(last) and last not in self.MOVES,
                     primary=v is first)
        if first.lost_marks:
            self.notify("warning", f"დასაფარად მონიშნული {first.lost_marks} ადგილი მოიხსნა: "
                                   "გვერდი შეიცვალა. მონიშნეთ თავიდან.")
        if mapping is not None and len(mapping) == self.list.model_.rows:
            for lst in (self.list, self.grid):  # same pages: refresh without losing scroll
                for p, o in enumerate(mapping):
                    if o != p:
                        lst.model_.refresh(p)
        else:
            for lst in (self.list, self.grid):
                lst.model_.reset()
        self._fill_toc(info["toc"])
        self._after_objects(kind)
        sel = kept if self._select_after is None else self._select_after
        sel = [p for p in sel if p < len(self.sizes)]
        if self.organizing:
            self.grid.select(sel)
            self.grid.doItemsLayout()
            self.grid.verticalScrollBar().setValue(pos)
        elif len(sel) > 1:
            self.list.select(sel)
        else:
            self.list.follow(self.view.page)
        if self.query.text().strip():
            self.start_search(jump=False)
        self.load_comments()
        self.changed.emit()

    def rotate_pages(self, delta):
        pages = self.selected_pages()
        if pages:
            self.edit(("rotate", pages, delta), pages)

    def delete_pages(self):
        pages = set(self.selected_pages())
        if not pages or not self.can_edit:
            return
        order = [i for i in range(len(self.sizes)) if i not in pages]
        if not order:
            QMessageBox.information(self, "წაშლა", "ყველა გვერდის წაშლა შეუძლებელია: ერთი მაინც უნდა დარჩეს.")
            return
        if self.edit(("order", order), [min(min(pages), len(order) - 1)]):
            self._after = f"წაიშალა გვერდი {files.describe(list(pages))}. დაბრუნება: Ctrl+Z"

    def move_pages(self, pages, at):
        chosen = set(pages)
        rest = [i for i in range(len(self.sizes)) if i not in chosen]
        cut = sum(1 for i in rest if i < at)
        order = rest[:cut] + sorted(chosen) + rest[cut:]
        if order != list(range(len(self.sizes))):
            self.edit(("order", order), list(range(cut, cut + len(chosen))))

    def shift_pages(self, delta):
        pages = self.selected_pages()
        if not pages:
            return
        if delta < 0 and pages[0] > 0:
            self.move_pages(pages, pages[0] - 1)
        elif delta > 0 and pages[-1] < len(self.sizes) - 1:
            self.move_pages(pages, pages[-1] + 2)

    def duplicate_pages(self):
        pages = self.selected_pages()
        if pages:
            at = pages[-1] + 1
            self.edit(("duplicate", pages), list(range(at, at + len(pages))))

    def insert_blank(self):
        if not self.can_edit:
            return
        pages = self.selected_pages() or [len(self.sizes) - 1]
        w, h = self.sizes[pages[-1]]
        self.edit(("blank", pages[-1] + 1, w, h), [pages[-1] + 1])

    def insert_file(self):
        if not self.can_edit:
            return
        path, _ = QFileDialog.getOpenFileName(self, "რომელი ფაილიდან ჩავსვა",
                                              os.path.dirname(self.path), PDF_FILTER)
        if not path:
            return
        got = inspect_file(self.client, path, self)
        if got is None:
            return
        pages = self.selected_pages() or [len(self.sizes) - 1]
        dlg = InsertDialog(os.path.basename(path), got[0], pages[-1] + 1, len(self.sizes), self)
        if dlg.exec():
            self._insert(path, got, dlg.pages(), dlg.after.value())

    def _insert(self, path, got, pages, at):
        if not self.can_edit:
            return
        if got[2] and not self.info["protected"] and not warn_unprotected(self):
            return
        # Undo must work even if that file changes or disappears later.
        try:
            copy = self.keep(path, "insert.pdf")
        except OSError:
            QMessageBox.warning(self, "ჩასმა", error_text("no_access"))
            return
        n = got[0] if pages is None else len(pages)
        self.edit(("insert", copy, got[1], pages, at), list(range(at, at + n)))

    def _files_dropped(self, paths, at):
        path = paths[0]
        got = inspect_file(self.client, path, self)
        if got:
            self._insert(path, got, None, at)
            if len(paths) > 1:
                self.status.emit("ჩაისვა მხოლოდ პირველი ფაილი. რამდენიმესთვის გამოიყენეთ „გაერთიანება“.")

    # -- crop --------------------------------------------------------------
    def start_crop(self):
        if self.can_edit:
            # remember now: drawing happens in the page view, where selection is not visible
            self._crop_pages = self.selected_pages()
            self.organize(False)
            if self._crop_pages and self.view.page not in self._crop_pages:
                self.view.go_to(self._crop_pages[0])
            self.view.start_crop()
            self.status.emit("მონიშნეთ მაუსით დასატოვებელი არე. გაუქმება: Esc")

    def _crop_drawn(self, pno, rect):
        w, h = self.sizes[pno]
        margins = (rect[0], rect[1], w - rect[2], h - rect[3])
        pages = self._crop_pages if pno in self._crop_pages else [pno]
        dlg = CropDialog((w, h), margins, pages, len(self.sizes), self)
        if dlg.exec():
            target = list(range(len(self.sizes))) if dlg.all_pages() else pages
            m = dlg.margins()
            small = [p for p in target if self.sizes[p][0] - m[0] - m[2] < 20
                     or self.sizes[p][1] - m[1] - m[3] < 20]
            if small:
                QMessageBox.warning(self, "ჩამოჭრა", "ეს ჩამოჭრა ზედმეტად დიდია გვერდებისთვის: "
                                    + files.describe(small))
                return
            self.edit(("crop", target, m), pages)

    def uncrop(self):
        pages = self.selected_pages()
        if pages:
            self.edit(("crop", pages, None), pages)

    # -- new files ---------------------------------------------------------
    def _may_extract(self):
        if not self.info["can_copy"]:
            QMessageBox.information(self, "გვერდები", "ავტორმა ამ ფაილიდან ასლის აღება აკრძალა.")
            return False
        if not self.marks_ok():
            return False
        return not self.info["protected"] or warn_unprotected(self)

    def extract_pages(self):
        pages = self.selected_pages()
        if not pages or self._busy or not self._may_extract():
            return None
        stem = os.path.splitext(self.path)[0]
        start = files.unique_path(f"{stem} (გვ. {files.describe(pages)}).pdf")
        out, _ = QFileDialog.getSaveFileName(self, "ამოღებული გვერდების შენახვა", start, PDF_FILTER)
        if not out:
            return None
        if not out.lower().endswith(".pdf"):
            out = files.unique_path(out + ".pdf")
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(self.path)):
            QMessageBox.warning(self, "ამოღება", "აირჩიეთ სხვა სახელი: ეს თვითონ ეს ფაილია.")
            return None
        return out if self._export([(pages, out)]) else None

    def split(self):
        if self._busy or not self._may_extract():
            return
        dlg = SplitDialog(self, self)
        if not dlg.exec():
            return
        folder = QFileDialog.getExistingDirectory(self, "სად შევინახო ნაწილები",
                                                  os.path.dirname(self.path))
        if not folder:
            return
        parts = [(g, files.unique_path(os.path.join(folder, name))) for g, name in dlg.parts()]
        seen = set()  # two bookmarks may share a title
        fixed = []
        for g, p in parts:
            stem, ext = os.path.splitext(p)
            n = 2
            while os.path.normcase(p) in seen or os.path.exists(p):
                p = f"{stem} ({n}){ext}"
                n += 1
            seen.add(os.path.normcase(p))
            fixed.append((g, p))
        if self._export(fixed):
            if QMessageBox.question(self, "დაყოფა", f"შეიქმნა {len(fixed)} ფაილი. გავხსნა საქაღალდე?"
                                    ) == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _export(self, parts) -> bool:
        tmps = [(g, files.outbox(p)) for g, p in parts]
        try:
            run_task(self.client, "export", (tmps,), "ფაილები იქმნება…", self)
            for (_, t), (_, p) in zip(tmps, parts):
                files.put_in_place(t, p)
            return True
        except (TaskError, OSError) as e:
            QMessageBox.warning(self, "შენახვა", error_text(
                str(e) if isinstance(e, TaskError) else "no_access"))
            return False
        finally:
            for _, t in tmps:
                files.discard(t)

    # -- saving ------------------------------------------------------------
    def marks_ok(self, question="ფაილში მათი შიგთავსი მთლიანად დარჩება. გავაგრძელო?") -> bool:
        """Places marked for redaction look covered, but nothing has been removed yet."""
        n = sum(len(m) for m in self.view.marks.values())
        return not n or QMessageBox.question(
            self, "დაფარვა", f"დასაფარად მონიშნულია {n} ადგილი, მაგრამ ჯერ არაფერი წაშლილა. "
            + question) == QMessageBox.StandardButton.Yes

    def wait_idle(self):
        """Wait until the running edit, and those queued behind it, are done."""
        if not (self._busy or getattr(self, "_queue", [])):
            return
        timer = QTimer(self)
        timer.setInterval(30)

        def start(loop):
            timer.timeout.connect(lambda: loop.quit() if not (
                self._busy or getattr(self, "_queue", [])) else None)
            timer.start()
        try:
            _blocking(start, "ცვლილება სრულდება…", self)
        finally:
            timer.stop()
            timer.deleteLater()

    def save(self) -> bool:
        if self._busy or self._saving:  # first: a running edit is not in the list yet
            self.status.emit("მოიცადეთ, ცვლილება ჯერ სრულდება.")
            return False
        if not self.dirty:
            return True
        if self.broken:
            return self._refuse_broken()
        if not self._overwrite_ok:
            box = QMessageBox(QMessageBox.Icon.Question, "შენახვა",
                              f"გადავაწერო „{os.path.basename(self.path)}“-ს?\n\n"
                              "სანამ გადავაწერ, ძველი ვერსიის სარეზერვო ასლს შევინახავ.", parent=self)
            over = box.addButton("გადაწერა", QMessageBox.ButtonRole.AcceptRole)
            other = box.addButton("ახალ ფაილად…", QMessageBox.ButtonRole.ActionRole)
            box.addButton("გაუქმება", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is other:
                return self.save_as()
            if box.clickedButton() is not over:
                return False
        return self._write_to(self.path)

    def _refuse_broken(self):
        QMessageBox.warning(self, "შენახვა", "ეს დოკუმენტი სწორად ვერ აღდგა, ამიტომ მისი შენახვა გამორთულია. "
                            "დახურეთ ჩანართი და თავიდან გახსენით.")
        return False

    def save_as(self) -> bool:
        if self.broken:
            return self._refuse_broken()
        if self._busy or self._saving:
            return False
        out, _ = QFileDialog.getSaveFileName(self, "შენახვა როგორც",
                                             files.unique_path(self.path), PDF_FILTER)
        if not out:
            return False
        if not out.lower().endswith(".pdf"):
            out = files.unique_path(out + ".pdf")
        out = os.path.abspath(out)
        if self.is_open_elsewhere(out):
            QMessageBox.warning(self, "შენახვა", "ეს ფაილი სხვა ჩანართში ღიაა. აირჩიეთ სხვა სახელი.")
            return False
        return self._write_to(out)

    def is_open_elsewhere(self, path) -> bool:
        """The window replaces this: is the file open in another tab?"""
        return False

    def _write_to(self, target) -> bool:
        if self._saving:
            return False
        self._saving = True
        try:
            return self._write_now(target)
        finally:
            self._saving = False

    PRIVATE = ("redact", "scrub", "protect")

    def _offer_purge(self, ops, backup):
        """Backups still hold what was just removed or locked away."""
        if backup:
            self._backups.append(backup)
        mine = [b for b in self._backups if os.path.exists(b)]
        if not mine or not any(op[0] in self.PRIVATE for op in ops):
            return
        if QMessageBox.question(
                self, "სარეზერვო ასლი", "სარეზერვო ასლში ჯერ კიდევ არის ის, რაც ახლა წაშალეთ ან დაიცავით. "
                f"წავშალო ეს {len(mine)} ასლი?\n\nწაშლის შემდეგ ძველ ვერსიას ვეღარ დააბრუნებთ."
                ) == QMessageBox.StandardButton.Yes:
            for b in mine:
                try:
                    os.remove(b)
                except OSError:
                    QMessageBox.warning(self, "სარეზერვო ასლი", f"ვერ წაიშალა: {b}")
            self._backups = [b for b in mine if os.path.exists(b)]

    def _write_now(self, target) -> bool:
        same = os.path.normcase(target) == os.path.normcase(os.path.abspath(self.path))
        if same and os.path.exists(target):
            st = os.stat(target)
            if (st.st_mtime_ns, st.st_size) != tuple(self.info["stamp"]) and QMessageBox.question(
                    self, "შენახვა", "ეს ფაილი ამასობაში სხვა პროგრამამ შეცვალა. მაინც გადავაწერო?\n\n"
                    "სარეზერვო ასლი შეიქმნება.") != QMessageBox.StandardButton.Yes:
                return False
        if self.info["signed"] and QMessageBox.question(
                self, "შენახვა", "ამ ფაილს ციფრული ხელმოწერის ველი აქვს. შენახვის შემდეგ არსებული "
                "ხელმოწერა ძალას დაკარგავს. გავაგრძელო?") != QMessageBox.StandardButton.Yes:
            return False
        if not self.marks_ok():
            return False
        ops = list(self.ops)
        tmp = ""
        try:
            files.drop_stale_temps(target)
            tmp = files.outbox(target)
            run_task(self.client, "save", (tmp,), "ინახება…", self)
            # the engine's file is copied over from its folder: big ones while the window lives
            if max(os.path.getsize(tmp), os.path.getsize(target) if os.path.exists(target)
                   else 0) > 20 * 1024 * 1024:
                backup = run_thread(lambda: files.put_in_place(tmp, target), "ინახება…", self)
            else:
                backup = files.put_in_place(tmp, target)
        except (TaskError, OSError) as e:
            QMessageBox.warning(self, "შენახვა", error_text(
                str(e) if isinstance(e, TaskError) else "no_access")
                + "\n\nცვლილებები არ დაკარგულა, სცადეთ „შენახვა როგორც“.")
            return False
        finally:
            if tmp:
                files.discard(tmp)
        try:
            st = os.stat(target)
            self.info["bytes"] = st.st_size
            self.info["stamp"] = (st.st_mtime_ns, st.st_size)
        except OSError:  # saved, but somebody holds it just now: the next save will ask
            pass
        else:
            if same and not self._base:  # the engine still knows the file by its path
                self.client.accept_stamp(self.info["stamp"])
        self.path = target
        fresh = [op for op in ops if op not in self._saved_ops]  # not in any backup's successor yet
        self._saved_ops = ops
        self._overwrite_ok = True
        self.after_save(ops)
        self.status.emit("შენახულია." + (" ძველი ვერსია სარეზერვო ასლებშია." if backup else ""))
        self.changed.emit()
        self._offer_purge(fresh, backup)
        return True

    # -- actions -----------------------------------------------------------
    def copy(self):
        if not self.view.has_selection():
            return
        if not self.view.can_copy:
            self.status.emit("ავტორმა ამ ფაილიდან ტექსტის კოპირება აკრძალა.")
            return
        text = self.view.selected_text()
        if text is None:
            self.status.emit("ტექსტი ჯერ იტვირთება, სცადეთ ერთ წამში.")
            return
        QGuiApplication.clipboard().setText(text)
        self.status.emit(f"დაკოპირდა {len(text)} სიმბოლო")

    def _open_uri(self, uri):
        url = QUrl(uri)
        if url.scheme().lower() not in ("http", "https", "mailto"):
            QMessageBox.warning(self, "ბმული", "ამ ტიპის ბმული უსაფრთხოებისთვის დაბლოკილია:\n"
                                + uri[:300])
            return
        shown = uri if len(uri) <= 300 else uri[:300] + "…"
        shown = "".join(c if c.isprintable() else " " for c in shown)
        host = url.host() or url.path()[:100]
        if QMessageBox.question(self, "ბმულის გახსნა", "გავხსნა ეს ბმული?\n\n"
                                f"მისამართი: {host}\n\n{shown}") == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(url)
