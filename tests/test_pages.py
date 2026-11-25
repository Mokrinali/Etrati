"""Page operations, checked on the saved file rather than in memory."""
import os

import numpy as np
import pymupdf
import pytest
from pymupdf import Point, Rect

from furceli.engine import EditEngine, Engine, EngineError


def build(path, n=6, tag="A"):
    d = pymupdf.open()
    for i in range(n):
        p = d.new_page(width=600, height=800)
        p.insert_text((72, 72), f"{tag}SECRET{i}", fontsize=20)
        w = pymupdf.Widget()
        w.field_name, w.field_type = f"f{i}", pymupdf.PDF_WIDGET_TYPE_TEXT
        w.rect, w.field_value = Rect(72, 200, 300, 220), f"{tag}VALUE{i}"
        p.add_widget(w)
        p.add_highlight_annot(Rect(72, 50, 200, 80))
    if n > 4:
        d[0].insert_link({"kind": 1, "from": Rect(10, 300, 50, 320), "page": 3,
                          "to": Point(0, 250)})
        d[1].insert_link({"kind": 2, "from": Rect(10, 300, 50, 320), "uri": "https://a.b/"})
    d.set_toc([[1 if i % 2 == 0 else 2, f"{tag}T{i}", i + 1, 100.0] for i in range(n)])
    d.set_metadata({"title": "სათაური"})
    # ways a removed page could stay reachable
    cat, pg = d.pdf_catalog(), d[2].xref
    root, el = d.get_new_xref(), d.get_new_xref()
    d.update_object(root, f"<</Type/StructTreeRoot/K {el} 0 R>>")
    d.update_object(el, f"<</Type/StructElem/S/P/P {root} 0 R/Pg {pg} 0 R/K 0"
                        f"/ActualText({tag}SECRET2)>>")
    d.xref_set_key(cat, "StructTreeRoot", f"{root} 0 R")
    d.xref_set_key(cat, "OpenAction", f"[{pg} 0 R /Fit]")
    d.xref_set_key(d[0].xref, "AA", f"<</O<</S/GoTo/D[{pg} 0 R/Fit]>>>>")
    d.save(str(path))
    d.close()
    return str(path)


@pytest.fixture
def six(tmp_path):
    return build(tmp_path / "ექვსი.pdf")


@pytest.fixture
def run(six, tmp_path):
    def go(*ops):
        e = EditEngine()
        e.open(six)
        for op in ops:
            e.apply(op)
        out = str(tmp_path / "out.pdf")
        e.save(out)
        return pymupdf.open(out)
    return go


def names(d):
    return [p.get_text().split()[0] if p.get_text().split() else "" for p in d]


def everything(d):
    """Every decompressed byte of a file, for looking for text that should be gone."""
    return b"".join((d.xref_stream(x) or b"") + d.xref_object(x).encode()
                    for x in range(1, d.xref_length())).lower()


def holds(d, text):
    raw = everything(d)
    return text.lower().encode() in raw or text.encode().hex().encode() in raw


def test_leak_detector_really_detects(six):
    d = pymupdf.open(six)
    assert holds(d, "ASECRET2") and holds(d, "AVALUE2")


def test_delete_removes_every_trace(run):
    d = run(("order", [0, 1, 3, 4, 5]))
    assert names(d) == [f"ASECRET{i}" for i in (0, 1, 3, 4, 5)]
    for text in ("ASECRET2", "AVALUE2"):
        assert not holds(d, text), text
    for i in (0, 1, 3, 4, 5):
        assert holds(d, f"ASECRET{i}") and holds(d, f"AVALUE{i}")
    assert [t[1:] for t in d.get_toc() if t[2] > 0] == [
        ["AT0", 1], ["AT1", 2], ["AT3", 3], ["AT4", 4], ["AT5", 5]]
    assert d.metadata["title"] == "სათაური"


def test_delete_after_other_ops_leaves_no_trace(run):
    d = run(("rotate", [2], 90), ("duplicate", [4]), ("order", [0, 1, 3, 4, 5, 6]))
    assert not holds(d, "ASECRET2") and not holds(d, "AVALUE2")


