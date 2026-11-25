"""Problems first seen on real documents, rebuilt as small synthetic files."""
import os
import time

import numpy as np
import pymupdf
import pytest
from test_features import META, engine, pic, saved
from test_pages import build, holds

from furceli import convert
from furceli.engine import EngineError

SY = "C:/Windows/Fonts/sylfaen.ttf"


def ucs_font_pdf(path, lines):
    """Identity-H font without ToUnicode whose codes are Unicode values (bank statements)."""
    from fontTools.ttLib import TTFont
    font = TTFont(SY)
    cmap, order = font.getBestCmap(), {n: i for i, n in enumerate(font.getGlyphOrder())}
    table = bytearray(0x10000 * 2)
    widths = []
    upm = font["head"].unitsPerEm
    for code, name in cmap.items():
        if code < 0x10000:
            table[code * 2:code * 2 + 2] = order[name].to_bytes(2, "big")
            widths.append(f"{code}[{font['hmtx'][name][0] * 1000 // upm}]")
    d = pymupdf.open()
    p = d.new_page()
    ids = [d.get_new_xref() for _ in range(6)]
    f0, cid, desc, file, gid, cont = ids
    with open(SY, "rb") as f:
        data = f.read()
    for x in (file, gid, cont):
        d.update_object(x, "<<>>")
    d.update_stream(file, data)
    d.update_stream(gid, bytes(table))
    d.update_object(desc, f"<</Type/FontDescriptor/FontName/ABCDEF+Sylfaen-UCS/Flags 4/FontBBox[0 -300 1000 900]"
                          f"/ItalicAngle 0/Ascent 900/Descent -300/CapHeight 700/StemV 80/FontFile2 {file} 0 R>>")
    d.update_object(cid, f"<</Type/Font/Subtype/CIDFontType2/BaseFont/ABCDEF+Sylfaen-UCS/CIDSystemInfo"
                         f"<</Registry(Adobe)/Ordering(Identity)/Supplement 0>>/FontDescriptor {desc} 0 R"
                         f"/CIDToGIDMap {gid} 0 R/W[{' '.join(widths)}]>>")
    d.update_object(f0, f"<</Type/Font/Subtype/Type0/BaseFont/ABCDEF+Sylfaen-UCS/Encoding/Identity-H"
                        f"/DescendantFonts[{cid} 0 R]>>")
    body = "".join(f"BT /F1 12 Tf 1 0 0 1 60 {760 - 24 * i} Tm <{t.encode('utf-16-be').hex()}> Tj ET\n"
                   for i, t in enumerate(lines))
    d.update_stream(cont, body.encode())
    d.xref_set_key(p.xref, "Contents", f"{cont} 0 R")
    d.xref_set_key(p.xref, "Resources", f"<</Font<</F1 {f0} 0 R>>>>")
    d.save(path, garbage=1)
    return path


LINES = ["ანგარიშის ამონაწერი", "თანხა -1250.50 GEL", "დანიშნულება: ხელფასი"]


def test_text_without_unicode_table_is_readable(tmp_path):
    e = engine(ucs_font_pdf(str(tmp_path / "bank.pdf"), LINES))
    got = ["".join(c[4] for c in ln) for ln in e.chars(0)]
    assert got == LINES
    assert len(e.search(0, "ამონაწერი")) == 1 and len(e.search(0, "-1250.50")) == 1
    assert [ln["text"] for ln in e.lines(0)] == LINES
    assert e.page_texts()[0].split("\n") == LINES


def test_text_without_unicode_table_can_be_redacted_and_edited(tmp_path):
    path = ucs_font_pdf(str(tmp_path / "bank.pdf"), LINES)
    e = engine(path)
    e.apply(("redact", {0: [e.search(0, "ხელფასი")[0][0]]}, {"fill": (0, 0, 0)}))
    saved(e, tmp_path)
    assert not engine(str(tmp_path / "out.pdf")).search(0, "ხელფასი")
    e = engine(path)
    ln = e.lines(0)[1]
    e.apply(("replace", 0, ln["index"], ln["text"], "თანხა 7", {
        k: ln[k] for k in ("size", "color", "bold", "italic", "serif", "mono")}))
    assert sorted(x["text"].strip() for x in e.lines(0)) == sorted([LINES[0], "თანხა 7", LINES[2]])


