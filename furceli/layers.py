"""What hides in a file: layers that are switched off, and notes inside photos."""
from __future__ import annotations

import re

import pymupdf

from .engine import EngineError

SPACE = b"\0\t\n\f\r "
DELIM = b"()<>[]{}/%"
PATH = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}
PAINT = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"S", b"s", b"n"}
CLIP = {b"W", b"W*"}
MARKED = {b"BMC", b"BDC", b"EMC", b"MP", b"DP"}


class _Broken(ValueError):
    pass


# ---------------------------------------------------------------------------
# content streams
# ---------------------------------------------------------------------------
def _token(s, i):
    """(start, end, is it an operator) of the next token from i on; None at the end."""
    n = len(s)
    while i < n:
        if s[i] in SPACE:
            i += 1
        elif s[i:i + 1] == b"%":
            while i < n and s[i] not in b"\r\n":
                i += 1
        else:
            break
    if i >= n:
        return None
    a, c = i, s[i:i + 1]
    if c == b"(":
        depth = 0
        while i < n:
            if s[i:i + 1] == b"\\":
                i += 1
            elif s[i:i + 1] == b"(":
                depth += 1
            elif s[i:i + 1] == b")":
                depth -= 1
                if not depth:
                    return a, i + 1, False
            i += 1
        raise _Broken
    if c == b"<" and s[i + 1:i + 2] != b"<":
        end = s.find(b">", i)
        if end < 0:
            raise _Broken
        return a, end + 1, False
    if c == b"[" or s[i:i + 2] == b"<<":
        close, i = (b"]", i + 1) if c == b"[" else (b">>", i + 2)
        while True:
            while i < n and s[i] in SPACE:
                i += 1
            if s[i:i + len(close)] == close:
                return a, i + len(close), False
            t = _token(s, i)
            if t is None:
                raise _Broken
            i = t[1]
    if c in b")>]{}":
        raise _Broken
    i += 1
    while i < n and s[i] not in SPACE and s[i] not in DELIM:
        i += 1
    word = s[a:i]
    return a, i, not (c in b"/+-.0123456789" or word in (b"true", b"false", b"null"))


def _inline_end(s, start, head):
    """Where an inline picture ends: by its size when that is certain, else at EI."""
    keys = dict(zip(head[::2], head[1::2]))

    def get(*names):
        return next((keys[n] for n in names if n in keys), None)
    try:
        if get(b"/F", b"/Filter") is None:
            bits = 1 if get(b"/IM", b"/ImageMask") == b"true" else int(
                get(b"/BPC", b"/BitsPerComponent") or 8)
            cs = get(b"/CS", b"/ColorSpace") or b"/G"
            parts = {b"/RGB": 3, b"/DeviceRGB": 3, b"/CMYK": 4, b"/DeviceCMYK": 4, b"/G": 1,
                     b"/DeviceGray": 1, b"/I": 1}.get(cs, 1 if re.match(rb"\[\s*/I", cs) else 0)
            size = (int(get(b"/W", b"/Width")) * bits * parts + 7) // 8 * int(
                get(b"/H", b"/Height"))
            m = re.compile(rb"[\0\t\n\f\r ]*EI(?![^\0\t\n\f\r /\[<(])").match(s, start + size)
            if parts and m:
                return m.end()
    except (TypeError, ValueError):
        pass
    m = re.compile(rb"[\0\t\n\f\r ]EI(?=[\0\t\n\f\r /\[<(]|$)").search(s, start - 1)
    if not m:
        raise _Broken
    return m.end()


def _operations(s):
    """(operands, operator, start, end) one after another."""
    i, operands, start = 0, [], None
    while True:
        t = _token(s, i)
        if t is None:
            return
        a, b, operator = t
        i = b
        if start is None:
            start = a
        if not operator:
            operands.append(s[a:b])
            continue
        op = s[a:b]
        if op == b"BI":  # an inline picture: its data are not tokens
            head = []
            while True:
                t = _token(s, i)
                if t is None:
                    raise _Broken
                i = t[1]
                if t[2]:
                    break
                head.append(s[t[0]:t[1]])
            if s[t[0]:t[1]] != b"ID":
                raise _Broken
            i = b = _inline_end(s, i + 1, head)
        yield operands, op, start, b
        operands, start = [], None


