"""Is there a newer ETRATI? Asks GitHub for the latest release; only the version is sent
(the request carries "ETRATI/<version>" and nothing about the person or their files)."""
from __future__ import annotations

import json
import re
import threading

from PySide6.QtCore import QObject, Signal

from . import __version__

LATEST = "https://api.github.com/repos/Mokrinali/Etrati/releases/latest"
PAGE = "https://github.com/Mokrinali/Etrati/releases/latest"


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
    """{"version", "url"} of the newest release, or None when GitHub cannot be reached."""
    import urllib.request
    req = urllib.request.Request(LATEST, headers={"User-Agent": f"ETRATI/{__version__}",
                                                  "Accept": "application/vnd.github+json"})
    with _open(req, timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    tag = data.get("tag_name") or ""
    if not tag:
        return None
    return {"version": tag.lstrip("vV"), "url": data.get("html_url") or PAGE}


def open_page(url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl(url))


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
