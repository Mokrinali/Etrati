"""PDF engine. Runs in a child process; the only module that touches MuPDF.

All coordinates returned are in points, in *displayed page space*: the page's
own /Rotate is already applied, the origin is top-left.
"""
from __future__ import annotations

import bisect
import os
import re
from collections import OrderedDict

import pymupdf

from . import legacy

pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)

# Smaller files are read fully, so other programs can still replace them on disk.
IN_MEMORY_LIMIT = 200 * 1024 * 1024
LIST_CACHE = 12


class EngineError(Exception):
    """An error with a stable code the UI can translate."""


def plain_char(c: str, old_font: bool = False) -> str:
    """One extracted character as a person would type it."""
    if c in "\u00a0\t":       # many fonts map the space glyph to NBSP
        return " "
    if c == "\u00ad":          # ... and the hyphen (minus!) glyph to the invisible soft hyphen
        return "-"
    return legacy.to_unicode(c) if old_font else c


def view_matrix(width: float, height: float, scale: float, rotation: int):
    """Matrix from displayed page space to rotated + scaled view space."""
    s = scale
    return {
        0: pymupdf.Matrix(s, 0, 0, s, 0, 0),
        90: pymupdf.Matrix(0, s, -s, 0, height * s, 0),
        180: pymupdf.Matrix(-s, 0, 0, -s, width * s, height * s),
        270: pymupdf.Matrix(0, -s, s, 0, 0, width * s),
    }[rotation % 360]


