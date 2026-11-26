"""Regressions for the second independent review (bypasses of the first fixes, N1-N11)."""
import os
import re
import time
import types
import zipfile

import numpy as np
import psutil
import pymupdf
import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialog, QMessageBox, QProgressDialog, QPushButton,
                               QVBoxLayout)
from test_features import blank, engine, picture, saved
from test_pages import build, holds
from test_review import crash
from test_ui import app, open_doc, settled, wait, win  # noqa: F401
from test_ui_pages import idle, no_dialogs, read  # noqa: F401
from test_ui_tools import FakeClient

from furceli import ai, client, convert, files, signing
from furceli.engine import EngineError, _set_toc, _to_pdf

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


@pytest.fixture
def doc(win, tmp_path):  # noqa: F811
    return open_doc(win, build(tmp_path / "ექვსი.pdf"))


# -- N1: Office relationships -------------------------------------------------
NS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
IMG = 'Type="http://x/relationships/image"'


def rels(one, encoding="utf-8", head=""):
    return f'<?xml version="1.0" encoding="{encoding}"?>{head}<Relationships {NS}>{one}</Relationships>'.encode(
        encoding)


@pytest.mark.parametrize("one", [
    f'<Relationship Id="r" {IMG} Target="http://evil.example/hyperlink" TargetMode="External"/>',
    f'<Relationship Id="r" {IMG} Target="http://evil.example/a" TargetMode="Extern&#97;l"/>',
    f'<Relationship Id="r" {IMG} Target = "http://evil.example/a" TargetMode = "External"/>',
    f'<Relationship Id="a&gt;b" {IMG} Target="http://evil.example/a" TargetMode="External"/>',
    f'<Relationship Id="a>b" {IMG} Target="http://evil.example/a" TargetMode="External"/>',
    f'<Relationship Id="r" {IMG} Target="\\\\evil.example\\s\\a" TargetMode="external"/>',
    f'<Relationship Id="r" {IMG} Target="http://evil.example/a"/>',  # no mode at all
    f'<Relationship Id="r" {IMG} Target="//evil.example/a"/>',
    '<Relationship Id="r" Type="http://x/hyperlink" Target="\\\\evil.example\\s" TargetMode="External"/>',
    f'<x:Relationship xmlns:x="http://x" Id="r" {IMG} Target="http://evil.example/a" TargetMode="External"/>',
])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_n1_no_spelling_gets_through(one, encoding):
    assert b"evil" not in convert._clean_rels(rels(one, encoding))


@pytest.mark.parametrize("data", [
    b"<Relationships><Relationship", b"not xml",
    rels("&e;", head='<!DOCTYPE r [<!ENTITY e "x">]>')])
def test_n1_unreadable_parts_refuse_the_file(data):
    with pytest.raises(EngineError, match="untrusted"):
        convert._clean_rels(data)


def test_n1_ordinary_parts_survive(tmp_path):
    out = convert._clean_rels(rels(
        f'<Relationship Id="a" {IMG} Target="media/i.png"/>'
        '<Relationship Id="b" Type="http://x/hyperlink" Target="https://good.ge/" TargetMode="External"/>'
        '<Relationship Id="c" Type="http://x/aFChunk" Target="/word/evil.rtf"/>'))
    assert b"media/i.png" in out and b"https://good.ge/" in out and b"evil.rtf" not in out


def test_n1_whole_file(tmp_path):
    src = str(tmp_path / "a.xlsx")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("xl/connections.xml", '<connections><connection refreshOnLoad="1"/></connections>')
        z.writestr("xl/_RELS/x.RELS", rels(f'<Relationship {IMG} Target="http://evil/" TargetMode="External"/>'))
    with open(src + ":Zone.Identifier", "w") as f:
        f.write("[ZoneTransfer]\nZoneId=3\n")
    out = convert.without_links(src, str(tmp_path))
    with zipfile.ZipFile(out) as z:
        assert b'refreshOnLoad="0"' in z.read("xl/connections.xml")
        assert b"evil" not in z.read("xl/_RELS/x.RELS")
    assert files.from_internet(out)  # Office stays as careful as with the original
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("word/a.rels", rels(""))
        z.writestr("word/A.rels", rels(""))
    with pytest.raises(EngineError, match="untrusted"):
        convert.without_links(src, str(tmp_path))


