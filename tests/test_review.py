"""Regressions for the independent review (findings F1-F14)."""
import os
import threading
import time
import types
import zipfile

import numpy as np
import pymupdf
import pytest
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from test_features import META, blank, engine, picture, saved
from test_pages import build, holds
from test_ui import app, open_doc, settled, vp, wait, win  # noqa: F401
from test_ui_pages import idle, no_dialogs, press, read  # noqa: F401
from test_ui_tools import FakeClient, drag

from furceli import ai, client, convert, files, forms, printing, signing
from furceli.engine import EngineError
from furceli.features import Features

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


@pytest.fixture
def doc(win, tmp_path):  # noqa: F811
    return open_doc(win, build(tmp_path / "ექვსი.pdf"))


def crash(doc):
    seen = []
    doc.client.crashed.connect(lambda: seen.append(1))
    doc.client.submit(("boom", time.time()), 0, "_crash")
    wait(lambda: seen)


# -- F1 ----------------------------------------------------------------------
def test_f1_failed_replay_leaves_no_document(tmp_path):
    path = build(tmp_path / "a.pdf")
    e = Features()
    with pytest.raises(EngineError):
        e.replay(path, "", [("rotate", [0], 90), ("image", 0, (1, 1, 50, 50), "missing.png")])
    assert e.doc is None


def test_f1_undo_with_missing_asset_changes_nothing(doc, tmp_path, monkeypatch):
    doc.edit(("image", 0, (100, 100, 200, 200), doc._keep_file(picture(tmp_path))))
    idle(doc)
    doc.edit(("rotate", [1], 90))
    idle(doc)
    for f in doc._holds:
        f.close()
    asset = doc.ops[0][3]
    os.remove(asset)
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    doc.undo()
    assert told and not doc._busy and len(doc.ops) == 2 and not doc.broken


