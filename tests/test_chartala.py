"""ჩართალა: visual comparison of two drawings in the engine, and the list of changes."""
import hashlib
import os

import pymupdf
import pytest
from pymupdf import Point, Rect
from test_features import SY, blank, engine, saved

from furceli.features import Features

from furceli.compare_view import SENSITIVITY, merge_changes

BASE = [("rect", (100, 100, 300, 200)), ("line", (400, 100, 700, 400)),
        ("rect", (60, 470, 260, 540))]


def drawing(path, shapes=(), texts=(), size=(842, 595), frame=True):
    """A small landscape 'drawing': a frame, a few shapes and words."""
    d = pymupdf.open()
    p = d.new_page(width=size[0], height=size[1])
    if frame:
        p.draw_rect(Rect(20, 20, size[0] - 20, size[1] - 20), color=(0, 0, 0), width=1.5)
    for kind, a in shapes:
        if kind == "rect":
            p.draw_rect(Rect(a), color=(0, 0, 0), width=1)
        elif kind == "line":
            p.draw_line(Point(a[:2]), Point(a[2:]), color=(0, 0, 0), width=1)
        elif kind == "disc":
            p.draw_circle(Point(a[:2]), a[2], color=(0, 0, 0), fill=(0, 0, 0))
    if texts:
        p.insert_font(fontname="sy", fontfile=SY)
        for at, t in texts:
            p.insert_text(at, t, fontname="sy", fontsize=14)
    d.save(str(path))
    d.close()
    return str(path)


def near(r, want, tol=4):
    return r is not None and all(abs(a - b) <= tol for a, b in zip(r, want))


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest(), os.stat(path).st_mtime_ns


def test_removed_is_red_on_the_old_and_added_green_on_the_new(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE + [("rect", (500, 450, 600, 520))])
    new = drawing(tmp_path / "new.pdf", BASE + [("disc", (150, 350, 40))])
    r = engine(new).visual_diff(old, "", 0, 0, {})
    removed = [g for g in r["regions"] if g["old"] and not g["new"]]
    added = [g for g in r["regions"] if g["new"] and not g["old"]]
    assert len(r["regions"]) == 2
    assert len(removed) == 1 and near(removed[0]["old"], (500, 450, 600, 520))
    assert len(added) == 1 and near(added[0]["new"], (110, 310, 190, 390))


def test_a_changed_shape_is_marked_on_both_sides(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE)
    new = drawing(tmp_path / "new.pdf", [("rect", (100, 100, 360, 200))] + BASE[1:])
    r = engine(new).visual_diff(old, "", 0, 0, {})
    assert r["regions"], r
    both = [g for g in r["regions"] if g["old"] and g["new"]]
    assert both and all(300 - 6 < g["new"][2] for g in both)


def test_identical_pages_have_no_regions(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE, [((80, 300), "ფასი 100 ლარი")])
    new = drawing(tmp_path / "new.pdf", BASE, [((80, 300), "ფასი 100 ლარი")])
    r = engine(new).visual_diff(old, "", 0, 0, {})
    assert r["regions"] == [] and r["dpi"] > 0


def test_pages_with_text_changes_are_still_checked_as_pictures(tmp_path):
    """The text comparison used to skip every page whose words changed."""
    old = drawing(tmp_path / "old.pdf", BASE, [((80, 300), "ფასი 100 ლარი")])
    new = drawing(tmp_path / "new.pdf", BASE + [("disc", (650, 480, 30))],
                  [((80, 300), "ფასი 250 ლარი")])
    r = engine(new).compare(old)
    assert r["changes"] and r["pictures"] == [0]


def test_comparing_changes_neither_file(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE)
    new = drawing(tmp_path / "new.pdf", BASE + [("disc", (150, 350, 40))])
    before = digest(old), digest(new)
    e = engine(new)
    e.visual_diff(old, "", 0, 0, {})
    e.compare(old)
    e.close()
    assert (digest(old), digest(new)) == before


def test_text_and_pixel_change_at_one_place_is_one_entry():
    text = [{"kind": "replace", "old": "100", "new": "250", "page": 0,
             "old_at": [(0, (100, 100, 130, 114))], "new_at": [(0, (100, 100, 130, 114))]}]
    regions = {(0, 0): [{"old": (101, 102, 128, 112), "new": (101, 102, 129, 112)},
                        {"old": None, "new": (400, 400, 450, 450)}]}
    entries = merge_changes(text, regions, [(0, 0)])
    assert len(entries) == 2
    first = [e for e in entries if e["text"]][0]
    assert "100" in first["text"] and "250" in first["text"]
    assert first["new"] == [(101, 102, 129, 112)] and first["old"] == [(101, 102, 128, 112)]
    other = [e for e in entries if not e["text"]][0]
    assert other["kind"] == "added" and other["old"] == [] and other["pair"] == (0, 0)