def test_n1_real_office_files_still_convert(tmp_path):
    import docx
    import openpyxl
    d = docx.Document()
    d.add_paragraph("ქართული")
    d.add_picture(picture(tmp_path))
    d.save(str(tmp_path / "a.docx"))
    b = openpyxl.Workbook()
    b.active["A1"] = "ქართული"
    b.save(str(tmp_path / "a.xlsx"))
    again = docx.Document(convert.without_links(str(tmp_path / "a.docx"), str(tmp_path)))
    assert again.paragraphs[0].text == "ქართული" and len(again.inline_shapes) == 1
    assert openpyxl.load_workbook(convert.without_links(
        str(tmp_path / "a.xlsx"), str(tmp_path))).active["A1"].value == "ქართული"


# -- N2: HTML -----------------------------------------------------------------
HOSTILE = [
    '<img src="{other}">', '<img src="../secret.png">', '<img src="{uri}">',
    '<img src="file://host/share/x.png">', "<img src='//host/share/x.png'>",
    '<img src="\\\\host\\share\\x.png">', '<img srcset="{uri} 1x">',
    '<svg><image href="{uri}"/></svg>', '<svg><image xlink:href="{uri}"/></svg>',
    '<svg><use href="{uri}#a"/></svg>', '<style>body{{background:url("{uri}")}}</style>',
    '<div style="background:url({uri})">x</div>', '<style>@import "{uri}";</style>',
    '<link rel="stylesheet" href="{uri}">', '<link rel="preload" href="{uri}">',
    '<meta http-equiv="refresh" content="0;url={uri}">', '<base href="file://host/">',
    '<iframe src="{uri}"></iframe>', '<object data="{uri}"></object>', '<embed src="{uri}">',
    '<video poster="{uri}" src="{uri}"></video>', '<body background="{uri}">',
    '<table background="{uri}"><tr><td>x</td></tr></table>', '<a href="file://host/x" ping="{uri}">x</a>',
    '<form action="{uri}"><button formaction="{uri}">x</button></form>',
    "<script>location='{uri}'</script>", '<img src=x onerror="location=\'{uri}\'">',
    '<IMG SRC="{uri}">', '<img src=" {uri}">', '<input type=image src="{uri}">',
    '<picture><source srcset="{uri}"><img></picture>', '<!--[if IE]><img src="{uri}"><![endif]-->']


@pytest.mark.parametrize("bad", HOSTILE)
def test_n2_nothing_outside_the_folder_is_referenced(tmp_path, bad):
    home = tmp_path / "home"
    home.mkdir()
    secret = tmp_path / "secret.png"
    os.rename(picture(tmp_path), secret)
    page = bad.format(uri=secret.as_uri(), other=str(secret))
    out = convert.harmless_html(f"<h1>სათაური</h1>{page}".encode(), str(home)).decode()
    assert "სათაური" in out and "secret" not in out and "host" not in out, out
    assert "data:image" not in out


def test_n2_own_pictures_and_styles_are_kept(tmp_path):
    os.mkdir(tmp_path / "img")
    os.rename(picture(tmp_path), tmp_path / "img" / "ა ბ.png")
    (tmp_path / "s.css").write_text("h1{color:red} @import 'x'; p{background:url(http://evil/)}",
                                    encoding="utf8")
    out = convert.harmless_html(
        '<link rel=stylesheet href=s.css><h1>x</h1><img src="img/%E1%83%90%20%E1%83%91.png" width=50>'
        '<a href="https://good.ge/">l</a>'.encode(), str(tmp_path)).decode()
    assert out.count("data:image/png;base64,") == 1 and "color:red" in out
    assert "evil" not in out and "https://good.ge/" in out and 'width="50"' in out


@pytest.mark.parametrize("enc,head", [("utf-8", ""), ("utf-8-sig", ""), ("utf-16", ""),
                                      ("utf-8", '<?xml version="1.0" encoding="utf-8"?>')])
