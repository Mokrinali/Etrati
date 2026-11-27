"""Tools, forms, redaction, OCR, conversion, signing, AI and batch through the window."""
import os
import types

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox,
                               QProgressDialog)
from test_features import blank, form, para, picture, scanned
from test_pages import build, holds
from test_ui import app, open_doc, settled, vp, wait, win  # noqa: F401
from test_ui_pages import idle, no_dialogs, read  # noqa: F401

from furceli import ai, forms, tools
from furceli.batch import BatchDialog
from furceli.engine import EngineError


@pytest.fixture
def doc(win, tmp_path):  # noqa: F811
    return open_doc(win, build(tmp_path / "ექვსი.pdf"))


def drag(doc, pno, *pts):
    """Press, move through and release at page points."""
    v = doc.view
    vp_ = [vp(v, pno, *p) for p in pts]
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=vp_[0])
    for p in vp_[1:]:
        q = QPointF(p)
        QApplication.sendEvent(v.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, q, v.viewport().mapToGlobal(q), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=vp_[-1])


def comments(doc):
    doc.sidebar.setCurrentWidget(doc.comments)
    doc.load_comments()
    key = ("comments", doc.view.rev)
    wait(lambda: key not in doc.client._pending)
    n = {}
    wait(lambda: n.setdefault("x", 0) is not None and _tick(n))
    return [doc.comments.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(doc.comments.list.count())]


def _tick(n):
    n["x"] += 1
    return n["x"] > 15


def kinds(doc):
    return [a["type"] for p, a in doc._quiet("all_annots")]


@pytest.mark.parametrize("tool,points,kind", [
    ("rect", [(100, 300), (300, 400)], "Square"), ("ellipse", [(100, 300), (300, 400)], "Circle"),
    ("line", [(100, 300), (300, 400)], "Line"), ("arrow", [(100, 300), (300, 400)], "Line"),
    ("ink", [(100, 300), (150, 350), (200, 300), (250, 360)], "Ink")])
@pytest.mark.parametrize("rot", [0, 1])
def test_drawing_tools(win, doc, tool, points, kind, rot):  # noqa: F811
    for _ in range(rot):
        doc.view.rotate()
    doc.view.set_fit("page")
    win.tool_acts[tool].trigger()
    assert doc.view.tool == tool
    drag(doc, 0, *points)
    idle(doc)
    assert kinds(doc).count(kind) == 1
    new = [a for p, a in doc._quiet("all_annots") if a["type"] == kind][0]
    assert abs(new["rect"][0] - 100) < 12 and abs(new["rect"][1] - 300) < 12
    doc.undo()
    idle(doc)
    assert kind not in kinds(doc)


def test_scroll_position_survives_a_comment(win, doc):  # noqa: F811
    doc.view.set_zoom(2.0)
    doc.view.verticalScrollBar().setValue(1234)
    before = doc.view.verticalScrollBar().value()
    doc.edit(("annot", 1, "rect", {"rect": (10, 10, 60, 60)}, doc._meta()))
    idle(doc)
    assert doc.view.verticalScrollBar().value() == before


def test_highlight_by_dragging_over_text(win, doc):  # noqa: F811
    doc.view.set_fit("page")
    wait(lambda: 0 in doc.view.chars)
    chars = [c for c, ln in doc.view.chars[0] if ln == 0]
    n = kinds(doc).count("Highlight")
    win.tool_acts["highlight"].trigger()
    mid = (chars[0][1] + chars[0][3]) / 2
    drag(doc, 0, (chars[0][0] + 1, mid), (chars[-1][2] - 1, mid))
    idle(doc)
    assert kinds(doc).count("Highlight") == n + 1 and not doc.view.has_selection()


def test_highlight_over_two_pages_is_two_edits(doc):
    doc.view._sel_a, doc.view._sel_b = (0, 0), (1, 4)
    wait(lambda: doc.view.selected_text() is not None)
    n = kinds(doc).count("Underline")
    doc.mark_selection("underline")
    wait(lambda: len(doc.ops) == 2)
    idle(doc)
    assert kinds(doc).count("Underline") == n + 2


