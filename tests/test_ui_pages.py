"""Page editing through the real window, including the things that go wrong."""
import os
import time

import pymupdf
import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from furceli import dialogs, files
from furceli.thumbs import MIME
from test_pages import build, holds, names
from test_ui import app, open_doc, settled, vp, wait, win  # noqa: F401


def read(path):
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def six(tmp_path):
    return build(tmp_path / "ექვსი.pdf")


@pytest.fixture
def doc(win, six):  # noqa: F811
    return open_doc(win, six)


@pytest.fixture
def press(monkeypatch):
    """Answer custom message boxes by button text; records what was asked."""
    asked = []

    def answer(*texts):
        queue = list(texts)

        def run(box):
            asked.append(box.text())
            want = queue.pop(0)
            box._pick = next(b for b in box.buttons() if b.text() == want)
        monkeypatch.setattr(QMessageBox, "exec", run)
        monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box._pick)
        return asked
    return answer


@pytest.fixture(autouse=True)
def no_dialogs(monkeypatch):
    """Any unexpected dialog is a test failure, not a hang."""
    def boom(*a, **k):
        raise AssertionError(f"unexpected dialog: {a[1:]}")
    for name in ("question", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, boom)
    monkeypatch.setattr(QMessageBox, "exec", boom)


def idle(doc):
    wait(lambda: not doc._busy)
    wait(lambda: settled(doc))


def page_names(doc):
    """What the pages shown right now really say."""
    out = []
    for p in range(len(doc.sizes)):
        doc.view._need_content(p)
        wait(lambda: p in doc.view.chars)
        text = "".join(c[4] for c, ln in doc.view.chars[p] if ln == 0)
        out.append(text.strip()[-1:] if "SECRET" in text else "")
    return "".join(out)


def saved_names(path):
    return "".join(n[-1:] for n in names(pymupdf.open(path)))


def select(doc, *pages):
    doc.sidebar.setCurrentIndex(0)
    doc.list.select(list(pages))


def test_delete_undo_redo(win, doc):  # noqa: F811
    select(doc, 1, 3)
    win.a_del.trigger()
    idle(doc)
    assert page_names(doc) == "0245" and doc.dirty and win.windowTitle().startswith("●")
    win.a_undo.trigger()
    idle(doc)
    assert page_names(doc) == "012345" and not doc.dirty and not win.a_undo.isEnabled()
    win.a_redo.trigger()
    idle(doc)
    assert page_names(doc) == "0245"
    assert win.page_box.maximum() == 4


def test_cannot_delete_everything(doc, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a))
    select(doc, *range(6))
    doc.delete_pages()
    assert told and not doc.ops


@pytest.mark.parametrize("pages,at,want", [
    ([0], 6, "123450"), ([5], 0, "501234"), ([1, 4], 3, "021435"), ([2], 2, "012345"),
    ([2], 3, "012345"), ([0, 1], 2, "012345"), ([0, 5], 3, "120534")])
def test_drag_and_drop_order(doc, pages, at, want):
    doc.move_pages(pages, at)
    idle(doc)
    assert page_names(doc) == want
    assert bool(doc.ops) == (want != "012345")


class FakeDrop:
    def __init__(self, pos, mime, source):
        self.pos, self.mime, self.src, self.ok = pos, mime, source, False

    def position(self):
        return QPointF(self.pos)

    def mimeData(self):
        return self.mime

    def source(self):
        return self.src

    def acceptProposedAction(self):
        self.ok = True

    def ignore(self):
        self.ok = False