def test_glyph_numbers_are_never_shown_as_text(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="sy", fontfile=SY)
    p.insert_text((72, 100), "ქართული ტექსტი", fontname="sy", fontsize=14)
    x = p.get_fonts()[0][0]
    d.xref_set_key(x, "ToUnicode", "null")  # codes are glyph numbers here
    path = str(tmp_path / "g.pdf")
    d.save(path)
    text = "".join(c[4] for ln in engine(path).chars(0) for c in ln)
    assert "ქართული" in text or all(c in "\ufffd " for c in text)


def test_hyphen_glyph_is_a_real_minus(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="sy", fontfile=SY)
    p.insert_text((72, 100), "ფასი -250 ლარი", fontname="sy", fontsize=14)
    path = str(tmp_path / "m.pdf")
    d.save(path)
    e = engine(path)
    assert "".join(c[4] for c in e.chars(0)[0]) == "ფასი -250 ლარი"
    assert len(e.search(0, "-250")) == 1 and "-250" in e.search(0, "ფასი")[0][1]


def damaged_xref(tmp_path):
    """References to objects that do not exist, as many programs write them."""
    d = pymupdf.open(build(tmp_path / "a.pdf"))
    d.xref_set_key(d.pdf_catalog(), "Broken", "9999 0 R")
    data = d.tobytes(garbage=0)
    d.close()
    path = str(tmp_path / "broken.pdf")
    # a cross-reference entry that points into nowhere
    at = data.rindex(b" 00000 n")
    data = data[:at - 10] + b"0000000007" + data[at:]
    with open(path, "wb") as f:
        f.write(data)
    return path


def test_scrub_works_on_files_with_damaged_references(tmp_path):
    e = engine(damaged_xref(tmp_path))
    look = [pic(p) for p in e.doc]
    e.apply(("scrub", {}))
    d = saved(e, tmp_path)
    assert all(np.array_equal(a, pic(p)) for a, p in zip(look, d)) and not d.metadata["title"]


