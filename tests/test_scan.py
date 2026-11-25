"""Scan clean-up: geometry, light, text, files, window."""
import importlib.util
import os

import cv2
import numpy as np
import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QProgressDialog
from test_features import engine, pic
from test_pages import build
from test_ui import app, open_doc, settled, wait, win  # noqa: F401
from test_ui_pages import no_dialogs, read  # noqa: F401
from test_ui_tools import blank

from furceli import convert, scan, scan_dialog
from furceli.batch import BatchDialog
from furceli.engine import EngineError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("scan_bench", os.path.join(ROOT, "scripts", "scan_bench.py"))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


@pytest.fixture(scope="module")
def page():
    return next(bench.synthetic(1))


def photo(page, kind, seed=1):
    return bench.photograph(page[0], kind, np.random.default_rng(seed))


def write(path, img, **kw):
    Image.fromarray(img).save(path, **kw)
    return str(path)


def error(corners, true):
    got = np.asarray(corners)
    return min(np.abs(np.roll(got, s, 0) - true).mean() for s in range(4))


# -- geometry ----------------------------------------------------------------
@pytest.mark.parametrize("kind", ["usual", "strong", "shadow", "dark", "bright", "wood", "low", "blur"])
@pytest.mark.parametrize("seed", [1, 2])
def test_sheet_is_found(page, kind, seed):
    img, true, _ = photo(page, kind, seed)
    got = scan.find_sheet(img)
    assert got is not None and error(got, true) < .02
    assert scan_dialog.convex(got)


@pytest.mark.parametrize("kind", ["screenshot", "scan"])
def test_whole_picture_is_left_alone(page, kind):
    assert scan.find_sheet(photo(page, kind)[0]) is None


def test_nothing_is_cut_from_pictures_without_a_sheet():
    rnd = np.random.default_rng(1)
    assert scan.find_sheet(np.full((800, 600, 3), 255, np.uint8)) is None
    noise = rnd.integers(0, 255, (800, 600, 3), dtype=np.uint8)
    assert scan.find_sheet(noise) is None
    assert scan.find_sheet(np.zeros((40, 40, 3), np.uint8)) is None


def test_a_table_on_the_page_is_not_taken_for_the_sheet():
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 60), "heading outside the frame", fontsize=14)
    p.draw_rect(pymupdf.Rect(70, 100, 525, 700), width=2)
    for i in range(20):
        p.insert_text((90, 140 + i * 25), f"row {i} with some words in it", fontsize=12)
    p.insert_text((72, 780), "footer outside the frame", fontsize=14)
    x = p.get_pixmap(dpi=150)
    img = np.frombuffer(x.samples, np.uint8).reshape(x.height, x.width, 3)
    assert scan.find_sheet(img) is None


def test_flatten_restores_the_shape(page):
    img, true, _ = photo(page, "strong", 3)
    flat = scan.flatten(img, true)
    assert abs(flat.shape[0] / flat.shape[1] - scan.A4) < .01
    assert scan.flatten(img, scan.FULL) is img


@pytest.mark.parametrize("q", [0, 1, 2, 3])
def test_upright(page, q):
    x = page[0].get_pixmap(dpi=130)
    img = np.frombuffer(x.samples, np.uint8).reshape(x.height, x.stride)[:, :x.width * 3].reshape(
        x.height, x.width, 3)
    assert scan.upright(scan.turn(img, q), "kat+eng") == (4 - q) % 4


def test_upright_leaves_pictures_alone():
    assert scan.upright(np.full((600, 800, 3), 240, np.uint8), "kat+eng") == 0
    rnd = np.random.default_rng(2)
    assert scan.upright(rnd.integers(0, 255, (600, 800, 3), dtype=np.uint8), "kat+eng") == 0


@pytest.mark.parametrize("angle", [-6, -1.3, .8, 4])
def test_level_takes_away_the_tilt(page, angle):
    x = page[0].get_pixmap(dpi=150)
    img = np.frombuffer(x.samples, np.uint8).reshape(x.height, x.stride)[:, :x.width * 3].reshape(
        x.height, x.width, 3)
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
    tilted = cv2.warpAffine(img, m, (w, h), borderValue=(255, 255, 255))
    assert abs(scan.skew(tilted) + angle) < .3
    assert abs(scan.skew(scan.level(tilted))) < .35


