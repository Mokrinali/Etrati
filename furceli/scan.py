"""Photos and scans into clean, searchable pages. Runs in an engine process.

A page goes through: load -> find the sheet -> flatten the perspective -> turn upright ->
straighten the lines -> even out the light -> recognise the text -> PDF.
Pictures are RGB uint8 arrays; corners are fractions of the picture, in the order
top-left, top-right, bottom-right, bottom-left as the sheet lies in the picture.
"""
from __future__ import annotations

import io
import math
import os
import re

import numpy as np

from .convert_names import IMAGES
from .engine import EngineError

MAX_PIXELS = 130_000_000      # refuse picture bombs
MAX_SIDE = 4200               # enough for 300 dpi A3
WORK = 900                    # long side when looking for the sheet
A4 = math.sqrt(2)
LETTER = 11 / 8.5
MODES = ("color", "gray", "bw", "original")


def _cv():
    import cv2
    cv2.setNumThreads(max((os.cpu_count() or 2) // 2, 1))
    return cv2


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def count_pages(path, password=""):
    """How many pages or frames a file has, and whether it has text already."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        doc = _pdf(path, password)
        text = any(len(doc[i].get_text().strip()) > 40 for i in range(doc.page_count))
        return {"pages": doc.page_count, "text": text}
    if ext not in IMAGES:
        raise EngineError("bad_type")
    with _open_image(path) as im:
        n = getattr(im, "n_frames", 1) if im.format in ("TIFF", "GIF") else 1
        return {"pages": min(n, 500), "text": False}


def _open_pdf(path, password):
    """Reading the text of a page makes it copyable, so the owner must allow copying."""
    import pymupdf
    from .engine import _open_source
    doc = _open_source(path, password)
    if not doc.permissions & pymupdf.PDF_PERM_COPY:
        doc.close()
        raise EngineError("not_allowed")
    return doc


_cache: dict = {}  # the one PDF being scanned


def _pdf(path, password):
    """The PDF being scanned, read once.

    Every page used to read and parse the whole file again. The copy is kept in memory, so
    the file itself stays free; a file changed since is read anew.
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise EngineError("not_found")
    except OSError:
        raise EngineError("no_access")
    key = (os.path.abspath(path), password, st.st_mtime_ns, st.st_size)
    if _cache.get("key") != key:
        old = _cache.pop("doc", None)
        _cache.clear()
        if old is not None:
            old.close()
        _cache["doc"], _cache["key"] = _open_pdf(path, password), key
    return _cache["doc"]


def _open_image(path):
    from PIL import Image, UnidentifiedImageError
    if path.lower().endswith((".heic", ".heif")):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise EngineError("bad_type")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(path)
        if im.width * im.height > MAX_PIXELS:
            raise EngineError("too_big")
        return im
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise EngineError("too_big")
    except FileNotFoundError:
        raise EngineError("not_found")
    except (UnidentifiedImageError, OSError, ValueError):
        raise EngineError("bad_image")


def load(path, index=0, password=""):
    """One page as an RGB array, the right way up for its EXIF flag."""
    from PIL import Image, ImageOps
    if path.lower().endswith(".pdf"):
        import pymupdf
        doc = _pdf(path, password)
        if not 0 <= index < doc.page_count:
            raise EngineError("bad_image")
        page = doc.load_page(index)
        w, h = page.rect.width, page.rect.height
        if min(w, h) <= 0:
            raise EngineError("bad_image")
        k = min(300 / 72, MAX_SIDE / max(w, h))
        if min(w, h) * k < 16:
            raise EngineError("bad_image")
        pix = page.get_pixmap(matrix=pymupdf.Matrix(k, k), colorspace=pymupdf.csRGB,
                              alpha=False)
        return np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height, pix.stride)[:, :pix.width * 3].reshape(pix.height, pix.width, 3).copy()
    with _open_image(path) as im:
        try:
            if getattr(im, "n_frames", 1) > 1:
                im.seek(min(index, im.n_frames - 1))
                if im.width * im.height > MAX_PIXELS:  # every frame has a size of its own
                    raise EngineError("too_big")
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P", "PA"):
                im = im.convert("RGBA")
                flat = Image.new("RGB", im.size, "white")
                flat.paste(im, mask=im.split()[-1])
                im = flat
            if im.mode.startswith("I;16") or im.mode == "I":
                im = im.point(lambda v: v / 256).convert("L")
            im = im.convert("RGB")
            if max(im.size) > MAX_SIDE:
                im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            if min(im.size) < 16:
                raise EngineError("bad_image")
            return np.asarray(im).copy()
        except (OSError, SyntaxError, ValueError):
            raise EngineError("bad_image")


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
FULL = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


def _order(pts):
    """Corners clockwise from the top-left one."""
    pts = np.asarray(pts, np.float64).reshape(4, 2)
    c = pts.mean(0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]
    first = int(np.argmin(pts.sum(1)))
    return np.roll(pts, -first, axis=0)


def _score(quad, lum, edges, w, h):
    """How much a quadrilateral looks like a sheet; 0 when it cannot be one."""
    cv2 = _cv()
    area = cv2.contourArea(quad.astype(np.float32))
    share = area / (w * h)
    if share < .12 or not cv2.isContourConvex(quad.astype(np.float32).reshape(-1, 1, 2)):
        return 0.0
    for i in range(4):  # angles of a sheet stay well between 45 and 135 degrees
        a, b, c = quad[i - 1] - quad[i], quad[(i + 1) % 4] - quad[i], 0
        n = np.linalg.norm(a) * np.linalg.norm(b)
        if n < 1:
            return 0.0
        c = abs(float(a @ b) / n)
        if c > .62:
            return 0.0
    sides = [np.linalg.norm(quad[i] - quad[(i + 1) % 4]) for i in range(4)]
    if min(sides) < .15 * max(w, h):
        return 0.0
    # real border: edges along the sides, brighter inside than outside
    along, step, free, frees = [], [], 0, []
    center = quad.mean(0)
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        t = np.linspace(.06, .94, 60)[:, None]
        p = a + (b - a) * t
        inward = center - p
        inward /= np.linalg.norm(inward, axis=1, keepdims=True) + 1e-9

        def sample(img, q):
            x = np.clip(q[:, 0].round().astype(int), 0, w - 1)
            y = np.clip(q[:, 1].round().astype(int), 0, h - 1)
            return img[y, x].astype(np.float64)
        on = np.max([sample(edges, p + inward * k) for k in (-3, -1.5, 0, 1.5, 3)], axis=0)
        border = ((p[:, 0] < 3) | (p[:, 0] > w - 4) | (p[:, 1] < 3) | (p[:, 1] > h - 4)).mean()
        along.append(max((on > 0).mean(), .75 * border))
        diff = sample(lum, p + inward * 9) - sample(lum, p - inward * 9)
        step.append(np.median(diff) if border < .5 else 6.0)
        free += border < .5
        frees.append(border < .5)
    if min(along) < .22 or free < 1:
        return 0.0
    real = [(a, d) for a, d, f in zip(along, step, frees) if f]
    if len(real) == 1 and (real[0][0] < .7 or real[0][1] < 14):  # a single edge must be clear
        return 0.0
    bright = np.clip(np.mean(step) / 40, 0, 1) * (np.min(step) > -18)
    return float((np.mean(along) ** 1.5 + .35 * np.min(along)) * (.45 + .55 * bright)
                 * min(share, .9) ** .35)


def find_sheet(img):
    """Corners of the sheet as fractions, or None when the picture is the sheet itself."""
    cv2 = _cv()
    H, W = img.shape[:2]
    k = min(WORK / max(H, W), 1.0)
    small = cv2.resize(img, (max(int(W * k), 8), max(int(H * k), 8)), interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB)
    lum = cv2.GaussianBlur(lab[..., 0], (5, 5), 0)
    # the text inside must not lead the search: close it away
    size = max(int(max(h, w) * .03), 9) | 1
    plain = cv2.medianBlur(cv2.morphologyEx(
        lum, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8)), 7)
    chroma = [cv2.medianBlur(lab[..., i], 9) for i in (1, 2)]
    edges = cv2.Canny(plain, 12, 36)
    for c in chroma:
        edges |= cv2.Canny(c, 10, 30)
    faint = cv2.Canny(cv2.GaussianBlur(plain, (0, 0), 1.5), 4, 11) | edges
    thick = cv2.dilate(edges, np.ones((3, 3), np.uint8))

    found = []

    def offer(contour):
        hull = cv2.convexHull(contour)
        if cv2.contourArea(hull) < .12 * w * h:
            return
        peri = cv2.arcLength(hull, True)
        for eps in (.015, .025, .04, .06, .09):
            q = cv2.approxPolyDP(hull, eps * peri, True)
            if len(q) == 4:
                found.append(_order(q))
                break
        found.append(_order(cv2.boxPoints(cv2.minAreaRect(hull))))

    masks = []
    otsu = cv2.threshold(plain, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    masks.append(otsu)
    for pct in (35, 50, 65):
        masks.append((plain > np.percentile(plain, pct)).astype(np.uint8) * 255)
    # like the colour in the middle
    mid = lab[h // 3:2 * h // 3, w // 3:2 * w // 3].reshape(-1, 3).astype(np.float32)
    ref = np.percentile(mid, 60, axis=0)
    dist = np.abs(cv2.merge([plain, *chroma]).astype(np.float32) - ref) @ np.float32([.6, 1.6, 1.6])
    for lim in (18, 30, 45):
        masks.append((dist < lim).astype(np.uint8) * 255)
    kern = np.ones((7, 7), np.uint8)
    for m in masks:
        m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, kern), cv2.MORPH_CLOSE, kern)
        cs = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        for c in sorted(cs, key=cv2.contourArea, reverse=True)[:2]:
            offer(c)
    cs = cv2.findContours(thick, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]
    for c in sorted(cs, key=lambda c: cv2.contourArea(cv2.convexHull(c)), reverse=True)[:6]:
        offer(c)
    found += _line_quads(edges, w, h)
    found += _line_quads(faint, w, h)

    thin = cv2.dilate(faint, np.ones((3, 3), np.uint8))
    ink = cv2.resize(_ink(small), (w, h), interpolation=cv2.INTER_NEAREST)
    ranked = []
    for q in found:
        q = np.clip(q, 0, [w - 1, h - 1])
        ranked.append((max(_score(q, lum, thick, w, h), .82 * _score(q, lum, thin, w, h)), q))
    best = None
    for sc, q in sorted(ranked, key=lambda r: -r[0])[:40]:
        if sc < .34:
            break
        if sc > .8 or _holds(q, ink):  # a sheet this clear needs no second opinion
            best = q
            break
    if best is None:
        return None
    best = _refine(best, plain, w, h)
    frac = best / [w - 1, h - 1]
    if np.abs(frac - np.asarray(FULL)).max() < .02:  # the sheet fills the picture
        return None
    return tuple((float(x), float(y)) for x, y in np.clip(frac, 0, 1))


