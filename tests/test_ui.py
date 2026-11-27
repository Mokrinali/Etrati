import os
import time

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtPrintSupport import QPrinter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from furceli import printing  # noqa: E402
from furceli.window import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyle("Fusion")
    return a


def wait(cond, secs=15):
    end = time.time() + secs
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("FURCELI_SETTINGS", str(tmp_path / "s.ini"))
    monkeypatch.setenv("FURCELI_BACKUPS", str(tmp_path / "backups"))
    w = MainWindow()
    w.resize(1200, 850)
    w.show()
    yield w
    while w.tabs.count():
        w.close_tab(0, ask=False)
    w.close()


def open_doc(win, path):
    win.open_file(path)
    doc = win.doc
    wait(lambda: doc.loaded)
    wait(lambda: settled(doc))
    return doc


def settled(doc):
    v = doc.view
    v.viewport().repaint()
    want = v._request()
    return want and all(k in v._tiles or k in v._bad for k in want)


def vp(view, pno, x, y):
    """Page point -> viewport logical QPoint."""
    d = view.page_to_doc(pno, QPointF(x, y)) - QPointF(view._origin())
    return (d / view.dpr).toPoint()


def test_open_and_paint(win, sample):
    doc = open_doc(win, sample)
    assert win.page_total.text().strip() == "/ 12"
    assert "ნიმუში ერთი" in win.windowTitle() and win.windowTitle().endswith("— Etrati")
    img = doc.view.viewport().grab().toImage()
    p = vp(doc.view, 0, 30, 30)
    assert img.pixelColor(p * img.devicePixelRatio()).lightness() > 240


@pytest.mark.parametrize("rot", [0, 1, 2, 3])
@pytest.mark.parametrize("two", [False, True])
def test_coordinates_round_trip(win, sample, rot, two):
    doc = open_doc(win, sample)
    v = doc.view
    v.set_two_up(two)
    for _ in range(rot):
        v.rotate()
    for pno in (0, 2, 3):
        for x, y in ((0, 0), (100, 50), (300, 400)):
            back = v.doc_to_page(pno, v.page_to_doc(pno, QPointF(x, y)))
            assert abs(back.x() - x) < .01 and abs(back.y() - y) < .01
            assert v.rects[pno].adjusted(-2, -2, 2, 2).contains(
                v.page_to_doc(pno, QPointF(x, y)).toPoint())


def test_navigation(win, sample):
    doc = open_doc(win, sample)
    for target in (5, 11, 0, 7):
        doc.view.go_to(target)
        assert doc.view.page == target and win.page_box.value() == target + 1
    win.page_box.setValue(3)
    assert doc.view.page == 2
    doc.view.go_to(0)
    doc.view.step(1)
    assert doc.view.page == 1
    doc.view.step(-5)
    assert doc.view.page == 0


def test_zoom_keeps_point_under_cursor(win, sample):
    doc = open_doc(win, sample)
    v = doc.view
    v.go_to(4)
    at = QPointF(v.viewport().width() / 2, v.viewport().height() / 2)

    def under():
        p = v._nearest_page(at * v.dpr)
        return p, v.doc_to_page(p, at * v.dpr + QPointF(v._origin()))
    p0, q0 = under()
    v.set_zoom(v.zoom * 1.7, at)
    p1, q1 = under()
    assert p0 == p1 and abs(q0.x() - q1.x()) < 3 and abs(q0.y() - q1.y()) < 3
    v.set_zoom(99)
    assert v.zoom == 8.0
    wait(lambda: settled(doc))
    v.set_zoom(0.0001)
    assert v.zoom == 0.1


def test_fit_modes_fit(win, sample):
    doc = open_doc(win, sample)
    v = doc.view
    v.set_fit("page")
    assert v.rects[0].height() <= v.viewport().height() * v.dpr
    v.set_fit("width")
    vw = v.viewport().width() * v.dpr
    for pno in (0, 2, 5, 3):  # portrait, landscape, portrait, rotated
        v.go_to(pno)
        left = v.rects[pno].left() - v._origin().x()
        right = vw - (v.rects[pno].right() - v._origin().x())
        assert v.page == pno and v.rects[pno].width() > vw * .9
        assert left >= 0 and right >= 0 and abs(left - right) <= 3


