"""Shapes drawn on a page, read from the page's own drawing commands (HANDOFF.md, item 9).

Every painted path (a line, a rectangle, a hatch of many lines, a filled shape) is one
object: the operator that paints it (S, f, B, ...) is where it can be deleted, by turning
that operator into `n`, which ends the path unpainted. Nothing else on the page changes,
so what lies inside or under a deleted shape stays, text included; a clipping path set
with the same operator (W ... f) still clips.

parse() reads one content stream; the caller follows Form XObjects (Do) itself."""
from __future__ import annotations

import re

_TOKEN = re.compile(rb"[\s\x00]+|%[^\r\n]*|\(|<<|>>|<[0-9A-Fa-f\s]*>|[\[\]{}]"
                    rb"|/[^\s\x00()<>\[\]{}/%]*|[^\s\x00()<>\[\]{}/%]+")
PAINT = {b"S": False, b"s": False, b"f": True, b"F": True, b"f*": True,
         b"B": True, b"B*": True, b"b": True, b"b*": True}
CLOSE = {b"s", b"b", b"b*"}
_PATH = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_FILL = {b"g", b"rg", b"k", b"sc", b"scn", b"cs"}
_STROKE = {b"G", b"RG", b"K", b"SC", b"SCN", b"CS"}


def tokens(data: bytes):
    """(start, end, token) of a content stream; strings, dictionaries and inline image data
    come as single tokens."""
    pos, n, match = 0, len(data), _TOKEN.match
    while pos < n:
        m = match(data, pos)
        if m is None:
            pos += 1
            continue
        start, end = m.span()
        c = data[start]
        if c in b" \t\r\n\f\x00%":
            pos = end
            continue
        if c == 0x28:  # ( string ), nested and escaped
            depth, i = 1, start + 1
            while i < n and depth:
                b = data[i]
                if b == 0x5C:
                    i += 2
                    continue
                if b == 0x28:
                    depth += 1
                elif b == 0x29:
                    depth -= 1
                i += 1
            yield start, i, data[start:i]
            pos = i
            continue
        tok = data[start:end]
        if tok == b"ID":  # inline image data, up to EI between white space
            j = end + 1
            while True:
                j = data.find(b"EI", j)
                if j < 0 or (data[j - 1] in b" \t\r\n\f\x00"
                             and (j + 2 >= n or data[j + 2] in b" \t\r\n\f\x00")):
                    break
                j += 2
            end = n if j < 0 else j + 2
            tok = b"ID"
        yield start, end, tok
        pos = end


def mul(m, n):
    """m then n (PDF matrices as six numbers)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _at(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _curve(p0, p1, p2, p3):
    out = []
    for t in (.25, .5, .75, 1.0):
        u = 1 - t
        out.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def parse(data: bytes, ctm=IDENTITY, on_do=None, tag=None):
    """Painted paths of a content stream: (tag, start, end, fill, polylines, look), start and
    end being where the painting operator is; polylines are in the space ctm leads to; look
    is (fill colour, stroke colour, line width) as written, to tell pieces of one hatch.
    on_do(name, ctm) is called at each Do and may yield paths of its own (a form's)."""
    stack, args, subs, cur = [], [], [], None
    look = (b"", b"", b"1")
    for start, end, tok in tokens(data):
        c = tok[:1]
        if c.isdigit() or c in b"+-." or c in b"/[]<({}" or tok in (b"<<", b">>", b"true", b"false", b"null"):
            args.append(tok)
            continue
        if tok in _PATH:
            try:
                v = [float(a) for a in args[-6:]]
            except ValueError:
                v = []
            if tok == b"m" and len(v) >= 2:
                cur = [_at(ctm, v[-2], v[-1])]
                subs.append(cur)
            elif tok == b"l" and len(v) >= 2 and cur is not None:
                cur.append(_at(ctm, v[-2], v[-1]))
            elif tok == b"c" and len(v) >= 6 and cur is not None:
                cur += _curve(cur[-1], _at(ctm, v[0], v[1]), _at(ctm, v[2], v[3]), _at(ctm, v[4], v[5]))
            elif tok in (b"v", b"y") and len(v) >= 4 and cur is not None:
                a, b = _at(ctm, v[-4], v[-3]), _at(ctm, v[-2], v[-1])
                cur += _curve(cur[-1], cur[-1], a, b) if tok == b"v" else _curve(cur[-1], a, b, b)
            elif tok == b"h" and cur:
                cur.append(cur[0])
            elif tok == b"re" and len(v) >= 4:
                x, y, w, h = v[-4:]
                cur = [_at(ctm, x, y), _at(ctm, x + w, y), _at(ctm, x + w, y + h),
                       _at(ctm, x, y + h), _at(ctm, x, y)]
                subs.append(cur)
        elif tok in PAINT:
            if tok in CLOSE:
                for s in subs:
                    if s and s[-1] != s[0]:
                        s.append(s[0])
            if subs:
                yield tag, start, end, PAINT[tok], subs, look
            subs, cur = [], None
        elif tok == b"n":
            subs, cur = [], None
        elif tok == b"q":
            stack.append((ctm, look))
        elif tok == b"Q":
            if stack:
                ctm, look = stack.pop()
        elif tok in _FILL:
            look = (b" ".join(args) + tok, look[1], look[2])
        elif tok in _STROKE:
            look = (look[0], b" ".join(args) + tok, look[2])
        elif tok == b"w":
            look = (look[0], look[1], b" ".join(args[-1:]))
        elif tok == b"cm":
            try:
                ctm = mul(tuple(float(a) for a in args[-6:]), ctm)
            except (ValueError, TypeError):
                pass
        elif tok == b"Do" and on_do is not None and args:
            got = on_do(args[-1][1:].decode("latin-1"), ctm)
            if got is not None:
                yield from got
        args = []


def bbox(polylines):
    xs = [p[0] for s in polylines for p in s]
    ys = [p[1] for s in polylines for p in s]
    return (min(xs), min(ys), max(xs), max(ys))


SMALL = 12.0   # a hatch's pieces are smaller than this (points), each way
NEAR = 4.0     # and each lies this close to the pieces before it
FEW = 6        # fewer pieces in a row are not a hatch


def groups(items):
    """Hatches written as many small shapes: for each (rect, look) in drawing order, the
    number of its hatch or -1. A hatch is a run of small shapes drawn one after another,
    with the same look, each near the ones before it."""
    out = [-1] * len(items)
    run, box, look, n = [], None, None, 0

    def close():
        nonlocal n
        if len(run) >= FEW:
            for i in run:
                out[i] = n
            n += 1

    for i, (r, lk) in enumerate(items):
        x0, y0, x1, y1 = r
        small = x1 - x0 <= SMALL and y1 - y0 <= SMALL
        if (small and run and lk == look and x0 <= box[2] + NEAR and x1 >= box[0] - NEAR
                and y0 <= box[3] + NEAR and y1 >= box[1] - NEAR):
            run.append(i)
            box = (min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1))
            continue
        close()
        run, box, look = ([i], (x0, y0, x1, y1), lk) if small else ([], None, None)
    close()
    return out