def test_n2_georgian_in_every_encoding(tmp_path, enc, head):
    out = convert.harmless_html(f"{head}<p>ქართული ტექსტი</p>".encode(enc), str(tmp_path))
    assert "ქართული ტექსტი" in out.decode()


def test_n2_through_the_browser(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    secret = tmp_path / "secret.png"
    os.rename(picture(tmp_path), secret)
    os.rename(picture(tmp_path), home / "own.png")
    src = home / "p.html"
    src.write_text(f"<meta charset=utf8><h1>სათაური</h1><img src=own.png><img src='{secret.as_uri()}'>"
                   f"<svg width=99 height=99><image href='{secret.as_uri()}' width=99 height=99/></svg>",
                   encoding="utf8")
    out = str(tmp_path / "o.pdf")
    try:
        convert._web(str(src), out)
    except EngineError as e:
        if "no_edge" in str(e):
            pytest.skip("Edge is not installed")
        raise
    d = pymupdf.open(out)
    assert "სათაური" in d[0].get_text() and len(d[0].get_images()) == 1


# -- N3: input during a task --------------------------------------------------
def test_n3_dialog_that_started_a_task_is_blocked(win, doc):  # noqa: F811
    dlg, hits = QDialog(win), []
    b = QPushButton("x", dlg)
    QVBoxLayout(dlg).addWidget(b)
    b.clicked.connect(lambda: hits.append(1))
    dlg.setModal(True)
    dlg.show()
    for text in ("", "x"):
        for at in (50, 250):
            QTimer.singleShot(at, lambda: QTest.mouseClick(b, Qt.MouseButton.LeftButton))
        client.run_task(doc.client, "_sleep", (.5,), text, dlg)
    assert not hits
    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    assert hits == [1]
    dlg.close()


def test_n3_progress_with_its_own_cancel_stays_usable(win, doc):  # noqa: F811
    bar = QProgressDialog("x", "გაუქმება", 0, 5, win)
    bar.setWindowModality(Qt.WindowModality.WindowModal)
    bar.show()
    button = bar.findChild(QPushButton)
    QTimer.singleShot(80, lambda: QTest.mouseClick(button, Qt.MouseButton.LeftButton))
    with pytest.raises(client.TaskError, match="cancelled"):  # and it stops the task itself
        client.run_task(doc.client, "_sleep", (8,), "", None, allow=bar)
    assert bar.wasCanceled()
    bar.close()
    bar.deleteLater()


# -- N4: restore is retried -----------------------------------------------------
def test_n4_restore_survives_one_bad_attempt(doc, monkeypatch):
    doc.edit(("rotate", [0], 90))
    idle(doc)
    real, n = doc.client.submit, []

    def flaky(key, prio, op, *a):
        if key[0] == "restore" and not n:
            n.append(1)
            QTimer.singleShot(0, lambda: doc.client.failed.emit(key, "crash"))
            return
        real(key, prio, op, *a)
    monkeypatch.setattr(doc.client, "submit", flaky)
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    doc.edit(("crop", [0], (900, 0, 900, 0)))  # fails, so the document is rebuilt
    wait(lambda: told and n)
    idle(doc)
    assert not doc.broken and doc.sizes[0] == (800, 600) and len(doc.ops) == 1
    doc._overwrite_ok = True
    assert doc.save() and pymupdf.open(doc.path)[0].rotation == 90


def test_n4_user_can_keep_trying(doc, monkeypatch):
    doc.edit(("rotate", [0], 90))
    idle(doc)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(1) or YES)
    real, n = doc.client.submit, []

    def flaky(key, prio, op, *a):
        if key[0] == "restore" and len(n) < 3:
            n.append(1)
            QTimer.singleShot(0, lambda: doc.client.failed.emit(key, "crash"))
            return
        real(key, prio, op, *a)
    monkeypatch.setattr(doc.client, "submit", flaky)
    doc._start("restore", "replay", doc._base, doc._base_password, doc.ops)
    wait(lambda: len(n) == 3)
    idle(doc)
    assert asked and not doc.broken and doc.sizes[0] == (800, 600)


