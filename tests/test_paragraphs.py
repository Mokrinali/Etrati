"""Paragraph editing, step 1: which lines make one paragraph (HANDOFF.md, item 7, stage 2).

A paragraph is lines of one direction, size and colour, under one another at an even
spacing, with one left edge (the first line may be indented). Columns, headings, table
cells and paragraphs set apart by a wider gap stay apart."""
import pymupdf
import pytest

from test_features import engine
from test_ui import app  # noqa: F401

FONT = r"C:\Windows\Fonts\sylfaen.ttf"


def make(tmp_path, lines, rot=0, name="p.pdf"):
    """lines: [(x, y, text, size, colour)] as shown; returns the file."""
    d = pymupdf.open()
    p = d.new_page(width=600 if rot in (0, 180) else 800, height=800 if rot in (0, 180) else 600)
    p.set_rotation(rot)
    m = p.derotation_matrix
    p.insert_font(fontname="S", fontfile=FONT)
    for x, y, text, size, colour in lines:
        p.insert_text(pymupdf.Point(x, y) * m, text, fontname="S", fontsize=size, color=colour,
                      rotate=rot)
    path = str(tmp_path / name)
    d.save(path)
    return path


def body(x, y, texts, size=12, leading=16, colour=(0, 0, 0)):
    return [(x, y + i * leading, t, size, colour) for i, t in enumerate(texts)]


FOUR = ["ერთი ორი სამი ოთხი ხუთი", "one two three four five", "ექვსი შვიდი რვა ცხრა",
        "six seven eight nine ten"]


def paras(path):
    return engine(path).paragraphs(0)


def test_lines_under_one_another_are_one_paragraph(tmp_path):
    (p,) = paras(make(tmp_path, body(72, 300, FOUR)))
    assert p["text"] == " ".join(FOUR)
    assert len(p["lines"]) == 4 and p["texts"] == FOUR
    assert p["size"] == pytest.approx(12, abs=.1) and p["leading"] == pytest.approx(16, abs=.3)
    x0, y0, x1, y1 = p["rect"]
    assert x0 == pytest.approx(72, abs=1) and 280 < y0 < 300 and 340 < y1 < 360


def test_a_wider_gap_starts_a_new_paragraph(tmp_path):
    lines = body(72, 300, FOUR[:2]) + body(72, 300 + 16 + 32, FOUR[2:])
    assert [p["texts"] for p in paras(make(tmp_path, lines))] == [FOUR[:2], FOUR[2:]]


def test_a_heading_is_not_part_of_the_paragraph(tmp_path):
    lines = [(72, 270, "სათაური Heading", 18, (0, 0, 0))] + body(72, 300, FOUR)
    got = paras(make(tmp_path, lines))
    assert [p["texts"] for p in got] == [["სათაური Heading"], FOUR]


def test_another_colour_is_another_paragraph(tmp_path):
    lines = body(72, 300, FOUR[:2]) + body(72, 332, FOUR[2:], colour=(.8, 0, 0))
    assert [p["texts"] for p in paras(make(tmp_path, lines))] == [FOUR[:2], FOUR[2:]]


def test_columns_stay_apart(tmp_path):
    left, right = ["ა ბ გ დ", "ე ვ ზ თ", "ი კ ლ მ"], ["one two", "three four", "five six"]
    lines = body(72, 300, left) + body(330, 300, right)
    got = sorted(p["texts"] for p in paras(make(tmp_path, lines)))
    assert got == sorted([left, right])


def test_table_cells_stay_apart(tmp_path):
    cells = [(72, 300, "ცხრილი", 12, (0, 0, 0)), (250, 300, "უჯრა", 12, (0, 0, 0)),
             (72, 316, "მეორე", 12, (0, 0, 0)), (250, 316, "რიგი", 12, (0, 0, 0))]
    got = paras(make(tmp_path, cells))
    assert all(len(p["lines"]) <= 2 for p in got)
    assert not any("ცხრილი" in p["text"] and "უჯრა" in p["text"] for p in got)


def test_the_first_line_may_be_indented(tmp_path):
    lines = [(96, 300, FOUR[0], 12, (0, 0, 0))] + body(72, 316, FOUR[1:])
    (p,) = paras(make(tmp_path, lines))
    assert p["indent"] == pytest.approx(24, abs=1) and len(p["lines"]) == 4


def test_a_word_cut_at_the_end_of_a_line_is_joined(tmp_path):
    (p,) = paras(make(tmp_path, body(72, 300, ["the exam-", "ple works", "სიტყ-", "ვები აქ"])))
    assert p["text"] == "the example works სიტყვები აქ"


def test_turned_pages_are_read_as_shown(tmp_path):
    (p,) = paras(make(tmp_path, body(72, 300, FOUR), rot=90))
    x0, y0, x1, y1 = p["rect"]
    assert x0 == pytest.approx(72, abs=1) and 280 < y0 < 300 and len(p["lines"]) == 4


def html_page(tmp_path, html, rects=((72, 72, 330, 400),)):
    d = pymupdf.open()
    p = d.new_page()
    for r in rects:
        p.insert_htmlbox(pymupdf.Rect(r), html, css="*{font-family:sans-serif;font-size:11px}")
    path = str(tmp_path / "html.pdf")
    d.save(path)
    return path


JUSTIFIED = ("<p style='text-align:justify'>საქართველოს კანონი ადგენს წესს, რომლის მიხედვითაც "
             "ყველა მოქალაქე ვალდებულია დაიცვას საზოგადოებრივი წესრიგი და პატივი სცეს სხვათა "
             "უფლებებს. This sentence is in English and continues for a while so that the "
             "paragraph wraps over several lines.</p><p>მეორე აბზაცი უფრო მოკლეა, მაგრამ მაინც "
             "ორ სტრიქონზე გადადის, რადგან ჩარჩო ვიწროა.</p>")


def test_a_justified_line_is_one_line(tmp_path):
    """MuPDF splits a justified line where the gaps between words are wide."""
    got = paras(html_page(tmp_path, JUSTIFIED))
    assert len(got) == 2
    assert got[0]["text"].startswith("საქართველოს კანონი") and got[0]["text"].endswith("several lines.")
    assert "დაიცვას საზოგადოებრივი წესრიგი და" in got[0]["text"]
    assert got[1]["text"].startswith("მეორე აბზაცი") and got[1]["text"].endswith("ვიწროა.")


def test_wrapped_columns_are_whole_and_apart(tmp_path):
    html = "<p>Column text that wraps around inside its own column for several lines here.</p>"
    got = paras(html_page(tmp_path, html, ((72, 450, 280, 600), (320, 450, 528, 600))))
    assert [p["text"] for p in got] == [
        "Column text that wraps around inside its own column for several lines here."] * 2


def test_the_window_can_ask_the_engine(app, tmp_path):  # noqa: F811
    from furceli import client
    c = client.EngineClient()
    try:
        client.run_task(c, "open", (make(tmp_path, body(72, 300, FOUR)), ""))
        (p,) = client.run_task(c, "paragraphs", (0,))
        assert p["text"] == " ".join(FOUR)
    finally:
        c.shutdown()


def test_lines_are_numbered_as_the_text_tools_number_them(tmp_path):
    lines = [(72, 270, "სათაური Heading", 18, (0, 0, 0))] + body(72, 300, FOUR)
    e = engine(make(tmp_path, lines))
    by_index = {ln["index"]: ln["text"] for ln in e.lines(0)}
    for p in e.paragraphs(0):
        assert [by_index[n] for n in p["lines"]] == p["texts"]
