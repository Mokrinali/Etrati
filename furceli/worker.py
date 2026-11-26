"""The engine child process: receives (op, args), answers ("ok", value) / ("err", code)."""
from __future__ import annotations

import os
import time

from . import convert, files, scan, signing
from .engine import EngineError
from .features import Features

QUERIES = {"render", "chars", "search", "links", "annots", "all_annots", "widgets", "lines",
           "ocr_words", "compare", "mentions", "page_texts", "pdf_box"}
CHANGES = {"open", "replay", "apply", "save", "export", "pdfa"}
SCAN = {"scan_count", "scan_analyse", "scan_preview", "scan_build", "scan_join"}
NO_DOC = SCAN | {"open", "replay", "inspect", "merge", "make_pdf", "sign", "verify", "make_certificate",
                 "prepare"}
ALLOWED = QUERIES | CHANGES | NO_DOC | {"export_as", "to_images"}


# Which arguments of an operation are the files it writes.
WRITES = {"save": lambda a: [a[0]], "export": lambda a: [p for _, p in a[0]],
          "merge": lambda a: [a[2]], "pdfa": lambda a: [a[0]], "make_pdf": lambda a: [a[1]],
          "export_as": lambda a: [a[1]], "to_images": lambda a: [p for _, p in a[0]],
          "sign": lambda a: [a[1]], "make_certificate": lambda a: [a[0]],
          "scan_build": lambda a: [a[3]], "scan_join": lambda a: [a[1]]}


def _inside(path, root) -> bool:
    try:
        path, root = (os.path.normcase(os.path.realpath(str(p))) for p in (path, root))
        return os.path.commonpath([path, root]) == root and path != root
    except (TypeError, ValueError, OSError):
        return False


def run(engine, op, args, root=None):
    if op not in ALLOWED:
        raise EngineError("bad_op")
    # The engine writes only into its own folder; the window puts the files in place.
    if root is not None and op in WRITES:
        try:
            targets = WRITES[op](args)
        except (IndexError, TypeError, ValueError):
            raise EngineError("bad_op")
        if not all(_inside(p, root) for p in targets):
            raise EngineError("outside")
    if op not in NO_DOC and engine.doc is None:
        raise EngineError("not_open")
    if op in SCAN:
        return {"scan_count": scan.count_pages, "scan_analyse": scan.analyse,
                "scan_preview": scan.preview, "scan_build": scan.build_page,
                "scan_join": scan.join}[op](*args)
    if op == "make_pdf":
        return convert.make_pdf(*args)
    if op == "prepare":
        return convert.prepare(*args)
    if op == "export_as":
        return convert.export_as(engine, *args)
    if op == "to_images":
        return convert.to_images(engine, *args)
    if op in ("sign", "verify", "make_certificate"):
        return getattr(signing, op)(*args)
    return getattr(engine, op)(*args)


def serve(conn, root=None):
    if root is not None:
        files._root = root          # its working folders go there too
        convert.IN_ENGINE = True    # Office and Edge are the helper's
    engine = Features()
    convert.notify = conn.send
    testing = bool(os.environ.get("FURCELI_TEST"))
    while True:
        try:
            op, args = conn.recv()
        except (EOFError, OSError):
            return
        if op == "quit":
            return
        if testing and op == "_crash":
            os._exit(3)
        if testing and op == "_sleep":
            time.sleep(args[0])
            conn.send(("ok", None))
            continue
        try:
            conn.send(("ok", run(engine, op, args, root)))
        except EngineError as e:
            conn.send(("err", str(e)))
        except Exception as e:
            conn.send(("err", f"internal: {type(e).__name__}: {e}"))