def test_f1_end_to_end_failed_undo_then_save(doc, monkeypatch):
    """An undo that crashes the engine, a rebuild that works, and a correct save."""
    doc.edit(("rotate", [0], 90))
    idle(doc)
    doc.edit(("order", [0, 1, 2, 3, 4]))
    idle(doc)
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    real = doc.client.submit

    def once(key, prio, op, *a):
        if key[0] == "undo":
            return real(key, prio, "_crash")
        return real(key, prio, op, *a)
    monkeypatch.setattr(doc.client, "submit", once)
    doc.undo()
    wait(lambda: told)
    idle(doc)
    assert len(doc.ops) == 2 and len(doc.sizes) == 5 and not doc.broken
    doc._overwrite_ok = True
    assert doc.save()
    d = pymupdf.open(doc.path)
    assert d.page_count == 5 and d[0].rotation == 90


# -- N5 -------------------------------------------------------------------------
def test_n5_duplicated_marks_are_independent(doc):
    doc.view.marks = {0: [(60, 50, 250, 90)], 1: [(60, 50, 250, 90)]}
    said = []
    doc.status.connect(said.append)
    doc.edit(("duplicate", [0]))
    idle(doc)
    assert sorted(doc.view.marks) == [0, 1, 2] and doc.view.lost_marks == 0
    assert not any("მოიხსნა" in t for t in said)
    doc._unmark(0, (60, 50, 250, 90))
    assert sorted(doc.view.marks) == [1, 2]
    doc.undo()
    idle(doc)
    assert doc.view.marks == {} and doc.view.lost_marks == 2
    assert any("2 ადგილი მოიხსნა" in t for t in said)


# -- N6 -------------------------------------------------------------------------
def test_n6_a_stuck_task_can_be_cancelled_and_edits_survive(win, doc):  # noqa: F811
    doc.edit(("rotate", [0], 90))
    idle(doc)

    def press_cancel():
        bars = [w for w in QApplication.topLevelWidgets()
                if isinstance(w, QProgressDialog) and w.isVisible()
                and w.labelText() == "გაჭედილი ოპერაცია"]
        if not bars:  # a busy machine: look again
            QTimer.singleShot(200, press_cancel)
            return
        QTest.mouseClick(bars[0].findChild(QPushButton), Qt.MouseButton.LeftButton)
    QTimer.singleShot(1200, press_cancel)
    t = time.time()
    with pytest.raises(client.TaskError, match="cancelled"):
        client.run_task(doc.client, "_sleep", (40,), "გაჭედილი ოპერაცია", doc)
    assert time.time() - t < 15 and doc.client._restarts == 0
    doc._overwrite_ok = True
    assert doc.save() and pymupdf.open(doc.path)[0].rotation == 90


def test_n6_cancelling_a_waiting_task(doc):
    doc.client.submit(("task", "busy"), 0, "_sleep", 1.0)
    time.sleep(.2)
    got = []
    doc.client.failed.connect(lambda k, c: got.append((k, c)))
    doc.client.submit(("task", "later"), 0, "pdf_box", 0, (1, 1, 2, 2))
    doc.client.abort(("task", "later"))
    assert got == [(("task", "later"), "cancelled")]
    doc.client.abort(("task", "unknown"))  # nothing happens
    wait(lambda: doc.client._current is None)
    assert doc._quiet("pdf_box", 0, (1, 1, 2, 2)) is not None


