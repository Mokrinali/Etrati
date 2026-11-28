"""OCR inside MuPDF/Tesseract now and then crashes the engine (HANDOFF, point 2). An operation
that only reads and makes its own output is then tried once more, in a new engine."""
import os

import pytest
from test_ui import app  # noqa: F401

from furceli import client, files


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(client, "RETRY_ONCE", client.RETRY_ONCE | {"_crash_once", "_crash", "_sleep"})
    c = client.EngineClient()
    yield c
    c.shutdown()


def test_the_operations_that_recognise_text_are_tried_again():
    assert {"scan_analyse", "scan_build", "ocr_words"} <= client.RETRY_ONCE


def died():
    """A mark the engine may write (its own folder), so the second try does not crash."""
    return files.outbox("died")


def test_a_crash_once_is_not_seen(app, engine):  # noqa: F811
    mark, crashes = died(), []
    engine.crashed.connect(lambda: crashes.append(1))
    assert client.run_task(engine, "_crash_once", (mark,)) == "again"
    assert os.path.exists(mark) and crashes == [1]  # it did crash the first time


def test_a_second_crash_is_an_error(app, engine):  # noqa: F811
    with pytest.raises(client.TaskError, match="crash"):
        client.run_task(engine, "_crash", ())
    assert engine._restarts == 2  # tried twice, not more


def test_an_engine_that_hangs_is_not_waited_for_twice(app, engine, monkeypatch):  # noqa: F811
    monkeypatch.setitem(client.LONG, "_sleep", .5)
    with pytest.raises(client.TaskError, match="crash"):
        client.run_task(engine, "_sleep", (5,))
    assert engine._restarts == 1


def test_a_test_that_crashes_its_own_process_does_not_end_the_run(tmp_path):
    import shutil
    import subprocess
    import sys
    shutil.copy(os.path.join(os.path.dirname(__file__), "conftest.py"), tmp_path)
    (tmp_path / "test_x.py").write_text(
        "import os, pytest\n"
        "@pytest.mark.own_process\n"
        "def test_crash():\n    os._exit(3)\n"
        "def test_after():\n    pass\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "FURCELI_OWN_PROCESS"}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "--basetemp", str(tmp_path / "t"), str(tmp_path / "test_x.py")],
                       cwd=str(tmp_path), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert "1 failed, 1 passed" in r.stdout and "exit code 3" in r.stdout, r.stdout


def test_other_operations_are_not_tried_again(app):  # noqa: F811
    c = client.EngineClient()
    try:
        with pytest.raises(client.TaskError, match="crash"):
            client.run_task(c, "_crash_once", (died(),))
    finally:
        c.shutdown()