def side(lst, box):
    """Offset towards the next page: sideways in columns, down in a single column."""
    tops = [lst.visualRect(lst.model_.index(i)).top() for i in (0, 1)]
    return QPoint(box.width() // 3, 0) if tops[0] == tops[1] else QPoint(0, box.height() // 3)


@pytest.mark.parametrize("where", ["list", "wide list", "grid"])
def test_drop_lands_where_the_line_is(win, doc, where):  # noqa: F811
    if where == "grid":
        doc.organize(True)
    lst = doc.grid if where == "grid" else doc.list
    if "list" in where:
        doc.sidebar.setMinimumWidth(0)
        doc.splitter.setSizes([420, 700] if "wide" in where else [190, 900])
    QApplication.processEvents()
    box = lst.visualRect(lst.model_.index(2))
    before, after = box.center() - side(lst, box), box.center() + side(lst, box)
    assert lst.drop_index(before) == 2 and lst.drop_index(after) == 3
    mime = QMimeData()
    mime.setData(MIME, b"0")
    lst.dropEvent(FakeDrop(after, mime, lst))
    idle(doc)
    assert page_names(doc) == "120345"
    stranger = FakeDrop(after, mime, None)  # pages from another window are not ours
    lst.dragEnterEvent(stranger)
    assert not stranger.ok


def test_drop_pdf_file_inserts_it(doc, tmp_path):
    other = build(tmp_path / "b.pdf", 3, "B")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(other)])
    box = doc.list.visualRect(doc.list.model_.index(1))
    doc.list.dropEvent(FakeDrop(box.center() - side(doc.list, box), mime, None))
    idle(doc)
    assert page_names(doc) == "001212345"
    os.remove(other)  # undo/redo must not depend on the other file any more
    doc.undo()
    idle(doc)
    assert page_names(doc) == "012345"
    doc.redo()
    idle(doc)
    assert page_names(doc) == "001212345"


def test_rotate_duplicate_blank_shift(win, doc):  # noqa: F811
    select(doc, 0)
    win.a_rr.trigger()
    idle(doc)
    assert doc.sizes[0] == (800, 600)
    win.a_rl.trigger()
    idle(doc)
    assert doc.sizes[0] == (600, 800)
    select(doc, 1)
    win.a_dup.trigger()
    idle(doc)
    assert page_names(doc) == "0112345"
    select(doc, 0)
    win.a_blank.trigger()
    idle(doc)
    assert len(doc.sizes) == 8 and doc.sizes[1] == doc.sizes[0]
    select(doc, 0)
    win.a_down.trigger()
    idle(doc)
    win.a_up.trigger()
    idle(doc)
    for _ in range(6):
        win.a_undo.trigger()
        idle(doc)
    assert page_names(doc) == "012345" and not doc.dirty


def test_organizer(win, doc):  # noqa: F811
    doc.view.go_to(2)
    win.a_org.trigger()
    assert doc.organizing and doc.grid.selected() == [2]
    doc.grid.select([0, 1])
    win.a_rr.trigger()
    idle(doc)
    assert doc.grid.selected() == [0, 1]  # selection survives, so it can be repeated
    win.a_rr.trigger()
    idle(doc)
    doc.grid.select([4])
    QTest.keyClick(doc.grid, Qt.Key.Key_Delete)
    idle(doc)
    assert page_names(doc) == "01235"
    wait(lambda: doc.thumbs.get(0) is not None)
    win.a_org.trigger()
    assert not doc.organizing and doc.sidebar.isVisible()
    assert doc.sidebar.currentIndex() == 0
    bar = doc.sidebar.tabBar()
    assert bar.tabRect(2).right() <= doc.sidebar.width()  # all tab names fit


def test_reader_stays_on_same_page(doc):
    doc.view.go_to(4)
    doc.move_pages([0], 6)
    idle(doc)
    assert doc.view.page == 3 and page_names(doc)[3] == "4"


def test_save_overwrites_only_after_asking_and_keeps_backup(win, doc, six, press):  # noqa: F811
    original = read(six)
    doc.move_pages([5], 0)
    idle(doc)
    assert read(six) == original
    asked = press("გაუქმება", "გადაწერა")
    assert doc.save() is False and read(six) == original
    assert doc.save() is True
    assert len(asked) == 2 and "ექვსი.pdf" in asked[0]
    assert saved_names(six) == "501234" and not doc.dirty
    kept = os.listdir(files.backup_dir())
    assert len(kept) == 1 and read(os.path.join(files.backup_dir(), kept[0])) == original
    # the next save of this file does not ask again, but is still backed up
    doc.move_pages([5], 0)
    idle(doc)
    assert doc.save() and saved_names(six) == "450123"
    assert len(os.listdir(files.backup_dir())) == 2
    # undo still works after saving, and is an unsaved change again
    doc.undo()
    idle(doc)
    assert page_names(doc) == "501234" and doc.dirty
    assert not [f for f in os.listdir(os.path.dirname(six)) if f.endswith(".tmp")]