# -- N7 -------------------------------------------------------------------------
def test_n7_deep_scripts_launch_and_script_links(tmp_path):
    d = pymupdf.open(build(tmp_path / "a.pdf"))
    deep = "<</S/JavaScript/JS(EVILDEEP)>>"
    for _ in range(90):
        deep = "<</K " + deep + ">>"
    d.xref_set_key(d.pdf_catalog(), "Deep", deep)
    d.xref_set_key(d[0].xref, "AA", "<</O<</S/Launch/F(EVILLAUNCH.exe)>>>>")
    link = next(x for x in range(1, d.xref_length()) if "https://a.b/" in d.xref_object(x))
    d.xref_set_key(link, "A", "<</S/URI/URI( JavaScript:EVILURI)>>")
    d.xref_set_key(d.pdf_catalog(), "AcroForm/XFA", "[(t)(EVILXFA)]")
    path = str(tmp_path / "b.pdf")
    d.save(path)
    assert holds(pymupdf.open(path), "EVILDEEP")
    e = engine(path)
    e.apply(("scrub", {}))
    r = saved(e, tmp_path)
    for word in ("EVILDEEP", "EVILLAUNCH", "EVILURI", "EVILXFA"):
        assert not holds(r, word), word
    keep = engine(path)
    keep.apply(("scrub", {"javascript": False}))
    assert holds(saved(keep, tmp_path, "k.pdf"), "EVILDEEP")


