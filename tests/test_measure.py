"""Measuring drawings to scale (design canvas "ETRATI: მასშტაბით გაზომვა").

The numbers are checked by hand: 100 mm on paper at 1:100 is 10.00 m; a 100 x 50 mm
rectangle at 1:100 is 10 x 5 m, so 50.00 m² with a perimeter of 30.00 m."""
import pymupdf
import pytest

from test_features import engine, saved
from test_ui import app, vp, win  # noqa: F401

MM = 72 / 25.4  # points in a millimetre
M_PER_PT = 0.0254 / 72  # metres of paper in a point


def drawing(tmp_path, rot=0, pages=2):
    d = pymupdf.open()
    for _ in range(pages):
        p = d.new_page(width=842 if rot in (0, 180) else 595, height=595 if rot in (0, 180) else 842)
        p.set_rotation(rot)
    path = str(tmp_path / f"plan{rot}.pdf")
    d.save(path)
    return path


def at_1_100(e, pages=(0,)):
    e.apply(("scale", list(pages), M_PER_PT * 100, "m", 2, "1:100"))


def measures(e):
    return e.measures()


def test_a_scale_is_kept_in_the_file(tmp_path):
    e = engine(drawing(tmp_path))
    assert e.scale(0) is None
    at_1_100(e, [0, 1])
    s = e.scale(1)
    assert s["unit"] == "m" and s["digits"] == 2 and s["label"] == "1:100"
    assert abs(s["factor"] - M_PER_PT * 100) < 1e-12
    d = saved(e, tmp_path)
    vp_ = d.xref_get_key(d[0].xref, "VP")[1]
    assert "/Measure" in vp_ and "/RL" in vp_  # the standard place: Acrobat and Bluebeam read it
    again = engine(str(tmp_path / "out.pdf"))
    assert abs(again.scale(0)["factor"] - M_PER_PT * 100) < 1e-12


@pytest.mark.parametrize("rot", [0, 90])
def test_a_length_at_1_100(tmp_path, rot):
    e = engine(drawing(tmp_path, rot))
    at_1_100(e)
    e.apply(("measure", 0, "distance", [(100, 300), (100 + 100 * MM, 300)], ""))
    got = measures(e)
    assert len(got) == 1 and got[0]["kind"] == "distance" and got[0]["page"] == 0
    assert abs(got[0]["value"] - 10.0) < 1e-4 and got[0]["text"] == "10.00 მ"
    d = saved(e, tmp_path)
    page = d[0]
    a = next(page.annots())
    assert a.type[1] == "Line" and a.info["content"] == "10.00 მ"
    assert d.xref_get_key(a.xref, "IT")[1] == "/LineDimension"
    assert "/Measure" in d.xref_object(a.xref)


def test_an_area_and_its_perimeter(tmp_path):
    e = engine(drawing(tmp_path))
    at_1_100(e)
    x, y = 100, 100
    pts = [(x, y), (x + 100 * MM, y), (x + 100 * MM, y + 50 * MM), (x, y + 50 * MM)]
    e.apply(("measure", 0, "area", pts, "ოთახი 1"))
    m = measures(e)[0]
    assert abs(m["value"] - 50.0) < 1e-4 and abs(m["perimeter"] - 30.0) < 1e-4
    assert m["text"] == "ოთახი 1: 50.00 მ² · 30.00 მ" and m["note"] == "ოთახი 1"
    page = saved(e, tmp_path)[0]
    assert next(page.annots()).type[1] == "Polygon"


def test_a_path_adds_its_pieces(tmp_path):
    e = engine(drawing(tmp_path))
    at_1_100(e)
    pts = [(100, 100), (100 + 30 * MM, 100), (100 + 30 * MM, 100 + 40 * MM)]  # 3 m + 4 m
    e.apply(("measure", 0, "path", pts, ""))
    assert abs(measures(e)[0]["value"] - 7.0) < 1e-4


def test_without_a_scale_paper_millimetres(tmp_path):
    e = engine(drawing(tmp_path))
    e.apply(("measure", 0, "distance", [(100, 300), (100 + 100 * MM, 300)], ""))
    m = measures(e)[0]
    assert abs(m["value"] - 100.0) < 1e-4 and m["text"] == "100.0 მმ (ქაღალდზე)"


