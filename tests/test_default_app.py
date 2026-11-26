"""Offering ETRATI as the program for PDF files (current user only, no administrator)."""
import sys
import uuid
import winreg

import pytest

from test_ui import app, win  # noqa: F401

from furceli import default_app


@pytest.fixture
def base():
    """A throw-away place in the registry instead of the real Software key."""
    root = r"Software\ETRATI-test-" + uuid.uuid4().hex[:8]
    yield root
    default_app._delete_tree(winreg.HKEY_CURRENT_USER, root)


def value(path, name=""):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        return winreg.QueryValueEx(k, name)[0]


def test_packed_program_registers_itself(base, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"D:\ETRATI\ETRATI.exe")
    default_app.register(base)
    assert value(base + r"\Classes\ETRATI.pdf\shell\open\command") == r'"D:\ETRATI\ETRATI.exe" "%1"'
    assert value(base + r"\Classes\ETRATI.pdf\DefaultIcon") == r'"D:\ETRATI\ETRATI.exe",0'
    assert value(base + r"\ETRATI\Capabilities\FileAssociations", ".pdf") == "ETRATI.pdf"
    assert value(base + r"\RegisteredApplications", "ETRATI") == r"Software\ETRATI\Capabilities"
    assert value(base + r"\Classes\.pdf\OpenWithProgids", "ETRATI.pdf") == ""


def test_sources_register_pythonw(base, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    default_app.register(base)
    cmd = value(base + r"\Classes\ETRATI.pdf\shell\open\command")
    assert "pythonw.exe" in cmd and "etrati.pyw" in cmd and cmd.endswith('"%1"')


def test_is_default_reads_the_users_choice(base):
    assert default_app.is_default(base) is False
    key = base + r"\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, "ProgId", 0, winreg.REG_SZ, "ETRATI.pdf")
    assert default_app.is_default(base) is True



def test_window_asks_only_once(win, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox
    asked, made = [], []
    monkeypatch.setattr(default_app, "is_default", lambda base="Software": False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(1) or QMessageBox.StandardButton.No)
    monkeypatch.setattr(win, "make_default", lambda: made.append(1))
    win.offer_default()
    win.offer_default()
    assert asked == [1] and made == [] and win.settings.value("asked_default", type=bool)


def test_menu_registers_and_opens_settings(win, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox
    done = []
    monkeypatch.setattr(default_app, "register", lambda base="Software": done.append("register"))
    monkeypatch.setattr(default_app, "is_default", lambda base="Software": False)
    monkeypatch.setattr(default_app, "open_settings", lambda: done.append("settings"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: done.append("told"))
    win.a_default.trigger()
    assert done == ["register", "told", "settings"]