def test_search_finds_everything_in_order(win, sample):
    doc = open_doc(win, sample)
    doc.view.go_to(6)
    doc.query.setText("მსოფლიო")
    doc.start_search()
    wait(lambda: doc._left == 0)
    assert len(doc._found) == 12 * 14 == doc.results.count()
    assert doc._found == sorted(doc._found)
    assert doc.view.current_hit == (6, 0)          # starts where the reader is
    assert doc.results.currentRow() == 6 * 14
    doc.next_hit(1)
    assert doc.view.current_hit == (6, 1)
    doc.next_hit(-1)
    doc.next_hit(-1)
    assert doc.view.current_hit == (5, 13)
    for row in (0, 50, 167):
        assert doc.results.item(row).text().startswith(f"გვ. {doc._found[row][0] + 1}:")


def test_search_wraps_and_reports_nothing(win, sample):
    doc = open_doc(win, sample)
    doc.query.setText("უნიკალური")
    doc.start_search()
    wait(lambda: doc._left == 0)
    assert doc._found == [(4, 0)] and doc.view.page in (3, 4)
    doc.next_hit(1)
    assert doc.view.current_hit == (4, 0)
    doc.query.setText("ზზზზ")
    doc.start_search()
    wait(lambda: doc._left == 0)
    assert doc._found == [] and "ვერ მოიძებნა" in doc.found_label.text()


@pytest.mark.parametrize("pno,rot", [(0, 0), (0, 1), (3, 0)])
def test_select_and_copy(win, sample, pno, rot):
    doc = open_doc(win, sample)
    v = doc.view
    for _ in range(rot):
        v.rotate()
    v.set_fit("page")
    v.go_to(pno)
    wait(lambda: pno in v.chars)
    line = [c for c, ln in v.chars[pno] if ln == 1]
    a, b = line[0], line[len("გამარჯობა") - 1]

    def edge(c, after):
        # vertical lines (rotated page) run along y, horizontal ones along x
        if abs(line[-1][0] - line[0][0]) > abs(line[-1][1] - line[0][1]):
            return (c[2] - .3 if after else c[0] + .3), (c[1] + c[3]) / 2
        return (c[0] + c[2]) / 2, (c[3] - .3 if after else c[1] + .3)
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, pno, *edge(a, False)))
    QTest.mouseMove(v.viewport(), vp(v, pno, *edge(b, True)))
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, pno, *edge(b, True)))
    assert v.selected_text() == "გამარჯობა"
    doc.copy()
    assert QGuiApplication.clipboard().text() == "გამარჯობა"


def test_double_click_selects_word(win, sample):
    doc = open_doc(win, sample)
    v = doc.view
    wait(lambda: 0 in v.chars)
    c = [c for c, ln in v.chars[0] if ln == 1][12]
    pos = vp(v, 0, (c[0] + c[2]) / 2, (c[1] + c[3]) / 2)
    QTest.mouseDClick(v.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert v.selected_text() == "მსოფლიო"


def test_selection_over_pages(win, sample):
    doc = open_doc(win, sample)
    v = doc.view
    v._sel_a, v._sel_b = (0, 0), (2, 4)
    wait(lambda: v.selected_text() is not None)
    t = v.selected_text()
    assert t.startswith("თავი 1\n") and t.endswith("\nთავი") and "თავი 2" in t


def test_internal_link(win, sample):
    doc = open_doc(win, sample)
    v = doc.view
    v.set_fit("page")
    wait(lambda: 0 in v.links)
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, 0, 130, 610))
    assert v.page == 5


def test_external_link_asks_and_blocks_unsafe(win, sample, monkeypatch):
    doc = open_doc(win, sample)
    asked, warned, opened = [], [], []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a[-1]) or
                        QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[-1]))
    monkeypatch.setattr("furceli.docview.QDesktopServices.openUrl", lambda u: opened.append(u))
    doc._open_uri("https://example.com/")
    doc._open_uri("file:///C:/Windows/System32/calc.exe")
    assert len(asked) == 1 and len(warned) == 1 and not opened


def test_bookmarks(win, sample):
    doc = open_doc(win, sample)
    assert doc.toc.topLevelItemCount() == 4
    assert doc.toc.topLevelItem(0).childCount() == 2
    doc._toc_clicked(doc.toc.topLevelItem(2).child(0))
    assert doc.view.page == 7


