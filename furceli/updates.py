"""Is there a newer Etrati? Asks GitHub for the latest release; only the version is sent
(the request carries "ETRATI/<version>" and nothing about the person or their files).

Installing it happens only after the person agrees: the window downloads the release's zip
(its SHA-256 checked when GitHub gives one), unpacks it into a folder of its own and closes;
the new program, started with `--install-update`, waits for the old one to end, moves the old
folder aside, copies itself into its place and starts. On any failure the old folder goes back.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile

from PySide6.QtCore import QObject, Signal

from . import __version__

LATEST = "https://api.github.com/repos/Mokrinali/Etrati/releases/latest"
PAGE = "https://github.com/Mokrinali/Etrati/releases/latest"
MAX_SIZE = 1 << 30
EXE = "ETRATI.exe"


def _numbers(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def newer(found: str, current: str = __version__) -> bool:
    got = _numbers(found)
    return bool(got) and got > _numbers(current)


def _open(req, timeout):
    import urllib.request
    try:
        import truststore  # the network's own certificates, as Windows has them
        truststore.inject_into_ssl()
    except ImportError:
        pass
    return urllib.request.urlopen(req, timeout=timeout)


def latest(timeout: float = 6) -> dict | None:
    """{"version", "url", "asset", "size", "sha256"} of the newest release, or None."""
    import urllib.request
    req = urllib.request.Request(LATEST, headers={"User-Agent": f"ETRATI/{__version__}",
                                                  "Accept": "application/vnd.github+json"})
    with _open(req, timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    tag = data.get("tag_name") or ""
    if not tag:
        return None
    found = {"version": tag.lstrip("vV"), "url": data.get("html_url") or PAGE,
             "asset": None, "size": 0, "sha256": None}
    for a in data.get("assets") or []:
        name = (a.get("name") or "").lower()
        if name.startswith("etrati") and name.endswith(".zip"):
            digest = a.get("digest") or ""
            found.update(asset=a.get("browser_download_url"), size=int(a.get("size") or 0),
                         sha256=digest[7:].lower() if digest.startswith("sha256:") else None)
            break
    return found


def open_page(url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl(url))


# -- installing --------------------------------------------------------------
class UpdateError(Exception):
    """Something the person is told; the installed program stays as it was."""


class Cancelled(Exception):
    pass


def can_install(found) -> bool:
    """Only a packed program installs by itself, only from a release with a zip, and only into
    a folder that holds Etrati and nothing else (the whole folder is replaced)."""
    return bool(getattr(sys, "frozen", False) and found and found.get("asset")
                and looks_installed(install_target()))


def looks_installed(folder) -> bool:
    """ETRATI.exe and its _internal folder, nothing else: a folder that may be replaced."""
    try:
        names = set(os.listdir(folder))
    except OSError:
        return False
    return EXE in names and names <= {EXE, "_internal"}


LANGUAGES = os.path.join("_internal", "data", "tessdata")


def _keep_languages(old, new):
    """OCR languages the person added to the old program come along."""
    src, dst = os.path.join(old, LANGUAGES), os.path.join(new, LANGUAGES)
    if not os.path.isdir(src):
        return
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        if name.endswith(".traineddata") and not os.path.exists(os.path.join(dst, name)):
            shutil.copy2(os.path.join(src, name), os.path.join(dst, name))


def install_target() -> str:
    return os.path.dirname(os.path.abspath(sys.executable))


def stage_root(target=None) -> str:
    """Beside the installed folder: the same disk, and nothing left on another one."""
    return os.path.abspath(target or install_target()) + ".update"


def download(url, dest, sha256=None, progress=None, cancelled=None, timeout=30):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": f"ETRATI/{__version__}",
                                               "Accept": "application/octet-stream"})
    digest, done = hashlib.sha256(), 0
    try:
        with _open(req, timeout) as r, open(dest, "wb") as out:
            headers = getattr(r, "headers", None)
            total = int(headers.get("Content-Length") or 0) if headers is not None else 0
            while True:
                if cancelled and cancelled():
                    raise Cancelled()
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                done += len(chunk)
                if done > MAX_SIZE:
                    raise UpdateError("ფაილი მოსალოდნელზე გაცილებით დიდია.")
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(done, total)
        if sha256 and digest.hexdigest() != sha256.lower():
            raise UpdateError("ჩამოტვირთული ფაილი დაზიანებულია (საკონტროლო ჯამი არ ემთხვევა).")
    except BaseException:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise


def unpack(zip_path, into) -> str:
    """Unpacks the release; returns the folder that holds ETRATI.exe."""
    root = os.path.realpath(into)
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root)
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                where = os.path.realpath(os.path.join(root, name))
                if os.path.commonpath([root, where]) != root:
                    raise UpdateError("ფაილი საეჭვოა: შეიცავს ბილიკს საქაღალდის გარეთ.")
            z.extractall(root)
    except zipfile.BadZipFile as e:
        raise UpdateError("ჩამოტვირთული ფაილი დაზიანებულია.") from e
    for here, _dirs, names in os.walk(root):
        if EXE in names:
            return here
    raise UpdateError("ჩამოტვირთულ ფაილში Etrati ვერ მოიძებნა.")


def _result_file(target=None):
    return os.path.join(stage_root(target), "result.json")


def _write_result(data, target=None):
    os.makedirs(stage_root(target), exist_ok=True)
    with open(_result_file(target), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def read_result(target=None):
    """Of the installed folder, not of whoever asks: the installer runs from the download."""
    try:
        with open(_result_file(target), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _wait_for(pid, seconds=60):
    if not pid or sys.platform != "win32":
        return
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
    if h:
        k.WaitForSingleObject(h, int(seconds * 1000))
        k.CloseHandle(h)


def _retry(fn, seconds=20):
    """Explorer, an antivirus or a closing engine may hold a file for a moment."""
    end = time.monotonic() + seconds
    while True:
        try:
            return fn()
        except OSError:
            if time.monotonic() >= end:
                raise
            time.sleep(.5)


def _start(cmd):
    subprocess.Popen(cmd, close_fds=True, cwd=os.path.dirname(cmd[0]))


def install(new, target, pid, version, reopen=(), start=_start) -> bool:
    """Run by the new program: put it in place of `target` and start it (or the old one)."""
    _wait_for(pid)
    target = os.path.abspath(target)
    old, fresh = target + ".old", target + ".new"
    opened = [p for p in reopen if p]
    made = False
    try:
        # The whole folder is replaced: only one that holds Etrati and nothing else.
        if not looks_installed(target):
            raise UpdateError("Etrati-ის საქაღალდეში სხვა ფაილებიცაა; ახალი ვერსია ჩამოტვირთეთ ხელით")
        for leftover in (old, fresh):  # of an earlier try
            if os.path.exists(leftover):
                if not looks_installed(leftover):
                    raise UpdateError(f"საქაღალდე {os.path.basename(leftover)} უკვე არსებობს")
                _retry(lambda p=leftover: shutil.rmtree(p))
        # The new program is made whole beside the old one; only then two quick renames.
        made = True
        shutil.copytree(new, fresh)
        _keep_languages(target, fresh)
        _retry(lambda: os.rename(target, old))
        try:
            _retry(lambda: os.rename(fresh, target))
        except OSError:
            _retry(lambda: os.rename(old, target))
            raise
    except Exception as e:
        if made:
            shutil.rmtree(fresh, ignore_errors=True)  # only what this try made
        why = ("Etrati-ის საქაღალდეს სხვა პროგრამა იყენებს ან მასში ჩაწერის უფლება არ გაქვთ"
               if isinstance(e, PermissionError) else str(e))
        _write_result({"ok": False, "version": version, "error": why}, target)
        start([os.path.join(target, EXE)] + opened)
        return False
    _write_result({"ok": True, "version": version}, target)
    start([os.path.join(target, EXE)] + opened)
    return True


def start_installer(new, target, pid, version, reopen=()):
    """The window's last act before it closes: the new program takes over from here."""
    cmd = [os.path.join(new, EXE), "--install-update", target, str(pid), version] + list(reopen)
    subprocess.Popen(cmd, close_fds=True, cwd=new)