def test_f1_broken_document_cannot_be_saved(win, doc, tmp_path, monkeypatch):  # noqa: F811
    original = read(doc.path)
    doc.edit(("rotate", [1], 90))
    idle(doc)
    told = []
    for name in ("warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a: told.append(a[-1]))
    monkeypatch.setattr(doc, "_assets_present", lambda ops: True)
    doc.ops.append(("image", 0, (1, 1, 9, 9), "missing.png"))  # cannot be replayed
    monkeypatch.setattr(QMessageBox, "question", lambda *a: NO)  # no more retries
    doc._start("restore", "replay", doc._base, doc._base_password, doc.ops)
    wait(lambda: doc.broken)
    assert "შენახვა გამორთულია" in told[-1] and not win.a_undo.isEnabled()
    assert doc.save() is False and doc.save_as() is False
    assert doc.edit(("rotate", [0], 90)) is False
    assert read(doc.path) == original


def test_f1_working_files_cannot_be_deleted_by_others(doc, tmp_path):
    doc.edit(("rotate", [1], 90))
    idle(doc)
    with pytest.raises(PermissionError):
        os.remove(doc._base)


# -- F2 ----------------------------------------------------------------------
def test_f2_many_crashes_never_make_edits_unsaveable(doc, press, monkeypatch):  # noqa: F811
    doc.edit(("rotate", [0], 90))
    idle(doc)
    for k in range(client.MAX_RESTARTS + 3):
        crash(doc)
        assert doc._quiet("pdf_box", 0, (1, 1, 5, 5)) is not None, k
    doc._overwrite_ok = True
    assert doc.save()
    assert pymupdf.open(doc.path)[0].rotation == 90


def test_f2_save_works_even_after_crashes_in_a_row(doc):
    doc.edit(("rotate", [0], 90))
    idle(doc)
    doc.view.frozen = True  # no successful requests in between
    codes = []
    doc.client.failed.connect(lambda k, c: k[0] == "boom" and codes.append(c))
    for n in range(client.MAX_RESTARTS + 2):
        doc.client.submit(("boom", n), 0, "_crash")
    wait(lambda: len(codes) == client.MAX_RESTARTS + 2)
    assert codes[-1] == "engine_dead"
    doc._overwrite_ok = True
    assert doc.save() and pymupdf.open(doc.path)[0].rotation == 90


# -- F3 ----------------------------------------------------------------------
def test_f3_scrub_removes_every_script(tmp_path):
    d = pymupdf.open(build(tmp_path / "a.pdf"))
    cat = d.pdf_catalog()
    js = "<</S/JavaScript/JS(app.alert\\('EVIL%d'\\))>>"
    d.xref_set_key(cat, "OpenAction", js % 1)
    d.xref_set_key(cat, "AA", "<</WC " + js % 2 + ">>")
    d.xref_set_key(d[0].xref, "AA", "<</O " + js % 3 + ">>")
    w = next(d[1].widgets())
    d.xref_set_key(w.xref, "AA", "<</K " + js % 4 + ">>")
    d.xref_set_key(w.xref, "A", "<</S/GoTo/D[0/Fit]/Next[" + js % 5 + "]>>")
    x = d.get_new_xref()
    d.update_object(x, js % 6)
    d.xref_set_key(next(d[2].annots()).xref, "A", f"{x} 0 R")
    d.xref_set_key(cat, "Names", "<</JavaScript<</Names[(n) " + js % 7 + "]>>>>")
    path = str(tmp_path / "js.pdf")
    d.save(path)
    assert holds(pymupdf.open(path), "EVIL1")
    e = engine(path)
    look = [p.get_pixmap().samples for p in e.doc]
    e.apply(("scrub", {}))
    r = saved(e, tmp_path)
    for k in range(1, 8):
        assert not holds(r, f"EVIL{k}"), k
    assert not any("/JavaScript" in r.xref_object(x) for x in range(1, r.xref_length()))
    assert [p.get_pixmap().samples for p in r] == look
    assert [next(p.widgets()).field_value for p in r] == [f"AVALUE{i}" for i in range(6)]


def test_f3_scrub_can_keep_scripts_and_clears_comment_authors(tmp_path):
    e = engine(blank(tmp_path))
    e.apply(("annot", 0, "rect", {"rect": (10, 10, 60, 60)}, META))
    e.apply(("scrub", {}))
    assert e.annots(0)[0]["author"] == "" and not holds(saved(e, tmp_path), "ავტორი")


# -- F4 ----------------------------------------------------------------------
def test_f4_external_links_are_cut_but_hyperlinks_stay(tmp_path):
    src = str(tmp_path / "a.docx")
    rels = ('<Relationships><Relationship Id="r1" Type="http://x/image" '
            'Target="\\\\evil\\share\\a.png" TargetMode="External"/>'
            "<Relationship Id='r2' Type='http://x/attachedTemplate' Target='http://evil/t.dotm' "
            "TargetMode='External'/>"
            '<Relationship Id="r3" Type="http://x/hyperlink" Target="https://good.ge/" '
            'TargetMode="External"/><Relationship Id="r4" Type="http://x/image" '
            'Target="media/i.png"/></Relationships>')
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/document.xml", "<w/>")
    with zipfile.ZipFile(convert.without_links(src, str(tmp_path))) as z:
        got = z.read("word/_rels/document.xml.rels").decode()
        assert z.read("word/document.xml") == b"<w/>"
    assert "evil" not in got and got.count("furceli-blocked") == 2
    assert "https://good.ge/" in got and "media/i.png" in got


def test_f4_old_format_from_internet_is_refused(tmp_path):
    src = str(tmp_path / "a.doc")
    with open(src, "wb") as f:
        f.write(b"old word")
    assert not files.from_internet(src)
    with open(src + ":Zone.Identifier", "w") as f:
        f.write("[ZoneTransfer]\nZoneId=3\n")
    assert files.from_internet(src)
    with pytest.raises(EngineError, match="untrusted"):
        convert._safe_source(src, str(tmp_path))
    assert convert._safe_source(src, str(tmp_path), True) == src  # the user agreed


def test_f4_user_is_asked_about_downloaded_files(win, tmp_path, monkeypatch):  # noqa: F811
    src = str(tmp_path / "a.docx")
    with open(src, "wb") as f:
        f.write(b"x")
    with open(src + ":Zone.Identifier", "w") as f:
        f.write("[ZoneTransfer]\nZoneId=3\n")
    asked = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "o.pdf"), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a[-1]) or NO)
    win.make_pdf([src])
    assert "a.docx" in asked[0] and not os.path.exists(tmp_path / "o.pdf")


