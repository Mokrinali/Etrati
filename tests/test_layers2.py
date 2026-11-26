"""Hidden layers: what a third review of layers.py found."""
import pymupdf

from furceli import layers

HELV = "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"
COUR = "<</Type/Font/Subtype/Type1/BaseFont/Courier>>"
TIMES = "<</Type/Font/Subtype/Type1/BaseFont/Times-Roman>>"


def make(content, fonts=f"/F1 {HELV}", more=None):
    """One page whose layer "Secret" is switched off."""
    doc = pymupdf.open()
    page = doc.new_page()
    ocg = doc.get_new_xref()
    doc.update_object(ocg, "<</Type/OCG/Name(Secret)>>")
    doc.xref_set_key(doc.pdf_catalog(), "OCProperties",
                     f"<</OCGs[{ocg} 0 R]/D<</Order[{ocg} 0 R]/OFF[{ocg} 0 R]>>>>")
    c = doc.get_new_xref()
    doc.update_object(c, "<<>>")
    doc.update_stream(c, content)
    doc.xref_set_key(page.xref, "Contents", f"{c} 0 R")
    doc.xref_set_key(page.xref, "Resources", f"<</Font<<{fonts}>>/Properties<</MC0 {ocg} 0 R>>>>")
    if more:
        more(doc, page, ocg)
    return doc


def run(doc):
    assert layers.remove_hidden(doc, set())
    return pymupdf.open("pdf", doc.tobytes())


def spans(page):
    return [(s["font"], s["text"]) for b in page.get_text("dict")["blocks"]
            for ln in b.get("lines", ()) for s in ln["spans"]]


def blue(page):
    s = page.get_pixmap(dpi=50).samples
    return sum(s[k:k + 3] == b"\x00\x00\xff" for k in range(0, len(s), 3))


def annot(doc, page, ap):
    an = doc.get_new_xref()
    doc.update_object(an, f"<</Type/Annot/Subtype/Square/Rect[300 300 400 400]/AP<</N {ap} 0 R>>/F 4>>")
    doc.xref_set_key(page.xref, "Annots", f"[{an} 0 R]")


def test_look_without_subtype_keeps_a_shared_font():
    def more(doc, page, ocg):
        fd = doc.get_new_xref()
        doc.update_object(fd, f"<</F1 {HELV}/F2 {COUR}/F3 {TIMES}>>")
        doc.xref_set_key(page.xref, "Resources/Font", f"{fd} 0 R")
        ap = doc.get_new_xref()
        doc.update_object(ap, f"<</BBox[0 0 200 50]/Resources<</Font {fd} 0 R>>>>")
        doc.update_stream(ap, b"BT /F3 20 Tf 5 10 Td (ANNOT) Tj ET")
        annot(doc, page, ap)
    doc = make(b"/OC /MC0 BDC BT /F2 24 Tf 50 100 Td (SECRET) Tj ET EMC "
               b"BT /F1 24 Tf 50 300 Td (PAGE) Tj ET", more=more)
    before = spans(doc[0])
    got = run(doc)
    assert ("Times-Roman", "ANNOT") in before
    assert ("Times-Roman", "ANNOT") in spans(got[0]) and "SECRET" not in got[0].get_text()


def test_look_without_subtype_loses_its_hidden_part():
    def more(doc, page, ocg):
        ap = doc.get_new_xref()
        doc.update_object(ap, f"<</BBox[0 0 100 100]/Resources<</Properties<</MC0 {ocg} 0 R>>>>>>")
        doc.update_stream(ap, b"/OC /MC0 BDC 0 0 1 rg 0 0 100 100 re f EMC")
        annot(doc, page, ap)
    doc = make(b"/OC /MC0 BDC BT /F1 12 Tf 50 50 Td (x) Tj ET EMC", more=more)
    assert blue(doc[0]) == 0
    assert blue(run(doc)[0]) == 0


def test_drawing_shared_with_a_look_without_subtype_stays():
    def more(doc, page, ocg):
        fx = doc.get_new_xref()
        doc.update_object(fx, "<</Type/XObject/Subtype/Form/BBox[0 0 100 100]>>")
        doc.update_stream(fx, b"0 0 1 rg 0 0 100 100 re f")
        doc.xref_set_key(page.xref, "Resources/XObject", f"<</Fx {fx} 0 R>>")
        ap = doc.get_new_xref()
        doc.update_object(ap, f"<</BBox[0 0 100 100]/Resources<</XObject<</Fx {fx} 0 R>>>>>>")
        doc.update_stream(ap, b"/Fx Do")
        annot(doc, page, ap)
    doc = make(b"/OC /MC0 BDC /Fx Do EMC", more=more)
    shown = blue(doc[0])
    assert shown > 1000 and blue(run(doc)[0]) == shown