def test_a_new_scale_counts_the_measurements_again(tmp_path):
    e = engine(drawing(tmp_path))
    at_1_100(e)
    e.apply(("measure", 0, "distance", [(100, 300), (100 + 100 * MM, 300)], ""))
    e.apply(("scale", [0], M_PER_PT * 50, "cm", 1, "1:50"))
    m = measures(e)[0]
    assert abs(m["value"] - 500.0) < 1e-4 and m["text"] == "500.0 სმ"
    page = saved(e, tmp_path)[0]
    assert next(page.annots()).info["content"] == "500.0 სმ"


def test_a_note_is_written_into_the_label(tmp_path):
    e = engine(drawing(tmp_path))
    at_1_100(e)
    e.apply(("measure", 0, "distance", [(100, 300), (100 + 100 * MM, 300)], ""))
    e.apply(("measure_note", 0, measures(e)[0]["index"], "კედელი"))
    assert measures(e)[0]["text"] == "კედელი: 10.00 მ"


def test_the_label_is_drawn_on_the_page(tmp_path):
    e = engine(drawing(tmp_path))
    at_1_100(e)
    e.apply(("measure", 0, "distance", [(100, 300), (100 + 100 * MM, 300)], ""))
    pix = saved(e, tmp_path)[0].get_pixmap(dpi=72, annots=True)
    line = pix.pixel(200, 300)
    assert line[0] > 150 and line[1] < 100  # the red line
    inked = sum(1 for x in range(226, 258) for y in range(284, 294) if sum(pix.pixel(x, y)) < 700)
    assert inked > 20  # the letters of "10.00 მ" in their box above it


def test_the_list_counts_every_page(tmp_path):
    e = engine(drawing(tmp_path))
    at_1_100(e, [0, 1])
    e.apply(("measure", 0, "distance", [(100, 300), (100 + 100 * MM, 300)], ""))
    e.apply(("measure", 1, "distance", [(100, 300), (100 + 50 * MM, 300)], ""))
    got = sorted((m["page"], round(m["value"], 6)) for m in measures(e))
    assert got == [(0, 10.0), (1, 5.0)]


# -- the window --------------------------------------------------------------------------
def _click(v, pno, x, y, double=False):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    if double:
        QTest.mouseDClick(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, pno, x, y))
    else:
        QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, pno, x, y))


def _scaled_doc(win, tmp_path):
    from test_ui import open_doc
    from test_ui_pages import idle
    doc = open_doc(win, drawing(tmp_path))
    doc.edit(("scale", [0, 1], M_PER_PT * 100, "m", 2, "1:100"))
    idle(doc)
    return doc


def test_the_scale_dialog_gives_a_ratio_or_a_known_length(app):  # noqa: F811
    from furceli import measure_ui
    d = measure_ui.ScaleDialog(0, 3)
    d.ratio.setValue(50)
    factor, unit, digits, label, pages = d.result_scale()
    assert abs(factor - M_PER_PT * 50) < 1e-12 and (unit, digits, label, pages) == ("m", 2, "1:50", [0])
    k = measure_ui.ScaleDialog(1, 3, drawn=100 * MM)  # a line of 100 mm on paper was drawn
    assert k.tabs.currentIndex() == 1
    k.real.setValue(6)
    k.all_pages.setChecked(True)
    factor, unit, digits, label, pages = k.result_scale()
    assert abs(factor - 6 / (100 * MM)) < 1e-9 and label == "≈ 1:60" and pages == [0, 1, 2]
    assert "1:60" in k.drawn_label.text() and "100.0 მმ" in k.drawn_label.text()