def test_reorder_moves_links_bookmarks_and_fields(run):
    d = run(("order", [5, 4, 3, 2, 1, 0]))
    assert names(d) == [f"ASECRET{i}" for i in (5, 4, 3, 2, 1, 0)]
    assert {t[1]: t[2] for t in d.get_toc()} == {f"AT{i}": 6 - i for i in range(6)}
    link = d[5].get_links()[0]
    assert link["page"] == 2 and abs(link["to"].y - 250) < 1
    assert [[w.field_value for w in p.widgets()] for p in d] == [
        [f"AVALUE{i}"] for i in (5, 4, 3, 2, 1, 0)]
    assert holds(d, "ActualText")  # nothing removed: the tag tree stays whole


@pytest.mark.parametrize("bad", [[], [0, 0], [0, 9], [-1]])
def test_bad_orders_are_refused(six, bad):
    e = EditEngine()
    e.open(six)
    with pytest.raises(EngineError):
        e.apply(("order", bad))
    assert e.doc.page_count == 6


def test_rotate(run):
    d = run(("rotate", [1, 2], 90), ("rotate", [2], 90), ("rotate", [0], -90))
    assert [p.rotation for p in d] == [270, 90, 180, 0, 0, 0]


def test_duplicate_is_independent(run):
    d = run(("duplicate", [0, 1]))
    assert names(d) == [f"ASECRET{i}" for i in (0, 1, 0, 1, 2, 3, 4, 5)]
    assert {t[1]: t[2] for t in d.get_toc()} == {
        "AT0": 1, "AT1": 2, "AT2": 5, "AT3": 6, "AT4": 7, "AT5": 8}
    assert [a.xref for a in d[0].annots()] != [a.xref for a in d[2].annots()]
    f = [w.field_name for p in d for w in p.widgets()]
    assert len(f) == 8 == len(set(f))
    assert d[3].get_links()[0]["uri"] == "https://a.b/"
    assert d[0].get_links()[0]["page"] == 5  # the original link followed its target


@pytest.mark.parametrize("at", [0, 3, 6])
def test_insert_from_file(six, tmp_path, at):
    other = build(tmp_path / "სხვა.pdf", 5, "B")
    e = EditEngine()
    e.open(six)
    info = e.apply(("insert", other, "", [0, 3, 1], at))
    out = str(tmp_path / "o.pdf")
    e.save(out)
    d = pymupdf.open(out)
    want = [f"ASECRET{i}" for i in range(6)]
    want[at:at] = ["BSECRET0", "BSECRET3", "BSECRET1"]
    assert names(d) == want
    assert len(info["mapping"]) == 9 and info["mapping"][at:at + 3] == [-1] * 3
    assert not holds(d, "BSECRET2") and not holds(d, "BVALUE2")
    toc = {t[1]: t[2] for t in d.get_toc()}
    assert all(toc[f"AT{i}"] == want.index(f"ASECRET{i}") + 1 for i in range(6))
    assert all(toc[f"BT{i}"] == want.index(f"BSECRET{i}") + 1 for i in (0, 1, 3))
    link = d[want.index("BSECRET0")].get_links()[0]
    assert link["page"] == want.index("BSECRET3") and abs(link["to"].y - 250) < 1


def test_blank(run):
    d = run(("blank", 2, 300, 400), ("blank", 7, 595, 842))
    assert d.page_count == 8 and d[2].rect == Rect(0, 0, 300, 400)
    assert names(d)[2] == "" and names(d)[7] == ""