def _name(raw: bytes) -> bytes:
    """A name without its escapes: /My#20Layer and "My Layer" are the same."""
    return re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)


def _plain(name: str) -> bytes:
    """A name as MuPDF hands it over, comparable with the names in a stream."""
    return _name(name.encode("utf-8", "surrogateescape"))


def _without(s, hidden_marks, hidden_objects):
    """(stream without what is hidden, names drawn, names no longer drawn).

    Only what paints is taken out: settings inside a hidden part may still matter after it.
    The marks of visible layers go too, because the list of layers will. Everything else is
    left byte for byte as it was.
    """
    out, done, stack, path = [], 0, [], []
    drawn, dropped = set(), set()
    clip = skipped = False

    def cut(a, b, instead=b""):
        nonlocal done
        out.extend((s[done:a], instead, b" "))
        done = b
    for operands, op, a, b in _operations(s):
        name = _name(operands[-1][1:]) if operands and operands[-1][:1] == b"/" else b""
        off = "off" in stack
        if op in (b"BDC", b"BMC"):
            layer = op == b"BDC" and len(operands) > 1 and operands[0] == b"/OC"
            stack.append("off" if layer and name in hidden_marks else "layer" if layer else "")
            if off or layer:
                cut(a, b)
            continue
        if op == b"EMC":
            was = stack.pop() if stack else ""
            if off or was:
                cut(a, b)
            continue
        if op == b"Do":
            if off or name in hidden_objects:
                dropped.add(name)
                cut(a, b)
            else:
                drawn.add(name)
            continue
        if op in (b"BT", b"ET", b"Td", b"TD", b"Tm", b"T*", b"'", b'"'):
            skipped = False  # the next text has a place of its own
        if not off:
            if skipped and op in (b"Tj", b"TJ"):
                raise _Broken  # it would move to where the hidden text began
            continue
        if op in PATH or op in CLIP:
            clip = clip or op in CLIP
            path.append(s[a:b])
            cut(a, b)
        elif op in PAINT:
            cut(a, b, b" ".join([*path, b"n"]) if clip else b"")  # a clip keeps its shape
            path, clip = [], False
        elif op in (b"Tj", b"TJ"):
            skipped = True
            cut(a, b)
        elif op == b"'":
            skipped = True
            cut(a, b, b"T*")
        elif op == b'"':
            if len(operands) != 3:
                raise _Broken
            skipped = True
            cut(a, b, b"%s Tw %s Tc T*" % tuple(operands[:2]))
        elif op in (b"sh", b"BI") or op in MARKED:
            cut(a, b)
    if any(stack):
        raise _Broken  # a layer without an end
    out.append(s[done:])
    return b"".join(out), drawn, dropped


SHOW = {b"Tj", b"TJ", b"'", b'"'}
FILL = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
STROKE = {b"S", b"s", b"B", b"B*", b"b", b"b*"}
NAME = re.compile(rb"/([^\0\t\n\f\r ()<>\[\]{}/%]*)")


DA_FONT = re.compile(rb"/([^\s/\[\]()<>{}%]+)\s+[-+\d.]+\s+Tf")


def _all_names(s):
    """Every name a stream could mean, and more: for streams that stay as they were."""
    names = {_name(n) for n in NAME.findall(s)}
    return {"Font": names, "Pattern": names, "Shading": names}


def _mentioned(s):
    """Fonts, patterns and shadings a stream asks for, painted with or not."""
    out = {"Font": set(), "Pattern": set(), "Shading": set()}
    for operands, op, _, _ in _operations(s):
        if op in (b"Tf", b"scn", b"SCN", b"sh"):
            names = [o for o in operands if o[:1] == b"/"]
            if names:
                kind = {b"Tf": "Font", b"sh": "Shading"}.get(op, "Pattern")
                out[kind].add(_name(names[0 if op == b"Tf" else -1][1:]))
    return out


