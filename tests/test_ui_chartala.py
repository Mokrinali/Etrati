"""ჩართალა in the window: the menu, the comparison tab, moving together."""
import sys
import time

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QColorDialog, QDialog, QFileDialog, QInputDialog, QMessageBox,
                               QProgressDialog)
from test_chartala import BASE, SHIFT, digest, drawing, moved, unrelated
from test_ui import app, open_doc, vp, wait, win  # noqa: F401

from furceli import compare_view
from furceli.compare_view import CompareView


@pytest.fixture
def pair(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE + [("rect", (500, 450, 600, 520))],
                  [((80, 300), "ფასი 100 ლარი")])
    new = drawing(tmp_path / "new.pdf", BASE + [("disc", (150, 350, 40))],
                  [((80, 300), "ფასი 250 ლარი")])
    return old, new


def start(win, monkeypatch, old, new):  # noqa: F811
    monkeypatch.setattr(compare_view.CompareFilesDialog, "ask",
                        staticmethod(lambda parent: (old, new)))
    win.a_cmp_files.trigger()
    cv = win.tabs.currentWidget()
    assert isinstance(cv, CompareView)
    wait(lambda: cv.finished)
    return cv


def shown(view, pno, rect):
    """Is the page rectangle (points) inside the visible part of the view?"""
    d = view._doc_rect(pno, rect)
    o = view._origin()
    seen = QRectF(o.x(), o.y(), view.viewport().width() * view.dpr,
                  view.viewport().height() * view.dpr)
    return seen.contains(d)


def menu(win, title):  # noqa: F811
    return next(a.menu() for a in win.menuBar().actions() if a.text() == title)


def test_chartala_menu(win):  # noqa: F811
    m = menu(win, "ჩართალა")
    acts = m.actions()
    assert win.a_cmp_files in acts and win.a_link in acts and win.a_compare in acts
    assert win.a_compare not in menu(win, "ხელსაწყოები").actions()
    assert win.a_cmp_files.isEnabled()  # no file needs to be open


def test_comparison_tab_marks_red_and_green(win, pair, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *pair)
    assert win.doc is None and cv.old.loaded and cv.new.loaded
    texts = [cv.list.item(i).text() for i in range(cv.list.count())]
    assert len(cv.entries) == 3, texts                  # removed box, added disc, one word
    assert sum("250" in t for t in texts) == 1
    olds = cv.old.view.frames[0]
    news = cv.new.view.frames[0]
    assert olds and all(token == "mark-diff-old" for _, token, _ in olds)
    assert news and all(token == "mark-diff-new" for _, token, _ in news)


def test_choosing_a_change_moves_both_sides(win, pair, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *pair)
    cv.old.view.set_zoom(3.0)
    wait(lambda: abs(cv.new.view.zoom - 3.0) < 1e-3)
    for row in range(len(cv.entries)):
        cv.list.setCurrentRow(row)
        e = cv.entries[row]
        po, pn = e["pair"]
        for rect in e["old"]:
            assert shown(cv.old.view, po, rect)
        for rect in e["new"]:
            assert shown(cv.new.view, pn, rect)


def test_next_and_previous_go_round(win, pair, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *pair)
    n = len(cv.entries)
    cv.list.setCurrentRow(n - 1)
    cv.step(1)
    assert cv.list.currentRow() == 0
    cv.step(-1)
    assert cv.list.currentRow() == n - 1
    cv.next_btn.click()
    assert cv.list.currentRow() == 0


def test_scroll_and_zoom_together_and_apart(win, pair, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *pair)
    a, b = cv.old.view, cv.new.view
    a.set_zoom(3.0)
    assert abs(b.zoom - 3.0) < 1e-3
    bar = a.verticalScrollBar()
    before = b.verticalScrollBar().value()
    bar.setValue(bar.value() + 400)
    assert b.verticalScrollBar().value() != before
    b.horizontalScrollBar().setValue(b.horizontalScrollBar().value() + 300)
    assert abs(a.horizontalScrollBar().value() - b.horizontalScrollBar().value()) < 40
    win.a_link.trigger()                    # apart
    assert not win.a_link.isChecked() and not cv.linked
    now = b.verticalScrollBar().value()
    bar.setValue(bar.value() + 300)
    a.set_zoom(1.0)
    assert b.verticalScrollBar().value() == now and abs(b.zoom - 3.0) < 1e-3
    win.a_link.trigger()                    # together again: the other one follows at once
    assert cv.linked and abs(b.zoom - 1.0) < 1e-3


