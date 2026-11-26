"""Regression tests for the second round of fixes (the items left over from review 3)."""
import os

import pymupdf
import pytest
from PySide6.QtPrintSupport import QPrinter
from test_ui import app, open_doc, settled, wait, win  # noqa: F401

from furceli import convert, printing


def black_pages(path, sizes):
    d = pymupdf.open()
    for w, h in sizes:
        p = d.new_page(width=w, height=h)
        p.draw_rect(p.rect, color=None, fill=(0, 0, 0))
    d.save(str(path))
    return str(path)


def dark_share(page):
    pix = page.get_pixmap(dpi=20, colorspace=pymupdf.csGRAY)
    return sum(1 for b in pix.samples if b < 128) / len(pix.samples)


def test_mixed_orientation_fills_every_sheet(win, tmp_path):  # noqa: F811
    doc = open_doc(win, black_pages(tmp_path / "mixed.pdf", [(595, 842), (842, 595), (595, 842)]))
    out = str(tmp_path / "out.pdf")
    pr = QPrinter(QPrinter.PrinterMode.HighResolution)
    pr.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    pr.setOutputFileName(out)
    assert printing.print_pages(doc, pr, [0, 1, 2], win)
    d = pymupdf.open(out)
    assert [p.rect.width > p.rect.height for p in d] == [False, True, False]
    shares = [dark_share(p) for p in d]
    assert min(shares) > 0.8, shares


# -- Office files must neither ask anything nor fetch anything -------------------------------
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def word_with_field(path, url, simple=False, update=False):
    import docx
    from docx.oxml import parse_xml
    d = docx.Document()
    p = d.add_paragraph("hello ")
    if simple:
        p._p.append(parse_xml(f'<w:fldSimple {W} w:dirty="true" w:instr=\'INCLUDEPICTURE "{url}"\'>'
                              '<w:r><w:t>old</w:t></w:r></w:fldSimple>'))
    else:
        for x in (f'<w:fldChar w:fldCharType="begin" w:dirty="{"false" if update else "true"}"/>',
                  f'<w:instrText xml:space="preserve"> INCLUDEPICTURE "{url}" \\d </w:instrText>',
                  '<w:fldChar w:fldCharType="separate"/>', '<w:t>old</w:t>',
                  '<w:fldChar w:fldCharType="end"/>'):
            p._p.append(parse_xml(f'<w:r {W}>{x}</w:r>'))
    if update:
        d.settings.element.append(parse_xml(f'<w:updateFields {W} w:val="true"/>'))
    d.save(str(path))
    return str(path)


def excel_with_webservice(path, url):
    import openpyxl
    b = openpyxl.Workbook()
    b.active["A1"] = f'=_xlfn.WEBSERVICE("{url}")'
    b.active["A2"] = "cached"
    b.save(str(path))
    return str(path)


def test_word_fields_are_locked_before_opening(tmp_path):
    import zipfile
    src = word_with_field(tmp_path / "a.docx", "http://example.invalid/a.png", update=True)
    word_with_field(tmp_path / "b.docx", "http://example.invalid/b.png", simple=True)
    out = convert.without_links(src, str(tmp_path))
    with zipfile.ZipFile(out) as z:
        body, settings = z.read("word/document.xml"), z.read("word/settings.xml")
    assert b"dirty" not in body and b'fldLock="1"' in body and b"updateFields" not in settings
    os.makedirs(tmp_path / "x")
    out = convert.without_links(str(tmp_path / "b.docx"), str(tmp_path / "x"))
    with zipfile.ZipFile(out) as z:
        body = z.read("word/document.xml")
    assert b"dirty" not in body and b'fldLock="1"' in body