def test_scrub_removes_attachments_everywhere(tmp_path):
    d = pymupdf.open(build(tmp_path / "a.pdf"))
    d.embfile_add("x", b"SECRETFILE1", filename="x.txt")
    d[0].add_file_annot((100, 100), b"SECRETFILE2", "y.txt")
    path = str(tmp_path / "b.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("scrub", {}))
    r = saved(e, tmp_path)
    assert r.embfile_count() == 0 and not holds(r, "SECRETFILE1") and not holds(r, "SECRETFILE2")
    assert "x.txt" not in "".join(r.xref_object(k) for k in range(1, r.xref_length()))
    keep = engine(path)
    keep.apply(("scrub", {"files": False}))
    assert holds(saved(keep, tmp_path, "k.pdf"), "SECRETFILE1")


def test_scrub_hidden_text(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 100), "VISIBLE")
    p.insert_text((72, 200), "INVISIBLE", render_mode=3)
    path = str(tmp_path / "h.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("scrub", {"hidden": True}))
    t = saved(e, tmp_path)[0].get_text()
    assert "VISIBLE" in t.split() and "INVISIBLE" not in t


def tagged(tmp_path):
    """Three pages whose searchable text is only in the tag tree, plus a shared parent."""
    d = pymupdf.open()
    root, sect = d.get_new_xref(), d.get_new_xref()
    els = []
    for i in range(3):
        p = d.new_page()
        c = d.get_new_xref()
        d.update_object(c, "<<>>")
        d.update_stream(c, b"/Span <</MCID 0>> BDC 0 g 72 700 40 10 re f EMC")
        d.xref_set_key(p.xref, "Contents", f"{c} 0 R")
        d.xref_set_key(p.xref, "StructParents", str(i))
        el = d.get_new_xref()
        d.update_object(el, f"<</Type/StructElem/S/Span/P {sect} 0 R/Pg {p.xref} 0 R/K 0"
                            f"/ActualText(TAGTEXT{i})>>")
        els.append(el)
    kids = " ".join(f"{x} 0 R" for x in els)
    d.update_object(sect, f"<</Type/StructElem/S/Sect/P {root} 0 R/K[{kids}]/Alt(ABOUT TAGTEXT1)>>")
    nums = " ".join(f"{i} [{x} 0 R]" for i, x in enumerate(els))
    d.update_object(root, f"<</Type/StructTreeRoot/K {sect} 0 R/ParentTree<</Nums[{nums}]>>>>")
    d.xref_set_key(d.pdf_catalog(), "StructTreeRoot", f"{root} 0 R")
    d.xref_set_key(d.pdf_catalog(), "MarkInfo", "<</Marked true>>")
    path = str(tmp_path / "t.pdf")
    d.save(path)
    return path


def test_deleting_a_page_keeps_other_pages_tags(tmp_path):
    e = engine(tagged(tmp_path))
    e.apply(("order", [2, 0]))
    d = saved(e, tmp_path)
    assert not holds(d, "TAGTEXT1") and holds(d, "TAGTEXT0") and holds(d, "TAGTEXT2")
    assert sum(1 for x in range(1, d.xref_length())
               if d.xref_get_key(x, "Type")[1] == "/Page") == 2


def test_redaction_drops_the_whole_tag_tree(tmp_path):
    e = engine(tagged(tmp_path))
    e.apply(("redact", {1: [(70, 70, 200, 160)]}, {"fill": (0, 0, 0)}))
    assert not holds(saved(e, tmp_path), "TAGTEXT")


def test_tag_text_of_no_known_page_goes_with_a_deleted_page(tmp_path):
    d = pymupdf.open(tagged(tmp_path))
    root = int(d.xref_get_key(d.pdf_catalog(), "StructTreeRoot")[1].split()[0])
    sect = int(d.xref_get_key(root, "K")[1].split()[0])
    x = d.get_new_xref()
    d.update_object(x, f"<</Type/StructElem/S/P/P {sect} 0 R/ActualText(LOOSETEXT)>>")
    kids = d.xref_get_key(sect, "K")[1]
    d.xref_set_key(sect, "K", kids[:-1] + f" {x} 0 R]")
    path = str(tmp_path / "loose.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("order", [0, 2]))
    r = saved(e, tmp_path)
    assert not holds(r, "LOOSETEXT") and holds(r, "TAGTEXT0")


def test_strange_tag_tree_is_dropped_whole(tmp_path):
    d = pymupdf.open(tagged(tmp_path))
    root = int(d.xref_get_key(d.pdf_catalog(), "StructTreeRoot")[1].split()[0])
    sect = int(d.xref_get_key(root, "K")[1].split()[0])
    d.xref_set_key(sect, "K", f"[{sect} 0 R {sect} 0 R]")  # a loop
    path = str(tmp_path / "loop.pdf")
    d.save(path)
    e = engine(path)
    e.apply(("order", [0, 2]))
    r = saved(e, tmp_path)
    assert not holds(r, "TAGTEXT1") and r.page_count == 2


def test_redaction_with_comments_and_fields_on_the_same_page(tmp_path):
    e = engine(build(tmp_path / "a.pdf"))
    for k in range(3):
        e.apply(("annot", 0, "rect", {"rect": (60 + 40 * k, 50, 90 + 40 * k, 90)}, META))
    e.apply(("redact", {0: [(55, 45, 320, 225)]}, {"fill": (0, 0, 0)}))
    d = saved(e, tmp_path)
    assert not holds(d, "ASECRET0") and not holds(d, "AVALUE0") and holds(d, "ASECRET1")


def test_replace_handles_narrow_and_tall_glyphs(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="sy", fontfile=SY)
    for i in range(6):
        p.insert_text((72, 100 + i * 9), f"ი.,|'!:;-lIი.. {i} j()[] -- ჰჰ", fontname="sy", fontsize=8)
    path = str(tmp_path / "n.pdf")
    d.save(path)
    e = engine(path)
    ln = e.lines(0)[2]
    e.apply(("replace", 0, ln["index"], ln["text"], "ახალი", {
        k: ln[k] for k in ("size", "color", "bold", "italic", "serif", "mono")}))
    texts = [x["text"] for x in e.lines(0)]
    assert len(texts) == 6 and sum("ახალი" in t for t in texts) == 1


def test_slow_word_export_falls_back_to_text(tmp_path, monkeypatch):
    import docx
    monkeypatch.setattr(convert, "DOCX_BASE", .2)
    monkeypatch.setattr(convert, "DOCX_PER_PAGE", 0)
    e = engine(ucs_font_pdf(str(tmp_path / "bank.pdf"), LINES))
    out = str(tmp_path / "o.docx")
    t = time.time()
    assert convert.export_as(e, "docx", out, [0]) == "simple"
    assert time.time() - t < 20
    assert [p.text for p in docx.Document(out).paragraphs] == LINES


def test_word_export_of_a_picture_page(tmp_path, monkeypatch):
    import docx
    monkeypatch.setattr(convert, "DOCX_BASE", .2)
    monkeypatch.setattr(convert, "DOCX_PER_PAGE", 0)
    d = pymupdf.open()
    d.new_page().draw_rect(pymupdf.Rect(50, 50, 300, 300), fill=(1, 0, 0))
    path = str(tmp_path / "p.pdf")
    d.save(path)
    out = str(tmp_path / "o.docx")
    convert.export_as(engine(path), "docx", out, [0])
    assert len(docx.Document(out).inline_shapes) == 1


def test_oversized_crop_boxes_are_clipped_on_opening(tmp_path):
    for rot in (0, 90, 180, 270):
        d = pymupdf.open()
        p = d.new_page(width=600, height=800)
        d.xref_set_key(p.xref, "CropBox", "[-30 -20 640 830]")
        d.xref_set_key(p.xref, "Rotate", str(rot))
        path = str(tmp_path / f"{rot}.pdf")
        d.save(path)
        e = engine(path)
        e.apply(("annot", 0, "rect", {"rect": (100, 150, 200, 250)}, META))
        a = pic(saved(e, tmp_path, f"o{rot}.pdf")[0])
        ys, xs = np.where(a < 200)
        assert abs(xs.min() - 99) < 3 and abs(ys.min() - 149) < 3, rot
        assert abs(xs.max() - 201) < 3 and abs(ys.max() - 251) < 3, rot


def test_bad_operation_is_still_an_error(tmp_path):
    with pytest.raises(EngineError):
        engine(build(tmp_path / "a.pdf")).apply(("order", []))
    assert os.path.exists(SY)


def test_redaction_never_lets_text_slip_aside(tmp_path, monkeypatch):
    """If rewriting a page moves its text, the page becomes a picture instead."""
    path = build(tmp_path / "a.pdf")
    e = engine(path)
    look = pic(e.doc[0])
    real = e._moved
    monkeypatch.setattr(e, "_moved", lambda a, b: real(a, b) + 3)
    info = e.apply(("redact", {0: [(60, 50, 250, 90)]}, {"fill": (0, 0, 0)}))
    assert info["notes"] == [("picture", 0)]
    d = saved(e, tmp_path)
    assert not holds(d, "ASECRET0") and holds(d, "ASECRET1")
    a = pic(d[0])
    assert a[52:88, 62:248].max() < 60                       # covered
    assert np.mean(abs(a[260:] - look[260:]) > 60) < .01     # the rest looks the same
    assert [w.field_value for w in d[0].widgets()] == ["AVALUE0"]
    assert e.apply(("rotate", [1], 90))["notes"] == []       # said once


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_page_as_picture_is_upright(tmp_path, monkeypatch, rot):
    d = pymupdf.open()
    w, h = (600, 800) if rot in (0, 180) else (800, 600)
    p = d.new_page(width=w, height=h)
    p.set_rotation(rot)
    m = p.derotation_matrix
    p.insert_text(pymupdf.Point(100, 200) * m, "TOPSECRET", fontsize=30, rotate=rot)
    p.insert_text(pymupdf.Point(100, 500) * m, "PUBLIC", fontsize=30, rotate=rot)
    path = str(tmp_path / "a.pdf")
    d.save(path)
    e = engine(path)
    look = pic(e.doc[0])
    monkeypatch.setattr(e, "_wipe_plan", lambda *a: None)
    e.apply(("redact", {0: [(95, 170, 320, 210)]}, {"fill": (0, 0, 0)}))
    r = saved(e, tmp_path)
    a = pic(r[0])
    assert a[172:208, 98:318].max() < 60 and r[0].get_text().split() == ["PUBLIC"]
    assert np.mean(abs(a[300:] - look[300:]) > 90) < .01
    box = engine(str(tmp_path / "out.pdf")).search(0, "PUBLIC")[0][0]
    assert abs(box[0] - 100) < 4 and 455 < box[1] < 490 < box[3] < 520  # still where it is seen


def test_moved_text_is_noticed(tmp_path):
    e = engine(build(tmp_path / "a.pdf"))
    g = e._glyphs(e.doc[0])
    assert e._moved(g, g) == 0 and e._moved(g, g[:5]) == 0
    far = [(c, r + (3, 0, 3, 0)) for c, r in g[:4]]
    assert e._moved(g, far + g[4:]) >= 3


def test_redaction_takes_whole_wide_characters(tmp_path):
    """Widths in the file may be wrong: a letter that is half under the mark goes too."""
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 100), "SECRETM", fontsize=30)
    p.insert_text((72, 200), "OTHER", fontsize=30)
    path = str(tmp_path / "w.pdf")
    d.save(path)
    e = engine(path)
    box = e.search(0, "SECRETM")[0][0]
    e.apply(("redact", {0: [(box[0], box[1], box[2] - 9, box[3])]}, {"fill": (0, 0, 0)}))
    t = saved(e, tmp_path)[0].get_text().split()
    assert "OTHER" in t and not any("SECRET" in x for x in t)