def ink(page):
    pix = page.get_pixmap(colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    return a


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("origin", [(0, 0), (100, 50)])
def test_crop_cuts_the_side_the_user_sees(tmp_path, rot, origin):
    ox, oy = origin
    d = pymupdf.open()
    p = d.new_page(width=600, height=800)
    for k in range(40):  # asymmetric picture
        p.draw_rect(Rect(20 + k * 5, 30 + k * 11, 60 + k * 9, 38 + k * 11), fill=(0, 0, 0))
    d.xref_set_key(p.xref, "MediaBox", f"[{ox} {oy} {600 + ox} {800 + oy}]")
    p.set_rotation(rot)
    path = str(tmp_path / "c.pdf")
    d.save(path)
    e = EditEngine()
    e.open(path)
    before = ink(e.doc[0])
    e.apply(("crop", [0], (30, 50, 70, 110)))
    after = ink(e.doc[0])
    h, w = before.shape
    want = before[50:h - 110, 30:w - 70]
    assert after.shape == want.shape
    assert np.mean(np.abs(after.astype(int) - want) > 60) < 0.01
    e.apply(("crop", [0], (10, 10, 10, 10)))  # cropping again adds up
    assert ink(e.doc[0]).shape == (want.shape[0] - 20, want.shape[1] - 20)
    e.apply(("crop", [0], None))
    assert ink(e.doc[0]).shape == before.shape
    assert np.mean(np.abs(ink(e.doc[0]).astype(int) - before) > 60) < 0.01


def test_crop_refuses_nonsense(six):
    e = EditEngine()
    e.open(six)
    for m in ((300, 0, 295, 0), (-5, 0, 0, 0)):
        with pytest.raises(EngineError, match="crop_too_small"):
            e.apply(("crop", [0], m))


def test_replay_equals_applying_and_undo_restores(six, tmp_path):
    ops = [("rotate", [0], 90), ("order", [3, 2, 1, 0]), ("duplicate", [1]),
           ("blank", 0, 200, 200), ("crop", [2], (10, 10, 10, 10))]
    a, b = EditEngine(), EditEngine()
    a.open(six)
    for op in ops:
        last = a.apply(op)
    again = b.replay(six, "", ops)
    assert again["sizes"] == last["sizes"] and names(a.doc) == names(b.doc)
    undone = b.replay(six, "", ops[:1])
    assert len(undone["sizes"]) == 6 and undone["sizes"][0] == (800, 600)
    assert names(b.doc) == [f"ASECRET{i}" for i in range(6)]


def test_mapping_is_truthful(six):
    e = EditEngine()
    e.open(six)
    for op in (("order", [4, 2, 0, 1]), ("duplicate", [1, 2]), ("blank", 1, 100, 100)):
        before = names(e.doc)
        m = e.apply(op)["mapping"]
        assert len(m) == e.doc.page_count
        assert all(names(e.doc)[i] == before[o] for i, o in enumerate(m) if o >= 0)


def test_original_untouched_by_edits(six, tmp_path):
    with open(six, "rb") as f:
        before = f.read()
    e = EditEngine()
    e.open(six)
    e.apply(("order", [1]))
    e.save(str(tmp_path / "x.pdf"))
    with open(six, "rb") as f:
        assert f.read() == before


def test_export_parts(six, tmp_path):
    e = EditEngine()
    e.open(six)
    a, b = str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")
    e.export([([0, 3], a), ([5], b)])
    d = pymupdf.open(a)
    assert names(d) == ["ASECRET0", "ASECRET3"]
    assert d[0].get_links()[0]["page"] == 1
    assert [t[1:] for t in d.get_toc()] == [["AT0", 1], ["AT3", 2]]
    for other in (1, 2, 4, 5):
        assert not holds(d, f"ASECRET{other}") and not holds(d, f"AVALUE{other}")
    assert names(pymupdf.open(b)) == ["ASECRET5"]


def test_merge(six, tmp_path):
    other = build(tmp_path / "b.pdf", 5, "B")
    out = str(tmp_path / "m.pdf")
    r = EditEngine().merge([(six, "", None, "პირველი"), (other, "", [4, 0], "მეორე")], True, out)
    d = pymupdf.open(out)
    assert r["pages"] == 8
    assert names(d) == [f"ASECRET{i}" for i in range(6)] + ["BSECRET4", "BSECRET0"]
    toc = d.get_toc()
    assert toc[0] == [1, "პირველი", 1] and [1, "მეორე", 7] in toc
    assert {t[1]: t[2] for t in toc}["BT0"] == 8
    assert d[0].get_links()[0]["page"] == 3
    f = [w.field_name for p in d for w in p.widgets()]
    assert len(f) == 8 == len(set(f))
    assert [w.field_value for w in d[0].widgets()] == ["AVALUE0"]
    out = str(tmp_path / "m2.pdf")
    EditEngine().merge([(six, "", None, "x"), (other, "", None, "y")], False, out)
    assert {t[1] for t in pymupdf.open(out).get_toc()} == {f"AT{i}" for i in range(6)} | {
        f"BT{i}" for i in range(5)}


def test_merge_keeps_rotated_pages_and_their_links(tmp_path):
    d = pymupdf.open()
    for _ in range(2):
        d.new_page(width=600, height=800)
    d[0].insert_link({"kind": 1, "from": Rect(100, 200, 150, 220), "page": 1, "to": Point(50, 300)})
    d.set_toc([[1, "t", 2, 300.0]])
    d = pymupdf.open(stream=d.tobytes(), filetype="pdf")
    d[0].set_rotation(90)
    d[1].set_rotation(90)
    src = str(tmp_path / "r.pdf")
    d.save(src)
    out = str(tmp_path / "m.pdf")
    EditEngine().merge([(src, "", None, "x")], False, out)
    a, b = Engine(), Engine()
    ia, ib = a.open(src), b.open(out)
    assert ia["sizes"] == ib["sizes"]
    la, lb = a.links(0)[0], b.links(0)[0]
    assert np.allclose(la["rect"], lb["rect"], atol=1) and abs(la["y"] - lb["y"]) < 1
    assert abs(ia["toc"][0][3] - ib["toc"][0][3]) < 1


def test_links_on_rotated_page_are_where_they_are_drawn(tmp_path):
    """Regression: coordinates used to be rotated twice."""
    for rot in (0, 90, 180, 270):
        d = pymupdf.open()
        d.new_page(width=600, height=800).draw_rect(Rect(100, 200, 150, 220), fill=(0, 0, 0))
        d[0].insert_link({"kind": 2, "from": Rect(100, 200, 150, 220), "uri": "https://a.b/"})
        d = pymupdf.open(stream=d.tobytes(), filetype="pdf")
        d[0].set_rotation(rot)
        path = str(tmp_path / f"{rot}.pdf")
        d.save(path)
        e = Engine()
        e.open(path)
        x0, y0, x1, y1 = e.links(0)[0]["rect"]
        a = ink(e.doc[0])
        assert a[int(y0) + 2:int(y1) - 2, int(x0) + 2:int(x1) - 2].max() < 50, rot


def test_encryption_survives_saving(six, tmp_path):
    locked = str(tmp_path / "l.pdf")
    d = pymupdf.open(six)
    d.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="პაროლი", owner_pw="o")
    e = EditEngine()
    e.open(locked, "პაროლი")
    for op in (("duplicate", [0]), ("blank", 0, 99, 99), ("crop", [1], (5, 5, 5, 5)),
               ("insert", six, "", None, 0), ("order", [3, 1])):
        e.apply(op)
    e.export([([0], str(tmp_path / "part.pdf"))])  # must not unprotect the document
    out = str(tmp_path / "o.pdf")
    e.save(out)
    d = pymupdf.open(out)
    assert d.needs_pass and d.authenticate("პაროლი") and d.page_count == 2
    with pytest.raises(EngineError, match="needs_password"):
        EditEngine().merge([(locked, "", None, "x")], True, str(tmp_path / "m.pdf"))
    EditEngine().merge([(locked, "პაროლი", None, "x")], True, str(tmp_path / "m.pdf"))


