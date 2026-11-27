"""File safety: backups, unique names, page range text."""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
import time

KEEP_BACKUPS = 50


def backup_dir() -> str:
    root = os.environ.get("FURCELI_BACKUPS") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Furceli", "backups")
    os.makedirs(root, exist_ok=True)
    return root


def make_backup(path: str) -> str:
    """Copy a file that is about to be replaced. Raises OSError when it cannot."""
    root = backup_dir()
    stem, ext = os.path.splitext(os.path.basename(path))
    out = unique_path(os.path.join(root, f"{stem} {time.strftime('%Y-%m-%d %H-%M-%S')}{ext}"))
    try:
        shutil.copy2(path, out)
        if os.path.getsize(out) != os.path.getsize(path):
            raise OSError("backup is incomplete")
        _flush(out)
        os.utime(out)  # age counts from the backup, not from the original
    except OSError:  # half a copy must not count as one of the kept backups
        with contextlib.suppress(OSError):
            os.remove(out)
        raise
    kept = []
    with contextlib.suppress(OSError):
        for entry in os.scandir(root):
            with contextlib.suppress(OSError):  # somebody else may tidy up as well
                if entry.is_file():
                    kept.append((entry.stat().st_mtime, entry.path))
    for _, old in sorted(kept)[:-KEEP_BACKUPS]:
        with contextlib.suppress(OSError):
            os.remove(old)
    return out


def _flush(path: str):
    """Make Windows write the file to the disk now: a power cut must not leave it empty."""
    with contextlib.suppress(OSError):  # read-only files and some network drives refuse
        with open(path, "rb+") as f:
            os.fsync(f.fileno())


def unique_path(path: str) -> str:
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{stem} ({n}){ext}"
        n += 1
    return path


def temp_name(target: str) -> str:
    """A temporary file next to the target, so the final rename is atomic."""
    d, name = os.path.split(os.path.abspath(target))
    return unique_path(os.path.join(d, f".{name}.furceli.tmp"))


