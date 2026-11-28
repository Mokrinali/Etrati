"""Regressions for the third review (2026-11); the numbers are those of its report."""
import io
import os
import threading
import time
import types
import zipfile

import numpy as np
import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QLineEdit, QMessageBox,
                               QProgressDialog, QPushButton)
from test_features import blank, engine, form, picture, saved
from test_pages import build
from test_ui import app, open_doc, settled, wait, win  # noqa: F401
from test_ui_pages import idle, no_dialogs, press, read  # noqa: F401

from furceli import client, convert, dialogs, files, forms, scan, scan_dialog, signing, single
from furceli.batch import BatchDialog
from furceli.engine import EngineError

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


@pytest.fixture
def doc(win, tmp_path):  # noqa: F811
    return open_doc(win, build(tmp_path / "ექვსი.pdf"))


def answers(monkeypatch, name, reply):
    """Replace one QMessageBox function; returns what it was asked."""
    asked = []
    monkeypatch.setattr(QMessageBox, name, lambda *a, **k: asked.append(a[2]) or reply)
    return asked


# -- 1: closing while an edit runs -------------------------------------------
def test_1_save_during_an_edit_is_not_a_success(doc):
    assert doc.edit(("rotate", [0], 90))
    assert doc._busy and not doc.dirty
    assert doc.save() is False
    idle(doc)


def test_1_close_waits_for_the_edit_and_saves_it(win, doc, press):  # noqa: F811
    path, old = doc.path, read(doc.path)
    doc.edit(("rotate", [0], 90))
    press("შენახვა", "გადაწერა")
    win.close_tab(win.tabs.indexOf(doc))
    assert read(path) != old and win.tabs.count() == 0
    with pymupdf.open(path) as d:
        assert d[0].rotation == 90


def test_1_close_without_saving_during_an_edit(win, doc, press):  # noqa: F811
    path, old = doc.path, read(doc.path)
    doc.edit(("rotate", [0], 90))
    press("არ შეინახო")
    win.close_tab(win.tabs.indexOf(doc))
    assert read(path) == old and win.tabs.count() == 0