def tidy(target):
    """At start: what the last installation left (the old folder, the download), and how it went.

    The result is told once: the folder that holds it goes with the rest."""
    result = read_result(target)
    for leftover in (".old", ".new"):  # only folders of Etrati itself, never anything else
        folder = os.path.abspath(target) + leftover
        if looks_installed(folder):
            shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(stage_root(target), ignore_errors=True)
    return result


class Downloader(QObject):
    """Downloads and unpacks in a thread: progress(kB done, kB total), done(folder or error)."""

    progress = Signal(int, int)
    done = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()

    def cancel(self):
        self._stop.set()

    def start(self, found):
        threading.Thread(target=self._run, args=(found,), daemon=True).start()

    def _run(self, found):
        root = stage_root()
        size = found.get("size") or 0
        try:
            shutil.rmtree(root, ignore_errors=True)
            os.makedirs(root, exist_ok=True)
            path = os.path.join(root, "ETRATI.zip")
            download(found["asset"], path, found.get("sha256"),
                     progress=lambda d, t: self.progress.emit(d // 1024, (t or size) // 1024),
                     cancelled=self._stop.is_set)
            folder = unpack(path, os.path.join(root, "new"))
            os.remove(path)
            self.done.emit(folder)
        except Cancelled:
            shutil.rmtree(root, ignore_errors=True)
            self.done.emit(Cancelled())
        except Exception as e:
            shutil.rmtree(root, ignore_errors=True)
            self.done.emit(e if isinstance(e, UpdateError) else
                           UpdateError(f"ჩამოტვირთვა ვერ მოხერხდა: {e}"))


class Checker(QObject):
    """Asks in a thread of its own; done(result or None) arrives in the window's thread."""

    done = Signal(object)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            result = latest()
        except Exception:
            result = None
        self.done.emit(result)
