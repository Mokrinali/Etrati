"""Measuring drawings to scale: the numbers and how a measurement is written and drawn.

A scale is metres in reality per point of the page (a point is 1/72 inch on paper). Lengths
and areas are counted in the page's own space, which a page's turn does not change, so the
zoom and the turn of the page never change a result. Without a scale, results are paper
millimetres and say so."""
from __future__ import annotations

import math

PAPER = 0.0254 / 72  # metres of paper in a point
UNITS = {"mm": (1000.0, "მმ", "მმ²"), "cm": (100.0, "სმ", "სმ²"), "m": (1.0, "მ", "მ²")}
PDF_UNITS = {"mm": ("mm", "sq mm"), "cm": ("cm", "sq cm"), "m": ("m", "sq m")}
KINDS = ("distance", "path", "area")
NO_SCALE = {"factor": PAPER, "unit": "mm", "digits": 1, "label": "", "paper": True}


def length(pts) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def area(pts) -> float:
    """Shoelace: the area a closed outline holds, whatever way round it was drawn."""
    n = len(pts)
    return abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
                   for i in range(n))) / 2


def values(kind, pts, scale):
    """(value, perimeter or None) in the scale's unit (squared for an area)."""
    s = scale or NO_SCALE
    k = s["factor"] * UNITS[s["unit"]][0]  # units per point
    if kind == "area":
        return area(pts) * k * k, (length(pts) + math.dist(pts[-1], pts[0])) * k
    return length(pts) * k, None


def label(kind, pts, scale, note=""):
    """What the measurement says: "10.00 მ", "ოთახი 1: 50.00 მ² · 30.00 მ"."""
    s = scale or NO_SCALE
    value, around = values(kind, pts, s)
    _, unit, square = UNITS[s["unit"]]
    d = int(s["digits"])
    text = f"{value:.{d}f} {square if kind == 'area' else unit}"
    if around is not None:
        text += f" · {around:.{d}f} {unit}"
    if s.get("paper"):
        text += " (ქაღალდზე)"
    return f"{note}: {text}" if note else text


def measure_dict(scale) -> str:
    """The PDF's own description of a scale (ISO 32000, 12.9): Acrobat and Bluebeam read it."""
    from pymupdf import get_pdf_str
    s = scale or NO_SCALE
    per_point = s["factor"] * UNITS[s["unit"]][0]
    u, sq = PDF_UNITS[s["unit"]]
    d = 10 ** int(s["digits"])
    return (f"<</Type/Measure/Subtype/RL/R{get_pdf_str(s.get('label') or '')}"
            f"/X[<</Type/NumberFormat/U({u})/C {per_point:.10g}/D {d}>>]"
            f"/D[<</Type/NumberFormat/U({u})/C 1/D {d}>>]"
            f"/A[<</Type/NumberFormat/U({sq})/C 1/D {d}>>]>>")


def label_place(kind, pts):
    """Where the label goes (page space, y down): the middle of a line or of a path's
    longest piece, the centre of an area."""
    if kind == "area":
        a = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                for i in range(len(pts))) / 2
        if abs(a) > 1e-6:
            cx = sum((pts[i][0] + pts[(i + 1) % len(pts)][0]) * (
                pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1])
                for i in range(len(pts))) / (6 * a)
            cy = sum((pts[i][1] + pts[(i + 1) % len(pts)][1]) * (
                pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1])
                for i in range(len(pts))) / (6 * a)
            return (cx, cy), None
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)), None
    a, b = max(zip(pts, pts[1:]), key=lambda s: math.dist(*s))
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), (a, b)  # beside this piece


# -- snapping to a drawing's own lines ------------------------------------------------------
MAX_SEGMENTS = 20000
MAX_CROSSINGS = 50000


def _cross(a, b, c, d):
    """Where segment ab crosses cd inside both (not at their ends), or None."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-9:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
    u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
    if 1e-6 < t < 1 - 1e-6 and 1e-6 < u < 1 - 1e-6:
        return (a[0] + t * r[0], a[1] + t * r[1])
    return None


def _cells(a, b, size):
    """The grid cells a segment passes through (walked along it in quarter cells)."""
    n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / size * 4) + 1
    return {(int((a[0] + (b[0] - a[0]) * i / n) // size), int((a[1] + (b[1] - a[1]) * i / n) // size))
            for i in range(n + 1)}


def crossings(segments, size):
    """Points where straight segments cross, found cell by cell (a grid, not every pair)."""
    grid = {}
    for i, (a, b) in enumerate(segments):
        for cell in _cells(a, b, size):
            grid.setdefault(cell, []).append(i)
    out, seen = [], set()
    for idx in grid.values():
        for n, i in enumerate(idx):
            for j in idx[n + 1:]:
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                p = _cross(*segments[i], *segments[j])
                if p is not None:
                    out.append(p)
                    if len(out) >= MAX_CROSSINGS:
                        return out
    return out


class SnapIndex:
    """What a point may snap to near the mouse: the nearest end or crossing, else a line."""

    def __init__(self, points, segments, size=24.0):
        self.size = size
        self.points, self.segments = points, segments
        self._pgrid, self._sgrid = {}, {}
        for i, (x, y, _k) in enumerate(points):
            self._pgrid.setdefault((int(x // size), int(y // size)), []).append(i)
        for i, (x0, y0, x1, y1) in enumerate(segments):
            for cell in _cells((x0, y0), (x1, y1), size):
                self._sgrid.setdefault(cell, []).append(i)

    def _near(self, grid, x, y, tol):
        r = int(tol // self.size) + 1
        cx, cy = int(x // self.size), int(y // self.size)
        found = set()
        for gx in range(cx - r, cx + r + 1):
            for gy in range(cy - r, cy + r + 1):
                found.update(grid.get((gx, gy), ()))
        return found

    POINT_PULL = 1.5  # ends and crossings pull from further than a line: a corner wins

    def find(self, x, y, tol):
        """(x, y, kind) or None; kind: end, cross or line."""
        # the nearest end or crossing (as CAD programs do: a dimension's tick crosses its line
        # a point away from the tick's own ends); a line only when no point is near
        best = None
        reach = tol * self.POINT_PULL
        for i in self._near(self._pgrid, x, y, reach):
            px, py, kind = self.points[i]
            d = math.hypot(px - x, py - y)
            if d <= reach and (best is None or d < best[0]):
                best = (d, (px, py, kind))
        if best is not None:
            return best[1]
        near = None
        for i in self._near(self._sgrid, x, y, tol):
            x0, y0, x1, y1 = self.segments[i]
            dx, dy = x1 - x0, y1 - y0
            n = dx * dx + dy * dy
            if n < 1e-9:
                continue
            t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / n))
            px, py = x0 + t * dx, y0 + t * dy
            d = math.hypot(px - x, py - y)
            if d <= tol and (near is None or d < near[0]):
                near = (d, (px, py, "line"))
        return near[1] if near else None
