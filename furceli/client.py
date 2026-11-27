"""UI-side handle to the engine process: priority queue, cancellation, restart."""
from __future__ import annotations

import heapq
import io
import itertools
import os
import pickle
import multiprocessing as mp
import threading
import time

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QProgressDialog, QWidget

from . import files
from .worker import serve as worker_serve

# Lower runs first.
P_OPEN, P_PRINT, P_VISIBLE, P_TEXT, P_THUMB, P_SEARCH = 0, 5, 10, 20, 30, 40
TIMEOUT = 60.0
LONG = {"save": 900.0, "export": 900.0, "merge": 900.0, "replay": 300.0,
        "apply": 300.0, "open": 120.0, "make_pdf": 900.0, "convert": 900.0, "export_as": 1800.0,
        "to_images": 900.0, "compare": 600.0, "visual_diff": 300.0, "ocr_words": 300.0,
        "sign": 120.0, "pdfa": 900.0,
        "all_annots": 300.0, "scan_build": 300.0, "scan_analyse": 180.0, "scan_join": 600.0,
        "scan_count": 300.0, "scan_preview": 180.0, "page_texts": 300.0, "mentions": 300.0}
MAX_RESTARTS = 5             # in a row, without a single success between them
ALWAYS_TRY = {"save", "export", "replay", "open", "pdfa"}  # the user's work depends on these


MAX_REPLY = 1 << 29  # a reply larger than this is no answer of an engine that works
OFFICE = {"winword.exe", "excel.exe", "powerpnt.exe", "msedge.exe"}


class _Plain(pickle.Unpickler):
    """Reads only plain data: numbers, text, bytes, lists, tuples, dicts, sets. A reply of the
    engine (the side a bad PDF may take over) can name no class or function to call."""

    def find_class(self, module, name):
        raise pickle.UnpicklingError(f"{module}.{name}")


def plain(data: bytes):
    return _Plain(io.BytesIO(data)).load()


class FileChanged(Exception):
    """The engine was restarted and found another file under the document's name."""


_finishing: list = []  # threads that end the engines of closed tabs


def finish_all(seconds=3.0):
    """Before the program ends: engines still ending get their time, all at once."""
    end = time.monotonic() + seconds
    while _finishing:
        _finishing.pop().join(max(end - time.monotonic(), 0))


