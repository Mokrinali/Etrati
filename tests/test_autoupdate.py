"""Installing a newer ETRATI, only after the person agrees."""
import hashlib
import io
import json
import os
import zipfile

import pytest

from test_ui import app, win  # noqa: F401

from furceli import updates


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _release(asset=True, digest=None):
    data = {"tag_name": "v9.1.0", "html_url": "https://github.com/x/releases/tag/v9.1.0",
            "assets": []}
    if asset:
        a = {"name": "ETRATI-9.1.0.zip", "size": 1234,
             "browser_download_url": "https://github.com/x/releases/download/v9.1.0/ETRATI-9.1.0.zip"}
        if digest:
            a["digest"] = digest
        data["assets"] = [{"name": "notes.txt", "browser_download_url": "https://x/n"}, a]
    return data


def test_latest_names_the_zip_to_download(monkeypatch):
    monkeypatch.setattr(updates, "_open", lambda req, timeout: io.BytesIO(
        json.dumps(_release(digest="sha256:" + "ab" * 32)).encode()))
    got = updates.latest()
    assert got["asset"].endswith("/ETRATI-9.1.0.zip")
    assert got["size"] == 1234 and got["sha256"] == "ab" * 32


def test_a_release_without_a_zip_can_only_be_opened_in_the_browser(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates, "_open", lambda req, timeout: io.BytesIO(
        json.dumps(_release(asset=False)).encode()))
    got = updates.latest()
    assert got["asset"] is None and not updates.can_install(got)
    assert updates.can_install({"asset": "https://x/a.zip"})
    monkeypatch.setattr(sys, "frozen", False)
    assert not updates.can_install({"asset": "https://x/a.zip"})  # not from the sources


def test_download_checks_the_digest_and_can_be_cancelled(tmp_path, monkeypatch):
    body = b"x" * 300_000
    monkeypatch.setattr(updates, "_open", lambda req, timeout: io.BytesIO(body))
    seen = []
    dest = tmp_path / "a.zip"
    updates.download("https://x/a.zip", str(dest), hashlib.sha256(body).hexdigest(),
                     progress=lambda done, total: seen.append(done))
    assert dest.read_bytes() == body and seen[-1] == len(body)

    with pytest.raises(updates.UpdateError):
        updates.download("https://x/a.zip", str(dest), "00" * 32)
    assert not dest.exists()  # a damaged file is not left behind

    with pytest.raises(updates.Cancelled):
        updates.download("https://x/a.zip", str(dest), None, cancelled=lambda: True)
    assert not dest.exists()


def test_unpack_finds_the_program_and_refuses_paths_outside(tmp_path):
    good = tmp_path / "good.zip"
    good.write_bytes(_zip({"ETRATI/ETRATI.exe": b"exe", "ETRATI/_internal/a.txt": b"a",
                           "how to.txt": b"read me"}))
    folder = updates.unpack(str(good), str(tmp_path / "new"))
    assert os.path.isfile(os.path.join(folder, "ETRATI.exe"))

    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip({"ETRATI/ETRATI.exe": b"exe", "../evil.txt": b"x"}))
    with pytest.raises(updates.UpdateError):
        updates.unpack(str(bad), str(tmp_path / "new2"))
    assert not (tmp_path / "evil.txt").exists()

    empty = tmp_path / "empty.zip"
    empty.write_bytes(_zip({"readme.txt": b"no program here"}))
    with pytest.raises(updates.UpdateError):
        updates.unpack(str(empty), str(tmp_path / "new3"))


def _install_tree(tmp_path):
    target = tmp_path / "apps" / "ETRATI"
    (target / "_internal").mkdir(parents=True)
    (target / "ETRATI.exe").write_text("old")
    (target / "_internal" / "only-old.txt").write_text("old")
    new = tmp_path / "stage" / "new" / "ETRATI"
    (new / "_internal").mkdir(parents=True)
    (new / "ETRATI.exe").write_text("new")
    return target, new


def test_install_swaps_the_folders_and_keeps_the_old_one_aside(tmp_path, monkeypatch):
    target, new = _install_tree(tmp_path)
    monkeypatch.setattr(updates, "install_target", lambda: str(new))  # as the installer sees it
    started = []
    ok = updates.install(str(new), str(target), pid=0, version="9.1.0",
                         reopen=["C:/a.pdf"], start=lambda cmd: started.append(cmd))
    assert ok
    assert (target / "ETRATI.exe").read_text() == "new"
    assert not (target / "_internal" / "only-old.txt").exists()
    assert (tmp_path / "apps" / "ETRATI.old" / "ETRATI.exe").read_text() == "old"
    assert started == [[str(target / "ETRATI.exe"), "C:/a.pdf"]]
    assert updates.read_result(str(target)) == {"ok": True, "version": "9.1.0"}