# -- 2: redaction marks made by another program -------------------------------
def test_2_marks_of_other_programs_are_not_applied(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((50, 100), "MINE secret")
    p.insert_text((50, 400), "KEEP THIS LINE")
    p.add_redact_annot(pymupdf.Rect(40, 385, 250, 405))
    path = str(tmp_path / "a.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("redact", {0: [(45, 85, 200, 105)]}, {"fill": (0, 0, 0)}))
    d = saved(e, tmp_path)
    assert d[0].get_text().strip() == "KEEP THIS LINE"
    assert [a.type[1] for a in d[0].annots()] == ["Redact"]  # still waiting, as it came


def test_2_marks_of_other_programs_under_ours_go(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((50, 100), "MINE secret")
    p.add_redact_annot(pymupdf.Rect(45, 85, 200, 105), text="SECRET REPLACEMENT")
    path = str(tmp_path / "a.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("redact", {0: [(45, 85, 200, 105)]}, {"fill": (0, 0, 0)}))
    d = saved(e, tmp_path)
    assert not d[0].get_text().strip() and not list(d[0].annots())


# -- 3: words divided at a line end -------------------------------------------
@pytest.mark.parametrize("tail", ["-", "- ", "-  "])
def test_3_divided_word_is_found(tmp_path, tail):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((50, 100), "some secre" + tail)
    p.insert_text((50, 114), "tword here")
    path = str(tmp_path / "a.pdf")
    d.save(path)
    assert len(engine(path).search(0, "secretword")) == 2


# -- 4: HTML -------------------------------------------------------------------
BAD = '<meta http-equiv="refresh" content="0;url=file:///C:/x.html">'


@pytest.mark.parametrize("page", [
    f"<svg><style>{BAD}</style></svg>", f"<math><style>{BAD}</style></math>",
    f"<svg><title><style>{BAD}</style></title></svg>", f"<xmp>{BAD}</xmp>",
    f"<svg><xmp>{BAD}</xmp></svg>", f"<noembed>{BAD}</noembed>", f"<noframes>{BAD}</noframes>",
    f"<plaintext>{BAD}", f"<svg><noembed>{BAD}</noembed></svg>", f"<textarea>{BAD}</textarea>",
    f"<svg><textarea>{BAD}</textarea></svg>", f"<style><!-- {BAD} --></style>",
    f"<svg><desc><style>{BAD}</style></desc></svg>"])
def test_4_no_markup_hides_in_raw_text(tmp_path, page):
    out = convert.harmless_html(f"<html><body><p>x</p>{page}</body></html>".encode(), str(tmp_path))
    assert b'<meta http-equiv="refresh' not in out


@pytest.mark.parametrize("style", [
    "p{background:url(http://evil.example/a)}", "p{background:\\75rl(http://evil.example/a)}",
    "p{background:u\\72 l('http://evil.example/a')}", '@\\69mport "http://evil.example/a";',
    '@import "http://evil.example/a";', 'p{background:image-set("http://evil.example/a" 1x)}',
    'p{background:-webkit-image-set("http://evil.example/a" 1x)}',
    'p{background:src("http://evil.example/a")}'])
def test_4_styles_load_nothing(tmp_path, style):
    out = convert.harmless_html(f"<style>{style}</style><p>x</p>".encode(), str(tmp_path)).decode()
    assert "evil.example" not in out or not any(
        s in out for s in ("url(", "@import", " image-set(", ":image-set(", "-webkit-image-set(",
                           ":src(", "\\"))


def test_4_linked_style_cannot_hide_markup(tmp_path):
    (tmp_path / "a.css").write_text(f"p{{color:red}} {BAD}")
    out = convert.harmless_html(b'<svg><link rel="stylesheet" href="a.css"></svg>', str(tmp_path))
    assert b'<meta http-equiv="refresh' not in out and b"color:red" in out


def test_4_ordinary_styles_still_work(tmp_path):
    out = convert.harmless_html(
        b"<style><!-- p > b {color:red} --></style><p><b>x</b></p>", str(tmp_path))
    assert b"p > b {color:red}" in out and b"<!--" not in out


# -- 5: the tile cache ----------------------------------------------------------
def real(view):
    return sum(t.sizeInBytes() for t in view._tiles.values())


def test_5_tile_bytes_are_counted_once(doc):
    v = doc.view
    key = next(iter(v._tiles))
    img = v._tiles[key]
    raw = (img.width(), img.height(), img.width() * 3, bytes(img.width() * img.height() * 3))
    for _ in range(3):
        v.on_done(key, raw)
    assert v._tile_bytes == real(v)


def test_5_a_tile_being_rendered_is_not_asked_for_again(app):  # noqa: F811
    c = client.EngineClient()
    gate, calls = threading.Event(), []

    def slow(op, args):
        calls.append(args)
        gate.wait(5)
        return "ok", None
    c._call = slow
    c.submit(("tile", 1), 1, "render")
    wait(lambda: calls)
    c.submit(("tile", 1), 1, "render")
    gate.set()
    wait(lambda: c._current is None and not c._pending)
    time.sleep(.1)
    assert len(calls) == 1
    c.shutdown()


def test_5_hidden_tabs_give_memory_back(win, doc, tmp_path):  # noqa: F811
    big = QImage(1024, 1024, QImage.Format.Format_RGB888)
    for k in range(30):
        doc.view._tiles[("tile", 0, 9.9, 0, k, 0, doc.view.rev)] = big.copy()
        doc.view._tile_bytes += big.sizeInBytes()
    open_doc(win, build(tmp_path / "სხვა.pdf"))
    QApplication.processEvents()
    assert doc.view._tile_bytes == real(doc.view) <= 33 * 1024 * 1024


def test_5_text_of_far_pages_is_forgotten(doc):
    v = doc.view
    wait(lambda: v.page in v.chars)  # the text comes after the picture of the page
    for p in range(5000):
        v.on_done(("chars", 100 + p, v.rev), [])
    assert len(v.chars) <= 400 and v.page in v.chars


# -- 6: the file changes while the engine restarts ------------------------------
def test_6_restart_never_switches_to_another_file(win, doc):  # noqa: F811
    d = pymupdf.open()
    d.new_page().insert_text((50, 50), "REPLACED")
    data = d.tobytes()
    with open(doc.path, "wb") as f:
        f.write(data)
    seen = []
    doc.status.connect(seen.append)
    doc.client.submit(("boom",), 0, "_crash")
    wait(lambda: doc.client._restarts)
    for _ in range(3):
        with pytest.raises(client.TaskError, match="file_changed"):
            client.run_task(doc.client, "search", (0, "REPLACED"))
    doc.view._tiles.clear()
    doc.view.viewport().update()
    wait(lambda: doc.broken)
    assert any(dialogs.ERRORS["file_changed"] in s for s in seen)


def test_6_restart_with_the_same_file_goes_on(doc):
    doc.client.submit(("boom",), 0, "_crash")
    wait(lambda: doc.client._restarts)
    assert client.run_task(doc.client, "page_texts", ()) and not doc.broken


def test_6_own_save_is_not_a_foreign_change(doc, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (doc.path, ""))
    assert doc.save_as()  # nothing was edited, but the file on the disk is new
    doc.client.submit(("boom",), 0, "_crash")
    wait(lambda: doc.client._restarts)
    assert client.run_task(doc.client, "page_texts", ()) and not doc.broken


# -- 7, 8: form fields ------------------------------------------------------------
@pytest.fixture
def pairs(tmp_path):
    """A choice with separate export values, and two check boxes of one field."""
    d = pymupdf.open()
    p = d.new_page()

    def new(s):
        x = d.get_new_xref()
        d.update_object(x, s)
        return x
    ap = d.get_new_xref()
    d.update_object(ap, "<</Type/XObject/Subtype/Form/BBox[0 0 20 20]>>")
    d.update_stream(ap, b" ")
    combo = new("<</Type/Annot/Subtype/Widget/FT/Ch/Ff 131072/T(country)/Rect[50 700 200 720]"
                "/Opt[[(GE)(Georgia)][(US)(America)]]/V(US)/DA(/Helv 10 Tf 0 g)/F 4>>")
    par = d.get_new_xref()

    def box(y, name, on):
        return new(f"<</Type/Annot/Subtype/Widget/Parent {par} 0 R/Rect[50 {y} 70 {y + 20}]"
                   f"/AS/{name if on else 'Off'}/AP<</N<</{name} {ap} 0 R/Off {ap} 0 R>>>>/F 4>>")
    k1, k2 = box(600, "cash", True), box(560, "card", False)
    d.update_object(par, f"<</FT/Btn/T(pay)/V/cash/Kids[{k1} 0 R {k2} 0 R]>>")
    d.xref_set_key(p.xref, "Annots", f"[{combo} 0 R {k1} 0 R {k2} 0 R]")
    d.xref_set_key(d.pdf_catalog(), "AcroForm",
                   f"<</Fields[{combo} 0 R {par} 0 R]/DA(/Helv 0 Tf 0 g)/DR<</Font<</Helv<</Type/Font"
                   "/Subtype/Type1/BaseFont/Helvetica>>>>>>>>")
    path = str(tmp_path / "pairs.pdf")
    d.save(path)
    return path


def test_7_choice_stores_the_export_value(pairs, tmp_path):
    e = engine(pairs)
    w = e.widgets(0)[0]
    assert w["choices"] == ["Georgia", "America"] and w["exports"] == ["GE", "US"]
    e.apply(("fill", 0, 0, "GE"))
    d = saved(e, tmp_path)
    w = next(d[0].widgets())
    assert w.field_value == "GE" and "Georgia" in d[0].get_text(clip=w.rect + (-5, -5, 5, 5))


def test_7_dialog_starts_at_the_current_choice(win, pairs, monkeypatch):  # noqa: F811
    doc = open_doc(win, pairs)
    seen = []

    def item(parent, title, label, items, current, editable):
        seen.append((items, current))
        return "Georgia", True
    monkeypatch.setattr(QInputDialog, "getItem", item)
    doc.field_clicked(0, client.run_task(doc.client, "widgets", (0,))[0])
    idle(doc)
    assert seen == [(["Georgia", "America"], 1)] and doc.ops == [("fill", 0, 0, "GE")]


def marks(e):
    return [w["value"] for w in e.widgets(0) if w["kind"] == "check"]


def test_8_check_boxes_of_one_field(pairs, tmp_path):
    e = engine(pairs)
    assert marks(e) == [True, False]
    e.apply(("fill", 0, 2, True))
    assert marks(e) == [False, True]
    d = saved(e, tmp_path)
    assert marks(engine(str(tmp_path / "out.pdf"))) == [False, True]
    assert {w.field_value for w in d[0].widgets() if w.field_name == "pay"} == {"card"}
    e.apply(("fill", 0, 2, False))
    assert marks(e) == [False, False]


def test_8_single_check_box_still_works(tmp_path):
    e = form(tmp_path)
    i = next(w["index"] for w in e.widgets(0) if w["kind"] == "check")
    for on in (True, False, True):
        e.apply(("fill", 0, i, on))
        assert marks(e) == [on]
    saved(e, tmp_path)
    assert marks(engine(str(tmp_path / "out.pdf"))) == [True]


# -- 9: hidden data ------------------------------------------------------------------
def every_stream(d):
    out = b""
    for x in range(1, d.xref_length()):
        if d.xref_is_stream(x):
            out += d.xref_stream(x) or b""
    return out


ALL = {"hidden": True, "files": True, "metadata": True, "javascript": True, "comments": True}


def layered(tmp_path, inside=False):
    d = pymupdf.open()
    p = d.new_page()
    off, on = d.add_ocg("secret", on=False), d.add_ocg("shown", on=True)
    p.insert_text((50, 100), "HIDDENSECRET (a) \\ b", oc=off)
    p.insert_text((50, 200), "VISIBLE")
    p.insert_text((50, 250), "SHOWNLAYER", oc=on)
    if inside:
        q = pymupdf.open()
        r = q.new_page(width=200, height=100)
        r.insert_text((10, 50), "INNERSECRET")
        p.show_pdf_page(pymupdf.Rect(50, 300, 250, 400), q, 0, oc=off)
    path = str(tmp_path / "layers.pdf")
    d.save(path)
    return path


@pytest.mark.parametrize("inside", [False, True])
def test_9_hidden_layers_are_really_removed(tmp_path, inside):
    e = engine(layered(tmp_path, inside))
    before = e.doc[0].get_pixmap(dpi=60).samples
    e.apply(("scrub", ALL))
    d = saved(e, tmp_path)
    raw = every_stream(d)
    for secret in (b"HIDDENSECRET", b"INNERSECRET"):
        assert secret not in raw and secret.hex().encode() not in raw.lower()
    assert "VISIBLE" in d[0].get_text() and "SHOWNLAYER" in d[0].get_text()
    assert not d.get_ocgs() and d[0].get_pixmap(dpi=60).samples == before


def test_9_layers_stay_unless_hidden_data_goes(tmp_path):
    e = engine(layered(tmp_path))
    e.apply(("scrub", dict(ALL, hidden=False)))
    assert len(saved(e, tmp_path).get_ocgs()) == 2


def raw_layers(tmp_path, content, oc="", form=None, name="MC0", mark=None):
    """A page written by hand: layer "off" has no Usage entry, as most programs write it."""
    d = pymupdf.open()
    p = d.new_page()

    def new(s, stream=None):
        x = d.get_new_xref()
        d.update_object(x, s)
        if stream is not None:
            d.update_stream(x, stream)
        return x
    font = new("<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    off, on = new("<</Type/OCG/Name(off)>>"), new("<</Type/OCG/Name(on)>>")
    d.xref_set_key(d.pdf_catalog(), "OCProperties",
                   f"<</OCGs[{off} 0 R {on} 0 R]/D<</OFF[{off} 0 R]/Order[]>>>>")
    marks = new(mark.format(off=off, on=on)) if mark else off
    xobj = ""
    if form is not None:
        x = new("<</Type/XObject/Subtype/Form/BBox[0 0 600 800]/Resources<</Font<</F %d 0 R>>>>%s>>"
                % (font, oc.format(off=off, on=on)), form)
        xobj = f"/XObject<</{name if not mark else 'X'} {x} 0 R/Fm {x} 0 R>>"
    d.xref_set_key(p.xref, "Resources", f"<</Font<</F {font} 0 R>>/Properties<</{name} {marks} 0 R"
                   f"/ON {on} 0 R>>{xobj}>>")
    x = new("<<>>", content)
    d.xref_set_key(p.xref, "Contents", f"{x} 0 R")
    path = str(tmp_path / "raw.pdf")
    d.save(path)
    return path


def text_ops(t, y):
    return b"BT /F 12 Tf 50 %d Td (%s) Tj ET " % (y, t)


HID, VIS = text_ops(b"HIDDENSECRET", 700), text_ops(b"VISIBLE", 600)


def scrubbed(path, tmp_path):
    """Scrub, save, and compare the picture as a fresh reader sees it."""
    with pymupdf.open(path) as d:
        before = d[0].get_pixmap(dpi=60).samples
    e = engine(path)
    e.apply(("scrub", ALL))
    d = saved(e, tmp_path, "clean.pdf")
    assert b"HIDDENSECRET" not in every_stream(d)
    assert not d.get_ocgs() and d[0].get_pixmap(dpi=60).samples == before
    return d


@pytest.mark.parametrize("content", [
    b"/OC /MC0 BDC " + HID + b"EMC " + VIS,
    b"/OC/MC0 BDC q 1 0 0 rg 10 10 50 50 re f Q " + HID + b"EMC\n" + VIS,
    b"/OC /MC0 BDC /Span <</ActualText(HIDDENSECRET)>> BDC " + HID + b"EMC EMC " + VIS,
    b"/OC /ON BDC /OC /MC0 BDC " + HID + b"EMC " + VIS + b"EMC ",
    b"BT /F 12 Tf 14 TL 50 700 Td /OC /MC0 BDC (HIDDENSECRET) ' 1 2 (HIDDENSECRET) \" EMC "
    b"1 0 0 1 50 600 Tm (VISIBLE) Tj ET ",
    b"q /OC /MC0 BDC 0 0 300 300 re W n " + HID + b"EMC 0 1 0 rg 100 100 400 400 re f Q " + VIS,
])
def test_9_layer_without_usage(tmp_path, content):
    d = scrubbed(raw_layers(tmp_path, content), tmp_path)
    assert "VISIBLE" in d[0].get_text().replace(" ", "")  # the spacing that was set stays set


def test_9_names_are_compared_decoded(tmp_path):
    content = b"/OC /My#20Layer_1 BDC " + HID + b"EMC " + VIS
    scrubbed(raw_layers(tmp_path, content, name="My#20Layer_1"), tmp_path)
    content = b"/OC /#c3#a9x BDC " + HID + b"EMC " + VIS
    scrubbed(raw_layers(tmp_path, content, name="#C3#A9x"), tmp_path)


@pytest.mark.parametrize("oc", ["/OC {off} 0 R"])
def test_9_hidden_form(tmp_path, oc):
    scrubbed(raw_layers(tmp_path, b"/Fm Do " + VIS, oc=oc, form=HID), tmp_path)


def test_9_field_and_comment_on_a_hidden_layer(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    w = pymupdf.Widget()
    w.field_name, w.field_value = "f", "HIDDENSECRET"
    w.field_type, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(50, 50, 200, 70)
    p.add_widget(w)
    note = p.add_text_annot((300, 300), "HIDDENSECRET")
    off = d.get_new_xref()
    d.update_object(off, "<</Type/OCG/Name(off)>>")
    d.xref_set_key(d.pdf_catalog(), "OCProperties",
                   f"<</OCGs[{off} 0 R]/D<</OFF[{off} 0 R]/Order[]>>>>")
    for x in (next(p.widgets()).xref, note.xref):
        d.xref_set_key(x, "OC", f"{off} 0 R")
    path = str(tmp_path / "a.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("scrub", dict(ALL, comments=False)))
    d = saved(e, tmp_path, "clean.pdf")
    everything = every_stream(d) + b"".join(
        d.xref_object(x).encode() for x in range(1, d.xref_length()))
    assert b"HIDDENSECRET" not in everything and not list(d[0].annots())


def test_9_form_drawn_hidden_and_visible_stays(tmp_path):
    path = raw_layers(tmp_path, b"/OC /MC0 BDC /Fm Do EMC /Fm Do " + VIS,
                      form=text_ops(b"SHARED", 300))
    assert "SHARED" in scrubbed(path, tmp_path)[0].get_text()


@pytest.mark.parametrize("mark,shown", [
    ("<</Type/OCMD/OCGs[{off} 0 R]>>", False), ("<</Type/OCMD/OCGs[{off} 0 R {on} 0 R]>>", True),
    ("<</Type/OCMD/OCGs[{off} 0 R]/P/AllOff>>", True),
    ("<</Type/OCMD/OCGs[{on} 0 R]/P/AnyOff>>", False)])
def test_9_combined_layers(tmp_path, mark, shown):
    """What was visible stays visible even where that depended on a layer being off."""
    path = raw_layers(tmp_path, b"/OC /MC0 BDC " + (VIS if shown else HID) + b"EMC ", mark=mark)
    d = scrubbed(path, tmp_path)
    assert ("VISIBLE" in d[0].get_text()) is shown


@pytest.mark.parametrize("mark", [
    "<</Type/OCMD/OCGs[{off} 0 R {on} 0 R]/P/AllOn>>",  # MuPDF shows it, the standard does not
    "<</Type/OCMD/OCGs[{off} 0 R]/VE[/Not {off} 0 R]>>"])
def test_9_doubtful_combinations_are_refused(tmp_path, mark):
    path = raw_layers(tmp_path, b"/OC /MC0 BDC " + HID + b"EMC ", mark=mark)
    with pytest.raises(EngineError, match="scrub_layers"):
        engine(path).apply(("scrub", ALL))


def test_9_text_that_would_move_is_refused(tmp_path):
    path = raw_layers(tmp_path, b"BT /F 12 Tf 50 700 Td /OC /MC0 BDC (HIDDENSECRET) Tj EMC "
                      b"(VISIBLE) Tj ET ")
    with pytest.raises(EngineError, match="scrub_layers"):
        engine(path).apply(("scrub", ALL))


def test_9_inline_pictures_survive(tmp_path):
    data = b"\x10 EI \xff/OC" + bytes(range(16, 47))
    img = b"q 80 0 0 80 100 400 cm BI /W 5 /H 8 /BPC 8 /CS /G ID " + data + b" EI Q "
    assert len(data) == 40
    d = scrubbed(raw_layers(tmp_path, img + b"/OC /MC0 BDC " + HID + img + b"EMC " + VIS), tmp_path)
    assert data in every_stream(d)


def test_9_broken_content_is_refused(tmp_path):
    path = raw_layers(tmp_path, b"/OC /MC0 BDC " + HID)
    with pytest.raises(EngineError, match="scrub_layers"):
        engine(path).apply(("scrub", ALL))


def jpeg(**kw):
    im = Image.fromarray((np.random.default_rng(3).random((64, 64, 3)) * 255).astype("uint8"))
    ex = Image.Exif()
    ex[0x010F] = "SECRETCAMERA"
    b = io.BytesIO()
    im.save(b, "JPEG", exif=ex, **kw)
    return b.getvalue()


@pytest.mark.parametrize("kw", [{}, {"progressive": True}, {"optimize": True},
                                {"restart_marker_rows": 1}, {"subsampling": 0}])
def test_9_jpeg_kinds(kw):
    from furceli import layers
    data = jpeg(**kw)
    out = layers.bare_jpeg(data + b"trailing SECRET")
    assert b"SECRET" not in out
    a, b = (np.asarray(Image.open(io.BytesIO(x)).convert("RGB")) for x in (data, out))
    assert (a == b).all()
    assert layers.bare_jpeg(data[:20]) is None and layers.bare_jpeg(b"PNG") is None


def test_9_photo_inside_flate(tmp_path):
    import zlib
    d = pymupdf.open()
    p = d.new_page()
    p.insert_image(pymupdf.Rect(50, 50, 250, 200), stream=jpeg())
    x = p.get_images()[0][0]
    d.update_stream(x, zlib.compress(jpeg()), compress=False)
    d.xref_set_key(x, "Filter", "[/FlateDecode/DCTDecode]")
    path = str(tmp_path / "chain.pdf")
    d.save(path)
    e = engine(path)
    before = e.doc[0].get_pixmap().samples
    e.apply(("scrub", {}))
    assert b"SECRETCAMERA" not in zlib.decompress(zlib.compress(e.doc.xref_stream_raw(x)))
    assert saved(e, tmp_path)[0].get_pixmap().samples == before


def test_9_unreadable_photo_is_reported(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_image(pymupdf.Rect(50, 50, 250, 200), stream=jpeg())
    x = p.get_images()[0][0]
    d.update_stream(x, b"not a jpeg SECRETCAMERA", compress=False)
    d.xref_set_key(x, "Filter", "/DCTDecode")
    path = str(tmp_path / "odd.pdf")
    d.save(path)
    assert ("photos", 1) in engine(path).apply(("scrub", {}))["notes"]


@pytest.mark.parametrize("mode", ["RGB", "L", "CMYK"])
def test_9_camera_data_leaves_the_picture(tmp_path, mode):
    im = Image.fromarray((np.random.default_rng(2).random((90, 120, 3)) * 255).astype("uint8")
                         ).convert(mode)
    ex = Image.Exif()
    ex[0x010F] = "SECRETCAMERA"
    b = io.BytesIO()
    im.save(b, "JPEG", exif=ex, comment=b"SECRETCOMMENT")
    d = pymupdf.open()
    p = d.new_page()
    p.insert_image(pymupdf.Rect(50, 50, 250, 200), stream=b.getvalue())
    x = p.get_images()[0][0]
    d.update_object(m := d.get_new_xref(), "<</Type/Metadata/Subtype/XML>>")
    d.update_stream(m, b"<x>SECRETXMP</x>")
    d.xref_set_key(x, "Metadata", f"{m} 0 R")
    path = str(tmp_path / "jpg.pdf")
    d.save(path)
    assert b"SECRETCAMERA" in read(path)
    e = engine(path)
    before = e.doc[0].get_pixmap().samples
    e.apply(("scrub", dict(ALL, hidden=False)))
    d = saved(e, tmp_path)
    data = read(str(tmp_path / "out.pdf")) + every_stream(d)
    assert not [s for s in (b"SECRETCAMERA", b"SECRETCOMMENT", b"SECRETXMP") if s in data]
    assert d[0].get_pixmap().samples == before


def test_9_resource_named_js_survives(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((50, 100), "TEXT", fontname="JS", fontfile="C:/Windows/Fonts/arial.ttf")
    path = str(tmp_path / "a.pdf")
    d.save(path)
    e = engine(path)
    before = e.doc[0].get_pixmap().samples
    e.apply(("scrub", {}))
    assert saved(e, tmp_path)[0].get_pixmap().samples == before


# -- 10, 11: scans ---------------------------------------------------------------------
@pytest.fixture
def no_tessdata(tmp_path, monkeypatch):
    from furceli import features
    monkeypatch.setattr(features, "TESSDATA", str(tmp_path / "none"))


def test_10_no_language_files_no_problem_without_ocr(tmp_path, no_tessdata):
    d = pymupdf.open()
    p = d.new_page()
    for k in range(30):
        p.insert_text((50, 60 + k * 22), "some words that make a page look written " * 2)
    path = str(tmp_path / "a.png")
    p.get_pixmap(dpi=120).save(path)
    got = scan.analyse(path)
    assert got["turn"] == 0
    assert scan.build_page(path, 0, dict(got, mode="gray", languages=""), str(tmp_path / "o.pdf")) == 0
    with pytest.raises(EngineError, match="no_language"):
        scan.build_page(path, 0, dict(got, mode="gray", languages="kat"), str(tmp_path / "o2.pdf"))


def test_11_text_after_page_ten_is_seen(tmp_path):
    d = pymupdf.open()
    for i in range(14):
        p = d.new_page()
        if i == 12:
            p.insert_text((50, 50), "real text " * 20)
    path = str(tmp_path / "a.pdf")
    d.save(path)
    assert scan.count_pages(path) == {"pages": 14, "text": True}


@pytest.mark.parametrize("w,h", [(100_000, 100_000), (14_400, 40), (14_400, 14_400)])
def test_11_odd_pages_stay_within_limits(tmp_path, w, h):
    d = pymupdf.open()
    d.new_page(width=w, height=h)
    path = str(tmp_path / "a.pdf")
    d.save(path)
    try:
        assert max(scan.load(path).shape) <= scan.MAX_SIDE + 1
    except EngineError as e:
        assert str(e) == "bad_image"


def test_11_bomb_in_a_later_frame(tmp_path, monkeypatch):
    path = str(tmp_path / "a.tif")
    Image.new("L", (50, 50)).save(path, save_all=True, append_images=[Image.new("L", (3000, 3000))])
    monkeypatch.setattr(scan, "MAX_PIXELS", 1_000_000)
    assert scan.load(path, 0).shape[0] == 50
    with pytest.raises(EngineError, match="too_big"):
        scan.load(path, 1)


@pytest.mark.parametrize("ratio", [11 / 8.5, 8.5 / 11, 842 / 595, 595 / 842, 1.0, 2.0])
def test_11_sheet_keeps_its_shape(ratio):
    img = np.full((1400, 1400, 3), 90, np.uint8)
    h = min(900 * ratio, 1300)
    w = h / ratio
    quad = np.array([(5, 5), (5 + w, 5), (5 + w, 5 + h), (5, 5 + h)]) / 1399
    out = scan.flatten(img, [tuple(p) for p in quad])
    assert out.shape[0] / out.shape[1] == pytest.approx(ratio, rel=.02)


@pytest.mark.own_process
@pytest.mark.parametrize("q", range(4))
def test_11_aligned_lines_do_not_hide_the_direction(q):
    """Lines that repeat look like columns; reading them decides, not their look."""
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="sy", fontfile="C:/Windows/Fonts/sylfaen.ttf")
    for k in range(25):
        p.insert_text((50, 80 + k * 24), "საქართველოს კანონი და წესრიგი თბილისი", fontname="sy",
                      fontsize=12)
    x = p.get_pixmap(dpi=200)
    img = np.frombuffer(x.samples, np.uint8).reshape(x.h, x.w, 3).copy()
    assert scan.upright(scan.turn(img, q), "kat+eng") == (4 - q) % 4


# -- 12: marks that were never applied -----------------------------------------------------
def test_12_saving_with_open_marks_asks(doc, monkeypatch, press):  # noqa: F811
    doc.edit(("rotate", [5], 90))
    idle(doc)
    doc.view.marks[0] = [(10, 10, 60, 60)]
    old = read(doc.path)
    asked = answers(monkeypatch, "question", NO)
    press("გადაწერა")
    assert doc.save() is False and read(doc.path) == old
    assert len(asked) == 1 and "დასაფარ" in asked[0]
    asked = answers(monkeypatch, "question", YES)
    press("გადაწერა")
    assert doc.save() is True and len(asked) == 1


def test_12_export_with_open_marks_asks(doc, monkeypatch, tmp_path):
    doc.view.marks[0] = [(10, 10, 60, 60)]
    asked = answers(monkeypatch, "question", NO)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "x.txt"), ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(tmp_path / "im"))
    assert doc.export_as("txt") is None and doc.export_images("png", 50) == []
    doc.export_pdfa()
    assert doc.extract_pages() is None
    assert len(asked) == 4 and not os.path.exists(tmp_path / "x.txt")


def test_12_closing_with_open_marks_asks(win, doc, monkeypatch):  # noqa: F811
    doc.view.marks[0] = [(10, 10, 60, 60)]
    asked = answers(monkeypatch, "question", NO)
    win.close_tab(win.tabs.indexOf(doc))
    assert win.tabs.count() == 1 and len(asked) == 1


def test_12_same_place_is_marked_once(doc):
    doc.view.hits = {0: [((10, 10, 60, 20), "x")]}
    doc._found, doc._left = [(0, 0)], 0
    doc.redact_search()
    doc.redact_search()
    assert len(doc.view.marks[0]) == 1


# -- 13: batch ---------------------------------------------------------------------------------
def test_13_password_needs_pdf(win, monkeypatch, tmp_path):  # noqa: F811
    dlg = BatchDialog(win)
    dlg.list.addItem(build(tmp_path / "a.pdf"))
    dlg.lock.setChecked(True)
    dlg.password.setText("x")
    dlg.kind.setCurrentIndex(1)
    told = answers(monkeypatch, "warning", None)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: pytest.fail("went on"))
    dlg.start()
    assert len(told) == 1


# -- 14: an existing signature field --------------------------------------------------------------
@pytest.fixture
def cert(tmp_path):
    path = str(tmp_path / "c.pfx")
    signing.make_certificate(path, "parolii", "ტესტი")
    return path


def sign_fields(path):
    return [(w["name"], w["signed"]) for w in engine(path).widgets(0) if w["kind"] == "sign"]


@pytest.mark.parametrize("rot", [0, 90])
def test_14_the_clicked_field_is_signed(tmp_path, cert, rot):
    e = engine(blank(tmp_path, rot))
    e.apply(("field", 0, (100, 100, 300, 160), "sign", "Sig1", {}))
    e.apply(("field", 0, (100, 300, 300, 360), "sign", "Sig2", {}))
    src, out = str(tmp_path / "form.pdf"), str(tmp_path / "signed.pdf")
    e.save(src)
    box = e.pdf_box(0, (100, 100, 300, 160))
    signing.sign(src, out, "", cert, "parolii", {"page": 0, "box": box, "field": "Sig1"})
    assert sorted(sign_fields(out)) == [("Sig1", True), ("Sig2", False)]
    rows = signing.verify(out)
    assert [r["field"] for r in rows] == ["Sig1"] and rows[0]["intact"]
    with pymupdf.open(out) as d:  # and it can be seen
        pix = d[0].get_pixmap(clip=pymupdf.Rect(105, 105, 295, 155))
        assert len({pix.samples[k:k + 3] for k in range(0, len(pix.samples), 3)}) > 5
    with pytest.raises(EngineError, match="already_signed"):
        signing.sign(out, str(tmp_path / "x.pdf"), "", cert, "parolii",
                     {"page": 0, "box": box, "field": "Sig1"})


def test_14_window_passes_the_field(win, tmp_path, cert, monkeypatch):  # noqa: F811
    e = engine(blank(tmp_path))
    e.apply(("field", 0, (100, 100, 300, 160), "sign", "Sig1", {}))
    src, out = str(tmp_path / "form.pdf"), str(tmp_path / "signed.pdf")
    e.save(src)
    doc = open_doc(win, src)

    def fill(dlg):
        dlg._pfx.setText(cert)
        dlg._pw.setText("parolii")
        return 1
    monkeypatch.setattr(forms.SignDialog, "exec", fill)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    doc.field_clicked(0, client.run_task(doc.client, "widgets", (0,))[0])
    assert sign_fields(out) == [("Sig1", True)]
    two = win.doc
    wait(lambda: two.loaded)
    shown = []
    monkeypatch.setattr(two, "verify", lambda: shown.append(1))
    two.field_clicked(0, client.run_task(two.client, "widgets", (0,))[0])
    assert shown  # a signed field shows its signature and is never stamped over


# -- 15: a signature picture that was given up -------------------------------------------------------
def test_15_cancelled_signature_is_forgotten(doc, tmp_path, monkeypatch):
    monkeypatch.setattr(forms.SignaturePad, "exec", lambda self: setattr(
        self, "path", picture(tmp_path)) or 1)
    doc.place_signature()
    assert doc.view.tool == "image" and doc.pending_image
    doc.tool_done("cancel", 0, None)
    asked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: asked.append(1) or ("", ""))
    doc.set_tool("image")
    doc.tool_done("image", 0, {"rect": (10, 10, 100, 100)})
    assert asked and not doc.ops


# -- 16: the scan window ---------------------------------------------------------------------------------
@pytest.fixture
def scans(win, tmp_path):  # noqa: F811
    path = str(tmp_path / "p.png")
    Image.new("RGB", (400, 500), "white").save(path)
    dlg = scan_dialog.ScanDialog(win, [path])
    dlg.show()
    wait(lambda: dlg.pages and dlg.pages[0]["found"] and dlg.pages[0]["after"], 60)
    yield dlg
    dlg.reject()


def test_16_failed_preview_is_not_for_ever(scans):
    p = scans.current
    scans._failed(("look", p["id"], "an older setting"), "crash")
    assert not p["error"] and not p["note"]
    scans._failed(("look", p["id"], scans._stamp(p)), "crash")
    assert p["note"] and not p["error"]
    scans._turn(1)
    wait(lambda: p["seen"] == scans._stamp(p), 30)
    assert not p["note"]


def test_16_corners_again_keeps_the_turn(scans):
    scans._turn(1)
    p = scans.current
    scans.find_again()
    assert not scans.right.isEnabled()  # what is being found cannot be changed meanwhile
    wait(lambda: p["found"], 60)
    assert p["o"]["turn"] == 1 and scans.right.isEnabled()


def test_16_cancel_while_joining(scans, tmp_path, monkeypatch):
    out = str(tmp_path / "o.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    bars, run = [], scan_dialog.run_task

    def task(c, op, args, text="", parent=None, allow=None):
        got = run(c, op, args, text, parent, allow)
        if op == "scan_join":
            bars.append(1)
            allow.cancel()
        return got
    monkeypatch.setattr(scan_dialog, "run_task", task)
    scans.create()
    assert bars and not os.path.exists(out) and scans.result_path is None
    assert not [f for f in os.listdir(tmp_path) if "furceli" in f]


def test_16_any_source_is_protected(scans, tmp_path, monkeypatch):
    bad = str(tmp_path / "bad.pdf")
    with open(bad, "wb") as f:
        f.write(b"x")
    scans.pages.append(dict(scans.pages[0], id=99, path=bad, error="unreadable",
                            o=dict(scans.pages[0]["o"])))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (bad, ""))
    answers(monkeypatch, "question", YES)
    told = answers(monkeypatch, "warning", None)
    scans.create()
    scans.pages.pop()
    assert told and read(bad) == b"x"


def test_16_preview_follows_after_a_cancelled_create(scans, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "o.pdf"), ""))
    monkeypatch.setattr(scan_dialog, "run_task",
                        lambda *a, **k: (_ for _ in ()).throw(client.TaskError("cancelled")))
    p = scans.current
    scans.mode.blockSignals(True)
    scans.mode.setCurrentIndex(2)
    scans.mode.blockSignals(False)
    scans.create()  # no message: the user asked for it
    wait(lambda: p["seen"] == scans._stamp(p), 30)


# -- 17: the worker thread ----------------------------------------------------------------------------------
@pytest.mark.parametrize("error", [MemoryError(), ValueError("pickle"), RuntimeError()])
def test_17_no_error_stops_the_worker(app, error):  # noqa: F811
    c = client.EngineClient()
    good = c._call

    def bad(op, args):
        c._call = good
        raise error
    c._call = bad
    got = []
    c.failed.connect(lambda k, code: got.append(code))
    c.submit(("a",), 0, "inspect", "missing.pdf")
    wait(lambda: got, 20)
    c.submit(("b",), 0, "inspect", "missing.pdf")
    wait(lambda: len(got) == 2, 20)
    assert got == ["crash", "not_found"]
    c.shutdown()


def test_17_failing_cleanup_does_not_stop_it(app, monkeypatch):  # noqa: F811
    c = client.EngineClient()
    monkeypatch.setattr(files, "sweep_sessions", lambda: (_ for _ in ()).throw(OSError()))
    got = []
    c.failed.connect(lambda k, code: got.append(code))
    c.submit(("a",), 0, "_crash")
    wait(lambda: got, 20)
    c.submit(("b",), 0, "inspect", "missing.pdf")
    wait(lambda: len(got) == 2, 20)
    assert got == ["crash", "not_found"]
    c.shutdown()


def test_17_cancel_button_of_the_caller_stops_the_task(app):  # noqa: F811
    c = client.EngineClient()
    bar = QProgressDialog("x", "y", 0, 5)
    QTimer.singleShot(400, lambda: bar.findChild(QPushButton).click())
    t = time.monotonic()
    with pytest.raises(client.TaskError, match="cancelled"):
        client.run_task(c, "_sleep", (15,), "", None, allow=bar)
    assert time.monotonic() - t < 10
    c.shutdown()


def test_17_recovery_knows_the_edit_before_the_window_does(doc):
    """A crash between an edit's success and the window's reaction must not lose the edit."""
    doc.edit(("rotate", [0], 90))
    end = time.time() + 20
    while time.time() < end and (doc.client._current is not None or doc.client._pending):
        time.sleep(.01)  # no event loop: the window has not heard of it
    assert not doc.ops
    assert doc.client._opened[1][2] == [("rotate", [0], 90)]
    idle(doc)


# -- files ------------------------------------------------------------------------------------------------------
def test_backup_housekeeping(tmp_path, monkeypatch):
    root = tmp_path / "b"
    monkeypatch.setenv("FURCELI_BACKUPS", str(root))
    src = tmp_path / "a.pdf"
    src.write_bytes(b"data")
    (root / "folder").mkdir(parents=True)

    def half(a, b):
        with open(b, "wb") as f:
            f.write(b"d")
    monkeypatch.setattr(files.shutil, "copy2", half)
    with pytest.raises(OSError):
        files.make_backup(str(src))
    assert os.listdir(root) == ["folder"]  # no half copy that would push out a real one


def test_session_mark_is_never_half_written(monkeypatch):
    seen, real_replace = [], os.replace

    def spy(a, b):
        with open(a) as f:
            seen.append(len(f.read().split()))
        real_replace(a, b)
    monkeypatch.setattr(files.os, "replace", spy)
    path = files.new_session()
    assert seen == [2] and os.listdir(path) == [files.MARK]
    files.shutil.rmtree(path)


def test_two_starts_at_once_have_one_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("FURCELI_SETTINGS", str(tmp_path))
    first = single.claim()
    assert first is not None and single.claim() is None
    first.unlock()
    again = single.claim()
    assert again is not None
    again.unlock()


# -- saving is checked against the open document ------------------------------------------------------------------
def test_save_notices_what_the_copy_lost(tmp_path, monkeypatch):
    e = engine(build(tmp_path / "a.pdf"))
    good = e._snapshot

    def lossy():
        d = good()
        d[1].add_redact_annot(d[1].rect)
        d[1].apply_redactions()
        return d
    monkeypatch.setattr(e, "_snapshot", lossy)
    with pytest.raises(EngineError, match="save_failed"):
        e.save(str(tmp_path / "o.pdf"))


# -- small ones -----------------------------------------------------------------------------------------------------
def test_zoom_must_be_a_number(win, doc):  # noqa: F811
    for text in ("nan", "inf", "-inf%"):
        before = doc.view.zoom
        win.zoom_box.setEditText(text)
        win._zoom_typed()
        assert doc.view.zoom == before


def test_crop_ends_when_the_document_changes(doc):
    doc.start_crop()
    doc.edit(("rotate", [0], 90))
    idle(doc)
    assert not doc.view.cropping


def test_save_as_onto_another_open_tab_is_refused(win, doc, tmp_path, monkeypatch):  # noqa: F811
    (tmp_path / "x").mkdir()
    other = open_doc(win, build(tmp_path / "x" / "სხვა.pdf"))
    old = read(other.path)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (other.path, ""))
    told = answers(monkeypatch, "warning", None)
    assert doc.save_as() is False and told and read(other.path) == old


def test_password_field_is_not_shown(doc, monkeypatch):
    modes = []

    def text(parent, title, label, echo=QLineEdit.EchoMode.Normal, text=""):
        modes.append((echo, text))
        return "", False
    monkeypatch.setattr(QInputDialog, "getText", text)
    doc.field_clicked(0, {"readonly": False, "kind": "text", "label": "", "name": "n", "value": "pw",
                          "password": True, "multiline": False, "max": 0, "index": 0})
    assert modes == [(QLineEdit.EchoMode.Password, "")]


def test_pages_are_stamped_once(doc):
    dlg = types.SimpleNamespace(pages_text=lambda: "1-3, 2")
    assert doc._pages_dialog(dlg) == [0, 1, 2]


def test_very_small_text_keeps_its_size(app):  # noqa: F811
    line = {"text": "x", "size": 2.517, "color": (0, 0, 0), "bold": False, "italic": False,
            "serif": False, "mono": False}
    assert forms.LineDialog(line).style_()["size"] == 2.517


def test_page_without_text_explains_itself(doc, monkeypatch):
    seen = []
    doc.status.connect(seen.append)
    monkeypatch.setattr(doc, "_task", lambda *a: [])
    doc.edit_line(0, (10, 10))
    assert any("OCR" in s for s in seen)


def test_legacy_georgian_survives_html(tmp_path):
    e = engine(build(tmp_path / "a.pdf"))
    e.chars = lambda p: [[(0, 0, 5, 10, c) for c in "გამარჯობა <b>"]]
    out = str(tmp_path / "a.html")
    convert.export_as(e, "html", out, [0])
    assert "გამარჯობა &lt;b&gt;" in read(out).decode()


def test_ligatures_do_not_cost_the_formatting(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_htmlbox(p.rect + (50, 50, -50, -50), "<p>the first <b>office</b> file</p>")
    path = str(tmp_path / "a.pdf")
    d.save(path)
    out = str(tmp_path / "a.html")
    convert.export_as(engine(path), "html", out, [0])
    assert "<b>" in read(out).decode()


def test_second_start_does_not_wait_for_ever(monkeypatch):
    from furceli import __main__ as start
    monkeypatch.setattr(single, "claim", lambda: None)
    monkeypatch.setattr(single, "hand_over", lambda paths: time.sleep(.6) or False)
    t = time.monotonic()
    assert start.take_turn([], wait=2) == (None, False)
    assert time.monotonic() - t < 4


def test_excel_says_when_only_text_went(tmp_path):
    e = engine(build(tmp_path / "a.pdf"))
    assert convert.export_as(e, "xlsx", str(tmp_path / "a.xlsx"), [0]) == "simple"


def test_powerpoint_is_closed_after_a_failed_save(monkeypatch, tmp_path):
    import win32com.client
    log = []

    class Show:
        def SaveAs(self, *a):
            raise RuntimeError("disk full")

        def Close(self):
            log.append("close")
            ppt.Presentations.Count = 0
    ppt = types.SimpleNamespace(AutomationSecurity=1, Quit=lambda: log.append("quit"))
    ppt.Presentations = types.SimpleNamespace(Count=1, Open=lambda *a, **k: Show())

    def none(name):
        raise RuntimeError
    monkeypatch.setattr(win32com.client, "GetActiveObject", none)
    monkeypatch.setattr(win32com.client, "DispatchEx", lambda n: ppt)
    monkeypatch.setattr(convert, "_safe_source", lambda *a: "x")
    with pytest.raises(RuntimeError, match="disk full"):
        convert._slides("x.pptx", str(tmp_path / "o.pdf"))
    assert log == ["close", "quit"]


def test_renamed_binary_is_not_trusted_with_the_rest(tmp_path):
    path = str(tmp_path / "a.docx")  # the user was never asked about this one
    with open(path, "wb") as f:
        f.write(b"\xd0\xcf\x11\xe0 not a zip")
    with pytest.raises(EngineError, match="untrusted"):
        convert._safe_source(path, str(tmp_path), True)


def test_office_bomb_is_refused(tmp_path, monkeypatch):
    path = str(tmp_path / "a.docx")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * 3_000_000)
    monkeypatch.setattr(convert, "MAX_UNPACKED", 1_000_000)
    (tmp_path / "out").mkdir()
    with pytest.raises(EngineError, match="too_big"):
        convert.without_links(path, str(tmp_path / "out"))


def test_wrong_owner_password_leaves_the_document_usable(tmp_path):
    d = pymupdf.open()
    d.new_page().insert_text((50, 50), "hello")
    path = str(tmp_path / "a.pdf")
    d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o",
           permissions=int(pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_MODIFY))
    e = engine(path, "u")
    with pytest.raises(EngineError, match="owner_needed"):
        e.apply(("protect", {"mode": "none", "auth": "wrong"}))
    assert "hello" in e.doc[0].get_text()
    e.apply(("protect", {"mode": "none", "auth": "o"}))