def test_a_text_change_without_a_picture_keeps_its_words():
    text = [{"kind": "delete", "old": "სტრიქონი", "new": "", "page": 0,
             "old_at": [(1, (50, 60, 90, 74)), (1, (92, 60, 120, 74))], "new_at": []}]
    entries = merge_changes(text, {(1, 1): []}, [(0, 0), (1, 1)])
    assert len(entries) == 1
    e = entries[0]
    assert e["pair"] == (1, 1) and e["old"] == [(50, 60, 120, 74)] and e["new"] == []


def test_engine_text_and_pixels_of_one_word_are_listed_once(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE, [((80, 300), "ფასი 100 ლარი")])
    new = drawing(tmp_path / "new.pdf", BASE, [((80, 300), "ფასი 250 ლარი")])
    e = engine(new)
    regions = {(0, 0): e.visual_diff(old, "", 0, 0, {})["regions"]}
    entries = merge_changes(e.compare(old)["changes"], regions, [(0, 0)])
    assert len(entries) == 1 and "250" in entries[0]["text"]


# -- step 3: alignment, sensitivity ------------------------------------------------
SHIFT = (1.02, -8, -5)  # the returned drawing: 2 % bigger and moved; inside the sheet still


def moved(path, src, shift=SHIFT, shapes=()):
    """The page of src as somebody's program put it back: scaled and moved, plus shapes."""
    s, dx, dy = shift
    old = pymupdf.open(src)
    d = pymupdf.open()
    w, h = old[0].rect.width, old[0].rect.height
    p = d.new_page(width=w, height=h)
    p.show_pdf_page(Rect(dx, dy, dx + w * s, dy + h * s), old, 0)
    for kind, a in shapes:
        if kind == "disc":
            p.draw_circle(Point(a[:2]), a[2], color=(0, 0, 0), fill=(0, 0, 0))
        elif kind == "box":
            p.draw_rect(Rect(a), color=(0, 0, 0), fill=(0, 0, 0))
    d.save(str(path))
    return str(path)


def only(regions, want):
    """Every region is one of the wanted places (new side), and each wanted place is found."""
    news = [g["new"] for g in regions]
    return (len(regions) == len(want)
            and all(any(near(n, w, 6) for n in news) for w in want))


def unrelated(path):
    return drawing(path, [("line", (60 + 37 * i, 40, 840 - 23 * i, 560)) for i in range(12)],
                   frame=False)


def test_identical_pages_are_not_aligned_at_all(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE)
    new = drawing(tmp_path / "new.pdf", BASE + [("disc", (150, 350, 40))])
    r = engine(new).visual_diff(old, "", 0, 0, {})
    assert r["aligned"] == "none" and r["ok"] and r["matrix"] == [[1, 0, 0], [0, 1, 0]]


def test_a_moved_and_scaled_drawing_is_aligned(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE, [((80, 300), "ფასი 100 ლარი")])
    new = moved(tmp_path / "new.pdf", old, shapes=[("disc", (400, 470, 30))])
    e = engine(new)
    plain = e.visual_diff(old, "", 0, 0, {"auto": False})
    assert len(plain["regions"]) > 3           # without alignment everything looks changed
    r = e.visual_diff(old, "", 0, 0, {})
    assert r["aligned"] == "auto" and r["ok"]
    (a, b, c), (d, f, g) = r["matrix"]
    assert abs(a - 1.02) < .006 and abs(f - 1.02) < .006 and abs(b) < .006 and abs(d) < .006
    assert abs(c + 8) < 1.5 and abs(g + 5) < 1.5
    assert only(r["regions"], [(370, 440, 430, 500)]), r["regions"]


def test_a_big_object_added_at_the_edge_does_not_spoil_alignment(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE, [((80, 300), "ფასი 100 ლარი")], frame=False)
    new = moved(tmp_path / "new.pdf", old, shapes=[("box", (720, 20, 835, 580))])
    r = engine(new).visual_diff(old, "", 0, 0, {})
    assert r["aligned"] == "auto" and r["ok"]
    assert abs(r["matrix"][0][0] - 1.02) < .006 and abs(r["matrix"][0][2] + 8) < 1.5
    assert only(r["regions"], [(720, 20, 835, 580)]), r["regions"]


