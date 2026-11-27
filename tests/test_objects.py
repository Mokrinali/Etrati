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


# -- rotating, and the same picture on other pages (design: ETRATI სურათი და თარიღი) ---------
def _strip_side(page, rect):
    """Where the blue strip of the test picture is (it marks the picture's left edge)."""
    pix = page.get_pixmap(dpi=72)
    x0, y0, x1, y1 = (int(v) for v in rect)
    blue = [(x, y) for x in range(x0 + 2, x1 - 2) for y in range(y0 + 2, y1 - 2)
            if pix.pixel(x, y)[2] > 150 and pix.pixel(x, y)[0] < 80]
    xs, ys = [p[0] for p in blue], [p[1] for p in blue]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if max(xs) - min(xs) < max(ys) - min(ys):  # a strip standing up: the left or right edge
        return "left" if sum(xs) / len(xs) < cx else "right"
    return "top" if sum(ys) / len(ys) < cy else "bottom"


def striped(tmp_path, rot=0, pages=3, sizes=None):
    """Pages displayed 600x800 (or `sizes`), the striped picture at (100, 100, 220, 160) on page 0."""
    d = pymupdf.open()
    for i in range(pages):
        w, h = (sizes or [(600, 800)] * pages)[i]
        p = d.new_page(width=w if rot in (0, 180) else h, height=h if rot in (0, 180) else w)
        p.set_rotation(rot)
    im = Image.new("RGB", (120, 60), "white")
    im.paste((0, 0, 200), (0, 0, 30, 60))
    path = str(tmp_path / "striped.png")
    im.save(path)
    p = d[0]
    p.insert_image(Rect(100, 100, 220, 160) * p.derotation_matrix, filename=path, rotate=rot)
    out = str(tmp_path / f"striped{rot}.pdf")
    d.save(out)
    return out


@pytest.mark.parametrize("rot", [0, 90])
def test_a_picture_turns_by_a_quarter_around_its_middle(tmp_path, rot):
    e = engine(striped(tmp_path, rot, pages=1))
    pic = e.images(0)[0]
    e.apply(("image_turn", 0, pic["xref"], pic["rect"], 90))  # clockwise, as shown
    r = e.images(0)[0]["rect"]
    assert close(r, (130, 70, 190, 190))  # 60 wide, 120 tall, same middle
    d = saved(e, tmp_path)
    assert _strip_side(d[0], r) == "top"  # the left edge went up: turned clockwise
    pic = engine(str(tmp_path / "out.pdf")).images(0)[0]
    e2 = engine(str(tmp_path / "out.pdf"))
    e2.apply(("image_turn", 0, pic["xref"], pic["rect"], -90))
    assert close(e2.images(0)[0]["rect"], (100, 100, 220, 160))


@pytest.mark.parametrize("rot", [0, 90])
def test_the_picture_goes_to_the_same_place_on_other_pages(tmp_path, rot):
    e = engine(striped(tmp_path, rot))
    pic = e.images(0)[0]
    e.apply(("image_copy", 0, pic["xref"], pic["rect"], [1, 2], [0, 0]))
    d = saved(e, tmp_path)
    for pno in (1, 2):
        got = engine(str(tmp_path / "out.pdf")).images(pno)
        assert len(got) == 1 and close(got[0]["rect"], (100, 100, 220, 160))
        assert _strip_side(d[pno], got[0]["rect"]) == "left"  # same way up
    assert len(engine(str(tmp_path / "out.pdf")).images(0)) == 1


def test_on_a_wider_page_the_distance_from_the_chosen_corner_stays(tmp_path):
    e = engine(striped(tmp_path, pages=2, sizes=[(600, 800), (800, 600)]))
    pic = e.images(0)[0]
    e.apply(("image_copy", 0, pic["xref"], pic["rect"], [1], [1, 0]))  # from the top right
    got = e.images(1)[0]["rect"]
    assert close(got, (800 - 380 - 120, 100, 800 - 380, 160))  # 380 from the right edge
    e.apply(("image_copy", 0, pic["xref"], pic["rect"], [1], [0, 1]))  # from the bottom left
    rects = sorted(tuple(round(v) for v in i["rect"]) for i in e.images(1))
    assert (100, 0, 220, 60) in rects  # 640 from the bottom would be off the page: kept on it


def test_copies_are_one_picture_in_the_file_but_move_on_their_own(tmp_path):
    e = engine(striped(tmp_path))
    pic = e.images(0)[0]
    e.apply(("image_copy", 0, pic["xref"], pic["rect"], [1, 2], [0, 0]))
    d = saved(e, tmp_path)
    xrefs = {i["xref"] for p in range(3) for i in engine(str(tmp_path / "out.pdf")).images(p)}
    assert len(xrefs) == 1  # stored once: the file does not grow per page
    e.apply(("image_move", 1, pic["xref"], (100, 100, 220, 160), (300, 500, 420, 560)))
    assert close(e.images(1)[0]["rect"], (300, 500, 420, 560))
    assert close(e.images(2)[0]["rect"], (100, 100, 220, 160))
    assert close(e.images(0)[0]["rect"], (100, 100, 220, 160))