def test_save_to_bad_place(six, tmp_path):
    e = EditEngine()
    e.open(six)
    with pytest.raises(EngineError):
        e.save(str(tmp_path / "no_such_dir" / "x.pdf"))


def test_bookmark_targets_survive_edits_on_rotated_pages(tmp_path):
    d = pymupdf.open()
    for _ in range(3):
        d.new_page(width=600, height=800)
    d.set_toc([[1, "a", 1, 300.0], [1, "b", 2, 300.0], [1, "c", 3, 300.0]])
    d = pymupdf.open(stream=d.tobytes(), filetype="pdf")
    for i, r in enumerate((0, 90, 270)):
        d[i].set_rotation(r)
    path = str(tmp_path / "r.pdf")
    d.save(path)
    e = EditEngine()
    before = e.open(path)["toc"]
    after = e.apply(("blank", 1, 100, 100))["toc"]
    assert [t[2] for t in after] == [0, 2, 3]
    assert np.allclose([t[3] for t in before], [t[3] for t in after], atol=1)
    # inserting a file with its own bookmarks rewrites the whole list
    info = e.apply(("insert", path, "", None, 4))
    assert [t[2] for t in info["toc"]] == [0, 2, 3, 4, 5, 6]
    assert np.allclose([t[3] for t in info["toc"]], [t[3] for t in before] * 2, atol=1)
    e.apply(("crop", [4], (40, 30, 20, 10)))
    out = str(tmp_path / "o.pdf")
    e.save(out)
    assert [t[2] for t in Engine().open(out)["toc"]] == [0, 2, 3, 4, 5, 6]


