"""Paragraph editing, step 4: a click with the text tool on a line of a paragraph opens the
whole paragraph (HANDOFF.md, item 7, stage 2)."""
import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox
from test_features import para
from test_ui import app, open_doc, vp, win  # noqa: F401
from test_ui_pages import idle

from furceli import forms, theme

NEW = "ახალი აბზაცი, რომელიც ძველის ადგილას დაიწერა new text"


def click_line(win, doc, i):
    doc.view.set_fit("page")
    win.tool_acts["edit"].trigger()
    QTest.mouseClick(doc.view.viewport(), Qt.MouseButton.LeftButton,
                     pos=vp(doc.view, 0, 120, 120 + i * 15 - 4))
    idle(doc)


def found(doc, text):
    return bool(doc._quiet("search", 0, text))


def test_a_click_on_a_paragraph_opens_the_whole_paragraph(win, tmp_path, monkeypatch):  # noqa: F811
    doc = open_doc(win, para(tmp_path))
    seen = []

    def run(dlg):
        seen.append(dlg.text())
        dlg._text.setPlainText(NEW)
        return 1
    monkeypatch.setattr(forms.ParagraphDialog, "exec", run)
    click_line(win, doc, 3)
    assert "ნომერი 0" in seen[0] and "ნომერი 7" in seen[0]
    assert found(doc, "ახალი აბზაცი") and not found(doc, "ნომერი 3") and not found(doc, "ნომერი 7")
    doc.undo()
    idle(doc)
    assert found(doc, "ნომერი 3") and not found(doc, "ახალი აბზაცი")


def test_only_this_line_is_still_possible(win, tmp_path, monkeypatch):  # noqa: F811
    doc = open_doc(win, para(tmp_path))
    monkeypatch.setattr(forms.ParagraphDialog, "exec", lambda dlg: forms.ParagraphDialog.LINE_ONLY)

    def run(dlg):
        assert "ნომერი 3" in dlg.text()
        dlg._text.setText("შეცვლილი სტრიქონი")
        return True
    monkeypatch.setattr(forms.LineDialog, "exec", run)
    click_line(win, doc, 3)
    assert found(doc, "შეცვლილი სტრიქონი") and not found(doc, "ნომერი 3")
    assert found(doc, "ნომერი 2") and found(doc, "ნომერი 4")


def test_a_line_on_its_own_opens_the_line_dialog(win, tmp_path, monkeypatch):  # noqa: F811
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="sy", fontfile=r"C:\Windows\Fonts\sylfaen.ttf")
    p.insert_text((80, 120), "მარტო სტრიქონი Alone", fontname="sy", fontsize=13)
    path = str(tmp_path / "one.pdf")
    d.save(path)
    doc = open_doc(win, path)

    def never(dlg):
        raise AssertionError("a single line is not a paragraph")
    monkeypatch.setattr(forms.ParagraphDialog, "exec", never)
    opened = []
    monkeypatch.setattr(forms.LineDialog, "exec", lambda dlg: opened.append(dlg.text()) or False)
    click_line(win, doc, 0)
    assert opened == ["მარტო სტრიქონი Alone"]


def test_text_that_does_not_fit_is_explained_and_changes_nothing(win, tmp_path, monkeypatch):  # noqa: F811
    doc = open_doc(win, para(tmp_path))
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[2]))

    def run(dlg):
        dlg._text.setPlainText(" ".join(["ძალიან გრძელი ტექსტი"] * 200))
        return 1
    monkeypatch.setattr(forms.ParagraphDialog, "exec", run)
    click_line(win, doc, 3)
    idle(doc)
    assert told and "არ ეტევა" in told[0]
    assert found(doc, "ნომერი 3") and not doc.ops


def test_the_dialog_follows_the_design(win, tmp_path):  # noqa: F811
    p = {"text": "ერთი ორი", "size": 12.0, "color": (0, 0, 1), "bold": False, "italic": False,
         "serif": False, "mono": False, "lines": [0, 1], "texts": ["ერთი", "ორი"]}
    dlg = forms.ParagraphDialog(p, win)
    ok = dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
    assert ok.property("variant") == "primary"
    assert dlg.style_()["size"] == 12.0 and not dlg.style_changed()
    dlg._size.setValue(10)
    assert dlg.style_()["size"] == 10 and dlg.style_changed()
    assert theme  # colours only through the theme (CLAUDE.md)
