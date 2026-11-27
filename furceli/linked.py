"""Two page views that move together: scroll, zoom and page (ჩართალა)."""
from __future__ import annotations

from PySide6.QtCore import QObject, QPointF


class Linker(QObject):
    """Keeps view b on the spot of view a and back, while on.

    place(src, dst, page, point) -> (page, point) in dst, or None where dst has no such place;
    scale(src, dst) -> how much bigger dst must draw a point for both to show one size.
    By default both views show the same document.
    """

    def __init__(self, a, b, place=None, scale=None, parent=None):
        super().__init__(parent)
        self.a, self.b = a, b
        self.place = place or (lambda src, dst, pno, pt: (pno, pt))
        self.scale = scale or (lambda src, dst: 1.0)
        self.on = True
        self._busy = False
        self._wires: list = []         # (signal, slot): undone by close()
        for src, dst in ((a, b), (b, a)):
            follow = (lambda *_, s=src, d=dst: self.follow(s, d))
            for signal in (src.verticalScrollBar().valueChanged,
                           src.horizontalScrollBar().valueChanged, src.zoomChanged):
                signal.connect(follow)
                self._wires.append((signal, follow))

    def close(self):
        """Let go of both views. A pane that goes must not be called, nor kept alive, by the
        lambdas left connected to the one that stays."""
        self.on = False
        for signal, slot in self._wires:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):  # that view is gone already
                pass
        self._wires = []
        self.a = self.b = None

    def set_on(self, on, lead=None):
        """Turned on, the other view comes to lead (a by default) at once."""
        self.on = on
        if on:
            lead = lead or self.a
            self.follow(lead, self.b if lead is self.a else self.a)

    def paused(self):
        """with linker.paused(): both views are moved on purpose, one by one."""
        linker = self

        class _Pause:
            def __enter__(self):
                self.was, linker._busy = linker._busy, True

            def __exit__(self, *exc):
                linker._busy = self.was
        return _Pause()

    def follow(self, src, dst):
        if not self.on or self._busy or not src.sizes or not dst.sizes:
            return
        self._busy = True
        try:
            zoom = src.zoom * self.scale(src, dst)
            if abs(dst.zoom - zoom) > 1e-6:
                dst.set_zoom(zoom)
            mid = QPointF(src.viewport().width() * src.dpr / 2,
                          src.viewport().height() * src.dpr / 2)
            pno = src._nearest_page(mid)
            got = self.place(src, dst, pno, src.doc_to_page(pno, mid + QPointF(src._origin())))
            if got is None:
                return
            to = dst.page_to_doc(*got)
            dst.horizontalScrollBar().setValue(round(to.x() - dst.viewport().width() * dst.dpr / 2))
            dst.verticalScrollBar().setValue(round(to.y() - dst.viewport().height() * dst.dpr / 2))
        finally:
            self._busy = False