def _holds(quad, ink):
    """The sheet must hold the writing: no line of text may cross its edge."""
    cv2 = _cv()
    h, w = ink.shape
    inside = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(inside, quad.round().astype(np.int32), 255)
    total = int((ink > 0).sum())
    if total < 40:
        return True
    if (cv2.bitwise_and(ink, inside) > 0).sum() < .86 * total:
        return False
    # words as blobs: one that lies on both sides is being cut
    blob = cv2.dilate(ink, np.ones((3, max(int(w * .012), 5)), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(blob, connectivity=8)
    shrunk = cv2.erode(inside, np.ones((7, 7), np.uint8), borderValue=255)
    grown = cv2.dilate(inside, np.ones((7, 7), np.uint8))
    out = np.bincount(lab[(grown == 0)], minlength=n)
    ins = np.bincount(lab[(shrunk > 0)], minlength=n)
    area = np.maximum(st[:, cv2.CC_STAT_AREA], 1)
    crossed = (out > .12 * area) & (ins > .12 * area) & (area > 25)
    crossed[0] = False
    return int(crossed.sum()) <= max(1, int(.03 * (n - 1)))


def _line_quads(edges, w, h):
    """Sheets made of four long straight edges, even when the corners are not visible."""
    cv2 = _cv()
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, 60, minLineLength=int(.2 * max(w, h)),
                            maxLineGap=int(.035 * max(w, h)))
    if lines is None:
        return []
    hor, ver = [], []
    for x0, y0, x1, y1 in np.asarray(lines).reshape(-1, 4).astype(float):
        dx, dy = x1 - x0, y1 - y0
        n = math.hypot(dx, dy)
        row = (n, x0, y0, x1, y1)
        (hor if abs(dx) > abs(dy) else ver).append(row)

    def pick(rows, at):
        rows = sorted(rows, reverse=True)
        out = []
        for r in rows:  # distinct lines only
            if all(abs(at(r) - at(o)) > .04 * max(w, h) for o in out):
                out.append(r)
            if len(out) == 5:
                break
        return out
    hor = pick(hor, lambda r: (r[2] + r[4]) / 2)
    ver = pick(ver, lambda r: (r[1] + r[3]) / 2)
    # the sheet often runs out of the picture
    hor += [(w, 0, 0, w - 1, 0), (w, 0, h - 1, w - 1, h - 1)]
    ver += [(h, 0, 0, 0, h - 1), (h, w - 1, 0, w - 1, h - 1)]

    def cross(a, b):
        x1, y1, x2, y2 = a[1:]
        x3, y3, x4, y4 = b[1:]
        d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(d) < 1e-6:
            return None
        p, q = x1 * y2 - y1 * x2, x3 * y4 - y3 * x4
        return ((p * (x3 - x4) - (x1 - x2) * q) / d, (p * (y3 - y4) - (y1 - y2) * q) / d)
    out = []
    for i, a in enumerate(hor):
        for b in hor[i + 1:]:
            for j, c in enumerate(ver):
                for d in ver[j + 1:]:
                    pts = [cross(a, c), cross(a, d), cross(b, d), cross(b, c)]
                    if all(p and -.06 * w < p[0] < 1.06 * w and -.06 * h < p[1] < 1.06 * h
                           for p in pts):
                        out.append(_order(pts))
    return out


def _refine(quad, plain, w, h):
    """Move every side onto the strongest step near it and intersect the sides again."""
    cv2 = _cv()
    gx = cv2.Sobel(plain, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(plain, cv2.CV_32F, 0, 1)
    center, lines = quad.mean(0), []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        d = (b - a) / (np.linalg.norm(b - a) + 1e-9)
        n = np.array([-d[1], d[0]])
        if (center - a) @ n < 0:
            n = -n
        pts = []
        for t in np.linspace(.05, .95, 50):
            p = a + (b - a) * t
            best, at = 6.0, None
            for k in np.arange(-10, 10.5, 1):
                q = p + n * k
                x, y = int(round(q[0])), int(round(q[1]))
                if 1 <= x < w - 1 and 1 <= y < h - 1:
                    g = float(gx[y, x] * n[0] + gy[y, x] * n[1])  # brighter towards the inside
                    if g > best:
                        best, at = g, q
            if at is not None:
                pts.append(at)
        if len(pts) < 14:
            lines.append((a, d))
            continue
        vx, vy, x0, y0 = cv2.fitLine(np.float32(pts), cv2.DIST_HUBER, 0, .01, .01).ravel()
        if abs(vx * d[0] + vy * d[1]) < .985:  # it ran away along something else
            lines.append((a, d))
        else:
            lines.append((np.array([x0, y0]), np.array([vx, vy])))
    out = []
    for i in range(4):
        (p, u), (q, v) = lines[i - 1], lines[i]
        m = np.array([u, -v]).T
        if abs(np.linalg.det(m)) < 1e-6:
            return quad
        out.append(p + u * np.linalg.solve(m, q - p)[0])
    out = np.clip(np.asarray(out), 0, [w - 1, h - 1])
    if np.abs(out - quad).max() > .06 * max(w, h):
        return quad
    return out


def _aspect(quad, w, h):
    """Height / width of the sheet behind a perspective view (Zhang and He)."""
    m1, m2, m4, m3 = [np.array([x, y, 1.0]) for x, y in quad]  # tl, tr, bl, br order below
    u0, v0 = w / 2, h / 2
    try:
        k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
        k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
        n2, n3 = k2 * m2 - m1, k3 * m3 - m1
        if abs(n2[2]) < 1e-5 or abs(n3[2]) < 1e-5:
            raise ZeroDivisionError
        f2 = -(n2[0] * n3[0] - (n2[0] * n3[2] + n2[2] * n3[0]) * u0 + n2[2] * n3[2] * u0 * u0
               + n2[1] * n3[1] - (n2[1] * n3[2] + n2[2] * n3[1]) * v0
               + n2[2] * n3[2] * v0 * v0) / (n2[2] * n3[2])
        if not .2 * max(w, h) < math.sqrt(abs(f2)) < 6 * max(w, h) or f2 < 0:
            raise ZeroDivisionError
        a = np.array([[math.sqrt(f2), 0, u0], [0, math.sqrt(f2), v0], [0, 0, 1]])
        s = np.linalg.inv(a).T @ np.linalg.inv(a)
        return math.sqrt((n3 @ s @ n3) / (n2 @ s @ n2))
    except (ZeroDivisionError, ValueError, np.linalg.LinAlgError, FloatingPointError):
        q = np.asarray(quad)
        wide = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
        tall = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
        return tall / max(wide, 1e-6)


def flatten(img, corners):
    """The sheet seen from straight above."""
    cv2 = _cv()
    h, w = img.shape[:2]
    quad = np.asarray(corners, np.float64) * [w - 1, h - 1]
    if np.abs(np.asarray(corners) - np.asarray(FULL)).max() < .002:
        return img
    ratio = _aspect(quad, w, h)
    plain = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / max(
        np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3]), 1e-6)
    if not .55 < ratio / plain < 1.8:  # the estimate is unstable when the view is almost flat
        ratio = plain
    known = min((A4, 1 / A4, LETTER, 1 / LETTER), key=lambda r: abs(ratio / r - 1))
    if abs(ratio / known - 1) < .09:
        ratio = known
    wide = max(np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[3]))
    tall = max(np.linalg.norm(quad[3] - quad[0]), np.linalg.norm(quad[2] - quad[1]))
    W = max(wide, tall / ratio)
    H = W * ratio
    k = min(MAX_SIDE / max(W, H), 1.0)
    W, H = max(int(W * k), 16), max(int(H * k), 16)
    m = cv2.getPerspectiveTransform(quad.astype(np.float32),
                                    np.float32([[0, 0], [W, 0], [W, H], [0, H]]))
    return cv2.warpPerspective(img, m, (W, H), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def turn(img, quarter):
    """Rotate clockwise by 90-degree steps."""
    return np.ascontiguousarray(np.rot90(img, -(quarter % 4)))


# ---------------------------------------------------------------------------
# light
# ---------------------------------------------------------------------------
def _background(ch):
    """The paper's own brightness everywhere: large-scale maximum, without the writing."""
    cv2 = _cv()
    h, w = ch.shape
    k = min(600 / max(h, w), 1.0)
    s = cv2.resize(ch, (max(int(w * k), 4), max(int(h * k), 4)), interpolation=cv2.INTER_AREA)
    n = max(int(max(s.shape) * .028), 7) | 1
    bg = cv2.dilate(s, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n)))
    bg = cv2.medianBlur(bg, min(n * 2 + 1, 41) | 1)
    bg = cv2.GaussianBlur(bg, (0, 0), n * .6)
    return cv2.resize(bg, (w, h), interpolation=cv2.INTER_CUBIC)