def test_measuring_a_length_an_path_and_an_area_with_the_mouse(win, tmp_path):  # noqa: F811
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from test_ui_pages import idle
    doc = _scaled_doc(win, tmp_path)
    v = doc.view
    win.tool_acts["m_distance"].trigger()
    assert win.a_scale.text() == "მასშტაბი: 1:100 · მ"
    _click(v, 0, 100, 300)
    _click(v, 0, 100 + 100 * MM, 300)
    idle(doc)
    win.tool_acts["m_path"].trigger()
    for x, y in ((100, 400), (100 + 30 * MM, 400), (100 + 30 * MM, 400 + 40 * MM)):
        _click(v, 0, x, y)
    QTest.keyClick(v, Qt.Key.Key_Return)
    idle(doc)
    win.tool_acts["m_area"].trigger()
    x, y = 450, 100
    _click(v, 0, x, y)
    _click(v, 0, x + 100 * MM, y)
    _click(v, 0, x + 100 * MM, y + 50 * MM)
    _click(v, 0, x, y + 50 * MM)
    _click(v, 0, x, y + 50 * MM, double=True)
    idle(doc)
    got = sorted((m["kind"], m["value"]) for m in doc._quiet("measures"))
    # clicks land on whole screen pixels: within a few centimetres, as zooming in makes exact
    assert [k for k, _ in got] == ["area", "distance", "path"]
    for (_, value), want in zip(got, (50.0, 10.0, 7.0)):
        assert abs(value - want) / want < .006
    assert v.tool == "m_area"  # the tool stays for the next one


def test_shift_keeps_a_line_straight(win, tmp_path):  # noqa: F811
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    doc = _scaled_doc(win, tmp_path)
    v = doc.view
    win.tool_acts["m_distance"].trigger()
    shift = Qt.KeyboardModifier.ShiftModifier
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, shift, vp(v, 0, 100, 300))
    p = QPointF(vp(v, 0, 300, 330))  # 30 points off level: Shift makes it level
    QApplication.sendEvent(v.viewport(), QMouseEvent(
        QEvent.Type.MouseMove, p, v.viewport().mapToGlobal(p), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, shift))
    first, now = v._measure[1][0], v._measure[2]
    assert abs(now.y() - first.y()) < .01 and now.x() > first.x() + 150
    assert v.measure_text("distance", [first, now], 0).endswith(" მ")  # the live value


def test_calibrating_with_a_known_length(win, tmp_path, monkeypatch):  # noqa: F811
    from test_ui import open_doc
    from test_ui_pages import idle
    from furceli import measure_ui
    doc = open_doc(win, drawing(tmp_path))
    answers = iter([measure_ui.DRAW, True])

    def run(dlg):
        r = next(answers)
        if r is True:
            assert dlg.tabs.currentIndex() == 1 and abs(dlg._drawn - 100 * MM) < .6
            dlg.real.setValue(6)
            dlg.real_unit.setCurrentIndex(0)  # metres
        return r
    monkeypatch.setattr(measure_ui.ScaleDialog, "exec", run)
    win.a_scale.trigger()
    assert doc.view.tool == "m_calibrate"
    v = doc.view
    _click(v, 0, 100, 300)
    _click(v, 0, 100 + 100 * MM, 300)
    idle(doc)
    s = doc._quiet("scale", 0)
    assert abs(s["factor"] / (6 / (100 * MM)) - 1) < .004 and s["label"] == "≈ 1:60"
    assert doc.view.tool == "select"


def test_without_a_scale_the_strip_offers_one(win, tmp_path):  # noqa: F811
    from test_ui import open_doc
    doc = open_doc(win, drawing(tmp_path))
    win.tool_acts["m_distance"].trigger()
    assert "მასშტაბი არ არის დაყენებული" in doc.banner.message()


