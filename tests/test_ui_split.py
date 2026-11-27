"""ჩართალა: one document in two panes. One engine, one undo; each pane its own place."""
import gc
import os
import sys
import time
import weakref

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
from test_pages import build
from test_ui import app, open_doc, vp, wait, win  # noqa: F401
from test_ui_pages import idle, read

from furceli.view import _VIEWS


@pytest.fixture
def doc(win, tmp_path):  # noqa: F811
    return open_doc(win, build(tmp_path / "ექვსი.pdf"))


def ready(v):
    v.viewport().repaint()
    want = v._request()
    return want and all(k in v._tiles or k in v._bad for k in want)


def split(win, doc, act=None):  # noqa: F811
    (act or win.a_split_v).trigger()
    assert len(doc.views) == 2
    a, b = doc.views
    wait(lambda: ready(a) and ready(b))
    return a, b


def drag_in(v, pno, *pts):
    """Press, move through and release at page points of one pane."""
    at = [vp(v, pno, *p) for p in pts]
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=at[0])
    for p in at[1:]:
        q = QPointF(p)
        QApplication.sendEvent(v.viewport(), QMouseEvent(
            QEvent.Type.MouseMove, q, v.viewport().mapToGlobal(q), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=at[-1])


def squares(doc, pno=0):
    return [a for a in doc._quiet("annots", pno) if a["type"] == "Square"]


def crash(doc):
    seen = []
    doc.client.crashed.connect(lambda: seen.append(1))
    doc.client.submit(("boom", time.time()), 0, "_crash")
    wait(lambda: seen)


def menu(win, title):  # noqa: F811
    return next(a.menu() for a in win.menuBar().actions() if a.text() == title)


def test_split_menu(win, doc):  # noqa: F811
    acts = [a for a in menu(win, "ჩართალა").actions()]
    sub = next(a.menu() for a in acts if a.menu() and a.text() == "ეკრანის გაყოფა")
    assert sub.actions() == [win.a_split_v, win.a_split_h, win.a_split_off]
    assert win.a_split_v.isEnabled() and not win.a_split_off.isEnabled()
    assert not win.a_link.isEnabled()          # one pane: nothing to move together


def test_split_turn_and_close(win, doc):  # noqa: F811
    first = doc.view
    a, b = split(win, doc)
    assert a is first and doc.panes.orientation() == Qt.Orientation.Horizontal
    # split, each pane is free: a whole sheet here, a detail there (the person's choice)
    assert win.a_split_off.isEnabled() and win.a_link.isEnabled() and not win.a_link.isChecked()
    win.a_split_h.trigger()                    # turned, not doubled
    assert doc.views == [a, b] and doc.panes.orientation() == Qt.Orientation.Vertical
    win.a_split_off.trigger()
    assert doc.views == [a] and doc.view is a and doc.panes.count() == 1
    assert b not in a.shared.views and not win.a_link.isEnabled()


def test_closing_the_split_keeps_the_pane_worked_in(win, doc):  # noqa: F811
    a, b = split(win, doc)
    b.go_to(4)
    QTest.mouseClick(b.viewport(), Qt.MouseButton.LeftButton, pos=vp(b, 4, 500, 700))
    assert doc.view is b
    win.a_split_off.trigger()
    assert doc.views == [b] and doc.view is b and b.page == 4 and b.shared.views == [b]
    doc._mark(4, (100, 100, 200, 150))         # the pane left is the first one now
    doc.edit(("order", [1, 2, 3, 4, 5]))
    idle(doc)
    assert b.marks == {3: [(100, 100, 200, 150)]}


def test_closing_the_split_leaves_nothing_behind(win, doc, monkeypatch):  # noqa: F811
    a, b = split(win, doc)
    win.a_link.trigger()                       # together: the link is busy with both
    gone = weakref.ref(b)
    win.a_split_off.trigger()
    del b
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *e: errors.append(e))
    a.verticalScrollBar().setValue(a.verticalScrollBar().value() + 500)
    a.set_zoom(1.5)
    assert not errors, errors                  # nothing calls a pane that is gone
    for _ in range(3):
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        gc.collect()
    assert gone() is None and all(v is not None for v in list(_VIEWS))


def test_a_rectangle_drawn_in_the_second_pane(win, doc):  # noqa: F811
    a, b = split(win, doc)
    win.tool_acts["rect"].trigger()
    assert a.tool == b.tool == "rect"
    drag_in(b, 0, (300, 400), (420, 480))
    idle(doc)
    assert doc.view is b                       # the pane drawn in (amendment 3)
    assert len(squares(doc)) == 1 and a.rev == b.rev
    wait(lambda: ready(a) and ready(b))        # both show the new state
    win.tool_acts["select"].trigger()
    assert a.tool == b.tool == "select"