def even_light(img):
    """Remove shadows and colour casts; paper becomes white, pictures keep their shape."""
    cv2 = _cv()
    lum = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    ref = _background(lum).astype(np.float32)
    paper = max(float(np.percentile(ref, 90)), 1.0)
    # A big area darker than any shadow is a picture: it keeps its darkness and its colour.
    t = np.clip((ref / paper - .25) / .15, 0, 1)
    t = t * t * (3 - 2 * t)
    out = np.empty_like(img)
    for i in range(3):
        bg = _background(img[..., i]).astype(np.float32)
        bg = bg * t + float(np.percentile(bg, 90)) * (1 - t)
        out[..., i] = np.clip(img[..., i].astype(np.float32) / np.maximum(bg, 30.0) * 255,
                              0, 255).astype(np.uint8)
    return out


def _levels(a, low, high):
    table = np.clip((np.arange(256) - low) * 255.0 / max(high - low, 1), 0, 255).astype(np.uint8)
    return table[a]


def needs_light(img):
    """False for screenshots and clean scans, which would only lose by it."""
    cv2 = _cv()
    lum = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    bg = _background(lum)
    lo, hi = np.percentile(bg, (4, 96))
    return bool(hi < 244 or hi - lo > 14)


def enhance(img, mode):
    """Cleaned picture: RGB for "color", gray for "gray", 0/255 for "bw"."""
    cv2 = _cv()
    if mode == "original":
        return img
    light = needs_light(img)
    flat = even_light(img) if light else img
    if mode == "color":
        if not light:
            return flat
        gray = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY)
        low = min(float(np.percentile(gray, .5)), 90)
        return _levels(flat, low * .8, 236)
    gray = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY)
    if light:
        low = min(float(np.percentile(gray, .5)), 100)
        gray = _levels(gray, low * .85, 232)
        soft = cv2.GaussianBlur(gray, (0, 0), 1.1)
        gray = cv2.addWeighted(gray, 1.5, soft, -.5, 0)
    if mode == "gray":
        return gray
    n = max(int(max(gray.shape) * .022), 15) | 1
    bw = cv2.adaptiveThreshold(cv2.GaussianBlur(gray, (3, 3), 0), 255,
                               cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, n, 14)
    count, lab, stats, _ = cv2.connectedComponentsWithStats(255 - bw, connectivity=8)
    tiny = max(int(max(gray.shape) / 1200), 1) ** 2 * 3
    drop = np.where(stats[:, cv2.CC_STAT_AREA] < tiny)[0]
    drop = drop[drop != 0]
    if len(drop):
        mask = np.zeros(count, bool)
        mask[drop] = True
        bw[mask[lab]] = 255
    return bw


