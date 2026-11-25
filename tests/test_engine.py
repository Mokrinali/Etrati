import os
import shutil

import numpy as np
import pytest

from furceli.engine import Engine, EngineError


@pytest.fixture
def eng(sample):
    e = Engine()
    e.info = e.open(sample)
    yield e
    e.close()


def arr(r):
    w, h, stride, data = r
    return np.frombuffer(data, np.uint8).reshape(h, stride)[:, : w * 3].reshape(h, w, 3)


def test_open_info(eng):
    assert len(eng.info["sizes"]) == 12
    assert eng.info["sizes"][2] == (842, 595)
    assert eng.info["sizes"][3] == (842, 595)  # /Rotate 90 applied
    assert eng.info["toc"][1][:3] == (2, "თავი 2", 1)
    assert eng.info["metadata"]["title"] == "სატესტო დოკუმენტი"
    assert eng.info["can_print"] and eng.info["can_copy"]


@pytest.mark.parametrize("pno", [0, 2, 3])
@pytest.mark.parametrize("scale", [1.0, 1.37, 2.5])
@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_tiles_stitch_to_full_page(eng, pno, scale, rot):
    full = arr(eng.render(pno, scale, rot))
    h, w = full.shape[:2]
    out = np.zeros_like(full)
    T = 256
    for y in range(0, h, T):
        for x in range(0, w, T):
            a = arr(eng.render(pno, scale, rot, (x, y, min(x + T, w), min(y + T, h))))
            assert a.shape[:2] == (min(T, h - y), min(T, w - x))
            out[y:y + a.shape[0], x:x + a.shape[1]] = a
    # Anti-aliasing may differ by a hair at tile edges, nothing visible.
    assert np.mean(np.abs(full.astype(int) - out) > 40) < 0.001


def test_view_rotation_is_clockwise(eng):
    a, b = arr(eng.render(0, 1.0, 0)), arr(eng.render(0, 1.0, 90))
    assert b.shape[:2] == (a.shape[1], a.shape[0])
    assert np.mean(np.abs(np.rot90(a, -1).astype(int) - b) > 60) < 0.02


@pytest.mark.parametrize("pno", [0, 2, 3])
def test_search_boxes_land_on_ink(eng, pno):
    """Covers rotated pages: the reported box must really contain the text."""
    hits = eng.search(pno, "მსოფლიო")
    assert len(hits) == 14
    img = arr(eng.render(pno, 2.0))
    for (x0, y0, x1, y1), _ in hits:
        box = img[int(y0 * 2):int(y1 * 2), int(x0 * 2):int(x1 * 2)]
        assert box.size and (box.min(axis=2) < 100).mean() > 0.03


def test_search_case_and_missing(eng):
    assert len(eng.search(0, "HELLO")) == 14
    assert len(eng.search(0, "გამარჯობა მსოფლიო")) == 14  # spaces are NBSP in the file
    assert eng.search(0, "არარსებული") == []
    hits = eng.search(4, "უნიკალური")
    assert len(hits) == 1 and "უნიკალური" in hits[0][1]


def test_chars_match_search_on_rotated_page(eng):
    lines = eng.chars(3)
    text = ["".join(c[4] for c in ln) for ln in lines]
    assert any("გამარჯობა მსოფლიო, სტრიქონი 1." in t for t in text)
    w, h = eng.info["sizes"][3]
    assert all(-1 <= c[0] and c[2] <= w + 1 and -1 <= c[1] and c[3] <= h + 1
               for ln in lines for c in ln)
    hit = eng.search(3, "გამარჯობა")[0][0]
    first = next(ln for ln in lines if "".join(c[4] for c in ln).startswith("გამარჯობა"))[0]
    assert hit[0] - 2 <= first[0] and first[3] <= hit[3] + 2


def test_links(eng):
    links = eng.links(0)
    assert len(links) == 2
    goto = next(ln for ln in links if "page" in ln)
    uri = next(ln for ln in links if "uri" in ln)
    assert uri["uri"] == "https://example.com/"
    assert goto["page"] == 5 and abs(goto["y"] - 300) < 1
    assert abs(goto["rect"][1] - 600) < 1


def test_password(locked):
    e = Engine()
    with pytest.raises(EngineError, match="needs_password"):
        e.open(locked)
    with pytest.raises(EngineError, match="wrong_password"):
        e.open(locked, "x")
    info = e.open(locked, "საიდუმლო")
    assert len(info["sizes"]) == 12
    assert not info["can_print"] and not info["can_copy"]


def test_bad_files(tmp_path):
    e = Engine()
    empty = tmp_path / "e.pdf"
    empty.write_bytes(b"")
    junk = tmp_path / "j.pdf"
    junk.write_bytes(b"this is not a pdf" * 200)
    for p in (empty, junk):
        with pytest.raises(EngineError, match="damaged"):
            e.open(str(p))
    with pytest.raises(EngineError, match="not_found"):
        e.open(str(tmp_path / "none.pdf"))


def test_truncated_file_is_repaired_or_rejected(sample, tmp_path):
    with open(sample, "rb") as f:
        data = f.read()
    p = tmp_path / "t.pdf"
    p.write_bytes(data[: int(len(data) * .9)])
    e = Engine()
    try:
        assert e.open(str(p))["repaired"]
    except EngineError as x:
        assert str(x) == "damaged"


def test_file_not_locked_while_open(sample, tmp_path):
    c = str(tmp_path / "c.pdf")
    shutil.copy(sample, c)
    e = Engine()
    e.open(c)
    os.remove(c)
    assert e.render(0, 1.0)[0] > 0


def test_original_is_never_modified(sample):
    with open(sample, "rb") as f:
        before = f.read()
    e = Engine()
    e.open(sample)
    e.render(0, 2.0)
    e.search(0, "თავი")
    e.chars(0)
    e.links(0)
    e.close()
    with open(sample, "rb") as f:
        assert f.read() == before