def test_note_text_stamp_and_edit_comment(win, doc, monkeypatch):  # noqa: F811
    doc.view.set_fit("page")
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a: ("ჩემი შენიშვნა", True))
    win.tool_acts["note"].trigger()
    QTest.mouseClick(doc.view.viewport(), Qt.MouseButton.LeftButton, pos=vp(doc.view, 0, 400, 300))
    idle(doc)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: ("დამტკიცებულია", True))
    win.tool_acts["stamp"].trigger()
    QTest.mouseClick(doc.view.viewport(), Qt.MouseButton.LeftButton, pos=vp(doc.view, 0, 300, 500))
    idle(doc)
    got = {a["text"]: (p, a) for p, a in doc._quiet("all_annots")}
    assert {"ჩემი შენიშვნა", "დამტკიცებულია"} <= set(got)
    doc.sidebar.setCurrentWidget(doc.comments)
    wait(lambda: doc.comments.list.count() >= 8)
    texts = [doc.comments.list.item(i).text() for i in range(doc.comments.list.count())]
    assert any("შენიშვნა" in t and "ჩემი შენიშვნა" in t for t in texts)
    doc.comments.list.setCurrentRow(next(i for i, t in enumerate(texts) if "ჩემი" in t))
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a: ("შეცვლილი", True))
    doc.comments.edit()
    idle(doc)
    assert "შეცვლილი" in [a["text"] for _, a in doc._quiet("all_annots")]
    wait(lambda: any("შეცვლილი" in doc.comments.list.item(i).text()
                     for i in range(doc.comments.list.count())))
    row = next(i for i in range(doc.comments.list.count())
               if "შეცვლილი" in doc.comments.list.item(i).text())
    doc.comments.list.setCurrentRow(row)
    doc.comments.delete()
    idle(doc)
    assert "შეცვლილი" not in [a["text"] for _, a in doc._quiet("all_annots")]


def test_add_text_to_page(win, doc, monkeypatch):  # noqa: F811
    doc.view.set_fit("page")

    def run(dlg):
        dlg._text.setPlainText("ახალი ქართული წინადადება, რომელიც გრძელია")
        return True
    monkeypatch.setattr(forms.TextDialog, "exec", run)
    win.tool_acts["text"].trigger()
    QTest.mouseClick(doc.view.viewport(), Qt.MouseButton.LeftButton, pos=vp(doc.view, 0, 100, 400))
    idle(doc)
    assert doc._quiet("search", 0, "წინადადება")


def test_escape_leaves_tool(win, doc):  # noqa: F811
    win.tool_acts["rect"].trigger()
    QTest.keyClick(doc.view, Qt.Key.Key_Escape)
    assert doc.view.tool == "select" and win.tool_acts["select"].isChecked()


def test_edit_line(win, tmp_path, monkeypatch):  # noqa: F811
    doc = open_doc(win, para(tmp_path))
    doc.view.set_fit("page")

    def run(dlg):
        assert "ნომერი 3" in dlg.text()
        dlg._text.setText("შეცვლილი სტრიქონი")
        return True
    monkeypatch.setattr(forms.LineDialog, "exec", run)
    win.tool_acts["edit"].trigger()
    QTest.mouseClick(doc.view.viewport(), Qt.MouseButton.LeftButton,
                     pos=vp(doc.view, 0, 120, 120 + 3 * 15 - 4))
    idle(doc)
    assert doc._quiet("search", 0, "შეცვლილი სტრიქონი") and not doc._quiet("search", 0, "ნომერი 3")
    assert doc._quiet("search", 0, "ნომერი 4") and doc._quiet("search", 0, "ნომერი 2")