def test_a_note_and_the_list(win, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QInputDialog
    from test_ui_pages import idle
    from furceli import measure_ui, tools
    doc = _scaled_doc(win, tmp_path)
    x, y = 100, 100
    doc.edit(("measure", 0, "area", [(x, y), (x + 100 * MM, y), (x + 100 * MM, y + 50 * MM),
                                     (x, y + 50 * MM)], ""))
    idle(doc)
    doc.edit(("measure", 1, "distance", [(100, 300), (100 + 100 * MM, 300)], ""))
    idle(doc)
    a = [c for c in doc._quiet("annots", 0) if c["measure"]][0]
    assert tools.kind_name(a) == "ფართობი"
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("ოთახი 1", True))
    doc.note_measure(0, a)
    idle(doc)
    items = doc._quiet("measures")
    assert items[0]["text"] == "ოთახი 1: 50.00 მ² · 30.00 მ"
    lst = measure_ui.MeasureList(items)
    assert lst.table.rowCount() == 2 and lst.table.item(0, 2).text() == "ოთახი 1"
    assert lst.table.item(0, 3).text() == "50.00 მ² · 30.00 მ"
    assert "ფართობი: 50.00 მ²" in lst.totals.text() and "სიგრძე და ტეხილი: 10.00 მ" in lst.totals.text()
    out = tmp_path / "m.xlsx"
    assert lst.save_excel(str(out))
    from openpyxl import load_workbook
    rows = list(load_workbook(out).active.values)
    assert rows[1][:5] == (1, "ფართობი", "ოთახი 1", 50.0, "მ²") and rows[2][3] == 10.0


# -- snapping to the drawing's own lines --------------------------------------------------
def plan(tmp_path, rot=0):
    """A room 100 x 200 points as shown, a wall line across it from (100, 200) to (400, 250)."""
    d = pymupdf.open()
    p = d.new_page(width=842 if rot in (0, 180) else 595, height=595 if rot in (0, 180) else 842)
    p.set_rotation(rot)
    m = p.derotation_matrix
    p.draw_rect(pymupdf.Rect(100, 100, 400, 300) * m, width=2)
    p.draw_line(pymupdf.Point(100, 200) * m, pymupdf.Point(400, 250) * m)
    path = str(tmp_path / f"room{rot}.pdf")
    d.save(path)
    return path


@pytest.mark.parametrize("rot", [0, 90])
def test_the_drawing_gives_its_ends_and_crossings(tmp_path, rot):
    e = engine(plan(tmp_path, rot))
    s = e.snaps(0)
    ends = {(round(x), round(y)) for x, y, k in s["points"] if k == "end"}
    cross = {(round(x), round(y)) for x, y, k in s["points"] if k == "cross"}
    assert {(100, 100), (400, 100), (400, 300), (100, 300), (100, 200), (400, 250)} <= ends
    assert len(s["segments"]) >= 5


def test_crossings_inside_lines_are_found(tmp_path):
    d = pymupdf.open()
    p = d.new_page(width=600, height=600)
    p.draw_line((100, 100), (500, 500))
    p.draw_line((100, 500), (500, 100))
    path = str(tmp_path / "x.pdf")
    d.save(path)
    s = engine(path).snaps(0)
    assert (300, 300) in {(round(x), round(y)) for x, y, k in s["points"] if k == "cross"}


def test_a_click_near_a_corner_lands_on_it(win, tmp_path):  # noqa: F811
    from test_ui import open_doc
    from test_ui_pages import idle
    doc = open_doc(win, plan(tmp_path))
    doc.edit(("scale", [0], M_PER_PT * 100, "m", 2, "1:100"))
    idle(doc)
    win.tool_acts["m_distance"].trigger()
    v = doc.view
    _click(v, 0, 102, 98)   # a little off the corner (100, 100)
    _click(v, 0, 397, 103)  # a little off the corner (400, 100)
    idle(doc)
    m = doc._quiet("measures")[0]
    assert abs(m["value"] - 300 * M_PER_PT * 100) < 1e-6  # exactly 300 points: both corners


def test_alt_and_the_switch_turn_snapping_off(win, tmp_path):  # noqa: F811
    from PySide6.QtCore import QPointF
    from test_ui import open_doc
    doc = open_doc(win, plan(tmp_path))
    win.tool_acts["m_distance"].trigger()
    v = doc.view
    pt = QPointF(vp(v, 0, 102, 98)) * v.dpr
    q = v._measure_point(0, pt)
    assert (round(q.x(), 3), round(q.y(), 3)) == (100, 100) and v._snap[2] == "end"
    win.a_snap.setChecked(False)
    q = v._measure_point(0, pt)
    assert abs(q.x() - 100) > .5 and v._snap is None