def test_comparing_changes_no_file_and_closing_ends_engines(win, pair, monkeypatch):  # noqa: F811
    before = [digest(p) for p in pair]
    cv = start(win, monkeypatch, *pair)
    clients = (cv.old.client, cv.new.client)
    win.close_tab(win.tabs.indexOf(cv))
    assert win.tabs.count() == 0 and all(c._stop for c in clients)
    assert [digest(p) for p in pair] == before


def test_cancelling_keeps_the_tab_and_the_files(win, pair, monkeypatch):  # noqa: F811
    before = [digest(p) for p in pair]
    monkeypatch.setattr(QProgressDialog, "wasCanceled", lambda self: True)
    cv = start(win, monkeypatch, *pair)
    assert cv.entries == [] and "გაუქმდა" in cv.note.text()
    assert win.tabs.currentWidget() is cv
    assert [digest(p) for p in pair] == before


def test_different_page_counts_are_told(win, tmp_path, monkeypatch):  # noqa: F811
    import pymupdf
    old = drawing(tmp_path / "old.pdf", BASE)
    d = pymupdf.open(old)
    d.new_page(width=842, height=595)
    new = str(tmp_path / "new.pdf")
    d.save(new)
    cv = start(win, monkeypatch, old, new)
    assert cv.pairs == [(0, 0)]
    assert "1" in cv.banner.message() and "2" in cv.banner.message()


def test_every_menu_command_is_safe_on_the_comparison_tab(win, pair, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *pair)
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *e: errors.append(e))
    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "about", lambda *a: None)
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: 0)
    monkeypatch.setattr(QDialog, "exec", lambda *a: 0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([], ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False))
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("", False))
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor())  # cancelled
    monkeypatch.setattr(compare_view.CompareFilesDialog, "ask", staticmethod(lambda parent: None))
    outside = {win.a_quit, win.a_default, win.a_backups, win.a_update, win.a_close}

    def walk(m):
        for a in m.actions():
            if a.menu():
                yield from walk(a.menu())
            elif not a.isSeparator():
                yield a
    acts = [a for top in win.menuBar().actions() for a in walk(top.menu())]
    assert len(acts) > 60
    for a in acts:
        if a in outside or not a.isEnabled():
            continue
        a.trigger()
        if a is win.a_full:
            a.trigger()
        assert win.tabs.currentWidget() is cv, a.text()
    win.set_theme("light")
    assert not errors, errors
    assert not any(a.isEnabled() for a in win.tool_acts.values())
    assert not win.a_save.isEnabled() and not win.a_save_as.isEnabled()
    assert not win.a_undo.isEnabled() and not win.a_print.isEnabled()


def test_documents_still_work_beside_a_comparison(win, pair, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *pair)
    doc = open_doc(win, pair[1])
    assert win.tabs.count() == 2 and win.doc is doc
    win._sync()
    assert win.a_save.isEnabled() is False and win.tool_acts["rect"].isEnabled()
    win.tabs.setCurrentWidget(cv)
    assert win.doc is None and not win.tool_acts["rect"].isEnabled()
    win.tabs.setCurrentWidget(doc)
    assert win.tabs.tabText(win.tabs.indexOf(doc)).endswith("new.pdf")


# -- step 3: alignment, sensitivity, pairs of pages -----------------------------------
@pytest.fixture
def shifted(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE, [((80, 300), "ფასი 100 ლარი")])
    return old, moved(tmp_path / "new.pdf", old, shapes=[("disc", (400, 470, 30))])


def again(cv):
    cv.run()
    wait(lambda: cv.finished)


def click(view, pno, x, y):
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=vp(view, pno, x, y))


def test_a_moved_drawing_shows_only_the_real_change(win, shifted, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *shifted)
    assert len(cv.entries) == 1 and cv.entries[0]["kind"] == "added"
    cv.old.view.set_zoom(2.0)                  # the new one is 2 % bigger: drawn 2 % smaller
    assert abs(cv.new.view.zoom - 2.0 / 1.02) < .01


