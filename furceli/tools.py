"""What the drawing tools, form fields, comments and document commands do in a DocView."""
from __future__ import annotations

import os
import time
import uuid

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressDialog,
                               QPushButton, QVBoxLayout, QWidget)

from . import APP_NAME, files
from .client import P_TEXT, TaskError, run_task
from .dialogs import PDF_FILTER, error_text, warn_unprotected
from .forms import (ChoicesDialog, CompareDialog, CompressDialog, FieldDialog, HeaderDialog,
                    LineDialog, OcrDialog, ProtectDialog, ScrubDialog, SignDialog, SignaturePad,
                    TextDialog, VerifyDialog, WatermarkDialog)

TYPE_NAMES = {"Highlight": "მარკერი", "Underline": "ხაზგასმა", "StrikeOut": "გადახაზვა",
              "Squiggly": "ტალღოვანი ხაზი", "Text": "შენიშვნა", "FreeText": "ტექსტი",
              "Ink": "ნახატი", "Square": "ოთხკუთხედი", "Circle": "ელიფსი", "Line": "ხაზი",
              "Stamp": "შტამპი", "Polygon": "მრავალკუთხედი"}


def kind_name(a):
    """What a comment is called; a polygon marked as a cloud is a revision cloud."""
    return "ღრუბელი" if a.get("cloud") else TYPE_NAMES.get(a["type"], a["type"])


def rgb(color: QColor):
    return (round(color.redF(), 4), round(color.greenF(), 4), round(color.blueF(), 4))