def test_a_corner_pulls_harder_than_the_line_beside_it():
    from furceli import measure
    idx = measure.SnapIndex([(400, 100, "end")], [(100, 100, 400, 100), (400, 100, 400, 300)])
    assert idx.find(397, 103, 4) == (400, 100, "end")  # 4.2 from the corner, 3 from a line
    assert idx.find(300, 103, 4)[2] == "line"
    assert idx.find(300, 110, 4) is None


def test_the_nearest_point_wins_whatever_it_is():
    # a dimension's slanted tick: its ends lie off the line, where it crosses the line is wanted
    from furceli import measure
    idx = measure.SnapIndex([(98.6, 101.4, "end"), (101.4, 98.6, "end"), (100, 100, "cross")],
                            [(0, 100, 200, 100), (98.6, 101.4, 101.4, 98.6)])
    assert idx.find(100.3, 100.2, 4) == (100, 100, "cross")
    assert idx.find(98.8, 101.3, 4) == (98.6, 101.4, "end")


def test_moving_the_mouse_while_the_lines_load_does_not_start_another_load(win, tmp_path, monkeypatch):  # noqa: F811
    # Reading a page's lines waits for the engine with events still flowing: a mouse move
    # then asked for them again, and again, until the program fell over (stack overflow).
    from PySide6.QtCore import QPointF
    from test_ui import open_doc
    doc = open_doc(win, plan(tmp_path))
    win.tool_acts["m_distance"].trigger()
    v = doc.view
    calls = []
    real = type(doc)._load_snaps

    def load(self, view, pno):
        calls.append(pno)
        if len(calls) < 50:  # what the waiting loop does: the next mouse move arrives meanwhile
            view._measure_point(pno, QPointF(vp(view, 0, 150, 150)) * view.dpr)
        real(self, view, pno)
    monkeypatch.setattr(type(doc), "_load_snaps", load)
    v.snaps.clear()
    v._measure_point(0, QPointF(vp(v, 0, 102, 98)) * v.dpr)
    assert calls == [0]
    assert v._measure_point(0, QPointF(vp(v, 0, 102, 98)) * v.dpr).x() == 100  # loaded, snapping


def test_choosing_while_the_objects_load_does_not_start_another_load(win, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtCore import QPointF
    from test_ui import open_doc
    doc = open_doc(win, plan(tmp_path))
    win.tool_acts["object"].trigger()
    v = doc.view
    calls = []
    real = type(doc)._load_objects

    def load(self, view, pno):
        calls.append(pno)
        if len(calls) < 50:
            view._object_press(QPointF(vp(view, 0, 150, 150)) * view.dpr)
        real(self, view, pno)
    monkeypatch.setattr(type(doc), "_load_objects", load)
    v.objects.clear()
    v._object_press(QPointF(vp(v, 0, 150, 150)) * v.dpr)
    assert calls == [0]


def test_labels_of_measurements_are_not_lines_to_edit(tmp_path):
    # Clicking a measurement's label with "replace text" was refused with a puzzling warning:
    # the label belongs to the annotation, not to the page's text.
    e = engine(plan(tmp_path))
    at_1_100(e)
    e.apply(("measure", 0, "distance", [(100, 450), (100 + 100 * MM, 450)], "კედელი"))
    texts = [ln["text"] for ln in e.lines(0)]
    assert not any("კედელი" in t for t in texts)
    assert not any("კედელი" in " ".join(o.get("texts", [])) for o in e.objects(0))


def test_a_click_on_a_measurement_with_the_text_tool_explains(win, tmp_path):  # noqa: F811
    from test_ui import open_doc
    from test_ui_pages import idle
    doc = open_doc(win, plan(tmp_path))
    doc.edit(("measure", 0, "distance", [(100, 450), (100 + 100 * MM, 450)], "კედელი"))
    idle(doc)
    a = [c for c in doc._quiet("annots", 0) if c["measure"]][0]
    x0, y0, x1, y1 = a["rect"]
    doc.edit_line(0, ((x0 + x1) / 2, (y0 + y1) / 2 - 6))
    assert "გაზომვ" in doc.banner.message() and "შენიშვნა" in doc.banner.message()