def test_aligning_by_hand(win, shifted, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *shifted)
    cv.opts["auto"] = False
    again(cv)
    assert len(cv.entries) > 3
    win.a_link.trigger()                        # apart: each side is pointed at alone
    cv.old.view.set_fit("page")
    cv.new.view.set_fit("page")
    cv.align_btn.click()
    assert cv.aligning and cv.old.view.tool == "point" and cv.new.view.tool == "point"
    s, dx, dy = SHIFT
    click(cv.old.view, 0, 100, 100)
    click(cv.new.view, 0, 100 * s + dx, 100 * s + dy)
    click(cv.old.view, 0, 700, 400)
    click(cv.new.view, 0, 700 * s + dx, 400 * s + dy)
    wait(lambda: cv.finished and not cv.aligning)
    assert cv.manual[(0, 0)] and len(cv.entries) == 1
    assert cv.old.view.tool == "select" and cv.new.view.tool == "select"


def test_aligning_by_hand_can_be_cancelled(win, shifted, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *shifted)
    cv.align_btn.click()
    click(cv.old.view, 0, 100, 100)
    QTest.keyClick(cv.new.view, Qt.Key.Key_Escape)
    assert not cv.aligning and cv.old.view.tool == "select" and not cv.manual


def test_failed_alignment_offers_aligning_by_hand(win, tmp_path, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, drawing(tmp_path / "old.pdf", BASE),
               unrelated(tmp_path / "new.pdf"))
    assert "ხელით" in cv.banner.message()


def test_sensitivity_compares_again(win, tmp_path, monkeypatch):  # noqa: F811
    old = drawing(tmp_path / "old.pdf", BASE)
    dots = [("disc", (60 + 71 * i, 60 + (i * 37) % 480, 1.0)) for i in range(11)]
    cv = start(win, monkeypatch, old, drawing(tmp_path / "new.pdf", BASE + dots))
    cv.sensitivity.setValue(1)
    wait(lambda: cv.finished and cv.opts.get("min_px") == 80)
    assert cv.entries == []
    cv.sensitivity.setValue(5)
    wait(lambda: cv.finished and cv.opts.get("min_px") == 3)
    assert len(cv.entries) >= 8


def test_pairs_of_pages_can_be_changed(win, tmp_path, monkeypatch):  # noqa: F811
    import pymupdf
    old = drawing(tmp_path / "old.pdf", BASE)
    d = pymupdf.open(unrelated(tmp_path / "cover.pdf"))
    d.insert_pdf(pymupdf.open(old))             # a cover page was put in front
    new = str(tmp_path / "new.pdf")
    d.save(new)
    cv = start(win, monkeypatch, old, new)
    assert cv.pairs == [(0, 0)] and cv.entries and not cv.pairs_box.isHidden()
    cv.pair_combo(0).setCurrentIndex(2)         # old page 1 is new page 2
    wait(lambda: cv.finished and cv.pairs == [(0, 1)])
    assert cv.entries == []
    cv.pair_combo(0).setCurrentIndex(0)         # "—": not compared
    wait(lambda: cv.finished and cv.pairs == [])
    assert cv.entries == []


# -- after the independent review ------------------------------------------------------
def test_a_bigger_returned_drawing_is_shown_at_one_size(win, tmp_path, monkeypatch):  # noqa: F811
    """One physical scale: the same wall is as long on the screen on both sides."""
    import pymupdf
    old = drawing(tmp_path / "old.pdf", BASE, [((80, 300), "ფასი 100 ლარი")])
    src = pymupdf.open(old)
    d = pymupdf.open()
    p = d.new_page(width=842 * 1.5, height=595 * 1.5)   # plotted half as big again
    p.show_pdf_page(p.rect, src, 0)
    new = str(tmp_path / "new.pdf")
    d.save(new)
    cv = start(win, monkeypatch, old, new)
    assert abs(cv.results[(0, 0)]["matrix"][0][0] - 1.5) < .01
    cv.old.view.set_zoom(1.0)
    wall_old = cv.old.view._doc_rect(0, (100, 100, 300, 200)).width()
    wall_new = cv.new.view._doc_rect(0, (150, 150, 450, 300)).width()
    assert abs(wall_new / wall_old - 1) < .02, (wall_old, wall_new)
    cv.new.view.set_zoom(0.5)                  # and back from the other side
    wall_old = cv.old.view._doc_rect(0, (100, 100, 300, 200)).width()
    wall_new = cv.new.view._doc_rect(0, (150, 150, 450, 300)).width()
    assert abs(wall_new / wall_old - 1) < .02, (wall_old, wall_new)


