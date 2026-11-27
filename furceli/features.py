"""Editing operations beyond pages: comments, forms, content, redaction, OCR, security.

Like page operations, every op is deterministic plain data, so undo and crash
recovery can replay it. Anything slow or environment-dependent (OCR) runs first
as a query, and its *result* becomes the op.

Coordinates coming in are displayed page space. On a rotated page they are
turned with derotation_matrix and insertions get rotate=page.rotation.
"""
from __future__ import annotations

import difflib
import html
import io
import json
import math
import os
import re
import zlib

import pymupdf
from pymupdf import Matrix, Point, Quad, Rect

from . import layers, legacy, measure, strokes
from .engine import EditEngine, EngineError, _open_source, _to_pdf, plain_char, view_matrix

PUNCT = ".,;:!?\"'()[]{}«»„“”‘’—–-…/\\"

FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
TESSDATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "tessdata")
OCR_MAX_PIXELS = 30_000_000


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------
def _font_file(text: str, serif=False, mono=False, bold=False, italic=False) -> str:
    """A Windows font that has every character of text; style is best effort."""
    b, i = ("b" if bold else ""), ("i" if italic else "")
    names = []
    if mono:
        names.append({"": "consola", "b": "consolab", "i": "consolai", "bi": "consolaz"}[b + i])
    if serif:
        names.append({"": "times", "b": "timesbd", "i": "timesi", "bi": "timesbi"}[b + i])
        names.append("segoeuib" if bold else "sylfaen")
    names.append({"": "segoeui", "b": "segoeuib", "i": "segoeuii", "bi": "segoeuiz"}[b + i])
    names += ["segoeuib" if bold else "segoeui", "sylfaen", "arial", "seguisym"]
    need = {c for c in text if c not in "\n\r\t"}
    best, most = None, -1
    for name in names:
        path = os.path.join(FONTS, name + ".ttf")
        if not os.path.exists(path):
            continue
        font = pymupdf.Font(fontfile=path)
        have = sum(1 for c in need if font.has_glyph(ord(c)))
        if have == len(need):
            return path
        if have > most:
            best, most = path, have
    if best is None:
        raise EngineError("no_font")
    return best


def _subset(path: str, text: str) -> bytes:
    """Only the glyphs that are used, so a file grows by kilobytes, not megabytes."""
    import logging
    from fontTools import subset
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    from fontTools.ttLib import TTFont
    font = TTFont(path)
    opt = subset.Options()
    opt.notdef_outline, opt.recalc_timestamp = True, False
    opt.layout_features = []
    sub = subset.Subsetter(opt)
    sub.populate(text=text + " ")
    sub.subset(font)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


class _Type:
    """Writes text on a page with a subset font."""

    def __init__(self, page, text, **style):
        path = _font_file(text, **style)
        data = _subset(path, text)
        self.page, self.font = page, pymupdf.Font(fontbuffer=data)
        self.name = f"FU{page.parent.xref_length()}"
        page.insert_font(fontname=self.name, fontbuffer=data)

    def width(self, text, size):
        return self.font.text_length(text, fontsize=size)

    def put(self, at: Point, text, size, color=(0, 0, 0), angle=0.0, opacity=1.0,
            overlay=True, mode=0, rotate=None, to=None, border=None):
        """at is in displayed space; angle turns counter-clockwise as displayed. border: the
        outline's width as a part of the size, for text drawn filled and outlined (mode 2)."""
        page = self.page
        pt = Point(at) * page.derotation_matrix
        morph = (pt, Matrix(angle)) if angle else None
        extra = {} if to else {"overlay": overlay, "fill_opacity": opacity, "stroke_opacity": opacity}
        if border:
            extra.update(fill=color, border_width=border)
        (to or page).insert_text(
            pt, text, fontname=self.name, fontsize=size, color=color,
            rotate=page.rotation if rotate is None else rotate, morph=morph,
            render_mode=mode, **extra)


def _words(lines):
    """[(x0, y0, x1, y1, word), ...] from Engine.chars() lines."""
    out = []
    for ln in lines:
        cur = None
        for x0, y0, x1, y1, c in ln:
            if c.isspace():
                cur = None
                continue
            if cur is not None:
                size = max(min(cur[2] - cur[0], cur[3] - cur[1]), 1.0)
                gap = max(x0 - cur[2], cur[0] - x1, y0 - cur[3], cur[1] - y1)
                if gap > size * 0.8:
                    cur = None
            if cur is None:
                cur = [x0, y0, x1, y1, c]
                out.append(cur)
            else:
                cur[:] = [min(cur[0], x0), min(cur[1], y0), max(cur[2], x1), max(cur[3], y1),
                          cur[4] + c]
    return [tuple(w) for w in out]


def _ocr_words(raw) -> list:
    """Words of an OCR text page. MuPDF runs Georgian words together, but the gaps survive."""
    out = []
    for block in raw["blocks"]:
        for line in block.get("lines", ()):
            cur, size = None, 1.0
            for span in line["spans"]:
                for c in span["chars"]:
                    x0, y0, x1, y1 = c["bbox"]
                    if c["c"].isspace():
                        cur = None
                        continue
                    if cur is not None and (x0 - cur[2] > 0.14 * size or x0 < cur[0]):
                        cur = None
                    if cur is None:
                        cur = [x0, y0, x1, y1, c["c"]]
                        out.append(cur)
                    else:
                        cur[:] = [cur[0], min(cur[1], y0), max(cur[2], x1), max(cur[3], y1),
                                  cur[4] + c["c"]]
                    size = max(y1 - y0, 1.0)
    return [tuple(w) for w in out if w[4].strip() and w[2] - w[0] > 0.5]


def _pdf_name(text: str) -> str:
    """A name as it is spelled in a file."""
    return "".join(c if c.isascii() and (c.isalnum() or c in "-_.+") else
                   "".join(f"#{b:02X}" for b in c.encode("utf-8")) for c in text)