def test_formulas_that_fetch_go_before_opening(tmp_path):
    import zipfile
    import openpyxl
    b = openpyxl.Workbook()
    ws = b.active
    ws["A1"], ws["A2"] = '=_xlfn.WEBSERVICE("http://x.invalid")', "=SUM(B1:B2)"
    ws["A3"], ws["A4"] = '=image("http://x.invalid/a.png")', '=RTD("a.b",,"c")'
    ws["A5"], ws["A6"] = "=MYIMAGE(1)", '="WEBSERVICE(" & B1'  # not what they look like
    b.defined_names["feed"] = openpyxl.workbook.defined_name.DefinedName(
        "feed", attr_text='WEBSERVICE("http://x.invalid")')
    b.save(str(tmp_path / "a.xlsx"))
    out = convert.without_links(str(tmp_path / "a.xlsx"), str(tmp_path))
    with zipfile.ZipFile(out) as z:
        sheet, book = z.read("xl/worksheets/sheet1.xml"), z.read("xl/workbook.xml")
    assert b"x.invalid" not in sheet and b"x.invalid" not in book
    assert b"SUM(B1:B2)" in sheet and b"MYIMAGE(1)" in sheet and b"&amp; B1" in sheet


def test_shared_formulas_that_fetch_go_whole():
    data = (b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            b'<sheetData><row r="1"><c r="A1"><f t="shared" ref="A1:A2" si="0">'
            b'WEBSERVICE(B1)</f><v>1</v></c></row><row r="2"><c r="A2"><f t="shared" si="0"/>'
            b'<v>2</v></c><c r="B2"><f t="shared" ref="B2" si="1">SUM(C1)</f></c></row>'
            b'</sheetData></worksheet>')
    out = convert._no_fetch(data)
    assert out.count(b"<f") == 1 and b"SUM(C1)" in out and out.count(b"<v>") == 2


@pytest.fixture
def listener():
    import http.server
    import threading
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"FETCHED")
        do_HEAD = do_OPTIONS = do_PROPFIND = do_GET

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", hits
    srv.shutdown()


