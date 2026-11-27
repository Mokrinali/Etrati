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