def test_unrelated_drawings_are_not_aligned(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE)
    new = unrelated(tmp_path / "new.pdf")
    r = engine(new).visual_diff(old, "", 0, 0, {})
    assert not r["ok"] and r["matrix"] == [[1, 0, 0], [0, 1, 0]]


def test_two_points_on_each_drawing_align_by_hand(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE, [((80, 300), "ფასი 100 ლარი")])
    new = moved(tmp_path / "new.pdf", old, shapes=[("disc", (400, 470, 30))])
    s, dx, dy = SHIFT
    pts = [[(100, 100), (100 * s + dx, 100 * s + dy)], [(700, 400), (700 * s + dx, 400 * s + dy)]]
    r = engine(new).visual_diff(old, "", 0, 0, {"auto": False, "points": pts})
    assert r["aligned"] == "manual" and r["ok"]
    assert only(r["regions"], [(370, 440, 430, 500)]), r["regions"]


def hairlines(path):
    """Thin grey CAD lines: drawn, they come out grey near the line between ink and paper."""
    d = pymupdf.open()
    p = d.new_page(width=842, height=595)
    for i in range(40):
        p.draw_line(Point(60, 60 + 12.3 * i), Point(780, 70 + 12.3 * i), color=(0, 0, 0),
                    width=.3)
        p.draw_line(Point(60 + 17.7 * i, 60), Point(70 + 17.7 * i, 540), color=(.5,) * 3,
                    width=.3)
    p.draw_rect(Rect(40, 40, 802, 555), color=(0, 0, 0), width=2)
    d.save(str(path))
    return str(path)


def test_thin_lines_drawn_a_shade_lighter_are_no_change(tmp_path):
    """Put back by another program, a hairline falls between pixels and comes out a shade
    lighter: it was reported removed (seen on real drawings, 10 to 126 places a page)."""
    old = hairlines(tmp_path / "old.pdf")
    new = moved(tmp_path / "new.pdf", old, shift=(1.006, -6, 4))
    r = engine(new).visual_diff(old, "", 0, 0, {})
    assert r["aligned"] == "auto" and r["regions"] == [], r["regions"]


def test_dark_drawing_over_light_grey_is_found(tmp_path):
    """New lines over an old light grey fill (a hatch, a shaded room) are a change: a rule that
    lets a line fade must not hide them (seen: "REV B" lost its B on a real drawing)."""
    def page(path, extra):
        d = pymupdf.open()
        p = d.new_page(width=842, height=595)
        p.draw_rect(Rect(100, 100, 500, 400), color=None, fill=(.9, .9, .9))
        p.draw_rect(Rect(40, 40, 802, 555), color=(0, 0, 0), width=2)
        if extra:
            p.draw_rect(Rect(250, 200, 330, 260), color=(0, 0, 0), width=2)
        d.save(str(path))
        return str(path)
    old, new = page(tmp_path / "old.pdf", False), page(tmp_path / "new.pdf", True)
    r = engine(new).visual_diff(old, "", 0, 0, {})
    assert only(r["regions"], [(249, 199, 331, 261)]), r["regions"]


def photo(path):
    """A whole page that is one picture (renders and photos go into drawing sets)."""
    import cv2
    import numpy as np
    rng = np.random.default_rng(7)
    img = cv2.GaussianBlur(rng.integers(0, 256, (600, 850), dtype=np.uint8), (0, 0), 6)
    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    for i in range(12):
        cv2.rectangle(img, (40 + 60 * i, 100), (70 + 60 * i, 400 + 10 * i), 0, 3)
    d = pymupdf.open()
    p = d.new_page(width=842, height=595)
    p.insert_image(p.rect, stream=cv2.imencode(".png", img)[1].tobytes())
    d.save(str(path))
    return str(path)


def test_a_page_that_is_a_picture_and_moved_is_still_aligned(tmp_path):
    """A picture has ink everywhere: moved, it still seemed to lie on itself, so it was not
    aligned and all of it was reported changed."""
    old = photo(tmp_path / "old.pdf")
    new = moved(tmp_path / "new.pdf", old, shift=(1.006, -6, 4))
    r = engine(new).visual_diff(old, "", 0, 0, {})
    assert r["aligned"] == "auto" and r["ok"]
    assert len(r["regions"]) <= 2, r["regions"]