def convert_or_kill(src, out, seconds=120):
    """make_pdf, but an Office program that hangs on a hidden question is ended."""
    import threading
    import psutil
    helpers, box = [], {}
    old = convert.notify
    convert.notify = lambda m: helpers.append(m[1]) if m[0] == "started" else None

    def run():
        try:
            convert.make_pdf([src], out)
        except Exception as e:  # noqa: BLE001
            box["err"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    convert.notify = old
    if t.is_alive():
        for pid, _ in helpers:
            psutil.Process(pid).kill()
        t.join(30)
        raise AssertionError("Office hung on a question nobody can see")
    if "err" in box and "no_office" in str(box["err"]):
        pytest.skip("Office is not installed")
    if "err" in box:
        raise box["err"]


@pytest.mark.parametrize("kind", ["dirty", "simple", "update"])
def test_word_fields_neither_hang_nor_fetch(tmp_path, listener, kind):
    url, hits = listener
    src = word_with_field(tmp_path / "a.docx", url + "/a.png", simple=kind == "simple",
                          update=kind == "update")
    convert_or_kill(src, str(tmp_path / "a.pdf"))
    assert hits == []


def test_excel_fetches_nothing(tmp_path, listener):
    url, hits = listener
    out = str(tmp_path / "a.pdf")
    convert_or_kill(excel_with_webservice(tmp_path / "a.xlsx", url + "/ws"), out)
    assert hits == [] and "FETCHED" not in pymupdf.open(out)[0].get_text()


# -- small leftovers ------------------------------------------------------------------------
def test_header_without_any_text_is_not_accepted(app, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox
    from furceli import forms
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    dlg = forms.HeaderDialog(3)
    for w in dlg._texts.values():
        w.setText("   ")
    dlg.try_accept()
    assert warned and not dlg.result()
    dlg._texts["tl"].setText("ა")
    dlg.try_accept()
    assert dlg.result()


def test_signing_time_is_shown_as_the_signers_claim(app, tmp_path):  # noqa: F811
    from test_pages import build
    from furceli import forms, signing
    path, pfx, out = build(tmp_path / "a.pdf"), str(tmp_path / "c.pfx"), str(tmp_path / "s.pdf")
    signing.make_certificate(pfx, "pw", "ტესტი")
    signing.sign(path, out, "", pfx, "pw", {})
    row = signing.verify(out)[0]
    assert row["time"] and row["time_proved"] is False
    text = forms.VerifyDialog._row(row).text()
    assert row["time"] + " (ხელმომწერის კომპიუტერის დრო, დადასტურებული არ არის)" in text
    proved = dict(row, time_proved=True)
    assert row["time"] + " (დროის შტამპით)" in forms.VerifyDialog._row(proved).text()


# -- hidden layers leave nothing that only they used ---------------------------------------
def leftover_layers(tmp_path, hidden, visible=b"BT /F 12 Tf 50 600 Td (VISIBLE) Tj ET ",
                    shared=False):
    """A page with layer "off" (no Usage, as most programs write it), a font F for the visible
    text, a font G (Courier), a tile pattern P with secret text and a shading S."""
    d = pymupdf.open()
    p = d.new_page()

    def new(s, stream=None):
        x = d.get_new_xref()
        d.update_object(x, s)
        if stream is not None:
            d.update_stream(x, stream)
        return x
    f = new("<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    g = new("<</Type/Font/Subtype/Type1/BaseFont/Courier>>")
    pat = new("<</Type/Pattern/PatternType 1/PaintType 1/TilingType 1/BBox[0 0 300 300]"
              f"/XStep 300/YStep 300/Resources<</Font<</F {f} 0 R>>>>>>",
              b"BT /F 12 Tf 10 100 Td (PATTERNSECRET) Tj ET")
    sh = new("<</ShadingType 2/ColorSpace/DeviceRGB/Coords[0 0 100 0]"
             "/Function<</FunctionType 2/Domain[0 1]/C0[1 0 0]/C1[0 0 1]/N 1>>>>")
    off = new("<</Type/OCG/Name(off)>>")
    d.xref_set_key(d.pdf_catalog(), "OCProperties", f"<</OCGs[{off} 0 R]/D<</OFF[{off} 0 R]/Order[]>>>>")
    res = (f"<</Font<</F {f} 0 R/G {g} 0 R>>/Pattern<</P {pat} 0 R>>/Shading<</S {sh} 0 R>>"
           f"/Properties<</MC0 {off} 0 R>>>>")
    if shared:  # one resources object for two pages
        r = new(res)
        res = f"{r} 0 R"
        q = d.new_page()
        d.xref_set_key(q.xref, "Resources", res)
        q.set_contents(new("<<>>", b"q /Pattern cs /P scn 0 0 300 300 re f Q "
                                   b"BT /G 12 Tf 50 500 Td (OTHER) Tj ET /S sh"))
        p = d[0]
    d.xref_set_key(p.xref, "Resources", res)
    p.set_contents(new("<<>>", b"/OC /MC0 BDC " + hidden + b" EMC " + visible))
    path = str(tmp_path / "left.pdf")
    d.save(path)
    return path


def scrub_all(path, tmp_path):
    from test_features import engine, saved
    e = engine(path)
    e.apply(("scrub", {"hidden": True}))
    return saved(e, tmp_path, "clean.pdf")


def objects_text(d):
    return b"".join(d.xref_object(x).encode() + (d.xref_stream(x) or b"")
                    for x in range(1, d.xref_length()))


@pytest.mark.parametrize("hidden,gone", [
    (b"q /Pattern cs /P scn 0 0 300 300 re f Q", b"PATTERNSECRET"),
    (b"BT /G 12 Tf 50 700 Td (X) Tj ET", b"Courier"),
    (b"/S sh", b"ShadingType"),
])
def test_what_only_hidden_layers_used_goes(tmp_path, hidden, gone):
    path = leftover_layers(tmp_path, hidden)
    with pymupdf.open(path) as d:
        before = d[0].get_pixmap(dpi=50).samples
    d = scrub_all(path, tmp_path)
    assert gone not in objects_text(d)
    assert d[0].get_pixmap(dpi=50).samples == before and "VISIBLE" in d[0].get_text()


def test_resources_another_page_uses_stay(tmp_path):
    path = leftover_layers(tmp_path, b"q /Pattern cs /P scn 0 0 300 300 re f Q /S sh "
                                     b"BT /G 12 Tf 50 700 Td (X) Tj ET", shared=True)
    with pymupdf.open(path) as d:
        before = [p.get_pixmap(dpi=50).samples for p in d]
    d = scrub_all(path, tmp_path)
    assert [p.get_pixmap(dpi=50).samples for p in d] == before
    assert b"PATTERNSECRET" in objects_text(d) and "OTHER" in d[1].get_text()


def test_resources_a_form_borrows_stay(tmp_path):
    """A form without resources of its own uses the names of whoever draws it."""
    d = pymupdf.open(leftover_layers(tmp_path, b"BT /G 12 Tf 50 700 Td (X) Tj ET"))
    p = d[0]
    x = d.get_new_xref()
    d.update_object(x, "<</Type/XObject/Subtype/Form/BBox[0 0 600 800]>>")
    d.update_stream(x, b"BT /G 12 Tf 50 300 Td (BORROWED) Tj ET")
    d.xref_set_key(p.xref, "Resources/XObject", f"<</Fm {x} 0 R>>")
    y = d.get_new_xref()
    d.update_object(y, "<<>>")
    d.update_stream(y, p.read_contents() + b" /Fm Do")
    p.set_contents(y)
    path = str(tmp_path / "borrow.pdf")
    d.save(path)
    d = scrub_all(path, tmp_path)
    assert "BORROWED" in d[0].get_text()


def test_letters_drawn_by_the_file_itself_stay(tmp_path):
    """A Type3 font without resources of its own borrows the page's: nothing may go then."""
    d = pymupdf.open(leftover_layers(tmp_path, b"q /Pattern cs /P scn 0 0 300 300 re f Q",
                                     visible=b"BT /T 40 Tf 50 400 Td (a) Tj ET"))
    p = d[0]

    def new(s, stream=None):
        x = d.get_new_xref()
        d.update_object(x, s)
        if stream is not None:
            d.update_stream(x, stream)
        return x
    glyph = new("<<>>", b"1000 0 d0 /Pattern cs /P scn 0 0 800 800 re f")
    t3 = new("<</Type/Font/Subtype/Type3/FontBBox[0 0 1000 1000]/FontMatrix[.001 0 0 .001 0 0]"
             f"/CharProcs<</a {glyph} 0 R>>/Encoding<</Differences[97/a]>>/FirstChar 97"
             "/LastChar 97/Widths[1000]>>")
    d.xref_set_key(p.xref, "Resources/Font/T", f"{t3} 0 R")
    path = str(tmp_path / "t3.pdf")
    d.save(path)
    with pymupdf.open(path) as d:
        before = d[0].get_pixmap(dpi=50).samples
    d = scrub_all(path, tmp_path)
    assert d[0].get_pixmap(dpi=50).samples == before


# -- redaction ------------------------------------------------------------------------------
def test_area_redaction_tells_where_else_the_words_are(tmp_path):
    from test_features import engine
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((100, 200), "TOPSECRET stays here", fontsize=12)
    word = next(w for w in p.get_text("words") if w[4] == "TOPSECRET")
    d.set_metadata({"title": "about topsecret"})
    d.set_toc([[1, "TOPSECRET chapter", 1]])
    path = str(tmp_path / "m.pdf")
    d.save(path)
    e = engine(path)
    notes = e.apply(("redact", {0: [tuple(word[:4])]}, {"fill": (0, 0, 0)}))["notes"]
    said = [x for what, x in notes if what == "mentions"]
    assert said, notes
    text = "\n".join(said[0])
    assert "TOPSECRET" in text and "სანიშნე" in text and "თვისებები" in text
    assert "stays" not in text


def test_redaction_without_mentions_says_nothing(tmp_path):
    from test_features import engine
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((100, 200), "TOPSECRET stays", fontsize=12)
    word = next(w for w in p.get_text("words") if w[4] == "TOPSECRET")
    path = str(tmp_path / "m.pdf")
    d.save(path)
    notes = engine(path).apply(("redact", {0: [tuple(word[:4])]}, {"fill": (0, 0, 0)}))["notes"]
    assert not [x for what, x in notes if what == "mentions"]


def test_window_tells_after_an_area_redaction(win, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox
    from test_ui_pages import idle
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((100, 200), "TOPSECRET stays", fontsize=12)
    word = next(w for w in p.get_text("words") if w[4] == "TOPSECRET")
    d.set_metadata({"title": "TOPSECRET"})
    d.save(str(tmp_path / "w.pdf"))
    doc = open_doc(win, str(tmp_path / "w.pdf"))
    doc.view.marks = {0: [tuple(word[:4])]}
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    doc.apply_redactions()
    idle(doc)
    wait(lambda: told)
    assert "TOPSECRET" in told[0] and "თვისებები" in told[0]


def jbig2_pdf(path, a):
    """A page with one JBIG2 picture (generic region, MMR coded: CCITT G4 from a TIFF)."""
    import io
    import struct
    from PIL import Image
    h, w = a.shape
    b = io.BytesIO()
    Image.fromarray(255 - a).convert("1").save(b, "TIFF", compression="group4")
    t = Image.open(io.BytesIO(b.getvalue()))
    g4 = b.getvalue()[t.tag_v2[273][0]:t.tag_v2[273][0] + t.tag_v2[279][0]]

    def seg(number, kind, data):
        return struct.pack(">IBBBI", number, kind, 0, 1, len(data)) + data
    data = (seg(0, 48, struct.pack(">IIIIBH", w, h, 0, 0, 0, 0))
            + seg(1, 39, struct.pack(">IIIIB", w, h, 0, 0, 0) + b"\x01" + g4) + seg(2, 49, b""))
    d = pymupdf.open()
    p = d.new_page(width=w, height=h)
    x = d.get_new_xref()
    d.update_object(x, f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}"
                       "/ColorSpace/DeviceGray/BitsPerComponent 1>>")
    d.update_stream(x, data, compress=False)
    d.xref_set_key(x, "Filter", "/JBIG2Decode")
    d.xref_set_key(p.xref, "Resources", f"<</XObject<</Im {x} 0 R>>>>")
    c = d.get_new_xref()
    d.update_object(c, "<<>>")
    d.update_stream(c, b"q %d 0 0 %d 0 0 cm /Im Do Q" % (w, h))
    p.set_contents(c)
    d.save(str(path))
    return str(path)


def test_redaction_reaches_into_jbig2_pictures(tmp_path):
    import numpy as np
    from test_features import engine, saved
    a = np.full((200, 300), 255, np.uint8)
    yy, xx = np.mgrid[0:200, 0:300]
    secret = ((yy // 4 + xx // 4) % 2 == 0)
    a[50:150, 50:250][secret[50:150, 50:250]] = 0
    path = jbig2_pdf(tmp_path / "jb.pdf", a)
    with pymupdf.open(path) as d:
        pix = d[0].get_pixmap(colorspace=pymupdf.csGRAY)
        assert (np.frombuffer(pix.samples, np.uint8).reshape(200, 300) == a).mean() > .99
    e = engine(path)
    e.apply(("redact", {0: [(100, 80, 200, 120)]}, {"fill": (0, 0, 0)}))
    d = saved(e, tmp_path)
    img = [x for x in range(1, d.xref_length()) if d.xref_get_key(x, "Subtype")[1] == "/Image"]
    assert len(img) == 1
    pm = pymupdf.Pixmap(d, img[0])
    got = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width)
    inner = got[84:116, 104:196]
    assert inner.std() < 1, "the checkered secret is still in the picture"
    assert got[60:70, 60:90].std() > 50  # the rest of the picture is still there


@pytest.mark.parametrize("rotation", [0, 90])
def test_partly_covered_letters(tmp_path, rotation):
    """Mostly covered letters go, barely touched ones stay, and nothing else moves."""
    from test_features import engine, saved
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((100, 300), "ABCDEFGH", fontsize=30)
    chars = [c for b in p.get_text("rawdict")["blocks"] for ln in b["lines"]
             for s in ln["spans"] for c in s["chars"]]
    box = {c["c"]: pymupdf.Rect(c["bbox"]) for c in chars}
    c, f = box["C"], box["F"]
    area = pymupdf.Rect(c.x0 + c.width * .4, c.y0, f.x0 + f.width * .15, c.y1)
    p.set_rotation(rotation)
    shown = area * p.rotation_matrix
    path = str(tmp_path / "abc.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("redact", {0: [tuple(shown)]}, {"fill": (0, 0, 0)}))
    r = saved(e, tmp_path)
    text = "".join(r[0].get_text().split())
    assert text == "ABFGH", text


# -- tag tree -------------------------------------------------------------------------------
@pytest.mark.parametrize("kid", ["0", "<</Type/MCR/MCID 0>>"])
def test_tagged_content_of_no_known_page_goes_with_a_deleted_page(tmp_path, kid):
    """Marked content without /Pg anywhere above it may be on the deleted page."""
    from test_features import engine, saved
    from test_realfiles import holds, tagged
    d = pymupdf.open(tagged(tmp_path))
    root = int(d.xref_get_key(d.pdf_catalog(), "StructTreeRoot")[1].split()[0])
    sect = int(d.xref_get_key(root, "K")[1].split()[0])
    x = d.get_new_xref()
    d.update_object(x, f"<</Type/StructElem/S/P/P {sect} 0 R/K {kid}/ActualText(LOOSETEXT)>>")
    kids = d.xref_get_key(sect, "K")[1]
    d.xref_set_key(sect, "K", kids[:-1] + f" {x} 0 R]")
    path = str(tmp_path / "loose.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("order", [0, 2]))
    r = saved(e, tmp_path)
    assert not holds(r, "LOOSETEXT") and not holds(r, "TAGTEXT1")


# -- forms ----------------------------------------------------------------------------------
def multi_list(path):
    d = pymupdf.open()
    p = d.new_page()
    w = pymupdf.Widget()
    w.field_name, w.rect = "L", pymupdf.Rect(100, 100, 300, 200)
    w.field_type = pymupdf.PDF_WIDGET_TYPE_LISTBOX
    w.choice_values = [["a", "Alpha"], ["b", "Beta"], ["c", "Gamma"]]
    w.field_value, w.field_flags = "Beta", pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT
    p.add_widget(w)
    x = next(p.widgets()).xref
    d.xref_set_key(int(d.xref_get_key(x, "Parent")[1].split()[0]), "V", "(b)")  # as files keep it
    d.save(str(path))
    return str(path)


def test_several_choices_of_a_list_are_kept(tmp_path):
    from test_features import engine, saved
    e = engine(multi_list(tmp_path / "l.pdf"))
    w = e.widgets(0)[0]
    assert w["multi"] and w["value"] == ["b"]
    e.apply(("fill", 0, 0, ["c", "a"]))
    assert e.widgets(0)[0]["value"] == ["a", "c"]
    d = saved(e, tmp_path)
    x = next(d[0].widgets()).xref
    holder = int(d.xref_get_key(x, "Parent")[1].split()[0])
    assert d.xref_get_key(holder, "V") == ("array", "[(a)(c)]")
    assert d.xref_get_key(holder, "I") == ("array", "[0 2]")
    e = engine(str(tmp_path / "out.pdf"))
    assert e.widgets(0)[0]["value"] == ["a", "c"]
    e.apply(("fill", 0, 0, []))
    assert e.widgets(0)[0]["value"] == []


def test_window_offers_several_choices(win, tmp_path, monkeypatch):  # noqa: F811
    from test_ui_pages import idle
    from furceli import forms
    doc = open_doc(win, multi_list(tmp_path / "l.pdf"))
    shown = []

    def fake(self):
        shown.append([self.list.item(i).text() for i in range(self.list.count())
                      if self.list.item(i).isSelected()])
        for i in range(self.list.count()):
            self.list.item(i).setSelected(self.list.item(i).text() in ("Alpha", "Gamma"))
        return True
    monkeypatch.setattr(forms.ChoicesDialog, "exec", fake)
    w = doc._quiet("widgets", 0)[0]
    doc.field_clicked(0, w)
    idle(doc)
    assert shown == [["Beta"]]
    assert doc._quiet("widgets", 0)[0]["value"] == ["a", "c"]


# -- saving checks what it wrote ----------------------------------------------------------
def pages_with_text(n, odd=None):
    d = pymupdf.open()
    for i in range(n):
        d.new_page().insert_text((72, 72), f"page {i}" + (" changed" if i == odd else ""))
    return d


def test_save_check_reads_every_page_of_ordinary_files(tmp_path):
    from furceli.engine import EngineError, _write
    with pytest.raises(EngineError, match="save_failed"):
        _write(pages_with_text(60, odd=1), str(tmp_path / "a.pdf"), like=pages_with_text(60))


def test_save_check_counts_pictures(tmp_path):
    from furceli.engine import EngineError, _write
    with_pic = pages_with_text(1)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0)
    pix.clear_with(90)
    with_pic[0].insert_image(pymupdf.Rect(100, 100, 200, 200), pixmap=pix)
    with pytest.raises(EngineError, match="save_failed"):
        _write(pages_with_text(1), str(tmp_path / "a.pdf"), like=with_pic)
    _write(with_pic, str(tmp_path / "b.pdf"))  # the same document passes


# -- scanning reads a PDF once --------------------------------------------------------------
def test_scanned_pdf_is_read_once(tmp_path, monkeypatch):
    from furceli import engine as eng
    from furceli import scan
    path = tmp_path / "s.pdf"
    pages_with_text(3).save(str(path))
    opened = []
    real = eng._open_source
    monkeypatch.setattr(eng, "_open_source", lambda *a: opened.append(a) or real(*a))
    scan.count_pages(str(path))
    for i in range(3):
        scan.load(str(path), i)
    assert len(opened) == 1
    pages_with_text(2).save(str(path) + ".new")
    os.replace(str(path) + ".new", str(path))
    os.utime(str(path), ns=(1, 1))  # surely another stamp than before
    assert scan.count_pages(str(path))["pages"] == 2 and len(opened) == 2
    os.remove(str(path))  # nothing keeps the file open


# -- one memory budget for the pictures of all tabs ---------------------------------------
def test_all_tabs_share_one_tile_budget(win, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from test_pages import build
    from furceli import view
    monkeypatch.setattr(view, "TOTAL_BYTES", 40 * 1024 * 1024)
    big = QImage(1024, 1024, QImage.Format.Format_RGB888)
    docs = []
    for n in range(4):
        doc = open_doc(win, build(tmp_path / f"{n}.pdf"))
        for k in range(10):  # 30 MB more than the screen needs, under the 32 MB of a hidden tab
            doc.view._tiles[("tile", 0, 9.9, 0, k, 0, doc.view.rev)] = big.copy()
            doc.view._tile_bytes += big.sizeInBytes()
        docs.append(doc)
    QApplication.processEvents()
    key = next(k for k in docs[-1].view._tiles if k[2] != 9.9)
    img = docs[-1].view._tiles[key]
    docs[-1].view.on_done(key, (img.width(), img.height(), img.width() * 3,
                                bytes(img.width() * img.height() * 3)))
    total = sum(d.view._tile_bytes for d in docs)
    assert total <= 41 * 1024 * 1024, total / 2 ** 20
    assert all(d.view._tile_bytes == sum(t.sizeInBytes() for t in d.view._tiles.values())
               for d in docs)
    assert docs[-1].view._tile_bytes > 25 * 1024 * 1024  # the tab on the screen comes first


# -- closing a tab does not hold up the window ----------------------------------------------
def test_closing_a_busy_tab_returns_at_once(win, tmp_path):  # noqa: F811
    import time
    from test_pages import build
    from furceli import client
    doc = open_doc(win, build(tmp_path / "busy.pdf"))
    tmp = doc._session()
    proc = doc.client._proc
    doc.client.submit(("sleep",), 0, "_sleep", 5)
    wait(lambda: doc.client._current == ("sleep",))
    t = time.monotonic()
    win.close_tab(0, ask=False)
    assert time.monotonic() - t < .25
    client.finish_all()
    assert not proc.is_alive() and not os.path.exists(tmp)


def test_redaction_copies_a_big_file_once(tmp_path, monkeypatch):
    from test_features import engine, saved
    path = str(tmp_path / "many.pdf")
    pages_with_text(6).save(path)
    e = engine(path)
    snaps = []
    real = type(e)._snapshot
    monkeypatch.setattr(type(e), "_snapshot", lambda self: snaps.append(1) or real(self))
    marks = {p: [tuple(next(w for w in e.doc[p].get_text("words") if w[4] == str(p))[:4])]
             for p in range(6)}
    e.apply(("redact", marks, {"fill": (0, 0, 0)}))
    assert len(snaps) == 1
    d = saved(e, tmp_path)
    assert all("".join(d[p].get_text().split()) == "page" for p in range(6))


def test_pages_sharing_a_drawing_are_tried_on_what_they_are_now(tmp_path):
    """Redacting a drawing two pages share may change the second page as well."""
    from test_features import engine, saved
    d = pymupdf.open()
    form = d.get_new_xref()
    font = d.get_new_xref()
    d.update_object(font, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    d.update_object(form, f"<</Type/XObject/Subtype/Form/BBox[0 0 600 800]"
                          f"/Resources<</Font<</F {font} 0 R>>>>>>")
    d.update_stream(form, b"BT /F 14 Tf 72 700 Td (SHAREDSECRET) Tj ET")
    for i in range(2):
        p = d.new_page()
        p.insert_text((72, 300), f"OWN{i} words")
        c = d.get_new_xref()
        d.update_object(c, "<<>>")
        d.update_stream(c, b"q /Fm Do Q")
        kind, res = d.xref_get_key(p.xref, "Resources")
        holder, key = (int(res.split()[0]), "XObject") if kind == "xref" else (p.xref,
                                                                                "Resources/XObject")
        d.xref_set_key(holder, key, f"<</Fm {form} 0 R>>")
        own = d.xref_get_key(p.xref, "Contents")[1].strip("[]")
        d.xref_set_key(p.xref, "Contents", f"[{own} {c} 0 R]")
    path = str(tmp_path / "shared.pdf")
    d.save(path)
    e = engine(path)
    w0 = next(w for w in e.doc[0].get_text("words") if w[4] == "SHAREDSECRET")
    w1 = next(w for w in e.doc[1].get_text("words") if w[4] == "OWN1")
    e.apply(("redact", {0: [tuple(w0[:4])], 1: [tuple(w1[:4])]}, {"fill": (0, 0, 0)}))
    r = saved(e, tmp_path)
    assert "SHAREDSECRET" not in r[0].get_text() and "OWN1" not in r[1].get_text()
    assert "OWN0" in r[0].get_text() and "words" in r[1].get_text()


def test_generated_workbooks_still_show_their_sums(tmp_path):
    """openpyxl and the like keep no results: those must still be worked out."""
    import openpyxl
    b = openpyxl.Workbook()
    b.active["A1"], b.active["A2"], b.active["A3"] = 2, 3, "=SUM(A1:A2)*1111"
    b.save(str(tmp_path / "sum.xlsx"))
    out = str(tmp_path / "sum.pdf")
    convert_or_kill(str(tmp_path / "sum.xlsx"), out)
    assert "5555" in pymupdf.open(out)[0].get_text()


def test_word_page_numbers_still_count(tmp_path):
    import docx
    from docx.oxml import parse_xml
    d = docx.Document()
    for i in range(3):
        d.add_paragraph(f"page body {i}")
        if i < 2:
            d.add_page_break()
    p = d.sections[0].footer.paragraphs[0]
    for x in ('<w:fldChar w:fldCharType="begin"/>', '<w:instrText> PAGE </w:instrText>',
              '<w:fldChar w:fldCharType="separate"/>', '<w:t>1</w:t>', '<w:fldChar w:fldCharType="end"/>',
              '<w:t xml:space="preserve"> of </w:t>',
              '<w:fldChar w:fldCharType="begin"/>', '<w:instrText> NUMPAGES </w:instrText>',
              '<w:fldChar w:fldCharType="separate"/>', '<w:t>1</w:t>', '<w:fldChar w:fldCharType="end"/>'):
        p._p.append(parse_xml(f'<w:r {W}>{x}</w:r>'))
    d.save(str(tmp_path / "pages.docx"))
    out = str(tmp_path / "pages.pdf")
    convert_or_kill(str(tmp_path / "pages.docx"), out)
    r = pymupdf.open(out)
    assert [("%d of 3" % (i + 1)) in r[i].get_text() for i in range(r.page_count)] == [True] * 3


def test_tile_budget_after_a_tab_was_closed(win, tmp_path):  # noqa: F811
    import gc
    from PySide6.QtWidgets import QApplication
    from test_pages import build
    from furceli import view
    first = open_doc(win, build(tmp_path / "a.pdf"))
    gone = first.view
    other = open_doc(win, build(tmp_path / "b.pdf"))
    win.close_tab(0, ask=False)
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for _ in range(5):
        QApplication.processEvents()
    gc.collect()
    v = other.view
    key = next(iter(v._tiles))
    img = v._tiles[key]
    v.on_done(key, (img.width(), img.height(), img.width() * 3, bytes(img.width() * img.height() * 3)))
    assert v._tile_bytes == sum(t.sizeInBytes() for t in v._tiles.values())
    assert gone not in view._VIEWS or sum(x._tile_bytes for x in view._VIEWS) <= view.TOTAL_BYTES
    del gone