def test_thumbnails_load_lazily(win, sample):
    doc = open_doc(win, sample)
    wait(lambda: doc.thumbs.get(0) is not None)
    doc.list.clicked.emit(doc.list.model_.index(9))
    assert doc.view.page == 9


def test_password_flow(win, locked, monkeypatch):
    answers = iter([("bad", True), ("საიდუმლო", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: next(answers))
    doc = open_doc(win, locked)
    assert len(doc.sizes) == 12
    assert not win.a_print.isEnabled()
    doc.view._sel_a, doc.view._sel_b = (0, 0), (0, 4)
    wait(lambda: 0 in doc.view.chars)
    QGuiApplication.clipboard().setText("x")
    doc.copy()
    assert QGuiApplication.clipboard().text() == "x"


def test_password_cancel_closes_tab(win, locked, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("", False))
    win.open_file(locked)
    wait(lambda: win.tabs.count() == 0)
    assert win.stack.currentIndex() == 0


def test_damaged_file_message(win, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[-1]))
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"nope" * 100)
    win.open_file(str(bad))
    wait(lambda: win.tabs.count() == 0)
    assert "დაზიანებულია" in shown[0]


def test_engine_crash_does_not_take_down_the_window(win, sample):
    doc = open_doc(win, sample)
    doc.client.submit(("boom",), 0, "_crash")
    crashed = []
    doc.client.crashed.connect(lambda: crashed.append(1))
    wait(lambda: crashed)
    doc.view.go_to(8)
    wait(lambda: settled(doc))
    assert any(k[1] == 8 for k in doc.view._tiles)


def test_print_to_pdf(win, sample, tmp_path):
    doc = open_doc(win, sample)
    out = str(tmp_path / "out.pdf")
    pr = QPrinter(QPrinter.PrinterMode.HighResolution)
    pr.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    pr.setOutputFileName(out)
    assert printing.print_pages(doc, pr, [0, 2, 3], win)
    d = pymupdf.open(out)
    assert d.page_count == 3
    assert [p.rect.width > p.rect.height for p in d] == [False, True, True]
    for p in d:
        pix = p.get_pixmap(dpi=40, colorspace=pymupdf.csGRAY)
        assert min(pix.samples) < 150  # not blank


def test_tabs_recent_and_last_position(win, sample, locked, app, tmp_path):
    doc = open_doc(win, sample)
    win.open_file(sample)
    assert win.tabs.count() == 1            # same file is not opened twice
    doc.view.go_to(7)
    win.close_tab(0)
    assert win._recent() == [os.path.abspath(sample)]
    win.settings.sync()
    w2 = MainWindow()
    w2.resize(1200, 850)
    w2.show()
    assert w2._recent() == [os.path.abspath(sample)]
    d2 = open_doc(w2, sample)
    wait(lambda: d2.view.page == 7)
    w2.close()


def test_closing_leaves_no_engine_process(win, sample):
    doc = open_doc(win, sample)
    proc = doc.client._proc
    assert proc.is_alive()
    win.close_tab(0)
    proc.join(3)
    assert not proc.is_alive()


def test_large_document_is_usable(win, tmp_path):
    path = str(tmp_path / "big.pdf")
    d = pymupdf.open()
    for i in range(2000):
        d.new_page().insert_text((72, 72), f"page {i + 1}")
    d.save(path)
    t = time.time()
    doc = open_doc(win, path)
    assert time.time() - t < 8
    t = time.time()
    doc.view.set_zoom(1.3)
    doc.view.go_to(1500)
    doc.view.viewport().repaint()
    assert time.time() - t < 1
    wait(lambda: settled(doc))
    doc.query.setText("page 1777")
    doc.start_search()
    wait(lambda: doc._left == 0, 30)
    assert doc._found == [(1776, 0)]


def test_shortcut_with_georgian_layout(win, sample):
    """Georgian layout: the key text is ფ, but the physical key is still F."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    doc = open_doc(win, sample)
    assert doc.sidebar.currentIndex() == 0
    e = QKeyEvent(QEvent.Type.KeyPress, ord("ფ"), Qt.KeyboardModifier.ControlModifier,
                  0, ord("F"), 0, "ფ")
    QApplication.sendEvent(doc.view, e)
    assert doc.sidebar.currentIndex() == 2
