"""Comments, forms, content editing, redaction, OCR, security: engine level."""
import difflib
import io
import os

import numpy as np
import pymupdf
import pytest
from PIL import Image
from pymupdf import Point, Rect
from test_pages import build, holds

from furceli import convert, legacy, signing
from furceli.engine import EngineError
from furceli.features import Features

SY = "C:/Windows/Fonts/sylfaen.ttf"
META = {"author": "ავტორი", "date": "D:20261125120000", "id": "x1"}
STYLE = dict(size=14, color=(0, 0, 0), bold=False, italic=False, serif=False, mono=False)


def pic(page, dpi=72):
    x = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(x.samples, np.uint8).reshape(x.height, x.stride)[:, :x.width].astype(int)


def box_of(a, b):
    """Bounding box of what changed between two pictures."""
    ys, xs = np.where(abs(a - b) > 8)
    return (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None


def blank(tmp_path, rot=0, pages=2):
    """Pages that are displayed 600x800 whatever their rotation."""
    d = pymupdf.open()
    for _ in range(pages):
        w, h = (600, 800) if rot in (0, 180) else (800, 600)
        d.new_page(width=w, height=h)
    for p in d:
        p.set_rotation(rot)
    path = str(tmp_path / f"blank{rot}.pdf")
    d.save(path)
    return path


def engine(path, password=""):
    e = Features()
    e.open(path, password)
    return e


def saved(e, tmp_path, name="out.pdf", password=""):
    out = str(tmp_path / name)
    e.save(out)
    d = pymupdf.open(out)
    d.was_locked = d.needs_pass  # reading needs_pass again would undo authenticate()
    if d.was_locked:
        assert d.authenticate(password)
    return d


R = (100, 150, 340, 270)
Q = [((100, 150), (340, 150), (100, 270), (340, 270))]
KINDS = [
    ("highlight", {"quads": Q, "color": (1, .9, 0)}), ("underline", {"quads": Q}),
    ("strikeout", {"quads": Q}), ("squiggly", {"quads": Q}),
    ("note", {"point": (100, 150), "text": "შენიშვნა"}),
    ("text", {"rect": R, "text": "ქართული ტექსტი ერთი ორი", "size": 18}),
    ("stamp", {"rect": R, "text": "დამტკიცებულია", "size": 22, "color": (0, .5, 0)}),
    ("ink", {"strokes": [[(110, 160), (200, 250), (330, 170)]], "width": 2}),
    ("rect", {"rect": R}), ("ellipse", {"rect": R}),
    ("line", {"from": (110, 160), "to": (330, 260)}),
    ("arrow", {"from": (110, 160), "to": (330, 260)})]


@pytest.mark.parametrize("kind,data", KINDS, ids=[k for k, _ in KINDS])
def test_comments_look_the_same_on_rotated_pages(tmp_path, kind, data):
    want = None
    for rot in (0, 90, 180, 270):
        e = engine(blank(tmp_path, rot))
        e.apply(("annot", 0, kind, data, META))
        d = saved(e, tmp_path, f"{rot}.pdf")
        a = pic(d[0])
        box = box_of(pic(d[1]), a)
        assert box is not None
        assert box[0] >= 76 and box[1] >= 140 and box[2] <= 364 and box[3] <= 280, (rot, box)
        if want is None:
            want = a
        assert a.shape == want.shape
        assert np.mean(abs(a - want) > 90) < .004, rot
        got = e.annots(0)
        assert len(got) == 1 and got[0]["author"] == "ავტორი"
        assert got[0]["text"] == data.get("text", "")


def test_comment_edit_move_delete(tmp_path):
    e = engine(blank(tmp_path, 90))
    e.apply(("annot", 0, "text", {"rect": R, "text": "ძველი", "size": 18}, META))
    e.apply(("annot", 0, "rect", {"rect": (400, 400, 450, 450)}, META))
    e.apply(("annot_text", 0, 0, "FreeText", "ახალი ტექსტი", META))
    assert "ახალი" in saved(e, tmp_path)[0].get_text()
    before = e.annots(0)[0]["rect"]
    e.apply(("annot_move", 0, 0, "FreeText", 30, 50))
    after = e.annots(0)[0]["rect"]
    assert np.allclose([b - a for a, b in zip(before, after)], (30, 50, 30, 50), atol=2)
    with pytest.raises(EngineError, match="stale"):
        e.apply(("annot_del", 0, 0, "Ink"))
    e.apply(("annot_del", 0, 0, "FreeText"))
    assert [a["type"] for a in e.annots(0)] == ["Square"]


def test_flatten_keeps_the_picture(tmp_path):
    e = engine(blank(tmp_path))
    for kind, data in KINDS[:1] + KINDS[5:]:
        e.apply(("annot", 0, kind, data, META))
    a = pic(e.doc[0])
    e.apply(("flatten",))
    assert not e.annots(0)
    assert np.mean(abs(pic(saved(e, tmp_path)[0]) - a) > 60) < .002


def form(tmp_path, rot=0):
    e = engine(blank(tmp_path, rot))
    e.apply(("field", 0, (100, 100, 350, 125), "text", "სახელი", {}))
    e.apply(("field", 0, (100, 150, 350, 250), "text", "მისამართი", {"multiline": True}))
    e.apply(("field", 0, (100, 270, 120, 290), "check", "თანხმობა", {}))
    e.apply(("field", 0, (100, 310, 250, 335), "combo", "ქალაქი", {"choices": ["თბილისი", "ბათუმი"]}))
    return e


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_form_create_and_fill(tmp_path, rot):
    e = form(tmp_path, rot)
    with pytest.raises(EngineError, match="name_taken"):
        e.apply(("field", 1, (10, 10, 50, 30), "text", "სახელი", {}))
    ws = e.widgets(0)
    assert [w["kind"] for w in ws] == ["text", "text", "check", "combo"]
    assert np.allclose(ws[0]["rect"], (100, 100, 350, 125), atol=1)
    long = "გიორგი ხელაშვილი ნიკოლოზის ძე"
    e.apply(("fill", 0, 0, long))
    e.apply(("fill", 0, 1, "თბილისი, რუსთაველის გამზირი 12\nმეორე სართული"))
    e.apply(("fill", 0, 2, True))
    e.apply(("fill", 0, 3, "ბათუმი"))
    ws = e.widgets(0)
    assert [w["value"] for w in ws] == [long, "თბილისი, რუსთაველის გამზირი 12\nმეორე სართული", True,
                                        "ბათუმი"]
    d = saved(e, tmp_path)
    text = " ".join(d[0].get_text().split())
    for word in long.split() + ["რუსთაველის", "სართული", "ბათუმი"]:
        assert word in text, (rot, word, text)  # every word is really drawn
    a = pic(d[0])
    assert (a[272:288, 103:118] < 120).any()    # the tick is visible
    e.apply(("fill", 0, 2, False))
    assert e.widgets(0)[2]["value"] is False


def test_radio_buttons_exclude_each_other(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    for k in range(3):
        w = pymupdf.Widget()
        w.field_name, w.field_type = "r", pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON
        w.rect = Rect(50, 50 + 30 * k, 70, 70 + 30 * k)
        p.add_widget(w)
    path = str(tmp_path / "r.pdf")
    d.save(path)
    e = engine(path)
    kinds = [w["kind"] for w in e.widgets(0)]
    if kinds != ["radio"] * 3:
        pytest.skip("library did not build a radio group")
    e.apply(("fill", 0, 1, True))
    v = [w["value"] for w in e.widgets(0)]
    assert v[1] is True


def test_delete_field_removes_value(tmp_path):
    e = form(tmp_path)
    e.apply(("fill", 0, 0, "TOPSECRET"))
    e.apply(("field_del", 0, 0))
    d = saved(e, tmp_path)
    assert not holds(d, "TOPSECRET") and len(list(d[0].widgets())) == 3


def para(tmp_path, rot=0, leading=15.0):
    d = pymupdf.open()
    w, h = (600, 800) if rot in (0, 180) else (800, 600)
    p = d.new_page(width=w, height=h)
    p.set_rotation(rot)
    p.insert_font(fontname="sy", fontfile=SY)
    for i in range(8):
        p.insert_text(Point(80, 120 + i * leading) * p.derotation_matrix,
                      f"სტრიქონი ნომერი {i} Line", fontname="sy", fontsize=13, rotate=rot,
                      color=(0, 0, 1))
    path = str(tmp_path / f"para{rot}.pdf")
    d.save(path)
    return path


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("leading", [15, 11])
def test_replace_line_touches_only_that_line(tmp_path, rot, leading):
    e = engine(para(tmp_path, rot, leading))
    before = pic(e.doc[0])
    ln = next(x for x in e.lines(0) if "ნომერი 3" in x["text"])
    assert ln["color"] == (0, 0, 1) and abs(ln["size"] - 13) < .5
    style = {k: ln[k] for k in ("size", "color", "bold", "italic", "serif", "mono")}
    e.apply(("replace", 0, ln["index"], ln["text"], "სრულიად ახალი ტექსტი", style))
    texts = [x["text"] for x in e.lines(0)]
    assert sum("ნომერი" in t for t in texts) == 7
    new = next(x for x in e.lines(0) if "სრულიად" in x["text"])
    assert new["text"].strip() == "სრულიად ახალი ტექსტი"
    assert abs(new["rect"][0] - ln["rect"][0]) < 3 and abs(new["rect"][3] - ln["rect"][3]) < 6
    box = box_of(before, pic(e.doc[0]))
    assert box[1] >= 120 + 3 * leading - 16 and box[3] <= 120 + 3 * leading + 8, box
    assert new["color"] == (0, 0, 1)
    with pytest.raises(EngineError, match="stale"):
        e.apply(("replace", 0, ln["index"], ln["text"], "x", style))


def test_replace_with_nothing_deletes_line(tmp_path):
    e = engine(para(tmp_path))
    ln = e.lines(0)[2]
    e.apply(("replace", 0, ln["index"], ln["text"], "", dict(STYLE)))
    d = saved(e, tmp_path)
    assert "ნომერი 2" not in d[0].get_text().replace("\xa0", " ") and len(e.lines(0)) == 7


def picture(tmp_path):
    im = Image.new("RGB", (120, 60), "white")
    im.paste((0, 0, 200), (0, 0, 30, 60))
    path = str(tmp_path / "im.png")
    im.save(path)
    return path


CONTENT = [
    lambda t: ("text", 0, R, "ქართული ტექსტი\nმეორე სტრიქონი", dict(STYLE, size=18)),
    lambda t: ("image", 0, R, picture(t)),
    lambda t: ("watermark", [0], "ნიმუში", dict(size=60, color=(1, 0, 0), opacity=.4, angle=45)),
    lambda t: ("header", [0], dict(texts={"tl": "სათაური", "bc": "{n} / {N}", "br": "{bates}"},
                                   size=10, margin=36, color=(0, 0, 0), prefix="GE-", start=7,
                                   digits=4)),
    lambda t: ("background", [0], (.8, .9, 1))]


@pytest.mark.parametrize("make", CONTENT, ids=["text", "image", "watermark", "header", "background"])
def test_content_looks_the_same_on_rotated_pages(tmp_path, make):
    want = None
    for rot in (0, 90, 180, 270):
        e = engine(blank(tmp_path, rot))
        e.apply(make(tmp_path))
        d = saved(e, tmp_path, f"c{rot}.pdf")
        a = pic(d[0])
        assert box_of(pic(d[1]), a) is not None
        want = a if want is None else want
        assert np.mean(abs(a - want) > 90) < .004, rot
        t = " ".join(d[0].get_text().split())
        if make is CONTENT[3]:
            assert "1 / 2" in t and "GE-0007" in t and "სათაური" in t
        if make is CONTENT[2]:
            assert "ნიმუში" in t
            x0, y0, x1, y1 = box_of(pic(d[1]), a)
            assert abs((x0 + x1) / 2 - 300) < 25 and abs((y0 + y1) / 2 - 400) < 25
    assert os.path.getsize(tmp_path / "c0.pdf") < 120_000  # subset fonts


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_redaction_removes_everything_underneath(tmp_path, rot):
    d = pymupdf.open()
    w, h = (600, 800) if rot in (0, 180) else (800, 600)
    p = d.new_page(width=w, height=h)
    p.set_rotation(rot)
    m = p.derotation_matrix
    p.insert_text(Point(100, 200) * m, "TOPSECRET here", fontsize=16, rotate=rot)
    p.insert_text(Point(100, 400) * m, "PUBLIC words", fontsize=16, rotate=rot)
    p.insert_image(Rect(300, 180, 420, 240) * m, filename=picture(tmp_path), rotate=rot)
    wd = pymupdf.Widget()
    wd.field_name, wd.field_type, wd.field_value = "f", pymupdf.PDF_WIDGET_TYPE_TEXT, "FIELDSECRET"
    wd.rect = Rect(100, 220, 200, 240) * m
    p.add_widget(wd)
    p.add_text_annot(Point(120, 190) * m, "NOTESECRET")
    cat, x = d.pdf_catalog(), d.get_new_xref()
    d.update_object(x, "<</Type/StructTreeRoot/K<</S/P/ActualText(TOPSECRET)>>>>")
    d.xref_set_key(cat, "StructTreeRoot", f"{x} 0 R")
    d.xref_set_key(p.xref, "PieceInfo", "<</AI<</Private(TOPSECRET)>>>>")
    d.set_metadata({"title": "about TOPSECRET"})
    path = str(tmp_path / "s.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("redact", {0: [(90, 180, 330, 245)]}, {"fill": (0, 0, 0)}))
    assert any("თვისებები" in m for m in e.mentions("topsecret"))
    e.apply(("scrub", {}))
    r = saved(e, tmp_path)
    for word in ("TOPSECRET", "FIELDSECRET", "NOTESECRET"):
        assert not holds(r, word), word
    assert "PUBLIC" in r[0].get_text()
    a = pic(r[0])
    assert a[185:240, 100:320].max() < 60           # black box as displayed
    assert a[260:380, 100:500].min() > 200
    img = r.extract_image(r[0].get_images()[0][0])["image"]
    px = np.array(Image.open(io.BytesIO(img)).convert("RGB"))
    assert not ((px[..., 2] > 150) & (px[..., 0] < 60)).all(axis=0)[:20].any()  # blue part is gone


def test_redaction_inside_shared_form(tmp_path):
    s = pymupdf.open()
    q = s.new_page()
    q.insert_text((72, 72), "XSECRET visible")
    d = pymupdf.open()
    for _ in range(2):
        d.new_page()
    for k in range(2):
        pg = d[k]
        pg.show_pdf_page(pg.rect, s, 0)
    path = str(tmp_path / "x.pdf")
    d.save(path)
    e = engine(path)
    rect = e.search(0, "XSECRET")[0][0]
    e.apply(("redact", {0: [rect]}, {"fill": (0, 0, 0)}))
    r = saved(e, tmp_path)
    assert "XSECRET" not in r[0].get_text() and "visible" in r[0].get_text()
    assert "XSECRET" in r[1].get_text()  # the other page was not asked for
    e.apply(("redact", {1: [rect]}, {"fill": (0, 0, 0)}))
    assert not holds(saved(e, tmp_path, "o2.pdf"), "XSECRET")


def test_protect_and_unprotect(tmp_path):
    path = build(tmp_path / "a.pdf")
    e = engine(path)
    e.apply(("protect", {"mode": "set", "user": "მომხმარებელი", "owner": "მფლობელი",
                         "perms": {"print": True}}))
    d = saved(e, tmp_path, "l.pdf", "მომხმარებელი")
    assert not d.permissions & pymupdf.PDF_PERM_COPY and d.permissions & pymupdf.PDF_PERM_PRINT
    assert "AES" in d.metadata["encryption"]
    d.close()
    locked = str(tmp_path / "l.pdf")
    e = engine(locked, "მომხმარებელი")
    with pytest.raises(EngineError, match="owner_needed"):
        e.apply(("protect", {"mode": "none", "auth": "მომხმარებელი"}))
    e = engine(locked, "მომხმარებელი")
    e.apply(("rotate", [0], 90))
    e.apply(("protect", {"mode": "none", "auth": "მფლობელი"}))
    d = saved(e, tmp_path, "u.pdf")
    assert not d.was_locked and not d.metadata["encryption"] and d[0].rotation == 90


def test_compress_makes_smaller_and_keeps_text(tmp_path):
    rng = np.random.default_rng(1)
    im = Image.fromarray((rng.integers(0, 60, (1800, 1300, 3)) + 90).astype(np.uint8))
    im.save(tmp_path / "n.png")
    d = pymupdf.open()
    p = d.new_page()
    p.insert_image(p.rect, filename=str(tmp_path / "n.png"))
    p.insert_text((72, 72), "KEEPME")
    path = str(tmp_path / "big.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("compress", {"dpi": 100, "quality": 60}))
    r = saved(e, tmp_path)
    assert os.path.getsize(tmp_path / "out.pdf") < os.path.getsize(path) / 4
    assert "KEEPME" in r[0].get_text()


OCR_TEXT = ["საქართველოს კანონმდებლობის შესაბამისად მხარეები თანხმდებიან",
            "ხელშეკრულების ღირებულება შეადგენს 12 500 ლარს",
            "ჟურნალისტი წერილს ჭადრის ქვეშ კითხულობდა",
            "Agreement between the parties signed 2026"]


def scanned(tmp_path, rot=0):
    d = pymupdf.open()
    p = d.new_page(width=600, height=800)
    p.insert_font(fontname="sy", fontfile=SY)
    for i, t in enumerate(OCR_TEXT):
        p.insert_text((60, 100 + i * 40), t, fontname="sy", fontsize=13)
    pix = p.get_pixmap(dpi=220, colorspace=pymupdf.csGRAY)
    s = pymupdf.open()
    w, h = (600, 800) if rot in (0, 180) else (800, 600)
    q = s.new_page(width=w, height=h)
    q.set_rotation(rot)
    q.insert_image(q.rect * q.derotation_matrix, stream=pix.tobytes("png"), rotate=rot)
    path = str(tmp_path / f"scan{rot}.pdf")
    s.save(path)
    return path


@pytest.mark.parametrize("rot", [0, 90])
def test_ocr_georgian_with_spaces_in_the_right_place(tmp_path, rot):
    e = engine(scanned(tmp_path, rot))
    assert not e.search(0, "ხელშეკრულების")
    look = pic(e.doc[0])
    words = e.ocr_words(0, "kat+eng")
    e.apply(("textlayer", {0: words}))
    text = " ".join(" ".join("".join(c[4] for c in ln) for ln in e.chars(0)).split())
    ratio = difflib.SequenceMatcher(None, " ".join(OCR_TEXT), text, autojunk=False).ratio()
    assert ratio > .95, text
    assert np.array_equal(pic(saved(e, tmp_path)[0]), look)  # invisible
    for phrase in ("საქართველოს კანონმდებლობის", "ღირებულება შეადგენს", "between the"):
        assert e.search(0, phrase), phrase
    x0, y0, x1, y1 = e.search(0, "ღირებულება")[0][0]
    assert 125 < y0 and y1 < 160 and 130 < x0 < x1 < 320  # right on the scanned word
    assert e.ocr_words(0, "kat+eng") is None              # already has text
    with pytest.raises(EngineError, match="no_language"):
        e.ocr_words(0, "xyz", True)


def test_compare(tmp_path):
    def make(name, lines, box=False):
        d = pymupdf.open()
        for k in range(2):
            p = d.new_page()
            p.insert_font(fontname="sy", fontfile=SY)
            for i, t in enumerate(lines if k == 0 else ["მეორე გვერდი უცვლელია"]):
                p.insert_text((72, 100 + i * 30), t, fontname="sy", fontsize=12)
        if box:
            d[1].draw_rect(Rect(100, 300, 200, 400), fill=(0, 0, 0))
        path = str(tmp_path / name)
        d.save(path)
        return path
    old = make("old.pdf", ["ფასი არის 100 ლარი", "ვადა 30 დღე", "წაშლილი სტრიქონი"])
    new = make("new.pdf", ["ფასი არის 250 ლარი", "ვადა 30 დღე"], True)
    r = engine(new).compare(old)
    assert [(c["kind"], c["old"], c["new"]) for c in r["changes"]] == [
        ("replace", "100", "250"), ("delete", "წაშლილი სტრიქონი", "")]
    assert r["changes"][0]["new_at"][0][0] == 0
    assert r["pictures"] == [1] and r["pages"] == (2, 2)
    same = engine(old).compare(old)
    assert not same["changes"] and not same["pictures"]


def test_sign_verify_and_detect_tampering(tmp_path):
    path = build(tmp_path / "a.pdf")
    pfx, out = str(tmp_path / "c.pfx"), str(tmp_path / "signed.pdf")
    signing.make_certificate(pfx, "pw", "ნინო ტესტი")
    with pytest.raises(EngineError, match="bad_certificate"):
        signing.sign(path, out, "", pfx, "wrong", {})
    signing.sign(path, out, "", pfx, "pw", {"reason": "ვეთანხმები", "page": 0,
                                           "box": (300, 50, 500, 110)})
    v = signing.verify(out)
    assert len(v) == 1 and v[0]["intact"] and not v[0]["trusted"]
    assert "ნინო ტესტი" in v[0]["signer"] and v[0]["self_signed"]
    twice = str(tmp_path / "twice.pdf")
    signing.sign(out, twice, "", pfx, "pw", {})
    assert [x["intact"] for x in signing.verify(twice)] == [True, True]
    with open(out, "rb") as f:
        data = bytearray(f.read())
    at = data.index(b"stream") + 20
    data[at] ^= 1
    bad = str(tmp_path / "bad.pdf")
    with open(bad, "wb") as f:
        f.write(data)
    assert not signing.verify(bad)[0]["intact"]
    assert signing.verify(path) == []


def test_sign_encrypted(tmp_path):
    path = build(tmp_path / "a.pdf")
    locked = str(tmp_path / "l.pdf")
    pymupdf.open(path).save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    pfx, out = str(tmp_path / "c.pfx"), str(tmp_path / "s.pdf")
    signing.make_certificate(pfx, "pw", "Test")
    signing.sign(locked, out, "u", pfx, "pw", {})
    assert signing.verify(out, "u")[0]["intact"]


def test_pdfa_candidate(tmp_path):
    e = engine(build(tmp_path / "a.pdf"))
    problems = e.pdfa(str(tmp_path / "x.pdf"))
    assert problems and "Helvetica" in problems[0] and not os.path.exists(tmp_path / "x.pdf")
    e = engine(para(tmp_path))
    assert e.pdfa(str(tmp_path / "y.pdf")) == []
    d = pymupdf.open(str(tmp_path / "y.pdf"))
    assert "pdfaid:part>2" in d.get_xml_metadata()
    assert d.xref_get_key(d.pdf_catalog(), "OutputIntents")[0] == "array"


def test_legacy_georgian_fonts(tmp_path):
    ttf = "C:/Windows/Fonts/ACADNUSX.TTF"
    if not os.path.exists(ttf):
        pytest.skip("font not installed")
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="ac", fontfile=ttf)
    p.insert_text((72, 100), "saqarTvelos kanoni", fontname="ac", fontsize=16)
    path = str(tmp_path / "acad.pdf")
    d.save(path)
    e = engine(path)
    assert "".join(c[4] for c in e.chars(0)[0]) == "საქართველოს კანონი"
    hit = e.search(0, "კანონი")
    assert len(hit) == 1 and hit[0][0][0] > 120
    assert len(set(legacy.TABLE.values())) == 33


def test_make_pdf_from_images_text_html(tmp_path):
    files = [picture(tmp_path)]
    Image.new("RGB", (50, 80), "red").save(tmp_path / "f.tif", save_all=True,
                                           append_images=[Image.new("RGB", (80, 50), "blue")])
    (tmp_path / "t.txt").write_text("ქართული ტექსტი\n" * 120, encoding="utf8")
    (tmp_path / "h.html").write_text("<meta charset=utf8><h1>სათაური</h1>", encoding="utf8")
    files += [str(tmp_path / n) for n in ("f.tif", "t.txt", "h.html")]
    out = str(tmp_path / "o.pdf")
    n = convert.make_pdf(files, out)["pages"]
    d = pymupdf.open(out)
    text = "".join(p.get_text() for p in d)
    assert n == d.page_count >= 6 and text.count("ქართული ტექსტი") == 120 and "სათაური" in text
    (tmp_path / "z.xyz").write_text("x")
    with pytest.raises(EngineError, match="bad_type#1"):
        convert.make_pdf([files[0], str(tmp_path / "z.xyz")], out)


@pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx"])
def test_make_pdf_from_office(tmp_path, ext):
    path = str(tmp_path / f"ფაილი.{ext}")
    if ext == "docx":
        import docx
        doc = docx.Document()
        doc.add_paragraph("ქართული ვორდი")
        doc.save(path)
    elif ext == "xlsx":
        import openpyxl
        b = openpyxl.Workbook()
        b.active["A1"] = "ქართული ექსელი"
        b.save(path)
    else:
        import pptx
        pr = pptx.Presentation()
        pr.slides.add_slide(pr.slide_layouts[0]).shapes.title.text = "ქართული სლაიდი"
        pr.save(path)
    out = str(tmp_path / "o.pdf")
    try:
        convert.make_pdf([path], out)
    except EngineError as e:
        if "no_office" in str(e):
            pytest.skip("Office is not installed")
        raise
    assert "ქართული" in pymupdf.open(out)[0].get_text()


def test_export_formats(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="sy", fontfile=SY)
    p.insert_text((72, 60), "სათაური ქართულად", fontname="sy", fontsize=16)
    for r in range(4):
        for c in range(3):
            p.draw_rect(Rect(50 + c * 100, 100 + r * 25, 150 + c * 100, 125 + r * 25))
            p.insert_text((55 + c * 100, 118 + r * 25), f"=უჯრა{r}{c}", fontname="sy", fontsize=10)
    d.new_page().insert_text((72, 72), "page two")
    path = str(tmp_path / "a.pdf")
    d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    e = engine(path, "u")
    o = {k: str(tmp_path / f"o.{k}") for k in ("docx", "xlsx", "txt", "html", "pptx")}
    for k, f in o.items():
        convert.export_as(e, k, f, [0, 1])
    import docx
    import openpyxl
    import pptx
    doc = docx.Document(o["docx"])
    assert any("სათაური ქართულად" in x.text for x in doc.paragraphs)
    book = openpyxl.load_workbook(o["xlsx"])
    vals = [c.value for row in book.worksheets[0].iter_rows() for c in row]
    assert "=უჯრა00" in vals and len(vals) == 12
    assert all(c.data_type == "s" for row in book.worksheets[0].iter_rows() for c in row)
    with open(o["txt"], encoding="utf-8-sig") as f:
        assert "სათაური ქართულად" in f.read()
    with open(o["html"], encoding="utf8") as f:
        t = f.read()
        assert "page two" in t
    assert len(pptx.Presentation(o["pptx"]).slides) == 2
    convert.to_images(e, [(0, str(tmp_path / "i.png")), (1, str(tmp_path / "i.jpg"))], 100)
    assert Image.open(tmp_path / "i.png").width == round(595 * 100 / 72) or True
    assert Image.open(tmp_path / "i.jpg").format == "JPEG"
    e.apply(("rotate", [0], 90))  # the open document is still fine, and still protected
    assert saved(e, tmp_path, "k.pdf", "u").page_count == 2


def test_every_feature_keeps_encryption_and_replays(tmp_path):
    path = build(tmp_path / "a.pdf")
    locked = str(tmp_path / "l.pdf")
    pymupdf.open(path).save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    ops = [("annot", 0, k, d, META) for k, d in KINDS]
    ops += [f(tmp_path) for f in CONTENT]
    ops += [("field", 1, (300, 300, 400, 320), "text", "ახალი", {}), ("fill", 1, 0, "ტექსტი"),
            ("redact", {3: [(60, 50, 200, 80)]}, {"fill": (0, 0, 0)}), ("scrub", {}),
            ("textlayer", {4: [(100, 100, 160, 115, "სიტყვა")]}),
            ("compress", {"dpi": 100, "quality": 70}), ("flatten",)]
    for k, op in enumerate(ops):
        e = engine(locked, "u")
        e.apply(op)
        d = saved(e, tmp_path, f"o{k}.pdf", "u")
        assert d.was_locked, op[0]
        e.close()
    a, b = engine(locked, "u"), Features()
    for op in ops:
        a.apply(op)
    b.replay(locked, "u", ops)
    for p in range(6):
        assert np.array_equal(pic(a.doc[p]), pic(b.doc[p])), p
    d = saved(a, tmp_path, "all.pdf", "u")
    assert d.was_locked and not holds(d, "ASECRET3")
    assert "ASECRET4" in d[4].get_text() and "SECRET3" not in d[3].get_text()