def test_level_takes_away_perspective_without_a_visible_sheet(page):
    x = page[0].get_pixmap(dpi=150)
    img = np.frombuffer(x.samples, np.uint8).reshape(x.height, x.stride)[:, :x.width * 3].reshape(
        x.height, x.width, 3)
    h, w = img.shape[:2]
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]),
                                    np.float32([[0, 0], [w, 60], [w, h - 70], [0, h]]))
    bent = cv2.warpPerspective(img, m, (w, h), borderValue=(255, 255, 255))

    def spread(a):
        flat, _, sw, sh = scan._segments(a)
        s = [(b[3] - b[1]) / (b[2] - b[0]) for b in flat if b[2] - b[0] > .25 * sw]
        return np.percentile(np.abs(s), 90)
    assert spread(bent) > .03 > .012 > spread(scan.level(bent))


def test_level_keeps_everything_in_the_picture(page):
    x = page[0].get_pixmap(dpi=150)
    img = np.frombuffer(x.samples, np.uint8).reshape(x.height, x.stride)[:, :x.width * 3].reshape(
        x.height, x.width, 3).copy()
    img[:40, :40] = (200, 0, 0)  # something in the very corner
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 3, 1)
    m[:, 2] += (40, 60)
    tilted = cv2.warpAffine(img, m, (w + 80, h + 120), borderValue=(255, 255, 255))
    done = scan.level(tilted)
    red = (done[..., 0] > 150) & (done[..., 1] < 90)
    assert red.sum() > 1100


# -- light -------------------------------------------------------------------
def test_shadow_goes_and_stamp_stays(page):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 100), "text on paper", fontsize=20)
    p.draw_circle((300, 500), 60, color=(.1, .2, .8), width=6)
    img, true, _ = photo((p, ""), "shadow", 4)
    flat = scan.flatten(img, true)
    done = scan.enhance(flat, "color")
    h, w = done.shape[:2]
    paper = done[int(h * .25):int(h * .45), int(w * .1):int(w * .9)]
    assert np.percentile(paper, 5) > 225           # white on both sides of the shadow
    ring = done[int(h * .5):int(h * .7), int(w * .35):int(w * .65)].astype(int)
    assert ((ring[..., 2] - ring[..., 0]) > 60).sum() > 400   # still blue


def test_big_dark_picture_is_not_bleached():
    img = np.full((1200, 900, 3), 235, np.uint8)
    img[300:800, 200:700] = (40, 60, 50)
    done = scan.enhance(img, "color")
    assert done[500:600, 400:500].mean() < 110 and done[50:150].mean() > 245


def test_clean_pictures_are_not_touched(page):
    x = page[0].get_pixmap(dpi=100)
    img = np.frombuffer(x.samples, np.uint8).reshape(x.height, x.stride)[:, :x.width * 3].reshape(
        x.height, x.width, 3)
    assert np.array_equal(scan.enhance(img, "color"), img)
    assert scan.enhance(img, "original") is img


def test_black_and_white_is_really_that(page):
    img = photo(page, "dark")[0]
    bw = scan.enhance(img, "bw")
    assert set(np.unique(bw)) <= {0, 255}


# -- the whole way -------------------------------------------------------------
@pytest.mark.parametrize("kind", ["usual", "shadow", "turned", "scan"])
def test_photo_becomes_a_searchable_page(tmp_path, page, kind):
    img, true, q = photo(page, kind, 5)
    src, out = write(tmp_path / "ფოტო.jpg", img, quality=88), str(tmp_path / "o.pdf")
    before = read(src)
    o = scan.analyse(src)
    assert o["turn"] == q
    n = scan.build_page(src, 0, dict(o, mode="gray", languages="kat+eng"), out)
    got = bench.read(out)
    assert n > 60 and bench.cer(page[1], got) < .03 and bench.recall(page[1], got) > .95
    assert os.path.getsize(out) < 480_000 and read(src) == before
    e = engine(out)
    assert e.doc[0].rect.width == 595
    # the text lies on the words it belongs to
    look = pic(e.doc[0], 144)
    for word in ("ხელშეკრულების", "კითხულობდა"):
        for x0, y0, x1, y1 in (h[0] for h in e.search(0, word)):
            box = look[int(y0 * 2):int(y1 * 2), int(x0 * 2):int(x1 * 2)]
            mid = box[int(box.shape[0] * .3):int(box.shape[0] * .8)]
            assert (mid < 120).mean() > .05, word