def test_save_as(win, doc, six, tmp_path, monkeypatch):  # noqa: F811
    original = read(six)
    out = str(tmp_path / "ახალი")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    select(doc, 0)
    doc.delete_pages()
    idle(doc)
    assert doc.save_as()
    assert read(six) == original and saved_names(out + ".pdf") == "12345"
    assert doc.path == out + ".pdf" and win.tabs.tabText(0) == "ახალი.pdf"


def test_deleted_page_is_gone_from_saved_file(doc, six, press):
    select(doc, 2)
    doc.delete_pages()
    idle(doc)
    press("გადაწერა")
    assert doc.save()
    d = pymupdf.open(six)
    assert not holds(d, "ASECRET2") and not holds(d, "AVALUE2") and holds(d, "ASECRET3")


def test_changed_on_disk_before_edit(doc, six, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    with open(six, "ab") as f:
        f.write(b"\n%x")
    doc.move_pages([0], 3)
    assert told and not doc._busy and not doc.ops


def test_changed_on_disk_before_save(doc, six, press, monkeypatch):
    doc.move_pages([0], 3)
    idle(doc)
    with open(six, "ab") as f:
        f.write(b"\n%other program")
    theirs = read(six)
    press("გადაწერა", "გადაწერა")
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: answers.pop(0))
    assert doc.save() is False and read(six) == theirs
    assert doc.save() is True and saved_names(six) == "120345"
    backups = [read(os.path.join(files.backup_dir(), f)) for f in os.listdir(files.backup_dir())]
    assert theirs in backups


def test_save_failure_loses_nothing(doc, six, press, monkeypatch):
    original = read(six)
    doc.move_pages([0], 3)
    idle(doc)
    press("გადაწერა")
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))

    def locked(*a):
        raise PermissionError("in use")
    with monkeypatch.context() as m:
        m.setattr(files, "_replace", locked)
        assert doc.save() is False
    assert "არ დაკარგულა" in told[0]
    assert read(six) == original and doc.dirty and page_names(doc) == "120345"
    assert not [f for f in os.listdir(os.path.dirname(six)) if f.endswith(".tmp")]


def test_no_backup_means_no_overwrite(doc, six, press, monkeypatch):
    original = read(six)
    doc.move_pages([0], 3)
    idle(doc)
    press("გადაწერა")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)

    def full(*a):
        raise OSError("disk full")
    monkeypatch.setattr(files.shutil, "copy2", full)
    assert doc.save() is False and read(six) == original


@pytest.mark.parametrize("answer,closed,saved", [
    ("გაუქმება", False, False), ("არ შეინახო", True, False), ("შენახვა", True, True)])
def test_closing_with_unsaved_changes(win, doc, six, press, answer, closed, saved):  # noqa: F811
    original = read(six)
    doc.move_pages([0], 6)
    idle(doc)
    doc._overwrite_ok = True
    press(answer)
    tmp = doc._tmp
    win.close_tab(0)
    assert (win.tabs.count() == 0) == closed
    assert (read(six) != original) == saved
    assert os.path.exists(tmp) != closed  # temporary copies do not stay behind


def test_closing_window_can_be_cancelled(win, doc, press):  # noqa: F811
    doc.move_pages([0], 6)
    idle(doc)
    press("გაუქმება")
    win.close()
    assert win.isVisible() and win.tabs.count() == 1


def test_crash_after_edits_keeps_them(doc):
    doc.move_pages([0], 6)
    idle(doc)
    select(doc, 0)
    doc.rotate_pages(90)
    idle(doc)
    crashed = []
    doc.client.crashed.connect(lambda: crashed.append(1))
    doc.client.submit(("boom",), 0, "_crash")
    wait(lambda: crashed)
    doc.view.chars.clear()
    assert page_names(doc) == "123450" and doc.sizes[0] == (800, 600)
    doc.undo()
    idle(doc)
    assert page_names(doc) == "123450" and doc.sizes[0] == (600, 800)