class Engine:
    def __init__(self):
        self.doc = None
        self._lists: OrderedDict[int, tuple] = OrderedDict()
        self._text_flags: dict[int, int] = {}
        self._password = ""
        self._size = 0
        self._protected = False
        self._dropped = False        # some page was removed since opening
        self._gone_fields: set[int] = set()  # form fields that were on removed pages

    # -- document ----------------------------------------------------------
    def open(self, path: str, password: str = "") -> dict:
        self.close()
        try:
            stat = os.stat(path)
            size = stat.st_size
            if size == 0:
                raise EngineError("damaged")
            if size <= IN_MEMORY_LIMIT:
                with open(path, "rb") as f:
                    doc = pymupdf.open(stream=f.read(), filetype="pdf")
            else:
                doc = pymupdf.open(path, filetype="pdf")
        except FileNotFoundError:
            raise EngineError("not_found")
        except PermissionError:
            raise EngineError("no_access")
        except EngineError:
            raise
        except Exception:
            raise EngineError("damaged")
        if doc.needs_pass and not doc.authenticate(password):
            doc.close()
            raise EngineError("wrong_password" if password else "needs_password")
        if doc.page_count == 0:
            doc.close()
            raise EngineError("damaged")
        self.doc = doc
        self._clip_crops(doc)
        self._password, self._size, self._dropped = password, size, False
        self._protected = bool((doc.metadata or {}).get("encryption"))
        self._gone_fields.clear()
        info = self._info()
        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
        perm = doc.permissions
        info.update({
            "metadata": meta,
            "repaired": bool(doc.is_repaired),
            "can_print": bool(perm & pymupdf.PDF_PERM_PRINT),
            "can_copy": bool(perm & pymupdf.PDF_PERM_COPY),
            "can_assemble": bool(perm & (pymupdf.PDF_PERM_ASSEMBLE | pymupdf.PDF_PERM_MODIFY)),
            "protected": self._protected,
            "signed": doc.get_sigflags() > 0,
            "bytes": size,
            "stamp": (stat.st_mtime_ns, stat.st_size),
        })
        return info

    @staticmethod
    def _clip_crops(doc):
        """A crop box reaching outside the paper is clipped when shown; make the file agree,
        because the coordinate matrices of such rotated pages are wrong otherwise."""
        for i in range(doc.page_count):
            try:
                if doc.xref_get_key(doc.page_xref(i), "CropBox")[0] == "null":
                    continue
                page = doc.load_page(i)
                media, crop = page.mediabox, page.cropbox
                paper = pymupdf.Rect(media.x0, 0, media.x1, media.height)
                inside = crop & paper
                if not inside.is_empty and any(abs(a - b) > .01 for a, b in zip(inside, crop)):
                    page.set_cropbox(inside)
            except Exception:  # a broken page is dealt with when it is shown
                continue

    def _info(self) -> dict:
        sizes, labels = [], []
        for i in range(self.doc.page_count):
            try:
                page = self.doc.load_page(i)
                r = page.rect
                sizes.append((max(r.width, 1.0), max(r.height, 1.0)))
                labels.append(page.get_label() or "")
            except Exception:  # one broken page must not block the document
                sizes.append((595.0, 842.0))
                labels.append("")
        return {"sizes": sizes, "labels": labels if any(labels) else [], "toc": self._toc()}

    def close(self):
        self._lists.clear()
        self._text_flags.clear()
        if self.doc is not None:
            self.doc.close()
            self.doc = None

    def _target_y(self, pno: int, to) -> float:
        if to is None:
            return 0.0
        # get_links() / get_toc() already answer in displayed page space.
        return min(max(float(pymupdf.Point(to).y), 0.0), self.doc[pno].rect.height)

    def _toc(self) -> list:
        """[(level, title, page index, y)]; page is -1 when there is no target."""
        out = []
        try:
            for level, title, page, dest in self.doc.get_toc(simple=False):
                if 1 <= page <= self.doc.page_count:
                    to = dest.get("to") if isinstance(dest, dict) else None
                    out.append((level, title, page - 1, self._target_y(page - 1, to)))
                else:
                    out.append((level, title, -1, 0.0))
        except Exception:
            return []
        return out

    # -- rendering ---------------------------------------------------------
    def _list(self, pno: int):
        if pno in self._lists:
            self._lists.move_to_end(pno)
            return self._lists[pno]
        page = self.doc.load_page(pno)
        entry = (page.get_displaylist(annots=1), page.rect.width, page.rect.height)
        self._lists[pno] = entry
        while len(self._lists) > LIST_CACHE:
            self._lists.popitem(last=False)
        return entry

    def render(self, pno: int, scale: float, rotation: int = 0, tile=None):
        """RGB pixels of a page, or of tile=(x0, y0, x1, y1) in view pixels."""
        dl, w, h = self._list(pno)
        m = view_matrix(w, h, scale, rotation)
        clip = None
        if tile is not None:
            clip = pymupdf.Rect(tile) * ~m
        pix = dl.get_pixmap(matrix=m, colorspace=pymupdf.csRGB, alpha=0, clip=clip)
        return pix.width, pix.height, pix.stride, bytes(pix.samples)

    # -- content -----------------------------------------------------------
    def _flags(self, page) -> int:
        """Text flags for a page.

        Some programs (banks, Oracle reports) write fonts without a Unicode table, with the
        Unicode value itself as the character code. Those codes are used only when the page
        has such a font and they really give readable letters; elsewhere they are glyph
        numbers, and showing those would be garbage.
        """
        base = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
        key = page.xref
        if key not in self._text_flags:
            flags = base
            if any(f[2] == "Type0" and self.doc.xref_get_key(f[0], "ToUnicode")[0] == "null"
                   for f in page.get_fonts(full=True)):
                codes = base | pymupdf.TEXT_CID_FOR_UNKNOWN_UNICODE
                lost = page.get_text("text", flags=base).count("\ufffd")
                if lost:
                    text = page.get_text("text", flags=codes)
                    seen = [c for c in text if not c.isspace()]
                    good = sum(1 for c in seen if "\u10d0" <= c <= "\u10ff" or " " < c < "\x7f"
                               or "\u0400" <= c <= "\u04ff" or "\u2010" <= c <= "\u2044"
                               or c in "«»№€₾°§©")
                    if seen and good > .9 * len(seen):
                        flags = codes
            self._text_flags[key] = flags
        return self._text_flags[key]

    def chars(self, pno: int) -> list:
        """Lines of text: [[(x0, y0, x1, y1, char), ...], ...] in reading order."""
        page = self.doc.load_page(pno)
        m = page.rotation_matrix
        lines = []
        for block in page.get_text("rawdict", flags=self._flags(page))["blocks"]:
            for line in block.get("lines", ()):
                cur = []
                for span in line["spans"]:
                    old = legacy.is_legacy(span["font"])
                    for c in span["chars"]:
                        r = pymupdf.Rect(c["bbox"]) * m
                        cur.append((r.x0, r.y0, r.x1, r.y1, plain_char(c["c"], old)))
                if cur:
                    lines.append(cur)
        return lines

    def search(self, pno: int, text: str) -> list:
        """[(rect, text of the line)]; a hit that wraps onto a new line yields several.

        Searches the text as chars() reads it. MuPDF's own search misses a few words in real
        files and reports some at the wrong place, which would be dangerous for redaction.
        """
        want = " ".join(text.split()).lower()
        if not want:
            return []
        lines = self.chars(pno)
        out, seen = [], set()
        for joined in (False, True):  # second pass: words divided by a hyphen at a line end
            if joined and not any(ln[-1][4] == "-" for ln in lines if ln):
                break
            hay, refs = [], []
            for n, ln in enumerate(lines):
                end = len(ln)
                while end and ln[end - 1][4].isspace():
                    end -= 1
                glue = joined and end > 1 and ln[end - 1][4] == "-"
                for k in range(end - 1 if glue else end):
                    c = ln[k][4]
                    if c.isspace():
                        if hay and hay[-1] == " ":
                            continue
                        c = " "
                    for low in c.lower() or c:  # some characters lower to two
                        hay.append(low)
                        refs.append((n, k))
                if not glue and hay and hay[-1] != " ":
                    hay.append(" ")
                    refs.append(None)
            hay = "".join(hay)
            at = hay.find(want)
            while at >= 0:
                parts: dict = {}
                for ref in refs[at:at + len(want)]:
                    if ref is not None:
                        parts.setdefault(ref[0], []).append(ref[1])
                for n, ks in parts.items():
                    key = (n, ks[0], ks[-1])
                    if key not in seen:
                        seen.add(key)
                        box = [lines[n][k] for k in ks]
                        rect = (min(c[0] for c in box), min(c[1] for c in box),
                                max(c[2] for c in box), max(c[3] for c in box))
                        line = "".join(c[4] for c in lines[n])
                        lo = max(ks[0] - 45, 0)
                        out.append((rect, " ".join(line[lo:ks[-1] + 70].split()), n, ks[0]))
                at = hay.find(want, at + len(want))
        out.sort(key=lambda h: h[2:])
        return [h[:2] for h in out]

    def links(self, pno: int) -> list:
        page = self.doc.load_page(pno)
        out = []
        for ln in page.get_links():
            rect = tuple(ln["from"])  # already in displayed page space
            kind = ln.get("kind")
            if kind == pymupdf.LINK_URI:
                out.append({"rect": rect, "uri": ln.get("uri", "")})
            elif kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED):
                target = ln.get("page", -1)
                if isinstance(target, int) and 0 <= target < self.doc.page_count:
                    out.append({"rect": rect, "page": target,
                                "y": self._target_y(target, ln.get("to"))})
        return out  # launch / remote-file actions are deliberately ignored


