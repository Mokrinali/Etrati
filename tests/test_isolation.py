"""Engine isolation, step A: the engine writes only into its own folder; the window puts
files in their place."""
import os
import time

import pymupdf
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from test_pages import build
from test_ui import app, open_doc, wait, win  # noqa: F401
from test_ui_pages import idle  # noqa: F401

from furceli import client, files

WRITES = {"save": [0], "export": None, "merge": [2], "pdfa": [0], "make_pdf": [1],
          "export_as": [1], "to_images": None, "sign_prepare": [1], "sign_finish": [0],
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


# -- step B: Office and Edge run in the helper, never in the engine --------------------------
def office_and_web(tmp_path):
    import docx
    d = docx.Document()
    d.add_paragraph("HELLOWORD ქართული")
    d.save(str(tmp_path / "a.docx"))
    (tmp_path / "b.html").write_text("<p>HELLOHTML</p>", encoding="utf-8")
    return str(tmp_path / "a.docx"), str(tmp_path / "b.html")


def test_engine_does_not_start_office_or_edge(app, tmp_path):  # noqa: F811
    word, page = office_and_web(tmp_path)
    c = client.EngineClient()
    try:
        for i, path in enumerate((word, page)):
            with pytest.raises(client.TaskError, match="needs_helper#0"):
                client.run_task(c, "make_pdf", ([path], files.outbox(path + ".pdf")), "", None)
    finally:
        c.shutdown()


def test_helper_converts_only_inside_the_engine_folder(app, tmp_path):  # noqa: F811
    from furceli import helper
    word, _ = office_and_web(tmp_path)
    h = client.EngineClient(serve=helper.serve)
    try:
        for op, args in (("open", (word, "")), ("render", (0, 1, 0)), ("make_pdf", ([word], "x"))):
            with pytest.raises(client.TaskError, match="bad_op"):
                client.run_task(h, op, args, "", None)
        with pytest.raises(client.TaskError, match="outside"):
            client.run_task(h, "convert", ("word", word, files.outbox("a.pdf")), "", None)
    finally:
        h.shutdown()


def test_window_converts_office_and_html_through_the_helper(app, tmp_path):  # noqa: F811
    from furceli import helper
    word, page = office_and_web(tmp_path)
    pic = tmp_path / "c.png"
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), 0).save(str(pic))
    c = client.EngineClient()
    try:
        try:
            ready = helper.convert_first(c, [word, page, str(pic)], False, "", None)
        except client.TaskError as e:
            if "no_office" in str(e) or "no_edge" in str(e):
                pytest.skip(str(e))
            raise
        assert ready[2] == str(pic) and all(inside(p, files.engine_root()) for p in ready[:2])
        out = files.outbox(str(tmp_path / "all.pdf"))
        assert client.run_task(c, "make_pdf", (ready, out), "", None)["pages"] == 3
    finally:
        c.shutdown()
    text = "".join(p.get_text() for p in pymupdf.open(out))
    assert "HELLOWORD" in text and "HELLOHTML" in text


def test_helper_errors_keep_the_number_of_the_file(app, tmp_path):  # noqa: F811
    from furceli import helper
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    c = client.EngineClient()
    try:
        with pytest.raises(client.TaskError, match="untrusted#1"):
            helper.convert_first(c, [str(tmp_path / "x.png"), str(bad)], False, "", None)
    finally:
        c.shutdown()


# -- steps C and D: the engine has few rights ------------------------------------------------
def test_engine_runs_at_low_integrity(app, tmp_path):  # noqa: F811
    from furceli import sandbox
    c = client.EngineClient()
    try:
        client.run_task(c, "open", (build(tmp_path / "a.pdf"), ""), "", None)
        assert sandbox.failed == "" and sandbox.integrity(c._proc.pid) == 0x1000
    finally:
        c.shutdown()


