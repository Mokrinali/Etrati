"""The engine child process: receives (op, args), answers ("ok", value) / ("err", code)."""
from __future__ import annotations

import os
import time

from . import convert, signing
from .engine import EngineError
from .features import Features

QUERIES = {"render", "chars", "search", "links", "annots", "all_annots", "widgets", "lines",
           "ocr_words", "compare", "mentions", "page_texts", "pdf_box"}
CHANGES = {"open", "replay", "apply", "save", "export", "pdfa"}
NO_DOC = {"open", "replay", "inspect", "merge", "make_pdf", "sign", "verify", "make_certificate"}
ALLOWED = QUERIES | CHANGES | NO_DOC | {"export_as", "to_images"}


def run(engine, op, args):
    if op not in ALLOWED:
        raise EngineError("bad_op")
    if op not in NO_DOC and engine.doc is None:
        raise EngineError("not_open")
    if op == "make_pdf":
        return convert.make_pdf(*args)
    if op == "export_as":
        return convert.export_as(engine, *args)
    if op == "to_images":
        return convert.to_images(engine, *args)
    if op in ("sign", "verify", "make_certificate"):
        return getattr(signing, op)(*args)
    return getattr(engine, op)(*args)


def serve(conn):
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
            conn.send(("ok", run(engine, op, args)))
        except EngineError as e:
            conn.send(("err", str(e)))
        except Exception as e:
            conn.send(("err", f"internal: {type(e).__name__}: {e}"))
