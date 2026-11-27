"""Removing text keeps the outline width of the rest (Word's made-up bold: fill + thin outline).

MuPDF's redaction filter drops every `w` that only text outlines use, so after any removal the
outline is 1 point wide and bold text looks four times heavier. strokes.keep puts it back."""
import pymupdf

from furceli import strokes

WORD = (b"BT /helv 9 Tf 1 0 0 1 72 700 Tm 2 Tr 0.25714 w 0 g 0 G (First line) Tj ET\n"
        b"BT /helv 9 Tf 1 0 0 1 72 680 Tm 2 Tr 0.25714 w 0 g 0 G (Second line) Tj ET\n"
        b"BT /helv 12 Tf 1 0 0 1 72 660 Tm 2 Tr 0.34286 w (Bigger) Tj 0 Tr (plain) Tj ET\n"
        b"q 2 w 72 600 m 300 600 l S Q\n"
        b"BT /helv 9 Tf 1 0 0 1 72 640 Tm 0 Tr (Not bold) Tj ET\n")


def _page():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="helv")
    page.draw_line((0, 0), (1, 0), width=0)  # a content stream to write into
    page = doc[0]
    doc.update_stream(page.get_contents()[0], WORD)
    return doc


def _widths(page):
    out = {}
    for t in page.get_texttrace():
        if t["type"] == 1:
            text = "".join(chr(c[0]) for c in t["chars"])
            out[text] = round(t["linewidth"], 3)
    return out


def _cut_first(doc):
    page = doc[0]
    page.add_redact_annot(pymupdf.Rect(70, 137, 200, 141), fill=False)
    page.apply_redactions(images=0, graphics=0, text=0)


def test_mupdf_still_drops_text_outline_widths():
    """If this fails MuPDF was fixed, and strokes.keep may go."""
    doc = _page()
    plan = strokes.plan(doc[0])
    _cut_first(doc)
    assert _widths(doc[0])["Second line"] == 1.0
    assert plan


def test_keep_puts_the_widths_back():
    doc = _page()
    before = _widths(doc[0])
    assert before["Second line"] == 0.257 and before["Bigger"] == 0.343
    plan = strokes.plan(doc[0])
    _cut_first(doc)
    strokes.keep(doc[0], plan)
    after = _widths(doc[0])
    assert "First line" not in after
    assert after["Second line"] == 0.257 and after["Bigger"] == 0.343
    # line art and plain text are as they were
    assert [round(d["width"], 2) for d in doc[0].get_drawings()] == [2.0]
    assert "Not bold" in doc[0].get_text() and "plain" in doc[0].get_text()


def test_nothing_to_keep_without_outlined_text():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "hello")
    assert strokes.plan(page) == {}
    before = page.read_contents()
    strokes.keep(page, {})
    assert page.read_contents() == before


def test_keep_never_glues_onto_the_operator_before():
    """MuPDF writes "0 G[( )]TJ": the width must not become "0 G0.25 w"."""
    doc = _page()
    page = doc[0]
    doc.update_stream(page.get_contents()[0],
                      b"BT/helv 9 Tf 2 Tr 72 700 TD 0 G[(Tight)]TJ ET")
    strokes.keep(doc[0], {(b"/helv", 9.0): 0.25714})
    assert b"0 G 0.25714 w [(Tight)]TJ" in doc[0].read_contents()
    assert _widths(doc[0])["Tight"] == 0.257


# -- replacing text in a line Word made bold by outlining ------------------------------------
def _word_file(tmp_path):
    """Like Word: one face, the bold half drawn filled and outlined (a 35th of the size)."""
    d = pymupdf.open()
    p = d.new_page()
    p.insert_font(fontname="S", fontfile=r"C:\Windows\Fonts\sylfaen.ttf")
    p.draw_line((0, 0), (1, 0), width=0)
    p = d[0]
    font = pymupdf.Font(fontfile=r"C:\Windows\Fonts\sylfaen.ttf")
    hexed = lambda t: "".join("%04x" % font.has_glyph(ord(c)) for c in t)  # noqa: E731
    d.update_stream(p.get_contents()[0], (
        "BT /S 9 Tf 1 0 0 1 72 700 Tm 0 Tr 0 g <%s> Tj 2 Tr 0.25714 w 0 G <%s> Tj ET\n"
        "BT /S 9 Tf 1 0 0 1 72 686 Tm 2 Tr 0.25714 w 0 g 0 G <%s> Tj ET\n"
        % (hexed("ღირებულება "), hexed("შეადგინა 6230"), hexed("ათას ორას ლარი"))).encode())
    path = str(tmp_path / "word.pdf")
    d.save(path)
    return path


def _stroked_text(page, y):
    """The letters drawn outlined on the line near y, and their widths."""
    import collections
    got, widths = [], collections.Counter()
    for t in page.get_texttrace():
        if t["type"] == 1 and abs(t["bbox"][3] - y) < 6:
            got += [c[0] for c in t["chars"]]
            widths[round(t["linewidth"], 3)] += 1
    return "".join(chr(c) for c in got if chr(c) != " "), widths


def test_a_word_bold_line_is_bold_and_keeps_each_letters_weight(tmp_path):
    from test_features import engine
    e = engine(_word_file(tmp_path))
    first, second = e.lines(0)[:2]
    assert second["bold"]  # all of it outlined
    e.apply(("replace", 0, first["index"], first["text"], first["text"].replace("6230", "6231"),
             {q: first[q] for q in ("size", "color", "bold", "italic", "serif", "mono")}))
    page = e.doc[0]
    y = 842 - 700
    letters, widths = _stroked_text(page, y)
    assert letters == "შეადგინა6231"  # the bold half stays bold, the rest stays regular
    assert set(widths) == {0.257}
    # the other line keeps its thin outline (MuPDF would make it 1 point wide)
    assert set(_stroked_text(page, 842 - 686)[1]) == {0.257}
