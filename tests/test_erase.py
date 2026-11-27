"""Deleting anything with Delete, as in Acrobat (HANDOFF.md, item 9): the engine part.

Drawn shapes are read from the page's own drawing commands (paths.walk), each painted path
one object; deleting one stops only its own painting, so whatever lies inside or under it
stays, text included."""
import pymupdf
import pytest

from furceli import paths
from furceli.engine import EngineError
from test_features import engine


def drawing(tmp_path, rot=0, form=False):
    """A page as a CAD program writes one: a frame, a hatch (one path of many lines) with a
    label inside, a small square inside the hatch, and a line crossing everything."""
    d = pymupdf.open()
    p = d.new_page(width=600, height=400)
    p.insert_font(fontname="helv")
    p.draw_line((0, 0), (1, 0), width=0)  # a content stream to write into
    p = d[0]
    hatch = " ".join(f"{x} 100 m {x + 50} 200 l" for x in range(200, 350, 10))
    body = ("q 1 w 20 20 560 360 re S Q\n"                        # 0 frame (y up)
            f"q 0.5 w {hatch} S Q\n"                               # 1 hatch, 15 lines
            "q 0 0 1 rg 260 140 20 20 re f Q\n"                    # 2 small square inside
            "q 2 w 50 150 m 550 150 l S Q\n"                       # 3 long line across
            "BT /helv 10 Tf 1 0 0 1 230 170 Tm (B25) Tj ET\n")
    if form:
        fx = d.get_new_xref()
        d.update_object(fx, "<</Type/XObject/Subtype/Form/BBox[0 0 600 400]/Resources<<>>>>")
        d.update_stream(fx, b"q 3 w 400 50 m 500 50 l S Q\n")
        kind, res = d.xref_get_key(p.xref, "Resources")
        if kind == "xref":
            d.xref_set_key(int(res.split()[0]), "XObject", f"<</Fm1 {fx} 0 R>>")
        else:
            d.xref_set_key(p.xref, "Resources/XObject", f"<</Fm1 {fx} 0 R>>")
        body += "q /Fm1 Do Q\n"
    d.update_stream(p.get_contents()[0], body.encode())
    if rot:
        p.set_rotation(rot)
    path = str(tmp_path / f"drawing{rot}{'f' if form else ''}.pdf")
    d.save(path)
    return path


def test_each_painted_path_is_one_object_where_it_is_shown(tmp_path):
    e = engine(drawing(tmp_path))
    got = e.paths(0)
    assert len(got) == 4
    frame, hatch, square, line = got
    assert [round(v) for v in frame["rect"]] == [20, 20, 580, 380]
    assert [round(v) for v in hatch["rect"]] == [200, 200, 390, 300]  # y down as shown
    assert len(hatch["segs"]) == 15 and not hatch["fill"]
    assert square["fill"] and [round(v) for v in square["rect"]] == [260, 240, 280, 260]
    assert [round(v) for v in line["rect"]] == [50, 250, 550, 250]


def test_a_turned_page_shows_paths_turned(tmp_path):
    e = engine(drawing(tmp_path, rot=90))
    square = e.paths(0)[2]
    page = e.doc[0]
    want = pymupdf.Rect(260, 240, 280, 260) * page.rotation_matrix
    assert [round(v) for v in square["rect"]] == [round(v) for v in want.normalize()]


def test_deleting_the_hatch_keeps_what_is_inside_it(tmp_path):
    e = engine(drawing(tmp_path))
    hatch = e.paths(0)[1]
    e.apply(("erase", 0, [(hatch["id"], hatch["rect"])], [], [], []))
    left = e.paths(0)
    assert len(left) == 3
    assert [round(v) for v in left[1]["rect"]] == [260, 240, 280, 260]  # the square stays
    assert "B25" in e.doc[0].get_text()                                  # and the label
    assert all(round(d["rect"].width) != 150 for d in e.doc[0].get_drawings())


def test_a_line_crossing_a_deleted_one_stays(tmp_path):
    e = engine(drawing(tmp_path))
    frame = e.paths(0)[0]
    e.apply(("erase", 0, [(frame["id"], frame["rect"])], [], [], []))
    left = e.paths(0)
    assert [round(v) for v in left[-1]["rect"]] == [50, 250, 550, 250]
    assert len(left) == 3