def test_soft_mask_of_a_visible_picture_stays():
    def more(doc, page, ocg):
        m = doc.get_new_xref()
        doc.update_object(m, "<</Type/XObject/Subtype/Image/Width 2/Height 1/ColorSpace/DeviceGray"
                             "/BitsPerComponent 8>>")
        doc.update_stream(m, bytes([0, 255]))
        i = doc.get_new_xref()
        doc.update_object(i, f"<</Type/XObject/Subtype/Image/Width 2/Height 1/ColorSpace/DeviceRGB"
                             f"/BitsPerComponent 8/SMask {m} 0 R>>")
        doc.update_stream(i, bytes([255, 0, 0, 0, 0, 255]))
        doc.xref_set_key(page.xref, "Resources/XObject", f"<</Im1 {m} 0 R/Im2 {i} 0 R>>")
    doc = make(b"/OC /MC0 BDC q 100 0 0 100 50 50 cm /Im1 Do Q EMC "
               b"q 200 0 0 200 200 300 cm /Im2 Do Q", more=more)
    want = pymupdf.open("pdf", doc.tobytes())
    want.xref_set_key(want[0].xref, "Contents", "null")  # what the page shows: only Im2
    c = want.get_new_xref()
    want.update_object(c, "<<>>")
    want.update_stream(c, b"q 200 0 0 200 200 300 cm /Im2 Do Q")
    want.xref_set_key(want[0].xref, "Contents", f"{c} 0 R")
    assert run(doc)[0].get_pixmap(dpi=50).samples == want[0].get_pixmap(dpi=50).samples


def test_font_found_through_the_page_stays():
    def more(doc, page, ocg):
        fx = doc.get_new_xref()
        doc.update_object(fx, "<</Type/XObject/Subtype/Form/BBox[0 0 300 100]"
                              "/Resources<</ProcSet[/PDF/Text]>>>>")
        doc.update_stream(fx, b"BT /F2 24 Tf 0 10 Td (INFORM) Tj ET")
        doc.xref_set_key(page.xref, "Resources/XObject", f"<</Fx {fx} 0 R>>")
    doc = make(b"/OC /MC0 BDC 0 0 1 rg 0 0 50 50 re f EMC q 1 0 0 1 50 400 cm /Fx Do Q",
               fonts=f"/F1 {HELV}/F2 {COUR}", more=more)
    assert ("Courier", "INFORM") in spans(doc[0])
    assert ("Courier", "INFORM") in spans(run(doc)[0])


def test_form_field_font_stays():
    def more(doc, page, ocg):
        fd = doc.get_new_xref()
        doc.update_object(fd, f"<</F1 {HELV}/F2 {COUR}/Cour {COUR}>>")
        doc.xref_set_key(page.xref, "Resources/Font", f"{fd} 0 R")
        w = doc.get_new_xref()
        doc.update_object(w, f"<</Type/Annot/Subtype/Widget/FT/Tx/T(name)/Rect[50 600 250 630]"
                             f"/DA(/Cour 12 Tf 0 g)/P {page.xref} 0 R/F 4>>")
        doc.xref_set_key(page.xref, "Annots", f"[{w} 0 R]")
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"<</Fields[{w} 0 R]/DR<</Font {fd} 0 R>>>>")
    doc = make(b"/OC /MC0 BDC BT /F2 24 Tf 50 100 Td (SECRET) Tj ET EMC "
               b"BT /F1 24 Tf 50 300 Td (PAGE) Tj ET", more=more)
    got = run(doc)
    fonts = got.xref_get_key(got.pdf_catalog(), "AcroForm/DR/Font")
    assert "SECRET" not in got[0].get_text()
    assert "Cour" in got.xref_object(int(fonts[1].split()[0]))


def test_hidden_part_inside_a_type3_letter_goes():
    def more(doc, page, ocg):
        proc = doc.get_new_xref()
        doc.update_object(proc, "<<>>")
        doc.update_stream(proc, b"1000 0 d0 1 0 0 rg 0 0 1000 1000 re f "
                                b"/OC /MC0 BDC 0 0 1 rg 0 0 1000 1000 re f EMC")
        f = doc.get_new_xref()
        doc.update_object(f, f"<</Type/Font/Subtype/Type3/FontBBox[0 0 1000 1000]"
                             f"/FontMatrix[0.001 0 0 0.001 0 0]/FirstChar 65/LastChar 65/Widths[1000]"
                             f"/Encoding<</Differences[65/a]>>/CharProcs<</a {proc} 0 R>>"
                             f"/Resources<</Properties<</MC0 {ocg} 0 R>>>>>>")
        doc.xref_set_key(page.xref, "Resources/Font/T3", f"{f} 0 R")
    doc = make(b"BT /T3 100 Tf 300 300 Td (A) Tj ET", more=more)
    assert blue(doc[0]) == 0
    assert blue(run(doc)[0]) == 0


def test_hidden_part_inside_a_plain_form_goes():
    def more(doc, page, ocg):
        fx = doc.get_new_xref()
        doc.update_object(fx, f"<</Type/XObject/Subtype/Form/BBox[0 0 200 200]"
                              f"/Resources<</Properties<</MC0 {ocg} 0 R>>>>>>")
        doc.update_stream(fx, b"/OC /MC0 BDC 0 0 1 rg 0 0 200 200 re f EMC")
        doc.xref_set_key(page.xref, "Resources/XObject", f"<</Fx {fx} 0 R>>")
    doc = make(b"q 1 0 0 1 100 100 cm /Fx Do Q /OC /MC0 BDC BT /F1 9 Tf 9 9 Td (x) Tj ET EMC",
               more=more)
    assert blue(doc[0]) == 0
    assert blue(run(doc)[0]) == 0