def test_a_failed_copy_puts_the_old_program_back(tmp_path, monkeypatch):
    target, new = _install_tree(tmp_path)

    def broken(src, dst, **kw):
        os.makedirs(dst)
        open(os.path.join(dst, "half.txt"), "w").close()
        raise OSError("disk full")
    monkeypatch.setattr(updates.shutil, "copytree", broken)
    started = []
    assert not updates.install(str(new), str(target), pid=0, version="9.1.0",
                               start=lambda cmd: started.append(cmd))
    assert (target / "ETRATI.exe").read_text() == "old"
    assert (target / "_internal" / "only-old.txt").exists()
    assert not (tmp_path / "apps" / "ETRATI.old").exists()
    assert started == [[str(target / "ETRATI.exe")]]  # the old one opens again
    res = updates.read_result(str(target))
    assert res["ok"] is False and "disk full" in res["error"]


def test_tidy_removes_the_leftovers_and_tells_the_result(tmp_path, monkeypatch):
    target, new = _install_tree(tmp_path)
    updates.install(str(new), str(target), pid=0, version="9.1.0", start=lambda cmd: None)
    got = updates.tidy(str(target))
    assert got == {"ok": True, "version": "9.1.0"}
    assert not (tmp_path / "apps" / "ETRATI.old").exists()
    assert not (tmp_path / "apps" / "ETRATI.update").exists()
    assert updates.tidy(str(target)) is None  # told once


def test_the_banner_offers_to_install_only_with_a_zip(win, monkeypatch):  # noqa: F811
    monkeypatch.setattr(updates, "can_install", lambda r: bool(r.get("asset")))
    win._update_result({"version": "99.0", "url": "https://x/r", "asset": "https://x/a.zip",
                        "size": 10, "sha256": None}, manual=False)
    assert win.update_banner.link.text() == "განახლება…"
    win._update_result({"version": "99.0", "url": "https://x/r", "asset": None}, manual=False)
    assert win.update_banner.link.text() == "ჩამოტვირთვა…"


def test_nothing_is_downloaded_without_consent(win, monkeypatch):  # noqa: F811
    asked, fetched = [], []
    monkeypatch.setattr(win, "_confirm_update", lambda r: asked.append(r) or False)
    monkeypatch.setattr(updates.Downloader, "start", lambda self, *a: fetched.append(a))
    win.start_update({"version": "99.0", "asset": "https://x/a.zip", "size": 10, "sha256": None})
    assert len(asked) == 1 and fetched == []


def test_a_ready_update_waits_for_the_window_to_close(win, monkeypatch):  # noqa: F811
    launched = []
    monkeypatch.setattr(updates, "start_installer",
                        lambda new, target, pid, version, reopen: launched.append((new, version)))
    monkeypatch.setattr(updates, "install_target", lambda: "C:/apps/ETRATI")
    refuse = {"close": True}
    monkeypatch.setattr(win, "_docs", lambda: [object()])  # one document with unsaved changes
    monkeypatch.setattr(win, "_may_close", lambda doc: not refuse["close"])
    win._ready_update("C:/stage/new/ETRATI", "99.0")  # the person says "cancel" to closing
    assert win.isVisible() and launched == []
    assert "99.0" in win.update_banner.message()
    refuse["close"] = False
    win.update_banner.link.click()  # "install" on the strip closes again
    assert launched == [("C:/stage/new/ETRATI", "99.0")]


def test_the_next_start_tells_how_it_went(win, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(updates, "install_target", lambda: str(tmp_path / "apps" / "ETRATI"))
    monkeypatch.setattr(updates, "tidy", lambda target: None)  # tidy has its own test
    updates._write_result({"ok": False, "version": "99.0", "error": "access denied"})
    win.after_update()
    assert "access denied" in win.update_banner.message()
    assert win.update_banner.kind == "danger"
    updates._write_result({"ok": True, "version": "99.0"})
    win.after_update()
    assert "99.0" in win.update_banner.message() and win.update_banner.kind == "success"


def test_a_busy_folder_is_explained_in_words(tmp_path, monkeypatch):
    target, new = _install_tree(tmp_path)

    def busy(a, b):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(updates.os, "rename", busy)
    monkeypatch.setattr(updates, "_retry", lambda fn, seconds=20: fn())
    assert not updates.install(str(new), str(target), pid=0, version="9.1.0", start=lambda c: None)
    assert "სხვა პროგრამა იყენებს" in updates.read_result(str(target))["error"]
    assert (target / "ETRATI.exe").read_text() == "old"
