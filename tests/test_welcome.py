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