@pytest.mark.parametrize("mode", scan.MODES)
def test_every_mode_and_no_text(tmp_path, page, mode):
    src = write(tmp_path / "p.png", photo(page, "low")[0])
    out = str(tmp_path / "o.pdf")
    assert scan.build_page(src, 0, dict(scan.analyse(src), mode=mode, languages=""), out) == 0
    d = pymupdf.open(out)
    assert d.page_count == 1 and not d[0].get_text().strip() and len(d[0].get_images()) == 1


def test_join_order_and_metadata(tmp_path, page):
    src, parts = write(tmp_path / "p.png", photo(page, "low")[0]), []
    for k in range(3):
        parts.append(str(tmp_path / f"{k}.pdf"))
        scan.build_page(src, 0, {"mode": "gray", "turn": k}, parts[-1])
    out = str(tmp_path / "all.pdf")
    assert scan.join(parts, out, "სათაური") == 3
    d = pymupdf.open(out)
    assert d.metadata["title"] == "სათაური" and d[0].rect.width < d[1].rect.width
    with pytest.raises(EngineError):
        scan.join([], out)


def test_scanned_pdf_and_password(tmp_path, page):
    x = page[0].get_pixmap(dpi=150)
    d = pymupdf.open()
    for _ in range(2):
        p = d.new_page()
        p.insert_image(p.rect, pixmap=x)
    path = str(tmp_path / "s.pdf")
    d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with pytest.raises(EngineError, match="needs_password"):
        scan.count_pages(path)
    assert scan.count_pages(path, "u") == {"pages": 2, "text": False}
    assert scan.load(path, 1, "u").shape[2] == 3
    assert scan.count_pages(build(tmp_path / "t.pdf"))["text"] is False  # too little to matter
    long = pymupdf.open()
    long.new_page().insert_text((72, 72), "a real page with quite a lot of text on it, see")
    long.save(str(tmp_path / "l.pdf"))
    assert scan.count_pages(str(tmp_path / "l.pdf"))["text"] is True


def test_exif_frames_alpha_depth_and_heic(tmp_path, page):
    img = np.zeros((300, 200, 3), np.uint8)
    img[:50] = (255, 0, 0)
    ex = Image.Exif()
    ex[0x0112] = 6  # turn 90 degrees clockwise to see it right
    src = write(tmp_path / "e.jpg", img, exif=ex, quality=95)
    got = scan.load(src)
    assert got.shape[:2] == (200, 300) and got[100, 290, 0] > 200 > got[100, 10, 0]
    frames = [Image.fromarray(img), Image.fromarray(img[:150])]
    frames[0].save(tmp_path / "m.tif", save_all=True, append_images=frames[1:])
    assert scan.count_pages(str(tmp_path / "m.tif"))["pages"] == 2
    assert scan.load(str(tmp_path / "m.tif"), 1).shape[:2] == (150, 200)
    Image.new("RGBA", (60, 60), (0, 0, 0, 0)).save(tmp_path / "a.png")
    assert scan.load(str(tmp_path / "a.png")).min() == 255
    Image.fromarray(np.full((60, 60), 40000, np.uint16)).save(tmp_path / "d.png")
    assert 140 < scan.load(str(tmp_path / "d.png")).mean() < 170
    import pillow_heif
    pillow_heif.register_heif_opener()
    Image.fromarray(photo(page, "low")[0]).save(tmp_path / "h.heic")
    assert scan.load(str(tmp_path / "h.heic")).shape[2] == 3
    assert convert.make_pdf([str(tmp_path / "h.heic")], str(tmp_path / "h.pdf"))["pages"] == 1


@pytest.mark.parametrize("name,data,code", [
    ("x.png", b"not a picture", "bad_image"), ("x.jpg", b"", "bad_image"),
    ("x.heic", b"....ftypheic junk", "bad_image"), ("x.docx", b"x", "bad_type"),
    ("x.pdf", b"junk", "damaged")])
def test_bad_files(tmp_path, name, data, code):
    p = tmp_path / name
    p.write_bytes(data)
    with pytest.raises(EngineError, match=code):
        scan.count_pages(str(p))