def test_crash_after_save_does_not_apply_edits_twice(doc, six, press):
    doc.move_pages([0], 6)
    idle(doc)
    press("გადაწერა")
    assert doc.save()
    crashed = []
    doc.client.crashed.connect(lambda: crashed.append(1))
    doc.client.submit(("boom",), 0, "_crash")
    wait(lambda: crashed)
    doc.view.chars.clear()
    assert page_names(doc) == "123450"


def test_failed_edit_rolls_back(doc, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    doc.move_pages([0], 6)
    idle(doc)
    doc.edit(("crop", [0, 1], (300, 0, 299, 0)))
    wait(lambda: told)
    idle(doc)
    assert page_names(doc) == "123450" and len(doc.ops) == 1
    assert all(s == (600, 800) for s in doc.sizes)


def test_crop_with_mouse(win, doc, monkeypatch):  # noqa: F811
    doc.view.set_fit("page")
    seen = {}

    def run(dlg):
        seen["margins"] = dlg.margins()
        return True
    monkeypatch.setattr(dialogs.CropDialog, "exec", run)
    win.a_crop.trigger()
    assert doc.view.cropping
    v = doc.view
    QTest.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, 0, 100, 150))
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    p = QPointF(vp(v, 0, 500, 700))
    QApplication.sendEvent(v.viewport(), QMouseEvent(
        QEvent.Type.MouseMove, p, v.viewport().mapToGlobal(p), Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=vp(v, 0, 500, 700))
    idle(doc)
    assert all(abs(a - b) < 3 for a, b in zip(seen["margins"], (100, 150, 100, 100)))
    assert abs(doc.sizes[0][0] - 400) < 5 and abs(doc.sizes[0][1] - 550) < 5
    assert doc.sizes[1] == (600, 800) and not v.cropping
    select(doc, 0)
    win.a_uncrop.trigger()
    idle(doc)
    assert doc.sizes[0] == (600, 800)


def test_escape_leaves_crop_mode(win, doc):  # noqa: F811
    win.a_crop.trigger()
    QTest.keyClick(doc.view, Qt.Key.Key_Escape)
    assert not doc.view.cropping


def test_extract_opens_new_tab(win, doc, six, tmp_path, monkeypatch):  # noqa: F811
    original = read(six)
    out = str(tmp_path / "ამოღებული.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    select(doc, 1, 4)
    win.a_ext.trigger()
    assert saved_names(out) == "14" and read(six) == original and not doc.dirty
    assert win.tabs.count() == 2


def test_extract_refuses_to_replace_itself(doc, six, monkeypatch):
    told = []
    original = read(six)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (six, ""))
    assert doc.extract_pages() is None and told and read(six) == original


@pytest.mark.parametrize("mode,text,want", [
    (0, 4, ["0123", "45"]), (1, "1-2, 6, 5-3", ["01", "5", "432"]), (2, None, ["01", "23", "45"])])
def test_split(doc, tmp_path, monkeypatch, mode, text, want):
    folder = tmp_path / "ნაწილები"
    folder.mkdir()

    def run(dlg):
        dlg.group.button(mode).setChecked(True)
        if mode == 0:
            dlg.every.setValue(text)
        elif mode == 1:
            dlg.ranges.setText(text)
        return True
    monkeypatch.setattr(dialogs.SplitDialog, "exec", run)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(folder))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    doc.split()
    out = sorted(os.listdir(folder))
    assert [saved_names(str(folder / f)) for f in out] == want
    doc.split()  # a second time must not overwrite the first
    assert len(os.listdir(folder)) == 2 * len(want)


def test_split_with_equal_bookmark_names(win, tmp_path, monkeypatch):  # noqa: F811
    d = pymupdf.open(build(tmp_path / "x.pdf"))
    d.set_toc([[1, "ერთი: და/იგივე?", 1], [1, "ერთი: და/იგივე?", 4]])
    d.save(str(tmp_path / "y.pdf"))
    doc = open_doc(win, str(tmp_path / "y.pdf"))
    dlg = dialogs.SplitDialog(doc)
    dlg.group.button(2).setChecked(True)
    parts = dlg.parts()
    assert [len(g) for g, _ in parts] == [3, 3] and len({n for _, n in parts}) == 2
    assert not any(c in n for _, n in parts for c in '<>:"/\\|?*')


