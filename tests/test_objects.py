"""Editing what is already on a page, as in Acrobat: pictures moved, resized, replaced,
deleted (HANDOFF.md, item 7)."""
import pymupdf
import pytest
from PIL import Image
from pymupdf import Rect

from test_features import engine, saved
from test_ui import app, vp, win  # noqa: F401

from furceli.engine import EngineError


def _png(tmp_path, name, size=(120, 60), color=(0, 0, 200)):
    path = str(tmp_path / name)
    Image.new("RGB", size, color).save(path)
    return path


def page_with_picture(tmp_path, rot=0, text_over=False):
    """A page displayed 600x800, a picture at (100, 100, 220, 160) as displayed."""
    d = pymupdf.open()
    w, h = (600, 800) if rot in (0, 180) else (800, 600)
    p = d.new_page(width=w, height=h)
    p.set_rotation(rot)
    m = p.derotation_matrix
    p.insert_image(Rect(100, 100, 220, 160) * m, filename=_png(tmp_path, "a.png"), rotate=rot)
    if text_over:  # written after the picture: it is on top and must stay on top
        p.insert_text(pymupdf.Point(110, 135) * m, "OVER", fontsize=20, rotate=rot,
                      color=(1, 0, 0))
    path = str(tmp_path / f"pic{rot}.pdf")
    d.save(path)
    return path


def close(a, b, tol=1.0):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.mark.parametrize("rot", [0, 90])
def test_pictures_are_listed_where_they_are_shown(tmp_path, rot):
    e = engine(page_with_picture(tmp_path, rot))
    pics = e.images(0)
    assert len(pics) == 1 and pics[0]["movable"]
    assert close(pics[0]["rect"], (100, 100, 220, 160))


@pytest.mark.parametrize("rot", [0, 90])
def test_a_picture_moves_and_resizes(tmp_path, rot):
    e = engine(page_with_picture(tmp_path, rot))
    pic = e.images(0)[0]
    e.apply(("image_move", 0, pic["xref"], pic["rect"], (300, 400, 540, 520)))
    assert close(e.images(0)[0]["rect"], (300, 400, 540, 520))
    d = saved(e, tmp_path)
    shown = [Rect(i["bbox"]) * d[0].rotation_matrix for i in d[0].get_image_info()]
    assert len(shown) == 1 and close(shown[0].normalize(), (300, 400, 540, 520))
    pix = d[0].get_pixmap(dpi=72)
    assert pix.pixel(420, 460) == (0, 0, 200) and pix.pixel(160, 130) == (255, 255, 255)


def test_moving_keeps_what_was_drawn_over_the_picture_on_top(tmp_path):
    e = engine(page_with_picture(tmp_path, text_over=True))
    pic = e.images(0)[0]
    e.apply(("image_move", 0, pic["xref"], pic["rect"], (90, 90, 250, 170)))
    pix = saved(e, tmp_path)[0].get_pixmap(dpi=144)
    reds = sum(1 for x in range(220, 400) for y in range(240, 280)
               if pix.pixel(x, y)[0] > 200 and pix.pixel(x, y)[2] < 80)
    assert reds > 50  # the red word is still drawn over the picture


def test_a_picture_is_deleted_and_undo_brings_it_back(tmp_path):
    src = page_with_picture(tmp_path)
    e = engine(src)
    pic = e.images(0)[0]
    op = ("image_del", 0, pic["xref"], pic["rect"])
    e.apply(op)
    assert e.images(0) == []
    d = saved(e, tmp_path)
    assert d[0].get_image_info() == []
    e2 = engine(src)  # undo = the operations again on the original, without this one
    e2.replay(src, "", [])
    assert len(e2.images(0)) == 1


def test_a_picture_is_replaced_and_keeps_its_proportion(tmp_path):
    e = engine(page_with_picture(tmp_path))
    pic = e.images(0)[0]
    square = _png(tmp_path, "sq.png", (50, 50), (0, 160, 0))
    e.apply(("image_swap", 0, pic["xref"], pic["rect"], square))
    r = e.images(0)[0]["rect"]
    assert close((r[2] - r[0], r[3] - r[1]), (60, 60))  # fitted inside 120x60, centred
    assert close(r, (130, 100, 190, 160))
    pix = saved(e, tmp_path)[0].get_pixmap(dpi=72)
    assert pix.pixel(160, 130) == (0, 160, 0)