def test_fill_form_by_clicking(win, tmp_path, monkeypatch):  # noqa: F811
    e = form(tmp_path)
    path = str(tmp_path / "form.pdf")
    e.save(path)
    doc = open_doc(win, path)
    doc.view.set_fit("page")
    wait(lambda: len(doc.view.widgets.get(0, [])) == 4)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("ნინო", True))
    click = lambda x, y: QTest.mouseClick(  # noqa: E731
        doc.view.viewport(), Qt.MouseButton.LeftButton, pos=vp(doc.view, 0, x, y))
    click(200, 112)
    idle(doc)
    wait(lambda: 0 in doc.view.widgets)
    click(110, 280)
    idle(doc)
    wait(lambda: 0 in doc.view.widgets)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: ("ბათუმი", True))
    click(150, 322)
    idle(doc)
    wait(lambda: 0 in doc.view.widgets)
    assert [w["value"] for w in doc.view.widgets[0]] == ["ნინო", "", True, "ბათუმი"]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    win.a_flat.trigger()
    idle(doc)
    wait(lambda: 0 in doc.view.widgets)
    assert doc.view.widgets[0] == [] and doc._quiet("search", 0, "ნინო")


def test_create_field(win, tmp_path, monkeypatch):  # noqa: F811
    doc = open_doc(win, blank(tmp_path))
    doc.view.set_fit("page")

    def run(dlg):
        dlg._name.setText("ახალი")
        return True
    monkeypatch.setattr(forms.FieldDialog, "exec", run)
    win.tool_acts["field"].trigger()
    drag(doc, 0, (100, 100), (300, 130))
    idle(doc)
    wait(lambda: doc.view.widgets.get(0))
    assert doc.view.widgets[0][0]["name"] == "ახალი"


def test_signature_picture(win, tmp_path, monkeypatch):  # noqa: F811
    doc = open_doc(win, blank(tmp_path))
    doc.view.set_fit("page")

    def run(dlg):
        dlg.canvas.strokes = [[QPointF(20, 20), QPointF(200, 120), QPointF(300, 40)]]
        dlg._check()
        return True
    monkeypatch.setattr(forms.SignaturePad, "exec", run)
    win.a_sigpic.trigger()
    assert doc.view.tool == "image"
    drag(doc, 0, (100, 600), (300, 680))
    idle(doc)
    assert len(doc.ops) == 1 and doc.view.tool == "select"
    doc.undo()
    idle(doc)
    doc.redo()
    idle(doc)
    assert len(doc.ops) == 1


def test_typed_signature_makes_picture(app, tmp_path):  # noqa: F811
    pad = forms.SignaturePad(None, str(tmp_path))
    pad.tabs.setCurrentIndex(1)
    pad.typed.setText("ნინო")
    pad._check()
    assert os.path.getsize(pad.path) > 200
    os.remove(pad.path)