def test_excel_export_of_endless_drawings_falls_back(tmp_path, monkeypatch):
    import openpyxl
    monkeypatch.setattr(convert, "XLSX_BASE", .2)
    monkeypatch.setattr(convert, "XLSX_PER_PAGE", 0)
    e = engine(ucs_font_pdf(str(tmp_path / "bank.pdf"), LINES))
    out = str(tmp_path / "o.xlsx")
    assert convert.export_as(e, "xlsx", out, [0]) == "simple"
    rows = [[c.value for c in r if c.value] for r in openpyxl.load_workbook(out).active.iter_rows()]
    assert rows == [[t] for t in LINES]


def test_text_rows_split_columns(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    for x, t in ((72, "one"), (250, "-5"), (400, "x y")):
        p.insert_text((x, 100), t)
    path = str(tmp_path / "c.pdf")
    d.save(path)
    assert convert._text_rows(engine(path), 0) == [["one", "-5", "x y"]]


def test_user_is_told_when_a_page_became_a_picture(tmp_path, monkeypatch):
    from furceli.docview import DocView
    assert "სურათად" in open(__import__("inspect").getsourcefile(DocView), encoding="utf8").read()


def test_search_finds_what_the_library_misses(tmp_path, monkeypatch):
    path = build(tmp_path / "a.pdf")
    e = engine(path)
    monkeypatch.setattr(pymupdf.Page, "search_for", lambda *a, **k: [])
    hit = e.search(0, "asecret0")
    assert len(hit) == 1 and "ASECRET0" in hit[0][1]
    assert e.search(0, "") == [] and e.search(0, "  ") == []


def test_search_every_occurrence_wraps_and_hyphens(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="sy", fontfile=SY)
    for i, t in enumerate(["სიტყვა სიტყვა  სიტყვა", "გრძელი ფრა-", "ზა აქ და ორი", "სიტყვა ბოლოა"]):
        p.insert_text((72, 100 + 20 * i), t, fontname="sy", fontsize=12)
    path = str(tmp_path / "w.pdf")
    d.save(path)
    e = engine(path)
    assert len(e.search(0, "სიტყვა")) == 4
    assert len(e.search(0, "სიტყვა სიტყვა სიტყვა")) == 1   # several spaces count as one
    assert len(e.search(0, "ფრაზა")) == 2                   # two pieces of one hyphenated word
    assert len(e.search(0, "და ორი სიტყვა")) == 2           # across a line end
    boxes = [pymupdf.Rect(r) for r, _ in e.search(0, "სიტყვა")]
    assert all(not a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i + 1:])