def test_undo_and_redo_from_the_other_pane(win, doc):  # noqa: F811
    a, b = split(win, doc)
    win.tool_acts["rect"].trigger()
    drag_in(b, 0, (300, 400), (420, 480))
    idle(doc)
    win.tool_acts["select"].trigger()
    QTest.mouseClick(a.viewport(), Qt.MouseButton.LeftButton, pos=vp(a, 0, 500, 700))
    assert doc.view is a
    win.a_undo.trigger()
    idle(doc)
    assert squares(doc) == [] and a.rev == b.rev
    win.a_redo.trigger()
    idle(doc)
    assert len(squares(doc)) == 1


def test_panes_move_together_and_apart(win, doc):  # noqa: F811
    a, b = split(win, doc)
    assert not doc.linked
    win.a_link.trigger()                       # together
    assert doc.linked and win.a_link.isChecked()
    a.set_zoom(2.0)
    assert abs(b.zoom - 2.0) < 1e-3
    bar = a.verticalScrollBar()
    bar.setValue(bar.value() + 900)
    assert b.page == a.page and abs(b.verticalScrollBar().value() - bar.value()) < 60
    win.a_pgdn.trigger()
    assert b.page == a.page
    win.a_link.trigger()                       # apart: a whole page here, a detail there
    assert not doc.linked
    now, zoom = b.verticalScrollBar().value(), b.zoom
    bar.setValue(bar.value() + 700)
    a.set_zoom(0.5)
    assert b.verticalScrollBar().value() == now and b.zoom == zoom
    win.a_link.trigger()
    assert doc.linked and abs(b.zoom - 0.5) < 1e-3


def test_turned_apart_at_once_stays_apart(win, doc):  # noqa: F811
    """Moving together was switched on a moment after splitting, even when the person had
    switched it off in between (seen on a real drawing)."""
    win.a_split_v.trigger()
    win.a_link.trigger()                       # on and off at once, before anything is drawn
    win.a_link.trigger()
    a, b = doc.views
    wait(lambda: ready(a) and ready(b))
    assert not doc.linked and not doc._link.on
    b.set_zoom(2.5)
    a.set_fit("page")
    QApplication.processEvents()
    assert abs(b.zoom - 2.5) < 1e-3 and abs(a.zoom - 2.5) > .1


def test_two_zooms_both_get_drawn(win, doc):  # noqa: F811
    a, b = split(win, doc)
    b.set_zoom(3.0)
    a.set_fit("page")
    wait(lambda: ready(a) and ready(b), 30)
    assert any(k[2] == b.scale for k in b._tiles) and any(k[2] == a.scale for k in a._tiles)


def test_one_pane_does_not_take_back_what_the_other_asked_for(win, doc):  # noqa: F811
    a, b = split(win, doc)
    b.set_zoom(3.0)
    doc.client.submit(("nap", time.time()), 0, "_sleep", 1.0)  # the engine is busy a while
    wait(lambda: doc.client._current is not None and doc.client._current[0] == "nap")
    wanted_b = [k for k in b._request() if k not in b._tiles]
    assert wanted_b and all(k in doc.client._pending for k in wanted_b)
    a.set_fit("page")
    a._request()                               # the other pane asks for its own tiles
    assert all(k in doc.client._pending or k in b._tiles for k in wanted_b)


def test_redaction_marks_follow_their_page_once(win, doc):  # noqa: F811
    doc._mark(3, (100, 100, 200, 150))
    a, b = split(win, doc)
    assert b.marks is a.marks
    doc.edit(("order", [1, 2, 3, 4, 5]))       # page 1 went: page 4 is page 3 now
    idle(doc)
    assert a.marks == {2: [(100, 100, 200, 150)]} and b.marks is a.marks
    assert not doc.banner.message()


def test_search_hits_show_in_both(win, doc):  # noqa: F811
    a, b = split(win, doc)
    doc.query.setText("ASECRET3")
    doc.start_search()
    wait(lambda: doc._left == 0)
    assert a.hits and b.hits is a.hits and 3 in b.hits
    painted = []
    doc.update_views = lambda: painted.append(1)
    doc._select_hit((3, 0))                    # the current hit is both panes' business
    assert painted


def test_a_form_field_filled_from_the_second_pane(win, doc, monkeypatch):  # noqa: F811
    a, b = split(win, doc)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("ახალი", True))
    wait(lambda: 0 in b.widgets)
    QTest.mouseClick(b.viewport(), Qt.MouseButton.LeftButton, pos=vp(b, 0, 150, 210))
    idle(doc)
    assert doc.view is b and doc._quiet("widgets", 0)[0]["value"] == "ახალი"