def test_f4_helper_program_is_ended_with_the_engine(app):  # noqa: F811
    import psutil
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    c = client.EngineClient()
    c._helpers = [(p.pid, psutil.Process(p.pid).create_time()),
                  (os.getpid(), 1.0)]  # a recycled number must never be killed
    c.shutdown()
    assert p.wait(5) is not None


# -- F5 ----------------------------------------------------------------------
@pytest.mark.parametrize("start", [b"<!doctype html>", b"", b"  <!DOCTYPE html>\n", b"\xef\xbb\xbf"])
def test_f5_hostile_html_cannot_read_files_or_run(tmp_path, start):
    src = str(tmp_path / "evil.html")
    other = tmp_path / "private.txt"
    other.write_text("PRIVATE")
    with open(src, "wb") as f:
        f.write(start + f"""<meta charset=utf8><h1>სათაური</h1>
<iframe src="{other.as_uri()}"></iframe><object data="{other.as_uri()}"></object>
<script>document.write('SCRIPT'+'RAN')</script>""".encode())
    out = str(tmp_path / "o.pdf")
    try:
        convert._web(src, out)
    except EngineError as e:
        if "no_edge" in str(e):
            pytest.skip("Edge is not installed")
        raise
    text = "".join(p.get_text() for p in pymupdf.open(out))
    assert "სათაური" in text and "PRIVATE" not in text and "SCRIPTRAN" not in text


# -- F6 ----------------------------------------------------------------------
def test_f6_task_ends_when_client_shuts_down(doc):
    QTimer.singleShot(200, doc.client.shutdown)
    t = time.time()
    with pytest.raises(client.TaskError, match="closed"):
        client.run_task(doc.client, "_sleep", (20,), "x", doc)
    assert time.time() - t < 8
    with pytest.raises(client.TaskError, match="closed"):
        client.run_task(doc.client, "pdf_box", (0, (1, 1, 2, 2)), "", doc)


def test_f6_input_is_blocked_from_the_first_moment(win, doc):  # noqa: F811
    seen = {}

    def try_things():
        QTest.keyClick(doc.view, Qt.Key.Key_End)
        e = QCloseEvent()
        QApplication.sendEvent(win, e)
        seen.update(page=doc.view.page, closed=e.isAccepted(), tabs=win.tabs.count())
    QTimer.singleShot(60, try_things)
    client.run_task(doc.client, "_sleep", (.4,), "x", doc)
    assert seen == {"page": 0, "closed": False, "tabs": 1}
    QTest.keyClick(doc.view, Qt.Key.Key_End)
    assert doc.view.page == 5  # and free again afterwards


def test_f6_second_save_during_a_save_is_ignored(doc):
    doc.edit(("rotate", [0], 90))
    idle(doc)
    doc._overwrite_ok = True
    inner = []
    QTimer.singleShot(0, lambda: inner.append(doc.save()))
    assert doc.save() is True and inner == [False]
    assert len(os.listdir(files.backup_dir())) == 1


def test_f6_thread_helper_returns_and_raises(app):  # noqa: F811
    assert client.run_thread(lambda: threading.current_thread().name) != "MainThread"
    with pytest.raises(OSError):
        client.run_thread(lambda: os.stat("no such file"))


