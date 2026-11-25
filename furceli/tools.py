"""What the drawing tools, form fields, comments and document commands do in a DocView."""
from __future__ import annotations

import os
import time
import uuid

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QProgressDialog, QPushButton,
                               QVBoxLayout, QWidget)

from . import APP_NAME, files
from .client import P_TEXT, TaskError, run_task
from .dialogs import PDF_FILTER, error_text
from .forms import (CompareDialog, CompressDialog, FieldDialog, HeaderDialog, LineDialog,
                    OcrDialog, ProtectDialog, ScrubDialog, SignDialog, SignaturePad, TextDialog,
                    VerifyDialog, WatermarkDialog)

TYPE_NAMES = {"Highlight": "მარკერი", "Underline": "ხაზგასმა", "StrikeOut": "გადახაზვა",
              "Squiggly": "ტალღოვანი ხაზი", "Text": "შენიშვნა", "FreeText": "ტექსტი",
              "Ink": "ნახატი", "Square": "ოთხკუთხედი", "Circle": "ელიფსი", "Line": "ხაზი",
              "Stamp": "შტამპი"}


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

    def show_list(self, items):
        self.list.clear()
        for pno, a in items:
            head = f"გვ. {pno + 1} · {TYPE_NAMES.get(a['type'], a['type'])}"
            if a["author"]:
                head += f" · {a['author']}"
            it = QListWidgetItem(head + ("\n" + a["text"] if a["text"] else ""))
            it.setData(Qt.ItemDataRole.UserRole, (pno, a))
            self.list.addItem(it)
        self.note.setText(f"სულ {len(items)}" if items else "კომენტარები არ არის")

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _go(self, it):
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

    def set_tool(self, name):
        self._moving = None
        if name != "select":
            self.organize(False)
        self.view.set_tool(name)
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
        self.view.can_copy = self.info["can_copy"]
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
        if self._moving and tool == "note":
            self._place_comment(pno, data)
            return
        color = rgb(self.view.tool_color)
        width = self.view.tool_width
        if tool in ("highlight", "underline", "strikeout"):
            self.mark_text(tool, data["quads"])
        elif tool in ("rect", "ellipse", "line", "arrow", "ink"):
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
            self.view.viewport().update()
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

    def mark_selection(self, kind):
        quads = self.view.selection_quads()
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
            path = getattr(self, "pending_image", None)
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
        if self.edit(("image", pno, rect, copy)):
            self.pending_image = None
            self.set_tool("select")

    def place_signature(self):
        pad = SignaturePad(self, self._session())
        if pad.exec() and pad.path:
            self.pending_image = pad.path
            self.set_tool("image")
            self.status.emit("დახატეთ ჩარჩო იქ, სადაც ხელმოწერა უნდა ჩაისვას")

    # -- comments ----------------------------------------------------------
    def edit_comment(self, pno, a):
        text, ok = QInputDialog.getMultiLineText(
            self, TYPE_NAMES.get(a["type"], a["type"]), "ტექსტი:", a["text"])
        if ok and text != a["text"]:
            self.edit(("annot_text", pno, a["index"], a["type"], text, self._meta()))

    def view_menu(self, pno, q, pos):
        menu = QMenu(self)
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
                name = TYPE_NAMES.get(a["type"], a["type"])
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
            self.sign(pno, w["rect"])
            return
        name = w["label"] or w["name"]
        if w["kind"] in ("check", "radio"):
            if w["kind"] == "radio" and w["value"]:
                return
            self.edit(("fill", pno, w["index"], not w["value"]))
            return
        if w["kind"] in ("combo", "list"):
            cur = w["choices"].index(w["value"]) if w["value"] in w["choices"] else 0
            value, ok = QInputDialog.getItem(self, name, "აირჩიეთ:", w["choices"], cur, False)
        elif w["multiline"]:
            value, ok = QInputDialog.getMultiLineText(self, name, "ტექსტი:", w["value"] or "")
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
        if not lines:
            return
        x, y = point
        best = None
        for ln in lines:
            x0, y0, x1, y1 = ln["rect"]
            d = max(x0 - x, 0, x - x1) + 2 * max(y0 - y, 0, y - y1)
            if d < 6 and (best is None or d < best[0]):
                best = (d, ln)
        if best is None:
            self.status.emit("აქ ტექსტი არ არის. დასკანერებულ გვერდს ჯერ OCR სჭირდება.")
            return
        ln = best[1]
        dlg = LineDialog(ln, self)
        if dlg.exec() and (dlg.text() != ln["text"] or dlg.style_changed()):
            self.edit(("replace", pno, ln["index"], ln["text"], dlg.text(), dlg.style_()))

    def _pages_dialog(self, dlg):
        """Pages chosen in a dialog that has a .pages_text()."""
        text = dlg.pages_text()
        n = len(self.sizes)
        return files.parse_group(text, n) if text else list(range(n))

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
        for pno, quads in self.view.selection_quads().items():
            for q in quads:
                xs, ys = [p[0] for p in q], [p[1] for p in q]
                self.view.marks.setdefault(pno, []).append(
                    (min(xs) - .5, min(ys), max(xs) + .5, max(ys)))
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
                self.view.marks.setdefault(pno, []).append(
                    (rect[0] - .5, rect[1], rect[2] + .5, rect[3]))
        self.view.viewport().update()
        self.changed.emit()

    def _unmark(self, pno, rect):
        self.view.marks[pno].remove(rect)
        if not self.view.marks[pno]:
            del self.view.marks[pno]
        self.view.viewport().update()
        self.changed.emit()

    def clear_marks(self):
        self.view.marks = {}
        self.view.viewport().update()
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
        word = " ".join(self.query.text().split())
        if self.edit(("redact", marks, {"fill": (0, 0, 0)})):
            self._check_word = word
            QTimer.singleShot(50, self._after_redact)

    def _after_redact(self):
        if self._busy:
            QTimer.singleShot(50, self._after_redact)
            return
        word, self._check_word = self._check_word, ""
        if len(word) < 2 or not self.ops or self.ops[-1][0] != "redact":
            return
        left = self._quiet("mentions", word)
        if left:
            QMessageBox.information(
                self, "დაფარვა", f"„{word}“ კიდევ გვხვდება გვერდების გარეთ:\n\n• "
                + "\n• ".join(left[:12])
                + "\n\nმათ წასაშლელად გამოიყენეთ „ფარული მონაცემების წაშლა“.")

    def scrub(self):
        dlg = ScrubDialog(self)
        if dlg.exec():
            self.edit(("scrub", dlg.options()))

    # -- security, size ----------------------------------------------------
    def protect(self):
        dlg = ProtectDialog(self.info["protected"], self)
        if dlg.exec() and self.edit(("protect", dlg.options())):
            self.status.emit("პაროლის ცვლილება შენახვისას ამოქმედდება.")

    def compress(self):
        dlg = CompressDialog(self.info["bytes"], self)
        if dlg.exec():
            self.edit(("compress", dlg.options()))
            self.status.emit("ზომა შენახვის შემდეგ შემცირდება.")

    # -- OCR ---------------------------------------------------------------
    def ocr(self):
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
            if bar.wasCanceled():
                break
            try:
                words = run_task(self.client, "ocr_words", (p, dlg.languages(), dlg.force()), "",
                                 None, allow=bar)
            except TaskError as e:
                bar.close()
                QMessageBox.warning(self, "OCR", error_text(str(e)))
                return
            if words is None:
                skipped += 1
            elif words:
                layer[p] = words
        bar.close()
        if layer:
            self.edit(("textlayer", layer))
            n = sum(len(w) for w in layer.values())
            self.status.emit(f"ამოცნობილია {n} სიტყვა {len(layer)} გვერდზე"
                             + (f"; {skipped} გვერდს ტექსტი უკვე ჰქონდა" if skipped else ""))
        else:
            self.status.emit("ახალი ტექსტი ვერ ვიპოვე"
                             + (" (გვერდებს ტექსტი უკვე აქვს)" if skipped else ""))

    # -- conversion out ----------------------------------------------------
    EXPORTS = {"docx": "Word (*.docx)", "xlsx": "Excel (*.xlsx)", "pptx": "PowerPoint (*.pptx)",
               "txt": "ტექსტი (*.txt)", "html": "HTML (*.html)"}

    def _may_copy(self):
        if not self.info["can_copy"]:
            QMessageBox.information(self, "ექსპორტი", "ავტორმა ამ ფაილიდან ასლის აღება აკრძალა.")
            return False
        return not self._busy

    def export_as(self, kind):
        if not self._may_copy():
            return None
        start = files.unique_path(os.path.splitext(self.path)[0] + "." + kind)
        out, _ = QFileDialog.getSaveFileName(self, "ექსპორტი", start, self.EXPORTS[kind])
        if not out:
            return None
        if not out.lower().endswith("." + kind):
            out = files.unique_path(out + "." + kind)
        stem, ext = os.path.splitext(files.temp_name(out))
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
        if not self._task("to_images", (targets, dpi), "სურათები იქმნება…"):
            return []
        self.status.emit(f"შეიქმნა {len(targets)} სურათი")
        return [t for _, t in targets]

    def export_pdfa(self):
        if not self._may_copy():
            return
        start = files.unique_path(os.path.splitext(self.path)[0] + " (PDF-A).pdf")
        out, _ = QFileDialog.getSaveFileName(self, "PDF/A", start, PDF_FILTER)
        if not out or os.path.normcase(os.path.abspath(out)) == os.path.normcase(self.path):
            return
        tmp = files.temp_name(out)
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
        self.view.viewport().update()
        dlg = CompareDialog(os.path.basename(path), os.path.basename(self.path), result, self)
        dlg.chosen.connect(self._show_change)
        dlg.finished.connect(lambda *_: self._clear_diff())
        dlg.show()

    def _show_change(self, pno, rect):
        self.organize(False)
        if rect is not None:
            self.view.show_rect(pno, rect)
        else:
            self.view.go_to(pno)

    def _clear_diff(self):
        self.view.diff = {}
        self.view.viewport().update()

    # -- certificates ------------------------------------------------------
    def sign(self, pno=None, rect=None):
        if self.dirty:
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
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(self.path):
            QMessageBox.warning(self, "ხელმოწერა", "აირჩიეთ სხვა სახელი: ორიგინალი უცვლელი უნდა დარჩეს.")
            return
        o = dlg.options()
        if rect is not None:
            box = self._task("pdf_box", (pno, rect), "")
            if box is None:
                return
            o.update(page=pno, box=box)
        tmp = files.temp_name(out)
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
