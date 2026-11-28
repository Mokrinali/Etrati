"""Page names under the thumbnails stay inside their own page (HANDOFF, point 5: long names
of drawing sheets ran into each other)."""
import pymupdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QStyleOptionViewItem
from test_ui import app, open_doc, wait, win  # noqa: F401

from furceli.thumbs import ThumbDelegate

LONG = "A-101 პირველი სართულის გეგმა და ჭრილები "


@pytest.fixture
def sheets(tmp_path):
    d = pymupdf.open()
    for _ in range(4):
        d.new_page()
    d.set_page_labels([{"startpage": 0, "prefix": LONG, "style": "D", "firstpagenum": 1}])
    path = str(tmp_path / "sheets.pdf")
    d.save(path)
    return path


def badges(lst):
    """(cell, badge, written) for every page of a thumbnail list."""
    delegate, model = lst.itemDelegate(), lst.model_
    option = QStyleOptionViewItem()
    option.font = lst.font()
    m = QFontMetrics(ThumbDelegate.font(option))
    out = []
    for row in range(model.rowCount()):
        index = model.index(row)
        cell = lst.visualRect(index)
        badge, text = delegate.badge(cell, lst.iconSize(), index, m)
        out.append((cell, badge, text))
    return out


def test_long_page_names_stay_inside_their_page(win, sheets):  # noqa: F811
    doc = open_doc(win, sheets)
    for lst in (doc.list, doc.grid):
        for cell, badge, text in badges(lst):
            assert cell.left() <= badge.left() and badge.right() <= cell.right() + 1, (text, cell, badge)
            assert text.endswith("…"), text
        boxes = [b for _, b, _ in badges(lst)]
        for a, b in zip(boxes, boxes[1:]):  # neighbours in a row never meet
            assert not (a.intersects(b)), (a, b)


def test_the_whole_name_is_a_tooltip(win, sheets):  # noqa: F811
    doc = open_doc(win, sheets)
    index = doc.list.model_.index(1)
    assert index.data(Qt.ItemDataRole.ToolTipRole) == doc.page_label(1)
    assert LONG.strip() in doc.page_label(1)


def test_short_names_are_left_alone(win, tmp_path):  # noqa: F811
    d = pymupdf.open()
    d.new_page()
    d.new_page()
    path = str(tmp_path / "plain.pdf")
    d.save(path)
    doc = open_doc(win, path)
    (_, _, first), (_, _, second) = badges(doc.list)
    assert (first, second) == ("1", "2")
    assert doc.list.model_.index(0).data(Qt.ItemDataRole.ToolTipRole) is None