def test_saving_writes_one_file_with_the_comment(win, doc, tmp_path, monkeypatch):  # noqa: F811
    original = doc.path
    before = read(original)
    a, b = split(win, doc)
    win.tool_acts["rect"].trigger()
    drag_in(b, 0, (300, 400), (420, 480))
    idle(doc)
    out = str(tmp_path / "შენახული.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (out, ""))
    assert doc.save_as()
    d = pymupdf.open(out)
    assert [x.type[1] for x in d[0].annots()].count("Square") == 1   # a real PDF comment
    assert read(original) == before and not doc.dirty
    assert os.path.normcase(doc.path) == os.path.normcase(out)


def test_after_a_crash_both_panes_come_back(win, doc):  # noqa: F811
    a, b = split(win, doc)
    win.tool_acts["rect"].trigger()
    drag_in(b, 0, (300, 400), (420, 480))
    idle(doc)
    crash(doc)
    a.go_to(1)
    b.go_to(0)
    wait(lambda: ready(a) and ready(b))
    assert len(squares(doc)) == 1 and doc.ops and not doc.broken


def test_colour_and_tool_are_one_for_both(win, doc):  # noqa: F811
    a, b = split(win, doc)
    win._set_color(QColor(0, 128, 0))
    assert a.tool_color == b.tool_color == QColor(0, 128, 0)
    win.tool_acts["ellipse"].trigger()
    assert a.tool == b.tool == "ellipse"


def test_selection_and_drawing_stay_in_their_pane(win, doc):  # noqa: F811
    a, b = split(win, doc)
    win.tool_acts["rect"].trigger()
    at = vp(b, 0, 300, 400)
    QTest.mousePress(b.viewport(), Qt.MouseButton.LeftButton, pos=at)
    assert b._shape and not a._shape           # what is being drawn is seen only where it is
    QTest.keyClick(b, Qt.Key.Key_Escape)       # first Esc: the shape goes, the pen stays
    assert not b._shape and a.tool == b.tool == "rect"
    QTest.keyClick(b, Qt.Key.Key_Escape)       # second: the pen goes, in both
    assert a.tool == b.tool == "select"
    QTest.mouseClick(b.viewport(), Qt.MouseButton.LeftButton, pos=vp(b, 0, 200, 72))
    QTest.mouseDClick(b.viewport(), Qt.MouseButton.LeftButton, pos=vp(b, 0, 120, 66))
    assert b.has_selection() and not a.has_selection()


def test_page_box_and_zoom_follow_the_active_pane(win, doc):  # noqa: F811
    a, b = split(win, doc)
    b.go_to(4)
    QTest.mouseClick(b.viewport(), Qt.MouseButton.LeftButton, pos=vp(b, 4, 500, 700))
    assert doc.view is b and win.page_box.value() == 5
    QTest.mouseClick(a.viewport(), Qt.MouseButton.LeftButton, pos=vp(a, a.page, 500, 700))
    assert doc.view is a and win.page_box.value() == a.page + 1


# -- step 5: the revision cloud ---------------------------------------------------------
def clouds(doc, pno=0):
    return [a for a in doc._quiet("annots", pno) if a["type"] == "Polygon"]


def test_the_cloud_tool_draws_a_cloud_in_either_pane(win, doc):  # noqa: F811
    cloud = win.tool_acts["cloud"]
    assert cloud in win.toolbar2.actions() and cloud in menu(win, "კომენტარი").actions()
    assert cloud.isEnabled() and cloud.toolTip() == "ღრუბელი"
    cloud.trigger()
    drag_in(doc.view, 0, (300, 400), (420, 480))
    idle(doc)
    assert len(clouds(doc)) == 1
    a, b = split(win, doc)
    drag_in(b, 0, (100, 500), (220, 560))
    idle(doc)
    assert len(clouds(doc)) == 2 and doc.view is b
    win.a_undo.trigger()
    idle(doc)
    assert len(clouds(doc)) == 1
    doc.sidebar.show()
    doc.sidebar.setCurrentWidget(doc.comments)
    doc.load_comments()
    wait(lambda: doc.comments.rev == doc.view.rev)
    texts = [doc.comments.list.item(i).text() for i in range(doc.comments.list.count())]
    assert sum("ღრუბელი" in t for t in texts) == 1


def test_a_cloud_too_small_is_not_made(win, doc):  # noqa: F811
    win.tool_acts["cloud"].trigger()
    drag_in(doc.view, 0, (300, 400), (302, 401))
    idle(doc)
    assert clouds(doc) == [] and not doc.ops