def test_ctrl_w_closes_a_comparison(win, pair, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *pair)
    clients = (cv.old.client, cv.new.client)
    assert win.a_close.isEnabled()
    win.a_close.trigger()
    assert win.tabs.count() == 0 and all(c._stop for c in clients)


def test_comparing_again_while_comparing_waits_its_turn(win, pair, monkeypatch):  # noqa: F811
    """A slider moved while the window breathes between two pages must not start a second
    comparison inside the first: the results of both would be mixed."""
    cv = start(win, monkeypatch, *pair)
    real, inner = compare_view.run_task, []

    def once(client, op, args, *a, **k):
        if op == "visual_diff" and not inner:
            inner.append(1)
            cv.run()                           # what a setting changed just now would do
        return real(client, op, args, *a, **k)
    monkeypatch.setattr(compare_view, "run_task", once)
    cv.run()
    assert inner and cv._again.isActive() and not cv.finished  # it comes after, not inside
    wait(lambda: cv.finished and not cv._again.isActive())
    assert len(cv.entries) == 3


def test_the_old_dialog_tells_a_drawing_change_beside_words(app):  # noqa: F811
    from furceli import forms
    result = {"changes": [{"kind": "replace", "old": "100", "new": "250", "page": 0,
                           "old_at": [(0, (1, 1, 2, 2))], "new_at": [(0, (1, 1, 2, 2))]}],
              "pictures": [0, 1], "pages": (2, 2)}
    dlg = forms.CompareDialog("ძველი.pdf", "ახალი.pdf", result)
    texts = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    assert texts[1] == "გვ. 1 · ნახაზიც განსხვავდება (ტექსტის გარდა)"
    assert texts[2] == "გვ. 2 · გამოსახულება განსხვავდება"
    dlg.deleteLater()


def test_cancelling_a_comparison_that_is_running(win, pair, monkeypatch):  # noqa: F811
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QPushButton
    from test_ui_split import ready
    cv = start(win, monkeypatch, *pair)
    real = compare_view.run_task

    def slow(client, op, args, text="", parent=None, allow=None):
        if op == "visual_diff":                # a big sheet: the person presses გაუქმება
            QTimer.singleShot(300, lambda: allow.findChild(QPushButton).click())
            return real(client, "_sleep", (8.0,), text, parent, allow=allow)
        return real(client, op, args, text, parent, allow=allow)
    monkeypatch.setattr(compare_view, "run_task", slow)
    t = time.time()
    again(cv)
    assert time.time() - t < 5 and "გაუქმდა" in cv.note.text() and cv.entries == []
    assert win.tabs.currentWidget() is cv
    monkeypatch.setattr(compare_view, "run_task", real)
    cv.new.view.go_to(0)
    wait(lambda: ready(cv.new.view))           # its engine came back and draws again
    again(cv)
    assert len(cv.entries) == 3


def test_aligning_by_hand_can_be_taken_back(win, shifted, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *shifted)
    assert cv.auto_btn.isHidden()
    cv.opts["auto"] = False
    again(cv)
    many = len(cv.entries)
    win.a_link.trigger()
    cv.old.view.set_fit("page")
    cv.new.view.set_fit("page")
    cv.align_btn.click()
    s, dx, dy = SHIFT
    click(cv.old.view, 0, 100, 100)
    click(cv.new.view, 0, 100 * s + dx, 100 * s + dy)
    click(cv.old.view, 0, 700, 400)
    click(cv.new.view, 0, 700 * s + dx, 400 * s + dy)
    wait(lambda: cv.finished)
    assert len(cv.entries) == 1 and not cv.auto_btn.isHidden()
    cv.auto_btn.click()                        # back to finding it by itself
    wait(lambda: cv.finished)
    assert not cv.manual and len(cv.entries) == many and cv.auto_btn.isHidden()


def test_esc_stops_aligning_wherever_the_focus_is(win, shifted, monkeypatch):  # noqa: F811
    cv = start(win, monkeypatch, *shifted)
    cv.align_btn.click()
    cv.list.setFocus()
    QTest.keyClick(cv.list, Qt.Key.Key_Escape)
    assert not cv.aligning and cv.old.view.tool == "select"