# ---------------------------------------------------------------------------
# Page operations. Each op is a plain tuple, so the UI can keep the list and
# replay it on the untouched original: that is both undo and crash recovery.
# ---------------------------------------------------------------------------

MIN_PAGE = 20.0  # points; a crop may not leave less than this


def _open_source(path: str, password: str):
    try:
        with open(path, "rb") as f:
            src = pymupdf.open(stream=f.read(), filetype="pdf")
    except FileNotFoundError:
        raise EngineError("not_found")
    except PermissionError:
        raise EngineError("no_access")
    except Exception:
        raise EngineError("damaged")
    if src.needs_pass and not src.authenticate(password):
        raise EngineError("wrong_password" if password else "needs_password")
    if not src.permissions & (pymupdf.PDF_PERM_ASSEMBLE | pymupdf.PDF_PERM_COPY):
        raise EngineError("not_allowed")
    return src


def _copy_pages(dst, src, pages: list[int], at: int):
    """Insert src pages into dst before index `at`, keeping their links."""
    at = min(max(at, 0), dst.page_count)
    before = dst.page_count
    run_start = 0
    while run_start < len(pages):  # copy consecutive runs, shared resources stay shared
        end = run_start
        while end + 1 < len(pages) and pages[end + 1] == pages[end] + 1:
            end += 1
        where = at + run_start
        dst.insert_pdf(src, from_page=pages[run_start], to_page=pages[end],
                       start_at=where if where < dst.page_count else -1,
                       links=0, annots=1, widgets=1, final=0)
        run_start = end + 1
    if dst.page_count != before + len(pages):
        raise EngineError("copy_failed")
    where = {}
    for i, p in enumerate(pages):
        where.setdefault(p, at + i)
    for i, p in enumerate(pages):
        src_page = src.load_page(p)
        page = dst.load_page(at + i)
        for ln in src_page.get_links():
            # insert_link() wants unrotated coordinates, get_links() gives displayed ones.
            new = {"from": pymupdf.Rect(ln["from"]) * src_page.derotation_matrix}
            if ln.get("kind") == pymupdf.LINK_URI and ln.get("uri"):
                new.update(kind=pymupdf.LINK_URI, uri=ln["uri"])
            elif (ln.get("kind") in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED)
                  and ln.get("page") in where):
                to = pymupdf.Point(ln.get("to") or (0, 0)) * src[ln["page"]].derotation_matrix
                new.update(kind=pymupdf.LINK_GOTO, page=where[ln["page"]], to=to)
            else:
                continue
            page.insert_link(new)