def _settled(s):
    """(stream, names it really uses) after the fonts and patterns nothing paints with went.

    What was hidden often set a font or a pattern and painted nothing more after its parts
    were gone. Such a setting is followed through the saving and restoring of the graphics
    state: if nothing shows text or paints with it before it is replaced, it goes, and with
    it the reason to keep that font or pattern in the file.
    """
    used = {"Font": set(), "Pattern": set(), "Shading": set()}
    sets = {}                    # setting -> (kind, name, start, end)
    taken = set()                # settings something painted with
    state, stack = {"font": None, "fill": None, "stroke": None}, []

    def take(*keys):
        taken.update(state[k] for k in keys if state[k] is not None)
    for operands, op, a, b in _operations(s):
        name = _name(operands[-1][1:]) if operands and operands[-1][:1] == b"/" else None
        if op == b"q":
            stack.append(dict(state))
        elif op == b"Q":
            if stack:
                state = stack.pop()
        elif op == b"Tf":
            state["font"] = None
            if len(operands) == 2 and operands[0][:1] == b"/":
                sets[a] = ("Font", _name(operands[0][1:]), a, b)
                state["font"] = a
        elif op in (b"scn", b"SCN"):
            side = "fill" if op == b"scn" else "stroke"
            state[side] = None
            if name is not None:
                sets[a] = ("Pattern", name, a, b)
                state[side] = a
        elif op in (b"cs", b"g", b"rg", b"k", b"sc"):
            state["fill"] = None
        elif op in (b"CS", b"G", b"RG", b"K", b"SC"):
            state["stroke"] = None
        elif op in SHOW:
            take("font", "fill", "stroke")  # how text is painted depends on its mode
        elif op in (b"Do", b"BI"):  # a form may go on with the font; a mask paints with the fill
            take("font", "fill", "stroke")
        else:
            if op in FILL:
                take("fill")
            if op in STROKE:
                take("stroke")
            if op == b"sh" and name is not None:
                used["Shading"].add(name)
    out, done = [], 0
    for key in sorted(sets):
        kind, name, a, b = sets[key]
        if key in taken:
            used[kind].add(name)
        else:
            out.extend((s[done:a], b" "))
            done = b
    out.append(s[done:])
    return b"".join(out), used


