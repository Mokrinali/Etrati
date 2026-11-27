"""Keeping the outline width of text when other text is removed.

Word makes bold from a regular face by drawing each letter filled and outlined (render mode 2)
with a thin line, about a 35th of the size. MuPDF's redaction filter drops the `w` that only
these outlines use, so after any removal the outline is 1 point wide and the page's bold looks
four times heavier. plan() reads, before a removal, which width each font and size was outlined
with; keep() writes those widths back afterwards, just before the text that needs them, and
sets the width the filter left after that text object, so line art does not change."""
from __future__ import annotations

from collections import Counter

_SPACE = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"
_SHOW = {b"Tj", b"TJ", b"'", b'"'}
_OUTLINED = {1, 2, 5, 6}  # render modes that stroke


def _tokens(data: bytes):
    """(start, end, token, is_operator) for each token of a content stream."""
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in _SPACE:
            i += 1
            continue
        start = i
        if c == 0x25:  # % comment
            while i < n and data[i] not in b"\r\n":
                i += 1
            continue
        if c == 0x28:  # ( string )
            depth, i = 1, i + 1
            while i < n and depth:
                if data[i] == 0x5C:
                    i += 2
                    continue
                depth += {0x28: 1, 0x29: -1}.get(data[i], 0)
                i += 1
            yield start, i, data[start:i], False
            continue
        if data.startswith(b"<<", i) or data.startswith(b">>", i):
            yield start, i + 2, data[i:i + 2], False
            i += 2
            continue
        if c == 0x3C:  # <hex>
            j = data.find(b">", i)
            i = n if j < 0 else j + 1
            yield start, i, data[start:i], False
            continue
        if c in b"[]{}":
            yield start, i + 1, data[i:i + 1], False
            i += 1
            continue
        i += 1
        while i < n and data[i] not in _SPACE and data[i] not in _DELIM:
            i += 1
        tok = data[start:i]
        operator = c != 0x2F and not (tok[:1] in b"+-." or tok[:1].isdigit())
        if tok == b"ID":  # inline image data: up to EI between white space
            j = i + 1
            while True:
                j = data.find(b"EI", j)
                if j < 0 or ((data[j - 1] in _SPACE) and (j + 2 >= n or data[j + 2] in _SPACE)):
                    break
                j += 2
            i = n if j < 0 else j + 2
            yield start, i, data[start:i], True
            continue
        yield start, i, tok, operator


def _walk(data: bytes):
    """Each operator with its operands and the state before it: (start, end, op, args, state);
    start is where its first operand begins. state is [width, font key, render mode]; q and Q
    save and restore it."""
    state, stack, args, first = [1.0, None, 0], [], [], None
    for start, end, tok, op in _tokens(data):
        if not op:
            if not args:
                first = start
            args.append(tok)
            continue
        yield (first if args else start), end, tok, args, list(state)
        try:
            if tok == b"q":
                stack.append(list(state))
            elif tok == b"Q" and stack:
                state = stack.pop()
            elif tok == b"w" and args:
                state[0] = float(args[-1])
            elif tok == b"Tf" and len(args) >= 2:
                state[1] = (args[-2], round(float(args[-1]), 3))
            elif tok == b"Tr" and args:
                state[2] = int(float(args[-1]))
        except ValueError:
            pass
        args = []


def _contents(page) -> bytes:
    return page.read_contents() or b""


def plan(page) -> dict:
    """{(font, size): width} of text outlined with a width other than 1 on this page."""
    seen = {}
    for _s, _e, op, _a, (w, font, mode) in _walk(_contents(page)):
        if op in _SHOW and mode in _OUTLINED and font is not None:
            seen.setdefault(font, Counter())[w] += 1
    out = {}
    for font, widths in seen.items():
        w = widths.most_common(1)[0][0]
        if abs(w - 1.0) > 1e-6:
            out[font] = w
    return out


def keep(page, widths: dict):
    """Write the outline widths of plan() back into the page after a removal."""
    if not widths:
        return
    data = _contents(page)
    parts, last, changed, inside = [], 0, False, None  # inside: width before our first change
    have = None  # the width we set last in this text object
    for start, end, op, _args, (w, font, mode) in _walk(data):
        if op == b"BT":
            inside, have = None, None
        elif op in _SHOW and mode in _OUTLINED:
            want = widths.get(font)
            current = w if have is None else have
            if want is not None and abs(current - want) > 1e-6:
                if inside is None:
                    inside = w
                parts += [data[last:start], b" %g w " % want]
                last, have, changed = start, want, True
        elif op == b"w":
            have = None
        elif op == b"ET" and inside is not None:
            parts += [data[last:end], b" %g w" % inside]
            last, inside = end, None
    if not changed:
        return
    parts.append(data[last:])
    doc, xrefs = page.parent, page.get_contents()
    doc.update_stream(xrefs[0], b"".join(parts))
    for x in xrefs[1:]:
        doc.update_stream(x, b"")