def _toc_for(src, pages: list[int], at: int, shift: int = 0) -> list:
    """Bookmarks of src that point into `pages`, retargeted to their new place."""
    where = {}
    for i, p in enumerate(pages):
        where.setdefault(p, at + i)
    out, depth = [], 0
    for level, title, page, dest in src.get_toc(simple=False):
        if page - 1 not in where:
            continue
        level = min(level + shift, depth + 1)  # a kept child of a dropped parent moves up
        depth = level
        to = pymupdf.Point((dest or {}).get("to") or (0, 0))  # set_toc() wants displayed ones
        out.append([level, title, where[page - 1] + 1,
                    {"kind": pymupdf.LINK_GOTO, "page": where[page - 1], "to": to, "zoom": 0}])
    return out


def _doc_toc(doc) -> list:
    """The document's own bookmarks in a form set_toc() accepts unchanged."""
    out = []
    for level, title, page, dest in doc.get_toc(simple=False):
        dest = dict(dest or {})
        dest.pop("xref", None)
        out.append([level, title, page, dest])
    return out


def _to_pdf(page, point):
    """Displayed point -> PDF user space. transformation_matrix is wrong on rotated pages
    whose boxes do not start at the origin, so the boxes are used directly."""
    x, y = pymupdf.Point(point)
    w, h = page.rect.width, page.rect.height
    p = pymupdf.Point(*{0: (x, y), 90: (y, w - x), 180: (w - x, h - y),
                        270: (h - y, x)}[page.rotation % 360])
    media = page.mediabox
    # A crop box larger than the paper is clipped when shown, so clip it here too.
    crop = page.cropbox & pymupdf.Rect(media.x0, 0, media.x1, media.height)
    if crop.is_empty:
        crop = pymupdf.Rect(media.x0, 0, media.x1, media.height)
    return pymupdf.Point(crop.x0 + p.x, media.y1 - crop.y0 - p.y)


def _set_toc(doc, toc: list):
    """set_toc(), then exact targets: the library misplaces them on rotated pages."""
    doc.set_toc(toc)
    for xref, item in zip(doc.get_outline_xrefs(), toc):
        page_no, dest = item[2], item[3] if len(item) > 3 else None
        if not isinstance(dest, dict) or not 1 <= page_no <= doc.page_count:
            continue
        page = doc.load_page(page_no - 1)
        to = pymupdf.Point(dest.get("to") or (0, 0))
        pdf = _to_pdf(page, to)
        doc.xref_set_key(xref, "A", "null")
        doc.xref_set_key(xref, "Dest",
                         f"[{page.xref} 0 R/XYZ {pdf.x:g} {pdf.y:g} {dest.get('zoom') or 0:g}]")