# -- N8 -------------------------------------------------------------------------
def test_n8_limits_apply_in_the_same_tab(win, doc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(QMessageBox, "question", lambda *a: NO)
    doc._overwrite_ok = True
    doc.edit(("protect", {"mode": "set", "user": "u", "owner": "o",
                          "perms": {"print": False, "copy": False, "modify": True, "annotate": True}}))
    idle(doc)
    assert doc.save()
    assert not doc.info["can_copy"] and not doc.view.can_copy and not win.a_print.isEnabled()
    assert doc.info["can_modify"]
    doc.undo()
    idle(doc)
    assert doc.save() and doc.info["can_copy"] and doc.view.can_copy and win.a_print.isEnabled()


# -- N9 and F9, checked by drawing, not by inverting the same formula -----------
BOXES = [("[50 60 750 960]", "[120 150 650 800]"), ("[0 0 600 800]", "[-30 -20 640 830]"),
         ("[50 60 750 960]", "[0 0 800 1000]"), ("[750 960 50 60]", "[120 150 650 800]")]


@pytest.mark.parametrize("media,crop", BOXES)
@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_n9_points_land_where_they_are_drawn(tmp_path, media, crop, rot):
    d = pymupdf.open()
    p = d.new_page()
    for key, v in (("MediaBox", media), ("CropBox", crop), ("Rotate", str(rot))):
        d.xref_set_key(p.xref, key, v)
    path = str(tmp_path / "a.pdf")
    d.save(path)
    d = pymupdf.open(path)
    p = d[0]
    want = pymupdf.Point(200, 120)
    at = _to_pdf(p, want)
    x = d.get_new_xref()
    d.update_object(x, "<<>>")
    d.update_stream(x, f"0 g {at.x - 4:g} {at.y - 4:g} 8 8 re f".encode())
    d.xref_set_key(p.xref, "Contents", f"{x} 0 R")
    d.save(str(tmp_path / "b.pdf"))
    pix = pymupdf.open(str(tmp_path / "b.pdf"))[0].get_pixmap(colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    ys, xs = np.where(a < 100)
    assert len(xs) and abs(xs.mean() - 199.5) < 1.5 and abs(ys.mean() - 119.5) < 1.5


def test_f9_bookmarks_go_to_the_drawn_point(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    for key, v in (("MediaBox", BOXES[0][0]), ("CropBox", BOXES[0][1]), ("Rotate", "270")):
        d.xref_set_key(p.xref, key, v)
    src = str(tmp_path / "a.pdf")
    d.save(src)
    d = pymupdf.open(src)
    _set_toc(d, [[1, "t", 1, {"kind": 1, "page": 0, "to": pymupdf.Point(200, 120), "zoom": 0}]])
    raw = d.xref_object(d.get_outline_xrefs()[0], compressed=True)
    x, y = map(float, re.findall(r"XYZ\s+([\d.\-]+)\s+([\d.\-]+)", raw)[0])
    s = d.get_new_xref()
    d.update_object(s, "<<>>")
    d.update_stream(s, f"0 g {x - 4:g} {y - 4:g} 8 8 re f".encode())
    d.xref_set_key(d[0].xref, "Contents", f"{s} 0 R")
    d = pymupdf.open(stream=d.tobytes(), filetype="pdf")
    pix = d[0].get_pixmap(colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    ys, xs = np.where(a < 100)
    assert abs(xs.mean() - 199.5) < 1.5 and abs(ys.mean() - 119.5) < 1.5


# -- N10: helper programs --------------------------------------------------------
class FakePipe:
    def __init__(self, answers):
        self.answers = list(answers)

    def send(self, _):
        pass

    def poll(self, _):
        return bool(self.answers)

    def recv(self):
        return self.answers.pop(0)


def test_n10_started_and_ended_messages(app):  # noqa: F811
    c = client.EngineClient()
    c._conn = FakePipe([("started", (11, 1.0)), ("started", (12, 2.0)), ("ended", (11, 1.0)),
                        ("ok", 5)])
    assert c._ask("make_pdf", ()) == ("ok", 5) and c._helpers == [(12, 2.0)]
    c._helpers = []
    c._conn = None
    c.shutdown()


def test_n10_office_is_recognised_by_its_window(tmp_path):
    """Two programs started at once must not be taken for each other."""
    import pythoncom
    pythoncom.CoInitialize()
    told = []
    convert.notify = told.append
    try:
        with convert._Com("Excel.Application") as a, convert._Com("Excel.Application") as b:
            a.own(a.app.Hwnd)
            b.own(b.app.Hwnd)
            assert a.helper[0] != b.helper[0]
            for h in (a.helper, b.helper):
                line = " ".join(psutil.Process(h[0]).cmdline()).lower()
                assert "automation" in line or "embedding" in line  # never the user's own
    except EngineError:
        pytest.skip("Office is not installed")
    finally:
        convert.notify = None
    assert [t[0] for t in told] == ["started", "started", "ended", "ended"]


# -- N11 -------------------------------------------------------------------------
class SlowStream:
    def __init__(self):
        self.closed = False
        usage = types.SimpleNamespace(input_tokens=1_000_000, output_tokens=10,
                                      cache_creation_input_tokens=0, cache_read_input_tokens=0)
        self.current_message_snapshot = types.SimpleNamespace(usage=usage, model="claude-opus-5-5")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        self.closed = True

    @property
    def text_stream(self):
        for _ in range(400):
            if self.closed:
                raise RuntimeError("closed")
            yield "სიტყვა "
            time.sleep(.01)


@pytest.mark.parametrize("how", ["button", "reset"])
def test_n11_stopping_is_possible_and_counted(win, doc, monkeypatch, how):  # noqa: F811
    monkeypatch.setattr(QMessageBox, "question", lambda *a: YES)
    fake, stream = FakeClient(), SlowStream()
    p = ai.AiPanel(doc, win.settings, lambda: doc._quiet("page_texts"), fake)
    fake.messages.stream = fake.beta.messages.stream = lambda **k: stream
    p.ask("რა წერია?")
    wait(lambda: "სიტყვა" in p.log.toPlainText())
    assert p.send.text() == "შეჩერება"
    p.send.click() if how == "button" else p.reset()
    wait(lambda: stream.closed and p._idle and p.spent > 0)
    assert abs(p.spent - 4.0) < .01  # the document was read, so it is billed
    assert p.send.text() == "გაგზავნა"
    if how == "button":
        assert "შეჩერებულია" in p.log.toPlainText() and p.chat._messages == []
        assert "სიტყვა" not in p.log.toPlainText()


# -- F12 on rotated pages ----------------------------------------------------------
@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_f12_numbers_are_exact_on_rotated_pages(tmp_path, rot):
    import openpyxl
    d = pymupdf.open()
    w, h = (600, 800) if rot in (0, 180) else (800, 600)
    p = d.new_page(width=w, height=h)
    p.set_rotation(rot)
    m = p.derotation_matrix
    data = [["-5", "ქართული"], ["-1 250,50", "=1+1"], ["7", "x"]]
    p.insert_font(fontname="sy", fontfile="C:/Windows/Fonts/sylfaen.ttf")
    for r, row in enumerate(data):
        for c, t in enumerate(row):
            p.draw_rect(pymupdf.Rect(50 + c * 150, 100 + r * 30, 200 + c * 150, 130 + r * 30) * m)
            p.insert_text(pymupdf.Point(56 + c * 150, 120 + r * 30) * m, t, fontname="sy",
                          fontsize=11, rotate=rot)
    path = str(tmp_path / "a.pdf")
    d.save(path)
    out = str(tmp_path / "o.xlsx")
    assert convert.export_as(engine(path), "xlsx", out, [0]) == 1
    sheet = openpyxl.load_workbook(out).worksheets[0]
    assert [[c.value for c in row] for row in sheet.iter_rows()] == data


# -- remaining gaps ------------------------------------------------------------------
def test_f7_failed_signature_look_leaves_nothing(tmp_path, monkeypatch):
    pfx = str(tmp_path / "c.pfx")
    signing.make_certificate(pfx, "pw", "x")
    made = []
    real = files.new_session
    monkeypatch.setattr(files, "new_session", lambda: made.append(real()) or made[-1])

    def boom(*a):
        raise ValueError("x")
    monkeypatch.setattr(signing, "_stamp", boom)
    with pytest.raises(EngineError, match="sign_failed"):
        signing.sign(build(tmp_path / "a.pdf"), str(tmp_path / "o.pdf"), "", pfx, "pw",
                     {"page": 0, "box": (1, 1, 90, 90)})
    assert made and not os.path.exists(made[0])


def test_f7_killed_engine_leaves_no_folders(doc, monkeypatch):
    swept = []
    monkeypatch.setattr(files, "sweep_sessions", lambda: swept.append(1) or 0)
    crash(doc)
    assert swept


def test_f14_image_export_is_all_or_nothing_to_the_end(tmp_path, monkeypatch):
    e = engine(build(tmp_path / "a.pdf"))
    real, n = os.rename, []

    def second_fails(a, b):
        n.append(b)
        if len(n) == 2:
            raise OSError("disk")
        real(a, b)
    monkeypatch.setattr(convert.os, "rename", second_fails)
    with pytest.raises(EngineError):
        convert.to_images(e, [(k, str(tmp_path / f"{k}.png")) for k in range(3)], 40)
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".png")]


def test_f14_saving_keeps_access_rights(tmp_path):
    import ntsecuritycon
    import win32security
    target, tmp = str(tmp_path / "a.pdf"), str(tmp_path / "n.tmp")
    for p in (target, tmp):
        with open(p, "w") as f:
            f.write(p)
    info = win32security.DACL_SECURITY_INFORMATION
    guests = win32security.CreateWellKnownSid(win32security.WinBuiltinGuestsSid)
    sd = win32security.GetFileSecurity(target, info)
    dacl = sd.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ, guests)
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(target, info, sd)
    files._replace(tmp, target)
    after = win32security.GetFileSecurity(target, info).GetSecurityDescriptorDacl()
    sids = [after.GetAce(i)[2] for i in range(after.GetAceCount())]
    assert guests in sids and read(target).decode() == tmp


def test_f14_compare_is_fast_and_renders_are_capped(tmp_path):
    d = pymupdf.open(blank(tmp_path, 0, 40))
    for p in d:
        p.insert_text((72, 72), "same words on every page " * 3, fontsize=6)
    a = str(tmp_path / "a.pdf")
    d.save(a)
    d[39].draw_rect(pymupdf.Rect(100, 300, 200, 400), fill=(0, 0, 0))
    b = str(tmp_path / "b.pdf")
    d.save(b)
    t = time.time()
    assert engine(b).compare(a)["pictures"] == [39]
    assert time.time() - t < 6
    big = pymupdf.open()
    big.new_page(width=14000, height=14000)
    path = str(tmp_path / "big.pdf")
    big.save(path)
    t = time.time()
    convert.export_as(engine(path), "pptx", str(tmp_path / "o.pptx"), [0])
    assert time.time() - t < 30


def test_f14_big_copies_leave_the_window_alive(doc, monkeypatch, tmp_path):
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    client.run_thread(lambda: time.sleep(.4))
    timer.stop()
    assert len(ticks) > 10