def test_search_and_redact_leaves_no_occurrence(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="sy", fontfile=SY)
    for i in range(6):
        p.insert_text((72, 100 + 20 * i), f"პირადი {i} 01001012345 და 01001012345-პირადი",
                      fontname="sy", fontsize=12)
    path = str(tmp_path / "w.pdf")
    d.save(path)
    e = engine(path)
    rects = [h[0] for h in e.search(0, "01001012345")]
    assert len(rects) == 12
    e.apply(("redact", {0: rects}, {"fill": (0, 0, 0)}))
    out = saved(e, tmp_path)
    text = "".join(c[4] for ln in engine(str(tmp_path / "out.pdf")).chars(0) for c in ln)
    assert "01001012345" not in text and text.count("პირადი") == 12 and out.page_count == 1
    assert not holds(out, "01001012345")


def test_redaction_never_shifts_the_rest_of_a_line(tmp_path):
    """MuPDF shifts it by one letter; whatever way is taken, the reader must not see that."""
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="sy", fontfile=SY)
    p.insert_text((72, 100), "დასაწყისი SECRETWORD შუა SECRETWORD ბოლო", fontname="sy", fontsize=16)
    path = str(tmp_path / "w.pdf")
    d.save(path)
    e = engine(path)
    look, hits = pic(e.doc[0]), e.search(0, "SECRETWORD")
    rects = [h[0] for h in hits]
    at = {w: e.search(0, w)[0][0] for w in ("დასაწყისი", "შუა", "ბოლო")}
    e.apply(("redact", {0: rects}, {"fill": (0, 0, 0)}))
    r = saved(e, tmp_path)
    assert not holds(r, "SECRETWORD")
    a, mask = pic(r[0]), np.ones_like(look, bool)
    for x0, y0, x1, y1 in rects:
        mask[int(y0) - 2:int(y1) + 3, int(x0) - 2:int(x1) + 3] = False
        assert a[int(y0) + 2:int(y1) - 2, int(x0) + 2:int(x1) - 2].max() < 60
    assert np.mean((abs(a - look) > 110) & mask) < .0015
    after = engine(str(tmp_path / "out.pdf"))
    for w, box in at.items():
        got = after.search(0, w)
        assert len(got) == 1 and abs(got[0][0][0] - box[0]) < 3 and abs(got[0][0][2] - box[2]) < 4, w