# ---------------------------------------------------------------------------
# text direction
# ---------------------------------------------------------------------------
def _ink(img, side=1100):
    """Small black-on-white mask of the writing, without big pictures and long lines."""
    cv2 = _cv()
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    k = min(side / max(gray.shape), 1.0)
    gray = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    bg = np.maximum(_background(gray).astype(np.float32), 40)
    gray = np.clip(gray / bg * 255, 0, 255).astype(np.uint8)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    n, lab, st, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    big = max(gray.shape) * .06
    ok = ((st[:, cv2.CC_STAT_AREA] >= 4) & (st[:, cv2.CC_STAT_WIDTH] < big)
          & (st[:, cv2.CC_STAT_HEIGHT] < big))
    ok[0] = False
    return ok[lab].astype(np.uint8) * 255


def _profile(ink, angle):
    """How clearly rows of text stand out when the page is turned by angle degrees."""
    cv2 = _cv()
    h, w = ink.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
    row = cv2.warpAffine(ink, m, (w, h), flags=cv2.INTER_LINEAR).sum(1, dtype=np.float64)
    return float(((row[1:] - row[:-1]) ** 2).sum())


def skew(img, limit=12.0):
    """Degrees by which the lines of text are tilted (0 when there is too little text)."""
    ink = _ink(img)
    if (ink > 0).mean() < .0015:
        return 0.0
    base = _profile(ink, 0)
    best, top = 0.0, base
    for step, reach in ((1.0, limit), (.25, 1.0), (.05, .25)):
        start = best
        for a in np.arange(start - reach, start + reach + step / 2, step):
            v = _profile(ink, a)
            if v > top:
                best, top = float(a), v
    if abs(best) > limit - .6 or top < base * 1.04:
        return 0.0
    return best