# -- F7 ----------------------------------------------------------------------
def test_f7_leftover_sessions_are_swept(tmp_path, monkeypatch):
    monkeypatch.setattr(files.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(files.tempfile, "tempdir", str(tmp_path))
    mine = files.new_session()
    dead = files.new_session()
    with open(os.path.join(dead, files.MARK), "w") as f:
        f.write("999999 1.0")
    reused = files.new_session()
    with open(os.path.join(reused, files.MARK), "w") as f:
        f.write(f"{os.getpid()} 1.0")  # same number, another program
    foreign = tmp_path / "furceli_other"
    foreign.mkdir()
    assert files.sweep_sessions() == 2
    assert os.path.isdir(mine) and foreign.is_dir()
    assert not os.path.exists(dead) and not os.path.exists(reused)


def test_f7_signature_picture_lives_in_the_session(win, doc, monkeypatch):  # noqa: F811
    from PySide6.QtCore import QPointF

    def run(dlg):
        dlg.canvas.strokes = [[QPointF(20, 20), QPointF(200, 120)]]
        dlg._check()
        return True
    monkeypatch.setattr(forms.SignaturePad, "exec", run)
    win.a_sigpic.trigger()
    folder = doc._tmp
    assert doc.pending_image.startswith(folder)
    win.close_tab(0, ask=False)
    __import__("furceli.client").client.finish_all()  # the folder goes after the engine
    assert not os.path.exists(folder)


def test_f7_word_export_leaves_no_decrypted_copy(tmp_path, monkeypatch):
    locked = str(tmp_path / "l.pdf")
    pymupdf.open(build(tmp_path / "a.pdf")).save(
        locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    import contextlib
    seen = []
    real = files.work_dir  # inside the engine's folder when there is one, else a new session

    @contextlib.contextmanager
    def spy():
        with real() as path:
            seen.append(path)
            yield path
    monkeypatch.setattr(files, "work_dir", spy)
    convert.export_as(engine(locked, "u"), "docx", str(tmp_path / "o.docx"), [0])
    assert seen and not any(os.path.exists(p) for p in seen)


def test_f7_old_unfinished_saves_are_removed(tmp_path):
    target = str(tmp_path / "a.pdf")
    old, new, other = (str(tmp_path / n) for n in (
        ".a.pdf.furceli.tmp", ".a.pdf.furceli (2).tmp", ".b.pdf.furceli.tmp"))
    for p in (old, new, other):
        with open(p, "w") as f:
            f.write("x")
    for p in (old, other):
        os.utime(p, (1, 1))
    files.drop_stale_temps(target)
    assert [os.path.exists(p) for p in (old, new, other)] == [False, True, True]


@pytest.mark.parametrize("answer,left", [(YES, 0), (NO, 1)])
def test_f7_backup_purge_is_offered_after_redaction(doc, monkeypatch, answer, left):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a[-1]) or answer)
    doc._overwrite_ok = True
    doc.edit(("redact", {0: [(60, 50, 250, 90)]}, {"fill": (0, 0, 0)}))
    idle(doc)
    assert doc.save()
    assert len(asked) == 1 and len(os.listdir(files.backup_dir())) == left
    doc.edit(("rotate", [1], 90))  # an ordinary later save does not ask again
    idle(doc)
    assert doc.save() and len(asked) == 1


