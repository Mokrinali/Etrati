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
    shutil.copy2(path, out)
    if os.path.getsize(out) != os.path.getsize(path):
        raise OSError("backup is incomplete")
    os.utime(out)  # age counts from the backup, not from the original
    kept = sorted((os.path.join(root, f) for f in os.listdir(root)), key=os.path.getmtime)
    for old in kept[:-KEEP_BACKUPS]:
        try:
            os.remove(old)
        except OSError:
            pass
    return out


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


def put_in_place(tmp: str, target: str) -> str | None:
    """Replace target by tmp, backing up what was there. Returns the backup path."""
    saved = make_backup(target) if os.path.exists(target) else None
    _replace(tmp, target)
    return saved


# -- temporary folders -------------------------------------------------------
MARK = "furceli.session"


def new_session() -> str:
    """A private temporary folder that names its owner, so leftovers can be found."""
    import psutil
    path = tempfile.mkdtemp(prefix="furceli_")
    with open(os.path.join(path, MARK), "w") as f:
        f.write(f"{os.getpid()} {psutil.Process().create_time()}")
    return path


@contextlib.contextmanager
def work_dir():
    path = new_session()
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


def sweep_sessions() -> int:
    """Remove folders left behind by a crashed or killed program. Only marked ones."""
    root, n = tempfile.gettempdir(), 0
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
    return " ".join(text.split()).strip(". ")[:80] or "გვერდები"


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
