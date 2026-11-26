"""Page thumbnails: cache, model, and a list with drag-and-drop reordering."""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import (QAbstractListModel, QItemSelection, QItemSelectionModel, QMimeData,
                            QModelIndex, QPoint, QPointF, QRectF, QSize, Qt, Signal)
from PySide6.QtGui import QColor, QDrag, QFont, QImage, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from . import theme

RENDER_W, RENDER_H = 180, 240
CACHE = 250
MIME = "application/x-furceli-pages"


class Thumbs:
    """Small page images, shared by the lists and the page placeholders."""

    def __init__(self):
        self._d: OrderedDict[int, QImage] = OrderedDict()

    def get(self, pno):
        return self._d.get(pno)

    def put(self, pno, img):
        self._d[pno] = img
        while len(self._d) > CACHE:
            self._d.popitem(last=False)

    def remap(self, mapping):
        """Keep pictures of pages that only moved."""
        old, self._d = self._d, OrderedDict()
        for new, o in enumerate(mapping or ()):
            if o in old:
                self._d[new] = old[o]


class ThumbModel(QAbstractListModel):
    def __init__(self, doc, width, height):
        super().__init__(doc)
        self.doc, self.w, self.h = doc, width, height
        self.rows = 0

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.rows

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return f | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else f

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        pno = index.row()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.doc.page_label(pno)
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(self.w + 24, self.h + 38)
        if role == Qt.ItemDataRole.DecorationRole:
            img = self.doc.thumbs.get(pno)
            if img is None:
                self.doc.request_thumb(pno)
            return self._framed(pno, img)
        return None

    def _framed(self, pno, img):
        dpr = self.doc.devicePixelRatioF()
        pix = QPixmap(round(self.w * dpr), round(self.h * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        w, h = self.doc.sizes[pno]
        box = self.page_box(pno)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(box, theme.color("paper"))
        if img is not None:
            p.drawImage(box, img)
        p.end()
        return pix

    def page_box(self, pno) -> QRectF:
        """Where the page lies in its picture."""
        w, h = self.doc.sizes[pno]
        k = min(self.w / w, self.h / h)
        tw, th = max(w * k, 4), max(h * k, 4)
        return QRectF((self.w - tw) / 2 + .5, (self.h - th) / 2 + .5, tw - 1, th - 1)

    def reset(self):
        self.beginResetModel()
        self.rows = len(self.doc.sizes)
        self.endResetModel()

    def refresh(self, pno):
        i = self.index(pno)
        self.dataChanged.emit(i, i, [Qt.ItemDataRole.DecorationRole])


class ThumbDelegate(QStyledItemDelegate):
    """A page with a soft shadow; a chosen one gets a red frame and a red number
    (design/components/Sidebar)."""

    def paint(self, p, option, index):
        view, model = self.parent(), index.model()
        pno = index.row()
        if pno >= len(model.doc.sizes):
            return
        chosen = bool(option.state & QStyle.StateFlag.State_Selected)
        r = option.rect
        icon = view.iconSize()
        top = QPointF(r.x() + (r.width() - icon.width()) / 2, r.y() + 6)
        box = model.page_box(pno).translated(top)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        dark = theme.current() == "dark"
        for grow, alpha in ((4, 10), (2, 18), (1, 30)):  # shadow-page, roughly
            p.setBrush(QColor(0, 0, 0, alpha * (3 if dark else 1)))
            p.drawRoundedRect(box.adjusted(-grow, -grow + 2, grow, grow + 2), 3, 3)
        pix = index.data(Qt.ItemDataRole.DecorationRole)
        if pix is not None:
            p.drawPixmap(top, pix)
        if chosen:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(theme.color("brand"), 2))
            p.drawRoundedRect(box.adjusted(-4, -4, 4, 4), 3, 3)
        f = QFont(option.font)
        f.setFamilies(["Noto Sans Georgian Medium", "Noto Sans Medium", "Segoe UI"])
        f.setPixelSize(11)
        p.setFont(f)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        m = p.fontMetrics()
        tw = m.horizontalAdvance(text) + 12
        badge = QRectF(r.x() + (r.width() - tw) / 2, top.y() + icon.height() + 8, tw, 16)
        if chosen:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("brand"))
            p.drawRoundedRect(badge, 4, 4)
        p.setPen(theme.color("on-brand" if chosen else "ink-muted"))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    def sizeHint(self, option, index):
        return index.data(Qt.ItemDataRole.SizeHintRole)