def _replace(tmp: str, target: str):
    """Swap the file in; an existing one keeps its access rights and attributes."""
    if os.name == "nt" and os.path.exists(target):
        import ctypes
        fn = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
        fn.argtypes = [ctypes.c_wchar_p] * 3 + [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
        # 2|4: do not fail when rights or attributes cannot be merged
        if fn(os.path.abspath(target), os.path.abspath(tmp), None, 2 | 4, None, None):
            return
        if not os.path.exists(tmp):
            raise OSError("the new file disappeared while replacing")
    os.replace(tmp, target)


def discard(path: str):
    """Remove a temporary file; one that cannot be removed must not hide the real outcome."""
    with contextlib.suppress(OSError):
        if os.path.exists(path):
            os.remove(path)


def _near(tmp: str, target: str) -> str:
    """tmp as a file in the target's folder: only there can it be renamed into place.

    The engine writes into its own folder, often on another drive than the target.
    """
    if os.path.dirname(os.path.abspath(tmp)) == os.path.dirname(os.path.abspath(target)):
        return tmp
    near = temp_name(target)
    try:
        shutil.copyfile(tmp, near)
        _flush(near)
    except BaseException:
        discard(near)
        raise
    return near


def _tidy(tmp: str):
    """The engine's file and its private folder go once they are delivered."""
    discard(tmp)
    folder = os.path.dirname(os.path.abspath(tmp))
    if _root and os.path.dirname(folder) == os.path.abspath(_root):
        with contextlib.suppress(OSError):
            os.rmdir(folder)


def put_in_place(tmp: str, target: str) -> str | None:
    """Replace target by tmp, backing up what was there. Returns the backup path."""
    saved = make_backup(target) if os.path.exists(target) else None
    _flush(tmp)
    near = _near(tmp, target)
    try:
        _replace(near, target)
    except BaseException:
        if near != tmp:
            discard(near)
        raise
    if near != tmp:
        _tidy(tmp)
    return saved


def deliver_all(pairs: list) -> None:
    """New files [(engine file, target), ...]: all of them or none; never over another file."""
    done = []
    try:
        for tmp, target in pairs:
            near = _near(tmp, target)
            try:
                os.rename(near, target)  # refuses a name taken meanwhile
            except BaseException:
                if near != tmp:
                    discard(near)
                raise
            done.append(target)
    except BaseException:
        for target in done:
            discard(target)
        raise
    for tmp, _ in pairs:
        _tidy(tmp)


# -- the engine's own folder -------------------------------------------------
_root: str | None = None


def engine_root() -> str:
    """The one folder engines of this window may write to. The window puts what they wrote
    in its place, so a PDF that takes over an engine cannot reach the user's files this way.
    """
    global _root
    if _root is None or not os.path.isdir(_root):
        # Under %LOCALAPPDATA%Low: an engine of low integrity may write only there.
        _root = new_session(_engine_base())
    return _root


def engine_root_is_low() -> bool:
    """Is the engine folder where an engine of low integrity may write?"""
    base = _engine_base()
    root = os.path.normcase(os.path.abspath(engine_root()))
    return bool(base) and os.path.dirname(root) == os.path.normcase(os.path.abspath(base))


def outbox_dir() -> str:
    """A folder of its own inside the engine's folder, for working files of one task; named
    after this process (an engine's goes when that engine dies)."""
    return new_session(engine_root())


def drop_engine_root():
    """When the window closes: what its engines wrote is not needed any more."""
    global _root
    root, _root = _root, None
    if root:
        shutil.rmtree(root, ignore_errors=True)


def outbox(target: str) -> str:
    """Where an engine writes what is meant for target: a folder of its own for every file,
    so that two files of the same name never meet."""
    return os.path.join(tempfile.mkdtemp(dir=engine_root()), os.path.basename(target) or "file")


# -- temporary folders -------------------------------------------------------
MARK = "furceli.session"


def new_session(base: str | None = None) -> str:
    """A private temporary folder that names its owner, so leftovers can be found."""
    path = tempfile.mkdtemp(prefix="furceli_", dir=base)
    claim(path, os.getpid())
    return path


def claim(path: str, pid: int):
    """Name the process a folder belongs to: it goes once that process is gone."""
    import psutil
    mark = os.path.join(path, MARK)
    with open(mark + ".new", "w") as f:
        f.write(f"{pid} {psutil.Process(pid).create_time()}")
    os.replace(mark + ".new", mark)  # a half-written mark would look like a dead owner's


@contextlib.contextmanager
def work_dir():
    # Inside the engine's folder when there is one: the helper only takes files from there.
    # Named after this process, so what a killed engine left is found and removed.
    path = new_session(_root if _root and os.path.isdir(_root) else None)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _owner_alive(mark: str) -> bool:
    import psutil
    try:
        with open(mark) as f:
            pid, started = f.read().split()
        return abs(psutil.Process(int(pid)).create_time() - float(started)) < 2
    except (psutil.NoSuchProcess, ValueError):
        return False
    except (OSError, psutil.Error):
        return True  # cannot tell: leave it alone


def _engine_base() -> str | None:
    if os.name != "nt":
        return None
    from . import sandbox
    try:
        return sandbox.engine_base()
    except OSError:
        return None


def sweep_sessions() -> int:
    """Remove folders left behind by a crashed or killed program. Only marked ones."""
    n = 0
    mine = _root if _root and os.path.isdir(_root) else None  # what our dead engines left there
    for root in {tempfile.gettempdir(), _engine_base(), mine} - {None}:
        with contextlib.suppress(OSError):
            for name in os.listdir(root):
                mark = os.path.join(root, name, MARK)
                if name.startswith("furceli_") and os.path.isfile(mark) and not _owner_alive(mark):
                    shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                    n += not os.path.exists(mark)
    return n


def drop_stale_temps(target: str):
    """Unfinished saves of this very file, left by a power cut."""
    d, name = os.path.split(os.path.abspath(target))
    for f in os.listdir(d):
        if re.fullmatch(re.escape(f".{name}.furceli") + r"( \(\d+\))?\.tmp", f):
            path = os.path.join(d, f)
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(path) > 3600:
                    os.remove(path)


def from_internet(path) -> bool:
    """Windows marks downloaded files; Office would open them in Protected View."""
    try:
        with open(path + ":Zone.Identifier", encoding="utf-8", errors="replace") as f:
            m = re.search(r"ZoneId\s*=\s*(\d)", f.read())
        return bool(m) and int(m.group(1)) >= 3
    except OSError:
        return False


def safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = " ".join(text.split()).strip(". ")[:80].strip(". ") or "გვერდები"
    if re.fullmatch(r"(con|prn|aux|nul|com\d|lpt\d)(\..*)?", text, re.I):
        text = "_" + text  # Windows keeps these names for devices
    return text


def parse_group(text: str, n: int) -> list[int]:
    """'1-3, 7, 10-' -> zero-based pages. Raises ValueError with a message."""
    pages = []
    for part in re.split(r"[,;]", text):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*[-–—]\s*(\d*)|(\d+)", part)
        if not m or part in "-–—":
            raise ValueError(f"გაუგებარია: „{part}“. მაგალითი: 1-3, 7, 10-")
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a, b = int(m.group(1) or 1), int(m.group(2) or n)
        if not (1 <= a <= n and 1 <= b <= n):
            raise ValueError(f"„{part}“: ფაილში მხოლოდ {n} გვერდია")
        pages += list(range(a - 1, b)) if a <= b else list(range(a - 1, b - 2, -1))
    if not pages:
        raise ValueError("მიუთითეთ გვერდები. მაგალითი: 1-3, 7, 10-")
    return pages


def parse_parts(text: str, n: int) -> list[list[int]]:
    """Split ranges: every comma-separated item becomes its own file."""
    return [parse_group(p, n) for p in re.split(r"[,;]", text) if p.strip()] or parse_group("", n)


def describe(pages: list[int]) -> str:
    """Zero-based pages -> '1-3, 7'."""
    out, i = [], 0
    pages = sorted(pages)
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i] + 1) if i == j else f"{pages[i] + 1}-{pages[j] + 1}")
        i = j + 1
    return ", ".join(out)