def _write(doc, path: str, password: str = "", protected: bool = False, **kw):
    """Save, then prove the result opens and has every page before anyone uses it."""
    try:
        doc.save(path, garbage=3, deflate=True, **kw)
    except Exception as e:
        raise EngineError("no_access" if isinstance(e, PermissionError) or
                          "Permission" in str(e) or "cannot open" in str(e) else "save_failed")
    check = pymupdf.open(path, filetype="pdf")
    try:
        # The password comes first: metadata is unreadable before it.
        if check.needs_pass and not check.authenticate(password):
            raise EngineError("save_failed")
        if protected and not (check.metadata or {}).get("encryption"):
            raise EngineError("protection_lost")
        if check.page_count != doc.page_count:
            raise EngineError("save_failed")
        for i in range(doc.page_count):
            a, b = check[i].rect, doc[i].rect
            if abs(a.width - b.width) > 1 or abs(a.height - b.height) > 1:
                raise EngineError("save_failed")
        step = max(doc.page_count // 25, 1)
        for i in range(0, doc.page_count, step):
            if check[i].get_text() != doc[i].get_text():
                raise EngineError("save_failed")
    finally:
        check.close()


def _prune_field(doc, xref, gone: set, dead: set, depth: int) -> bool:
    """False when a form field has nothing left after its widgets were removed."""
    if xref in gone or depth > 50:
        return False
    kind, value = doc.xref_get_key(xref, "Kids")
    if kind != "array":
        return True
    kids = [int(r) for r in re.findall(r"(\d+) \d+ R", value)]
    kept = [k for k in kids if _prune_field(doc, k, gone, dead, depth + 1)]
    if len(kept) != len(kids):
        doc.xref_set_key(xref, "Kids", "[" + " ".join(f"{k} 0 R" for k in kept) + "]")
    if not kept:
        dead.add(xref)
    return bool(kept)


class EditEngine(Engine):
    def replay(self, path: str, password: str, ops: list) -> dict:
        """Open the untouched original and apply all ops."""
        info = self.open(path, password)
        try:
            for op in ops:
                self._apply(op)
        except Exception:
            self.close()  # never leave a half-rebuilt document that could be saved
            raise
        if ops:
            info.update(self._info())
        info["mapping"] = None
        return info

    def apply(self, op: tuple) -> dict:
        mapping = self._apply(op)
        info = self._info()
        info["mapping"] = mapping  # new index -> old index whose picture still fits, or -1
        return info

    def _apply(self, op: tuple) -> list:
        self._lists.clear()
        self._text_flags.clear()
        mapping = getattr(self, "_op_" + op[0])(*op[1:])
        self._lists.clear()
        self._text_flags.clear()
        return mapping

    def _snapshot(self):
        # KEEP matters: a plain tobytes() strips encryption from self.doc itself.
        data = self.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_KEEP)
        copy = pymupdf.open(stream=data, filetype="pdf")
        if copy.needs_pass:
            copy.authenticate(self._password)
        return copy

    def _op_rotate(self, pages, delta):
        for p in pages:
            page = self.doc.load_page(p)
            page.set_rotation((page.rotation + delta) % 360)
        changed = set(pages)
        return [-1 if i in changed else i for i in range(self.doc.page_count)]

    def _op_order(self, order):
        """Reorder and/or delete: the new document is these old pages."""
        n = self.doc.page_count
        if not order:
            raise EngineError("no_pages")
        if len(set(order)) != len(order) or not all(0 <= p < n for p in order):
            raise EngineError("bad_op")
        # Document.select() would do all this, but MuPDF 1.28.6 corrupts memory when
        # it is called repeatedly, so pages are deleted and moved one by one.
        keep = set(order)
        gone = [p for p in range(n) if p not in keep]
        if gone:
            self._dropped = True
            self._drop_bookmarks(set(gone))
            for p in gone:
                page = self.doc.load_page(p)
                for widget in list(page.widgets()):
                    self._gone_fields.add(widget.xref)
            page = None
            self._prune_tags({self.doc.page_xref(p) for p in gone})
            self.doc.delete_pages(gone)
        self._permute([p for p in range(n) if p in keep], list(order))
        return list(order)

    def _drop_tags(self):
        cat = self.doc.pdf_catalog()
        for key in ("StructTreeRoot", "MarkInfo"):
            if self.doc.xref_get_key(cat, key)[0] != "null":
                self.doc.xref_set_key(cat, key, "null")

    def _prune_tags(self, gone: set):
        """Remove what the tag tree says about these pages (by xref); keep the rest.

        The tree quotes page text (ActualText, Alt), so nothing about a removed page may stay.
        Anything unexpected drops the whole tree: safe, only less accessible.
        """
        cat = self.doc.pdf_catalog()
        if self.doc.xref_get_key(cat, "StructTreeRoot")[0] == "null":
            return
        try:
            self._prune_tree(gone)
        except Exception:
            self._drop_tags()

    def _prune_tree(self, gone):
        mu = pymupdf.mupdf
        pdf = mu.pdf_document_from_fz_document(self.doc)
        root = mu.pdf_dict_gets(mu.pdf_dict_gets(mu.pdf_trailer(pdf), "Root"), "StructTreeRoot")
        if not mu.pdf_is_dict(root):
            raise ValueError
        seen, budget = set(), [400_000]

        def page_of(o, inherited):
            pg = mu.pdf_dict_gets(o, "Pg")
            return mu.pdf_to_num(pg) if mu.pdf_is_indirect(pg) else inherited

        # Iterative post-order walk: (element, page, its K entries still to visit, kept, changed)
        def entries(o):
            k = mu.pdf_dict_gets(o, "K")
            if mu.pdf_is_array(k):
                return [mu.pdf_array_get(k, i) for i in range(mu.pdf_array_len(k))]
            return [k] if k.m_internal else []

        stack = [[root, 0, entries(root), [], False]]
        while stack:
            budget[0] -= 1
            if budget[0] < 0:
                raise ValueError
            o, pg, todo, kept, changed = stack[-1]
            if todo:
                child = todo.pop(0)
                if mu.pdf_is_int(child):          # marked content on this element's page
                    if pg in gone:
                        stack[-1][4] = True
                    else:
                        kept.append(child)
                    continue
                if not mu.pdf_is_dict(child):
                    stack[-1][4] = True
                    continue
                num = mu.pdf_to_num(child) if mu.pdf_is_indirect(child) else 0
                kind = mu.pdf_to_name(mu.pdf_dict_gets(child, "Type"))
                here = page_of(child, pg)
                if kind in ("MCR", "OBJR"):
                    if here in gone:
                        stack[-1][4] = True
                    else:
                        kept.append(child)
                    continue
                if num and num in seen:
                    raise ValueError              # not a tree
                seen.add(num or id(child))
                stack.append([child, here, entries(child), [], False])
                continue
            stack.pop()
            if changed:
                arr = mu.pdf_new_array(pdf, max(len(kept), 1))
                for c in kept:
                    mu.pdf_array_push(arr, c)
                mu.pdf_dict_puts(o, "K", arr)
                for key in ("ActualText", "Alt", "E", "T", "A", "ID"):
                    mu.pdf_dict_dels(o, key)      # may describe what was removed
            if stack:
                if not kept and not pg:  # text that belongs to no known page: cannot be judged
                    changed = True
                empty = not kept and (changed or pg in gone)
                if empty:
                    for key in ("ActualText", "Alt", "E", "T", "A", "ID", "K"):
                        mu.pdf_dict_dels(o, key)
                    stack[-1][4] = True
                else:
                    stack[-1][3].append(o)
        # Indexes into the tree: rebuilding them needs the pages, so entries of removed pages go.
        mu.pdf_dict_dels(root, "IDTree")
        dead = set()
        for x in gone:
            page = mu.pdf_load_object(pdf, x)
            sp = mu.pdf_dict_gets(page, "StructParents")
            if mu.pdf_is_int(sp):
                dead.add(mu.pdf_to_int(sp))
            annots = mu.pdf_dict_gets(page, "Annots")
            if mu.pdf_is_array(annots):
                for i in range(mu.pdf_array_len(annots)):
                    sp = mu.pdf_dict_gets(mu.pdf_array_get(annots, i), "StructParent")
                    if mu.pdf_is_int(sp):
                        dead.add(mu.pdf_to_int(sp))
        nodes = [mu.pdf_dict_gets(root, "ParentTree")]
        while nodes:
            budget[0] -= 1
            if budget[0] < 0:
                raise ValueError
            node = nodes.pop()
            if not mu.pdf_is_dict(node):
                continue
            kids = mu.pdf_dict_gets(node, "Kids")
            if mu.pdf_is_array(kids):
                nodes += [mu.pdf_array_get(kids, i) for i in range(mu.pdf_array_len(kids))]
            nums = mu.pdf_dict_gets(node, "Nums")
            if mu.pdf_is_array(nums):
                for i in reversed(range(0, mu.pdf_array_len(nums) - 1, 2)):
                    if mu.pdf_to_int(mu.pdf_array_get(nums, i)) in dead:
                        mu.pdf_array_delete(nums, i + 1)
                        mu.pdf_array_delete(nums, i)

    def _permute(self, cur, order):
        """Turn page sequence cur into order with the fewest moves."""
        want = {p: i for i, p in enumerate(order)}
        tails, idx, back = [], [], [-1] * len(cur)  # longest run already in order
        for i, p in enumerate(cur):
            k = bisect.bisect_left(tails, want[p])
            if k == len(tails):
                tails.append(want[p])
                idx.append(i)
            else:
                tails[k], idx[k] = want[p], i
            back[i] = idx[k - 1] if k else -1
        stay, i = set(), idx[-1] if idx else -1
        while i >= 0:
            stay.add(cur[i])
            i = back[i]
        for at, p in enumerate(order):
            if p in stay:
                continue
            j = cur.index(p)
            to = cur.index(order[at - 1]) + 1 if at else 0
            if to not in (j, j + 1):
                self.doc.move_page(j, to if to < len(cur) else -1)
                cur.insert(to, p)
                cur.pop(j if j < to else j + 1)
        if cur != order:
            raise EngineError("copy_failed")

    def _drop_bookmarks(self, gone: set):
        """Remove bookmarks of pages about to be deleted: their titles quote them."""
        toc = _doc_toc(self.doc)
        out, depth = [], 0
        for level, title, page, dest in toc:
            if page - 1 in gone:
                continue
            depth = min(level, depth + 1)
            out.append([depth, title, page, dest])
        if len(out) != len(toc):
            _set_toc(self.doc, out)

    def _op_duplicate(self, pages):
        pages = sorted(set(pages))
        copy = self._snapshot()
        mapping = list(range(self.doc.page_count))
        at = pages[-1] + 1
        _copy_pages(self.doc, copy, pages, at)
        mapping[at:at] = pages
        return mapping

    def _op_insert(self, path, password, pages, at):
        src = _open_source(path, password)
        pages = list(range(src.page_count)) if pages is None else list(pages)
        if not pages or not all(0 <= p < src.page_count for p in pages):
            raise EngineError("no_pages")
        n = self.doc.page_count
        _copy_pages(self.doc, src, pages, at)
        self._add_toc(_toc_for(src, pages, at), at)
        return list(range(at)) + [-1] * len(pages) + list(range(at, n))

    def _op_blank(self, at, width, height):
        n = self.doc.page_count
        self.doc.new_page(pno=at if at < n else -1, width=width, height=height)
        return list(range(at)) + [-1] + list(range(at, n))

    def _add_toc(self, extra, at):
        """Put bookmarks of newly inserted pages where they belong in the list."""
        if not extra:
            return
        out, placed = [], False
        for item in _doc_toc(self.doc):
            if not placed and item[0] == 1 and item[2] - 1 >= at:
                out += extra
                placed = True
            out.append(item)
        _set_toc(self.doc, out if placed else out + extra)

    def _op_crop(self, pages, margins):
        """margins = (left, top, right, bottom) as displayed; None removes cropping."""
        n = self.doc.page_count
        for p in pages:
            page = self.doc.load_page(p)
            if margins is None:
                self.doc.xref_set_key(page.xref, "CropBox", "null")
                page = None
                page = self.doc.load_page(p)
                page.set_cropbox(pymupdf.Rect(page.mediabox.x0, 0, page.mediabox.x1,
                                              page.mediabox.height))
                continue
            l, t, r, b = margins
            # Turn displayed margins into those of the unrotated page.
            l, t, r, b = {0: (l, t, r, b), 90: (t, r, b, l),
                          180: (r, b, l, t), 270: (b, l, t, r)}[page.rotation]
            c = page.cropbox
            new = pymupdf.Rect(c.x0 + l, c.y0 + t, c.x1 - r, c.y1 - b)
            if min(l, t, r, b) < 0 or new.width < MIN_PAGE or new.height < MIN_PAGE:
                raise EngineError("crop_too_small")
            page.set_cropbox(new)
        changed = set(pages)
        return [-1 if i in changed else i for i in range(n)]

    # -- output ------------------------------------------------------------
    @staticmethod
    def _sweep(doc, gone_fields):
        """Erase removed pages that something in the file still refers to."""
        live = {doc.page_xref(i) for i in range(doc.page_count)}
        dead = set()
        for x in range(1, doc.xref_length()):
            if x in live:
                continue
            try:
                kind = doc.xref_get_key(x, "Type")[1]
            except Exception:  # a freed slot: nothing there to keep
                continue
            if kind == "/Page":
                dead.add(x)
        form = doc.xref_get_key(doc.pdf_catalog(), "AcroForm")
        if gone_fields and form[0] != "null":
            owner, prefix = (int(form[1].split()[0]), "") if form[0] == "xref" else (
                doc.pdf_catalog(), "AcroForm/")
            for key in ("Fields", "CO"):
                kind, value = doc.xref_get_key(owner, prefix + key)
                holder, name = owner, prefix + key
                if kind == "xref":  # the array is an object of its own
                    holder, name = int(value.split()[0]), None
                    value = doc.xref_object(holder, compressed=True)
                elif kind != "array":
                    continue
                kept = [r for r in map(int, re.findall(r"(\d+) \d+ R", value))
                        if _prune_field(doc, r, gone_fields, dead, 0)]
                text = "[" + " ".join(f"{r} 0 R" for r in kept) + "]"
                if name is None:
                    doc.update_object(holder, text)
                else:
                    doc.xref_set_key(holder, name, text)
        for x in dead | gone_fields:
            try:
                doc.update_object(x, "null")
            except Exception:
                continue

    def _save_kw(self):
        return {"encryption": pymupdf.PDF_ENCRYPT_KEEP}, self._password, self._protected

    def save(self, out: str) -> dict:
        # Saving renumbers objects, so it works on a copy: editing can go on safely.
        copy = self._snapshot()
        if self._dropped:
            self._sweep(copy, self._gone_fields)
        kw, password, protected = self._save_kw()
        _write(copy, out, password, protected, **kw)
        copy.close()
        return {"bytes": os.path.getsize(out)}

    def export(self, parts: list) -> list:
        """Write page groups to new files: [(pages, path), ...]."""
        # A separate copy: insert_pdf() must never see the document being edited.
        src = self._snapshot()
        for pages, path in parts:
            out = pymupdf.open()
            _copy_pages(out, src, list(pages), 0)
            toc = _toc_for(src, list(pages), 0)
            if toc:
                _set_toc(out, toc)
            _write(out, path)
            out.close()
        return [p for _, p in parts]

    def inspect(self, path: str, password: str = "") -> dict:
        src = _open_source(path, password)
        n, protected = src.page_count, bool((src.metadata or {}).get("encryption"))
        src.close()
        return {"pages": n, "protected": protected}

    def merge(self, items: list, bookmarks: bool, path: str) -> dict:
        """items = [(path, password, pages or None, title), ...]"""
        out, toc = pymupdf.open(), []
        for src_path, password, pages, title in items:
            src = _open_source(src_path, password)
            pages = list(range(src.page_count)) if pages is None else list(pages)
            at = out.page_count
            _copy_pages(out, src, pages, at)
            if bookmarks:
                toc.append([1, title, at + 1])
            toc += _toc_for(src, pages, at, 1 if bookmarks else 0)
            src.close()
        fixed, depth = [], 0
        for item in toc:  # levels must never jump by more than one
            depth = min(item[0], depth + 1)
            fixed.append([depth, *item[1:]])
        if fixed:
            _set_toc(out, fixed)
        _write(out, path)
        return {"pages": out.page_count}