# ---------------------------------------------------------------------------
# layers
# ---------------------------------------------------------------------------
class _Remover:
    def __init__(self, doc, gone_fields):
        self.doc, self.gone_fields = doc, gone_fields
        self.mu = pymupdf.mupdf
        self.pdf = self.mu.pdf_document_from_fz_document(doc)
        self.links: dict = {}        # stream -> objects it still draws
        self.lost: set = set()       # objects some hidden part drew
        self.found = False           # something really was hidden
        self.uses: dict = {}         # resource list -> names some stream uses from it
        self.prune: dict = {}        # resource lists of changed streams (identities)
        self.borrowed = False        # a stream uses names of whoever draws it

    def hidden(self, o):
        """Is this layer (or combination of layers) not shown on the screen?"""
        mu = self.mu
        if not mu.pdf_is_dict(o):
            return False
        if not mu.pdf_is_indirect(o):
            raise _Broken  # a combination without a number: MuPDF cannot judge it
        ref = mu.pdf_new_indirect(self.pdf, mu.pdf_to_num(o), 0)
        off = bool(mu.pdf_is_ocg_hidden(self.pdf, mu.PdfResourceStack(), "View", ref))
        if mu.pdf_to_name(mu.pdf_dict_gets(o, "Type")) == "OCMD" and off != self.combined(o):
            raise _Broken  # programs disagree about this one: no guessing
        return off

    def combined(self, o):
        """Is a combination of layers hidden, going by the PDF standard?"""
        mu = self.mu
        if mu.pdf_dict_gets(o, "VE").m_internal:
            raise _Broken  # a formula: too rare to risk a wrong answer
        groups = mu.pdf_dict_gets(o, "OCGs")
        items = ([mu.pdf_array_get(groups, i) for i in range(mu.pdf_array_len(groups))]
                 if mu.pdf_is_array(groups) else [groups])
        on = [not self.hidden(g) for g in items if mu.pdf_is_dict(g)]
        rule = mu.pdf_to_name(mu.pdf_dict_gets(o, "P")) or "AnyOn"
        return bool(on) and not {"AllOn": all(on), "AnyOff": not all(on),
                                 "AllOff": not any(on)}.get(rule, any(on))

    def entries(self, o):
        mu = self.mu
        return [(mu.pdf_to_name(mu.pdf_dict_get_key(o, i)), mu.pdf_dict_get_val(o, i))
                for i in range(mu.pdf_dict_len(o))] if mu.pdf_is_dict(o) else []

    def lists(self, resources, owner):
        """category -> (identity, dict) of the fonts, patterns and shadings a stream may use.

        The identity also says where the list is: changing an object in PyMuPDF writes a new
        one, so the list is looked up again before anything is taken out of it.
        """
        mu, out = self.mu, {}
        for cat in ("Font", "Pattern", "Shading"):
            sub = mu.pdf_dict_gets(resources, cat)
            if not mu.pdf_is_dict(sub):
                continue
            if mu.pdf_is_indirect(sub):
                key = ("x", mu.pdf_to_num(sub))
            elif mu.pdf_is_indirect(resources):
                key = ("res", mu.pdf_to_num(resources), cat)
            else:
                key = ("in", owner, cat)
            out[cat] = (key, sub)
        return out

    def find(self, key):
        """The resource list an identity from lists() names, as it is now."""
        mu = self.mu
        if key[0] == "x":
            return mu.pdf_load_object(self.pdf, key[1])
        o = mu.pdf_load_object(self.pdf, key[1])
        if key[0] == "in":
            o = mu.pdf_dict_gets(o, "Resources")
        return mu.pdf_dict_gets(o, key[2])

    def note(self, resources, owner, used, changed, named=None):
        """Remember which names a stream uses, and from which lists."""
        if not resources.m_internal:
            self.borrowed = self.borrowed or any(used.values())
            return
        lists = self.lists(resources, owner)
        for cat, names in (named or {}).items():
            have = {_plain(n) for n, _ in self.entries(lists[cat][1])} if cat in lists else set()
            if names - have:  # MuPDF looks for a name a stream lacks where the stream is drawn
                self.borrowed = True
        for cat, (key, sub) in lists.items():
            self.uses.setdefault(key, set()).update(used[cat])
            if changed:
                self.prune[key] = True

    def stream(self, key, data, resources, owner):
        """Clean one content stream; returns the new one, or None when nothing changes."""
        mu = self.mu
        marks = {_plain(n) for n, v in self.entries(mu.pdf_dict_gets(resources, "Properties"))
                 if self.hidden(v)}
        number, gone = {}, set()
        for n, v in self.entries(mu.pdf_dict_gets(resources, "XObject")):
            number[_plain(n)] = mu.pdf_to_num(v)
            if self.hidden(mu.pdf_dict_gets(v, "OC")):
                gone.add(_plain(n))
        new, drawn, dropped = _without(data, marks, gone)
        named = _mentioned(data)
        if new != data:
            new, used = _settled(new)
            self.note(resources, owner, used, True, named)
        else:
            self.note(resources, owner, _all_names(data), False, named)
        if not resources.m_internal and (drawn or dropped):
            raise _Broken  # it borrows names from wherever it is drawn
        self.links[key] = {number[n] for n in drawn if n in number}
        self.lost |= {number[n] for n in dropped | gone if n in number}
        self.found = self.found or bool(marks or gone)
        return new if new != data else None

    def roots(self, o):
        """Content drawn without a "Do": looks of comments, soft masks, letters as drawings."""
        mu, out, todo = self.mu, set(), [o]
        while todo:
            o = todo.pop()
            if mu.pdf_is_array(o):
                todo += [v for v in (mu.pdf_array_get(o, i) for i in range(mu.pdf_array_len(o)))
                         if not mu.pdf_is_indirect(v)]
            if not mu.pdf_is_dict(o):
                continue
            letters = mu.pdf_to_name(mu.pdf_dict_gets(o, "Subtype")) == "Type3"
            for k, v in self.entries(o):
                if k in ("G", "SMask", "Mask") and mu.pdf_is_indirect(v):
                    out.add(mu.pdf_to_num(v))
                elif k == "AP":
                    for _, w in self.entries(v):
                        out |= {mu.pdf_to_num(x) for _, x in [("", w), *self.entries(w)]
                                if mu.pdf_is_indirect(x)}
                elif letters and k == "Resources":
                    out |= {mu.pdf_to_num(x)
                            for _, x in self.entries(mu.pdf_dict_gets(v, "XObject"))}
                if not mu.pdf_is_indirect(v):
                    todo.append(v)
        return out

    def objects(self):
        for x in range(1, self.doc.xref_length()):
            try:
                o = self.mu.pdf_load_object(self.pdf, x)
            except Exception:  # a damaged slot
                continue
            yield x, o

    def run(self):
        doc, mu = self.doc, self.mu
        roots = set()
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            for a in list(page.annots()):
                if self.hidden(mu.pdf_dict_gets(mu.pdf_load_object(self.pdf, a.xref), "OC")):
                    self.found = True
                    page.delete_annot(a)
            for w in list(page.widgets()):
                if self.hidden(mu.pdf_dict_gets(mu.pdf_load_object(self.pdf, w.xref), "OC")):
                    self.found = True
                    self.gone_fields.add(w.xref)  # its value goes when the file is saved
                    page.delete_widget(w)
            o = mu.pdf_load_object(self.pdf, page.xref)
            res, holder, number = mu.pdf_dict_gets_inheritable(o, "Resources"), o, page.xref
            while holder.m_internal and not mu.pdf_dict_gets(holder, "Resources").m_internal:
                holder = mu.pdf_dict_gets(holder, "Parent")
                number = mu.pdf_to_num(holder)
            new = self.stream(("page", pno), page.read_contents() or b"", res, number)
            roots.add(("page", pno))
            if new is not None:
                x = doc.get_new_xref()
                doc.update_object(x, "<<>>")
                doc.update_stream(x, new)
                page.set_contents(x)
            page = None
        letters = {}  # letter drawing -> the resources of its font
        for x, o in list(self.objects()):
            roots |= self.roots(o)
            if mu.pdf_to_name(mu.pdf_dict_gets(o, "Subtype")) == "Type3":
                for _, proc in self.entries(mu.pdf_dict_gets(o, "CharProcs")):
                    if not mu.pdf_is_indirect(proc) or not self.doc.xref_is_stream(mu.pdf_to_num(proc)):
                        raise _Broken  # letters that cannot be read cannot be judged
                    letters[mu.pdf_to_num(proc)] = x
        for x, o in list(self.objects()):
            if not doc.xref_is_stream(x) or mu.pdf_to_name(mu.pdf_dict_gets(o, "Subtype")) == "Image":
                continue
            pattern = mu.pdf_to_int(mu.pdf_dict_gets(o, "PatternType")) == 1
            form = mu.pdf_to_name(mu.pdf_dict_gets(o, "Subtype")) == "Form"
            if x in letters:
                font = letters[x]
                res = mu.pdf_dict_gets(mu.pdf_load_object(self.pdf, font), "Resources")
                new = self.stream(x, doc.xref_stream(x) or b"", res, font)
                roots.add(x)
            elif pattern or form or x in roots:
                if pattern:
                    roots.add(x)
                new = self.stream(x, doc.xref_stream(x) or b"", mu.pdf_dict_gets(o, "Resources"), x)
            else:
                continue
            if new is not None:
                doc.update_stream(x, new)
        # 6: fonts form fields name in their text settings come from the form's list
        form = mu.pdf_dict_gets(mu.pdf_load_object(self.pdf, doc.pdf_catalog()), "AcroForm")
        dr = mu.pdf_dict_gets(form, "DR")
        if mu.pdf_is_dict(dr):
            named = set()
            for x, o in list(self.objects()):
                da = mu.pdf_dict_gets(o, "DA") if mu.pdf_is_dict(o) else None
                if da is not None and mu.pdf_is_string(da):
                    named |= {_name(n) for n in DA_FONT.findall(mu.pdf_to_text_string(da).encode(
                        "latin-1", "replace"))}
            if mu.pdf_is_string(mu.pdf_dict_gets(form, "DA")):
                named |= {_name(n) for n in DA_FONT.findall(mu.pdf_to_text_string(
                    mu.pdf_dict_gets(form, "DA")).encode("latin-1", "replace"))}
            self.note(dr, None, {"Font": named, "Pattern": set(), "Shading": set()}, False)
        seen, todo = set(), list(roots)
        while todo:
            key = todo.pop()
            if key not in seen:
                seen.add(key)
                todo += self.links.get(key, ())
        for x in self.lost - seen - {0}:  # still listed as a resource, so it must be emptied
            doc.update_object(x, "<</Type/XObject/Subtype/Form/BBox[0 0 0 0]>>")
            doc.update_stream(x, b" ")
        # Fonts, patterns and shadings only hidden parts used: a font keeps the letters it
        # was cut for, a pattern its whole drawing. A list no stream left alone uses goes.
        if not self.borrowed:
            for key in self.prune:
                sub = self.find(key)
                keep = self.uses.get(key, set())
                for n, _ in self.entries(sub):
                    if _plain(n) not in keep:
                        mu.pdf_dict_dels(sub, n)
        # What is left is plain content: nothing may depend on the list of layers any more.
        for x, o in list(self.objects()):
            if mu.pdf_is_dict(o):
                if mu.pdf_dict_gets(o, "Subtype").m_internal and mu.pdf_dict_gets(o, "OC").m_internal:
                    doc.xref_set_key(x, "OC", "null")
                if mu.pdf_to_name(mu.pdf_dict_gets(o, "Type")) == "OCG":
                    doc.update_object(x, "<</Type/OCG/Name()>>")  # names may tell something too
        doc.xref_set_key(doc.pdf_catalog(), "OCProperties", "null")
        return self.found