def test_a_stale_picture_is_refused(tmp_path):
    e = engine(page_with_picture(tmp_path))
    pic = e.images(0)[0]
    with pytest.raises(EngineError, match="stale"):
        e.apply(("image_move", 0, pic["xref"], (0, 0, 10, 10), (300, 400, 420, 460)))


def test_a_picture_used_twice_is_not_half_changed(tmp_path):
    d = pymupdf.open()
    p = d.new_page(width=600, height=800)
    path = _png(tmp_path, "a.png")
    x = p.insert_image(Rect(100, 100, 220, 160), filename=path)
    p.insert_image(Rect(100, 300, 220, 360), xref=x)
    src = str(tmp_path / "twice.pdf")
    d.save(src)
    e = engine(src)
    pics = e.images(0)
    assert len(pics) == 2
    pic = pics[0]
    if pic["movable"]:
        e.apply(("image_move", 0, pic["xref"], pic["rect"], (300, 500, 420, 560)))
        rects = sorted(tuple(round(v) for v in i["rect"]) for i in e.images(0))
        assert (300, 500, 420, 560) in rects and len(rects) == 2  # only that one moved
    with pytest.raises(EngineError, match="image_shared"):
        e.apply(("image_swap", 0, pic["xref"], e.images(0)[0]["rect"], path))


# -- the "objects" tool in the window ------------------------------------------------------
def _drag(v, a, b):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from test_ui import vp
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, 0, *a))
    p = QPointF(vp(v, 0, *b))
    QApplication.sendEvent(v.viewport(), QMouseEvent(
        QEvent.Type.MouseMove, p, v.viewport().mapToGlobal(p), Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, 0, *b))


def _rects(doc):
    return [tuple(round(x) for x in i["rect"]) for i in doc._quiet("images", 0)]


def test_the_objects_tool_moves_resizes_and_deletes_a_picture(win, tmp_path):  # noqa: F811
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from test_ui import open_doc
    from test_ui_pages import idle
    doc = open_doc(win, page_with_picture(tmp_path))
    win.tool_acts["object"].trigger()
    v = doc.view
    assert v.tool == "object" and win.tool_acts["object"].isEnabled()
    _drag(v, (160, 130), (360, 330))  # the middle of the picture, 200 right and down
    idle(doc)
    assert _rects(doc) == [(300, 300, 420, 360)]
    assert doc.ops[-1][0] == "image_move"
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, 0, 360, 330))
    assert v.selected_object() is not None
    _drag(v, (420, 360), (540, 420))  # the lower right corner: twice as big, same proportion
    idle(doc)
    assert _rects(doc) == [(300, 300, 540, 420)]
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, 0, 400, 350))
    QTest.keyClick(v, Qt.Key.Key_Delete)
    idle(doc)
    assert _rects(doc) == []
    doc.undo()
    idle(doc)
    assert _rects(doc) == [(300, 300, 540, 420)]


def test_the_menu_replaces_a_picture(win, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QFileDialog, QMenu
    from test_ui import open_doc
    from test_ui_pages import idle
    doc = open_doc(win, page_with_picture(tmp_path))
    win.tool_acts["object"].trigger()
    square = _png(tmp_path, "sq.png", (50, 50), (0, 160, 0))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (square, ""))
    seen = []

    class Menu(QMenu):  # chooses "replace" instead of waiting for the person
        def exec(self, *a):
            seen.extend(x.text() for x in self.actions())
            next(x for x in self.actions() if x.text() == "სურათის ჩანაცვლება…").trigger()
    from furceli import tools
    monkeypatch.setattr(tools, "QMenu", Menu)
    doc.view_menu(0, QPointF(160, 130), None)
    idle(doc)
    assert "სურათის წაშლა" in seen
    assert _rects(doc) == [(130, 100, 190, 160)]