class EngineClient(QObject):
    """One engine process for one document. Results arrive as Qt signals."""

    done = Signal(object, object)    # key, value
    failed = Signal(object, str)     # key, error code
    crashed = Signal()               # the engine died and will be restarted

    def __init__(self, parent=None, serve=None):
        """serve: what the process runs; the engine by default, the helper for Office."""
        super().__init__(parent)
        self._serve = serve or worker_serve
        self._ctx = mp.get_context("spawn")
        self._root = files.engine_root()  # the only folder the engine may write to
        self._cv = threading.Condition()
        self._pending: dict = {}     # key -> (seq, op, args)
        self._heap: list = []        # (priority, seq, key); stale entries are skipped
        self._seq = itertools.count()
        self._stop = False
        self._proc = self._conn = None
        self._opened = None          # (op, args) that rebuilds the document after a crash
        self._stamp = None           # the file as it was opened: (time, size)
        self._lost = False           # it is another file now: nothing may be read from it
        self._restarts = 0
        self._current = None         # key being worked on
        self._aborting = False
        self._helpers: list = []     # (pid, start time) of programs the engine started
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # -- called from the UI thread ----------------------------------------
    def submit(self, key, priority: int, op: str, *args):
        with self._cv:
            if key not in self._pending and (key != self._current or self._aborting):
                seq = next(self._seq)
                self._pending[key] = (seq, op, args)
                heapq.heappush(self._heap, (priority, seq, key))
                self._cv.notify()

    def cancel(self, predicate):
        with self._cv:
            for k in [k for k in self._pending if predicate(k)]:
                del self._pending[k]

    def abort(self, key):
        """Give up one operation. The engine is restarted and rebuilds the document."""
        with self._cv:
            if key in self._pending:
                del self._pending[key]
                waiting = True
            else:
                waiting = False
                if self._current == key:
                    self._aborting = True
                    if self._proc is not None:  # else it dies as soon as it has started
                        self._proc.kill()
        if waiting:
            self.failed.emit(key, "cancelled")

    def recover_with(self, path, password, ops):
        """After a crash, rebuild the edited document rather than the plain file."""
        with self._cv:
            self._opened = ("replay", (path, password, list(ops)))

    def accept_stamp(self, stamp):
        """The window itself replaced the file."""
        with self._cv:
            self._stamp = tuple(stamp)

    def shutdown(self, wait=True, after=None):
        """End the engine. wait=False leaves the waiting to a thread of its own: closing a tab
        must not hold up the window. after() runs once the engine is gone."""
        with self._cv:
            if self._stop:
                return
            waiting = [k for k in [self._current, *self._pending]
                       if k is not None and k[0] in ("task", "print")]
            self._stop = True
            self._pending.clear()
            self._cv.notify()
        for key in waiting:  # somebody is blocked on these
            self.failed.emit(key, "closed")
        t = threading.Thread(target=self._finish, args=(after,), daemon=True)
        t.start()
        _finishing.append(t)
        if wait:
            t.join(3)

    def _finish(self, after):
        try:
            proc = self._proc
            if proc is not None:
                proc.join(0.3)
                if proc.is_alive():
                    proc.kill()
            self._thread.join(2)
            self._stop_helpers()
            files.sweep_sessions()  # its TEMP and working folders
        finally:
            if after is not None:
                after()

    # -- worker thread -----------------------------------------------------
    def _process(self, child):
        """The engine runs with few rights (furceli/sandbox.py); the helper with normal ones."""
        args = (child, self._root)
        if self._serve is worker_serve and os.name == "nt":
            from . import sandbox
            try:
                if not files.engine_root_is_low():  # it could not even write its own folder
                    raise OSError("the engine folder is not under LocalLow")
                proc = sandbox.LowProcess(target=self._serve, args=args, daemon=True)
                proc._tmp = files.new_session(self._root)  # its own TEMP
                proc.start()
                files.claim(proc._tmp, proc.pid)  # goes with this engine, killed or not
                sandbox.failed = ""
                return proc
            except OSError as e:  # a Windows that refuses: the engine still has to work
                sandbox.failed = f"{type(e).__name__}: {e}"
        proc = self._ctx.Process(target=self._serve, args=args, daemon=True)
        proc.start()
        return proc

    def _start(self):
        parent, child = self._ctx.Pipe()
        proc = self._process(child)
        child.close()
        self._conn = parent
        with self._cv:  # visible to other threads only once it really runs
            self._proc = proc
            if self._stop or self._aborting:
                proc.kill()

    def _kill(self):
        if self._proc is not None:
            self._proc.kill()
            self._proc.join(2)
        if self._conn is not None:
            self._conn.close()
        self._proc = self._conn = None
        self._stop_helpers()
        try:
            files.sweep_sessions()  # what the dead engine left in the temporary folder
        except OSError:
            pass  # tidying up must not stop the work

    def _own(self, value) -> bool:
        """A program the engine says it started: ended with it later, so it must really be
        its own (its child), or Office and Edge that the helper starts through COM."""
        import psutil
        try:
            pid, started = int(value[0]), float(value[1])
            p = psutil.Process(pid)
            if abs(p.create_time() - started) > 1:
                return False
            if self._proc is not None and any(q.pid == self._proc.pid for q in p.parents()):
                return True
            return self._serve is not worker_serve and p.name().lower() in OFFICE
        except (TypeError, ValueError, IndexError, KeyError, psutil.Error):
            return False

    def _stop_helpers(self):
        """End an invisible Office or Edge that a killed engine left behind."""
        import psutil
        helpers, self._helpers = self._helpers, []
        for pid, started in helpers:
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - started) > 1:
                    continue  # the number now belongs to another program
                for q in [*p.children(recursive=True), p]:
                    q.kill()
            except psutil.Error:
                continue

    def _ask(self, op, args):
        self._conn.send((op, args))
        end = time.monotonic() + LONG.get(op, TIMEOUT)
        while True:
            # A process that died while starting never closes its end of the pipe,
            # so do not rely on the pipe alone.
            while not self._conn.poll(min(.25, max(end - time.monotonic(), 0))):
                dead = self._proc is not None and not self._proc.is_alive()
                if (dead and not self._conn.poll(0)) or time.monotonic() >= end:
                    raise EOFError
            try:
                reply = plain(self._conn.recv_bytes(MAX_REPLY))
            except (pickle.UnpicklingError, ValueError, TypeError, AttributeError,
                    IndexError, MemoryError) as e:
                raise EOFError("not a reply") from e
            if not (isinstance(reply, tuple) and len(reply) == 2
                    and reply[0] in ("ok", "err", "started", "ended")):
                raise EOFError("not a reply")
            status, value = reply
            if status == "err" and not isinstance(value, str):
                raise EOFError("not a reply")
            if status == "started":
                if self._own(value):
                    self._helpers.append(tuple(value))
            elif status == "ended":
                self._helpers = [h for h in self._helpers if h != value]
            else:
                return status, value

    def _call(self, op, args):
        if self._proc is None:
            self._start()
            if self._opened and op not in ("open", "replay"):
                status, info = self._ask(*self._opened)
                if status != "ok":
                    raise EOFError
                if self._opened[0] == "open" and tuple(info["stamp"]) != self._stamp:
                    self._lost = True
                    raise FileChanged
        return self._ask(op, args)

    def _remember(self, op, args, value):
        """What rebuilds the document is known here first, before the window hears of it."""
        with self._cv:
            if op == "open":
                self._opened, self._stamp, self._lost = (op, args), tuple(value["stamp"]), False
            elif op == "replay":
                self._opened, self._lost = (op, (*args[:2], list(args[2]))), False
            elif op == "apply" and self._opened and self._opened[0] == "replay":
                path, password, ops = self._opened[1]
                self._opened = ("replay", (path, password, [*ops, args[0]]))

    def _run(self):
        while True:
            with self._cv:
                while not self._pending and not self._stop:
                    self._cv.wait()
                if self._stop:
                    break
                _, seq, key = heapq.heappop(self._heap)
                if key not in self._pending or self._pending[key][0] != seq:
                    continue  # cancelled
                _, op, args = self._pending.pop(key)
                self._current = key
                dead = ("file_changed" if self._lost and op not in ("open", "replay") else
                        "engine_dead" if self._restarts > MAX_RESTARTS and op not in ALWAYS_TRY
                        else "")
                if dead:
                    self._current, self._aborting = None, False
            if dead:
                self._emit(self.failed, key, dead)
                continue
            try:
                status, value = self._call(op, args)
                if status == "ok":
                    self._remember(op, args, value)
            except FileChanged:
                self._kill()
                self._current, self._aborting = None, False
                self._emit(self.failed, key, "file_changed")
                continue
            except Exception:  # whatever went wrong, the window must get an answer
                try:
                    self._kill()
                except Exception:
                    self._proc = self._conn = None
                if self._stop:
                    break
                aborted, self._aborting, self._current = self._aborting, False, None
                if aborted:
                    self._emit(self.failed, key, "cancelled")
                    continue
                self._restarts += 1
                self._emit(self.failed, key, "crash")
                self._emit(self.crashed)
                continue
            with self._cv:  # an abort that came too late must not hit the next operation
                self._current, late, self._aborting = None, self._aborting, False
            if late:
                self._kill()
            if status == "ok":
                self._restarts = 0
                self._emit(self.done, key, value)
            else:
                self._emit(self.failed, key, value)
        try:
            if self._conn is not None:
                self._conn.send(("quit", ()))
        except OSError:
            pass

    def _emit(self, signal, *args):
        if self._stop:
            return
        try:
            signal.emit(*args)
        except RuntimeError:  # QObject already deleted during shutdown
            pass