def test_certificate_dialog_explains_and_keeps_old_files(win, tmp_path, monkeypatch):  # noqa: F811
    dlg = forms.SignDialog(client.EngineClient(win), win)
    told = answers(monkeypatch, "warning", None)

    def typed(*words):
        said = iter([(w, True) for w in words])
        monkeypatch.setattr(QInputDialog, "getText", lambda *a: next(said))
        dlg._create()
    typed("სახელი", "abc")
    assert len(told) == 1
    typed("სახელი", "parolii", "sxva")
    assert len(told) == 2
    out = str(tmp_path / "old.pfx")
    with open(out, "wb") as f:
        f.write(b"old certificate")
    monkeypatch.setenv("FURCELI_BACKUPS", str(tmp_path / "bk"))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    typed("სახელი", "parolii", "parolii")
    kept = os.listdir(tmp_path / "bk")
    assert len(kept) == 1 and read(str(tmp_path / "bk" / kept[0])) == b"old certificate"
    assert dlg.pfx() == out and len(read(out)) > 500
    dlg.client.shutdown()


def test_corrupt_saved_position_does_not_stop_opening(win, tmp_path):  # noqa: F811
    path = build(tmp_path / "a.pdf")
    win.settings.setValue("positions", {os.path.normcase(path): "oops"})
    assert open_doc(win, path).loaded


def test_long_links_are_shortened(doc, monkeypatch):
    asked = answers(monkeypatch, "question", NO)
    doc._open_uri("https://bank.example@evil.example/" + "x" * 5000)
    assert len(asked[0]) < 700 and "evil.example" in asked[0].split("\n\n")[1]


def test_comments_panel_ignores_an_old_list(doc):
    doc.edit(("annot", 0, "rect", {"rect": (10, 10, 60, 60), "color": (1, 0, 0), "width": 1},
              doc._meta()))
    idle(doc)
    doc.sidebar.setCurrentWidget(doc.comments)
    doc.load_comments()
    wait(lambda: doc.comments.rev == doc.view.rev and doc.comments.list.count())
    doc.comments.list.setCurrentRow(0)
    n = len(doc.ops)
    doc.comments.rev = -1  # the list of an earlier state of the document
    doc.comments.delete()
    assert len(doc.ops) == n and not doc._busy


def test_search_list_is_bounded(doc):
    doc._gen += 1
    doc._left, doc._jump = 1, False
    doc._add_hits(0, [((0, 0, 5, 5), "x")] * 30_000)
    assert len(doc._found) == 30_000 and doc.results.count() <= 2001
    doc._select_hit((0, 29_999))
