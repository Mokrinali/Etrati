"""The helper: Office and Edge turn Office and HTML files into PDFs, outside the engine.

The engine reads strangers' files and is meant to run with few rights, where Office and
Edge cannot be started. The helper is a process of normal rights that only converts: it
never opens a PDF, takes only files the engine made harmless in its own folder, and is
driven by the window alone, so a PDF that took over an engine cannot reach it.
"""
from __future__ import annotations

import os

from . import convert, files
from .engine import EngineError


def _inside(path, root) -> bool:
    try:
        path, root = (os.path.normcase(os.path.realpath(str(p))) for p in (path, root))
        return os.path.commonpath([path, root]) == root and path != root
    except (TypeError, ValueError, OSError):
        return False


def serve(conn, root):
    """The helper process: ("convert", (kind, path, out)) and nothing else."""
    files._root = root
    convert.notify = conn.send  # Office and Edge it starts, so the window can end them
    while True:
        try:
            op, args = conn.recv()
        except (EOFError, OSError):
            return
        if op == "quit":
            return
        try:
            if op != "convert" or len(args) != 3:
                raise EngineError("bad_op")
            kind, path, out = args
            if not (_inside(path, root) and _inside(out, root)):
                raise EngineError("outside")
            convert.helper_convert(kind, path, out)
            conn.send(("ok", None))
        except EngineError as e:
            conn.send(("err", str(e)))
        except Exception:  # noqa: BLE001 - the window must get an answer
            conn.send(("err", "convert_failed"))


def convert_first(engine, paths, trusted, text, parent, allow=None):
    """paths with their Office and HTML files turned into PDFs, for make_pdf.

    The engine makes each one harmless, the helper converts it. An error code ends with the
    number of the file, as those of make_pdf do.
    """
    from .client import EngineClient, TaskError, run_task
    out, helper = list(paths), None
    try:
        for i, path in enumerate(paths):
            if os.path.splitext(path)[1].lower() not in convert.NEEDS_HELPER:
                continue
            if helper is None:
                helper = EngineClient(parent, serve=serve)
            pdf, ready = files.outbox(os.path.basename(path) + ".pdf"), None
            try:
                kind, ready = run_task(engine, "prepare", (path, trusted), text, parent,
                                       allow=allow)
                run_task(helper, "convert", (kind, ready, pdf), text, parent, allow=allow)
            except TaskError as e:
                tidy([pdf])
                if str(e) == "cancelled":
                    raise
                raise TaskError(f"{e}#{i}")
            finally:
                if ready:
                    tidy([ready])  # the cleaned copy of the stranger's file
            out[i] = pdf
    except BaseException:
        tidy([o for o, p in zip(out, paths) if o != p])  # those converted before the failure
        raise
    finally:
        if helper is not None:
            helper.shutdown()
    return out


def tidy(paths):
    """Remove files made in the engine's folder, with their own folders; others are left."""
    import shutil
    root = files.engine_root()
    for p in paths:
        folder = os.path.dirname(os.path.abspath(p))
        if _inside(p, root) and os.path.dirname(folder) == os.path.abspath(root):
            shutil.rmtree(folder, ignore_errors=True)