class TaskError(Exception):
    """An engine task failed; str() is the error code."""


_task_ids = itertools.count()


class _Guard(QObject):
    """Swallows input while a task runs, from its first millisecond.

    A modal progress dialog alone is not enough: it appears only after a delay.
    """

    EATEN = {QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride,
             QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
             QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel, QEvent.Type.ContextMenu,
             QEvent.Type.Drop, QEvent.Type.DragEnter}

    def __init__(self, dialog, allow):
        super().__init__()
        self._dialog, self._allow = dialog, allow
        # The dialog that started the task must not stay alive under it.
        self._old = [w for w in QApplication.topLevelWidgets() if w.isVisible()]

    def eventFilter(self, obj, e):
        kind = e.type()
        if kind == QEvent.Type.Shortcut:
            return True
        if kind == QEvent.Type.Close and isinstance(obj, QWidget) and obj.isWindow():
            if obj not in (self._dialog, self._allow):
                e.ignore()
                return True
        if kind in self.EATEN and isinstance(obj, QWidget):
            window = obj.window()
            if window in (self._dialog, self._allow):
                return False
            # only a dialog that opened on top of the running task may be used
            return not (window is QApplication.activeModalWidget()
                        and all(window is not w for w in self._old))
        return False


def _blocking(start, text, parent, allow=None, cancel=None):
    """Run start(loop) and wait for loop.quit() with the window blocked but alive."""
    loop = QEventLoop()
    dlg = QProgressDialog(text, "გაუქმება", 0, 0, parent)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    if cancel is None:
        dlg.setCancelButton(None)
    else:
        dlg.canceled.connect(cancel)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    dlg.setMinimumDuration(2_000_000_000)  # its own timer never starts for an endless bar
    timer = QTimer(dlg)
    timer.setSingleShot(True)
    timer.timeout.connect(dlg.show)
    if text:  # otherwise somebody else shows progress
        timer.start(400)
    guard = _Guard(dlg, allow)
    app = QApplication.instance()
    app.installEventFilter(guard)
    try:
        start(loop)
        loop.exec()
    finally:
        app.removeEventFilter(guard)
        timer.stop()
        if cancel is not None:
            dlg.canceled.disconnect(cancel)
        dlg.close()
        dlg.deleteLater()