def test_merge_dialog(win, six, tmp_path, monkeypatch):  # noqa: F811
    other = build(tmp_path / "b.pdf", 3, "B")
    locked = str(tmp_path / "locked.pdf")
    pymupdf.open(six).save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    out = str(tmp_path / "ერთად.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    answers = iter([("bad", True), ("u", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: next(answers))
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a: asked.append(a[-1]) or QMessageBox.StandardButton.Yes)
    before = [read(p) for p in (six, other, locked)]
    dlg = dialogs.MergeDialog(win, [six, other, locked])
    assert dlg.table.rowCount() == 3
    dlg.table.selectRow(1)
    dlg.shift(-1)
    dlg.table.item(2, 2).setText("3, 1")
    dlg.merge()
    assert dlg.result_path == out and asked  # warned that protection is not carried over
    assert saved_names(out) == "012" + "012345" + "20"
    assert [read(p) for p in (six, other, locked)] == before
    dlg2 = dialogs.MergeDialog(win, [six])
    dlg2.table.item(0, 2).setText("7")
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    dlg2.merge()
    assert "მხოლოდ 6 გვერდია" in told[0] and dlg2.result_path is None
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (six, ""))
    dlg2.table.item(0, 2).setText("")
    dlg2.merge()
    assert len(told) == 2 and read(six) == before[0]
    dlg2.reject()


def test_protected_file_without_rights_cannot_be_edited(win, locked, monkeypatch):  # noqa: F811
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("საიდუმლო", True))
    doc = open_doc(win, locked)
    assert not doc.can_edit and not win.a_del.isEnabled() and not win.a_org.isEnabled()
    doc.move_pages([0], 3)
    assert not doc.ops
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    assert doc.extract_pages() is None


def test_search_follows_edit(doc):
    doc.query.setText("ASECRET3")
    doc.start_search()
    wait(lambda: doc._left == 0)
    assert doc._found == [(3, 0)]
    doc.move_pages([3], 0)
    idle(doc)
    wait(lambda: doc._left == 0 and doc._found)
    assert doc._found == [(0, 0)]


def test_many_quick_edits_stay_consistent(win, doc, six, press):  # noqa: F811
    expect = list("012345")
    for i in range(12):
        a, b = i % 6, (i * 5 + 2) % 7
        chosen = expect[a]
        rest = expect[:a] + expect[a + 1:]
        cut = b if b <= a else b - 1
        want = rest[:cut] + [chosen] + rest[cut:]
        doc.move_pages([a], b)
        wait(lambda: not doc._busy)
        expect = want
    assert page_names(doc) == "".join(expect)
    press("გადაწერა")
    assert doc.save() and saved_names(six) == "".join(expect)


def test_big_document_edit_speed(win, tmp_path):  # noqa: F811
    path = str(tmp_path / "big.pdf")
    d = pymupdf.open()
    for i in range(2000):
        d.new_page().insert_text((72, 72), f"page {i + 1}")
    d.save(path)
    doc = open_doc(win, path)
    t = time.time()
    doc.move_pages([1999], 0)
    wait(lambda: not doc._busy)
    doc.undo()
    wait(lambda: not doc._busy)
    assert time.time() - t < 8
    doc.organize(True)
    QApplication.processEvents()


@pytest.mark.parametrize("text,want", [
    ("1-3, 5", [0, 1, 2, 4]), ("4-", [3, 4, 5]), ("-2", [0, 1]), ("3-1", [2, 1, 0]),
    ("2;2", [1, 1]), (" 6 ", [5]), ("2–3", [1, 2])])
def test_page_ranges(text, want):
    assert files.parse_group(text, 6) == want


@pytest.mark.parametrize("text", ["", "0", "7", "1-9", "a", "1--2", "-", ","])
def test_bad_page_ranges(text):
    with pytest.raises(ValueError):
        files.parse_group(text, 6)


def test_backup_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("FURCELI_BACKUPS", str(tmp_path / "b"))
    monkeypatch.setattr(files, "KEEP_BACKUPS", 3)
    src = tmp_path / "f.pdf"
    for i in range(6):
        src.write_bytes(b"%d" % i)
        last = files.make_backup(str(src))
        time.sleep(.02)
    left = os.listdir(tmp_path / "b")
    assert len(left) == 3 and os.path.basename(last) in left