def test_redact_flow(win, doc, tmp_path, monkeypatch):  # noqa: F811
    doc.query.setText("ASECRET")
    doc.start_search()
    wait(lambda: doc._left == 0)
    win.a_redfind.trigger()
    assert sum(len(m) for m in doc.view.marks.values()) == 6
    win.a_redclear.trigger()
    assert not doc.view.marks and not win.a_redgo.isEnabled()
    doc.view.set_fit("page")
    win.tool_acts["redact"].trigger()
    drag(doc, 0, (60, 50), (250, 90))
    win.a_redfind.trigger()
    assert win.a_redgo.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[-1]))
    win.a_redgo.trigger()
    idle(doc)
    wait(lambda: told or not getattr(doc, "_check_word", ""))
    out = str(tmp_path / "r.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    assert doc.save_as()
    d = pymupdf.open(out)
    assert not any(holds(d, f"ASECRET{i}") for i in range(6)) and holds(d, "AVALUE5")


def test_redact_undo(win, doc, monkeypatch):  # noqa: F811
    doc.view.marks = {0: [(60, 50, 250, 90)]}
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    doc.apply_redactions()
    idle(doc)
    assert not doc._quiet("search", 0, "ASECRET0")
    doc.undo()
    idle(doc)
    assert doc._quiet("search", 0, "ASECRET0")


def test_password_set_and_reopen(win, doc, tmp_path, monkeypatch):  # noqa: F811
    def run(dlg):
        dlg._user.setText("საიდუმლო")
        dlg._user2.setText("საიდუმლო")
        dlg._check()
        return dlg.result() == QDialog.DialogCode.Accepted
    monkeypatch.setattr(forms.ProtectDialog, "exec", run)
    win.a_protect.trigger()
    idle(doc)
    out = str(tmp_path / "p.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    assert doc.save_as()
    d = pymupdf.open(out)
    assert d.needs_pass and d.authenticate("საიდუმლო")
    d.close()
    # the same tab keeps working and keeps the password on the next save
    doc.edit(("rotate", [0], 90))
    idle(doc)
    assert doc.save()
    d = pymupdf.open(out)
    assert d.needs_pass and d.authenticate("საიდუმლო") and d[0].rotation == 90


def test_ocr_through_window(win, tmp_path, monkeypatch):  # noqa: F811
    doc = open_doc(win, scanned(tmp_path))
    monkeypatch.setattr(forms.OcrDialog, "exec", lambda d: True)
    win.a_ocr.trigger()
    idle(doc)
    doc.query.setText("ხელშეკრულების ღირებულება")
    doc.start_search()
    wait(lambda: doc._left == 0)
    assert len(doc._found) == 1
    ocr_calls = []
    real = doc.client.submit
    doc.client.submit = lambda k, p, op, *a: (ocr_calls.append(op), real(k, p, op, *a))
    doc.undo()
    idle(doc)
    doc.redo()
    idle(doc)
    assert "ocr_words" not in ocr_calls  # undo never repeats the slow part


def test_watermark_header_background(win, doc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(forms.WatermarkDialog, "exec", lambda d: True)
    monkeypatch.setattr(forms.HeaderDialog, "exec", lambda d: True)
    win.a_water.trigger()
    idle(doc)
    win.a_head.trigger()
    idle(doc)
    win.a_bates.trigger()
    idle(doc)
    assert doc._quiet("search", 2, "ნიმუში") and doc._quiet("search", 2, "3 / 6")
    assert doc._quiet("search", 2, "000003")


@pytest.mark.parametrize("k", range(5))
def test_exports(win, doc, tmp_path, monkeypatch, k):  # noqa: F811
    before = read(doc.path)
    out = str(tmp_path / "შედეგი")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    win.a_exports[k].trigger()
    ext = list(tools.ToolsMixin.EXPORTS)[k]
    assert os.path.getsize(f"{out}.{ext}") > 20 and read(doc.path) == before
    assert not [f for f in os.listdir(tmp_path) if "furceli" in f]


def test_export_images_and_pdfa(win, doc, tmp_path, monkeypatch):  # noqa: F811
    folder = tmp_path / "im"
    folder.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(folder))
    doc.export_images("png", 60)
    doc.export_images("png", 60)
    assert len(os.listdir(folder)) == 12
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "a.pdf"), ""))
    win.a_pdfa.trigger()
    assert "Helvetica" in told[0] and not os.path.exists(tmp_path / "a.pdf")


def test_create_pdf(win, tmp_path, monkeypatch):  # noqa: F811
    out = str(tmp_path / "new.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    win.make_pdf([picture(tmp_path)])
    wait(lambda: win.doc and win.doc.loaded)
    assert len(win.doc.sizes) == 1
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"x")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "n2.pdf"), ""))
    win.make_pdf([picture(tmp_path), str(bad)])
    assert "broken.png" in told[0] and not os.path.exists(tmp_path / "n2.pdf")