def test_truncated_and_huge_pictures(tmp_path, page, monkeypatch):
    src = write(tmp_path / "p.jpg", photo(page, "low")[0], quality=85)
    data = read(src)
    (tmp_path / "t.jpg").write_bytes(data[:len(data) // 2])
    with pytest.raises(EngineError, match="bad_image"):
        scan.load(str(tmp_path / "t.jpg"))
    monkeypatch.setattr(scan, "MAX_PIXELS", 10_000)
    with pytest.raises(EngineError, match="too_big"):
        scan.load(src)
    monkeypatch.undo()
    Image.new("RGB", (5, 5)).save(tmp_path / "s.png")
    with pytest.raises(EngineError, match="bad_image"):
        scan.load(str(tmp_path / "s.png"))


def test_unknown_mode_and_language_are_refused(tmp_path, page):
    src = write(tmp_path / "p.png", photo(page, "low")[0])
    with pytest.raises(EngineError, match="bad_op"):
        scan.build_page(src, 0, {"mode": "x"}, str(tmp_path / "o.pdf"))
    with pytest.raises(EngineError, match="no_language"):
        scan.build_page(src, 0, {"mode": "gray", "languages": "xyz"}, str(tmp_path / "o.pdf"))


# -- window ----------------------------------------------------------------------
@pytest.fixture
def dialog(win, tmp_path, page):  # noqa: F811
    paths = [write(tmp_path / f"ფოტო {k}.png", photo(page, "low", k)[0]) for k in (1, 2)]
    dlg = scan_dialog.ScanDialog(win, paths)
    dlg.show()
    wait(lambda: len(dlg.pages) == 2 and all(p["found"] for p in dlg.pages), 60)
    wait(lambda: dlg.current and dlg.current["after"] is not None, 30)
    yield dlg
    dlg.reject()


def stable(dlg):
    wait(lambda: not dlg._timer.isActive() and not dlg.client._pending
         and dlg.client._current is None, 40)


def test_dialog_creates_a_searchable_pdf(dialog, tmp_path, page, monkeypatch):
    out = str(tmp_path / "შედეგი")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    assert dialog.go.isEnabled() and dialog.before.corners != scan_dialog.FULL
    dialog.shift(1)  # the first page goes last
    dialog.mode.setCurrentIndex(1)
    dialog.create()
    assert dialog.result_path == out + ".pdf"
    e = engine(out + ".pdf")
    assert e.doc.page_count == 2 and e.search(0, "ხელშეკრულების") and e.search(1, "ხელშეკრულების")
    assert not [f for f in os.listdir(tmp_path) if "furceli" in f]


def test_dialog_corner_dragging(dialog):
    stable(dialog)
    v = dialog.before
    old = v.corners
    p = v._pts()[0].toPoint()
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    def drag(to):
        QTest.mousePress(v, Qt.MouseButton.LeftButton, pos=v._pts()[0].toPoint())
        QApplication.sendEvent(v, QMouseEvent(
            QEvent.Type.MouseMove, QPointF(to), v.mapToGlobal(QPointF(to)), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QTest.mouseRelease(v, Qt.MouseButton.LeftButton, pos=to)
    drag(p + QPointF(14, 10).toPoint())
    assert dialog.current["o"]["corners"][0] != old[0]
    assert dialog.current["o"]["corners"][1:] == tuple(old[1:])
    kept = dialog.current["o"]["corners"]
    drag(v._pts()[2].toPoint() + QPointF(10, 10).toPoint())  # would turn the sheet inside out
    assert dialog.current["o"]["corners"] == kept and v.corners == kept
    dialog.full.click()
    assert dialog.current["o"]["corners"] is None
    dialog.auto.click()
    wait(lambda: dialog.current["o"]["corners"] is not None, 40)
    assert np.allclose(dialog.current["o"]["corners"], old)


def test_dialog_turn_mode_and_removal(dialog):
    stable(dialog)
    a = dialog.current["after"]
    dialog.right.click()
    wait(lambda: dialog.current["after"] is not None
         and dialog.current["after"].width() > dialog.current["after"].height(), 30)
    assert a.width() < a.height()
    dialog.left.click()
    dialog.mode.setCurrentIndex(2)
    stable(dialog)
    img = dialog.current["after"]
    assert img.pixelColor(img.width() // 2, 4).saturation() < 12
    dialog.remove()
    assert len(dialog.pages) == 1 == dialog.list.count()
    dialog.remove()
    assert not dialog.go.isEnabled() and dialog.before.image is None


def test_dialog_never_overwrites_a_source(dialog, monkeypatch, tmp_path):
    told = []
    src = dialog.pages[0]["path"]
    pdf = str(tmp_path / "s.pdf")
    pymupdf.open(blank(tmp_path)).save(pdf)
    dialog.pages[0]["path"] = pdf
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (pdf, ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    before = read(pdf)
    dialog.create()
    assert told and read(pdf) == before and dialog.result_path is None
    dialog.pages[0]["path"] = src


def test_dialog_cancel_and_failure_leave_nothing(dialog, monkeypatch, tmp_path):
    out = str(tmp_path / "o.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (out, ""))
    with monkeypatch.context() as m:
        m.setattr(QProgressDialog, "wasCanceled", lambda s: True)
        dialog.create()
    assert dialog.result_path is None and not os.path.exists(out)
    stable(dialog)
    assert all(p["after"] is not None for p in dialog.pages)
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    os.remove(dialog.pages[1]["path"])
    dialog.create()
    assert told and not os.path.exists(out)
    assert not [f for f in os.listdir(tmp_path) if "furceli" in f]


def test_dialog_bad_file_and_password(win, tmp_path, page, monkeypatch):  # noqa: F811
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"x")
    told = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: told.append(a[-1]))
    x = page[0].get_pixmap(dpi=60)
    d = pymupdf.open()
    p = d.new_page()
    p.insert_image(p.rect, pixmap=x)
    locked = str(tmp_path / "l.pdf")
    d.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("u", True))
    warned = []
    monkeypatch.setattr(scan_dialog, "warn_unprotected", lambda *a: not warned.append(1))
    dlg = scan_dialog.ScanDialog(win)
    dlg.add_paths([str(bad), locked])
    assert len(told) == 1 and len(dlg.pages) == 1 and dlg.pages[0]["password"] == "u"
    assert warned
    dlg.reject()


def test_pdf_with_text_is_asked_about(win, tmp_path, monkeypatch):  # noqa: F811
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "a real page with quite a lot of text on it, see")
    path = str(tmp_path / "t.pdf")
    d.save(path)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(1) or NO)
    dlg = scan_dialog.ScanDialog(win)
    dlg.add_paths([path])
    assert asked and not dlg.pages
    dlg.reject()


def test_copy_protected_pdf_is_refused(tmp_path, page):
    x = page[0].get_pixmap(dpi=60)
    d = pymupdf.open()
    p = d.new_page()
    p.insert_image(p.rect, pixmap=x)
    path = str(tmp_path / "r.pdf")
    d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o",
           permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ASSEMBLE)
    for f in (scan.count_pages, scan.load, scan.analyse):
        with pytest.raises(EngineError, match="not_allowed"):
            f(path)


def test_batch_does_not_turn_text_into_pictures(win, tmp_path):  # noqa: F811
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "a real page with quite a lot of text on it, see")
    path = str(tmp_path / "t.pdf")
    d.save(path)
    out = tmp_path / "out"
    out.mkdir()
    dlg = BatchDialog(win)
    dlg.list.addItem(path)
    dlg.scan.setChecked(True)
    dlg.run(str(out))
    assert dlg.report[0][1] and not os.listdir(out)
    dlg.reject()