def test_scan_noise_is_hidden_by_low_sensitivity(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE)
    dots = [("disc", (60 + 71 * i, 60 + (i * 37) % 480, 1.0)) for i in range(11)]
    new = drawing(tmp_path / "new.pdf", BASE + dots)
    e = engine(new)
    assert e.visual_diff(old, "", 0, 0, SENSITIVITY[1])["regions"] == []
    assert len(e.visual_diff(old, "", 0, 0, SENSITIVITY[5])["regions"]) >= 8


# -- step 5: revision cloud and arrow, as standard PDF comments ------------------------
META = {"author": "არქიტექტორი", "date": "D:20261127120000", "id": "chartala1"}


@pytest.mark.parametrize("rot", [0, 90])
def test_a_cloud_is_a_standard_polygon_comment(tmp_path, rot):
    """Acrobat and Bluebeam show a revision cloud as a Polygon with a cloudy border."""
    e = engine(blank(tmp_path, rot))
    e.apply(("annot", 0, "cloud", {"rect": (100, 100, 300, 200), "color": (1, 0, 0),
                                   "width": 1.5}, META))
    d = saved(e, tmp_path)
    page = d[0]
    a = [x for x in page.annots()][-1]
    assert a.type[1] == "Polygon" and a.colors["stroke"] == [1.0, 0.0, 0.0]
    obj = d.xref_object(a.xref, compressed=True)
    assert "/BE<</S/C/I 1>>" in obj and "/IT/PolygonCloud" in obj and "/Vertices" in obj
    ap = d.xref_get_key(a.xref, "AP/N")
    assert ap[0] == "xref" and d.xref_stream(int(ap[1].split()[0])).count(b" c\n") > 20
    assert a.info["title"] == "არქიტექტორი"
    # where it shows, on the page as it is displayed (rotated or not)
    shown = engine(str(tmp_path / "out.pdf")).annots(0)[-1]
    x0, y0, x1, y1 = shown["rect"]
    assert shown["type"] == "Polygon"
    assert 85 < x0 <= 100 and 85 < y0 <= 100 and 300 <= x1 < 315 and 200 <= y1 < 215


def test_a_cloud_comes_back_after_undo_and_a_crash(tmp_path):
    path = blank(tmp_path)
    op = ("annot", 0, "cloud", {"rect": (100, 100, 300, 200), "color": (0, 0, 1),
                                "width": 1}, META)
    e = Features()
    e.replay(path, "", [op])                  # how undo and crash recovery rebuild it
    assert [a["type"] for a in e.annots(0)] == ["Polygon"]
    e.replay(path, "", [])
    assert e.annots(0) == []