def test_word_at_the_end_of_a_line_keeps_the_page_as_text(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 100), "public SECRETWORD", fontsize=16)
    p.insert_text((72, 140), "second line", fontsize=16)
    path = str(tmp_path / "w.pdf")
    d.save(path)
    e = engine(path)
    info = e.apply(("redact", {0: [e.search(0, "SECRETWORD")[0][0]]}, {"fill": (0, 0, 0)}))
    assert info["notes"] == [] and not e.doc[0].get_images()
    assert saved(e, tmp_path)[0].get_text().split() == ["public", "second", "line"]


@pytest.mark.parametrize("kind", ["xlsx", "docx", "txt", "html", "pptx"])
def test_exports_leave_nothing_in_the_temporary_folder(tmp_path, monkeypatch, kind):
    from furceli import files
    made, real = [], files.new_session
    monkeypatch.setattr(files, "new_session", lambda: made.append(real()) or made[-1])
    d = pymupdf.open()
    p = d.new_page()
    for r in range(3):
        for c in range(3):
            p.draw_rect(pymupdf.Rect(50 + c * 100, 50 + r * 30, 150 + c * 100, 80 + r * 30))
            p.insert_text((60 + c * 100, 70 + r * 30), f"a{r}{c}")
    path = str(tmp_path / "t.pdf")
    d.save(path)
    convert.export_as(engine(path), kind, str(tmp_path / ("o." + kind)), [0])
    assert not [x for x in made if os.path.exists(x)]


def test_closing_while_the_engine_is_starting(qapp_client):
    for delay in (0, .01, .05, .15):
        c = qapp_client()
        c.submit(("task", 1), 0, "inspect", "nothing.pdf", "")
        time.sleep(delay)
        c.shutdown()
        assert c._proc is None or not c._proc.is_alive()


def test_cancelling_while_the_engine_is_starting(qapp_client):
    from PySide6.QtWidgets import QApplication
    for delay in (0, .01, .05, .15):
        c, got = qapp_client(), []
        c.failed.connect(lambda k, code, got=got: got.append(code))
        c.submit(("task", 1), 0, "_sleep", 30)
        time.sleep(delay)
        c.abort(("task", 1))
        end = time.time() + 10
        while not got and time.time() < end:
            QApplication.processEvents()
            time.sleep(.01)
        assert got == ["cancelled"], delay
        c.shutdown()


@pytest.fixture
def qapp_client():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from furceli.client import EngineClient
    app = QApplication.instance() or QApplication([])
    yield EngineClient
    app.processEvents()
