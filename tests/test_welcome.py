"""The screen shown when no file is open."""
import os
import time

from test_ui import app, open_doc, settled, wait, win  # noqa: F401
from test_pages import build

from furceli import welcome


def test_when_opened():
    now = time.mktime((2026, 11, 26, 15, 0, 0, 0, 0, -1))
    assert welcome.when(now - 3600, now) == "დღეს"
    assert welcome.when(now - 86400, now) == "გუშინ"
    assert welcome.when(time.mktime((2026, 11, 20, 9, 0, 0, 0, 0, -1)), now) == "20 ნოე"
    assert welcome.when(time.mktime((2025, 3, 2, 9, 0, 0, 0, 0, -1)), now) == "2 მარ 2025"
    assert welcome.when(None, now) == ""


def test_cards_and_recent_files(win, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QFileDialog
    asked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: asked.append(1) or ([], ""))
    w = win.welcome
    assert [c.accessibleName() for c in w.cards] == [
        "ფაილის გახსნა…", "PDF სხვა ფაილებისგან…", "ფოტოს ან სკანის გასუფთავება…",
        "ფაილების გაერთიანება…"]
    w.cards[0].click()  # the main card asks for files
    assert asked
    path = build(tmp_path / "ბოლო.pdf")
    open_doc(win, path)
    assert win.recent_list.count() >= 1
    item = win.recent_list.item(0)
    assert os.path.normcase(item.data(256)) == os.path.normcase(path)
    row = win.recent_list.itemWidget(item)
    assert "დღეს" in [lab.text() for lab in row.findChildren(type(w.recent_label))]
    win.close_tab(0, ask=False)
    opened = []
    monkeypatch.setattr(win, "open_file", lambda p: opened.append(p))
    w.chosen.disconnect()
    w.chosen.connect(win.open_file)
    win.recent_list.itemClicked.emit(win.recent_list.item(0))
    assert opened and os.path.normcase(opened[0]) == os.path.normcase(path)


def test_drop_frame_shows_and_hides(win):  # noqa: F811
    from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile("D:/x.pdf")])
    e = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, data, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    win.dragEnterEvent(e)
    assert win.drop_frame.isVisible()
    win.dragLeaveEvent(QDragLeaveEvent())
    assert not win.drop_frame.isVisible()


def test_sidebar_looks(win, tmp_path):  # noqa: F811
    """Segmented sidebar; a chosen page is drawn with a red frame and number."""
    from PySide6.QtGui import QImage
    doc = open_doc(win, build(tmp_path / "s.pdf"))
    assert doc.sidebar.objectName() == "sidebar"
    doc.sidebar.setCurrentIndex(0)
    doc.list.select([1])
    settled(doc)
    img = doc.list.viewport().grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    red = sum(1 for y in range(0, img.height(), 2) for x in range(0, img.width(), 2)
              if img.pixelColor(x, y).red() > 180 and img.pixelColor(x, y).green() < 70)
    assert red > 40


def test_colour_circles(win, tmp_path):  # noqa: F811
    from PySide6.QtGui import QColor
    sw = win.swatches
    assert len(sw.buttons) == 6 and sw.buttons[0].isChecked()  # the pen colour by default
    doc = open_doc(win, build(tmp_path / "c.pdf"))
    sw.buttons[3].click()
    assert doc.view.tool_color.name() == sw.buttons[3].color.name()
    assert win.settings.value("color") == sw.buttons[3].color.name()
    assert [b.isChecked() for b in sw.buttons] == [False, False, False, True, False, False]
    win._set_color(QColor(10, 200, 30))  # a colour of one's own: no circle is chosen
    assert not any(b.isChecked() for b in sw.buttons)
    assert win.a_color.isEnabled()


def test_toolbar_fields_show_their_numbers(win, tmp_path):  # noqa: F811
    open_doc(win, build(tmp_path / "f.pdf"))
    assert win.page_box.lineEdit().width() > 20 and win.page_box.text() == "1"
