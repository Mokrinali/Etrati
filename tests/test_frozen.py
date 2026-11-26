"""What a packed program (PyInstaller exe) needs."""
import json
import sys

from furceli import convert
from furceli import __main__ as entry


def test_child_command_in_a_packed_program(monkeypatch):
    """An exe takes no "-c": the program runs the helper itself."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\ETRATI.exe")
    cmd = convert._child_command("_docx_child", ("in.pdf", "out.docx", [0, 2]))
    assert cmd[:3] == [r"C:\Apps\ETRATI.exe", "--run-child", "_docx_child"]
    assert json.loads(cmd[3]) == ["in.pdf", "out.docx", [0, 2]]


def test_child_command_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = convert._child_command("_docx_child", ("a", "b", [1]))
    assert cmd[0] == sys.executable and "-c" in cmd


def test_the_program_runs_only_known_helpers(monkeypatch):
    ran = []
    monkeypatch.setattr(convert, "_docx_child", lambda *a: ran.append(a))
    assert entry.run_child("_docx_child", json.dumps(["x", "y", [3]])) == 0
    assert ran == [("x", "y", [3])]
    assert entry.run_child("os.system", "[]") == 2 and len(ran) == 1


def test_helper_failure_is_a_failed_exit(monkeypatch):
    def boom(*a):
        raise RuntimeError("x")
    monkeypatch.setattr(convert, "_xlsx_child", boom)
    assert entry.run_child("_xlsx_child", "[]") == 1