def run_task(client, op, args, text="", parent=None, allow=None):
    """Run an engine operation, keeping the window responsive but blocked.

    allow: a dialog of the caller that stays usable; its cancel button stops the task.
    """
    key = ("task", next(_task_ids))
    box = {}
    hold = {}

    def done(k, v):
        if k == key:
            box["value"] = v
            hold["loop"].quit()

    def failed(k, code):
        if k == key:
            box["error"] = code
            hold["loop"].quit()

    def start(loop):
        hold["loop"] = loop
        client.done.connect(done)
        client.failed.connect(failed)
        if client._stop:
            box["error"] = "closed"
            loop.quit()
            QTimer.singleShot(0, loop.quit)
        else:
            client.submit(key, P_OPEN, op, *args)
    stop = getattr(allow, "canceled", None)  # a progress dialog of the caller

    def abort():
        client.abort(key)
    if stop is not None:
        stop.connect(abort)
    try:
        _blocking(start, text, parent, allow, abort if text else None)
    finally:
        if stop is not None:
            stop.disconnect(abort)
        try:
            client.done.disconnect(done)
            client.failed.disconnect(failed)
        except RuntimeError:
            pass
    if "value" not in box:
        raise TaskError(box.get("error", "closed"))
    return box["value"]


class _Relay(QObject):
    ready = Signal()


def run_thread(fn, text="", parent=None):
    """Run a slow file operation off the window's thread. Re-raises its exception."""
    box, relay = {}, _Relay()

    def work():
        try:
            box["value"] = fn()
        except BaseException as e:
            box["error"] = e
        relay.ready.emit()

    def start(loop):
        relay.ready.connect(loop.quit, Qt.ConnectionType.QueuedConnection)
        threading.Thread(target=work, daemon=True).start()
    _blocking(start, text, parent)
    if "error" in box:
        raise box["error"]
    return box["value"]