def test_losing_protection_is_an_error_not_a_surprise(six, tmp_path):
    locked = str(tmp_path / "l.pdf")
    d = pymupdf.open(six)
    d.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    e = EditEngine()
    e.open(locked, "u")
    e.doc.tobytes()  # the library call that silently drops encryption
    with pytest.raises(EngineError, match="protection_lost"):
        e.save(str(tmp_path / "o.pdf"))


@pytest.mark.parametrize("op", [
    ("rotate", [0], 90), ("order", [3, 1, 0, 2, 4, 5]), ("order", [3, 1]), ("duplicate", [0, 2]),
    ("blank", 1, 99, 99), ("crop", [1], (5, 5, 5, 5)), ("crop", [1], None),
    ("insert", "SIX", "", None, 2), ("insert", "SIX", "", [0], 2), ("export",)],
    ids=lambda o: f"{o[0]}{len(o[1]) if len(o) > 1 and isinstance(o[1], list) else ''}")
def test_every_operation_keeps_encryption(six, tmp_path, op):
    locked = str(tmp_path / "l.pdf")
    d = pymupdf.open(six)
    d.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    e = EditEngine()
    e.open(locked, "u")
    if op[0] == "export":
        e.export([([0], str(tmp_path / "part.pdf"))])
    else:
        e.apply(tuple(six if x == "SIX" else x for x in op))
    out = str(tmp_path / "o.pdf")
    e.save(out)
    d = pymupdf.open(out)
    assert d.needs_pass and d.authenticate("u")


def test_owner_password_only_is_kept_too(six, tmp_path):
    """Opens without a password, but printing/copying limits must survive."""
    locked = str(tmp_path / "l.pdf")
    d = pymupdf.open(six)
    d.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
           permissions=int(pymupdf.PDF_PERM_ASSEMBLE))
    e = EditEngine()
    info = e.open(locked)
    assert info["protected"] and info["can_assemble"] and not info["can_copy"]
    e.apply(("order", [2, 0]))
    out = str(tmp_path / "o.pdf")
    e.save(out)
    info = Engine().open(out)
    assert info["protected"] and not info["can_copy"] and not info["can_print"]


