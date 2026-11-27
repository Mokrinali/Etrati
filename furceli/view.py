"""Continuous page view: layout, tiled painting, zoom, text selection, links.

Layout and scroll positions are in device pixels so that tiles are drawn 1:1
and stay sharp at fractional Windows scaling (125 %, 150 %).
"""
from __future__ import annotations

import bisect
import math
import weakref
from collections import OrderedDict

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QAbstractScrollArea

from . import theme
from .client import P_TEXT, P_VISIBLE

TILE = 1024
CACHE_BYTES = 256 * 1024 * 1024
HIDDEN_BYTES = 32 * 1024 * 1024    # what a tab in the background may keep
TOTAL_BYTES = 384 * 1024 * 1024    # all tabs together
FAR_PAGES = 60                     # text of pages farther away is no longer waited for
KEEP_PAGES = 400                   # pages whose text, links and fields are remembered
ZOOM_MIN, ZOOM_MAX = 0.1, 8.0
GAP = 14  # logical px between pages


_VIEWS: "weakref.WeakSet[PageView]" = weakref.WeakSet()  # every page view, for _share()


class Shared:
    """What the panes of one document have in common (ჩართალა: one document, two panes):
    what is marked on the document, and the pen. Each pane keeps its own place and zoom, its
    own selection and the shape being drawn in it."""

    def __init__(self):
        self.views: list = []              # the panes, the first one first
        self.marks: dict[int, list] = {}   # pending redaction rectangles
        self.hits: dict[int, list] = {}    # search results
        self.current_hit = None            # (page, index)
        self.diff: dict[int, list] = {}    # comparison highlights
        self.frames: dict[int, list] = {}  # (rectangle, colour token, current) outlines
        self.tool_color = theme.color("ink-pen")
        self.tool_width = 2.0


def _shared(name):
    return property(lambda self: getattr(self.shared, name),
                    lambda self, value: setattr(self.shared, name, value))


def to_image(value) -> QImage:
    w, h, stride, data = value
    return QImage(data, w, h, stride, QImage.Format.Format_RGB888).copy()