def _segments(img):
    """Nearly straight runs: text lines and ruled lines, (x0, y0, x1, y1) in pixels of a
    picture at most 1100 pixels long, with that scale."""
    cv2 = _cv()
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    k = min(1100 / max(gray.shape), 1.0)
    small = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    h, w = small.shape
    flat, rest = [], []
    ink = cv2.resize(_ink(small), (w, h), interpolation=cv2.INTER_NEAREST)
    runs = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((1, max(int(w * .022), 9)), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(runs, connectivity=8)
    for i in range(1, n):
        if st[i, 2] < .09 * w or st[i, 3] > .3 * st[i, 2] or st[i, 3] > .06 * h:
            continue
        x, y, cw, ch = st[i, :4]
        ys, xs = np.where((lab[y:y + ch, x:x + cw] == i) & (ink[y:y + ch, x:x + cw] > 0))
        if len(xs) < 25:
            continue
        # the middle of the ink in every column: steady against tall letters
        top = np.bincount(xs, ys, cw) / np.maximum(np.bincount(xs, minlength=cw), 1)
        col = np.where(np.bincount(xs, minlength=cw) > 0)[0]
        vx, vy, px, py = cv2.fitLine(np.float32(np.c_[col, top[col]]), cv2.DIST_HUBER, 0, .01,
                                     .01).ravel()
        if abs(vx) > .85:
            m = vy / vx
            flat.append((x, y + py - m * px, x + cw, y + py + m * (cw - px)))
    bg = np.maximum(_background(small).astype(np.float32), 40)
    even = np.clip(small / bg * 255, 0, 255).astype(np.uint8)
    bw = cv2.adaptiveThreshold(even, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 21, 12)
    lines = cv2.HoughLinesP(bw, 1, np.pi / 720, 70, minLineLength=int(.09 * max(w, h)),
                            maxLineGap=4)
    if lines is not None:
        for x0, y0, x1, y1 in np.asarray(lines).reshape(-1, 4).astype(float):
            if abs(x1 - x0) > 2.5 * abs(y1 - y0) and abs(x1 - x0) > .16 * w:
                flat.append((x0, y0, x1, y1) if x1 > x0 else (x1, y1, x0, y0))
            elif abs(y1 - y0) > 2.5 * abs(x1 - x0):
                rest.append((x0, y0, x1, y1) if y1 > y0 else (x1, y1, x0, y0))
    return flat, rest, w, h


def _trend(at, slope, weight, spread):
    """Robust slope = a + b * at (at in 0..1). b only when the runs are spread enough."""
    at, slope, weight = (np.asarray(v, np.float64) for v in (at, slope, weight))
    if len(at) < 3:
        return None
    a, b = np.average(slope, weights=weight), 0.0
    med = np.median(slope)
    ok = np.abs(slope - med) < .12
    if ok.sum() < 3:
        return None
    wide = np.ptp(at[ok]) > spread and ok.sum() >= 5
    w = weight * ok
    for _ in range(12):
        if wide:
            A = np.c_[np.ones_like(at), at] * np.sqrt(w)[:, None]
            a, b = np.linalg.lstsq(A, slope * np.sqrt(w), rcond=None)[0]
        else:
            a = np.average(slope, weights=w + 1e-12)
        r = np.abs(slope - a - b * at)
        s = max(1.5 * np.median(r[w > 0]), .0015)
        w = weight * ok / (1 + (r / s) ** 2)
    return float(a), float(b)


def level(img):
    """Make the writing level and the margins upright, also under perspective.

    Used on every page: after the sheet was found it only takes away what is left.
    """
    cv2 = _cv()
    flat, rest, w, h = _segments(img)
    rows = [((s[1] + s[3]) / 2 / h, (s[3] - s[1]) / (s[2] - s[0]), s[2] - s[0]) for s in flat]
    hor = _trend(*zip(*rows), .28) if len(rows) >= 3 else None
    if hor is None:
        return straighten(img, skew(img))
    a, b = hor
    if abs(a + b / 2) > .25 or abs(b) > .3:
        return straighten(img, skew(img))
    cols = [((s[0] + s[2]) / 2 / w, (s[2] - s[0]) / (s[3] - s[1]), s[3] - s[1]) for s in rest]
    ver = _trend(*zip(*cols), .3) if len(cols) >= 4 else None
    c, d = ver if ver else (-(a + b / 2), 0.0)
    if abs(c + d / 2 + a + b / 2) > .16 or abs(d) > .3:  # not at right angles: not a margin
        c, d = -(a + b / 2), 0.0
    if max(abs(a), abs(a + b), abs(c), abs(c + d)) < .002:
        return img
    # a quadrilateral whose sides run along the measured directions
    top, bottom = (a, 0.0), (a + b, float(h))
    left, right = (c, 0.0), (c + d, float(w))

    def meet(row, col):  # y = y0 + m (x - w/2);  x = x0 + t (y - h/2)
        (m, y0), (t, x0) = row, col
        y = (y0 + m * (x0 - t * h / 2 - w / 2)) / (1 - m * t)
        return x0 + t * (y - h / 2), y
    quad = np.float32([meet(top, left), meet(top, right), meet(bottom, right), meet(bottom, left)])
    H, W = img.shape[:2]
    quad *= [W / w, H / h]
    tall = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    wide = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    m = cv2.getPerspectiveTransform(quad, np.float32([[0, 0], [wide, 0], [wide, tall], [0, tall]]))
    # keep everything that was in the picture, corners included
    box = cv2.perspectiveTransform(np.float32([[[0, 0]], [[W, 0]], [[W, H]], [[0, H]]]), m)[:, 0]
    lo, hi = box.min(0), box.max(0)
    size = np.clip(hi - lo, [16, 16], [W * 1.3, H * 1.3])
    if not np.isfinite(box).all() or size[0] > W * 1.29 or size[1] > H * 1.29:
        return straighten(img, skew(img))
    m = np.array([[1, 0, -lo[0]], [0, 1, -lo[1]], [0, 0, 1]]) @ m
    edge = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    fill = tuple(float(v) for v in np.percentile(edge, 70, axis=0)) if img.ndim == 3 else \
        float(np.percentile(edge, 70))
    return cv2.warpPerspective(img, m, (int(size[0]), int(size[1])), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=fill)


def straighten(img, angle):
    cv2 = _cv()
    if abs(angle) < .12:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
    edge = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    fill = tuple(float(v) for v in np.percentile(edge, 70, axis=0)) if img.ndim == 3 else \
        float(np.percentile(edge, 70))
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=fill)


