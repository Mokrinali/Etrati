"""What a click or a box selects for deleting (furceli/erase_pick.py): no window needed."""
from furceli import erase_pick as ep

DATA = {
    "paths": [
        {"id": 0, "rect": (20, 20, 580, 380), "fill": False, "group": -1, "shared": False,
         "segs": [[(20, 20), (580, 20), (580, 380), (20, 380), (20, 20)]]},       # frame
        {"id": 1, "rect": (100, 100, 106, 103), "fill": True, "group": 0, "shared": False,
         "segs": [[(100, 100), (106, 100), (103, 103), (100, 100)]]},              # hatch piece
        {"id": 2, "rect": (106, 100, 112, 103), "fill": True, "group": 0, "shared": False,
         "segs": [[(106, 100), (112, 100), (109, 103), (106, 100)]]},              # hatch piece
        {"id": 3, "rect": (50, 250, 550, 250), "fill": False, "group": -1, "shared": False,
         "segs": [[(50, 250), (550, 250)]]},                                       # long line
        {"id": 4, "rect": (0, 0, 600, 400), "fill": True, "group": -1, "shared": False,
         "segs": [[(0, 0), (600, 0), (600, 400), (0, 400), (0, 0)]]},              # white page
    ],
    "lines": [{"index": 7, "text": "B25", "rect": (300, 120, 330, 132)}],
    "images": [{"xref": 9, "rect": (400, 300, 450, 340)}],
    "dates": [],
}
PAGE = (600, 400)


def pick():
    return ep.items(DATA)


def test_a_hatch_of_pieces_is_one_item():
    items = pick()
    hatch = [i for i in items if i["kind"] == "path" and len(i["ids"]) == 2]
    assert len(hatch) == 1 and hatch[0]["rect"] == (100, 100, 112, 103)
    assert len(items) == 1 + 1 + 1 + 1 + 1 + 1  # frame, hatch, line, page fill, text, image


def test_a_click_on_a_line_takes_the_line_not_the_frame_around_it():
    got = ep.hit(pick(), 300, 251, tol=3, page=PAGE)
    assert got["ids"] == [(3, (50, 250, 550, 250))]


def test_a_click_inside_a_hatch_takes_the_hatch():
    got = ep.hit(pick(), 104, 101, tol=3, page=PAGE)
    assert len(got["ids"]) == 2


def test_a_click_on_empty_paper_takes_nothing():
    """The page-sized white fill is never taken by a click (only by a box)."""
    assert ep.hit(pick(), 200, 180, tol=3, page=PAGE) is None


def test_text_and_pictures_are_taken_by_their_box():
    assert ep.hit(pick(), 310, 125, tol=3, page=PAGE)["kind"] == "text"
    assert ep.hit(pick(), 420, 320, tol=3, page=PAGE)["kind"] == "image"


def test_left_to_right_takes_only_what_is_wholly_inside():
    got = ep.in_box(pick(), (90, 90, 340, 140), crossing=False)
    assert sorted(i["kind"] for i in got) == ["path", "text"]   # the hatch and B25


def test_right_to_left_takes_what_it_touches_too():
    got = ep.in_box(pick(), (90, 240, 340, 260), crossing=True)
    kinds = sorted((i["kind"], len(i["ids"]) if i["kind"] == "path" else 0) for i in got)
    assert ("path", 1) in kinds                     # the long line it touches
    assert not any(i["rect"] == (20, 20, 580, 380) for i in got)  # the frame's sides are far


def test_crossing_a_shape_only_inside_its_box_does_not_take_it():
    """A box inside the frame touches none of its lines."""
    got = ep.in_box(pick(), (200, 150, 260, 200), crossing=True)
    assert not any(i["rect"] == (20, 20, 580, 380) for i in got)


def test_the_operation_lists_everything_chosen():
    items = pick()
    chosen = [i for i in items if i["kind"] in ("text", "image")] + \
             [i for i in items if i["kind"] == "path" and len(i["ids"]) == 2]
    op = ep.operation(3, chosen)
    assert op == ("erase", 3, [(1, (100, 100, 106, 103)), (2, (106, 100, 112, 103))],
                  [(7, "B25")], [(9, (400, 300, 450, 340))], [])


def test_counts_and_filters_by_kind():
    items = pick()
    assert ep.counts(items) == {"lines": 4, "text": 1, "images": 1}
    assert [i["kind"] for i in ep.only(items, {"text", "images"})] == ["text", "image"]


def test_text_over_lines_is_taken_before_the_lines():
    data = dict(DATA, paths=DATA["paths"] + [
        {"id": 5, "rect": (300, 126, 330, 126), "fill": False, "group": -1, "shared": False,
         "segs": [[(300, 126), (330, 126)]]}])  # a line right through B25
    assert ep.hit(ep.items(data), 310, 126, tol=3, page=PAGE)["kind"] == "text"
