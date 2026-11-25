"""Opening a second file goes to the running window."""
import json
import os
import subprocess
import sys
import time
import uuid

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog
from test_ui import app, wait  # noqa: F401

from furceli import single

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def name():
    return "ETRATI-test-" + uuid.uuid4().hex


def send(name, paths):
    """From another process, like a real second start."""
    code = (f"import sys; sys.path.insert(0, {ROOT!r}); from PySide6.QtCore import QCoreApplication; "
            f"a = QCoreApplication([]); from furceli import single; "
            f"print(single.hand_over({paths!r}, {name!r}))")
    return subprocess.Popen([sys.executable, "-X", "utf8", "-c", code], stdout=subprocess.PIPE,
                            text=True, encoding="utf8")


def test_files_reach_the_running_window(app, name, tmp_path):  # noqa: F811
    r, got = single.Receiver(name), []
    r.arrived.connect(got.append)
    assert r.listening
    files = [str(tmp_path / "ფაილი ერთი.pdf"), str(tmp_path / "b & c.pdf")]
    p = send(name, files)
    wait(lambda: got)
    assert got == [files] and p.communicate(timeout=10)[0].strip() == "True"


def test_nobody_listening(app, name):  # noqa: F811
    t = time.time()
    assert single.hand_over(["x.pdf"], name) is False
    assert time.time() - t < 3


def test_files_wait_for_an_open_dialog(app, name):  # noqa: F811
    r, got, dlg = single.Receiver(name), [], QDialog()
    r.arrived.connect(got.append)
    dlg.setModal(True)
    dlg.show()
    p = send(name, ["x.pdf"])
    end = time.time() + 2.5
    while time.time() < end:
        QApplication.processEvents()
    assert not got and p.communicate(timeout=10)[0].strip() == "True"
    QTimer.singleShot(0, dlg.close)
    wait(lambda: got)


@pytest.mark.parametrize("data", [b"not json\n", b'{"a":1}\n', b"[1, null]\n", b"x" * (1 << 21)])
def test_garbage_is_ignored(app, name, data):  # noqa: F811
    r, got = single.Receiver(name), []
    r.arrived.connect(got.append)
    s = QLocalSocket()
    s.connectToServer(name)
    assert s.waitForConnected(1000)
    s.write(data)
    s.flush()
    end = time.time() + .5
    while time.time() < end:
        QApplication.processEvents()
    assert got in ([], [[]])
    s.abort()


def test_channel_is_private_to_settings(monkeypatch):
    monkeypatch.setenv("FURCELI_SETTINGS", "a")
    first = single.channel()
    monkeypatch.setenv("FURCELI_SETTINGS", "b")
    assert first != single.channel() and first.startswith("ETRATI-")
    assert json.dumps(first)