# -- F8 ----------------------------------------------------------------------
def test_f8_password_is_known_after_saving(win, doc, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(QMessageBox, "question", lambda *a: NO)
    doc._overwrite_ok = True
    doc.edit(("protect", {"mode": "set", "user": "ახალი", "owner": "", "perms": dict.fromkeys(
        ("print", "copy", "modify", "annotate"), True)}))
    idle(doc)
    assert doc.save()
    assert doc._password == "ახალი" and doc.info["protected"]
    assert "AES" in doc.info["metadata"]["encryption"]
    pfx, out = str(tmp_path / "c.pfx"), files.outbox(str(tmp_path / "s.pdf"))  # engine's folder
    signing.make_certificate(pfx, "secret", "ნინო")
    doc._task("sign", (doc.path, out, doc._password, pfx, "secret", {}), "")
    assert doc._quiet("verify", out, doc._password)[0]["intact"]
    doc.undo()  # the snapshot still has its own (empty) password
    idle(doc)
    assert doc.save() and doc._password == "" and not doc.info["protected"]
    assert not pymupdf.open(doc.path).needs_pass


# -- F9 ----------------------------------------------------------------------
def test_f9_signature_box_on_rotated_page_with_offset_boxes(tmp_path):
    for rot in (0, 90, 180, 270):
        d = pymupdf.open()
        p = d.new_page(width=700, height=900)
        d.xref_set_key(p.xref, "MediaBox", "[50 60 750 960]")
        d.xref_set_key(p.xref, "CropBox", "[120 150 650 800]")
        d.xref_set_key(p.xref, "Rotate", str(rot))
        path = str(tmp_path / f"{rot}.pdf")
        d.save(path)
        e = engine(path)
        shown = (20, 100, 120, 130)
        box = e.pdf_box(0, shown)
        if rot == 90:
            assert np.allclose(box, (220, 170, 250, 270))
        pfx, out = str(tmp_path / "c.pfx"), str(tmp_path / f"s{rot}.pdf")
        if not os.path.exists(pfx):
            signing.make_certificate(pfx, "pw", "ნინო ტესტი")
        signing.sign(path, out, "", pfx, "pw", {"page": 0, "box": box})
        s = engine(out)
        field = s.widgets(0)[-1]
        assert np.allclose(field["rect"], shown, atol=1.5), (rot, field["rect"])
        pix = s.doc[0].get_pixmap(colorspace=pymupdf.csGRAY)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
        ys, xs = np.where(a < 200)
        assert len(xs) and xs.min() >= 18 and xs.max() <= 122 and ys.min() >= 98 and ys.max() <= 132
        assert "ნინო" in s.doc[0].get_text()


def test_f9_bookmarks_on_rotated_page_with_offset_boxes(tmp_path):
    d = pymupdf.open()
    for rot in (0, 90, 180, 270):
        p = d.new_page(width=700, height=900)
        d.xref_set_key(p.xref, "MediaBox", "[50 60 750 960]")
        d.xref_set_key(p.xref, "CropBox", "[120 150 650 800]")
        d.xref_set_key(p.xref, "Rotate", str(rot))
    src = str(tmp_path / "a.pdf")
    d.save(src)
    from furceli.engine import _set_toc
    d = pymupdf.open(src)
    want = [pymupdf.Point(40, 100 + 10 * k) for k in range(4)]
    _set_toc(d, [[1, str(k), k + 1, {"kind": 1, "page": k, "to": want[k], "zoom": 0}]
                 for k in range(4)])
    d.save(str(tmp_path / "b.pdf"))
    toc = pymupdf.open(str(tmp_path / "b.pdf")).get_toc(simple=False)
    for k in range(4):
        page = pymupdf.open(str(tmp_path / "b.pdf"))[k]
        m = re_xyz(page.parent, toc[k][3]["xref"])
        back = pymupdf.Point(m[0] - page.cropbox.x0, page.mediabox.y1 - page.cropbox.y0 - m[1])
        back = back * page.rotation_matrix
        assert abs(back.x - want[k].x) < 1 and abs(back.y - want[k].y) < 1, k


def re_xyz(doc, xref):
    import re
    nums = re.findall(r"XYZ\s+([\d.\-]+)\s+([\d.\-]+)", doc.xref_object(xref, compressed=True))[0]
    return [float(n) for n in nums]


# -- F10 ---------------------------------------------------------------------
def test_f10_marks_survive_a_comment_and_follow_moved_pages(doc, monkeypatch):
    mark = (60.0, 50.0, 250.0, 90.0)
    doc.view.marks = {0: [mark], 2: [mark]}
    doc.edit(("annot", 0, "rect", {"rect": (300, 300, 350, 350)}, doc._meta()))
    idle(doc)
    assert doc.view.marks == {0: [mark], 2: [mark]}
    doc.move_pages([0], 6)
    idle(doc)
    assert doc.view.marks == {5: [mark], 1: [mark]}
    said = []
    doc.status.connect(said.append)
    doc.edit(("rotate", [5], 180))  # same size, but the mark would now cover something else
    idle(doc)
    assert doc.view.marks == {1: [mark]} and any("მოიხსნა" in t for t in said)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: YES)
    doc.apply_redactions()
    idle(doc)
    assert doc.view.marks == {} and not doc._quiet("search", 1, "ASECRET2")