def test_compare_dialog(win, tmp_path, monkeypatch):  # noqa: F811
    old = build(tmp_path / "old.pdf", 6, "B")
    doc = open_doc(win, build(tmp_path / "new.pdf"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (old, ""))
    shown = []
    monkeypatch.setattr(forms.CompareDialog, "show", lambda d: shown.append(d))
    win.a_compare.trigger()
    dlg = shown[0]
    assert dlg.list.count() >= 1 and "→" in dlg.list.item(0).text()
    assert set(doc.view.diff) == set(range(6))
    dlg._pick(dlg.list.item(0))
    dlg.reject()
    assert not doc.view.diff


def test_sign_and_verify(win, doc, tmp_path, monkeypatch):  # noqa: F811
    from furceli import signing
    pfx, out = str(tmp_path / "c.pfx"), str(tmp_path / "s.pdf")
    signing.make_certificate(pfx, "secret", "ნინო")

    def run(dlg):
        dlg._pfx.setText(pfx)
        dlg._pw.setText("secret")
        return True
    monkeypatch.setattr(forms.SignDialog, "exec", run)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    before = read(doc.path)
    doc.view.set_fit("page")
    win.a_signbox.trigger()
    drag(doc, 0, (300, 600), (500, 660))
    wait(lambda: win.tabs.count() == 2 and win.doc.loaded)
    assert read(doc.path) == before and win.doc.info["signed"]
    rows = win.doc._quiet("verify", out, "")
    assert rows[0]["intact"]
    assert "✔" in forms.VerifyDialog.describe(rows[0])[0]
    d = pymupdf.open(out)
    r = [w.rect for w in d[0].widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE][0]
    assert abs(r.x0 - 300) < 4 and abs(r.y0 - 600) < 4
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[-1]))
    doc.edit(("rotate", [0], 90))
    idle(doc)
    doc.sign()
    assert told  # unsaved changes: refused


def test_restricted_file_blocks_tools(win, locked, monkeypatch):  # noqa: F811
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("საიდუმლო", True))
    doc = open_doc(win, locked)
    for a in (win.a_ocr, win.a_water, win.a_redgo, win.tool_acts["edit"],
              win.tool_acts["highlight"]):
        assert not a.isEnabled()
    assert doc.edit(("annot", 0, "rect", {"rect": (1, 1, 50, 50)}, doc._meta())) is False


def test_batch(win, tmp_path):  # noqa: F811
    a, b = build(tmp_path / "ა.pdf"), build(tmp_path / "ბ.pdf", 5, "B")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"no")
    out = tmp_path / "out"
    out.mkdir()
    before = [read(a), read(b)]
    dlg = BatchDialog(win)
    dlg.list.addItems([a, str(bad), b])
    for box in (dlg.turn, dlg.mark, dlg.clean, dlg.squeeze, dlg.lock):
        box.setChecked(True)
    dlg.password.setText("x1")
    dlg.run(str(out))
    assert sorted(os.listdir(out)) == ["ა.pdf", "ბ.pdf"], dlg.report
    assert [bool(e) for _, e in dlg.report] == [False, True, False]
    d = pymupdf.open(str(out / "ა.pdf"))
    assert d.needs_pass and d.authenticate("x1") and d[0].rotation == 90
    assert "ნიმუში" in d[3].get_text()
    assert [read(a), read(b)] == before
    dlg.reject()