class Features(EditEngine):
    def __init__(self):
        super().__init__()
        self._security = None
        self._notes: list = []       # what the user should be told about the last operation

    def open(self, path, password=""):
        info = super().open(path, password)
        self._security = None
        perm = self.doc.permissions
        info.update(
            can_annotate=bool(perm & (pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_MODIFY)),
            can_fill=bool(perm & (pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ANNOTATE
                                  | pymupdf.PDF_PERM_MODIFY)),
            can_modify=bool(perm & pymupdf.PDF_PERM_MODIFY),
            is_form=bool(self.doc.is_form_pdf),
        )
        return info

    def _info(self):
        info = super()._info()
        info["notes"], self._notes = list(self._notes), []
        info["is_form"] = bool(self.doc.is_form_pdf)
        return info

    def _same(self, *changed):
        return [-1 if i in changed else i for i in range(self.doc.page_count)]

    # -- comments ----------------------------------------------------------
    def _annot_list(self, page):
        return [a for a in page.annots() if a.type[1] not in ("Popup", "Redact")]

    def annots(self, pno):
        page = self.doc.load_page(pno)
        out = []
        for i, a in enumerate(self._annot_list(page)):
            info = a.info
            out.append({"index": i, "type": a.type[1], "rect": tuple(a.rect * page.rotation_matrix),
                        "text": info.get("content") or "", "author": info.get("title") or "",
                        "date": info.get("modDate") or "",
                        "cloud": self.doc.xref_get_key(a.xref, "IT")[1] == "/PolygonCloud",
                        "measure": self._measure_kind(a.xref)})
        return out

    def all_annots(self):
        out = []
        for pno in range(self.doc.page_count):
            for a in self.annots(pno):
                out.append((pno, a))
        return out

    def _find_annot(self, page, index, kind):
        items = self._annot_list(page)
        if index >= len(items) or items[index].type[1] != kind:
            raise EngineError("stale")
        return items[index]

    def _op_annot(self, pno, kind, data, meta):
        page = self.doc.load_page(pno)
        m, rot = page.derotation_matrix, page.rotation
        color = data.get("color", (1, 0, 0))
        width = data.get("width", 1.5)
        text = data.get("text", "")

        def pt(p):
            return Point(p) * m
        if kind in ("highlight", "underline", "strikeout", "squiggly"):
            quads = [Quad(*[pt(p) for p in q]) for q in data["quads"]]
            a = getattr(page, f"add_{kind}_annot")(quads)
            a.set_colors(stroke=color)
        elif kind == "note":
            a = page.add_text_annot(pt(data["point"]), text, icon="Comment")
            a.set_colors(stroke=color)
        elif kind == "text":
            a = page.add_freetext_annot(Rect(data["rect"]) * m, text, fontsize=data["size"],
                                        text_color=color, rotate=rot)
        elif kind == "stamp":
            size = data.get("size", 18)
            a = page.add_freetext_annot(Rect(data["rect"]) * m, text, fontsize=size,
                                        text_color=color, border_width=2,
                                        align=1, rotate=rot)
        elif kind == "ink":
            a = page.add_ink_annot([[tuple(pt(p)) for p in s] for s in data["strokes"]])
        elif kind in ("rect", "ellipse"):
            r = Rect(data["rect"]) * m
            a = page.add_rect_annot(r) if kind == "rect" else page.add_circle_annot(r)
        elif kind in ("line", "arrow"):
            a = page.add_line_annot(pt(data["from"]), pt(data["to"]))
            if kind == "arrow":
                a.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        elif kind == "cloud":  # a revision cloud, as Acrobat and Bluebeam write one
            x0, y0, x1, y1 = data["rect"]
            a = page.add_polygon_annot([pt(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))])
        else:
            raise EngineError("bad_op")
        if kind in ("ink", "rect", "ellipse", "line", "arrow", "cloud"):
            a.set_colors(stroke=color)
            a.set_border(width=width, clouds=1 if kind == "cloud" else -1)
        a.set_info(title=meta.get("author", ""), content=text,
                   creationDate=meta.get("date", ""), modDate=meta.get("date", ""))
        if kind == "text":
            a.update(fontsize=data["size"], text_color=color, rotate=rot)
        elif kind == "stamp":
            a.update(fontsize=size, text_color=color, rotate=rot)
        else:
            a.update(opacity=data.get("opacity", 1))
        self.doc.xref_set_key(a.xref, "NM", pymupdf.get_pdf_str(meta.get("id", "")))
        if kind == "cloud":  # what tells other programs that this polygon is a cloud
            self.doc.xref_set_key(a.xref, "IT", "/PolygonCloud")
        return self._same(pno)

    def _op_annot_del(self, pno, index, kind):
        page = self.doc.load_page(pno)
        page.delete_annot(self._find_annot(page, index, kind))
        return self._same(pno)

    def _op_annot_text(self, pno, index, kind, text, meta):
        page = self.doc.load_page(pno)
        a = self._find_annot(page, index, kind)
        a.set_info(content=text, modDate=meta.get("date", ""))
        if kind == "FreeText":
            a.update(rotate=page.rotation)
        else:
            a.update()
        return self._same(pno)

    def _op_annot_move(self, pno, index, kind, dx, dy):
        page = self.doc.load_page(pno)
        a = self._find_annot(page, index, kind)
        if kind not in ("FreeText", "Stamp", "Text", "Square", "Circle"):
            raise EngineError("cannot_move")
        r = a.rect * page.rotation_matrix + (dx, dy, dx, dy)
        a.set_rect(r * page.derotation_matrix)
        a.update() if kind != "FreeText" else a.update(rotate=page.rotation)
        return self._same(pno)

    def _op_flatten(self):
        self.doc.bake(annots=True, widgets=True)
        return [-1] * self.doc.page_count

    # -- forms -------------------------------------------------------------
    def widgets(self, pno):
        page = self.doc.load_page(pno)
        out = []
        for i, w in enumerate(page.widgets()):
            flags = w.field_flags or 0
            kind = {pymupdf.PDF_WIDGET_TYPE_TEXT: "text",
                    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "check",
                    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
                    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "combo",
                    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "list",
                    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "sign",
                    }.get(w.field_type, "other")
            value = w.field_value
            if kind in ("check", "radio"):
                value = value not in (None, False, "", "Off")
                if kind == "radio":
                    value = w.field_value == w.on_state()
                elif len(self._kids(w.xref)) > 1:  # several boxes of one field
                    value = self.doc.xref_get_key(w.xref, "AS")[1] not in ("null", "/Off")
            pairs = [c if isinstance(c, (list, tuple)) else (c, c) for c in w.choice_values or []]
            multi = kind == "list" and bool(flags & pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT)
            if multi:
                value = self._chosen(w.xref)
            out.append({
                "index": i, "name": w.field_name or "", "kind": kind, "value": value,
                "rect": tuple(w.rect * page.rotation_matrix),
                "choices": [c[1] for c in pairs], "exports": [c[0] for c in pairs],
                "editable": kind == "combo" and bool(flags & pymupdf.PDF_CH_FIELD_IS_EDIT),
                "signed": kind == "sign" and self._has_value(w.xref),
                "multi": multi,
                "multiline": bool(flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE),
                "password": bool(flags & pymupdf.PDF_TX_FIELD_IS_PASSWORD),
                "max": w.text_maxlen or 0,
                "readonly": bool(flags & pymupdf.PDF_FIELD_IS_READ_ONLY),
                "label": w.field_label or ""})
        return out

    def _has_value(self, xref):
        for x in (xref, self._parent(xref)):
            if x and self.doc.xref_get_key(x, "V")[0] != "null":
                return True
        return False

    def _chosen(self, xref):
        """The export values chosen in a list: one or several, kept by the field or the widget."""
        mu = pymupdf.mupdf
        pdf = mu.pdf_document_from_fz_document(self.doc)
        v = mu.pdf_dict_get_inheritable(mu.pdf_load_object(pdf, xref), mu.pdf_new_name("V"))
        items = [mu.pdf_array_get(v, i) for i in range(mu.pdf_array_len(v))]             if mu.pdf_is_array(v) else [v] if mu.pdf_is_string(v) else []
        return [mu.pdf_to_text_string(i) for i in items if mu.pdf_is_string(i)]

    def _draw_list(self, pno, xref, holder, chosen):
        """The look of a list field, drawn here: MuPDF redraws a list without its text and
        marks no choice. Rows of the choices from the top one (TI), the chosen ones on the
        usual light blue (a colour written into the file), in a font that has every letter."""
        page = self.doc.load_page(pno)
        w = next((x for x in page.widgets() if x.xref == xref), None)
        if w is None:
            return
        pairs = [c if isinstance(c, (list, tuple)) else (c, c) for c in w.choice_values or ()]
        items = [str(c[1]) for c in pairs]
        size = w.text_fontsize or 12.0
        color = w.text_color or (0,)
        fill, border = w.fill_color, w.border_color
        bw = (w.border_width or 0) if border else 0
        rect = w.rect
        width, height = rect.width, rect.height
        w = None
        kind, top = self.doc.xref_get_key(xref, "TI")
        if kind != "int":
            kind, top = self.doc.xref_get_key(holder, "TI")
        first = int(top) if kind == "int" and 0 <= int(top) < len(items) else 0

        def paint(c):
            return " ".join(f"{v:g}" for v in c) + {1: " g", 3: " rg", 4: " k"}[len(c)]
        line = size * 1.2
        pad = bw + 1
        out = ["/Tx BMC", "q"]
        if fill:
            out.append(f"{paint(fill)} 0 0 {width:g} {height:g} re f")
        if bw:
            out.append(f"{paint(border).upper()} {bw:g} w "
                       f"{bw / 2:g} {bw / 2:g} {width - bw:g} {height - bw:g} re S")
        out.append(f"{pad:g} {pad:g} {width - 2 * pad:g} {height - 2 * pad:g} re W n")
        text = "".join(items[first:]) or "a"
        name, font = None, None
        rows = []
        for k, i in enumerate(range(first, len(items))):
            y = height - pad - (k + 1) * line
            if y + line < 0:
                break
            rows.append((i, y))
        for i, y in rows:
            if i in chosen:
                out.append(f"0.6 0.757 0.855 rg 0 {y:.3f} {width:g} {line:.3f} re f")
        if rows:
            data = _subset(_font_file(text), text)
            font = pymupdf.Font(fontbuffer=data)
            name = f"FL{self.doc.xref_length()}"
            fx = page.insert_font(fontname=name, fontbuffer=data)
            out.append(f"BT {paint(color)} /{name} {size:g} Tf")
            for i, y in rows:
                glyphs = "".join(f"{font.has_glyph(ord(ch)):04x}" for ch in items[i])
                base = y + (line - size) / 2 + size * .22
                out.append(f"1 0 0 1 {pad + 1:g} {base:.3f} Tm <{glyphs}> Tj")
            out.append("ET")
        out += ["Q", "EMC"]
        page = None
        kind, ap = self.doc.xref_get_key(xref, "AP/N")
        if kind != "xref":
            return
        x = int(ap.split()[0])
        self.doc.update_stream(x, "\n".join(out).encode("latin-1"))
        self.doc.xref_set_key(x, "BBox", f"[0 0 {width:g} {height:g}]")
        self.doc.xref_set_key(x, "Matrix", "[1 0 0 1 0 0]")
        self.doc.xref_set_key(x, "Resources", f"<</Font<</{name} {fx} 0 R>>>>" if rows else "<<>>")

    def _parent(self, xref):
        kind, value = self.doc.xref_get_key(xref, "Parent")
        return int(value.split()[0]) if kind == "xref" else 0

    def _kids(self, xref):
        """All widgets of the field a widget belongs to (none when it is the field itself)."""
        parent = self._parent(xref)
        if not parent or self.doc.xref_get_key(xref, "T")[0] != "null":
            return []
        kind, value = self.doc.xref_get_key(parent, "Kids")
        if kind == "xref":
            value = self.doc.xref_object(int(value.split()[0]), compressed=True)
        return [int(r) for r in re.findall(r"(\d+) \d+ R", value)]

    def _states(self, xref):
        """Names of the pictures a box has besides "Off", as written in the file."""
        kind, value = self.doc.xref_get_key(xref, "AP/N")
        if kind == "xref":
            return self.doc.xref_get_keys(int(value.split()[0]))
        o = pymupdf.mupdf.pdf_load_object(
            pymupdf.mupdf.pdf_document_from_fz_document(self.doc), xref)
        n = pymupdf.mupdf.pdf_dict_getp(o, "AP/N")
        return [pymupdf.mupdf.pdf_to_name(pymupdf.mupdf.pdf_dict_get_key(n, i))
                for i in range(pymupdf.mupdf.pdf_dict_len(n))]

    def _set_box(self, xref, on):
        """One of several check boxes that make up a field: the others follow."""
        name = next((s for s in self._states(xref) if s != "Off"), "Yes") if on else "Off"
        for k in self._kids(xref):
            here = name if on and name in self._states(k) else "Off"
            self.doc.xref_set_key(k, "AS", "/" + _pdf_name(here))
        self.doc.xref_set_key(self._parent(xref), "V", "/" + _pdf_name(name))

    def _widget(self, page, index):
        items = list(page.widgets())
        if index >= len(items):
            raise EngineError("stale")
        return items[index]

    def _field_size(self, text, rect, multiline):
        """A size at which the text really fits: automatic sizing drops Georgian words."""
        font = pymupdf.Font(fontfile=_font_file(text or "a"))
        w, h = max(rect.width, 8) - 5, max(rect.height, 6) - 2
        size = 12.0 if multiline else min(12.0, h * 0.72)
        while size > 4:
            widths = [font.text_length(t, fontsize=size) for t in (text or "").split("\n")]
            if multiline:
                rows = sum(max(1, math.ceil(x * 1.08 / w)) for x in widths)
                if rows * size * 1.3 <= h:
                    break
            elif max(widths) <= w:
                break
            size -= 0.5
        return size

    def _op_fill(self, pno, index, value):
        page = self.doc.load_page(pno)
        w = self._widget(page, index)
        flags = w.field_flags or 0
        if flags & pymupdf.PDF_FIELD_IS_READ_ONLY:
            raise EngineError("read_only")
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT:
            rect = w.rect
            if page.rotation in (90, 270):
                rect = Rect(0, 0, rect.height, rect.width)
            if not w.text_fontsize:
                size = self._field_size(value, rect,
                                        bool(flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE))
                words = "" if flags & pymupdf.PDF_TX_FIELD_IS_PASSWORD else "".join(value.split())
                color = w.text_color or (0,)
                da = " ".join(f"{c:g}" for c in color) + {1: " g", 3: " rg", 4: " k"}[len(color)]
                while True:
                    # Only a size written into the widget itself survives update(). The font
                    # drawn may be wider than the one measured, so look at the result.
                    self.doc.xref_set_key(w.xref, "DA", pymupdf.get_pdf_str(
                        f"{da} /{w.text_font or 'Helv'} {size:g} Tf"))
                    page = w = None
                    page = self.doc.load_page(pno)
                    w = self._widget(page, index)
                    w.field_value = value
                    w.update()
                    page = w = None
                    page = self.doc.load_page(pno)
                    w = self._widget(page, index)
                    shown = "".join(page.get_text(clip=w.rect + (-1, -1, 1, 1)).split())
                    if words in shown or size <= 3.5:
                        break
                    size -= .5
            w.field_value = value
        elif (w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX
                and len(self._kids(w.xref)) > 1):
            self._set_box(w.xref, bool(value))
            return [-1] * self.doc.page_count
        elif w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
            w.field_value = bool(value)
        elif w.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
            w.field_value = bool(value)
        elif w.field_type == pymupdf.PDF_WIDGET_TYPE_LISTBOX and isinstance(value, (list, tuple)):
            if not flags & pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT:
                raise EngineError("bad_op")
            pairs = [c if isinstance(c, (list, tuple)) else (c, c) for c in w.choice_values or ()]
            chosen = [i for i, c in enumerate(pairs) if c[0] in value]
            # The look first (MuPDF draws the list of choices), then the values themselves:
            # PyMuPDF writes only one.
            w.field_value = pairs[chosen[0]][1] if chosen else (pairs[0][1] if pairs else "")
            xref = w.xref
            w.update()
            page = w = None
            parent = self._parent(xref)
            holder = parent if self.doc.xref_get_key(xref, "T")[0] == "null" and parent else xref
            for x in {xref, holder} - {holder}:
                self.doc.xref_set_key(x, "V", "null")
                self.doc.xref_set_key(x, "I", "null")
            self.doc.xref_set_key(holder, "V", "[%s]" % "".join(
                pymupdf.get_pdf_str(pairs[i][0]) for i in chosen))
            self.doc.xref_set_key(holder, "I", "[%s]" % " ".join(map(str, chosen)))
            self._draw_list(pno, xref, holder, chosen)
            return [-1] * self.doc.page_count
        elif w.field_type in (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.PDF_WIDGET_TYPE_LISTBOX):
            # the box shows the display text, the file keeps the export value
            shown = next((c[1] for c in w.choice_values or ()
                          if isinstance(c, (list, tuple)) and c[0] == value), value)
            w.field_value = shown
            is_list = w.field_type == pymupdf.PDF_WIDGET_TYPE_LISTBOX
            pairs = [c if isinstance(c, (list, tuple)) else (c, c) for c in w.choice_values or ()]
            chosen = [i for i, c in enumerate(pairs) if value in (c[0], c[1])][:1]
            if shown != value or is_list:
                xref = w.xref
                w.update()
                page = w = None
                parent = self._parent(xref)
                holder = parent if self.doc.xref_get_key(xref, "T")[0] == "null" and parent else xref
                if shown != value:
                    for x in {xref, holder}:
                        if x == holder or self.doc.xref_get_key(x, "V")[0] != "null":
                            self.doc.xref_set_key(x, "V", pymupdf.get_pdf_str(value))
                if is_list:
                    self._draw_list(pno, xref, holder, chosen)
                return [-1] * self.doc.page_count
        else:
            raise EngineError("read_only")
        w.update()
        return [-1] * self.doc.page_count  # one field may be shown on several pages

    def _op_field(self, pno, rect, kind, name, opts):
        for p in range(self.doc.page_count):
            for other in self.doc.load_page(p).widgets():
                if other.field_name == name:
                    raise EngineError("name_taken")
        page = self.doc.load_page(pno)
        w = pymupdf.Widget()
        w.field_name = name
        w.rect = Rect(rect) * page.derotation_matrix
        w.border_color, w.border_width = (.45, .45, .45), 0.8
        if kind == "text":
            w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            if opts.get("multiline"):
                w.field_flags = pymupdf.PDF_TX_FIELD_IS_MULTILINE
            w.text_fontsize = 0
        elif kind == "check":
            w.field_type, w.field_value = pymupdf.PDF_WIDGET_TYPE_CHECKBOX, False
        elif kind in ("combo", "list"):
            w.field_type = (pymupdf.PDF_WIDGET_TYPE_COMBOBOX if kind == "combo"
                            else pymupdf.PDF_WIDGET_TYPE_LISTBOX)
            w.choice_values = list(opts["choices"])
            w.field_value = opts["choices"][0]
            w.text_fontsize = 11
        elif kind == "sign":
            w.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
        else:
            raise EngineError("bad_op")
        page.add_widget(w)
        if page.rotation:
            self.doc.xref_set_key(w.xref, "MK/R", str(page.rotation))
            w.update()
        return self._same(pno)

    def _op_field_del(self, pno, index):
        page = self.doc.load_page(pno)
        w = self._widget(page, index)
        self._gone_fields.add(w.xref)
        self._dropped = True
        page.delete_widget(w)
        return self._same(pno)

    # -- content -----------------------------------------------------------
    def _not_text(self, page):
        """Places whose words are not the page's text: the look of a measurement, a comment's
        text box or a stamp, and a date (each has a tool of its own). Unturned page space."""
        out = [Rect(r) * page.derotation_matrix for _x, _n, r in self._dates_drawn(page)]
        for a in self._annot_list(page):
            if a.type[1] in ("FreeText", "Stamp") or self._measure_kind(a.xref):
                out.append(Rect(a.rect))
        return [r.normalize() for r in out]

    @staticmethod
    def _within(bbox, places):
        r = Rect(bbox)
        mid = Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        return any(p.contains(mid) for p in places)

    def lines(self, pno):
        """Editable text lines: what a click in text-edit mode can change."""
        page = self.doc.load_page(pno)
        out = []
        skip = self._not_text(page)
        marks = self._outlines(page)
        for ln in self._raw_lines(page):
            if skip and self._within(ln["bbox"], skip):
                continue
            s = ln["spans"][0]
            text = self._line_text(ln)
            if not text.strip():
                continue
            c = s["color"]
            out.append({"index": ln["n"], "text": text,
                        "rect": tuple(Rect(ln["bbox"]) * page.rotation_matrix),
                        "size": s["size"], "font": s["font"],
                        "color": ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
                        "bold": self._looks_bold(ln, self._weights(ln, marks)),
                        "italic": bool(s["flags"] & 2), "serif": bool(s["flags"] & 4),
                        "mono": bool(s["flags"] & 8)})
        return out

    def _raw_lines(self, page):
        out = []
        raw = page.get_text("rawdict", flags=self._flags(page))
        for block in raw["blocks"]:
            for ln in block.get("lines", ()):
                if ln["spans"]:
                    ln["n"] = len(out)
                    out.append(ln)
        return out

    @staticmethod
    def _line_text(ln):
        out = []
        for s in ln["spans"]:
            old = legacy.is_legacy(s["font"])
            out += [plain_char(c["c"], old) for c in s["chars"]]
        return "".join(out)

    @staticmethod
    def _count(page):
        """Visible characters, counted the same way before and after an edit."""
        raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE
                            | pymupdf.TEXT_CID_FOR_UNKNOWN_UNICODE)
        return sum(1 for b in raw["blocks"] for ln in b.get("lines", ())
                   for s in ln["spans"] for c in s["chars"] if not c["c"].isspace())

    def _op_replace(self, pno, index, old, new, style):
        page = self.doc.load_page(pno)
        found = self._raw_lines(page)
        if index >= len(found) or self._line_text(found[index]) != old:
            raise EngineError("stale")
        ln = found[index]
        turn = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}.get(
            (round(ln["dir"][0], 2), round(ln["dir"][1], 2)))
        if turn is None:
            raise EngineError("slanted")
        chars = [c for s in ln["spans"] for c in s["chars"]]
        first = ln["spans"][0]
        own = sum(1 for c in chars if not c["c"].isspace())
        marks = self._outlines(page)
        weights = self._weights(ln, marks)
        made_up = any(weights)  # Word's bold: the regular face filled and outlined
        if made_up and style["bold"] == self._looks_bold(ln, weights):
            new_weights = self._carry_weights(list(old), new, weights)  # each letter as it was
        elif made_up and style["bold"]:
            new_weights = [max(w for w in weights if w)] * len(new)
        else:
            new_weights = [None] * len(new)
        strips = self._line_strips(pno, chars, turn, own)
        again = []
        if strips is None:
            # Some programs (Odoo, for one) write a cell's words so that their boxes overlap:
            # taking this line out takes letters of the touching one too. Then both go and the
            # touching one is written again as it was (its text, size, colour and weight).
            box = Rect(ln["bbox"]) + (-1, -1, 1, 1)
            again = [o for o in found if o is not ln and Rect(o["bbox"]).intersects(box)
                     and self._turn_of(o) == turn and self._line_text(o).strip()]
            if again and len(again) <= 4:
                both = chars + [c for o in again for s in o["spans"] for c in s["chars"]]
                strips = self._line_strips(pno, both, turn,
                                           sum(1 for c in both if not c["c"].isspace()))
            if strips is None:
                raise EngineError("replace_collateral")
        keep = [(self._line_text(o), Point(o["spans"][0]["chars"][0]["origin"]) * page.rotation_matrix,
                 self._span_style(o["spans"][0]), o["dir"], self._weights(o, marks)) for o in again]
        page = None
        page = self._remove_strips(self.doc, pno, strips)
        for text, at, st, d, ws in keep:
            kind = _Type(page, text, serif=st["serif"], mono=st["mono"],
                         bold=st["bold"] and not any(ws), italic=st["italic"])
            self._put_runs(page, kind, at, d, text, ws, st["size"], st["size"], st["color"], turn)
        if new.strip():
            kind = _Type(page, new, serif=style["serif"], mono=style["mono"],
                         bold=style["bold"] and not made_up, italic=style["italic"])
            size = style["size"]
            at = Point(chars[0]["origin"])
            room = {0: page.cropbox.width - at.x, 180: at.x,
                    90: at.y, 270: page.cropbox.height - at.y}[turn] - 6
            while kind.width(new, size) > room and size > first["size"] * .6:
                size -= .25
            self._put_runs(page, kind, at * page.rotation_matrix, ln["dir"], new, new_weights,
                           size, first["size"], style["color"], turn)
        return self._same(pno)

    # -- Word's bold: the regular face drawn filled and outlined with a thin line ------------
    @staticmethod
    def _spot(p):
        return (round(p[0], 1), round(p[1], 1))

    def _outlines(self, page) -> dict:
        """{letter's origin: outline width} of the letters drawn filled and outlined."""
        out = {}
        for t in page.get_texttrace():
            if t["type"] == 1 and t.get("linewidth"):
                for c in t["chars"]:
                    out[self._spot(c[2])] = t["linewidth"]
        return out

    def _weights(self, ln, marks) -> list:
        """Each letter's outline width (None: not outlined)."""
        return [marks.get(self._spot(c["origin"])) for s in ln["spans"] for c in s["chars"]]

    @staticmethod
    def _looks_bold(ln, weights) -> bool:
        """Bold as one sees it: a bold face, or most letters outlined (Word's made-up bold)."""
        s = ln["spans"][0]
        if s["flags"] & 16 or "bold" in s["font"].lower():
            return True
        chars = [c for sp in ln["spans"] for c in sp["chars"]]
        seen = [w for c, w in zip(chars, weights) if not c["c"].isspace()]
        return bool(seen) and sum(1 for w in seen if w) * 2 > len(seen)

    @staticmethod
    def _carry_weights(old, new, weights) -> list:
        """Each new letter gets the outline of the old letter it stands for; a letter put in
        gets that of the letter before it."""
        if not weights:
            return [None] * len(new)
        out = [None] * len(new)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            for k in range(j2 - j1):
                if tag in ("equal", "replace") and i1 + k < i2:
                    i = i1 + k
                else:
                    i = i1 - 1 if i1 > 0 else 0
                out[j1 + k] = weights[min(i, len(weights) - 1)]
        return out

    @staticmethod
    def _put_runs(page, kind, at, d, text, weights, size, base, color, turn):
        """Text written run by run: outlined letters as they were (the width kept as a part
        of the size: base is the size they had), the others plain. at is displayed space,
        d the line's direction on the unturned page."""
        pos, step = Point(at) * page.derotation_matrix, Point(d)
        i = 0
        while i < len(text):
            j = i + 1
            while j < len(text) and weights[j] == weights[i]:
                j += 1
            run, w = text[i:j], weights[i]
            if run.strip():
                kind.put(pos * page.rotation_matrix, run, size, color, rotate=turn,
                         mode=2 if w else 0, border=(w / base) if w else None)
            pos = pos + step * kind.width(run, size)
            i = j

    @staticmethod
    def _span_style(s):
        """The look of a span, as text is written again: size, colour, weight, kind of face."""
        c = s["color"]
        return {"size": s["size"], "color": ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
                "bold": bool(s["flags"] & 16) or "bold" in s["font"].lower(),
                "italic": bool(s["flags"] & 2) or "italic" in s["font"].lower(),
                "serif": bool(s["flags"] & 4), "mono": bool(s["flags"] & 8)}

    @staticmethod
    def _remove_strips(doc, pno, strips):
        page = doc.load_page(pno)
        widths = strokes.plan(page)
        for r in strips:
            page.add_redact_annot(r, fill=False, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        page = None
        page = doc.load_page(pno)
        strokes.keep(page, widths)  # Word's bold outlines keep their width
        return page

    def _line_strips(self, pno, chars, turn, own):
        """Strips that remove exactly these characters, found by trying on a copy.

        The full boxes would touch neighbouring lines; too thin strips miss odd glyphs.
        """
        src = self._snapshot()
        try:
            return self._try_strips(src, pno, chars, turn, own)
        finally:
            src.close()

    def _try_strips(self, src, pno, chars, turn, own):
        for along, across in ((.3, .08), (.15, .15), (.3, .25), (.05, .3), (.45, .02)):
            strips = []
            for c in chars:
                r = Rect(c["bbox"])
                if turn in (0, 180):
                    mid, half = (r.y0 + r.y1) / 2, max(r.height * across, .05)
                    a, b = r.x0 + r.width * along, r.x1 - r.width * along
                    if b - a < .05:
                        a, b = (a + b) / 2 - .05, (a + b) / 2 + .05
                    strips.append(Rect(a, mid - half, b, mid + half))
                else:
                    mid, half = (r.x0 + r.x1) / 2, max(r.width * across, .05)
                    a, b = r.y0 + r.height * along, r.y1 - r.height * along
                    if b - a < .05:
                        a, b = (a + b) / 2 - .05, (a + b) / 2 + .05
                    strips.append(Rect(mid - half, a, mid + half, b))
            trial = self._page_alone(src, pno, annots=1, widgets=1)
            try:
                base = self._glyphs(trial.load_page(0))
                after = self._glyphs(self._remove_strips(trial, 0, strips))
                if len(after) == len(base) - own and not self._moved(base, after):
                    return strips
            finally:
                trial.close()
        return None

    def _op_text(self, pno, rect, text, style):
        page = self.doc.load_page(pno)
        color = "#%02x%02x%02x" % tuple(round(c * 255) for c in style["color"])
        body = html.escape(text).replace("\n", "<br>")
        if style.get("bold"):
            body = f"<b>{body}</b>"
        css = (f"* {{font-family: sans-serif; font-size: {style['size']:g}px; color: {color}; "
               f"text-align: {style.get('align', 'left')}; line-height: 1.25;}}")
        spare, _ = page.insert_htmlbox(Rect(rect) * page.derotation_matrix, body, css=css,
                                       rotate=page.rotation)
        if spare < 0:
            raise EngineError("no_room")
        return self._same(pno)

    def _op_image(self, pno, rect, path):
        page = self.doc.load_page(pno)
        try:
            page.insert_image(Rect(rect) * page.derotation_matrix, filename=path,
                              rotate=page.rotation, keep_proportion=True)
        except Exception:
            raise EngineError("bad_image")
        return self._same(pno)

    # -- pictures already on a page (HANDOFF.md, item 7) -----------------------
    # A picture is changed where the page draws it ("a b c d e f cm /Name Do"), so what was
    # drawn over it stays over it. A picture inside a form, slanted, or drawn in a way that
    # cannot be found exactly is refused rather than changed in part.
    _CM_DO = rb"((?:[-+]?(?:\d+\.?\d*|\.\d+)\s+){6})cm\s*/(%s)\s+Do\b"

    @staticmethod
    def _own_images(page):
        """name -> xref of the pictures the page draws itself (not inside a form)."""
        return {i[7]: i[0] for i in page.get_images(full=True) if i[9] == 0 and i[7]}

    @staticmethod
    def _square(t):
        """Upright or turned by quarters (as on a turned page), never slanted."""
        a, b, c, d = t[:4]
        return abs(a * b) + abs(c * d) < 1e-6 and abs(a * d - b * c) > 1e-9

    def images(self, pno):
        """The pictures of a page, where they are shown: [{index, xref, rect, movable}]."""
        page = self.doc.load_page(pno)
        own = set(self._own_images(page).values())
        out = []
        for k, info in enumerate(page.get_image_info(xrefs=True)):
            x = info.get("xref") or 0
            if not x:
                continue  # an inline picture: not an object of its own
            r = (Rect(info["bbox"]) * page.rotation_matrix).normalize()
            out.append({"index": k, "xref": x, "rect": tuple(round(v, 3) for v in r),
                        "movable": self._square(info["transform"]) and x in own})
        return out

    def _image_site(self, pno, xref, rect, tidy=True):
        """(page, its picture info, content stream xref, regex match of its cm … Do)."""
        page = self.doc.load_page(pno)
        target = (Rect(rect) * page.derotation_matrix).normalize()
        same = [i for i in page.get_image_info(xrefs=True) if i.get("xref") == xref]
        hits = [i for i in same if all(abs(a - b) <= 1 for a, b in zip(Rect(i["bbox"]), target))]
        if len(hits) != 1:
            raise EngineError("stale")
        info = hits[0]
        if not self._square(info["transform"]):
            raise EngineError("image_complex")
        names = [re.escape(n.encode()) for n, x in self._own_images(page).items() if x == xref]
        if not names:
            raise EngineError("image_complex")
        pattern = re.compile(self._CM_DO % b"|".join(names))
        found = [(sx, m) for sx in page.get_contents()
                 for m in pattern.finditer(self.doc.xref_stream(sx) or b"")]
        if len(found) != len(same):
            if tidy:  # written in a way the pattern does not see: once in MuPDF's own words
                page.clean_contents()
                page = None
                return self._image_site(pno, xref, rect, tidy=False)
            raise EngineError("image_complex")
        sx, m = found[same.index(info)]
        return page, info, sx, m

    def _put_matrix(self, sx, m, matrix):
        data = self.doc.xref_stream(sx)
        nums = " ".join(f"{v:.6g}" for v in tuple(matrix)).encode()
        self.doc.update_stream(sx, data[:m.start(1)] + nums + b" " + data[m.end(1):])

    def _op_image_move(self, pno, xref, old, new):
        page, info, sx, m = self._image_site(pno, xref, old)
        o = Rect(info["bbox"])
        n = (Rect(new) * page.derotation_matrix).normalize()
        if n.width < 2 or n.height < 2 or o.width < 1e-3 or o.height < 1e-3:
            raise EngineError("bad_op")
        to = (Matrix(1, 0, 0, 1, -o.x0, -o.y0) * Matrix(n.width / o.width, 0, 0, n.height / o.height, 0, 0)
              * Matrix(1, 0, 0, 1, n.x0, n.y0))
        self._carry(info, sx, m, to)
        return self._same(pno)

    def _carry(self, info, sx, m, to):
        """The picture drawn as before, then carried by `to` (page space, MuPDF's y down)."""
        t = Matrix(info["transform"])
        local = Matrix(*map(float, m.group(1).split()))
        # MuPDF reports the picture with its top row first; PDF draws the unit square bottom up:
        # full = flip · local · outer.  Wanted: full · to  =>  local' = flip · full · to · full⁻¹ · flip · local
        flip = Matrix(1, 0, 0, -1, 0, 1)
        self._put_matrix(sx, m, flip * t * to * ~t * flip * local)

    @staticmethod
    def _turned(transform, page) -> int:
        """Quarter turns of a picture as shown, clockwise in degrees (0, 90, 180, 270)."""
        td = Matrix(transform) * page.rotation_matrix
        return round(math.degrees(math.atan2(td.b, td.a)) / 90) * 90 % 360

    def _op_image_turn(self, pno, xref, rect, degrees):
        """A quarter turn (clockwise as shown when positive) around the picture's middle."""
        if degrees % 90:
            raise EngineError("bad_op")
        _page, info, sx, m = self._image_site(pno, xref, rect)
        c = Rect(info["bbox"])
        cx, cy = (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2
        # a page's own turn is a turn too, so clockwise as shown is clockwise here (y down)
        self._carry(info, sx, m, Matrix(1, 0, 0, 1, -cx, -cy) * Matrix(degrees)
                    * Matrix(1, 0, 0, 1, cx, cy))
        return self._same(pno)

    # -- measuring to scale (design canvas "ETRATI: მასშტაბით გაზომვა") --------
    # A page's scale is kept twice: as the standard /VP viewport with its /Measure (what
    # Acrobat and Bluebeam read) and as our own exact record. A measurement is a standard
    # Line, PolyLine or Polygon annotation (/IT ...Dimension, /Measure) whose look, label
    # included, is drawn here, in a font that has the Georgian unit names.
    MEASURE_COLORS = {"distance": (0.82, 0.13, 0.13), "path": (0.82, 0.13, 0.13),
                      "area": (0.18, 0.44, 0.84)}
    MEASURE_IT = {"distance": "/LineDimension", "path": "/PolyLineDimension",
                  "area": "/PolygonDimension"}

    def scale(self, pno):
        """{factor (metres per point), unit, digits, label} of a page, or None."""
        page = self.doc.load_page(pno)
        kind, raw = self.doc.xref_get_key(page.xref, "ETRATIscale")
        if kind == "string":
            try:
                s = json.loads(raw)
                if s["unit"] in measure.UNITS and float(s["factor"]) > 0:
                    return {"factor": float(s["factor"]), "unit": s["unit"],
                            "digits": int(s["digits"]), "label": str(s.get("label", ""))}
            except (ValueError, KeyError, TypeError):
                pass
        # a scale written by another program: the first number format of its viewport
        kind, vp = self.doc.xref_get_key(page.xref, "VP")
        if kind == "array" and "/Measure" in vp:
            c = re.search(r"/X\s*\[\s*<<[^>]*?/C\s+([-\d.eE+]+)", vp)
            u = re.search(r"/X\s*\[\s*<<[^>]*?/U\s*\(([^)]*)\)", vp)
            unit = u.group(1).strip() if u else ""
            if c and unit in measure.UNITS and float(c.group(1)) > 0:
                return {"factor": float(c.group(1)) / measure.UNITS[unit][0], "unit": unit,
                        "digits": 2, "label": ""}
        return None

    def _op_scale(self, pages, factor, unit, digits, label):
        if unit not in measure.UNITS or not 0 < float(factor) < 1e6 or not 0 <= int(digits) <= 4:
            raise EngineError("bad_op")
        s = {"factor": float(factor), "unit": unit, "digits": int(digits), "label": str(label)[:40]}
        done = []
        for q in sorted({int(p) for p in pages}):
            if not 0 <= q < self.doc.page_count:
                raise EngineError("bad_op")
            page = self.doc.load_page(q)
            mb = page.mediabox
            self.doc.xref_set_key(page.xref, "ETRATIscale", pymupdf.get_pdf_str(json.dumps(s)))
            self.doc.xref_set_key(page.xref, "VP", f"[<</Type/Viewport/Name(ETRATI)/BBox["
                                  f"{mb.x0:g} {mb.y0:g} {mb.x1:g} {mb.y1:g}]/Measure "
                                  f"{measure.measure_dict(s)}>>]")
            page = None
            for _i, a, *_rest in self._measure_annots(self.doc.load_page(q)):
                self._draw_measure(q, a.xref)  # counted again at the new scale
            done.append(q)
        return self._same(*done)

    def _measure_annots(self, page):
        """[(index as the comment tools count, annot, kind, points, note)]."""
        out = []
        for i, a in enumerate(self._annot_list(page)):
            kind = self.doc.xref_get_key(a.xref, "ETRATIkind")[1].lstrip("/")
            if kind in measure.KINDS:
                k, note = self.doc.xref_get_key(a.xref, "ETRATInote")
                out.append((i, a, kind, [tuple(p) for p in a.vertices or ()],
                            note if k == "string" else ""))
        return out

    def snaps(self, pno):
        """What a measuring point may snap to, as shown: {points: [(x, y, kind)], segments}.
        Ends of the page's own straight lines, rectangles and curves, crossings of the
        straight ones, and the straight lines themselves. A scanned page has none."""
        page = self.doc.load_page(pno)
        rot = page.rotation_matrix
        lines, ends = [], []
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        for d in drawings:
            for it in d.get("items", ()):
                op = it[0]
                if op == "l":
                    lines.append((it[1], it[2]))
                elif op == "re":
                    r = Rect(it[1])
                    c = [r.tl, r.tr, r.br, r.bl]
                    lines += [(c[i], c[(i + 1) % 4]) for i in range(4)]
                elif op == "qu":
                    q = it[1]
                    c = [q.ul, q.ur, q.lr, q.ll]
                    lines += [(c[i], c[(i + 1) % 4]) for i in range(4)]
                elif op == "c":
                    ends += [it[1], it[4]]  # a curve: its two ends only
            if len(lines) > measure.MAX_SEGMENTS:
                break
        lines = lines[:measure.MAX_SEGMENTS]
        shown = [((Point(a) * rot).x, (Point(a) * rot).y, (Point(b) * rot).x, (Point(b) * rot).y)
                 for a, b in lines if abs(a.x - b.x) + abs(a.y - b.y) > .2]
        pts, seen = [], set()

        def put(x, y, kind):
            key = (round(x, 1), round(y, 1))
            if key not in seen:
                seen.add(key)
                pts.append((round(x, 3), round(y, 3), kind))
        for x0, y0, x1, y1 in shown:
            put(x0, y0, "end")
            put(x1, y1, "end")
        for p in ends:
            p = Point(p) * rot
            put(p.x, p.y, "end")
        size = max(page.rect.width, page.rect.height) / 50
        for x, y in measure.crossings([((x0, y0), (x1, y1)) for x0, y0, x1, y1 in shown], size):
            put(x, y, "cross")
        return {"points": pts, "segments": [tuple(round(v, 3) for v in s) for s in shown]}

    def _measure_kind(self, xref):
        kind = self.doc.xref_get_key(xref, "ETRATIkind")[1].lstrip("/")
        return kind if kind in measure.KINDS else ""

    def scales(self):
        """Every page's scale (or None), for the live value while measuring."""
        return [self.scale(p) for p in range(self.doc.page_count)]

    def measures(self):
        """Every measurement of the file, counted at its page's scale."""
        out = []
        for pno in range(self.doc.page_count):
            page = self.doc.load_page(pno)
            s = self.scale(pno)
            for i, _a, kind, pts, note in self._measure_annots(page):
                if len(pts) < 2:
                    continue
                value, around = measure.values(kind, pts, s)
                out.append({"page": pno, "index": i, "kind": kind, "value": value,
                            "perimeter": around, "note": note, "paper": s is None,
                            "unit": (s or measure.NO_SCALE)["unit"],
                            "text": measure.label(kind, pts, s, note)})
        return out

    def _op_measure(self, pno, kind, points, note):
        if kind not in measure.KINDS or not 2 <= len(points) <= 500 or (
                kind == "area" and len(points) < 3):
            raise EngineError("bad_op")
        page = self.doc.load_page(pno)
        m = page.derotation_matrix
        pts = [Point(p) * m for p in points]
        if measure.length([tuple(p) for p in pts]) < 1:
            raise EngineError("bad_op")
        color = self.MEASURE_COLORS[kind]
        if kind == "distance":
            a = page.add_line_annot(pts[0], pts[-1])
        elif kind == "path":
            a = page.add_polyline_annot(pts)
        else:
            a = page.add_polygon_annot(pts)
        a.set_colors(stroke=color, fill=color if kind == "area" else None)
        a.set_border(width=1.5)
        a.set_info(subject="ETRATI measure")
        a.update()
        x = a.xref
        a = page = None
        self.doc.xref_set_key(x, "ETRATIkind", "/" + kind)
        self.doc.xref_set_key(x, "ETRATInote", pymupdf.get_pdf_str(str(note)[:120]))
        self.doc.xref_set_key(x, "IT", self.MEASURE_IT[kind])
        self._draw_measure(pno, x)
        return self._same(pno)

    def _op_measure_note(self, pno, index, note):
        page = self.doc.load_page(pno)
        hit = [a for i, a, *_r in self._measure_annots(page) if i == index]
        if not hit:
            raise EngineError("stale")
        x = hit[0].xref
        hit = page = None
        self.doc.xref_set_key(x, "ETRATInote", pymupdf.get_pdf_str(str(note)[:120]))
        self._draw_measure(pno, x)
        return self._same(pno)

    def _draw_measure(self, pno, xref):
        """Contents, /Measure and the look of one measurement, at its page's scale now."""
        page = self.doc.load_page(pno)
        hit = [(a, kind, pts, note) for _i, a, kind, pts, note in self._measure_annots(page)
               if a.xref == xref]
        if not hit or len(hit[0][2]) < 2:
            raise EngineError("stale")
        _a, kind, pts, note = hit[0]
        s = self.scale(pno)
        text = measure.label(kind, pts, s, note)
        to_pdf = ~page.transformation_matrix  # MuPDF's space (y down) -> the PDF's
        P = [Point(p) * to_pdf for p in pts]
        r, g, b = self.MEASURE_COLORS[kind]
        path = " ".join(f"{p.x:.3f} {p.y:.3f} {'m' if i == 0 else 'l'}" for i, p in enumerate(P))
        ops = []
        if kind == "area":
            ops += ["q /G0 gs", f"{r:g} {g:g} {b:g} rg", path, "h f Q"]
        ops += [f"{r:g} {g:g} {b:g} RG 1.5 w 1 J 1 j", path + (" h S" if kind == "area" else " S")]
        if kind == "distance":  # short bars across both ends
            dx, dy = P[1].x - P[0].x, P[1].y - P[0].y
            n = math.hypot(dx, dy) or 1
            nx, ny = -dy / n * 5, dx / n * 5
            for p in (P[0], P[1]):
                ops.append(f"{p.x + nx:.3f} {p.y + ny:.3f} m {p.x - nx:.3f} {p.y - ny:.3f} l S")
        size, pad = 9.0, 2.5
        data = _subset(_font_file(text), text)
        font = pymupdf.Font(fontbuffer=data)
        w = font.text_length(text, fontsize=size)
        x0, y0, x1, y1 = -w / 2 - pad, -size * .3 - pad, w / 2 + pad, size * .85 + pad
        (lx, ly), piece = measure.label_place(kind, pts)
        shown = Point(lx, ly) * page.rotation_matrix  # as the page is shown, y down
        if piece:  # beside the line: on the side that is up (or left), clear of it
            a0, a1 = (Point(q) * page.rotation_matrix for q in piece)
            n = math.hypot(a1.x - a0.x, a1.y - a0.y) or 1
            nx, ny = -(a1.y - a0.y) / n, (a1.x - a0.x) / n
            if ny > 1e-9 or (abs(ny) <= 1e-9 and nx > 0):
                nx, ny = -nx, -ny
            off = 3 + abs(nx) * (x1 - x0) / 2 + abs(ny) * (y1 - y0) / 2
            shown = Point(shown.x + nx * off, shown.y + ny * off)
        L = shown * page.derotation_matrix * to_pdf
        ang = math.radians(page.rotation)  # the label reads upright as the page is shown
        c, sn = math.cos(ang), math.sin(ang)
        glyphs = "".join(f"{font.has_glyph(ord(ch)):04x}" for ch in text)
        ops += [f"q 1 0 0 1 {L.x:.3f} {L.y:.3f} cm {c:.6f} {sn:.6f} {-sn:.6f} {c:.6f} 0 0 cm",
                f"1 1 1 rg {r:g} {g:g} {b:g} RG 0.8 w {x0:.3f} {y0:.3f} {x1 - x0:.3f} {y1 - y0:.3f} re B",
                f"{r * .75:g} {g * .75:g} {b * .75:g} rg BT /F {size:g} Tf {-w / 2:.3f} {-size * .3:.3f} Td "
                f"<{glyphs}> Tj ET Q"]
        corners = [Point(L.x + u * c - v * sn, L.y + u * sn + v * c)
                   for u, v in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        allp = P + corners
        bx0, by0 = min(p.x for p in allp) - 6, min(p.y for p in allp) - 6
        bx1, by1 = max(p.x for p in allp) + 6, max(p.y for p in allp) + 6
        fx = page.insert_font(fontname=f"FM{self.doc.xref_length()}", fontbuffer=data)
        page = _a = None
        box = f"{bx0:.3f} {by0:.3f} {bx1:.3f} {by1:.3f}"
        ap = self.doc.get_new_xref()
        self.doc.update_object(ap, f"<</Type/XObject/Subtype/Form/BBox[{box}]/Matrix[1 0 0 1 0 0]"
                                   f"/Resources<</Font<</F {fx} 0 R>>/ExtGState<</G0<</ca 0.16>>>>>>>>")
        self.doc.update_stream(ap, "\n".join(ops).encode("latin-1"))
        self.doc.xref_set_key(xref, "AP", f"<</N {ap} 0 R>>")
        self.doc.xref_set_key(xref, "Rect", f"[{box}]")
        self.doc.xref_set_key(xref, "Contents", pymupdf.get_pdf_str(text))
        self.doc.xref_set_key(xref, "Measure", measure.measure_dict(s))

    # -- dates (design canvas "ETRATI: სურათი და თარიღი") --------------------
    # A date is real text on the page, drawn through a form of its own ("q /Name Do Q") whose
    # /Matrix holds its place: moving changes that matrix, deleting takes out its "Do". The
    # form is marked /ETRATI /Date and keeps its text and style for copies.
    DATE_FONTS = {"noto": os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets",
                                       "fonts", "NotoSansGeorgian-Regular.ttf"),
                  "sylfaen": os.path.join(FONTS, "sylfaen.ttf"),
                  "segoeui": os.path.join(FONTS, "segoeui.ttf")}

    @staticmethod
    def _same_place(src_size, rect, size, anchor):
        """The place of a rectangle on a page of another size: the same distance from the same
        point of the page (anchor: fractions of width and height), kept on the page."""
        ax, ay = (min(max(float(v), 0.0), 1.0) for v in anchor)
        (sw, sh), (tw, th) = src_size, size
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        nx = min(max(ax * tw + (x0 - ax * sw), 0.0), max(tw - w, 0.0))
        ny = min(max(ay * th + (y0 - ay * sh), 0.0), max(th - h, 0.0))
        return (nx, ny, nx + w, ny + h)

    def _dates_drawn(self, page):
        """[(form xref, name, rect as shown)] of the dates the page draws itself (a deleted
        one stays among the page's resources but is drawn no more)."""
        out = []
        for x, name, invoker, bbox in page.get_xobjects():
            if (invoker == 0 and self.doc.xref_get_key(x, "ETRATI")[1] == "/Date"
                    and self._date_uses(page, name)):
                r = (Rect(bbox) * page.transformation_matrix * page.rotation_matrix).normalize()
                out.append((x, name, tuple(round(v, 3) for v in r)))
        return out

    def _date_uses(self, page, name):
        pattern = re.compile(rb"/" + re.escape(name.encode()) + rb"\s+Do\b")
        return [(sx, m) for sx in page.get_contents()
                for m in pattern.finditer(self.doc.xref_stream(sx) or b"")]

    def _date_do(self, page, name):
        """(stream xref, match) of the one place the page draws a date; None when not once."""
        found = self._date_uses(page, name)
        return found[0] if len(found) == 1 else None

    def objects(self, pno):
        """What the "objects" tool may take on a page: blocks of text first (pictures and
        dates over them are found first, as the tool looks from the top), pictures, dates."""
        page = self.doc.load_page(pno)
        dates = [{"xref": x, "rect": r, "kind": "date",
                  "movable": self._date_do(page, name) is not None}
                 for x, name, r in self._dates_drawn(page)]
        out = [dict(b, kind="text", xref=0) for b in self._text_blocks(page)
               if not any(Rect(b["rect"]).intersects(Rect(d["rect"])) for d in dates)]
        page = None
        return out + [dict(o, kind="image") for o in self.images(pno)] + dates

    # -- blocks of text (HANDOFF.md, item 7) ------------------------------------
    # A block is taken out glyph by glyph (the strips of "replace", proven on a copy not to
    # touch anything else) and written again with the closest Windows font, at its size,
    # colour and weight: the original font holds only the letters it used.
    TURNS = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}

    def _turn_of(self, ln):
        return self.TURNS.get((round(ln["dir"][0], 2), round(ln["dir"][1], 2)))

    def _text_blocks(self, page):
        """[{lines, texts, rect, movable}] numbered as `_raw_lines` numbers lines."""
        raw = page.get_text("rawdict", flags=self._flags(page))
        skip = self._not_text(page)
        out, n = [], 0
        for block in raw["blocks"]:
            lines = []
            for ln in block.get("lines", ()):
                if ln["spans"]:
                    ln["n"] = n
                    n += 1
                    if self._line_text(ln).strip() and not (skip and self._within(ln["bbox"], skip)):
                        lines.append(ln)
            if not lines:
                continue
            r = Rect(lines[0]["bbox"])
            for ln in lines[1:]:
                r |= Rect(ln["bbox"])
            turns = {self._turn_of(ln) for ln in lines}
            out.append({"lines": [ln["n"] for ln in lines],
                        "texts": [self._line_text(ln) for ln in lines],
                        "rect": tuple(round(v, 3) for v in (r * page.rotation_matrix).normalize()),
                        "movable": len(turns) == 1 and None not in turns})
        return out

    def _block(self, pno, lines, texts):
        page = self.doc.load_page(pno)
        found = self._raw_lines(page)
        if not lines or len(lines) != len(texts):
            raise EngineError("stale")
        chosen = []
        for n, t in zip(lines, texts):
            if not 0 <= int(n) < len(found) or self._line_text(found[int(n)]) != t:
                raise EngineError("stale")
            chosen.append(found[int(n)])
        turns = {self._turn_of(ln) for ln in chosen}
        if len(turns) != 1 or None in turns:
            raise EngineError("slanted")
        return page, chosen, turns.pop()

    def _cut_block(self, pno, chosen, turn):
        chars = [c for ln in chosen for s in ln["spans"] for c in s["chars"]]
        own = sum(1 for c in chars if not c["c"].isspace())
        strips = self._line_strips(pno, chars, turn, own)
        if strips is None:
            raise EngineError("replace_collateral")
        return self._remove_strips(self.doc, pno, strips)

    def _op_text_del(self, pno, lines, texts):
        page, chosen, turn = self._block(pno, lines, texts)
        page = None
        self._cut_block(pno, chosen, turn)
        return self._same(pno)

    def _op_text_move(self, pno, lines, texts, old, new):
        """The block from `old` to `new` (as shown); a bigger box writes bigger letters."""
        page, chosen, turn = self._block(pno, lines, texts)
        o, n = Rect(old).normalize(), Rect(new).normalize()
        if o.width < .5 or n.width < .5:
            raise EngineError("bad_op")
        k = n.width / o.width
        rot = page.rotation_matrix
        put = []
        marks = self._outlines(page)
        for ln in chosen:
            s = ln["spans"][0]
            c = s["color"]
            at = Point(ln["spans"][0]["chars"][0]["origin"]) * rot
            ws = self._weights(ln, marks)
            put.append((self._line_text(ln), at, {
                "size": s["size"] * k, "base": s["size"],
                "color": ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
                "bold": (bool(s["flags"] & 16) or "bold" in s["font"].lower()) and not any(ws),
                "italic": bool(s["flags"] & 2), "serif": bool(s["flags"] & 4),
                "mono": bool(s["flags"] & 8)}, ln["dir"], ws))
        page = None
        page = self._cut_block(pno, chosen, turn)
        for text, at, st, d, ws in put:
            kind = _Type(page, text, serif=st["serif"], mono=st["mono"], bold=st["bold"],
                         italic=st["italic"])
            where = Point(n.x0 + (at.x - o.x0) * k, n.y0 + (at.y - o.y0) * k)
            self._put_runs(page, kind, where, d, text, ws, st["size"], st["base"], st["color"], turn)
        return self._same(pno)

    def _date_site(self, pno, xref, rect):
        page = self.doc.load_page(pno)
        hit = [(x, n, r) for x, n, r in self._dates_drawn(page)
               if x == xref and all(abs(a - b) <= 1 for a, b in zip(r, rect))]
        if len(hit) != 1:
            raise EngineError("stale")
        site = self._date_do(page, hit[0][1])
        if site is None:
            raise EngineError("image_complex")
        return page, hit[0], site

    def _put_dates(self, places, text, style):
        """The date drawn on pages: places = [(page, (x, y) top left as shown)]."""
        size = min(max(float(style.get("size", 11)), 4.0), 200.0)
        path = self.DATE_FONTS.get(style.get("font"), "")
        if not os.path.exists(path) or not all(
                pymupdf.Font(fontfile=path).has_glyph(ord(c)) for c in text if not c.isspace()):
            path = _font_file(text)  # a font with every letter of it
        data = _subset(path, text)
        w = pymupdf.Font(fontbuffer=data).text_length(text, fontsize=size) + 2
        h = size * 1.35
        src = pymupdf.open()
        sp = src.new_page(width=w, height=h)
        sp.insert_font(fontname="D", fontbuffer=data)
        color = tuple(float(c) for c in style.get("color", (0, 0, 0)))
        sp.insert_text((1, size * 1.05), text, fontname="D", fontsize=size, color=color)
        sp = None
        info = json.dumps({"text": text, "style": {"font": style.get("font"), "size": size,
                                                   "color": list(color)}}, ensure_ascii=False)
        done = []
        try:
            for q, (x, y) in places:
                tp = self.doc.load_page(q)
                tw, th = tp.rect.width, tp.rect.height
                x, y = min(max(float(x), 0.0), max(tw - w, 0.0)), min(max(float(y), 0.0), max(th - h, 0.0))
                before = {i[0] for i in tp.get_xobjects()}
                tp.show_pdf_page(Rect(x, y, x + w, y + h) * tp.derotation_matrix, src, 0,
                                 rotate=tp.rotation)
                new = [i[0] for i in tp.get_xobjects() if i[2] == 0 and i[0] not in before]
                tp = None
                if len(new) != 1:
                    raise EngineError("image_complex")
                self.doc.xref_set_key(new[0], "ETRATI", "/Date")
                self.doc.xref_set_key(new[0], "ETRATIinfo", pymupdf.get_pdf_str(info))
                done.append(q)
        finally:
            src.close()
        return done

    def _op_date(self, pno, at, text, style, pages):
        """A date at a point (its top left, as shown) on its page and, at the same distance
        from the nearest corner, on the other pages listed."""
        text = str(text).strip()
        if not text or len(text) > 200:
            raise EngineError("bad_op")
        page = self.doc.load_page(pno)
        size = (page.rect.width, page.rect.height)
        page = None
        # the place of the first one decides the corner the others are measured from
        first = self._put_dates([(pno, at)], text, style)
        x, name, rect = self._dates_drawn(self.doc.load_page(pno))[-1]
        near = (0 if (rect[0] + rect[2]) / 2 < size[0] / 2 else 1,
                0 if (rect[1] + rect[3]) / 2 < size[1] / 2 else 1)
        others = sorted({int(p) for p in pages} - {pno})
        places = []
        for q in others:
            if not 0 <= q < self.doc.page_count:
                raise EngineError("bad_op")
            tp = self.doc.load_page(q)
            places.append((q, self._same_place(size, rect, (tp.rect.width, tp.rect.height), near)[:2]))
        return self._same(*first, *self._put_dates(places, text, style))

    def _op_date_move(self, pno, xref, old, new):
        page, (x, _name, rect), _site = self._date_site(pno, xref, old)
        o = Rect(rect) * page.derotation_matrix
        n = Rect(new) * page.derotation_matrix
        o, n = o.normalize(), n.normalize()
        if n.width < 1 or n.height < 1:
            raise EngineError("bad_op")
        to = (Matrix(1, 0, 0, 1, -o.x0, -o.y0) * Matrix(n.width / o.width, 0, 0, n.height / o.height, 0, 0)
              * Matrix(1, 0, 0, 1, n.x0, n.y0))
        t0 = page.transformation_matrix  # PDF space -> MuPDF's (y down)
        kind, mat = self.doc.xref_get_key(x, "Matrix")
        old_m = Matrix(*map(float, re.findall(r"[-\d.]+", mat))) if kind == "array" else Matrix(1, 0, 0, 1, 0, 0)
        m = old_m * t0 * to * ~t0
        self.doc.xref_set_key(x, "Matrix", "[%s]" % " ".join(f"{v:.6g}" for v in tuple(m)))
        return self._same(pno)

    def _op_date_del(self, pno, xref, rect):
        _page, _hit, (sx, m) = self._date_site(pno, xref, rect)
        data = self.doc.xref_stream(sx)
        self.doc.update_stream(sx, data[:m.start()] + data[m.end():])
        return self._same(pno)

    def _op_date_copy(self, pno, xref, rect, pages, anchor):
        page, (x, _name, shown), _site = self._date_site(pno, xref, rect)
        size = (page.rect.width, page.rect.height)
        page = None
        try:
            info = json.loads(self.doc.xref_get_key(x, "ETRATIinfo")[1])
        except (ValueError, TypeError):
            raise EngineError("image_complex")
        places = []
        for q in sorted({int(p) for p in pages} - {pno}):
            if not 0 <= q < self.doc.page_count:
                raise EngineError("bad_op")
            tp = self.doc.load_page(q)
            places.append((q, self._same_place(size, shown, (tp.rect.width, tp.rect.height), anchor)[:2]))
        return self._same(*self._put_dates(places, info["text"], info["style"]))

    def _op_image_copy(self, pno, xref, rect, pages, anchor):
        """The same picture, the same size, on other pages; its place measured from the same
        point of each page (anchor: fractions of the width and height, (1, 0) = top right).
        The file keeps the picture once; every page draws it on its own."""
        page, info, _sx, _m = self._image_site(pno, xref, rect)
        ax, ay = (min(max(float(v), 0.0), 1.0) for v in anchor)
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        turned = self._turned(info["transform"], page)
        sw, sh = page.rect.width, page.rect.height  # as shown
        page = None
        ox, oy = x0 + ax * w - ax * sw, y0 + ay * h - ay * sh
        done = []
        for q in sorted({int(p) for p in pages} - {pno}):
            if not 0 <= q < self.doc.page_count:
                raise EngineError("bad_op")
            tp = self.doc.load_page(q)
            tw, th = tp.rect.width, tp.rect.height
            nx = min(max(ax * tw + ox - ax * w, 0.0), max(tw - w, 0.0))
            ny = min(max(ay * th + oy - ay * h, 0.0), max(th - h, 0.0))
            target = Rect(nx, ny, nx + w, ny + h) * tp.derotation_matrix
            try:
                tp.insert_image(target, xref=xref, rotate=(tp.rotation - turned) % 360,
                                keep_proportion=False)
            except Exception:
                raise EngineError("bad_image")
            tp = None
            done.append(q)
        return self._same(*done)

    def _op_image_del(self, pno, xref, rect):
        _page, _info, sx, m = self._image_site(pno, xref, rect)
        data = self.doc.xref_stream(sx)
        self.doc.update_stream(sx, data[:m.start()] + data[m.end():])
        return self._same(pno)

    def _op_image_swap(self, pno, xref, rect, path):
        uses = sum(1 for p in self.doc for i in p.get_image_info(xrefs=True) if i.get("xref") == xref)
        if uses != 1:
            raise EngineError("image_shared")  # one file object, shown in several places
        self._image_site(pno, xref, rect)  # stale or not drawn plainly: refused before changing
        try:
            pix = pymupdf.Pixmap(path)
            w, h = pix.width, pix.height
            pix = None
        except Exception:
            raise EngineError("bad_image")
        page = self.doc.load_page(pno)
        before = set(self._own_images(page))
        try:  # the new picture becomes an object of the file; its own drawing is taken out again
            new_x = page.insert_image(page.rect, filename=path)
        except Exception:
            raise EngineError("bad_image")
        page = None
        page = self.doc.load_page(pno)
        added = [n for n, x in self._own_images(page).items() if n not in before and x == new_x]
        if len(added) != 1:
            raise EngineError("image_complex")
        name = added[0].encode()
        drawn = re.compile(self._CM_DO % re.escape(name))
        for c in page.get_contents():
            data = self.doc.xref_stream(c) or b""
            mm = drawn.search(data)
            if mm:
                self.doc.update_stream(c, data[:mm.start()] + data[mm.end():])
                break
        page = None
        # where the old one was drawn, the new one is drawn now: same place among the others
        _page, _info, sx, m = self._image_site(pno, xref, rect)
        data = self.doc.xref_stream(sx)
        self.doc.update_stream(sx, data[:m.start(2)] + name + data[m.end(2):])
        _page = None
        o = Rect(rect)
        k = min(o.width / w, o.height / h)  # fitted inside the old place, centred
        fw, fh = w * k, h * k
        fit = (o.x0 + (o.width - fw) / 2, o.y0 + (o.height - fh) / 2,
               o.x0 + (o.width + fw) / 2, o.y0 + (o.height + fh) / 2)
        return self._op_image_move(pno, new_x, rect, fit)

    def _op_watermark(self, pages, text, o):
        for pno in pages:
            page = self.doc.load_page(pno)
            kind = _Type(page, text, bold=o.get("bold", False))
            size = o["size"]
            w = kind.width(text, size)
            limit = math.hypot(page.rect.width, page.rect.height) * .8
            if w > limit:
                size *= limit / w
                w = limit
            a = math.radians(o["angle"])
            c = Point(page.rect.width / 2, page.rect.height / 2)
            # baseline start, so that the text's middle lands on the page's middle
            x = c.x - w / 2 * math.cos(a) + size * .3 * math.sin(a)
            y = c.y + w / 2 * math.sin(a) + size * .3 * math.cos(a)
            kind.put(Point(x, y), text, size, o["color"], o["angle"], o["opacity"],
                     overlay=o.get("over", True))
        return self._same(*pages)

    def _op_header(self, pages, o):
        total = self.doc.page_count
        for k, pno in enumerate(pages):
            page = self.doc.load_page(pno)
            for place, raw in o["texts"].items():
                if not raw:
                    continue
                bates = f"{o.get('prefix', '')}{o.get('start', 1) + k:0{o.get('digits', 6)}d}"
                text = (raw.replace("{n}", str(o.get("first", 1) + k)).replace("{N}", str(total))
                        .replace("{date}", o.get("date", "")).replace("{bates}", bates))
                kind = _Type(page, text)
                size, margin = o["size"], o["margin"]
                w = kind.width(text, size)
                side = place[1]
                x = {"l": margin, "c": (page.rect.width - w) / 2,
                     "r": page.rect.width - margin - w}[side]
                y = margin if place[0] == "t" else page.rect.height - margin + size * .6
                kind.put(Point(x, y), text, size, o["color"])
        return self._same(*pages)

    def _op_background(self, pages, color):
        for pno in pages:
            page = self.doc.load_page(pno)
            page.draw_rect(page.rect * page.derotation_matrix, color=None, fill=color,
                           overlay=False)
        return self._same(*pages)

    # -- redaction ---------------------------------------------------------
    @staticmethod
    def _glyphs(page):
        """[(char, unrotated box), ...] of everything visible, read the same way every time."""
        raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE
                            | pymupdf.TEXT_CID_FOR_UNKNOWN_UNICODE)
        return [(c["c"], Rect(c["bbox"])) for b in raw["blocks"] for ln in b.get("lines", ())
                for s in ln["spans"] for c in s["chars"] if not c["c"].isspace()]

    @staticmethod
    def _moved(before, after) -> int:
        """Characters that are somewhere else than before: rewriting a page can shift text."""
        grid: dict = {}
        for c, r in before:
            grid.setdefault((c, int(r.x0), int(r.y0)), []).append((r.x0, r.y0))
        bad = 0
        for c, r in after:
            x, y = int(r.x0), int(r.y0)
            for key in ((c, x + i, y + j) for i in (0, -1, 1) for j in (0, -1, 1)):
                spot = next((p for p in grid.get(key, ())
                             if abs(p[0] - r.x0) < .8 and abs(p[1] - r.y0) < .8), None)
                if spot:
                    grid[key].remove(spot)
                    break
            else:
                bad += 1
        return bad

    @staticmethod
    def _inside(r, areas):
        mid = Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        for a in areas:
            part = r & a
            if a.contains(mid) or (not part.is_empty and r.get_area()
                                   and part.get_area() > .4 * r.get_area()):
                return True
        return False

    @staticmethod
    def _wipe(page, boxes, fill):
        # Applying takes every mark on the page: those of other programs are hidden meanwhile.
        doc, pno = page.parent, page.number
        foreign = [a.xref for a in page.annots(types=[pymupdf.PDF_ANNOT_REDACT])]
        if foreign:
            for x in foreign:
                doc.xref_set_key(x, "Subtype", "/Square")
            page = None
            page = doc.load_page(pno)
        widths = strokes.plan(page)
        for r, color in boxes:
            page.add_redact_annot(r, fill=color, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        strokes.keep(page, widths)  # Word's bold outlines keep their width
        for x in foreign:
            doc.xref_set_key(x, "Subtype", "/Redact")

    def _wipe_plan(self, src, pno, areas, fill):
        """Boxes that really remove the text and move nothing else, tried on a copy; or None.

        src is a list holding the snapshot: renewed when this page has changed since it was
        taken, as one that shares something with a page redacted before may have.
        """
        before = self._glyphs(self.doc.load_page(pno))
        trial = self._page_alone(src[0], pno)
        try:
            stale = self._glyphs(trial.load_page(0)) != before
        finally:
            trial.close()
        if stale:
            src[0].close()
            src[0] = self._snapshot()
        doomed = [r for _, r in before if self._inside(r, areas)]
        main = [(a, fill) for a in areas]
        wide = [(r + (-.2, 0, .2, 0), False) for r in doomed]
        narrow = [(Rect(r.x0 + r.width * .2, r.y0 + r.height * .3,
                        r.x1 - r.width * .2, r.y1 - r.height * .3), False) for r in doomed]
        for plan in (main, main + [b for b in narrow if not b[0].is_empty], main + wide):
            # comments and fields are handled on their own and not part of the text
            trial = self._page_alone(src[0], pno, annots=0, widgets=0)
            try:
                base = self._glyphs(trial.load_page(0))
                self._wipe(trial.load_page(0), plan, fill)
                after = self._glyphs(trial.load_page(0))
            finally:
                trial.close()
            gone = len([1 for _, r in base if self._inside(r, areas)])
            if any(self._inside(r, areas) for _, r in after) or self._moved(base, after):
                continue
            extra = len(base) - len(after) - gone
            if 0 <= extra <= 2 * len(areas):
                return plan
        return None

    def _as_picture(self, pno, areas, fill):
        """The page becomes an image, and the text that stays is laid over it invisibly.

        Needed whenever text follows a removed word on its line: MuPDF 1.28.6 then shifts the
        rest of the line by one letter, under the black box or away from where it belongs.
        """
        page = self.doc.load_page(pno)
        shown = [Rect(a) * page.rotation_matrix for a in areas]
        keep = []
        for ln in self.chars(pno):
            part = []
            for c in ln:
                if c[4] == "�" or self._inside(Rect(c[:4]), shown):
                    if part:
                        keep.append(part)
                    part = []
                else:
                    part.append(c)
            if part:
                keep.append(part)
        words = [w for w in _words(keep) if w[4].strip()]
        w, h = page.rect.width, page.rect.height
        dpi = int(min(250.0, 72 * math.sqrt(30_000_000 / (w * h))))
        pix = page.get_pixmap(dpi=dpi, annots=False, alpha=False)
        k = dpi / 72
        color = tuple(round(c * 255) for c in fill)
        for a in areas:
            r = a * page.rotation_matrix
            pix.set_rect((Rect(r) * Matrix(k, k) + (-1, -1, 1, 1)).irect & pix.irect, color)
        for ln in page.get_links():
            if any(not (Rect(ln["from"]) * page.derotation_matrix & a).is_empty for a in areas):
                page.delete_link(ln)
        for key in ("Contents", "Resources", "Group"):
            self.doc.xref_set_key(page.xref, key, "null")
        page = None
        page = self.doc.load_page(pno)
        data = min((pix.tobytes("png"), pix.tobytes("jpeg", jpg_quality=90)), key=len)
        page.insert_image(page.rect * page.derotation_matrix, stream=data, rotate=page.rotation)
        page = None
        if words:
            self._op_textlayer({pno: words})
        self._notes.append(("picture", pno))

    def _op_redact(self, marks, o):
        # One snapshot for all tries: the pages change one by one, but each is tried before
        # it changes, and comments and fields, which do go, are not part of the tries.
        src = [self._snapshot()]
        try:
            self._redact_pages(src, marks, o)
        finally:
            src[0].close()
        self._drop_tags()  # whole: an element without a page may quote the redacted text
        cat = self.doc.pdf_catalog()
        if self.doc.xref_get_key(cat, "PieceInfo")[0] != "null":
            self.doc.xref_set_key(cat, "PieceInfo", "null")
        self._dropped = True
        return self._same(*marks)

    MC_TEXT = re.compile(rb"/(?:ActualText|Alt|E)\s*([(<])")

    @classmethod
    def _blank_mc(cls, data: bytes) -> bytes:
        """Every ActualText, Alt and E string of marked content made empty. They repeat the
        words (for screen readers and search) and stay when the glyphs are redacted."""
        out, i = [], 0
        for m in cls.MC_TEXT.finditer(data):
            start = m.start(1)
            if start < i:
                continue
            if data[start:start + 1] == b"<":
                if data[start + 1:start + 2] == b"<":
                    continue  # a dictionary, not a hex string
                end = data.find(b">", start)
                if end < 0:
                    break
                end += 1
            else:
                depth, end = 0, start
                while end < len(data):
                    c = data[end:end + 1]
                    if c == b"\\":
                        end += 2
                        continue
                    depth += c == b"("
                    depth -= c == b")"
                    end += 1
                    if depth == 0:
                        break
            out += [data[i:start], b"()"]
            i = end
        return b"".join(out) + data[i:]

    def _scrub_marked(self, pno):
        """On a redacted page: the words that marked content repeats, in the page's streams,
        in its forms and in their /Properties."""
        doc = self.doc
        page = doc.load_page(pno)
        streams = list(page.get_contents())
        holders = [page.xref]
        for x in page.get_xobjects():
            if doc.xref_get_key(x[0], "Subtype")[1] == "/Form":
                streams.append(x[0])
                holders.append(x[0])
        page = None
        for x in streams:
            data = doc.xref_stream(x)
            new = self._blank_mc(data)
            if new != data:
                doc.update_stream(x, new)
        for h in holders:
            kind, res = doc.xref_get_key(h, "Resources")
            at, pre = (int(res.split()[0]), "") if kind == "xref" else (h, "Resources/")
            kind, props = doc.xref_get_key(at, pre + "Properties")
            if kind == "xref":
                at, pre, props = int(props.split()[0]), "", doc.xref_object(int(props.split()[0]))
            elif kind == "dict":
                pre += "Properties/"
            else:
                continue
            for name, ref in re.findall(r"/([^\s/<>\[\]()]+)\s*(\d+ 0 R|<<)", props):
                where, path = ((int(ref.split()[0]), "") if ref != "<<" else (at, pre + name + "/"))
                for key in ("ActualText", "Alt", "E"):
                    if doc.xref_get_key(where, path + key)[0] != "null":
                        doc.xref_set_key(where, path + key, "null")

    def _redact_pages(self, src, marks, o):
        words = {o["word"]} if len(o.get("word") or "") >= 2 else set()
        for pno, rects in marks.items():
            page = self.doc.load_page(pno)
            m = page.derotation_matrix
            areas = [(Rect(r) * m).normalize() for r in rects]
            for w in page.get_text("words"):
                bare = w[4].strip(PUNCT)
                if len(bare) >= 3 and self._inside(Rect(w[:4]), areas):
                    words.add(bare)

            def hit(r):
                return any(not (Rect(r) & a).is_empty for a in areas)
            self._delete_under(page, hit)
            page = None
            plan = self._wipe_plan(src, pno, areas, o["fill"])
            if plan is None:
                self._as_picture(pno, areas, o["fill"])
            else:
                self._wipe(self.doc.load_page(pno), plan, o["fill"])
            page = None
            self._scrub_marked(pno)
            page = self.doc.load_page(pno)
            shown = [r for c, r in self._glyphs(page)]
            fields = [w.rect for w in page.widgets()]
            if any(self._inside(r, areas) and not any(f.intersects(r) for f in fields)
                   for r in shown):
                raise EngineError("redact_failed")
            # private copies of the page that editors and viewers keep
            for key in ("Thumb", "PieceInfo"):
                self.doc.xref_set_key(page.xref, key, "null")
        said = self._mentions_of(sorted(words)[:300])
        if said:
            self._notes.append(("mentions", said))

    def _delete_under(self, page, hit):
        for a in list(page.annots()):
            if hit(a.rect):
                page.delete_annot(a)
        for w in list(page.widgets()):
            if hit(w.rect):
                self._gone_fields.add(w.xref)
                page.delete_widget(w)

    def _places(self):
        """(where, text) of everything outside the pages' content, read once."""
        out = [("თვისებები: " + k, str(v)) for k, v in (self.doc.metadata or {}).items() if v]
        out.append(("XMP მეტამონაცემები", self.doc.get_xml_metadata() or ""))
        out += [("სანიშნე: " + t[1], t[1]) for t in self.doc.get_toc()]
        out += [(f"კომენტარი, გვ. {pno + 1}", a["text"]) for pno, a in self.all_annots()]
        for pno in range(self.doc.page_count):
            out += [(f"ფორმის ველი, გვ. {pno + 1}", str(w.field_value or ""))
                    for w in self.doc.load_page(pno).widgets()]
        return [(where, text.lower()) for where, text in out]

    def _unchecked(self):
        out = []
        if self.doc.xref_get_key(self.doc.pdf_catalog(), "AcroForm/XFA")[0] != "null":
            out.append("XFA ფორმის მონაცემები (შიგთავსი არ შემოწმდა)")
        if self.doc.embfile_count():
            out.append("მიმაგრებული ფაილები (შიგთავსი არ შემოწმდა)")
        return out

    def mentions(self, text):
        """Where else a word appears: places redaction of pages does not reach."""
        low = text.lower()
        return [where for where, t in self._places() if low in t] + self._unchecked()

    def _mentions_of(self, words):
        """Lines telling where else the removed words appear; [] when nowhere."""
        if not words:
            return []
        places, out = self._places(), []
        for word in words:
            low = word.lower()
            where = list(dict.fromkeys(w for w, t in places if low in t))
            if where:
                out.append(f"„{word}“: " + ", ".join(where[:6]) + (" …" if len(where) > 6 else ""))
        return out + self._unchecked()  # what was not looked into is said every time

    def _op_scrub(self, o):
        # Document.scrub() stops at the first damaged cross-reference, which real files have.
        doc = self.doc
        if o.get("hidden"):
            from pymupdf import utils  # noqa: F401 - the one part worth keeping
            look = [doc.load_page(p).get_pixmap(dpi=50).samples for p in range(doc.page_count)]
            if doc.xref_get_key(doc.pdf_catalog(), "OCProperties")[0] != "null":
                if layers.remove_hidden(doc, self._gone_fields):
                    self._drop_tags()  # they may quote what was hidden
                self._reload()         # MuPDF remembers which layers were hidden
                doc = self.doc
            doc.scrub(attached_files=False, embedded_files=False, javascript=False,
                      metadata=False, xml_metadata=False, hidden_text=True, thumbnails=False,
                      clean_pages=False, redactions=False, remove_links=False,
                      reset_fields=False, reset_responses=False)
            import numpy as np
            for p, old in enumerate(look):  # removing invisible text must change nothing visible
                new = doc.load_page(p).get_pixmap(dpi=50).samples
                if len(new) != len(old) or (abs(
                        np.frombuffer(new, np.uint8).astype(np.int16)
                        - np.frombuffer(old, np.uint8)) > 60).mean() > .002:
                    raise EngineError("scrub_changed")
        if o.get("files", True):
            self._drop_files()
        if o.get("metadata", True):
            doc.set_metadata({})
            doc.del_xml_metadata()
        for pno in range(doc.page_count):
            x = doc.page_xref(pno)
            if doc.xref_get_key(x, "Thumb")[0] != "null":
                doc.xref_set_key(x, "Thumb", "null")
        if o.get("metadata", True):
            odd = 0
            for x in range(1, self.doc.xref_length()):  # pictures and fonts carry it too
                try:
                    for key in ("PieceInfo", "Metadata"):
                        if self.doc.xref_get_key(x, key)[0] != "null":
                            self.doc.xref_set_key(x, key, "null")
                    odd += self._bare_jpeg(x) is False
                except Exception:  # a damaged slot
                    continue
            if odd:
                self._notes.append(("photos", odd))
        if o.get("javascript", True):
            self._strip_javascript()
            if self._strip_javascript():  # a second pass must find nothing
                raise EngineError("scrub_failed")
        if o.get("metadata", True):  # comment authors are personal data too
            for pno in range(self.doc.page_count):
                page = self.doc.load_page(pno)
                for x in [a.xref for a in page.annots()]:
                    for key in ("T", "M", "CreationDate"):
                        self.doc.xref_set_key(x, key, "null")
        if o.get("comments"):
            for pno in range(self.doc.page_count):
                page = self.doc.load_page(pno)
                for a in list(page.annots()):
                    page.delete_annot(a)
        self._dropped = True
        return [-1] * self.doc.page_count

    def _bare_jpeg(self, x):
        """Take camera, place and editing notes out of a photo; its pixels stay as they are.

        False when it is a photo that cannot be read that way.
        """
        doc = self.doc
        filters = re.findall(r"/(\w+)", doc.xref_get_key(x, "Filter")[1])
        if doc.xref_get_key(x, "Subtype")[1] != "/Image" or "DCTDecode" not in filters:
            return None
        old = doc.xref_stream_raw(x)
        if filters == ["FlateDecode", "DCTDecode"] and doc.xref_get_key(x, "DecodeParms")[0] == "null":
            try:
                old = zlib.decompress(old)
            except zlib.error:
                return False
        elif filters != ["DCTDecode"]:
            return False
        new = layers.bare_jpeg(old)
        if new is None:
            return False
        if new == old:
            return True
        parms = doc.xref_get_key(x, "DecodeParms")
        doc.update_stream(x, new, compress=False)
        doc.xref_set_key(x, "Filter", "/DCTDecode")
        if parms[0] != "null":
            doc.xref_set_key(x, "DecodeParms", parms[1])
        return True

    def _reload(self):
        """The same document, read anew; object numbers stay as they are."""
        data = self.doc.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        doc = pymupdf.open(stream=data, filetype="pdf")
        if doc.needs_pass:
            doc.authenticate(self._password)
        self.doc.close()
        self.doc = doc

    def _drop_files(self):
        """Attached files: in the name tree, as page attachments, and as associated files."""
        doc = self.doc
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            for a in [a for a in page.annots() if a.type[1] == "FileAttachment"]:
                page.delete_annot(a)
            page = None
        cat = doc.pdf_catalog()
        names = doc.xref_get_key(cat, "Names")
        if names[0] == "xref":
            doc.xref_set_key(int(names[1].split()[0]), "EmbeddedFiles", "null")
        elif names[0] == "dict":
            doc.xref_set_key(cat, "Names/EmbeddedFiles", "null")
        for key in ("Collection", "AF"):
            if doc.xref_get_key(cat, key)[0] != "null":
                doc.xref_set_key(cat, key, "null")
        for x in range(1, doc.xref_length()):
            try:
                if doc.xref_get_key(x, "Type")[1] in ("/Filespec", "/F", "/EmbeddedFile"):
                    if doc.xref_get_key(x, "Type")[1] == "/EmbeddedFile" or \
                            doc.xref_get_key(x, "EF")[0] != "null":
                        doc.update_object(x, "<<>>")
                        if doc.xref_is_stream(x):
                            doc.update_stream(x, b" ")
            except Exception:  # a damaged slot
                continue

    def _strip_javascript(self) -> int:
        """Remove every action that runs something: JavaScript (inline too), javascript: links,
        program launches, and XFA forms. Returns how many were found."""
        mu = pymupdf.mupdf
        pdf = mu.pdf_document_from_fz_document(self.doc)
        found = 0

        def code(val):
            """A script's text; fonts and pictures may bear the same name by chance."""
            return bool(val.m_internal) and (mu.pdf_is_string(val) or (
                mu.pdf_is_stream(val) and not mu.pdf_dict_gets(val, "Subtype").m_internal))

        def named(key, val):
            if key == "JS":
                return code(val)
            if key == "XFA":
                return mu.pdf_is_array(val) or code(val)
            return key == "JavaScript" and not (  # the list of scripts by name
                mu.pdf_dict_gets(val, "Type").m_internal or mu.pdf_dict_gets(val, "Subtype").m_internal)

        def runs(o):
            if not mu.pdf_is_dict(o):
                return False
            kind = mu.pdf_to_name(mu.pdf_dict_gets(o, "S"))
            if kind in ("JavaScript", "Launch") or code(mu.pdf_dict_gets(o, "JS")):
                return True
            if kind == "URI":
                uri = mu.pdf_to_text_string(mu.pdf_dict_gets(o, "URI")) or ""
                return "".join(uri.split()).lower().startswith(("javascript:", "vbscript:"))
            return False

        def clean(top):
            nonlocal found
            todo = [top]  # no recursion: hostile files nest thousands of levels
            while todo:
                o = todo.pop()
                if mu.pdf_is_dict(o):
                    drop = []
                    for i in range(mu.pdf_dict_len(o)):
                        key = mu.pdf_to_name(mu.pdf_dict_get_key(o, i))
                        val = mu.pdf_dict_get_val(o, i)
                        if named(key, val) or runs(val):
                            drop.append(key)
                        elif not mu.pdf_is_indirect(val):
                            todo.append(val)
                    for key in drop:
                        found += 1
                        mu.pdf_dict_dels(o, key)
                elif mu.pdf_is_array(o):
                    for i in reversed(range(mu.pdf_array_len(o))):
                        val = mu.pdf_array_get(o, i)
                        if runs(val):
                            found += 1
                            mu.pdf_array_delete(o, i)
                        elif not mu.pdf_is_indirect(val):
                            todo.append(val)
        scripts = []
        for x in range(1, self.doc.xref_length()):
            try:
                o = mu.pdf_load_object(pdf, x)
            except Exception:  # a freed slot
                continue
            if runs(o):
                scripts.append(x)
            else:
                clean(o)
        for x in scripts:
            found += 1
            self.doc.update_object(x, "<<>>")
        return found

    # -- security and size -------------------------------------------------
    def _op_protect(self, o):
        if self._protected:
            asked = pymupdf.open(stream=self.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_KEEP),
                                 filetype="pdf")
            try:
                level = asked.authenticate(o.get("auth", ""))
            finally:
                asked.close()
            if not level & 4:
                raise EngineError("owner_needed")
        if o["mode"] == "set" and not (o.get("user") or o.get("owner")):
            raise EngineError("bad_op")
        self._security = dict(o)
        return self._same()

    def _save_kw(self):
        """(keyword arguments for save, password that opens the result, must be protected)"""
        o = self._security
        if o is None:
            return {"encryption": pymupdf.PDF_ENCRYPT_KEEP}, self._password, self._protected
        if o["mode"] == "none":
            return {"encryption": pymupdf.PDF_ENCRYPT_NONE}, "", False
        perm = pymupdf.PDF_PERM_ACCESSIBILITY
        for key, bits in (("print", pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ),
                          ("copy", pymupdf.PDF_PERM_COPY),
                          ("modify", pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE),
                          ("annotate", pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM)):
            if o["perms"].get(key):
                perm |= bits
        user, owner = o.get("user") or "", o.get("owner") or o.get("user")
        return {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": user, "owner_pw": owner,
                "permissions": int(perm)}, user, True

    def _op_compress(self, o):
        self.doc.rewrite_images(dpi_threshold=o["dpi"] + 20, dpi_target=o["dpi"],
                                quality=o["quality"], set_to_gray=o.get("gray", False))
        try:
            self.doc.subset_fonts()
        except Exception:  # optional gain only
            pass
        return [-1] * self.doc.page_count

    # -- OCR ---------------------------------------------------------------
    def ocr_words(self, pno, languages, force=False):
        """Recognised words of a page as displayed; None when it already has text."""
        page = self.doc.load_page(pno)
        if not force and self._count(page) > 15:
            return None
        for lang in languages.split("+"):
            if not os.path.exists(os.path.join(TESSDATA, lang + ".traineddata")):
                raise EngineError("no_language")
        w, h = page.rect.width, page.rect.height
        dpi = min(300.0, 72 * math.sqrt(OCR_MAX_PIXELS / (w * h)))
        pix = page.get_pixmap(dpi=int(dpi), colorspace=pymupdf.csGRAY)
        tmp = pymupdf.open()
        scan = tmp.new_page(width=w, height=h)
        scan.insert_image(scan.rect, pixmap=pix)
        tp = scan.get_textpage_ocr(language=languages, dpi=int(dpi), full=True, tessdata=TESSDATA)
        words = _ocr_words(scan.get_text("rawdict", textpage=tp))
        tmp.close()
        return words

    def _op_textlayer(self, pages):
        """Write recognised words as invisible text."""
        for pno, words in pages.items():
            if not words:
                continue
            page = self.doc.load_page(pno)
            kind = _Type(page, "".join(sorted({c for w in words for c in w[4]})) + " ")
            shape = page.new_shape()
            for x0, y0, x1, y1, word in words:
                unit = kind.width(word, 1) or 1
                size = min(max((x1 - x0) / unit, 1.0), (y1 - y0) * 1.6 + 1)
                kind.put(Point(x0, y1 - (y1 - y0) * .22), word + " ", size, mode=3, to=shape)
            shape.commit()
        return self._same(*pages)

    # -- compare -----------------------------------------------------------
    def compare(self, path, password="", pictures=True):
        """Differences between this document (new) and another file (old).
        pictures=False: words only (ჩართალა compares the pictures itself, visual_diff)."""
        other = self._other_engine(path, password)  # open already when the pictures were compared

        def stream(eng):
            out = []
            for p in range(eng.doc.page_count):
                out += [(w[4], p, w[:4]) for w in _words(eng.chars(p))]
            return out
        a, b = stream(other), stream(self)
        changes = []

        def add(i1, i2, j1, j2, tag):
            changes.append({"kind": tag,
                            "old": " ".join(w[0] for w in a[i1:i2]),
                            "new": " ".join(w[0] for w in b[j1:j2]),
                            "old_at": [(w[1], w[2]) for w in a[i1:i2]],
                            "new_at": [(w[1], w[2]) for w in b[j1:j2]],
                            "page": b[min(j1, len(b) - 1)][1] if b else 0})
        # Equal prefix and suffix are cheap to drop and usually most of the text.
        lo = 0
        while lo < min(len(a), len(b)) and a[lo][0] == b[lo][0]:
            lo += 1
        ha, hb = len(a), len(b)
        while ha > lo and hb > lo and a[ha - 1][0] == b[hb - 1][0]:
            ha, hb = ha - 1, hb - 1
        sm = difflib.SequenceMatcher(None, [w[0] for w in a[lo:ha]], [w[0] for w in b[lo:hb]],
                                     autojunk=(ha - lo) * (hb - lo) > 25_000_000)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                add(lo + i1, lo + i2, lo + j1, lo + j2, tag)
        looked, pictures = pictures, []  # pages whose words changed are looked at too
        for p in range(min(other.doc.page_count, self.doc.page_count) if looked else 0):
            x, y = (e.doc.load_page(p).get_pixmap(dpi=60, colorspace=pymupdf.csGRAY)
                    for e in (other, self))
            if (x.width, x.height) != (y.width, y.height):
                pictures.append(p)
            elif x.samples != y.samples:
                import numpy as np
                diff = int((abs(np.frombuffer(x.samples, np.uint8).astype(np.int16)
                                - np.frombuffer(y.samples, np.uint8)) > 70).sum())
                if diff > 12:
                    pictures.append(p)
        counts = (other.doc.page_count, self.doc.page_count)
        return {"changes": changes, "pictures": pictures, "pages": counts}

    # -- visual comparison of drawings (ჩართალა) ------------------------------
    # dpi, ink: darker than this is a line, tolerance: px a line may move and stay the same,
    # join: px between pieces of one change, min_px: smaller changes are noise
    # auto: align when the pages do not lie on each other; good: share of lines that must meet
    # for the pages to count as lying on each other; fit: least share an alignment must reach
    # faint: lighter than ink, yet still a line: what a line is allowed to fade to on the other
    # side (a hairline put back by another program falls between pixels and comes out lighter;
    # on two real drawing sets: no false place from 140 levels up, many at 100); contrast:
    # darker than that over what the other side has is a change all the same (a letter over
    # a grey hatch: 221 levels darker, the median)
    # ecc: the pictures' own correlation at which an alignment is right whatever the edges say
    DIFF = {"dpi": 100, "ink": 200, "faint": 245, "contrast": 140, "tolerance": 2, "join": 8,
            "min_px": 12, "auto": True, "good": 0.97, "fit": 0.6, "ecc": 0.85}
    DIFF_MAX_PIXELS = 30_000_000
    DIFF_MAX_REGIONS = 200     # on one page; the biggest are kept, "more" says how many went

    def close(self):
        held, self._other = getattr(self, "_other", None), None
        if held:
            held[1].close()
        super().close()

    def _other_engine(self, path, password):
        """The other drawing, opened once for all of its pages."""
        held = getattr(self, "_other", None)
        if held and held[0] == (path, password):
            return held[1]
        if held:
            held[1].close()
        self._other = None
        other = Features()
        other.open(path, password)
        self._other = ((path, password), other)
        return other

    @staticmethod
    def _gray(eng, pno, k):
        import numpy as np
        dl, w, h = eng._list(pno)
        pix = dl.get_pixmap(matrix=view_matrix(w, h, k, 0), colorspace=pymupdf.csGRAY, alpha=0)
        rows = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)
        return np.ascontiguousarray(rows[:, :pix.width])

    @staticmethod
    def _meet(a, b, m, o, near):
        """How well old picture a, put on new picture b by m (pixels), lies on it: the larger
        share of either side's edges that the other side has close by. Added or removed
        drawing lowers only one of the two shares. Edges, not ink: a filled area or a picture
        has ink everywhere and would seem to lie on itself however far it moved."""
        import cv2
        a = cv2.warpAffine(a, m, (b.shape[1], b.shape[0]), flags=cv2.INTER_LINEAR,
                           borderValue=255)
        box = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

        def edges(img, level):
            ink = (img < level).astype("uint8")
            return ink & (1 - cv2.erode(ink, box))
        ea, eb = edges(a, o["ink"]), edges(b, o["ink"])
        na, nb = int(ea.sum()), int(eb.sum())
        if not na or not nb:
            return 1.0
        return max(int((ea & cv2.dilate(edges(b, o["faint"]), near)).sum()) / na,
                   int((eb & cv2.dilate(edges(a, o["faint"]), near)).sum()) / nb)

    @staticmethod
    def _by_points(points):
        """Two points on each drawing, [[old, new], [old, new]] -> old -> new: scale, turn
        and move, nothing else."""
        (o1, n1), (o2, n2) = [(complex(*o), complex(*n)) for o, n in points]
        if abs(o2 - o1) < 1 or abs(n2 - n1) < 1:
            raise EngineError("bad_points")
        s = (n2 - n1) / (o2 - o1)
        t = n1 - s * o1
        return [[s.real, -s.imag, t.real], [s.imag, s.real, t.imag]]

    def _align(self, a, b, o, near):
        """Old -> new in pixels, found from the drawings themselves: points both have in common
        (ORB), the outliers thrown out (RANSAC), then refined on smaller pictures (ECC).
        Added drawing has no partner on the old side, so it cannot pull the result.
        Returns (matrix or None, how well the edges meet, ECC's correlation). A page that is
        a picture has edges that change when drawn again: there the correlation tells."""
        import cv2
        import numpy as np
        best, fit, cc = None, 0.0, 0.0

        def small(img, f):
            return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else img

        def factor(side):  # one for both pictures: the scale between them must not change
            return min(1.0, side / max(a.shape + b.shape))

        def full(m, f):
            m = np.array(m, np.float64)
            m[:, 2] /= f
            return m

        def consider(m):
            nonlocal best, fit
            got = self._meet(a, b, m, o, near)
            if got > fit:
                best, fit = m, got
        f = factor(1600)
        sa, sb = small(a, f), small(b, f)
        orb = cv2.ORB_create(nfeatures=5000)
        ka, da = orb.detectAndCompute(sa, None)
        kb, db = orb.detectAndCompute(sb, None)
        if da is not None and db is not None and len(ka) >= 8 and len(kb) >= 8:
            pairs = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
            if len(pairs) >= 8:
                src = np.float32([ka[x.queryIdx].pt for x in pairs])
                dst = np.float32([kb[x.trainIdx].pt for x in pairs])
                m, keep = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                                      ransacReprojThreshold=3.0, maxIters=5000,
                                                      confidence=0.995)
                if m is not None and keep is not None and int(keep.sum()) >= 8:
                    consider(full(m, f))
        if best is not None:
            fe = factor(900)
            ea, eb = small(a, fe), small(b, fe)
            back = np.array(best, np.float64)
            back[:, 2] *= fe
            warp = cv2.invertAffineTransform(back).astype(np.float32)  # ECC: new -> old
            try:
                cc, warp = cv2.findTransformECC(
                    eb.astype(np.float32), ea.astype(np.float32), warp, cv2.MOTION_AFFINE,
                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-6), None, 5)
                refined = full(cv2.invertAffineTransform(warp), fe)
                if cc >= o["ecc"]:  # the pictures themselves agree: that settles it
                    best, fit = refined, max(fit, self._meet(a, b, refined, o, near))
                else:
                    consider(refined)
            except cv2.error:
                cc = 0.0  # did not converge: the points alone stay
        return best, fit, float(cc)

    def visual_diff(self, path, password, p_old, p_new, opts=None):
        """Where page p_old of the file at path (old) and page p_new of this document (new)
        differ as pictures. Regions: {"old": rect or None, "new": rect or None}, in displayed
        page points of each side; "old" is something removed or changed, "new" something added
        or changed.

        How the old page is put on the new one ("matrix" of the result, old -> new in points):
        opts["matrix"] as given; else two pairs of points in opts["points"] (by hand); else
        first as it is, and only when the pages do not lie on each other (and opts["auto"])
        aligned from the drawings. "ok" is False when that failed: aligning by hand helps."""
        import cv2
        import numpy as np
        o = dict(self.DIFF, **(opts or {}))
        other = self._other_engine(path, password)
        _, wo, ho = other._list(p_old)
        _, wn, hn = self._list(p_new)
        k = o["dpi"] / 72
        most = max(wo * ho, wn * hn) * k * k
        if most > self.DIFF_MAX_PIXELS:  # a big sheet: fewer pixels, not more memory
            k *= math.sqrt(self.DIFF_MAX_PIXELS / most)
        a, b = self._gray(other, p_old, k), self._gray(self, p_new, k)
        eye = [[1, 0, 0], [0, 1, 0]]
        r = max(int(o["tolerance"]), 2)
        loose = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        aligned, ok = "none", True
        if o.get("matrix"):
            m, aligned = np.array(o["matrix"], np.float64), "given"
        elif o.get("points"):
            m, aligned = np.array(self._by_points(o["points"]), np.float64), "manual"
        else:
            m = np.array(eye, np.float64)
            plain = self._meet(a, b, m, o, loose)
            if plain < o["good"] and o["auto"]:
                found, fit, cc = self._align(a, b, o, loose)
                if found is not None and (cc >= o["ecc"] or fit >= o["fit"] and fit > plain + 0.02):
                    m = found.copy()
                    m[:, 2] /= k  # pixels -> points
                    aligned = "auto"
                elif plain < o["fit"]:
                    ok = False  # as it is; two points by hand will do it
                # else: as it is lies on it well enough, and nothing does better
        mp = m.copy()
        mp[:, 2] *= k
        a = cv2.warpAffine(a, mp, (b.shape[1], b.shape[0]), flags=cv2.INTER_LINEAR,
                           borderValue=255)
        t = max(int(o["tolerance"]), 0)
        near = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * t + 1, 2 * t + 1))

        def gone(x, y):
            """Ink of x that y does not have: y has not even a faint line close by, or what y
            has close by is far lighter (new lines over an old grey fill). A line that only
            faded a little is no change."""
            darkest = cv2.erode(y, near).astype(np.int16)
            return ((x < o["ink"]) & ((darkest >= o["faint"])
                                      | (darkest - x > o["contrast"]))).astype(np.uint8)
        removed, added = gone(a, b), gone(b, a)
        match = 1 - float(removed.sum()) / max(float((a < o["ink"]).sum()), 1.0)
        del a, b
        regions = []
        change = removed | added
        if change.any():
            j = max(int(o["join"]), 1)
            grown = cv2.dilate(change, cv2.getStructuringElement(cv2.MORPH_RECT,
                                                                (2 * j + 1, 2 * j + 1)))
            n, labels = cv2.connectedComponents(grown, connectivity=8)
            del grown
            ys, xs = np.nonzero(change)
            lab = labels[ys, xs]
            was_old = removed[ys, xs].astype(bool)
            del labels

            def boxes(side):
                lb, x, y = lab[side], xs[side], ys[side]
                count = np.bincount(lb, minlength=n)
                lo_x, lo_y = np.full(n, 1 << 30), np.full(n, 1 << 30)
                hi_x, hi_y = np.full(n, -1), np.full(n, -1)
                np.minimum.at(lo_x, lb, x)
                np.minimum.at(lo_y, lb, y)
                np.maximum.at(hi_x, lb, x)
                np.maximum.at(hi_y, lb, y)
                return count, lo_x, lo_y, hi_x, hi_y
            rem, add = boxes(was_old), boxes(~was_old)
            back = cv2.invertAffineTransform(m)
            side_min = max(3, o["min_px"] // 4)
            for i in range(1, n):
                if rem[0][i] + add[0][i] < o["min_px"]:
                    continue
                new = old = None
                if add[0][i] >= side_min:
                    new = (add[1][i] / k, add[2][i] / k, (add[3][i] + 1) / k, (add[4][i] + 1) / k)
                if rem[0][i] >= side_min:
                    x0, y0, x1, y1 = (rem[1][i] / k, rem[2][i] / k,
                                      (rem[3][i] + 1) / k, (rem[4][i] + 1) / k)
                    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]]) @ back.T
                    old = (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
                if new or old:
                    regions.append({"old": old and tuple(round(float(v), 2) for v in old),
                                    "new": new and tuple(round(float(v), 2) for v in new)})
        more = max(len(regions) - self.DIFF_MAX_REGIONS, 0)
        if more:  # pages that do not match at all: thousands of places help nobody
            def area(g):
                r = g["new"] or g["old"]
                return (r[2] - r[0]) * (r[3] - r[1])
            regions = sorted(regions, key=area, reverse=True)[:self.DIFF_MAX_REGIONS]
        regions.sort(key=lambda g: ((g["new"] or g["old"])[1], (g["new"] or g["old"])[0]))
        return {"regions": regions, "more": more, "dpi": round(k * 72, 2),
                "matrix": m.tolist(), "ok": ok, "aligned": aligned, "match": round(match, 4)}

    def pdf_box(self, pno, rect):
        """Displayed rectangle -> PDF user space, for signature fields."""
        page = self.doc.load_page(pno)
        a, b = _to_pdf(page, rect[:2]), _to_pdf(page, rect[2:])
        return (min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))

    # -- text for the assistant ---------------------------------------------
    def page_texts(self, first=0, last=None):
        last = self.doc.page_count if last is None else last
        return ["\n".join("".join(c[4] for c in ln) for ln in self.chars(p))
                for p in range(first, last)]

    # -- archive format ----------------------------------------------------
    def pdfa(self, out):
        """Save a PDF/A-2b *candidate*. Returns the problems that rule it out."""
        doc = self._snapshot()
        bad = set()
        for p in range(doc.page_count):
            for font in doc.get_page_fonts(p, full=True):
                if font[2] == "Type3":
                    continue
                if font[1] in ("n/a", ""):
                    bad.add(font[3])
        problems = [f"ფონტი ჩაშენებული არ არის: {f}" for f in sorted(bad)]
        if doc.embfile_count():
            problems.append("ფაილს აქვს მიმაგრებული ფაილები")
        if problems:
            doc.close()
            return problems
        helper = Features()
        helper.doc = doc
        helper._drop_files()
        helper._strip_javascript()
        from PIL import ImageCms
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        x = doc.get_new_xref()
        doc.update_object(x, "<</N 3>>")
        doc.update_stream(x, icc)
        cat = doc.pdf_catalog()
        doc.xref_set_key(cat, "OutputIntents",
                         f"[<</Type/OutputIntent/S/GTS_PDFA1/OutputConditionIdentifier(sRGB)"
                         f"/Info(sRGB IEC61966-2.1)/DestOutputProfile {x} 0 R>>]")
        doc.xref_set_key(cat, "AA", "null")
        doc.xref_set_key(cat, "OpenAction", "null")
        title = html.escape((doc.metadata or {}).get("title") or "")
        doc.set_xml_metadata(
            '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" '
            'xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><pdfaid:part>2</pdfaid:part>'
            '<pdfaid:conformance>B</pdfaid:conformance><dc:title><rdf:Alt><rdf:li '
            f'xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title></rdf:Description>'
            '</rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
        doc.set_metadata({"title": (doc.metadata or {}).get("title") or ""})
        for p in range(doc.page_count):  # every annotation must print
            for a in doc.load_page(p).annots():
                if a.type[1] != "Popup":
                    a.set_flags((a.flags | 4) & ~(2 | 32))
        from .engine import _write
        if self._dropped:
            self._sweep(doc, self._gone_fields)
        _write(doc, out, "", False, encryption=pymupdf.PDF_ENCRYPT_NONE)
        doc.close()
        return []


__all__ = ["Features", "_open_source", "_words"]