GOOD = re.compile(r"^[ა-ჿ]{3,}[.,;:!?]?$|^[A-Za-z]{3,}[.,;:!?]?$|^[Ѐ-ӿ]{3,}[.,;:!?]?$"
                  r"|^\d[\d.,/:\-]{2,}$")


def _reads(img, languages):
    """(letters in real words, their share): how well a picture reads."""
    k = 72 / 300  # one pixel of the picture stays one pixel for the reader
    words = recognise(img, img.shape[1] * k, img.shape[0] * k, languages)
    good = sum(len(w[4]) for w in words if GOOD.match(w[4]))
    return good, good / max(sum(len(w[4]) for w in words), 1)


def _dense(ink, part=.3):
    """(y0, y1, x0, x1) as fractions: the window of about `part` of the page with most writing."""
    cv2 = _cv()
    h, w = ink.shape
    if part >= 1 or min(h, w) < 40:
        return 0.0, 1.0, 0.0, 1.0
    wh, ww = max(int(h * math.sqrt(part)), 1), max(int(w * math.sqrt(part)), 1)
    s = cv2.integral((ink > 0).astype(np.uint8))
    step = max(min(h, w) // 60, 1)
    ys, xs = np.arange(0, h - wh + 1, step), np.arange(0, w - ww + 1, step)
    tot = (s[ys[:, None] + wh, xs[None, :] + ww] - s[ys[:, None], xs[None, :] + ww]
           - s[ys[:, None] + wh, xs[None, :]] + s[ys[:, None], xs[None, :]])
    i, j = np.unravel_index(int(np.argmax(tot)), tot.shape)
    return ys[i] / h, (ys[i] + wh) / h, xs[j] / w, (xs[j] + ww) / w


def upright(img, languages):
    """Quarter turns (clockwise) that put the writing upright."""
    cv2 = _cv()
    ink = _ink(img, 900)
    if (ink > 0).mean() < .0015:
        return 0
    across = max(_profile(ink, a) for a in (-2, -1, 0, 1, 2)) / ink.shape[0]
    side = np.ascontiguousarray(np.rot90(ink))
    down = max(_profile(side, a) for a in (-2, -1, 0, 1, 2)) / side.shape[0]
    # Rows and columns of tables can mislead: only a clear ratio leaves directions out.
    order = (0, 2, 1, 3) if across >= down else (1, 3, 0, 2)
    likely = order[:2] if max(across, down) > 3 * min(across, down) else order
    side = max(img.shape[:2])
    f = min(max(1500 / side, 1.0), 2.0) if side < 1500 else min(1700 / side, 1.0)
    probe = cv2.resize(enhance(img, "gray"), None, fx=f, fy=f,
                       interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
    # Upside-down writing still reads as half-plausible words (about half of them), so the
    # two ways of an axis are always compared, never one taken because it alone looks like
    # text. First on a few lines (the part with the most writing): reading the wrong way
    # round is slow, and a few lines usually settle it.
    y0, y1, x0, x1 = _dense(ink, .1)
    ph, pw = probe.shape[:2]
    part = probe[int(y0 * ph):int(y1 * ph), int(x0 * pw):int(x1 * pw)]
    shares = {}
    for pair in (order[:2], order[2:]):
        if pair[0] not in likely and shares and max(shares.values()) > .5:
            break  # what looked likely does read
        got = {q: _reads(turn(part, q), languages) for q in pair}
        shares.update({q: got[q][1] for q in pair})
        a, b = sorted(pair, key=lambda q: -got[q][0])
        (sa, ra), (sb, rb) = got[a], got[b]
        # clean text clearly ahead, or a phone photo (reads worse) twice ahead
        if sa >= 40 and ra - rb > .2 and (ra > .7 and sa > 1.4 * sb or sa > 2 * sb):
            return a
    # not settled by a few lines: the whole page, every likely way
    scores = {}
    for q in order:
        if q not in likely and scores and max(shares.get(k, 0) for k in scores) > .5:
            break
        scores[q], shares[q] = _reads(turn(probe, q), languages)
    best = max(scores, key=lambda q: (scores[q], q == 0))
    # without a clear winner the picture stays as it is
    rest = max([v for q, v in scores.items() if q != best], default=0)
    return best if best == 0 or (scores[best] >= 12 and scores[best] > 1.25 * rest) else 0


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------
def recognise(img, width, height, languages):
    """OCR words [(x0, y0, x1, y1, word)] on a page of width x height points."""
    import pymupdf
    from .features import TESSDATA, _ocr_words
    for lang in languages.split("+"):
        if not os.path.exists(os.path.join(TESSDATA, lang + ".traineddata")):
            raise EngineError("no_language")
    cv2 = _cv()
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    ok, png = cv2.imencode(".png", gray)
    if not ok:
        raise EngineError("bad_image")
    dpi = max(int(gray.shape[1] / width * 72), 72)
    tmp = pymupdf.open()
    try:
        page = tmp.new_page(width=width, height=height)
        page.insert_image(page.rect, stream=png.tobytes())
        tp = page.get_textpage_ocr(language=languages, dpi=min(dpi, 400), full=True,
                                   tessdata=TESSDATA)
        return _ocr_words(page.get_text("rawdict", textpage=tp))
    finally:
        tmp.close()


# ---------------------------------------------------------------------------
# the whole way
# ---------------------------------------------------------------------------
def analyse(path, index=0, password="", languages="kat+eng"):
    """What a page needs: corners (or None), quarter turns.

    The direction is found by reading. Without language files the page keeps its direction:
    only recognising text itself cannot do without them.
    """
    from .features import TESSDATA
    languages = "+".join(x for x in languages.split("+") if os.path.exists(
        os.path.join(TESSDATA, x + ".traineddata")))
    img = load(path, index, password)
    corners = find_sheet(img)
    flat = flatten(img, corners) if corners else img
    return {"corners": corners, "turn": upright(flat, languages) if languages else 0, "size": img.shape[1::-1]}


def _roomy(img, side=2300):
    """Small pictures are enlarged before the geometry: moving fine letters by fractions of a
    pixel would smear them."""
    cv2 = _cv()
    f = min(side / max(img.shape[:2]), 2.0)
    return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_LANCZOS4) if f > 1.1 else img


def straight(img, o):
    """Geometry only: sheet, quarter turns, tilt."""
    if o.get("corners"):
        img = flatten(img, o["corners"])
    img = turn(img, o.get("turn", 0))
    return level(img) if o.get("deskew", True) else img


def preview(path, index, o, password="", side=1000):
    """(original, result) as small RGB pictures (width, height, bytes)."""
    cv2 = _cv()
    img = load(path, index, password)
    k = min(1600 / max(img.shape[:2]), 1.0)
    work = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else img
    done = enhance(straight(work, o), o.get("mode", "color"))

    def small(a):
        f = min(side / max(a.shape[:2]), 1.0)
        a = cv2.resize(a, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else a
        a = cv2.cvtColor(a, cv2.COLOR_GRAY2RGB) if a.ndim == 2 else a
        a = np.ascontiguousarray(a)
        return a.shape[1], a.shape[0], a.tobytes()
    return small(work), small(done)


def page_size(img):
    """Points: A4 (or its turn) when the shape fits, else A4's area in the picture's shape."""
    h, w = img.shape[:2]
    r = h / w
    if abs(r / A4 - 1) < .05:
        return 595.0, 842.0
    if abs(r * A4 - 1) < .05:
        return 842.0, 595.0
    width = math.sqrt(595 * 842 / r)
    return width, width * r


def _encode(img, mode, width, dpi):
    """Picture bytes for the page, as small as the mode allows."""
    cv2 = _cv()
    want = int(width / 72 * dpi)
    if mode == "bw":
        from PIL import Image
        if img.shape[1] > want * 1.5:  # fine strokes need more pixels than shades do
            f = want * 1.5 / img.shape[1]
            img = cv2.threshold(cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA),
                                150, 255, cv2.THRESH_BINARY)[1]
        buf = io.BytesIO()
        Image.fromarray(img).convert("1", dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
        return buf.getvalue()
    if img.shape[1] > want:
        f = want / img.shape[1]
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85 if mode == "original" else 72,
                                     cv2.IMWRITE_JPEG_OPTIMIZE, 1])[1].tobytes()
    return jpg


