"""What a click or a box of the objects tool selects for deleting (HANDOFF.md, item 9).

Items come from the engine's erasable(): one per drawn shape (a hatch of many small pieces
is one), per text line, per picture and per date. Everything is as shown on the page."""
from __future__ import annotations

import math

FILTER = {"path": "lines", "text": "text", "image": "images", "date": "images"}


def items(data):
    out, hatches = [], {}
    for o in data.get("paths", ()):
        ref = (o["id"], tuple(o["rect"]))
        if o.get("group", -1) >= 0:
            h = hatches.get(o["group"])
            if h is None:
                h = hatches[o["group"]] = {"kind": "path", "rect": tuple(o["rect"]), "segs": [],
                                           "fill": True, "hatch": True, "ids": [], "shared": False}
                out.append(h)
            h["ids"].append(ref)
            h["segs"] = h["segs"] + list(o["segs"])
            r = h["rect"]
            h["rect"] = (min(r[0], o["rect"][0]), min(r[1], o["rect"][1]),
                         max(r[2], o["rect"][2]), max(r[3], o["rect"][3]))
            h["shared"] = h["shared"] or o.get("shared", False)
            continue
        out.append({"kind": "path", "rect": tuple(o["rect"]), "segs": o["segs"],
                    "fill": o["fill"], "hatch": False, "ids": [ref],
                    "shared": o.get("shared", False)})
    for ln in data.get("lines", ()):
        out.append({"kind": "text", "rect": tuple(ln["rect"]), "line": (ln["index"], ln["text"])})
    for o in data.get("images", ()):
        out.append({"kind": "image", "rect": tuple(o["rect"]), "ref": (o["xref"], tuple(o["rect"]))})
    for o in data.get("dates", ()):
        out.append({"kind": "date", "rect": tuple(o["rect"]), "ref": (o["xref"], tuple(o["rect"]))})
    return out


def _seg_dist(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = dx * dx + dy * dy
    t = 0.0 if n < 1e-12 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / n))
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


def _area(r):
    return max(r[2] - r[0], 0.0) * max(r[3] - r[1], 0.0)


def hit(items, x, y, tol, page):
    """The item a click takes: the text, picture or date the point is in (they lie over the
    drawing); else the nearest line within tol; else the smallest filled shape or hatch. A shape as big as most of the page (a
    white background, a sheet frame's fill) is never taken by a click."""
    big = 0.6 * page[0] * page[1]
    best = None
    for it in items:
        r = it["rect"]
        if not (r[0] - tol <= x <= r[2] + tol and r[1] - tol <= y <= r[3] + tol):
            continue
        area = _area(r)
        if it["kind"] == "path":
            d = None
            for s in it["segs"]:
                for a, b in zip(s, s[1:]):
                    v = _seg_dist(x, y, a, b)
                    if d is None or v < d:
                        d = v
            if not it["segs"]:  # too many points to send: its box stands for it
                d = 0.0 if r[0] <= x <= r[2] and r[1] <= y <= r[3] else tol
            near = d is not None and d <= tol
            if near and not (it["fill"] and area >= big):  # a page-sized fill's edge: no
                key = (0, d, area)
            elif (it["fill"] or it["hatch"]) and area < big and r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                key = (1, 0.0, area)
            else:
                continue
        else:  # text, pictures and dates lie over the drawing: inside their box, they win
            if not (r[0] <= x <= r[2] and r[1] <= y <= r[3]):
                continue
            key = (-1, 0.0, area)
        if best is None or key < best[0]:
            best = (key, it)
    return best[1] if best else None


def _inside(r, box):
    return r[0] >= box[0] and r[1] >= box[1] and r[2] <= box[2] and r[3] <= box[3]


def _touches(r, box):
    return r[0] <= box[2] and r[2] >= box[0] and r[1] <= box[3] and r[3] >= box[1]


def _cross(a, b, c, d):
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (side(a, b, c) * side(a, b, d) <= 0) and (side(c, d, a) * side(c, d, b) <= 0)


def _seg_touches(a, b, box):
    x0, y0, x1, y1 = box
    if x0 <= a[0] <= x1 and y0 <= a[1] <= y1 or x0 <= b[0] <= x1 and y0 <= b[1] <= y1:
        return True
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return any(_cross(a, b, corners[i], corners[(i + 1) % 4]) for i in range(4))


def in_box(items, box, crossing):
    """Left to right (crossing False): what lies wholly inside the box. Right to left: also
    what it touches; a shape is touched by its own lines (or, filled, by its area)."""
    box = (min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3]))
    out = []
    for it in items:
        r = it["rect"]
        if _inside(r, box):
            out.append(it)
        elif crossing and _touches(r, box):
            if it["kind"] != "path" or it["fill"] or not it["segs"]:
                out.append(it)
            elif any(_seg_touches(a, b, box) for s in it["segs"] for a, b in zip(s, s[1:])):
                out.append(it)
    return out


def counts(items):
    out = {"lines": 0, "text": 0, "images": 0}
    for it in items:
        out[FILTER[it["kind"]]] += 1
    return out


def only(items, kinds):
    """The items of the kinds left on ("lines", "text", "images")."""
    return [it for it in items if FILTER[it["kind"]] in kinds]


def operation(pno, chosen):
    paths, lines, images, dates = [], [], [], []
    for it in chosen:
        if it["kind"] == "path":
            paths += it["ids"]
        elif it["kind"] == "text":
            lines.append(it["line"])
        elif it["kind"] == "image":
            images.append(it["ref"])
        else:
            dates.append(it["ref"])
    return ("erase", pno, paths, lines, images, dates)