# -- F11 ---------------------------------------------------------------------
@pytest.fixture
def panel(win, doc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(QMessageBox, "question", lambda *a: YES)
    p = ai.AiPanel(doc, win.settings, lambda: doc._quiet("page_texts"), FakeClient())
    p.fake = p._client
    return p


def test_f11_prices():
    u = dict(input=1000, write=1000, read=1000, output=1000)
    assert abs(ai.price("claude-opus-5-5", u) - (4 + 5 + .2 + 20) / 1000) < 1e-9
    assert abs(ai.price("claude-sonnet-5-5", u) - (2 + 2.5 + .1 + 10) / 1000) < 1e-9
    big = dict(input=200_000, write=0, read=0, output=0)
    assert abs(ai.price("claude-haiku-5-5", big) - .1) < 1e-9
    assert ai.price("claude-opus-4-8", u) > ai.price("claude-opus-5-5", u)
    assert ai.price("something-new", u) == ai.price("claude-opus-4-8", u)


def test_f11_stop_closes_the_stream_and_late_answers_are_ignored(panel):
    closed = []
    chat = ai.Chat("", "claude-opus-5-5", ["x"], panel.fake)
    chat._stream = types.SimpleNamespace(close=lambda: closed.append(1))
    chat.stop()
    assert closed
    panel.ask("ერთი")
    wait(lambda: panel.chat and not panel.chat.busy and panel._idle)
    old = panel.chat
    panel.reset()
    old._stopped = False  # as if the answer had been queued just before the reset
    old.finished.emit([("ძველი", [])], dict(input=1, write=0, read=0, output=1), "claude-opus-5-5")
    QApplication.processEvents()
    assert "ძველი" not in panel.log.toPlainText()


def test_f11_refusal_is_billed_and_its_text_removed(panel):
    panel.fake.stop = "refusal"
    panel.ask("ერთი")
    wait(lambda: panel._idle and panel.chat)
    assert "ღირებულება" not in panel.log.toPlainText() and panel.spent > 0


def test_f11_consent_names_each_document(panel, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a[-1]) or YES)
    for q in ("ერთი", "ორი"):
        panel.ask(q)
        wait(lambda: panel.chat and not panel.chat.busy and panel._idle)
    assert len(asked) == 1 and "ექვსი.pdf" in asked[0]
    panel.doc.path = panel.doc.path.replace("ექვსი", "სხვა")
    panel.ask("სამი")
    wait(lambda: panel.chat and not panel.chat.busy and panel._idle)
    assert len(asked) == 2 and "სხვა.pdf" in asked[1]


def test_f11_copy_protected_text_is_not_sent(panel):
    panel.doc.info["can_copy"] = False
    panel.ask("რა წერია?")
    assert not panel.fake.calls and "არ ვაგზავნი" in panel.log.toPlainText()


# -- F12 ---------------------------------------------------------------------
def test_f12_excel_text_is_exact_and_never_a_formula(tmp_path):
    import openpyxl
    d = pymupdf.open()
    p = d.new_page()
    texts = ["+995 555 123456", "=1+1", "-5", "-1 250,50", "@x", "5-7"]
    for r, t in enumerate(texts):
        p.draw_rect(pymupdf.Rect(50, 50 + r * 25, 250, 75 + r * 25))
        p.draw_rect(pymupdf.Rect(250, 50 + r * 25, 350, 75 + r * 25))
        p.insert_text((55, 68 + r * 25), t)
        p.insert_text((255, 68 + r * 25), "x")
    path = str(tmp_path / "a.pdf")
    d.save(path)
    out = str(tmp_path / "o.xlsx")
    convert.export_as(engine(path), "xlsx", out, [0])
    cells = [row[0] for row in openpyxl.load_workbook(out).worksheets[0].iter_rows()]
    assert [c.value for c in cells] == texts
    assert all(c.data_type == "s" for c in cells)


# -- F13 ---------------------------------------------------------------------
def test_f13_document_text_is_never_html(win, tmp_path):  # noqa: F811
    d = pymupdf.open()
    d.new_page()
    d.set_toc([[1, "<b>x</b>", 1]])
    path = str(tmp_path / "t.pdf")
    d.save(path)
    doc = open_doc(win, path)
    assert doc.toc.topLevelItem(0).toolTip(0) == "&lt;b&gt;x&lt;/b&gt;"
    assert win.link_label.textFormat() == Qt.TextFormat.PlainText