def test_undo_by_replay_gives_the_same_page(tmp_path):
    src = drawing(tmp_path)
    e = engine(src)
    hatch = e.paths(0)[1]
    op = ("erase", 0, [(hatch["id"], hatch["rect"])], [], [], [])
    e.apply(op)
    after = [o["rect"] for o in e.paths(0)]
    e.replay(src, "", [op])
    assert [o["rect"] for o in e.paths(0)] == after


def test_a_stale_path_is_refused(tmp_path):
    e = engine(drawing(tmp_path))
    with pytest.raises(EngineError):
        e.apply(("erase", 0, [(1, (0, 0, 10, 10))], [], [], []))


def test_text_lines_are_deleted_and_lines_under_them_stay(tmp_path):
    e = engine(drawing(tmp_path))
    ln = next(l for l in e.lines(0) if l["text"].strip() == "B25")
    e.apply(("erase", 0, [], [(ln["index"], ln["text"])], [], []))
    assert "B25" not in e.doc[0].get_text()
    assert len(e.paths(0)) == 4


def test_paths_inside_a_form_are_found_and_deleted(tmp_path):
    e = engine(drawing(tmp_path, form=True))
    got = e.paths(0)
    assert len(got) == 5
    inner = got[4]
    assert [round(v) for v in inner["rect"]] == [400, 350, 500, 350]
    e.apply(("erase", 0, [(inner["id"], inner["rect"])], [], [], []))
    assert len(e.paths(0)) == 4


def test_check_names_what_cannot_go_alone(tmp_path):
    """A form drawn twice: removing its path in one place would remove it in both."""
    path = drawing(tmp_path, form=True)
    d = pymupdf.open(path)
    p = d[0]
    d.update_stream(p.get_contents()[0], d.xref_stream(p.get_contents()[0]) + b"q 1 0 0 1 0 -20 cm /Fm1 Do Q\n")
    d.save(str(tmp_path / "twice.pdf"))
    e = engine(str(tmp_path / "twice.pdf"))
    got = e.paths(0)
    shared = [o for o in got if o["shared"]]
    assert len(shared) == 2
    assert e.erase_check(0, [(o["id"], o["rect"]) for o in got]) == {"blocked": [o["id"] for o in shared]}
    with pytest.raises(EngineError):
        e.apply(("erase", 0, [(shared[0]["id"], shared[0]["rect"])], [], [], []))


def test_the_walk_is_fast_on_a_big_page():
    """30 000 lines read in well under two seconds."""
    import time
    data = b"q 0.2 w " + b" ".join(b"%d %d m %d %d l S" % (i % 500, i // 500, i % 500 + 3, i // 500 + 2)
                                    for i in range(30000)) + b" Q"
    t = time.perf_counter()
    found = list(paths.parse(data))
    assert len(found) == 30000
    assert time.perf_counter() - t < 2


def test_a_hatch_of_many_small_pieces_is_one_object(tmp_path):
    """CAD programs often write a hatch as hundreds of small shapes one after another:
    they are one object, with their own id each, so one click takes the whole hatch."""
    d = pymupdf.open()
    p = d.new_page(width=600, height=400)
    p.draw_line((0, 0), (1, 0), width=0)
    p = d[0]
    pieces = " ".join(f"{100 + (i % 10) * 6} {100 + (i // 10) * 6} m {103 + (i % 10) * 6} "
                      f"{100 + (i // 10) * 6} l {101 + (i % 10) * 6} {103 + (i // 10) * 6} l f"
                      for i in range(40))
    body = (f"q 0.5 g {pieces} Q\n"
            "q 0.5 g 400 300 4 4 re f Q\n"          # a lone square of the same look, far away
            "q 1 w 20 20 m 580 20 l S Q\n")          # a long line
    d.update_stream(p.get_contents()[0], body.encode())
    path = str(tmp_path / "hatch.pdf")
    d.save(path)
    got = engine(path).paths(0)
    assert len(got) == 42
    groups = {o["group"] for o in got[:40]}
    assert len(groups) == 1 and groups != {-1}
    assert got[40]["group"] == -1 and got[41]["group"] == -1


# -- the window: click, boxes, Shift, Delete ----------------------------------------------
from test_ui import app, vp, win  # noqa: E402,F401


def _press_key(v, key):
    from PySide6.QtTest import QTest
    QTest.keyClick(v.viewport(), key)


def _open(win, path):
    from test_ui import open_doc
    doc = open_doc(win, path)
    win.tool_acts["object"].trigger()
    return doc, doc.view


def _click(v, x, y, shift=False, pno=0):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    mod = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, mod, vp(v, pno, x, y))