def test_engine_cannot_write_into_the_users_folders(app, tmp_path):  # noqa: F811
    c = client.EngineClient()
    home = os.path.join(os.path.expanduser("~"), f"etrati-probe-{os.getpid()}.txt")
    desk = os.path.join(os.environ["USERPROFILE"], "Desktop", f"etrati-probe-{os.getpid()}.txt")
    try:
        mine = files.outbox(str(tmp_path / "ok.txt"))
        assert client.run_task(c, "_probe", (mine,), "", None) == "written"
        for target in (str(tmp_path / "no.txt"), home, desk):
            assert client.run_task(c, "_probe", (target,), "", None) == "PermissionError", target
            assert not os.path.exists(target)
    finally:
        c.shutdown()
        for p in (home, desk):
            if os.path.exists(p):
                os.remove(p)


def test_engine_dies_with_its_window(tmp_path):
    """However the window ends, its engines go with it: the job closes with the process."""
    import subprocess
    import sys
    import time
    import psutil
    script = tmp_path / "window.py"
    script.write_text("\n".join([
        "import sys, time",
        "if __name__ == '__main__':  # spawn runs this file again in the engine",
        "    from PySide6.QtWidgets import QApplication",
        "    app = QApplication([])",
        "    from furceli import client",
        "    c = client.EngineClient()",
        "    client.run_task(c, 'inspect', (sys.argv[1], ''), '', None)",
        "    c.submit(('busy',), 0, '_sleep', 30)  # busy: it does not notice the pipe close",
        "    time.sleep(1)",
        "    print(c._proc.pid, flush=True)",
        "    time.sleep(60)", ""]), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p = subprocess.Popen([sys.executable, str(script), build(tmp_path / "a.pdf")],
                         stdout=subprocess.PIPE, env=env, text=True)
    try:
        pid = int(p.stdout.readline())
        engine = psutil.Process(pid)
        assert engine.is_running()
        p.kill()  # as a crash would: nobody shuts anything down
        end = time.time() + 10
        while time.time() < end and engine.is_running():
            time.sleep(.1)
        assert not engine.is_running()
    finally:
        if p.poll() is None:
            p.kill()


# -- nothing of a document stays in the engine folder ---------------------------------------
def leftovers(root):
    from furceli.files import MARK
    out = []
    for dirpath, dirs, names in os.walk(root):
        out += [os.path.join(dirpath, n) for n in names if n != MARK]
    return out


def test_a_killed_engine_leaves_nothing(app, tmp_path):  # noqa: F811
    c = client.EngineClient()
    try:
        client.run_task(c, "open", (build(tmp_path / "a.pdf"), ""), "", None)
        folder = client.run_task(c, "_litter", (), "", None)
        assert os.path.exists(os.path.join(folder, "copy.pdf"))
        crashed = []
        c.crashed.connect(lambda: crashed.append(1))
        c.submit(("boom",), 0, "_crash")
        wait(lambda: crashed)
        wait(lambda: not os.path.exists(folder), 10)
    finally:
        c.shutdown()


def test_converting_office_leaves_nothing_behind(app, tmp_path):  # noqa: F811
    from furceli import helper
    word, page = office_and_web(tmp_path)
    before = set(leftovers(files.engine_root()))
    c = client.EngineClient()
    try:
        try:
            ready = helper.convert_first(c, [word, page], False, "", None)
        except client.TaskError as e:
            if "no_office" in str(e) or "no_edge" in str(e):
                pytest.skip(str(e))
            raise
        out = files.outbox(str(tmp_path / "all.pdf"))
        client.run_task(c, "make_pdf", (ready, out), "", None)
        helper.tidy(ready + [out])
        with pytest.raises(client.TaskError):  # a bad second file: the first one's PDF goes too
            helper.convert_first(c, [word, str(tmp_path / "missing.docx")], False, "", None)
    finally:
        c.shutdown()
    left = set(leftovers(files.engine_root())) - before
    assert not left, left


def test_closing_the_window_removes_the_engine_folder(win, tmp_path):  # noqa: F811
    open_doc(win, build(tmp_path / "a.pdf"))
    root = files.engine_root()
    win.close()
    assert not os.path.exists(root)


# -- when Windows gives less ------------------------------------------------------------------
def test_without_a_job_the_engine_still_has_few_rights(app, tmp_path, monkeypatch):  # noqa: F811
    from furceli import sandbox

    def no_job():
        raise OSError("refused")
    monkeypatch.setattr(sandbox, "_job", None)
    monkeypatch.setattr(sandbox, "_make_job", no_job)
    monkeypatch.setattr(sandbox, "weakened", "")
    c = client.EngineClient()
    try:
        client.run_task(c, "inspect", (build(tmp_path / "a.pdf"), ""), "", None)
        assert sandbox.integrity(c._proc.pid) == 0x1000 and not sandbox.failed
        assert "no job" in sandbox.weakened
    finally:
        c.shutdown()


def test_a_window_inside_a_job_of_its_own(tmp_path):
    """Some launchers put programs into a job with UI limits: the engine must still start."""
    import subprocess
    import sys
    script = tmp_path / "boxed.py"
    script.write_text("\n".join([
        "import ctypes, sys",
        "if __name__ == '__main__':",
        "    k = ctypes.WinDLL('kernel32')",
        "    k.CreateJobObjectW.restype = ctypes.c_void_p",
        "    job = k.CreateJobObjectW(None, None)",
        "    ui = ctypes.c_uint32(0xff)  # every UI limit there is",
        "    assert k.SetInformationJobObject(ctypes.c_void_p(job), 4, ctypes.byref(ui), 4)",
        "    assert k.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(k.GetCurrentProcess()))",
        "    from PySide6.QtWidgets import QApplication",
        "    app = QApplication([])",
        "    from furceli import client, sandbox",
        "    c = client.EngineClient()",
        "    client.run_task(c, 'inspect', (sys.argv[1], ''), '', None)",
        "    print(hex(sandbox.integrity(c._proc.pid)), repr(sandbox.failed), repr(sandbox.weakened))",
        "    c.shutdown()", ""]), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    r = subprocess.run([sys.executable, str(script), build(tmp_path / "a.pdf")],
                       capture_output=True, env=env, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    assert r.stdout.startswith("0x1000 ''"), r.stdout


def test_the_window_tells_when_the_engine_has_more_rights(win, tmp_path, monkeypatch):  # noqa: F811
    from furceli import sandbox
    monkeypatch.setattr(sandbox, "failed", "OSError: refused")
    open_doc(win, build(tmp_path / "a.pdf"))
    sandbox.failed = "OSError: refused"  # the engine start above has reset it
    win._walls_told = False
    win._tell_walls()
    assert "სრული უფლებებით" in win.statusBar().currentMessage()


# -- the window trusts nothing the engine sends (review 2026-11-27) -------------------------
def test_a_taken_over_engine_cannot_run_code_in_the_window(app, tmp_path):  # noqa: F811
    # An engine that a bad PDF took over sends a reply built to run a command when read.
    c = client.EngineClient()
    marker = tmp_path / "ran-in-the-window.txt"
    try:
        with pytest.raises(client.TaskError):
            client.run_task(c, "_evil", (str(marker),), "", None)
        assert not marker.exists()
        # and the next engine works as before
        assert client.run_task(c, "_probe", (files.outbox(str(tmp_path / "ok.txt")),), "", None) \
            == "written"
    finally:
        c.shutdown()


def test_an_engine_cannot_make_the_window_end_other_programs(app, tmp_path):  # noqa: F811
    import subprocess
    import sys
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    c = client.EngineClient()
    try:
        client.run_task(c, "_claim", (other.pid,), "", None)  # "I started this one"
        c._kill()  # as when the engine is killed: what it started is ended
        time.sleep(.5)
        assert other.poll() is None  # a program the engine did not start lives on
    finally:
        c.shutdown()
        other.kill()


def test_a_picture_with_too_few_bytes_is_refused():
    from furceli import view
    for bad in ((100, 100, 300, b"x" * 10), (2, 2, 3, b"\x00" * 12), (-1, 2, 6, b""),
                ("2", 2, 6, b"\x00" * 12), (2, 2, 6, "text"), None, (1, 2)):
        assert view.to_image(bad).isNull(), bad  # drawn as nothing, never read past the end
    img = view.to_image((2, 2, 6, b"\x00" * 12))
    assert not img.isNull() and img.width() == 2