# -- F14 ---------------------------------------------------------------------
def test_f14_visible_rows_match_the_slow_answer(win, tmp_path):  # noqa: F811
    doc = open_doc(win, build(tmp_path / "many.pdf", 60))
    doc.organize(True)
    QApplication.processEvents()
    g = doc.grid
    for pos in (0, 300, 900, g.verticalScrollBar().maximum()):
        g.verticalScrollBar().setValue(pos)
        vr = g.viewport().rect()
        rows = [r for r in range(60) if g.visualRect(g.model_.index(r)).intersects(vr)]
        a, b = g.visible_rows()
        assert a <= rows[0] and b >= rows[-1] and b - a <= len(rows) + 8


def test_f14_failed_image_export_leaves_nothing(tmp_path):
    e = engine(build(tmp_path / "a.pdf"))
    targets = [(0, str(tmp_path / "1.png")), (99, str(tmp_path / "2.png"))]
    with pytest.raises(EngineError):
        convert.to_images(e, targets, 60)
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".png")]


def test_f14_failed_print_is_aborted(win, doc, tmp_path, monkeypatch):  # noqa: F811
    pr = QPrinter(QPrinter.PrinterMode.HighResolution)
    pr.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    pr.setOutputFileName(str(tmp_path / "o.pdf"))
    aborted = []
    monkeypatch.setattr(pr, "abort", lambda: aborted.append(1) or True, raising=False)

    def boom(*a):
        raise printing.PrintError("x")
    monkeypatch.setattr(printing, "_render", boom)
    with pytest.raises(printing.PrintError):
        printing.print_pages(doc, pr, [0], win)
    assert aborted


def test_f14_moving_a_comment_can_be_abandoned(win, doc, monkeypatch):  # noqa: F811
    doc.view.set_fit("page")
    doc.edit(("annot", 0, "rect", {"rect": (300, 300, 350, 350)}, doc._meta()))
    idle(doc)
    a = next(a for p, a in doc._quiet("all_annots") if a["type"] == "Square")
    doc._move_comment(0, a)
    win.tool_acts["rect"].trigger()  # the user changes their mind
    drag(doc, 0, (100, 500), (200, 560))
    idle(doc)
    assert sum(a["type"] == "Square" for _, a in doc._quiet("all_annots")) == 2
    doc._move_comment(0, a)
    QTest.mouseClick(doc.view.viewport(), Qt.MouseButton.LeftButton, pos=vp(doc.view, 0, 400, 600))
    idle(doc)
    rects = [a["rect"] for _, a in doc._quiet("all_annots") if a["type"] == "Square"]
    assert any(abs(r[0] - 400) < 4 and abs(r[1] - 600) < 4 for r in rects)


def test_f14_replacing_keeps_file_rights_and_streams(tmp_path):
    target, tmp = str(tmp_path / "a.pdf"), str(tmp_path / "n.tmp")
    for p, t in ((target, "old"), (tmp, "new")):
        with open(p, "w") as f:
            f.write(t)
    with open(target + ":note", "w") as f:
        f.write("kept")
    files._replace(tmp, target)
    assert read(target) == b"new" and not os.path.exists(tmp)
    with open(target + ":note") as f:
        assert f.read() == "kept"


# -- gaps the review listed ----------------------------------------------------
def test_verify_reports_changes_made_after_signing(tmp_path):
    pfx, out = str(tmp_path / "c.pfx"), str(tmp_path / "s.pdf")
    signing.make_certificate(pfx, "pw", "x")
    signing.sign(build(tmp_path / "a.pdf"), out, "", pfx, "pw", {})
    d = pymupdf.open(out)
    d[0].insert_text((72, 400), "ADDED LATER")
    d.saveIncr()
    d.close()
    row = signing.verify(out)[0]
    text = forms.VerifyDialog.describe(row)
    assert row["modified_badly"] or not row["intact"]
    assert any(t.startswith("✖") for t in text)
    assert QEvent is not None