def build_page(path, index, o, out, password=""):
    """One finished page as a PDF of its own. Returns the number of recognised words."""
    import pymupdf
    from .engine import _write
    from .features import Features
    mode = o.get("mode", "color")
    if mode not in MODES:
        raise EngineError("bad_op")
    img = straight(_roomy(load(path, index, password)), o)
    width, height = page_size(img)
    done = enhance(img, mode)
    words = []
    if o.get("languages"):
        # The gray version reads best; small pictures are enlarged to about 300 dpi.
        cv2 = _cv()
        gray = done if mode == "gray" else enhance(img, "gray")
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_RGB2GRAY)
        f = width / 72 * 300 / gray.shape[1]
        if f > 1.15:
            gray = cv2.resize(gray, None, fx=min(f, 3), fy=min(f, 3), interpolation=cv2.INTER_CUBIC)
        words = recognise(gray, width, height, o["languages"])
    eng = Features()
    eng.doc = pymupdf.open()
    try:
        page = eng.doc.new_page(width=width, height=height)
        page.insert_image(page.rect, stream=_encode(done, mode, width, o.get("dpi", 200)))
        page = None
        if words:
            eng._op_textlayer({0: words})
        _write(eng.doc, out)
    finally:
        eng.doc.close()
    return len(words)


def join(parts, out, title=""):
    import pymupdf
    from .engine import _write
    doc = pymupdf.open()
    try:
        for p in parts:
            with pymupdf.open(p) as d:
                doc.insert_pdf(d)
        if not doc.page_count:
            raise EngineError("no_pages")
        doc.set_metadata({"title": title, "producer": "ETRATI", "creator": "ETRATI"})
        _write(doc, out)
        return doc.page_count
    finally:
        doc.close()


__all__ = ["analyse", "preview", "build_page", "join", "count_pages"]