def remove_hidden(doc, gone_fields) -> bool:
    """Remove the content of layers that are switched off; the others become plain content.

    Returns whether anything was hidden. MuPDF remembers the state of layers for an open
    document, so the result must be looked at in a fresh copy.
    """
    if doc.xref_get_key(doc.pdf_catalog(), "OCProperties")[0] == "null":
        return False
    try:
        return _Remover(doc, gone_fields).run()
    except _Broken:
        raise EngineError("scrub_layers")


# ---------------------------------------------------------------------------
# photos
# ---------------------------------------------------------------------------
def bare_jpeg(data: bytes):
    """The same picture without notes about camera, place or editing; None when it is odd."""
    if data[:2] != b"\xff\xd8":
        return None
    out, i, n = [data[:2]], 2, len(data)
    while i + 1 < n:
        if data[i] != 0xFF:
            return None
        kind = data[i + 1]
        if kind == 0xFF:  # padding
            i += 1
            continue
        if kind == 0xD9:
            out.append(data[i:i + 2])
            return b"".join(out)  # whatever follows the end is not part of the picture
        if kind == 0x01 or 0xD0 <= kind <= 0xD7:
            out.append(data[i:i + 2])
            i += 2
            continue
        size = int.from_bytes(data[i + 2:i + 4], "big")
        end = i + 2 + size
        if size < 2 or end > n:
            return None
        body = data[i + 4:end]
        note = (kind == 0xFE or 0xE1 <= kind <= 0xEF and kind != 0xEE and not (
            kind == 0xE2 and body.startswith(b"ICC_PROFILE"))
            or kind == 0xE0 and body.startswith(b"JFXX"))  # a small copy of the picture
        if not note:
            out.append(data[i:end])
        i = end
        if kind == 0xDA:  # picture data up to the next real marker
            j = i
            while True:
                j = data.find(b"\xff", j)
                if j < 0 or j + 1 >= n:
                    out.append(data[i:])  # cut off, as some cameras leave it
                    return b"".join(out)
                if data[j + 1] == 0 or 0xD0 <= data[j + 1] <= 0xD7:
                    j += 2
                elif data[j + 1] == 0xFF:
                    j += 1
                else:
                    break
            out.append(data[i:j])
            i = j
    return None
