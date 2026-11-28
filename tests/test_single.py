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


def test_channel_is_per_user_and_session_not_per_name(monkeypatch):
    """Fable's review, №9: pipe names are shared by the whole computer. A user name is not
    unique (a local and a domain "admin"), and one user may sit in two sessions."""
    monkeypatch.setattr(single, "_identity", lambda: ("S-1-5-21-1-1001", 1))
    first = single.channel()
    monkeypatch.setattr(single.getpass, "getuser", lambda: "someone else")
    assert single.channel() == first  # the account decides, not its name
    monkeypatch.setattr(single, "_identity", lambda: ("S-1-5-21-1-1001", 2))
    assert single.channel() != first  # another session of the same user
    monkeypatch.setattr(single, "_identity", lambda: ("S-1-5-21-2-1001", 1))
    assert single.channel() != first  # another account with the same name


def test_this_users_own_server_is_trusted(app, name):  # noqa: F811
    from PySide6.QtNetwork import QLocalServer
    server = QLocalServer()
    assert server.listen(name)
    s = QLocalSocket()
    s.connectToServer(name)
    assert s.waitForConnected(1000)
    assert single.server_is_mine(s) is True
    s.abort()
    server.close()


def test_files_are_not_given_to_a_stranger(app, name, monkeypatch):  # noqa: F811
    """A program of another user may take the pipe's name first; it gets no paths."""
    from PySide6.QtNetwork import QLocalServer
    server = QLocalServer()
    assert server.listen(name)
    monkeypatch.setattr(single, "server_is_mine", lambda sock: False)
    assert single.hand_over([r"C:\secret\contract.pdf"], name, wait=300) is False
    end = time.time() + .5
    while time.time() < end:
        QApplication.processEvents()
    conn = server.nextPendingConnection()
    heard = b""
    if conn is not None:
        conn.waitForReadyRead(200)
        heard = bytes(conn.readAll().data())
    assert b"secret" not in heard
    server.close()


def test_channel_is_private_to_settings(monkeypatch):
    monkeypatch.setenv("FURCELI_SETTINGS", "a")
    first = single.channel()
    monkeypatch.setenv("FURCELI_SETTINGS", "b")
    assert first != single.channel() and first.startswith("ETRATI-")
    assert json.dumps(first)