class ThumbList(QListView):
    moved = Signal(object, int)          # pages, insert before this index
    filesDropped = Signal(object, int)   # pdf paths, index
    deletePressed = Signal()
    menuRequested = Signal(QPoint)

    def __init__(self, doc, width, height, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.model_ = ThumbModel(doc, width, height)
        self.setModel(self.model_)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(width, height))
        self.setSpacing(4)
        self.setItemDelegate(ThumbDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self._drop_at = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda p: self.menuRequested.emit(self.viewport().mapToGlobal(p)))

    def selected(self) -> list[int]:
        return sorted(i.row() for i in self.selectionModel().selectedIndexes())

    def select(self, pages):
        sel = QItemSelection()
        for p in pages:
            i = self.model_.index(p)
            sel.select(i, i)
        self.selectionModel().select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def follow(self, pno):
        """Show the page being read without destroying a multiple selection."""
        i = self.model_.index(pno)
        if len(self.selected()) <= 1:
            self.setCurrentIndex(i)
        self.scrollTo(i)

    def visible_rows(self):
        """First and last row on screen; rows are sorted by height, so search by halves."""
        n, h = self.model_.rowCount(), self.viewport().height()
        if not n:
            return (0, 0)

        def first(test):
            lo, hi = 0, n
            while lo < hi:
                mid = (lo + hi) // 2
                if test(self.visualRect(self.model_.index(mid))):
                    hi = mid
                else:
                    lo = mid + 1
            return lo
        a = min(first(lambda r: r.bottom() >= 0), n - 1)
        b = max(first(lambda r: r.top() > h) - 1, a)
        return (a, b)

    # -- drag and drop -----------------------------------------------------
    def drop_index(self, pos: QPoint) -> int:
        n = self.model_.rowCount()
        if not n:
            return 0
        first = self.visualRect(self.model_.index(0))
        many = n > 1 and self.visualRect(self.model_.index(1)).top() == first.top()
        for r in range(n):
            box = self.visualRect(self.model_.index(r))
            if many:
                if pos.y() < box.top() - 4:
                    return r
                if pos.y() <= box.bottom() + 4 and pos.x() < box.center().x():
                    return r
            elif pos.y() < box.center().y():
                return r
        return n

    def startDrag(self, _actions):
        pages = self.selected()
        if not pages or not self.doc.can_edit:
            return
        mime = QMimeData()
        mime.setData(MIME, ",".join(map(str, pages)).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        pix = self.model_._framed(pages[0], self.doc.thumbs.get(pages[0]))
        drag.setPixmap(pix.scaledToWidth(round(pix.width() / 2)))
        drag.exec(Qt.DropAction.MoveAction)
        self._mark(None)

    def _mark(self, at):
        self._drop_at = at
        self.viewport().update()

    def _pdfs(self, e):
        return [u.toLocalFile() for u in e.mimeData().urls()
                if u.toLocalFile().lower().endswith(".pdf")]

    def _accepts(self, e):
        own = e.mimeData().hasFormat(MIME) and getattr(e.source(), "doc", None) is self.doc
        return self.doc.can_edit and (own or bool(self._pdfs(e)))

    def dragEnterEvent(self, e):
        if self._accepts(e):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if not self._accepts(e):
            e.ignore()
            return
        pos = e.position().toPoint()
        bar, h = self.verticalScrollBar(), self.viewport().height()
        if pos.y() < 30:
            bar.setValue(bar.value() - 18)
        elif pos.y() > h - 30:
            bar.setValue(bar.value() + 18)
        self._mark(self.drop_index(pos))
        e.acceptProposedAction()

    def dragLeaveEvent(self, _):
        self._mark(None)

    def dropEvent(self, e):
        if not self._accepts(e):
            return
        at = self.drop_index(e.position().toPoint())
        self._mark(None)
        e.acceptProposedAction()
        if e.mimeData().hasFormat(MIME):
            pages = [int(x) for x in bytes(e.mimeData().data(MIME)).decode().split(",")]
            self.moved.emit(pages, at)
        else:
            self.filesDropped.emit(self._pdfs(e), at)

    def paintEvent(self, e):
        super().paintEvent(e)
        n = self.model_.rowCount()
        if self._drop_at is None or not n:
            return
        last = self._drop_at >= n
        box = self.visualRect(self.model_.index(min(self._drop_at, n - 1)))
        many = n > 1 and (self.visualRect(self.model_.index(1)).top()
                          == self.visualRect(self.model_.index(0)).top())
        p = QPainter(self.viewport())
        p.setPen(QPen(self.palette().color(QPalette.ColorRole.Highlight), 3))
        if many:
            x = box.right() + 2 if last else box.left() - 2
            p.drawLine(x, box.top(), x, box.bottom())
        else:
            y = box.bottom() + 2 if last else box.top() - 2
            p.drawLine(box.left(), y, box.right(), y)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete:
            self.deletePressed.emit()
            return
        super().keyPressEvent(e)
