"""Printing: pages are rendered by the engine and fitted to the paper."""
from __future__ import annotations

import math

from PySide6.QtCore import QEventLoop, QRectF, Qt
from PySide6.QtGui import QPageLayout, QPainter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QProgressDialog

from .client import P_PRINT
from .view import to_image

MAX_DPI = 300
MAX_PIXELS = 40_000_000


class PrintError(Exception):
    pass


def _render(doc, pno, scale, cancelled):
    """Render one page while keeping the UI alive. None when cancelled."""
    key = ("print", pno, scale)
    box = {}
    loop = QEventLoop()

    def done(k, v):
        if k == key:
            box["img"] = to_image(v)
            loop.quit()

    def failed(k, code):
        if k == key:
            box["err"] = code
            loop.quit()
    doc.client.done.connect(done)
    doc.client.failed.connect(failed)
    cancelled.canceled.connect(loop.quit)
    try:
        doc.client.submit(key, P_PRINT, "render", pno, scale, 0)
        loop.exec()
    finally:
        doc.client.done.disconnect(done)
        doc.client.failed.disconnect(failed)
        cancelled.canceled.disconnect(loop.quit)
    if "err" in box:
        raise PrintError(f"გვერდი {pno + 1} ვერ მომზადდა დასაბეჭდად.")
    return box.get("img")


def _orient(printer, size):
    land = size[0] > size[1]
    printer.setPageOrientation(QPageLayout.Orientation.Landscape if land
                               else QPageLayout.Orientation.Portrait)


def print_pages(doc, printer: QPrinter, pages: list[int], parent=None) -> bool:
    """Print the given page indexes. False when the user cancelled."""
    prog = QProgressDialog("იბეჭდება…", "გაუქმება", 0, len(pages), parent)
    prog.setWindowModality(Qt.WindowModality.WindowModal)
    prog.setMinimumDuration(400)
    _orient(printer, doc.sizes[pages[0]])
    painter = QPainter()
    if not painter.begin(printer):
        raise PrintError("პრინტერთან დაკავშირება ვერ მოხერხდა.")
    try:
        for n, pno in enumerate(pages):
            prog.setValue(n)
            if prog.wasCanceled():
                printer.abort()
                return False
            w, h = doc.sizes[pno]
            if n:
                _orient(printer, (w, h))
                if not printer.newPage():
                    raise PrintError("პრინტერმა ახალი გვერდი ვერ დაიწყო.")
            scale = min(printer.resolution(), MAX_DPI) / 72
            scale = min(scale, math.sqrt(MAX_PIXELS / (w * h)))
            img = _render(doc, pno, scale, prog)
            if img is None:
                printer.abort()
                return False
            area = painter.viewport()
            k = min(area.width() / w, area.height() / h)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF((area.width() - w * k) / 2, (area.height() - h * k) / 2,
                                     w * k, h * k), img)
        prog.setValue(len(pages))
        return True
    except Exception:
        printer.abort()  # half a document must not come out
        raise
    finally:
        painter.end()
        prog.close()  # also after an error or a cancel
        prog.deleteLater()


def choose_and_print(doc, parent) -> bool:
    n = len(doc.sizes)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setDocName(doc.path)
    dlg = QPrintDialog(printer, parent)
    dlg.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, True)
    dlg.setOption(QPrintDialog.PrintDialogOption.PrintCurrentPage, True)
    dlg.setMinMax(1, n)
    dlg.setFromTo(1, n)
    if dlg.exec() != QPrintDialog.DialogCode.Accepted:
        return False
    rng = printer.printRange()
    if rng == QPrinter.PrintRange.CurrentPage:
        pages = [doc.view.page]
    elif rng == QPrinter.PrintRange.PageRange and printer.fromPage():
        pages = list(range(printer.fromPage() - 1, min(printer.toPage(), n)))
    else:
        pages = list(range(n))
    if not pages:
        return False
    return print_pages(doc, printer, pages, parent)