def test_an_arrow_is_a_standard_line_comment(tmp_path):
    e = engine(blank(tmp_path))
    e.apply(("annot", 0, "arrow", {"from": (100, 100), "to": (300, 250), "color": (0, 0, 1),
                                   "width": 2}, META))
    d = saved(e, tmp_path)
    page = d[0]
    a = [x for x in page.annots()][-1]
    assert a.type[1] == "Line" and a.line_ends == (0, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
    obj = d.xref_object(a.xref, compressed=True)
    assert "/LE[/None/OpenArrow]" in obj and "/L[" in obj
    ap = d.xref_get_key(a.xref, "AP/N")
    assert ap[0] == "xref" and len(d.xref_stream(int(ap[1].split()[0]))) > 20


# -- after the independent review ------------------------------------------------------
def big_sheet(path, size=(2384, 1684)):
    """An A1-sized drawing: a grid, rooms and words, more pixels than alignment looks at."""
    d = pymupdf.open()
    p = d.new_page(width=size[0], height=size[1])
    for i in range(40):
        p.draw_line(Point(100 + 54 * i, 100), Point(100 + 54 * i, size[1] - 100), width=.5)
    for j in range(28):
        p.draw_line(Point(100, 100 + 52 * j), Point(size[0] - 100, 100 + 52 * j), width=.5)
    for i in range(150):
        x, y = 150 + (i * 97) % (size[0] - 400), 150 + (i * 61) % (size[1] - 400)
        p.draw_rect(Rect(x, y, x + 40 + i % 30, y + 25 + i % 20), color=(0, 0, 0), width=1.2)
    p.insert_font(fontname="sy", fontfile=SY)
    for k in range(12):
        p.insert_text((200 + 150 * k, 1500), f"ოთახი {k + 1}", fontname="sy", fontsize=18)
    d.save(str(path))
    return str(path)


@pytest.mark.parametrize("size", [(2420, 1700), (2700, 1900)])
def test_sheets_of_another_size_are_aligned(tmp_path, size):
    """Both pictures were made smaller by their own factors before matching: the scale came out
    wrong by the ratio of the sheets, and false changes were reported as a good alignment."""
    old = big_sheet(tmp_path / "old.pdf")
    src = pymupdf.open(old)
    d = pymupdf.open()
    p = d.new_page(width=size[0], height=size[1])
    p.show_pdf_page(Rect(37, 23, 37 + 2384, 23 + 1684), src, 0)
    p.draw_circle(Point(1230, 830), 40, color=(0, 0, 0), fill=(0, 0, 0))
    new = str(tmp_path / "new.pdf")
    d.save(new)
    r = engine(new).visual_diff(old, "", 0, 0, {})
    (a, b, c), (_, f, g) = r["matrix"]
    assert r["aligned"] == "auto" and r["ok"], r
    assert abs(a - 1) < .003 and abs(f - 1) < .003 and abs(c - 37) < 1.5 and abs(g - 23) < 1.5
    assert only(r["regions"], [(1190, 790, 1270, 870)]), r["regions"]


def test_the_places_of_one_page_are_capped(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE)
    dots = [("disc", (40 + 26 * (i % 30), 40 + 26 * (i // 30), 2)) for i in range(600)]
    new = drawing(tmp_path / "new.pdf", BASE + dots)
    r = engine(new).visual_diff(old, "", 0, 0, SENSITIVITY[5])
    assert len(r["regions"]) == Features.DIFF_MAX_REGIONS and r["more"] > 0


def test_a_turned_page_is_compared_where_it_is_shown(tmp_path):
    def turned(path, shapes):
        d = pymupdf.open(drawing(path, shapes))
        d[0].set_rotation(90)
        d.saveIncr()
        return str(path)
    old = turned(tmp_path / "old.pdf", BASE)
    new = turned(tmp_path / "new.pdf", BASE + [("disc", (150, 350, 40))])
    want = Rect(110, 310, 190, 390) * pymupdf.open(new)[0].rotation_matrix
    r = engine(new).visual_diff(old, "", 0, 0, {})
    assert only(r["regions"], [tuple(want)]), (r["regions"], want)


def test_a_cloud_is_told_from_a_plain_polygon(tmp_path):
    from furceli.tools import kind_name
    path = blank(tmp_path)
    d = pymupdf.open(path)
    d[0].add_polygon_annot([(50, 50), (150, 50), (100, 120)])  # another program's polygon
    d.saveIncr()
    e = engine(path)
    e.apply(("annot", 0, "cloud", {"rect": (200, 200, 300, 300), "color": (1, 0, 0),
                                   "width": 1}, META))
    plain, cloud = e.annots(0)
    assert (plain["cloud"], cloud["cloud"]) == (False, True)
    assert kind_name(plain) == "მრავალკუთხედი" and kind_name(cloud) == "ღრუბელი"


def test_the_words_are_compared_with_the_old_file_already_open(tmp_path):
    old = drawing(tmp_path / "old.pdf", BASE, [((80, 300), "ფასი 100 ლარი")])
    new = drawing(tmp_path / "new.pdf", BASE, [((80, 300), "ფასი 250 ლარი")])
    e = engine(new)
    e.visual_diff(old, "", 0, 0, {})
    held = e._other[1]
    assert e.compare(old, "", False)["changes"]
    assert e._other[1] is held and held.doc is not None  # still open for the next page


def test_words_of_pages_not_compared_are_left_out():
    text = [{"kind": "replace", "old": "ა", "new": "ბ", "page": 1,
             "old_at": [(1, (10, 10, 20, 20))], "new_at": [(1, (10, 10, 20, 20))]},
            {"kind": "delete", "old": "გ", "new": "", "page": 0,
             "old_at": [(2, (10, 10, 20, 20))], "new_at": []}]
    assert merge_changes(text, {}, [(0, 0)]) == []          # page 2 of both is "—"
    kept = merge_changes(text, {}, [(1, 1), (2, 0)])
    assert [e["pair"] for e in kept] == [(2, 0), (1, 1)]
