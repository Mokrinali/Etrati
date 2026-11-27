"""Blocks of text moved, enlarged or deleted with the objects tool (HANDOFF.md, item 7)."""
import pymupdf
import pytest

from test_features import engine, saved
from test_ui import app, vp, win  # noqa: F401

from furceli.engine import EngineError


def page_with_text(tmp_path, rot=0):
    """Two blocks as shown: a heading at (72, 100) and a paragraph of two lines at (72, 300)."""
    d = pymupdf.open()
    p = d.new_page(width=600 if rot in (0, 180) else 800, height=800 if rot in (0, 180) else 600)
    p.set_rotation(rot)
    m = p.derotation_matrix
    font = r"C:\Windows\Fonts\sylfaen.ttf"
    p.insert_font(fontname="S", fontfile=font)
    p.insert_text(pymupdf.Point(72, 100) * m, "სათაური Heading", fontname="S", fontsize=18,
                  color=(0.8, 0, 0), rotate=rot)
    p.insert_text(pymupdf.Point(72, 300) * m, "პირველი სტრიქონი first line", fontname="S",
                  fontsize=12, rotate=rot)
    p.insert_text(pymupdf.Point(72, 316) * m, "მეორე სტრიქონი second line", fontname="S",
                  fontsize=12, rotate=rot)
    path = str(tmp_path / f"text{rot}.pdf")
    d.save(path)
    return path


def texts(e, pno=0):
    return [o for o in e.objects(pno) if o["kind"] == "text"]


def words_at(page, rect):
    return " ".join(w[4] for w in page.get_text("words", clip=pymupdf.Rect(rect) * page.derotation_matrix))


@pytest.mark.parametrize("rot", [0, 90])
def test_blocks_of_text_are_listed_where_they_are_shown(tmp_path, rot):
    e = engine(page_with_text(tmp_path, rot))
    got = texts(e)
    assert all(o["movable"] for o in got)
    assert sorted(t for o in got for t in o["texts"]) == sorted(
        ["სათაური Heading", "პირველი სტრიქონი first line", "მეორე სტრიქონი second line"])
    head = sorted(got, key=lambda o: o["rect"][1])[0]
    assert head["texts"] == ["სათაური Heading"]  # MuPDF decides how lines make blocks
    assert 70 <= head["rect"][0] <= 74 and 80 <= head["rect"][1] <= 100


@pytest.mark.parametrize("rot", [0, 90])
def test_a_block_moves_with_its_look(tmp_path, rot):
    e = engine(page_with_text(tmp_path, rot))
    para = [o for o in texts(e) if "second line" in o["texts"][-1]][0]
    x0, y0, x1, y1 = para["rect"]
    new = (x0 + 100, y0 + 200, x1 + 100, y1 + 200)
    e.apply(("text_move", 0, para["lines"], para["texts"], para["rect"], new))
    d = saved(e, tmp_path)
    page = d[0]
    there = words_at(page, (new[0] - 2, new[1] - 2, new[2] + 40, new[3] + 2))
    for line in para["texts"]:  # every line of the block, at its new place
        assert line.split()[0] in there and line.split()[-1] in there
    assert words_at(page, (x0 - 2, y0 - 2, x1 + 2, y1 + 2)) == ""  # gone from the old place
    assert "სათაური" in words_at(page, (60, 70, 400, 110))  # the other block untouched
    moved = [o for o in texts(engine(str(tmp_path / "out.pdf"))) if "second" in " ".join(o["texts"])]
    assert moved and abs(moved[0]["rect"][0] - new[0]) < 3


def test_enlarging_a_block_enlarges_its_letters(tmp_path):
    e = engine(page_with_text(tmp_path))
    head = sorted(texts(e), key=lambda o: o["rect"][1])[0]
    x0, y0, x1, y1 = head["rect"]
    e.apply(("text_move", 0, head["lines"], head["texts"], head["rect"],
             (x0, y0, x0 + (x1 - x0) * 2, y0 + (y1 - y0) * 2)))
    d = saved(e, tmp_path)
    spans = [s for b in d[0].get_text("dict")["blocks"] for ln in b.get("lines", ())
             for s in ln["spans"] if "Heading" in s["text"]]
    assert spans and abs(spans[0]["size"] - 36) < .6
    assert spans[0]["color"] == 0xcc0000  # its colour kept


