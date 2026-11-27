"""A date put on pages as text of its own (design canvas "ETRATI: სურათი და თარიღი")."""
import datetime

import pymupdf
import pytest

from test_features import engine, saved
from test_ui import app, vp, win  # noqa: F401

from furceli import objects_ui

STYLE = {"font": "sylfaen", "size": 11.0, "color": (0.06, 0.13, 0.42)}


def pages(tmp_path, rot=0, sizes=((600, 800), (600, 800), (600, 800))):
    d = pymupdf.open()
    for w, h in sizes:
        p = d.new_page(width=w if rot in (0, 180) else h, height=h if rot in (0, 180) else w)
        p.set_rotation(rot)
    path = str(tmp_path / f"p{rot}.pdf")
    d.save(path)
    return path


def dates(e, pno):
    return [o for o in e.objects(pno) if o["kind"] == "date"]


def close(a, b, tol=1.0):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_date_formats():
    day = datetime.date(2026, 11, 27)
    assert [objects_ui.date_text(day, f) for f in range(3)] == [
        "27.11.2026", "27 ნოემბერი 2026", "2026-11-27"]
    assert objects_ui.date_text(datetime.date(2026, 1, 5), 1) == "5 იანვარი 2026"


@pytest.mark.parametrize("rot", [0, 90])
def test_a_date_is_text_where_it_was_clicked(tmp_path, rot):
    e = engine(pages(tmp_path, rot))
    e.apply(("date", 0, (400, 50), "27 ნოემბერი 2026", STYLE, [0]))
    got = dates(e, 0)
    assert len(got) == 1 and got[0]["movable"]
    r = got[0]["rect"]
    assert close(r[:2], (400, 50), 1.5) and 60 < r[2] - r[0] < 140 and 10 < r[3] - r[1] < 20
    d = saved(e, tmp_path)
    assert "27ნოემბერი2026" in "".join(d[0].get_text().split())  # real text: found, copied
    words = d[0].get_text("dict")["blocks"][0]["lines"][0]
    upright = {0: (1, 0), 90: (0, -1)}[rot]  # reads left to right as shown
    assert close(words["dir"], upright, .01)
    assert dates(engine(str(tmp_path / "out.pdf")), 1) == []


def test_a_date_on_all_pages_keeps_its_corner(tmp_path):
    e = engine(pages(tmp_path, sizes=((600, 800), (800, 600), (600, 800))))
    e.apply(("date", 0, (450, 40), "27.11.2026", STYLE, [0, 1, 2]))
    r0 = dates(e, 0)[0]["rect"]
    r1 = dates(e, 1)[0]["rect"]
    assert close(r1, (r0[0] + 200, r0[1], r0[2] + 200, r0[3]), 1.5)  # top right: same distance
    assert close(dates(e, 2)[0]["rect"], r0, .5)


def test_each_date_moves_and_goes_on_its_own(tmp_path):
    e = engine(pages(tmp_path))
    e.apply(("date", 0, (100, 100), "27.11.2026", STYLE, [0, 1]))
    o = dates(e, 1)[0]
    w, h = o["rect"][2] - o["rect"][0], o["rect"][3] - o["rect"][1]
    e.apply(("date_move", 1, o["xref"], o["rect"], (300, 500, 300 + w, 500 + h)))
    assert close(dates(e, 1)[0]["rect"], (300, 500, 300 + w, 500 + h), .5)
    assert close(dates(e, 0)[0]["rect"][:2], (100, 100), 1.5)  # the other one stayed
    e.apply(("date_del", 0, dates(e, 0)[0]["xref"], dates(e, 0)[0]["rect"]))
    assert dates(e, 0) == [] and len(dates(e, 1)) == 1
    d = saved(e, tmp_path)
    assert "27.11.2026" not in d[0].get_text() and "27.11.2026" in d[1].get_text()


def test_a_date_goes_to_other_pages_later(tmp_path):
    e = engine(pages(tmp_path))
    e.apply(("date", 0, (100, 100), "2026-11-27", STYLE, [0]))
    o = dates(e, 0)[0]
    e.apply(("date_copy", 0, o["xref"], o["rect"], [1, 2], [0, 0]))
    for p in (1, 2):
        assert close(dates(e, p)[0]["rect"], o["rect"], .5)
    assert "2026-11-27" in saved(e, tmp_path)[2].get_text()


# -- the window --------------------------------------------------------------------------
def test_the_dialog_writes_the_date_as_chosen(app):  # noqa: F811
    from PySide6.QtCore import QDate
    d = objects_ui.DateDialog(0, 5)
    assert d.today.isChecked() and d.only.isChecked() and d.pages() == [0]
    d.day.setDate(QDate(2026, 3, 8))
    assert d.other.isChecked()
    d._formats.button(1).setChecked(True)
    assert d.text() == "8 მარტი 2026"
    assert [b.text() for b in d._format_buttons] == ["08.03.2026", "8 მარტი 2026", "2026-03-08"]
    d.all.setChecked(True)
    assert d.pages() == [0, 1, 2, 3, 4]
    d.some.setChecked(True)
    d.range.setText("3-4")
    assert d.pages() == [0, 2, 3]  # its own page too
    st = d.style()
    assert st["font"] == "sylfaen" and st["size"] == 11 and st["color"] == (0.06, 0.13, 0.42)


def _rects(doc, pno, kind="date"):
    return [tuple(round(x) for x in o["rect"]) for o in doc._quiet("objects", pno)
            if o["kind"] == kind]


def test_the_date_tool_puts_a_date_on_the_pages_chosen(win, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from test_ui import open_doc
    from test_ui_pages import idle

    def run(dlg):
        dlg.all.setChecked(True)
        dlg._formats.button(0).setChecked(True)
        return True
    monkeypatch.setattr(objects_ui.DateDialog, "exec", run)
    doc = open_doc(win, pages(tmp_path))
    win.tool_acts["date"].trigger()
    v = doc.view
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, 0, 420, 40))
    idle(doc)
    today = objects_ui.date_text(datetime.date.today(), 0)
    for p in range(3):
        r = _rects(doc, p)
        assert len(r) == 1 and abs(r[0][0] - 420) <= 2 and abs(r[0][1] - 40) <= 2
    assert len(doc.ops) == 1 and doc.ops[0][3] == today
    assert v.tool == "object" and v.selected_object()[1]["kind"] == "date"
    v.viewport().repaint()
    bar = v._object_bar
    assert bar.isVisible() and not bar._w.isVisible() and bar.copy_btn.isVisible()
    # one of them moved, the others stay
    o = v.selected_object()[1]
    x0, y0, x1, y1 = o["rect"]
    from test_objects import _drag
    _drag(v, ((x0 + x1) / 2, (y0 + y1) / 2), ((x0 + x1) / 2 - 300, (y0 + y1) / 2 + 400))
    idle(doc)
    assert _rects(doc, 0)[0][:2] == (round(x0 - 300), round(y0 + 400))
    assert abs(_rects(doc, 1)[0][0] - 420) <= 2
    v.viewport().repaint()
    v._object_bar.delete.emit()
    idle(doc)
    assert _rects(doc, 0) == [] and len(_rects(doc, 2)) == 1


def test_only_the_tool_in_use_is_checked(win, tmp_path, monkeypatch):  # noqa: F811
    from test_ui import open_doc
    doc = open_doc(win, pages(tmp_path))
    win.tool_acts["date"].trigger()
    doc.set_tool("object")  # changed by the program, as after putting a date in
    win._sync()
    checked = [k for k, a in win.tool_acts.items() if a.isChecked()]
    assert checked == ["object"]