def test_delete_field_that_spans_pages(tmp_path):
    """One field shown on two pages: it stays while one of them stays."""
    d = pymupdf.open()
    for i in range(3):
        d.new_page()
    xs = []
    for i in (0, 1):
        w = pymupdf.Widget()
        w.field_name, w.field_type = f"k{i}", pymupdf.PDF_WIDGET_TYPE_TEXT
        w.rect, w.field_value = Rect(72, 200, 300, 220), "SHAREDVALUE"
        d[i].add_widget(w)
        xs.append(w.xref)
    parent = d.get_new_xref()
    d.update_object(parent, f"<</FT/Tx/T(shared)/V(SHAREDVALUE)/Kids[{xs[0]} 0 R {xs[1]} 0 R]>>")
    for x in xs:
        d.xref_set_key(x, "Parent", f"{parent} 0 R")
        for k in ("T", "V", "FT"):
            d.xref_set_key(x, k, "null")
    d.xref_set_key(d.pdf_catalog(), "AcroForm/Fields", f"[{parent} 0 R]")
    path = str(tmp_path / "k.pdf")
    d.save(path)
    for order, stays in (([0, 2], True), ([2], False)):
        e = EditEngine()
        e.open(path)
        e.apply(("order", order))
        out = str(tmp_path / f"o{stays}.pdf")
        e.save(out)
        r = pymupdf.open(out)
        assert holds(r, "SHAREDVALUE") == stays
        assert sum(1 for p in r for _ in p.widgets()) == (1 if stays else 0)


def test_random_operations_never_crash_and_match_a_model(six, tmp_path):
    """Hundreds of random edits in a child process, compared with a plain list."""
    import subprocess
    import sys
    code = f'''
import faulthandler, random, sys
faulthandler.enable()
sys.path[:0] = [r"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}",
                r"{os.path.dirname(os.path.abspath(__file__))}"]
from test_pages import names, holds
from furceli.engine import EditEngine
import pymupdf
for seed in range(6):
    rnd = random.Random(seed)
    e = EditEngine(); e.open(r"{six}")
    model = list("012345"); ops = []
    for step in range(70):
        n = len(model)
        kind = rnd.choice(["order", "order", "order", "delete", "dup", "blank", "insert",
                           "rotate", "crop"])
        if kind == "order":
            o = list(range(n)); rnd.shuffle(o)
            op, model = ("order", o), [model[i] for i in o]
        elif kind == "delete" and n > 4:
            o = sorted(rnd.sample(range(n), n - rnd.randint(1, 2)))
            op, model = ("order", o), [model[i] for i in o]
        elif kind == "dup" and n < 14:
            p = sorted(rnd.sample(range(n), 2)); at = p[-1] + 1
            op, model = ("duplicate", p), model[:at] + [model[i] for i in p] + model[at:]
        elif kind == "blank":
            at = rnd.randint(0, n)
            op, model = ("blank", at, 300, 300), model[:at] + [""] + model[at:]
        elif kind == "insert" and n < 14:
            at = rnd.randint(0, n)
            op, model = ("insert", r"{six}", "", [4, 1], at), model[:at] + ["4", "1"] + model[at:]
        elif kind == "rotate":
            op = ("rotate", rnd.sample(range(n), 2), 90)
        else:
            op = ("crop", rnd.sample(range(n), 2), (1, 1, 1, 1))
        e.apply(op); ops.append(op)
        if step % 5 == 0:
            e.render(rnd.randrange(len(model)), .3); e.chars(0); e.links(0)
        if step % 23 == 0:
            e.save(r"{tmp_path}/r.pdf")
        got = [x[-1:] for x in names(e.doc)]
        assert got == model, (seed, step, op, got, model)
    e.save(r"{tmp_path}/r.pdf")
    d = pymupdf.open(r"{tmp_path}/r.pdf")
    assert [x[-1:] for x in names(d)] == model
    for i in range(6):
        if str(i) not in model:
            assert not holds(d, "ASECRET%d" % i) and not holds(d, "AVALUE%d" % i), (seed, i)
    d.close()
    again = EditEngine(); again.replay(r"{six}", "", ops)
    assert names(again.doc) == names(e.doc)
    print("seed", seed, "ok", flush=True)
'''
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True, text=True,
                       encoding="utf8", timeout=280)
    assert r.returncode == 0 and r.stdout.count("ok") == 6, (r.returncode, r.stdout, r.stderr[-2500:])