def test_a_block_is_deleted_and_undo_brings_it_back(tmp_path):
    src = page_with_text(tmp_path)
    e = engine(src)
    head = sorted(texts(e), key=lambda o: o["rect"][1])[0]
    e.apply(("text_del", 0, head["lines"], head["texts"]))
    d = saved(e, tmp_path)
    assert "Heading" not in d[0].get_text() and "second" in d[0].get_text()
    e2 = engine(src)
    e2.replay(src, "", [])
    assert len(texts(e2)) == 2


def test_a_stale_block_is_refused(tmp_path):
    e = engine(page_with_text(tmp_path))
    head = sorted(texts(e), key=lambda o: o["rect"][1])[0]
    with pytest.raises(EngineError, match="stale"):
        e.apply(("text_del", 0, head["lines"], ["something else"]))


def test_the_objects_tool_drags_a_block(win, tmp_path):  # noqa: F811
    from test_objects import _drag
    from test_ui import open_doc
    from test_ui_pages import idle
    doc = open_doc(win, page_with_text(tmp_path))
    win.tool_acts["object"].trigger()
    v = doc.view
    _drag(v, (90, 95), (290, 495))
    idle(doc)
    assert doc.ops[-1][0] == "text_move"
    blocks = [o for o in doc._quiet("objects", 0) if o["kind"] == "text"]
    head = [o for o in blocks if "Heading" in " ".join(o["texts"])][0]
    assert abs(head["rect"][0] - 272) < 4 and abs(head["rect"][1] - 482) < 8
    assert v.selected_object()[1]["kind"] == "text"
    v.viewport().repaint()
    assert not v._object_bar._w.isVisible() and not v._object_bar.copy_btn.isVisible()


def test_the_menu_offers_only_what_fits_a_block_of_text(win, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QMenu
    from test_ui import open_doc
    from furceli import tools
    doc = open_doc(win, page_with_text(tmp_path))
    win.tool_acts["object"].trigger()
    seen = []

    class Menu(QMenu):
        def exec(self, *a):
            seen.extend(x.text() for x in self.actions() if x.text())
    monkeypatch.setattr(tools, "QMenu", Menu)
    doc.view_menu(0, QPointF(90, 95), None)
    assert "ტექსტის წაშლა" in seen and "სურათის ჩანაცვლება…" not in seen


def overlapping(tmp_path):
    """A unit and a number whose boxes overlap, as some programs (Odoo) write table cells."""
    d = pymupdf.open()
    p = d.new_page(width=400, height=200)
    p.insert_font(fontname="S", fontfile=r"C:\Windows\Fonts\sylfaen.ttf")
    w = pymupdf.Font(fontfile=r"C:\Windows\Fonts\sylfaen.ttf").text_length("დღე", fontsize=12)
    # written apart in the page's order, as such programs do: two lines, their boxes overlapping
    p.insert_text((100 + w - 4, 100), "13", fontname="S", fontsize=12, color=(.4, .4, .4))
    p.insert_text((100, 170), "სხვა ტექსტი", fontname="S", fontsize=12)
    p.insert_text((100, 100), "დღე", fontname="S", fontsize=12)
    path = str(tmp_path / "cells.pdf")
    d.save(path)
    return path


def test_a_line_whose_box_overlaps_its_neighbour_can_be_changed(tmp_path):
    e = engine(overlapping(tmp_path))
    lines = {ln["text"]: ln for ln in e.lines(0)}
    assert set(lines) == {"დღე", "13", "სხვა ტექსტი"}
    ln = lines["13"]
    e.apply(("replace", 0, ln["index"], "13", "15",
             {k: ln[k] for k in ("size", "color", "bold", "italic", "serif", "mono")}))
    d = saved(e, tmp_path)
    words = "".join(d[0].get_text().split())
    assert "15" in words and "13" not in words and "დღე" in words  # the neighbour written again
    spans = {s["text"].strip(): s for b in d[0].get_text("dict")["blocks"] for l in b.get("lines", ())
             for s in l["spans"] if s["text"].strip()}
    assert spans["15"]["color"] == 0x666666 and abs(spans["დღე"]["size"] - 12) < .1