# -- assistant ---------------------------------------------------------------
class FakeStream:
    def __init__(self, owner, kw):
        self.owner, self.kw = owner, kw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        yield "ღირებულება "
        yield "არის 100"

    def get_final_message(self):
        if self.owner.error:
            raise self.owner.error
        cite = types.SimpleNamespace(type="content_block_location", start_block_index=2,
                                     end_block_index=3)
        return types.SimpleNamespace(
            stop_reason=self.owner.stop, model=self.kw["model"], content=[
                types.SimpleNamespace(type="thinking", thinking=""),
                types.SimpleNamespace(type="text", text="ღირებულება არის <100>", citations=[cite])],
            usage=types.SimpleNamespace(input_tokens=1000, output_tokens=100,
                                        cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeClient:
    def __init__(self):
        self.calls, self.error, self.stop = [], None, "end_turn"
        api = types.SimpleNamespace(stream=self._stream)
        self.messages = api
        self.beta = types.SimpleNamespace(messages=api)

    def _stream(self, **kw):
        import copy
        self.calls.append({k: (list(v) if k == "messages" else copy.copy(v)) for k, v in kw.items()})
        return FakeStream(self, kw)


@pytest.fixture
def panel(win, doc, monkeypatch):  # noqa: F811
    fake = FakeClient()
    p = ai.AiPanel(doc, win.settings, lambda: doc._quiet("page_texts"), fake)
    p.fake = fake
    return p


def test_ai_asks_for_consent_first(panel, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    panel.ask("რა წერია?")
    assert not panel.fake.calls


def test_ai_answer_with_page_links(win, doc, panel, monkeypatch):  # noqa: F811
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    panel.ask("რა ღირს?")
    wait(lambda: panel._idle and panel.chat and not panel.chat.busy)
    call = panel.fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and "tools" not in call
    block = call["messages"][0]["content"][0]
    assert block["citations"] == {"enabled": True} and len(block["source"]["content"]) == 6
    html = panel.log.toHtml()
    assert "page:2" in html and "&lt;100&gt;" in html
    assert panel.log.toPlainText().count("ღირებულება") == 1
    assert "$0.006" in panel.cost.text()
    panel._link(types.SimpleNamespace(scheme=lambda: "page", path=lambda: "2"))
    assert doc.view.page == 2
    panel.ask("კიდევ?")
    wait(lambda: len(panel.fake.calls) == 2 and not panel.chat.busy)
    second = panel.fake.calls[1]["messages"]
    assert len(second) == 3 and second[:1] == panel.fake.calls[0]["messages"]  # append-only
    assert second[1]["content"][0].type == "thinking"                         # passed back whole
    assert len(second[2]["content"]) == 1                                     # document sent once


def test_ai_errors_are_explained_and_can_be_retried(panel, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    panel.fake.error = RuntimeError("boom")
    panel.ask("ერთი")
    wait(lambda: panel._idle)
    assert "შეცდომა" in panel.log.toPlainText() and panel.chat._messages == []
    panel.fake.error, panel.fake.stop = None, "refusal"
    panel.ask("ორი")
    wait(lambda: panel._idle)
    assert "პასუხი არ გასცა" in panel.log.toPlainText() and panel.chat._messages == []


def test_ai_document_changes_start_a_new_chat(win, doc, panel, monkeypatch):  # noqa: F811
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    panel.ask("ერთი")
    wait(lambda: panel.chat and not panel.chat.busy and panel._idle)
    first = panel.chat
    doc.edit(("rotate", [0], 90))
    idle(doc)
    panel.ask("ორი")
    wait(lambda: panel.chat is not first and not panel.chat.busy and panel._idle)


def test_ai_key_is_encrypted(win):  # noqa: F811
    ai.save_key(win.settings, "sk-ant-test-123")
    win.settings.sync()
    assert ai.load_key(win.settings) == "sk-ant-test-123"
    assert b"sk-ant" not in read(win.settings.fileName())


def test_ai_panel_toggles(win, doc):  # noqa: F811
    win.a_ai.trigger()
    assert doc.ai is not None and not doc.ai.isHidden()
    win.a_ai.trigger()
    assert doc.ai.isHidden()


def test_dialogs_build_and_validate(app, monkeypatch):  # noqa: F811
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    d = forms.WatermarkDialog(5)
    d._pages.setText("9")
    d.try_accept()
    assert told and d.result() != QDialog.DialogCode.Accepted
    p = forms.ProtectDialog(False)
    p._user.setText("a")
    p._user2.setText("b")
    p._check()
    p._user2.setText("a")
    p._perms["print"].setChecked(False)
    p._check()
    assert len(told) == 3 and p.result() != QDialog.DialogCode.Accepted
    p._owner.setText("o")
    p._check()  # not repeated: a slip could never be undone
    assert len(told) == 4 and p.result() != QDialog.DialogCode.Accepted
    p._owner2.setText("o")
    p._check()
    assert p.result() == QDialog.DialogCode.Accepted
    f = forms.FieldDialog()
    f._check()
    assert len(told) == 5
    forms.ScrubDialog(), forms.CompressDialog(10), forms.OcrDialog(3, 0), forms.TextDialog()


def test_unknown_operations_are_refused():
    from furceli.worker import run
    from furceli.features import Features
    for op in ("__init__", "doc", "_apply", "close", "eval"):
        with pytest.raises(EngineError, match="bad_op"):
            run(Features(), op, ())
    assert QProgressDialog is not None