def _open_striped(win, tmp_path, pages=3):
    from test_ui import open_doc
    doc = open_doc(win, striped(tmp_path, pages=pages))
    win.tool_acts["object"].trigger()
    return doc


def _choose(doc, x=160, y=130):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    v = doc.view
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, 0, x, y))
    v.viewport().repaint()
    return v


def test_the_bar_shows_millimetres_and_sets_an_exact_size(win, tmp_path):  # noqa: F811
    from test_ui_pages import idle
    doc = _open_striped(win, tmp_path, pages=1)
    v = _choose(doc)
    bar = v._object_bar
    assert bar.isVisible()
    assert abs(bar._w.value() - 120 * 25.4 / 72) < .1 and abs(bar._h.value() - 60 * 25.4 / 72) < .1
    bar._w.setValue(84.7)  # 240 pt; the lock keeps the proportion
    bar._edited(bar._w)
    idle(doc)
    assert _rects(doc) == [(100, 100, 340, 220)]
    assert v.selected_object() is not None  # still chosen after the edit
    bar = v._object_bar
    bar.lock.setChecked(False)
    bar._h.setValue(21.2)  # 60 pt, the width as it is
    bar._edited(bar._h)
    idle(doc)
    assert _rects(doc) == [(100, 100, 340, 160)]


def test_the_bar_turns_and_deletes(win, tmp_path):  # noqa: F811
    from test_ui_pages import idle
    doc = _open_striped(win, tmp_path, pages=1)
    v = _choose(doc)
    v._object_bar.turned.emit(90)
    idle(doc)
    assert _rects(doc) == [(130, 70, 190, 190)]
    v._object_bar.delete.emit()
    idle(doc)
    assert _rects(doc) == []


def test_other_pages_too_from_the_bar(win, tmp_path, monkeypatch):  # noqa: F811
    from test_ui_pages import idle
    from furceli import objects_ui
    doc = _open_striped(win, tmp_path, pages=3)
    v = _choose(doc)
    asked = []

    def run(dlg):
        asked.append((dlg.anchor(), dlg.all.isChecked()))
        return True
    monkeypatch.setattr(objects_ui.CopyDialog, "exec", run)
    v._object_bar.copy.emit()
    idle(doc)
    assert asked == [((0, 0), True)]  # the nearest corner, all pages, chosen for the person
    for pno in (1, 2):
        assert [tuple(round(x) for x in i["rect"]) for i in doc._quiet("images", pno)] == \
            [(100, 100, 220, 160)]
    assert len(doc.ops) == 1  # one step to undo


def test_the_copy_dialog_reads_the_pages(app):  # noqa: F811
    from furceli.objects_ui import CopyDialog
    d = CopyDialog(1, 12, (1, 0))
    assert d.anchor() == (1, 0) and d.all.isChecked()
    assert d.pages() == [p for p in range(12) if p != 1]
    d.only.setChecked(True)
    assert d.pages() == []
    d.some.setChecked(True)
    d.range.setText("2-5, 8")
    assert d.pages(quiet=True) == [2, 3, 4, 7]  # its own page (2) left out
    d.range.setText("99")
    assert d.pages(quiet=True) is None


def test_shift_resizes_freely(win, tmp_path):  # noqa: F811
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from test_ui_pages import idle
    doc = _open_striped(win, tmp_path, pages=1)
    v = _choose(doc)
    shift = Qt.KeyboardModifier.ShiftModifier
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, shift, vp(v, 0, 220, 160))
    p = QPointF(vp(v, 0, 300, 400))
    QApplication.sendEvent(v.viewport(), QMouseEvent(
        QEvent.Type.MouseMove, p, v.viewport().mapToGlobal(p), Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, shift))
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, shift, vp(v, 0, 300, 400))
    idle(doc)
    assert _rects(doc) == [(100, 100, 300, 400)]  # not 120:60 any more


def test_a_dropped_or_pasted_picture_comes_chosen(win, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent, QImage
    from PySide6.QtWidgets import QApplication
    from test_ui import open_doc
    from test_ui_pages import idle
    from furceli import objects_ui
    monkeypatch.setattr(objects_ui.CopyDialog, "exec", lambda self: False)
    doc = open_doc(win, page_with_picture(tmp_path))
    png = _png(tmp_path, "drop.png", (160, 80), (0, 160, 0))
    v = doc.view
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(png)])
    at = v.viewport().mapTo(win, vp(v, 0, 300, 500))
    ev = QDropEvent(QPointF(at), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier)
    win.dropEvent(ev)
    idle(doc)
    assert (240, 470, 360, 530) in _rects(doc)  # 160x80 px = 120x60 pt, around the drop point
    assert v.tool == "object" and round(v.selected_object()[1]["rect"][0]) == 240
    QApplication.clipboard().setImage(QImage(png))
    win.a_paste.trigger()
    idle(doc)
    assert len(_rects(doc)) == 3
