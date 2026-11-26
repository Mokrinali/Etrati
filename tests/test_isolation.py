"""Engine isolation, step A: the engine writes only into its own folder; the window puts
files in their place."""
import os

import pymupdf
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from test_pages import build
from test_ui import app, open_doc, wait, win  # noqa: F401
from test_ui_pages import idle  # noqa: F401

from furceli import client, files

WRITES = {"save": [0], "export": None, "merge": [2], "pdfa": [0], "make_pdf": [1],
          "export_as": [1], "to_images": None, "sign": [1], "make_certificate": [0],
          "scan_build": [3], "scan_join": [1]}


def outputs(op, args):
    """The paths an engine operation writes to."""
    if op == "export":
        return [p for _, p in args[0]]
    if op == "to_images":
        return [p for _, p in args[0]]
    return [args[i] for i in WRITES[op]]


def inside(path, root):
    path, root = (os.path.normcase(os.path.realpath(p)) for p in (path, root))
    return os.path.commonpath([path, root]) == root


@pytest.fixture
def writes(monkeypatch):
    seen = []
    real = client.EngineClient.submit

    def submit(self, key, priority, op, *args):
        if op == "task" and args and args[0] in WRITES:
            seen.append((args[0], args[1]))
        elif op in WRITES:
            seen.append((op, args))
        return real(self, key, priority, op, *args)
    monkeypatch.setattr(client.EngineClient, "submit", submit)
    return seen


def test_engine_refuses_to_write_outside_its_folder(app, tmp_path):  # noqa: F811
    src = build(tmp_path / "a.pdf")
    c = client.EngineClient()
    try:
        client.run_task(c, "open", (src, ""), "", None)
        with pytest.raises(client.TaskError, match="outside"):
            client.run_task(c, "save", (str(tmp_path / "b.pdf"),), "", None)
        assert not os.path.exists(tmp_path / "b.pdf")
        mine = files.outbox(str(tmp_path / "b.pdf"))
        assert inside(mine, files.engine_root())
        client.run_task(c, "save", (mine,), "", None)
        assert pymupdf.open(mine).page_count == 6
        sneaky = os.path.join(files.engine_root(), "..", "escape.pdf")
        with pytest.raises(client.TaskError, match="outside"):
            client.run_task(c, "save", (sneaky,), "", None)
    finally:
        c.shutdown()


def test_every_write_of_the_window_goes_through_the_engine_folder(
        win, tmp_path, monkeypatch, writes):  # noqa: F811
    doc = open_doc(win, build(tmp_path / "doc.pdf"))
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out / "copy.pdf"), ""))
    assert doc.save_as()
    for kind in ("txt", "html"):
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            lambda *a, k=kind, **kw: (str(out / f"e.{k}"), ""))
        assert doc.export_as(kind)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(out))
    made = doc.export_images("png", 30)
    idle(doc)
    assert len(made) == 6 and all(os.path.exists(p) for p in made)
    assert (out / "copy.pdf").exists() and (out / "e.txt").exists() and (out / "e.html").exists()
    assert writes, "nothing was recorded"
    for op, args in writes:
        for p in outputs(op, args):
            assert inside(p, files.engine_root()), (op, p)
    # nothing half-written stays next to the user's files
    assert not [n for n in os.listdir(out) if "furceli" in n]


def test_put_in_place_across_drives(tmp_path, monkeypatch):
    """The engine folder may be on another drive than the target: a rename cannot do."""
    src = tmp_path / "work.pdf"
    src.write_bytes(b"new")
    target = tmp_path / "t" / "doc.pdf"
    target.parent.mkdir()
    target.write_bytes(b"old")
    real = os.replace

    def no_cross(a, b):
        if os.path.dirname(os.path.abspath(a)) != os.path.dirname(os.path.abspath(b)):
            raise OSError(17, "not the same device")
        return real(a, b)
    monkeypatch.setattr(os, "replace", no_cross)
    monkeypatch.setattr(files, "_replace", lambda tmp, t: no_cross(tmp, t))
    backup = files.put_in_place(str(src), str(target))
    assert target.read_bytes() == b"new" and not src.exists()
    assert backup and open(backup, "rb").read() == b"old"
    assert not [n for n in os.listdir(target.parent) if n != "doc.pdf"]


def test_pictures_are_all_or_nothing(tmp_path, monkeypatch):
    made = [tmp_path / f"{i}.png" for i in range(3)]
    work = []
    for i in range(3):
        w = files.outbox(str(made[i]))
        open(w, "wb").write(b"x")
        work.append(w)
    (tmp_path / "2.png").write_bytes(b"somebody's")  # taken meanwhile
    with pytest.raises(OSError):
        files.deliver_all(list(zip(work, map(str, made))))
    assert not made[0].exists() and not made[1].exists()
    assert made[2].read_bytes() == b"somebody's"
