"""Finding out about a newer ETRATI on GitHub (only the version number is asked for)."""
import io
import json
import time

from test_ui import app, win  # noqa: F401

from furceli import __version__, updates


def test_versions_compare_as_numbers():
    assert updates.newer("0.10.1", "0.10.0") and updates.newer("v1.0", "0.10.9")
    assert not updates.newer("0.9.12", "0.10.0") and not updates.newer("v0.10.0", "0.10.0")
    assert not updates.newer("", "0.10.0")


def test_latest_reads_the_release(monkeypatch):
    body = json.dumps({"tag_name": "v9.1.0", "html_url": "https://github.com/x/releases/tag/v9.1.0"})
    seen = {}

    def fake(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        return io.BytesIO(body.encode())
    monkeypatch.setattr(updates, "_open", fake)
    got = updates.latest()
    assert got == {"version": "9.1.0", "url": "https://github.com/x/releases/tag/v9.1.0"}
    assert seen["ua"] == f"ETRATI/{__version__}"


def test_a_newer_version_shows_a_banner(win, monkeypatch):  # noqa: F811
    opened = []
    monkeypatch.setattr(updates, "open_page", lambda url: opened.append(url))
    win._update_result({"version": "99.0", "url": "https://example.org/r"}, manual=False)
    b = win.update_banner
    assert not b.isHidden() and "99.0" in b.message()
    b.link.click()
    assert opened == ["https://example.org/r"]


def test_manual_check_says_when_there_is_nothing_new(win, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    win._update_result({"version": __version__, "url": "x"}, manual=True)
    win._update_result(None, manual=True)
    assert len(told) == 2 and win.update_banner.isHidden()
    win._update_result({"version": __version__, "url": "x"}, manual=False)
    assert len(told) == 2  # the quiet daily check says nothing


def test_daily_check_runs_once_a_day(win, monkeypatch):  # noqa: F811
    import sys
    started = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.Checker, "start", lambda self: started.append(1))
    win.check_updates()
    win.check_updates()
    assert started == [1]
    win.settings.setValue("update_checked", int(time.time()) - 2 * 86400)
    win.check_updates()
    assert started == [1, 1]