def test_window_offers_the_open_scan(win, tmp_path, page, monkeypatch):  # noqa: F811
    x = page[0].get_pixmap(dpi=80)
    d = pymupdf.open()
    p = d.new_page()
    p.insert_image(p.rect, pixmap=x)
    path = str(tmp_path / "s.pdf")
    d.save(path)
    open_doc(win, path)
    got = []

    class Fake:
        result_path = None

        def __init__(self, parent, paths):
            got.append(paths)

        def exec(self):
            return False
    monkeypatch.setattr("furceli.window.ScanDialog", Fake)
    win.a_scan.trigger()
    open_doc(win, build(tmp_path / "t.pdf"))
    assert got == [[path]]


def test_batch_cleans_photos(win, tmp_path, page):  # noqa: F811
    src = write(tmp_path / "ფოტო.jpg", photo(page, "low")[0], quality=88)
    before = read(src)
    out = tmp_path / "out"
    out.mkdir()
    dlg = BatchDialog(win)
    dlg.list.addItem(src)
    dlg.scan.setChecked(True)
    dlg.run(str(out))
    assert dlg.report == [(src, "")] and read(src) == before
    assert engine(str(out / "ფოტო.pdf")).search(0, "ხელშეკრულების")
    dlg.scan.setChecked(False)
    dlg.run(str(out))
    d = pymupdf.open(str(out / "ფოტო (2).pdf"))
    assert d.page_count == 1 and not d[0].get_text().strip()
    dlg.reject()