class CommentsPanel(QWidget):
    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._go)
        self.list.itemDoubleClicked.connect(lambda *_: self.edit())
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, slot in (("შეცვლა", self.edit), ("წაშლა", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        lay.addLayout(row)

    rev = -1  # the state of the document the list describes

    def show_list(self, items):
        self.rev = self.doc.view.rev
        self.list.clear()
        for pno, a in items:
            head = f"გვ. {pno + 1} · {kind_name(a)}"
            if a["author"]:
                head += f" · {a['author']}"
            it = QListWidgetItem(head + ("\n" + a["text"] if a["text"] else ""))
            it.setData(Qt.ItemDataRole.UserRole, (pno, a))
            self.list.addItem(it)
        self.note.setText(f"სულ {len(items)}" if items else "კომენტარები არ არის")

    def current(self):
        it = self.list.currentItem()
        if it is None or self.doc._busy or self.rev != self.doc.view.rev:
            return None  # the document has changed and the new list is not here yet
        return it.data(Qt.ItemDataRole.UserRole)

    def _go(self, it):
        if self.rev != self.doc.view.rev:
            return
        pno, a = it.data(Qt.ItemDataRole.UserRole)
        self.doc.organize(False)
        self.doc.view.show_rect(pno, a["rect"])

    def edit(self):
        cur = self.current()
        if cur:
            self.doc.edit_comment(*cur)

    def delete(self):
        cur = self.current()
        if cur:
            self.doc.edit(("annot_del", cur[0], cur[1]["index"], cur[1]["type"]))


class ToolsMixin:
    """Mixed into DocView; everything that changes the file goes through self.edit()."""

    author = ""
    set_tool_requested = None  # the window installs a callback that resets its buttons

    # -- plumbing ----------------------------------------------------------
    def _meta(self):
        return {"author": self.author, "date": time.strftime("D:%Y%m%d%H%M%S"),
                "id": uuid.uuid4().hex}

    def _task(self, op, args, text):
        """Run an engine task; None and a message when it fails."""
        try:
            return run_task(self.client, op, args, text, self)
        except TaskError as e:
            QMessageBox.warning(self, APP_NAME, error_text(str(e)))
            return None

    pending_image = None  # a signature waiting for its place

    def set_tool(self, name):
        self._moving = None
        if name != "image":
            self.pending_image = None
        if name != "select":
            self.organize(False)
        for v in self.views:  # the pen is one for every pane
            v.set_tool(name)
        if name == "object":
            self.status.emit("დააწკაპუნეთ სურათზე: გადათრევა — გადატანა, კუთხე — ზომა პროპორციით "
                             "(Shift — თავისუფლად), Delete — წაშლა, მარჯვენა ღილაკი — ჩანაცვლება. "
                             "Ctrl+V — სურათის ჩასმა ბუფერიდან")
        self.changed.emit()

    def load_comments(self):
        if self.loaded and self.sidebar.currentWidget() is self.comments:
            self.client.submit(("comments", self.view.rev), P_TEXT, "all_annots")

    def after_save(self, ops):
        """The saved file may have another password now: signing and warnings depend on it."""
        last = next((op[1] for op in reversed(ops) if op[0] == "protect"), None)
        if not self._base:
            return
        meta = self.info["metadata"]
        if last is None:
            self.info.update(self._base_flags)
        elif last["mode"] == "none":
            self.info.update(dict.fromkeys(self.FLAGS, True))
        else:
            p = last["perms"]
            self.info.update(can_print=bool(p.get("print")), can_copy=bool(p.get("copy")),
                             can_modify=bool(p.get("modify")), can_assemble=bool(p.get("modify")),
                             can_annotate=bool(p.get("annotate") or p.get("modify")),
                             can_fill=bool(p.get("annotate") or p.get("modify")))
        for v in self.views:
            v.can_copy = self.info["can_copy"]
        if last is None:
            self._password, self.info["protected"] = self._base_password, self._base_protected
        elif last["mode"] == "none":
            self._password, self.info["protected"] = "", False
        else:
            self._password, self.info["protected"] = last.get("user") or "", True
        if not self.info["protected"]:
            meta.pop("encryption", None)
        elif last is not None:
            meta["encryption"] = "AES-256"
        if any(op[0] == "scrub" and op[1].get("metadata", True) for op in ops):
            for k in ("title", "author", "subject", "keywords", "creator", "producer"):
                meta.pop(k, None)

    # -- tools -------------------------------------------------------------
    def tool_done(self, tool, pno, data):
        if tool == "cancel":
            self.set_tool("select")
            return
        if tool == "date":
            self._add_date(pno, data["point"])
            return
        if self._moving and tool == "note":
            self._place_comment(pno, data)
            return
        color = rgb(self.view.tool_color)
        width = self.view.tool_width
        if tool in ("highlight", "underline", "strikeout"):
            self.mark_text(tool, data["quads"])
        elif tool in ("rect", "ellipse", "line", "arrow", "ink", "cloud"):
            self.edit(("annot", pno, tool, dict(data, color=color, width=width), self._meta()))
        elif tool == "note":
            text, ok = QInputDialog.getMultiLineText(self, "შენიშვნა", "ტექსტი:")
            if ok and text.strip():
                self.edit(("annot", pno, "note", dict(data, text=text, color=(1, .85, 0)),
                           self._meta()))
        elif tool == "text":
            self._add_text(pno, data["rect"], color)
        elif tool == "stamp":
            text, ok = QInputDialog.getItem(self, "შტამპი", "ტექსტი:", [
                "დამტკიცებულია", "უარყოფილია", "პროექტი", "კონფიდენციალური", "ასლი",
                "გადახდილია", "მიღებულია " + time.strftime("%d.%m.%Y")], 0, True)
            if ok and text.strip():
                self._add_stamp(pno, data["rect"], text.strip(), color)
        elif tool == "image":
            self._add_image(pno, data["rect"])
        elif tool == "redact":
            self.view.marks.setdefault(pno, []).append(data["rect"])
            self.update_views()
            self.changed.emit()
        elif tool == "field":
            self._add_field(pno, data["rect"])
        elif tool == "edit":
            self.edit_line(pno, data["point"])
        elif tool == "signbox":
            self.set_tool("select")
            self.sign(pno, data["rect"])

    def mark_text(self, kind, quads):
        color = {"highlight": (1, .92, .2), "underline": (0, .6, 0),
                 "strikeout": (.85, 0, 0)}[kind]
        # One edit at a time: a selection over several pages becomes a small queue.
        self._queue = [("annot", p, kind, {"quads": q, "color": color}, self._meta())
                       for p, q in sorted(quads.items())]
        self.view.clear_selection()
        self._run_queue()

    def _run_queue(self):
        queue = getattr(self, "_queue", [])
        if not queue:
            return
        if self._busy:
            QTimer.singleShot(30, self._run_queue)
            return
        if self.edit(queue.pop(0)):
            QTimer.singleShot(30, self._run_queue)
        else:
            queue.clear()

    def _selected_quads(self):
        """None while some page of the selection is still loading: it would be left out."""
        if self.view.has_selection() and self.view.selected_text() is None:
            self.status.emit("ტექსტი ჯერ იტვირთება, სცადეთ ერთ წამში.")
            return None
        return self.view.selection_quads()

    def mark_selection(self, kind):
        quads = self._selected_quads()
        if quads is None:
            return
        if quads:
            self.mark_text(kind, quads)
        else:
            self.set_tool(kind)

    def _add_text(self, pno, rect, color):
        dlg = TextDialog(self, color=color)
        if not dlg.exec() or not dlg.text().strip():
            return
        if dlg.as_comment():
            self.edit(("annot", pno, "text", {"rect": self._grow(pno, rect, dlg.text(), dlg.size()),
                                              "text": dlg.text(), "size": dlg.size(),
                                              "color": dlg.color()}, self._meta()))
        else:
            self.edit(("text", pno, self._grow(pno, rect, dlg.text(), dlg.size()), dlg.text(),
                       {"size": dlg.size(), "color": dlg.color(), "bold": dlg.bold()}))

    def _grow(self, pno, rect, text, size):
        """Make the box tall enough for the text at its width."""
        x0, y0, x1, y1 = rect
        w, h = self.sizes[pno]
        longest = max((len(t) for t in text.split("\n")), default=1)
        x1 = min(max(x1, x0 + min(longest * size * .62 + 10, w - x0 - 4)), w - 2)
        per = max((x1 - x0) / (size * .62), 1)
        rows = sum(int(len(t) / per) + 1 for t in text.split("\n"))
        y1 = min(max(y1, y0 + rows * size * 1.5 + 6), h - 1)
        return (x0, y0, x1, y1)

    def _add_stamp(self, pno, rect, text, color):
        x0, y0, x1, y1 = rect
        size = min((y1 - y0) * .5, (x1 - x0) / max(len(text) * .72, 1))
        size = max(min(size, 60), 9)
        x1 = max(x1, min(x0 + len(text) * size * .75 + 10, self.sizes[pno][0]))
        y1 = max(y1, min(y0 + size * 1.9, self.sizes[pno][1]))
        self.edit(("annot", pno, "stamp", {"rect": (x0, y0, x1, y1), "text": text, "size": size,
                                           "color": color}, self._meta()))

    def _keep_file(self, path):
        """A private copy: undo must work even if the original picture is gone."""
        return self.keep(path, "asset" + os.path.splitext(path)[1].lower())

    def _add_image(self, pno, rect, path=None):
        if path is None:
            path = self.pending_image
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "სურათი", "",
                                                  "სურათები (*.png *.jpg *.jpeg *.bmp *.gif *.tif)")
        if not path:
            return
        try:
            copy = self._keep_file(path)
        except OSError:
            QMessageBox.warning(self, "სურათი", error_text("no_access"))
            return
        signature = path == self.pending_image
        self.set_tool("object")  # the new picture comes chosen: move, size, other pages
        self._pick_object, self._ask_copy = (pno, tuple(rect)), not signature
        if self.edit(("image", pno, rect, copy)):
            self.pending_image = None
            return True
        self._pick_object, self._ask_copy = None, False
        return False

    # -- pictures already on the page (HANDOFF.md, item 7) -----------------
    def _load_objects(self, v, pno):
        v.objects[pno] = self._quiet("objects", pno) or []

    @staticmethod
    def _kind(o):
        return "date" if o.get("kind") == "date" else "image"

    _pick_object = None   # (page, rectangle): chosen again once the edit is shown
    _ask_copy = False     # a picture just put in: ask about the other pages

    def _object_moved(self, pno, o, rect):
        self._pick_object = (pno, tuple(rect))
        self.edit((self._kind(o) + "_move", pno, o["xref"], tuple(o["rect"]), tuple(rect)))

    def _object_turned(self, pno, o, degrees):
        if self._kind(o) == "date":
            return
        x0, y0, x1, y1 = o["rect"]
        cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
        self._pick_object = (pno, (cx - h / 2, cy - w / 2, cx + h / 2, cy + w / 2))
        self.edit(("image_turn", pno, o["xref"], tuple(o["rect"]), degrees))

    def _object_copy(self, pno, o):
        from .objects_ui import CopyDialog
        w, h = self.sizes[pno]
        x0, y0, x1, y1 = o["rect"]
        near = (0 if (x0 + x1) / 2 < w / 2 else 1, 0 if (y0 + y1) / 2 < h / 2 else 1)
        what = "თარიღი" if self._kind(o) == "date" else "სურათი"
        dlg = CopyDialog(pno, len(self.sizes), near, what, parent=self)
        if not dlg.exec():
            return
        pages = dlg.pages(quiet=True)
        if pages:
            self._pick_object = (pno, tuple(o["rect"]))
            self.edit((self._kind(o) + "_copy", pno, o["xref"], tuple(o["rect"]), pages,
                       dlg.anchor()))

    def _add_date(self, pno, point):
        """The date tool: a click on a page, then which date, how, and on which pages."""
        from .objects_ui import DateDialog
        dlg = DateDialog(pno, len(self.sizes), parent=self)
        if not dlg.exec():
            return
        x, y = point
        self.set_tool("object")  # the new date comes chosen: move it, put it on other pages
        self._pick_object = (pno, (x - 2, y - 2, x + 2000, y + 2000))
        if not self.edit(("date", pno, (x, y), dlg.text(), dlg.style(), dlg.pages(quiet=True))):
            self._pick_object = None

    def _after_objects(self, kind):
        """After an edit: the picture worked on is chosen again (its bar stays at hand)."""
        want, self._pick_object = self._pick_object, None
        ask, self._ask_copy = self._ask_copy, False
        if not want or kind != "edit" or self.view.tool != "object":
            return
        pno, rect = want
        self._load_objects(self.view, pno)
        inside = [o for o in self.view.objects.get(pno, ())
                  if o["movable"] and all(a >= b - 1.5 for a, b in zip(o["rect"][:2], rect[:2]))
                  and all(a <= b + 1.5 for a, b in zip(o["rect"][2:], rect[2:]))]
        if inside:  # the one drawn last: a new picture is drawn over the others
            self.view.select_object(pno, inside[-1])
            if ask and len(self.sizes) > 1:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._object_copy(pno, inside[-1]))

    def image_rect(self, pno, center, path):
        """Where a picture file goes when put at a point: its own size (96 dpi), fitted in
        the page; None when it is no picture."""
        from PySide6.QtGui import QImageReader
        size = QImageReader(path).size()
        if not size.isValid() or size.isEmpty():
            return None
        pw, ph = self.sizes[pno]
        w, h = size.width() * .75, size.height() * .75
        k = min(1.0, pw * .6 / w, ph * .6 / h)
        w, h = w * k, h * k
        x = min(max(center[0] - w / 2, 0), pw - w)
        y = min(max(center[1] - h / 2, 0), ph - h)
        return (x, y, x + w, y + h)

    def place_image(self, pno, center, path):
        """A picture file put on a page at a point (dropped, pasted)."""
        rect = self.image_rect(pno, center, path)
        if rect is None:
            QMessageBox.warning(self, "სურათი", error_text("bad_image"))
            return False
        return self._add_image(pno, rect, path)

    def _object_deleted(self, pno, o):
        self.edit((self._kind(o) + "_del", pno, o["xref"], tuple(o["rect"])))

    def _object_swap(self, pno, o):
        path, _ = QFileDialog.getOpenFileName(self, "სურათის ჩანაცვლება", "",
                                              "სურათები (*.png *.jpg *.jpeg *.bmp *.gif *.tif)")
        if not path:
            return
        try:
            copy = self._keep_file(path)
        except OSError:
            QMessageBox.warning(self, "სურათი", error_text("no_access"))
            return
        self.edit(("image_swap", pno, o["xref"], tuple(o["rect"]), copy))

    def place_signature(self):
        pad = SignaturePad(self, self._session())
        if pad.exec() and pad.path:
            self.pending_image = pad.path
            self.set_tool("image")
            self.status.emit("დახატეთ ჩარჩო იქ, სადაც ხელმოწერა უნდა ჩაისვას")

    # -- comments ----------------------------------------------------------
    def edit_comment(self, pno, a):
        text, ok = QInputDialog.getMultiLineText(
            self, kind_name(a), "ტექსტი:", a["text"])
        if ok and text != a["text"]:
            self.edit(("annot_text", pno, a["index"], a["type"], text, self._meta()))

    def view_menu(self, pno, q, pos):
        menu = QMenu(self)
        menu.aboutToHide.connect(menu.deleteLater)
        if self.view.tool == "object" and self.allowed("image_move"):
            if pno not in self.view.objects:
                self._load_objects(self.view, pno)
            o = self.view.object_at(pno, q)
            if o:
                menu.addAction("სურათის ჩანაცვლება…", lambda: self._object_swap(pno, o))
                menu.addAction("სურათის წაშლა", lambda: self._object_deleted(pno, o))
                menu.addSeparator()
        if self.view.has_selection():
            menu.addAction("კოპირება", self.copy)
            menu.addAction("მარკერი", lambda: self.mark_selection("highlight"))
            menu.addAction("ხაზგასმა", lambda: self.mark_selection("underline"))
            menu.addAction("გადახაზვა", lambda: self.mark_selection("strikeout"))
            menu.addAction("დასაფარად მონიშვნა", self.redact_selection)
            menu.addSeparator()
        for a in reversed(self._quiet("annots", pno) or []):
            x0, y0, x1, y1 = a["rect"]
            if x0 - 2 <= q.x() <= x1 + 2 and y0 - 2 <= q.y() <= y1 + 2:
                name = kind_name(a)
                menu.addAction(f"{name}: ტექსტის შეცვლა…", lambda a=a: self.edit_comment(pno, a))
                menu.addAction(f"{name}: წაშლა", lambda a=a: self.edit(
                    ("annot_del", pno, a["index"], a["type"])))
                if a["type"] in ("FreeText", "Stamp", "Text", "Square", "Circle"):
                    menu.addAction(f"{name}: აქ გადატანა…", lambda a=a: self._move_comment(pno, a))
                menu.addSeparator()
                break
        w = self.view.widget_at(pno, q)
        if w and self.allowed("field_del"):
            menu.addAction(f"ველის წაშლა ({w['name']})",
                           lambda: self.edit(("field_del", pno, w["index"])))
        for rect in self.view.marks.get(pno, []):
            if rect[0] <= q.x() <= rect[2] and rect[1] <= q.y() <= rect[3]:
                menu.addAction("ამ მონიშვნის მოხსნა", lambda r=rect: self._unmark(pno, r))
        if not menu.isEmpty():
            menu.exec(pos)

    def _quiet(self, op, *args):
        try:
            return run_task(self.client, op, args, "", self)
        except TaskError:
            return None

    _moving = None  # (page, comment) waiting for its new place

    def _move_comment(self, pno, a):
        self.set_tool("note")
        self._moving = (pno, a)
        self.status.emit("დააწკაპუნეთ ახალ ადგილას. გაუქმება: Esc")

    def _place_comment(self, page, data):
        (pno, a), self._moving = self._moving, None
        self.set_tool("select")
        if page == pno:
            x, y = data["point"]
            self.edit(("annot_move", pno, a["index"], a["type"],
                       x - a["rect"][0], y - a["rect"][1]))

    # -- forms -------------------------------------------------------------
    def field_clicked(self, pno, w):
        if w["readonly"] or w["kind"] in ("other",):
            return
        if not self.allowed("fill"):
            self.status.emit("ამ ფაილში ფორმის შევსება აკრძალულია.")
            return
        if w["kind"] == "sign":
            if w.get("signed"):
                self.verify()  # never over a signature
            else:
                self.sign(pno, w["rect"], w["name"])
            return
        name = w["label"] or w["name"]
        if w["kind"] in ("check", "radio"):
            if w["kind"] == "radio" and w["value"]:
                return
            self.edit(("fill", pno, w["index"], not w["value"]))
            return
        if w.get("multi"):
            shown, exports = w["choices"], w.get("exports") or w["choices"]
            now = [i for i, (e, t) in enumerate(zip(exports, shown))
                   if e in w["value"] or t in w["value"]]  # some programs keep the shown text
            dlg = ChoicesDialog(name, shown, [shown[i] for i in now], self)
            if dlg.exec():
                value = [exports[shown.index(t)] for t in dlg.chosen()]
                if value != [exports[i] for i in now] or len(w["value"]) != len(now):
                    self.edit(("fill", pno, w["index"], value))
            return
        if w["kind"] in ("combo", "list"):
            shown, exports = w["choices"], w.get("exports") or w["choices"]
            cur = next((names.index(w["value"]) for names in (exports, shown)
                        if w["value"] in names), 0)
            value, ok = QInputDialog.getItem(self, name, "აირჩიეთ:", shown, cur,
                                             bool(w.get("editable")))
            if ok and value in shown:
                value = exports[shown.index(value)]
        elif w["multiline"]:
            value, ok = QInputDialog.getMultiLineText(self, name, "ტექსტი:", w["value"] or "")
        elif w.get("password"):
            value, ok = QInputDialog.getText(self, name, "ტექსტი:", QLineEdit.EchoMode.Password)
            ok = ok and bool(value)  # an empty answer keeps what is there
        else:
            value, ok = QInputDialog.getText(self, name, "ტექსტი:", text=w["value"] or "")
        if ok and value != (w["value"] or ""):
            if w["max"] and len(value) > w["max"]:
                QMessageBox.information(self, name, f"ამ ველში მაქსიმუმ {w['max']} სიმბოლოა.")
                return
            self.edit(("fill", pno, w["index"], value))

    def _add_field(self, pno, rect):
        dlg = FieldDialog(self)
        if dlg.exec():
            self.edit(("field", pno, rect, *dlg.result_op()))

    def flatten(self):
        if QMessageBox.question(
                self, "გაერთიანება გვერდთან", "კომენტარები და ფორმის ველები გვერდის ნაწილი გახდება და მათი "
                "შეცვლა აღარ იქნება შესაძლებელი. გავაგრძელო?") == QMessageBox.StandardButton.Yes:
            self.edit(("flatten",))

    # -- content -----------------------------------------------------------
    def edit_line(self, pno, point):
        lines = self._task("lines", (pno,), "იკითხება…")
        if lines is None:
            return
        x, y = point
        best = None
        for ln in lines:
            x0, y0, x1, y1 = ln["rect"]
            d = max(x0 - x, 0, x - x1) + 2 * max(y0 - y, 0, y - y1)
            if d < 6 and (best is None or d < best[0]):
                best = (d, ln)
        if best is None:
            self.notify("info", "აქ ტექსტი არ არის. დასკანერებულ გვერდს ჯერ ტექსტის ამოცნობა სჭირდება (OCR).",
                        "ამოცნობა…", self.ocr)
            return
        ln = best[1]
        dlg = LineDialog(ln, self)
        if dlg.exec() and (dlg.text() != ln["text"] or dlg.style_changed()):
            self.edit(("replace", pno, ln["index"], ln["text"], dlg.text(), dlg.style_()))

    def _pages_dialog(self, dlg):
        """Pages chosen in a dialog that has a .pages_text()."""
        text = dlg.pages_text()
        n = len(self.sizes)
        # every page once and in order: a second stamp would only cover the first
        return sorted(set(files.parse_group(text, n))) if text else list(range(n))

    def watermark(self):
        dlg = WatermarkDialog(len(self.sizes), self)
        if dlg.exec():
            self.edit(("watermark", self._pages_dialog(dlg), dlg.text(), dlg.options()))

    def header(self, bates=False):
        dlg = HeaderDialog(len(self.sizes), bates, self)
        if dlg.exec():
            self.edit(("header", self._pages_dialog(dlg), dlg.options()))

    def background(self, color):
        self.edit(("background", list(range(len(self.sizes))), rgb(color)))

    # -- redaction ---------------------------------------------------------
    def redact_selection(self):
        found = self._selected_quads()
        if found is None:
            return
        for pno, quads in found.items():
            for q in quads:
                xs, ys = [p[0] for p in q], [p[1] for p in q]
                self._mark(pno, (min(xs) - .5, min(ys), max(xs) + .5, max(ys)))
        self.view.clear_selection()
        self.changed.emit()

    def redact_search(self):
        if not self._found:
            self.open_search()
            self.status.emit("ჯერ იპოვეთ სიტყვა, მერე ისევ აირჩიეთ ეს ბრძანება")
            return
        if self._left > 0:
            self.status.emit("ძიება ჯერ მიდის, მოიცადეთ")
            return
        for pno, hits in self.view.hits.items():
            for rect, _ in hits:
                self._mark(pno, (rect[0] - .5, rect[1], rect[2] + .5, rect[3]))
        self.update_views()
        self.changed.emit()

    def _mark(self, pno, rect):
        there = self.view.marks.setdefault(pno, [])
        if rect not in there:
            there.append(rect)

    def _unmark(self, pno, rect):
        self.view.marks[pno].remove(rect)
        if not self.view.marks[pno]:
            del self.view.marks[pno]
        self.update_views()
        self.changed.emit()

    def clear_marks(self):
        self.view.marks = {}
        self.update_views()
        self.changed.emit()

    def apply_redactions(self):
        marks = {p: [tuple(r) for r in rs] for p, rs in self.view.marks.items() if rs}
        if not marks:
            self.status.emit("ჯერ მონიშნეთ დასაფარი ადგილები")
            return
        n = sum(len(r) for r in marks.values())
        if QMessageBox.question(
                self, "დაფარვა", f"{n} ადგილი სამუდამოდ წაიშლება: ტექსტი, სურათის ნაწილი, კომენტარები და "
                "ველები. შენახვამდე Ctrl+Z მუშაობს.\n\nყურადღება: თუ ორიგინალს გადააწერთ, სარეზერვო ასლში "
                "ტექსტი მაინც დარჩება. გავაგრძელო?") != QMessageBox.StandardButton.Yes:
            return
        # The engine looks where else the removed words appear: this one and those under the marks.
        word = " ".join(self.query.text().split())
        self.edit(("redact", marks, {"fill": (0, 0, 0), "word": word}))

    def scrub(self):
        dlg = ScrubDialog(self)
        if dlg.exec():
            self.edit(("scrub", dlg.options()))

    # -- security, size ----------------------------------------------------
    def protect(self):
        dlg = ProtectDialog(self.info["protected"], self)
        if dlg.exec() and self.edit(("protect", dlg.options())):
            self._after = "პაროლის ცვლილება შენახვისას ამოქმედდება."

    def compress(self):
        dlg = CompressDialog(self.info["bytes"], self)
        if dlg.exec() and self.edit(("compress", dlg.options())):
            self._after = "ზომა შენახვის შემდეგ შემცირდება."

    # -- OCR ---------------------------------------------------------------
    def ocr(self):
        if self._busy:
            self.status.emit("მოიცადეთ, ცვლილება ჯერ სრულდება.")
            return
        if not self.allowed("textlayer"):
            self.status.emit("ავტორმა ამ ფაილის ცვლილება აკრძალა.")
            return
        dlg = OcrDialog(len(self.sizes), self.view.page, self)
        if not dlg.exec():
            return
        pages = dlg.pages()
        bar = QProgressDialog("ტექსტის ამოცნობა…", "გაუქმება", 0, len(pages), self)
        bar.setWindowModality(Qt.WindowModality.WindowModal)
        bar.setMinimumDuration(0)
        layer, skipped = {}, 0
        for k, p in enumerate(pages):
            bar.setValue(k)
            bar.setLabelText(f"ტექსტის ამოცნობა: გვერდი {p + 1}")
            code = "cancelled" if bar.wasCanceled() else ""
            try:
                words = None if code else run_task(
                    self.client, "ocr_words", (p, dlg.languages(), dlg.force()), "", None, allow=bar)
            except TaskError as e:
                code = str(e)
            if code:
                bar.close()
                bar.deleteLater()
                if code == "cancelled":
                    self.status.emit("ტექსტის ამოცნობა გაუქმდა. არაფერი შეცვლილა.")
                else:
                    QMessageBox.warning(self, "OCR", error_text(code))
                return
            if words is None:
                skipped += 1
            elif words:
                layer[p] = words
        bar.close()
        bar.deleteLater()
        if layer and self.edit(("textlayer", layer)):
            n = sum(len(w) for w in layer.values())
            self._after = (f"ამოცნობილია {n} სიტყვა {len(layer)} გვერდზე"
                             + (f"; {skipped} გვერდს ტექსტი უკვე ჰქონდა" if skipped else ""))
        elif not layer:
            self.status.emit("ახალი ტექსტი ვერ ვიპოვე"
                             + (" (გვერდებს ტექსტი უკვე აქვს)" if skipped else ""))

    # -- conversion out ----------------------------------------------------
    EXPORTS = {"docx": "Word (*.docx)", "xlsx": "Excel (*.xlsx)", "pptx": "PowerPoint (*.pptx)",
               "txt": "ტექსტი (*.txt)", "html": "HTML (*.html)"}

    def _may_copy(self):
        if not self.info["can_copy"]:
            QMessageBox.information(self, "ექსპორტი", "ავტორმა ამ ფაილიდან ასლის აღება აკრძალა.")
            return False
        if self._busy or not self.marks_ok():
            return False
        return not self.info["protected"] or warn_unprotected(self)

    def export_as(self, kind):
        if not self._may_copy():
            return None
        start = files.unique_path(os.path.splitext(self.path)[0] + "." + kind)
        out, _ = QFileDialog.getSaveFileName(self, "ექსპორტი", start, self.EXPORTS[kind])
        if not out:
            return None
        if not out.lower().endswith("." + kind):
            out = files.unique_path(out + "." + kind)
        stem, ext = os.path.splitext(files.outbox(out))
        tmp = stem + "." + kind  # the libraries choose the format by the extension
        try:
            note = run_task(self.client, "export_as", (kind, tmp, list(range(len(self.sizes)))),
                            "გარდაიქმნება…", self)
            files.put_in_place(tmp, out)
        except (TaskError, OSError) as e:
            QMessageBox.warning(self, "ექსპორტი", error_text(
                str(e) if isinstance(e, TaskError) else "no_access"))
            return None
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.status.emit(f"შეიქმნა: {out}" + (
            "  (რთული გაფორმება ან ცხრილები ვერ გადავიდა, მხოლოდ ტექსტი)" if note == "simple" else ""))
        return out

    def export_images(self, fmt, dpi):
        if not self._may_copy():
            return []
        folder = QFileDialog.getExistingDirectory(self, "სად შევინახო სურათები",
                                                  os.path.dirname(self.path))
        if not folder:
            return []
        stem = os.path.splitext(os.path.basename(self.path))[0]
        width = len(str(len(self.sizes)))
        targets = [(p, files.unique_path(os.path.join(folder, f"{stem} {p + 1:0{width}d}.{fmt}")))
                   for p in range(len(self.sizes))]
        work = [(p, files.outbox(t)) for p, t in targets]
        try:
            if not self._task("to_images", (work, dpi), "სურათები იქმნება…"):
                return []
            files.deliver_all([(w, t) for (_, w), (_, t) in zip(work, targets)])
        except OSError:
            QMessageBox.warning(self, APP_NAME, error_text("no_access"))
            return []
        finally:
            for _, w in work:
                files.discard(w)
        self.status.emit(f"შეიქმნა {len(targets)} სურათი")
        return [t for _, t in targets]

    def export_pdfa(self):
        if not self._may_copy():
            return
        start = files.unique_path(os.path.splitext(self.path)[0] + " (PDF-A).pdf")
        out, _ = QFileDialog.getSaveFileName(self, "PDF/A", start, PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out = files.unique_path(out + ".pdf")
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(self.path):
            QMessageBox.warning(self, "PDF/A", "აირჩიეთ სხვა სახელი: ეს თვითონ ეს ფაილია.")
            return
        tmp = files.outbox(out)
        try:
            problems = run_task(self.client, "pdfa", (tmp,), "იქმნება…", self)
            if problems:
                QMessageBox.warning(self, "PDF/A", "ამ ფაილისგან PDF/A ვერ შეიქმნება:\n\n• "
                                    + "\n• ".join(problems[:12]))
                return
            files.put_in_place(tmp, out)
        except (TaskError, OSError) as e:
            QMessageBox.warning(self, "PDF/A", error_text(
                str(e) if isinstance(e, TaskError) else "no_access"))
            return
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        QMessageBox.information(
            self, "PDF/A", "ფაილი PDF/A-2b-ს წესებით მომზადდა, მაგრამ ოფიციალური შემმოწმებლით არ "
            "შემოწმებულა. თუ არქივი მას ითხოვს, შეამოწმეთ veraPDF-ით.")

    # -- compare -----------------------------------------------------------
    def compare_with(self):
        path, _ = QFileDialog.getOpenFileName(self, "რომელ ძველ ვერსიას შევადარო",
                                              os.path.dirname(self.path), PDF_FILTER)
        if not path:
            return
        from .dialogs import inspect_file
        got = inspect_file(self.client, path, self)
        if got is None:
            return
        result = self._task("compare", (path, got[1]), "დოკუმენტები დარდება…")
        if result is None:
            return
        self.view.diff = {}
        for c in result["changes"]:
            for p, rect in c["new_at"]:
                self.view.diff.setdefault(p, []).append(rect)
        self.update_views()
        dlg = CompareDialog(os.path.basename(path), os.path.basename(self.path), result, self)
        dlg.chosen.connect(self._show_change)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._compared = dlg
        dlg.finished.connect(lambda *_: self._clear_diff(dlg))
        dlg.show()

    _compared = None  # the newest comparison owns the highlights

    def _show_change(self, pno, rect):
        self.organize(False)
        if pno >= len(self.sizes):
            return  # the page was removed meanwhile
        if rect is not None:
            self.view.show_rect(pno, rect)
        else:
            self.view.go_to(pno)

    def _clear_diff(self, dlg):
        if dlg is not self._compared:
            return
        self._compared = None
        self.view.diff = {}
        self.update_views()

    # -- certificates ------------------------------------------------------
    def sign(self, pno=None, rect=None, field=""):
        if self.dirty or self._busy:
            QMessageBox.information(self, "ხელმოწერა", "ჯერ შეინახეთ ცვლილებები: ხელმოწერა შენახულ "
                                    "ფაილს ეხება.")
            return
        dlg = SignDialog(self.client, self)
        if not dlg.exec():
            return
        start = files.unique_path(os.path.splitext(self.path)[0] + " (ხელმოწერილი).pdf")
        out, _ = QFileDialog.getSaveFileName(self, "ხელმოწერილი ფაილი", start, PDF_FILTER)
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out = files.unique_path(out + ".pdf")
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(self.path):
            QMessageBox.warning(self, "ხელმოწერა", "აირჩიეთ სხვა სახელი: ორიგინალი უცვლელი უნდა დარჩეს.")
            return
        o = dlg.options()
        if rect is not None:
            box = self._task("pdf_box", (pno, rect), "")
            if box is None:
                return
            o.update(page=pno, box=box, field=field)
        tmp = files.outbox(out)
        try:
            run_task(self.client, "sign", (self.path, tmp, self._password, dlg.pfx(),
                                           dlg.password(), o), "ხელი ეწერება…", self)
            files.put_in_place(tmp, out)
        except (TaskError, OSError) as e:
            QMessageBox.warning(self, "ხელმოწერა", error_text(
                str(e) if isinstance(e, TaskError) else "no_access"))
            return
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.openRequested.emit(out)

    def verify(self):
        rows = self._task("verify", (self.path, self._password), "ხელმოწერები მოწმდება…")
        if rows is not None:
            VerifyDialog(rows, self.dirty, self).exec()

    # -- measuring helper for tests and tools ------------------------------
    def page_point(self, pno, x, y):
        return self.view.page_to_doc(pno, QPointF(x, y))