def test_a_box_left_to_right_takes_what_is_inside_and_delete_deletes_it(win, tmp_path):  # noqa: F811
    from PySide6.QtCore import Qt
    from test_objects import _drag
    from test_ui_pages import idle
    doc, v = _open(win, drawing(tmp_path))
    _drag(v, (190, 190), (400, 310))   # around the hatch, the square and B25
    kinds = sorted(it["kind"] for it in v.erase_chosen())
    assert kinds == ["path", "path", "text"]
    v.viewport().repaint()
    assert v._erase_bar_w.isVisible() and "3" in v._erase_bar_w.count.text()
    _press_key(v, Qt.Key.Key_Delete)
    idle(doc)
    assert doc.ops[-1][0] == "erase"
    left = doc._quiet("paths", 0)
    assert len(left) == 2                       # the frame and the long line stay
    assert "B25" not in " ".join(l["text"] for l in doc._quiet("lines", 0))


def test_a_box_right_to_left_takes_what_it_touches(win, tmp_path):  # noqa: F811
    from test_objects import _drag
    doc, v = _open(win, drawing(tmp_path))
    _drag(v, (120, 255), (60, 245))            # a small box over the long line only
    chosen = v.erase_chosen()
    assert len(chosen) == 1 and chosen[0]["rect"][0] == 50


def test_a_click_takes_one_line_and_shift_takes_things_out(win, tmp_path):  # noqa: F811
    from PySide6.QtCore import Qt
    from test_objects import _drag
    from test_ui_pages import idle
    doc, v = _open(win, drawing(tmp_path))
    _click(v, 450, 250)                        # on the long line, away from the rest
    assert [it["rect"] for it in v.erase_chosen()] == [(50.0, 250.0, 550.0, 250.0)]
    _drag(v, (190, 190), (400, 310))
    b25 = [it for it in v.erase_chosen() if it["kind"] == "text"][0]
    _click(v, (b25["rect"][0] + b25["rect"][2]) / 2, (b25["rect"][1] + b25["rect"][3]) / 2, shift=True)
    assert all(it["kind"] != "text" for it in v.erase_chosen())
    _press_key(v, Qt.Key.Key_Delete)
    idle(doc)
    assert "B25" in " ".join(l["text"] for l in doc._quiet("lines", 0))   # taken out: stays
    assert len(doc._quiet("paths", 0)) == 2


def test_the_filters_leave_text_out(win, tmp_path):  # noqa: F811
    from test_objects import _drag
    doc, v = _open(win, drawing(tmp_path))
    _drag(v, (190, 190), (400, 310))
    v.viewport().repaint()
    v._erase_bar_w.chips["text"][0].setChecked(False)
    assert all(it["kind"] != "text" for it in v.erase_chosen())
    assert len(v.erase_chosen()) == 2


def test_escape_lets_go(win, tmp_path):  # noqa: F811
    from PySide6.QtCore import Qt
    from test_objects import _drag
    doc, v = _open(win, drawing(tmp_path))
    _drag(v, (190, 190), (400, 310))
    _press_key(v, Qt.Key.Key_Escape)
    assert v.erase_chosen() == []


def test_slanted_text_is_deleted_and_the_rest_stays(tmp_path):
    """CAD labels along a sloped roof are written slanted."""
    d = pymupdf.open()
    p = d.new_page(width=600, height=400)
    p.insert_text((100, 300), "SLOPED LABEL", fontsize=11, morph=(pymupdf.Point(100, 300), pymupdf.Matrix(30)))
    p.insert_text((100, 100), "straight words", fontsize=11)
    path = str(tmp_path / "slant.pdf")
    d.save(path)
    e = engine(path)
    ln = next(l for l in e.lines(0) if "SLOPED" in l["text"])
    assert e.erase_check(0, [], [(ln["index"], ln["text"])])["lines_blocked"] == []
    e.apply(("erase", 0, [], [(ln["index"], ln["text"])], [], []))
    text = e.doc[0].get_text()
    assert "SLOPED" not in text and "straight words" in text
