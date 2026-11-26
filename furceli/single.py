"""One window for all: a file opened from Explorer becomes a tab of the running program."""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import sys
import time

from PySide6.QtCore import QDir, QLockFile, QObject, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

LIMIT = 1 << 20
END = bytes([10])  # a newline


def channel() -> str:
    """A name of its own for every user (and for every settings file, in tests)."""
    key = getpass.getuser() + "|" + os.environ.get("FURCELI_SETTINGS", "")
    return "ETRATI-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def hand_over(paths, name=None, wait=3000) -> bool:
    """Give the files to a running program. False when there is none (or it does not answer)."""
    sock = QLocalSocket()
    sock.connectToServer(name or channel())
    if not sock.waitForConnected(300):
        return False
    if sys.platform == "win32":  # let the other window come to the front
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    sock.write(json.dumps([os.path.abspath(p) for p in paths]).encode() + END)
    sock.flush()
    ok = sock.waitForReadyRead(wait) and bytes(sock.readAll().data()).startswith(b"ok")
    sock.abort()
    return ok


def claim():
    """The right to be the running program; None when another start already has it."""
    lock = QLockFile(os.path.join(QDir.tempPath(), channel() + ".lock"))
    lock.setStaleLockTime(0)  # only a program that is really gone loses it
    return lock if lock.tryLock(0) else None


class Receiver(QObject):
    """Listens for files from later starts. Only this Windows user can connect."""

    arrived = Signal(list)

    def __init__(self, name=None, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        self.listening = self._server.listen(name or channel())

    def _accept(self):
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        # Read here and now: the sender has already written, and it is this user's program.
        data, end = b"", time.monotonic() + 1.0
        while END not in data and len(data) < LIMIT and time.monotonic() < end:
            if not sock.bytesAvailable() and not sock.waitForReadyRead(100):
                if sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
                    break
                continue
            data += bytes(sock.readAll().data())
        paths = None
        if END in data:
            try:
                paths = json.loads(data.split(END)[0].decode())
            except ValueError:
                pass
        if isinstance(paths, list):
            sock.write(b"ok" + END)
            sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        sock.deleteLater()
        if isinstance(paths, list):
            paths = [p for p in paths if isinstance(p, str)]
            QTimer.singleShot(0, lambda: self._deliver(paths))
        if self._server.hasPendingConnections():
            QTimer.singleShot(0, self._accept)

    def _deliver(self, paths):
        # A dialog or a running save comes first; the files wait for it.
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(300, lambda: self._deliver(paths))
        else:
            self.arrived.emit(paths)


def bring_to_front(win):
    if win.isMinimized():  # back to what it was: normal or maximised
        win.setWindowState(win.windowState() & ~Qt.WindowState.WindowMinimized)
    win.raise_()
    win.activateWindow()