class PageView(QAbstractScrollArea):
    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    hovered = Signal(str)
    uriActivated = Signal(str)
    message = Signal(str)
    cropDrawn = Signal(int, object)    # page, rectangle to keep (points)
    toolDone = Signal(str, int, object)  # tool, page, shape data in page points
    clicked = Signal(int, object)      # plain click on a page (points)
    contextAt = Signal(int, object, object)  # page, point, global position
    activated = Signal()               # this pane is the one being worked in

    marks, hits, current_hit = _shared("marks"), _shared("hits"), _shared("current_hit")
    diff, frames = _shared("diff"), _shared("frames")
    tool_color, tool_width = _shared("tool_color"), _shared("tool_width")

    def __init__(self, client, thumbs, parent=None, shared=None):
        super().__init__(parent)
        self.client, self.thumbs = client, thumbs
        self.shared = shared or Shared()   # another pane of the same document shares it
        self.shared.views.append(self)
        self._wanted: dict = {}            # tiles this pane asked for last
        self._near_text = (0, -1)          # pages whose text this pane may still want
        self.sizes: list = []
        self.can_copy = True
        self.zoom, self.fit = 1.0, "width"
        self.rotation, self.two_up = 0, False
        self.rects: list[QRect] = []
        self._row_tops: list[int] = []
        self._rows: list[list[int]] = []
        self._content = (0, 0)
        self._tiles: OrderedDict = OrderedDict()
        self._tile_bytes = 0
        _VIEWS.add(self)
        self._bad: set = set()
        self.chars: dict[int, list] = {}
        self.links: dict[int, list] = {}
        self._sel_a = self._sel_b = None   # (page, caret)
        self._dragging = False
        self._press_link = None
        self._page = 0
        self.rev = 0                       # bumped by every edit; part of all keys
        self.frozen = False                # an edit is running: keep the last picture
        self._crop = None                  # None, or [page, start point, end point]
        self.cropping = False
        self.tool = "select"
        self._shape = None                 # [page, [points]] being drawn
        self.widgets: dict[int, list] = {}
        self._stale: dict = {}             # last picture of a page that was just edited
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        # Always on, so fitting to width cannot flip-flop with the bar.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)

    # -- document ----------------------------------------------------------
    def set_document(self, sizes, can_copy):
        self.sizes, self.can_copy = sizes, can_copy
        self._relayout()

    lost_marks = 0

    def reload(self, sizes, mapping, still=False, primary=True):
        """The document was edited. mapping: new page -> old page, or None.
        primary: the first pane also moves what all panes share (once, not once a pane)."""
        old = self._page
        old_rev, self.rev = self.rev, self.rev + 1
        same = (mapping is not None and list(sizes) == list(self.sizes)
                and all(o in (-1, i) for i, o in enumerate(mapping)))
        tiles, self._tiles, self._stale = self._tiles, OrderedDict(), {}
        self._tile_bytes = 0
        for new, o in enumerate(mapping or ()):
            for k, img in tiles.items():
                if k[1] == o and k[-1] == old_rev:
                    self._tiles[("tile", new, *k[2:-1], self.rev)] = img
                    self._tile_bytes += img.sizeInBytes()
                elif o < 0 and same and k[1] == new and k[-1] == old_rev:
                    self._stale[k[:-1]] = img  # shown until the new picture arrives
        keep = {o: new for new, o in enumerate(mapping or ()) if o >= 0}
        for name in ("chars", "links", "widgets"):
            store = getattr(self, name)
            setattr(self, name, {keep[p]: v for p, v in store.items() if p in keep})
        self._bad.clear()
        self.hits, self.current_hit, self.diff, self.frames = {}, None, {}, {}
        # Redaction marks follow their page. On an edited page they stay only when the
        # edit cannot have moved anything (the caller knows).
        if primary:
            old_marks = {p: m for p, m in self.marks.items() if m}
            self.marks = {}
            for new, o in enumerate(mapping or ()):
                if o in old_marks:
                    self.marks[new] = list(old_marks[o])
                elif o < 0 and same and still and new in old_marks:
                    self.marks[new] = list(old_marks[new])
            kept = {o if o >= 0 else new for new, o in enumerate(mapping or ())
                    if new in self.marks}
            self.lost_marks = sum(len(m) for p, m in old_marks.items() if p not in kept)
        self._sel_a = self._sel_b = None
        self._shape = None
        self.sizes = sizes
        if same:  # nothing moved: the reader keeps the exact scroll position
            self.viewport().update()
            return
        page = min(old, len(sizes) - 1)
        if mapping is not None and old in mapping:
            page = mapping.index(old)
        self._page = page
        self._relayout()
        self.go_to(page)

    # -- crop --------------------------------------------------------------
    def start_crop(self):
        self.cropping, self._crop = True, None
        self.clear_selection()
        self.setFocus()
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def cancel_crop(self):
        if self.cropping:
            self.cropping, self._crop = False, None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self.viewport().update()

    def _crop_rect(self):
        pno, a, b = self._crop
        w, h = self.sizes[pno]
        r = QRectF(a, b).normalized()
        return (max(r.left(), 0.0), max(r.top(), 0.0), min(r.right(), w), min(r.bottom(), h))

    @property
    def page(self) -> int:
        return self._page

    @property
    def dpr(self) -> float:
        return self.devicePixelRatioF()

    @property
    def scale(self) -> float:
        """Device pixels per PDF point."""
        return round(self.zoom * 96 / 72 * self.dpr, 5)

    def _turned(self, pno):
        w, h = self.sizes[pno]
        return (h, w) if self.rotation in (90, 270) else (w, h)

    # -- layout ------------------------------------------------------------
    def _make_rows(self):
        n = len(self.sizes)
        step = 2 if self.two_up else 1
        return [list(range(i, min(i + step, n))) for i in range(0, n, step)]

    def _fit_zoom(self):
        if self.fit == "none" or not self.sizes:
            return self.zoom
        # Fit to the row being read, so one odd page cannot shrink the rest.
        row = next(r for r in self._rows if self._page in r)
        w = sum(self._turned(p)[0] for p in row)
        h = max(self._turned(p)[1] for p in row)
        vp = self.viewport()
        zx = (vp.width() - GAP * (len(row) + 1)) / (w * 96 / 72)
        if self.fit == "width":
            return min(max(zx, ZOOM_MIN), ZOOM_MAX)
        zy = (vp.height() - GAP * 2) / (h * 96 / 72)
        return min(max(min(zx, zy), ZOOM_MIN), ZOOM_MAX)

    def _relayout(self):
        if not self.sizes:
            return
        self._rows = self._make_rows()
        zoom = self._fit_zoom()
        changed = abs(zoom - self.zoom) > 1e-6
        self.zoom = zoom
        s, gap = self.scale, round(GAP * self.dpr)
        dims = [(math.ceil(w * s - 0.001), math.ceil(h * s - 0.001))
                for w, h in map(self._turned, range(len(self.sizes)))]
        row_w = [sum(dims[p][0] for p in r) + gap * (len(r) - 1) for r in self._rows]
        total_w = max(row_w) + 2 * gap
        vw = round(self.viewport().width() * self.dpr)
        width = max(total_w, vw)
        self.rects = [QRect()] * len(self.sizes)
        self._row_tops = []
        y = gap
        for row, rw in zip(self._rows, row_w):
            self._row_tops.append(y)
            h = max(dims[p][1] for p in row)
            x = (width - rw) // 2
            for p in row:
                self.rects[p] = QRect(x, y + (h - dims[p][1]) // 2, *dims[p])
                x += dims[p][0] + gap
            y += h + gap
        self._content = (width, y)
        self._update_bars()
        if self.fit != "none":
            self._center_row()
        if changed:
            self.zoomChanged.emit(self.zoom)

    def _center_row(self):
        # Wider pages elsewhere make the content wider than the window.
        row = next(r for r in self._rows if self._page in r)
        mid = (self.rects[row[0]].left() + self.rects[row[-1]].right()) / 2
        self.horizontalScrollBar().setValue(
            round(mid - self.viewport().width() * self.dpr / 2))

    def _update_bars(self):
        vw = round(self.viewport().width() * self.dpr)
        vh = round(self.viewport().height() * self.dpr)
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setPageStep(vw)
        v.setPageStep(vh)
        h.setSingleStep(round(40 * self.dpr))
        v.setSingleStep(round(40 * self.dpr))
        h.setRange(0, max(0, self._content[0] - vw))
        v.setRange(0, max(0, self._content[1] - vh))
        self._scrolled()

    def _anchor(self, pt: QPointF):
        """Remember which spot of which page is under a viewport point (device px)."""
        p = self._nearest_page(pt)
        r = self.rects[p]
        doc = pt + QPointF(self._origin())
        return p, (doc.x() - r.x()) / max(r.width(), 1), (doc.y() - r.y()) / max(r.height(), 1), pt

    def _restore(self, anchor):
        p, fx, fy, pt = anchor
        r = self.rects[p]
        self.horizontalScrollBar().setValue(round(r.x() + fx * r.width() - pt.x()))
        self.verticalScrollBar().setValue(round(r.y() + fy * r.height() - pt.y()))

    def _change(self, fn, pt=None):
        if not self.sizes:
            fn()
            return
        page = self._page
        anchor = self._anchor(pt if pt is not None else QPointF(
            self.viewport().width() * self.dpr / 2, 1))
        fn()
        self._relayout()
        if pt is None and anchor[0] != page:
            self.go_to(page)
        else:
            self._restore(anchor)
        self.viewport().update()

    def set_zoom(self, zoom, at: QPointF | None = None):
        def fn():
            self.fit = "none"
            self.zoom = min(max(zoom, ZOOM_MIN), ZOOM_MAX)
        self._change(fn, at * self.dpr if at is not None else None)
        self.zoomChanged.emit(self.zoom)

    def set_fit(self, fit):
        page = self._page
        self.fit = fit
        if self.sizes:
            self._relayout()
            self.go_to(page)

    def rotate(self):
        page = self._page
        self.rotation = (self.rotation + 90) % 360
        if self.sizes:
            self._relayout()
            self.go_to(page)

    def set_two_up(self, on):
        page = self._page
        self.two_up = on
        if self.sizes:
            self._relayout()
            self.go_to(page)

    # -- navigation --------------------------------------------------------
    def go_to(self, pno, y_pt=None):
        if not self.sizes:
            return
        pno = min(max(pno, 0), len(self.sizes) - 1)
        self._page = pno
        if self.fit != "none":
            if abs(self._fit_zoom() - self.zoom) > 1e-6:
                self._relayout()  # a page of another size: fit it too
            self._page = pno
            self._center_row()
        r = self.rects[pno]
        y = r.y() - round(GAP * self.dpr / 2)
        if y_pt:
            pos = self.page_to_doc(pno, QPointF(0, y_pt))
            y = round(pos.y() - 20 * self.dpr)
        self.verticalScrollBar().setValue(y)
        self._page = pno  # short last pages cannot scroll to the top
        self.pageChanged.emit(self._page)
        self.viewport().update()

    def step(self, delta):
        self.go_to(self._page + delta * (2 if self.two_up else 1))

    def show_rect(self, pno, rect):
        """Scroll a page rectangle (points) into view."""
        d = self._doc_rect(pno, rect)
        o = self._origin()
        vw, vh = self.viewport().width() * self.dpr, self.viewport().height() * self.dpr
        if not (o.y() + 20 < d.top() and d.bottom() < o.y() + vh - 20):
            self.verticalScrollBar().setValue(round(d.center().y() - vh / 3))
        if not (o.x() <= d.left() and d.right() <= o.x() + vw):
            self.horizontalScrollBar().setValue(round(d.center().x() - vw / 2))
        self.viewport().update()

    def _scrolled(self):
        if not self.sizes or not self._row_tops:
            return
        o = self._origin().y()
        vh = self.viewport().height() * self.dpr
        i = max(bisect.bisect_right(self._row_tops, o + vh / 3) - 1, 0)
        row = self._rows[i]
        if self._page not in row:
            self._page = row[0]
            self.pageChanged.emit(self._page)
        self.viewport().update()

    # -- coordinates -------------------------------------------------------
    def _origin(self) -> QPoint:
        return QPoint(self.horizontalScrollBar().value(), self.verticalScrollBar().value())

    def page_to_doc(self, pno, pt: QPointF) -> QPointF:
        w, h = self.sizes[pno]
        x, y = {0: (pt.x(), pt.y()), 90: (h - pt.y(), pt.x()),
                180: (w - pt.x(), h - pt.y()), 270: (pt.y(), w - pt.x())}[self.rotation]
        r = self.rects[pno]
        return QPointF(r.x() + x * self.scale, r.y() + y * self.scale)

    def doc_to_page(self, pno, pt: QPointF) -> QPointF:
        w, h = self.sizes[pno]
        r = self.rects[pno]
        x, y = (pt.x() - r.x()) / self.scale, (pt.y() - r.y()) / self.scale
        return QPointF(*{0: (x, y), 90: (y, h - x),
                         180: (w - x, h - y), 270: (w - y, x)}[self.rotation])

    def _doc_rect(self, pno, rect) -> QRectF:
        a = self.page_to_doc(pno, QPointF(rect[0], rect[1]))
        b = self.page_to_doc(pno, QPointF(rect[2], rect[3]))
        return QRectF(a, b).normalized()

    def _visible_pages(self, margin=0.0):
        if not self._row_tops:
            return []
        top = self._origin().y()
        vh = self.viewport().height() * self.dpr
        lo = max(bisect.bisect_right(self._row_tops, top - vh * margin) - 1, 0)
        hi = bisect.bisect_left(self._row_tops, top + vh * (1 + margin))
        return [p for r in self._rows[lo:hi] for p in r]

    def _nearest_page(self, pt: QPointF) -> int:
        doc = pt + QPointF(self._origin())
        pages = self._visible_pages() or [self._page]

        def dist(p):
            r = self.rects[p]
            dx = max(r.left() - doc.x(), 0, doc.x() - r.right())
            dy = max(r.top() - doc.y(), 0, doc.y() - r.bottom())
            return dx * dx + dy * dy
        return min(pages, key=dist)

    # -- painting ----------------------------------------------------------
    def _request(self):
        """Ask for what is visible (plus half a screen) and drop stale requests."""
        s, rot = self.scale, self.rotation
        o = self._origin()
        vw, vh = self.viewport().width() * self.dpr, self.viewport().height() * self.dpr
        near = QRectF(o.x(), o.y() - vh / 2, vw, vh * 2)
        seen = QRectF(o.x(), o.y(), vw, vh)
        wanted = {}
        for p in self._visible_pages(0.5):
            r = self.rects[p]
            for ty in range(0, r.height(), TILE):
                for tx in range(0, r.width(), TILE):
                    x1, y1 = min(tx + TILE, r.width()), min(ty + TILE, r.height())
                    box = QRectF(r.x() + tx, r.y() + ty, x1 - tx, y1 - ty)
                    if box.intersects(near):
                        key = ("tile", p, s, rot, tx, ty, self.rev)
                        wanted[key] = ((tx, ty, x1, y1), box.intersects(seen))
        # Another pane of the document asks the same engine: what it wants stays asked for.
        self._wanted = wanted
        others = [v for v in self.shared.views if v is not self]
        self.client.cancel(lambda k: k[0] == "tile" and k not in wanted
                           and not any(k in v._wanted for v in others))
        a, b = self._page - FAR_PAGES, self._page + FAR_PAGES
        rng = self._range()
        if rng:  # the selection's text is needed wherever it is
            a, b = min(a, rng[0][0]), max(b, rng[1][0])
        self._near_text = (a, b)
        spans = [v._near_text for v in self.shared.views]
        self.client.cancel(lambda k: k[0] in ("chars", "links", "widgets")
                           and not any(lo <= k[1] <= hi for lo, hi in spans))
        for key, (tile, visible) in wanted.items():
            if key in self._tiles:
                self._tiles.move_to_end(key)
            elif key not in self._bad and not self.frozen:
                prio = P_VISIBLE if visible else P_VISIBLE + 5
                self.client.submit(key, prio, "render", key[1], s, rot, tile)
        for p in self._visible_pages():
            self._need_content(p)
        return wanted

    def _need_content(self, p):
        for kind, store in (("chars", self.chars), ("links", self.links),
                            ("widgets", self.widgets)):
            key = (kind, p, self.rev)
            if p not in store and key not in self._bad and not self.frozen:
                self.client.submit(key, P_TEXT, kind, p)

    def on_done(self, key, value):
        if key[-1] != self.rev:
            return  # from before an edit
        if key[0] == "tile":
            if key[2] != self.scale or key[3] != self.rotation:
                return
            img = to_image(value)
            before = self._tiles.pop(key, None)
            if before is not None:
                self._tile_bytes -= before.sizeInBytes()
            self._stale.pop(key[:-1], None)
            self._tiles[key] = img
            self._tile_bytes += img.sizeInBytes()
            self._trim(CACHE_BYTES)
            self._share()
            self.viewport().update()
        elif key[0] == "chars":
            self.chars[key[1]] = [(c, i) for i, ln in enumerate(value) for c in ln]
            self._forget_far(self.chars)
            self.viewport().update()
        elif key[0] == "links":
            self.links[key[1]] = value
            self._forget_far(self.links)
        elif key[0] == "widgets":
            self.widgets[key[1]] = value
            self._forget_far(self.widgets)
            self.viewport().update()

    def _trim(self, limit):
        while self._tile_bytes > limit and len(self._tiles) > 1:
            _, old = self._tiles.popitem(last=False)
            self._tile_bytes -= old.sizeInBytes()

    def _share(self):
        """All tabs together stay within one budget: those in the background give first."""
        views = []
        for v in list(_VIEWS):
            try:
                views.append((v is self, v.isVisible(), v))
            except RuntimeError:  # the tab was closed: its pictures went with it
                _VIEWS.discard(v)
        over = sum(v._tile_bytes for *_, v in views) - TOTAL_BYTES
        for *_, v in sorted(views, key=lambda t: t[:2]):
            if over <= 0:
                return
            before = v._tile_bytes
            v._trim(max(before - over, 0))
            over -= before - v._tile_bytes

    def _forget_far(self, store):
        """Keep the text of the pages around the reader; the rest is read again when needed."""
        if len(store) <= KEEP_PAGES:
            return
        keep = set(self._visible_pages(1))
        rng = self._range()
        if rng:
            keep.update(range(rng[0][0], rng[1][0] + 1))
        for p in sorted(store, key=lambda p: -abs(p - self._page)):
            if len(store) <= KEEP_PAGES * 3 // 4:
                break
            if p not in keep:
                del store[p]

    def hideEvent(self, e):
        self._trim(HIDDEN_BYTES)  # other tabs need the memory more
        super().hideEvent(e)

    def event(self, e):
        if e.type() == QEvent.Type.DevicePixelRatioChange and self.sizes:
            self._change(lambda: None)  # moved to a screen with another scaling
        return super().event(e)

    def on_failed(self, key):
        self._bad.add(key)
        self.viewport().update()

    def paintEvent(self, _):
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), self.palette().color(QPalette.ColorRole.Mid))
        if not self.sizes:
            return
        wanted = self._request()
        p.scale(1 / self.dpr, 1 / self.dpr)
        o = self._origin()
        p.translate(-o.x(), -o.y())
        for pno in self._visible_pages():
            r = self.rects[pno]
            p.fillRect(r, theme.color("paper"))
            mine = [k for k in wanted if k[1] == pno]
            if any(k not in self._tiles for k in mine):
                thumb = self.thumbs.get(pno)
                if thumb is not None and self.rotation == 0:
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    p.drawImage(r, thumb)
            for k in mine:
                img = self._tiles.get(k)
                if img is None:
                    img = self._stale.get(k[:-1])
                if img is not None:
                    p.drawImage(QPoint(r.x() + k[4], r.y() + k[5]), img)
            if any(k in self._bad for k in mine):
                p.setPen(theme.color("mark-error"))
                f = p.font()
                f.setPixelSize(round(15 * self.dpr))
                p.setFont(f)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "ამ გვერდის ჩვენება ვერ მოხერხდა")
            p.save()
            p.setClipRect(r)
            # Multiply keeps the text readable under the highlight.
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            for i, hit in enumerate(self.hits.get(pno, ())):
                now = self.current_hit == (pno, i)
                p.fillRect(self._doc_rect(pno, hit[0]), theme.color("mark-current" if now else "mark-highlight"))
            for rect in self._selected_rects(pno):
                p.fillRect(self._doc_rect(pno, rect), theme.color("mark-selection"))
            for rect in self.diff.get(pno, ()):
                p.fillRect(self._doc_rect(pno, rect), theme.color("mark-edit"))
            for w in self.widgets.get(pno, ()):
                if not w["readonly"] and w["kind"] != "other":
                    p.fillRect(self._doc_rect(pno, w["rect"]), theme.color("mark-field"))
            p.restore()
            for rect, token, now in self.frames.get(pno, ()):
                p.setPen(QPen(theme.color(token), (3 if now else 1.5) * self.dpr))
                p.drawRect(self._doc_rect(pno, rect).adjusted(-2, -2, 2, 2))
            for rect in self.marks.get(pno, ()):
                box = self._doc_rect(pno, rect)
                p.fillRect(box, QColor(0, 0, 0, 70))
                p.setPen(QPen(theme.color("mark-redact"), 2))
                p.drawRect(box)
            if self._shape and self._shape[0] == pno:
                self._paint_shape(p)
            if self._crop and self._crop[0] == pno:
                keep = self._doc_rect(pno, self._crop_rect())
                shade = QColor(0, 0, 0, 110)
                full = QRectF(r)
                p.fillRect(QRectF(full.left(), full.top(), full.width(), keep.top() - full.top()),
                           shade)
                p.fillRect(QRectF(full.left(), keep.bottom(), full.width(),
                                  full.bottom() - keep.bottom()), shade)
                p.fillRect(QRectF(full.left(), keep.top(), keep.left() - full.left(),
                                  keep.height()), shade)
                p.fillRect(QRectF(keep.right(), keep.top(), full.right() - keep.right(),
                                  keep.height()), shade)
                p.setPen(QPen(theme.color("mark-crop"), 2))
                p.drawRect(keep)
            p.setPen(QPen(QColor(0, 0, 0, 60), 1))
            p.drawRect(r.adjusted(-1, -1, 0, 0))

    # -- tools -------------------------------------------------------------
    BOX_TOOLS = {"text", "rect", "ellipse", "stamp", "image", "redact", "field", "signbox",
                 "cloud"}
    LINE_TOOLS = {"line", "arrow"}
    CLICK_TOOLS = {"note", "edit", "point"}  # point: ჩართალა, aligning by hand
    TEXT_TOOLS = {"highlight", "underline", "strikeout"}

    def set_tool(self, name):
        self.cancel_crop()
        self.tool, self._shape = name, None
        cursor = {"select": Qt.CursorShape.ArrowCursor, "edit": Qt.CursorShape.IBeamCursor,
                  "highlight": Qt.CursorShape.IBeamCursor,
                  "underline": Qt.CursorShape.IBeamCursor,
                  "strikeout": Qt.CursorShape.IBeamCursor}.get(name, Qt.CursorShape.CrossCursor)
        self.viewport().setCursor(cursor)
        self.viewport().update()

    def _clamp(self, pno, q):
        w, h = self.sizes[pno]
        return QPointF(min(max(q.x(), 0.0), w), min(max(q.y(), 0.0), h))

    def _paint_shape(self, p):
        pno, pts = self._shape
        a, b = self.page_to_doc(pno, pts[0]), self.page_to_doc(pno, pts[-1])
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self.tool_color, max(self.tool_width * self.scale, 1.5)))
        if self.tool == "ink":
            for x, y in zip(pts, pts[1:]):
                p.drawLine(self.page_to_doc(pno, x), self.page_to_doc(pno, y))
        elif self.tool in self.LINE_TOOLS:
            p.drawLine(a, b)
        elif self.tool == "ellipse":
            p.drawEllipse(QRectF(a, b).normalized())
        elif self.tool == "cloud":
            p.drawPath(self._cloud(QRectF(a, b).normalized(), 14 * self.dpr))
        else:
            p.setPen(QPen(self.tool_color, 1.5, Qt.PenStyle.DashLine))
            p.drawRect(QRectF(a, b).normalized())
        p.restore()

    @staticmethod
    def _cloud(r, step):
        """A revision cloud round a rectangle, while it is being drawn: arcs bulging out."""
        path = QPainterPath(r.topLeft())
        corners = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft(), r.topLeft()]
        for s, e in zip(corners, corners[1:]):  # clockwise: outside is to the left
            dx, dy = e.x() - s.x(), e.y() - s.y()
            n = max(1, round(math.hypot(dx, dy) / step))
            for i in range(n):
                x0, y0 = s.x() + dx * i / n, s.y() + dy * i / n
                x1, y1 = s.x() + dx * (i + 1) / n, s.y() + dy * (i + 1) / n
                length = math.hypot(x1 - x0, y1 - y0) or 1
                out = length * .6
                path.quadTo((x0 + x1) / 2 + (y1 - y0) / length * out,
                            (y0 + y1) / 2 - (x1 - x0) / length * out, x1, y1)
        return path

    def selection_quads(self):
        """{page: [quad, ...]} of the selected text; a quad is ul, ur, ll, lr as read."""
        rng = self._range()
        out: dict[int, list] = {}
        if rng is None:
            return out
        for pno in range(rng[0][0], rng[1][0] + 1):
            lines: dict[int, list] = {}
            for c, ln in self._page_span(pno):
                lines.setdefault(ln, []).append(c)
            for ln, chars in lines.items():
                x0, y0 = min(c[0] for c in chars), min(c[1] for c in chars)
                x1, y1 = max(c[2] for c in chars), max(c[3] for c in chars)
                whole = [c for c, n in self.chars[pno] if n == ln]
                f, la = whole[0], whole[-1]
                dx, dy = la[0] + la[2] - f[0] - f[2], la[1] + la[3] - f[1] - f[3]
                if abs(dx) >= abs(dy):
                    q = (((x0, y0), (x1, y0), (x0, y1), (x1, y1)) if dx >= 0 else
                         ((x1, y1), (x0, y1), (x1, y0), (x0, y0)))
                else:
                    q = (((x1, y0), (x1, y1), (x0, y0), (x0, y1)) if dy > 0 else
                         ((x0, y1), (x0, y0), (x1, y1), (x1, y0)))
                out.setdefault(pno, []).append(q)
        return out

    def widget_at(self, pno, q):
        for w in reversed(self.widgets.get(pno, [])):
            x0, y0, x1, y1 = w["rect"]
            if x0 <= q.x() <= x1 and y0 <= q.y() <= y1:
                return w
        return None

    def contextMenuEvent(self, e):
        if self.sizes:
            pt = QPointF(e.pos()) * self.dpr
            pno = self._nearest_page(pt)
            q = self.doc_to_page(pno, pt + QPointF(self._origin()))
            self.contextAt.emit(pno, q, e.globalPos())

    # -- selection ---------------------------------------------------------
    def _caret(self, pt: QPointF):
        """(page, caret index) nearest to a viewport point in device px."""
        pno = self._nearest_page(pt)
        chars = self.chars.get(pno)
        if not chars:
            return (pno, 0)
        q = self.doc_to_page(pno, pt + QPointF(self._origin()))
        x, y = q.x(), q.y()

        def dist(i):
            c = chars[i][0]
            dx = max(c[0] - x, 0, x - c[2])
            dy = max(c[1] - y, 0, y - c[3])
            return dx * dx + 4 * dy * dy
        i = min(range(len(chars)), key=dist)
        c, line = chars[i]
        # Direction of the line, so vertical text on rotated pages works too.
        a, b = i, i
        while a > 0 and chars[a - 1][1] == line:
            a -= 1
        while b + 1 < len(chars) and chars[b + 1][1] == line:
            b += 1
        ca, cb = chars[a][0], chars[b][0]
        dx = (cb[0] + cb[2] - ca[0] - ca[2]) or (0 if a != b else 1)
        dy = cb[1] + cb[3] - ca[1] - ca[3]
        side = (x - (c[0] + c[2]) / 2) * dx + (y - (c[1] + c[3]) / 2) * dy
        return (pno, i + 1 if side > 0 else i)

    def _range(self):
        if self._sel_a is None or self._sel_b is None or self._sel_a == self._sel_b:
            return None
        return min(self._sel_a, self._sel_b), max(self._sel_a, self._sel_b)

    def _page_span(self, pno):
        rng = self._range()
        if rng is None or not rng[0][0] <= pno <= rng[1][0]:
            return []
        chars = self.chars.get(pno, [])
        lo = rng[0][1] if pno == rng[0][0] else 0
        hi = rng[1][1] if pno == rng[1][0] else len(chars)
        return chars[lo:hi]

    def _selected_rects(self, pno):
        lines: dict[int, list] = {}
        for c, ln in self._page_span(pno):
            r = lines.get(ln)
            lines[ln] = list(c[:4]) if r is None else [
                min(r[0], c[0]), min(r[1], c[1]), max(r[2], c[2]), max(r[3], c[3])]
        return lines.values()

    def has_selection(self):
        return self._range() is not None

    def clear_selection(self):
        self._sel_a = self._sel_b = None
        self.viewport().update()

    def selected_text(self):
        """Selected text, or None while some page's text is still loading."""
        rng = self._range()
        if rng is None:
            return ""
        pages = range(rng[0][0], rng[1][0] + 1)
        missing = [p for p in pages if p not in self.chars 
                   and ("chars", p, self.rev) not in self._bad]
        for p in missing:
            self._need_content(p)
        if missing:
            return None
        out = []
        for p in pages:
            prev = None
            for c, ln in self._page_span(p):
                if prev is not None and ln != prev:
                    out.append("\n")
                out.append(c[4])
                prev = ln
            out.append("\n")
        return "".join(out).strip("\n")

    # -- events ------------------------------------------------------------
    def _link_at(self, pt: QPointF):
        pno = self._nearest_page(pt)
        q = self.doc_to_page(pno, pt + QPointF(self._origin()))
        for ln in self.links.get(pno, ()):
            x0, y0, x1, y1 = ln["rect"]
            if x0 <= q.x() <= x1 and y0 <= q.y() <= y1:
                return ln
        return None

    def _char_at(self, pt):
        pno = self._nearest_page(pt)
        q = self.doc_to_page(pno, pt + QPointF(self._origin()))
        return any(c[0] <= q.x() <= c[2] and c[1] <= q.y() <= c[3]
                   for c, _ in self.chars.get(pno, ()))

    def focusInEvent(self, e):
        self.activated.emit()
        super().focusInEvent(e)

    def mousePressEvent(self, e):
        self.activated.emit()
        if e.button() != Qt.MouseButton.LeftButton or not self.sizes:
            return
        pt = e.position() * self.dpr
        if self.cropping:
            pno = self._nearest_page(pt)
            q = self.doc_to_page(pno, pt + QPointF(self._origin()))
            self._crop = [pno, q, q]
            return
        if self.tool in self.BOX_TOOLS | self.LINE_TOOLS | {"ink"}:
            pno = self._nearest_page(pt)
            q = self._clamp(pno, self.doc_to_page(pno, pt + QPointF(self._origin())))
            self._shape = [pno, [q]]
            return
        if self.tool in self.CLICK_TOOLS:
            pno = self._nearest_page(pt)
            q = self._clamp(pno, self.doc_to_page(pno, pt + QPointF(self._origin())))
            self.toolDone.emit(self.tool, pno, {"point": (q.x(), q.y())})
            return
        pno = self._nearest_page(pt)
        q = self.doc_to_page(pno, pt + QPointF(self._origin()))
        self._press_widget = self.widget_at(pno, q) if self.tool == "select" else None
        if self._press_widget:
            return
        self._press_link = self._link_at(pt)
        if self._press_link is None:
            self._sel_a = self._sel_b = self._caret(pt)
            self._dragging = True
            self.viewport().update()

    def mouseMoveEvent(self, e):
        if not self.sizes:
            return
        pt = e.position() * self.dpr
        if self.cropping:
            if self._crop and e.buttons() & Qt.MouseButton.LeftButton:
                self._crop[2] = self.doc_to_page(self._crop[0], pt + QPointF(self._origin()))
                self.viewport().update()
            return
        if self._shape:
            pno, pts = self._shape
            q = self._clamp(pno, self.doc_to_page(pno, pt + QPointF(self._origin())))
            if self.tool == "ink":
                pts.append(q)
            else:
                self._shape[1] = [pts[0], q]
            self.viewport().update()
            return
        if self._dragging:
            h = self.viewport().height()
            y = e.position().y()
            if y < 0 or y > h:  # auto-scroll past the edges
                bar = self.verticalScrollBar()
                bar.setValue(bar.value() + round((y if y < 0 else y - h) * self.dpr))
            self._sel_b = self._caret(pt)
            self.viewport().update()
            return
        if self.tool != "select":
            return
        pno = self._nearest_page(pt)
        field = self.widget_at(pno, self.doc_to_page(pno, pt + QPointF(self._origin())))
        if field and not field["readonly"]:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor if field["kind"] == "text"
                                      else Qt.CursorShape.PointingHandCursor)
            self.hovered.emit(field["label"] or field["name"])
            return
        ln = self._link_at(pt)
        if ln:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            self.hovered.emit(ln.get("uri") or f"გვერდი {ln['page'] + 1}")
        else:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor if self._char_at(pt)
                                      else Qt.CursorShape.ArrowCursor)
            self.hovered.emit("")

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.cropping:
            if self._crop:
                pno, rect = self._crop[0], self._crop_rect()
                if rect[2] - rect[0] >= 20 and rect[3] - rect[1] >= 20:
                    self.cropDrawn.emit(pno, rect)
                    self.cancel_crop()
                else:
                    self._crop = None
                    self.viewport().update()
            return
        if self._shape:
            self._finish_shape()
            return
        if getattr(self, "_press_widget", None):
            w, self._press_widget = self._press_widget, None
            pt = e.position() * self.dpr
            pno = self._nearest_page(pt)
            q = self.doc_to_page(pno, pt + QPointF(self._origin()))
            if self.widget_at(pno, q) is w:
                self.clicked.emit(pno, w)
            return
        if self._dragging and self.tool in self.TEXT_TOOLS and self.has_selection():
            self._dragging = False
            self.toolDone.emit(self.tool, self._range()[0][0], {"quads": self.selection_quads()})
            return
        self._dragging = False
        ln, self._press_link = self._press_link, None
        if ln and ln is self._link_at(e.position() * self.dpr):
            if "uri" in ln:
                self.uriActivated.emit(ln["uri"])
            else:
                self.go_to(ln["page"], ln["y"])

    def _finish_shape(self):
        (pno, pts), self._shape = self._shape, None
        a, b = pts[0], pts[-1]
        self.viewport().update()
        if self.tool == "ink":
            if len(pts) > 1:
                self.toolDone.emit("ink", pno, {"strokes": [[(q.x(), q.y()) for q in pts]]})
        elif self.tool in self.LINE_TOOLS:
            if abs(a.x() - b.x()) + abs(a.y() - b.y()) > 4:
                self.toolDone.emit(self.tool, pno, {"from": (a.x(), a.y()), "to": (b.x(), b.y())})
        else:
            r = QRectF(a, b).normalized()
            if r.width() < 5 or r.height() < 5:
                if self.tool not in ("text", "stamp", "field", "image", "signbox"):
                    return
                w, h = self.sizes[pno]  # a plain click gets a box of a sensible size
                r = QRectF(a.x(), a.y(), min(180, w - a.x()), min(40, h - a.y()))
                if r.width() < 20 or r.height() < 10:
                    return
            self.toolDone.emit(self.tool, pno, {"rect": (r.left(), r.top(), r.right(), r.bottom())})

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.sizes or self.cropping:
            return
        if self.tool != "select":
            self.mousePressEvent(e)
            return
        pt = e.position() * self.dpr
        if self._link_at(pt) or not self._char_at(pt):
            return
        pno, i = self._caret(pt)
        chars = self.chars[pno]
        i = min(i, len(chars) - 1)
        if not chars[i][0][4].isalnum() and i > 0:
            i -= 1

        def word(k):
            return (0 <= k < len(chars) and chars[k][1] == chars[i][1]
                    and chars[k][0][4].isalnum())
        a = b = i
        while word(a - 1):
            a -= 1
        while word(b + 1):
            b += 1
        self._sel_a, self._sel_b = (pno, a), (pno, b + 1)
        self.viewport().update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self.set_zoom(self.zoom * 1.0015 ** d, e.position())
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        v = self.verticalScrollBar()
        k = e.key()
        page = round(self.viewport().height() * self.dpr * 0.9)
        back = e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        if k == Qt.Key.Key_PageDown or (k == Qt.Key.Key_Space and not back):
            v.setValue(v.value() + page)
        elif k == Qt.Key.Key_PageUp or k == Qt.Key.Key_Space:
            v.setValue(v.value() - page)
        elif k == Qt.Key.Key_Home:
            self.go_to(0)
        elif k == Qt.Key.Key_End:
            self.go_to(len(self.sizes) - 1)
        elif k == Qt.Key.Key_Right and not self.horizontalScrollBar().maximum():
            self.step(1)
        elif k == Qt.Key.Key_Left and not self.horizontalScrollBar().maximum():
            self.step(-1)
        elif k == Qt.Key.Key_Escape and self.cropping:
            self.cancel_crop()
        elif k == Qt.Key.Key_Escape and self._shape:
            self._shape = None
            self.viewport().update()
        elif k == Qt.Key.Key_Escape and self.tool != "select":
            self.toolDone.emit("cancel", self._page, None)
        elif k == Qt.Key.Key_Escape and self.has_selection():
            self.clear_selection()
        else:
            super().keyPressEvent(e)

    def resizeEvent(self, _):
        if not self.sizes:
            return
        page, off = self._page, 0.0
        if self.rects and self.rects[page].height():
            off = (self._origin().y() - self.rects[page].y()) / self.rects[page].height()
        self._relayout()
        r = self.rects[page]
        self.verticalScrollBar().setValue(round(r.y() + off * r.height()))

    def scrollContentsBy(self, dx, dy):
        self.viewport().update()
