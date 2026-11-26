"""Offers ETRATI to Windows as a program for PDF files: the current user only, no administrator.

Windows lets only the person choose the default (Settings), so this registers ETRATI and
opens that page; it never writes the protected choice itself. The same keys as
scripts/register_pdf.ps1.
"""
from __future__ import annotations

import os
import sys
import winreg

PROG = "ETRATI.pdf"
HKCU = winreg.HKEY_CURRENT_USER
SETTINGS_PAGE = "ms-settings:defaultapps?registeredAppUser=ETRATI"


def command() -> tuple[str, str]:
    """(how Windows starts ETRATI with a file, where its icon is)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"', sys.executable
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return (f'"{python}" "{os.path.join(root, "etrati.pyw")}" "%1"',
            os.path.join(root, "furceli", "assets", "furceli.ico"))


def _set(path, name, value):
    with winreg.CreateKey(HKCU, path) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def register(base: str = "Software") -> None:
    open_cmd, icon = command()
    classes = base + r"\Classes"
    _set(classes + "\\" + PROG, "", "PDF (ETRATI)")
    _set(classes + "\\" + PROG + r"\DefaultIcon", "", f'"{icon}",0')
    _set(classes + "\\" + PROG + r"\shell\open\command", "", open_cmd)
    app = classes + "\\" + PROG + r"\Application"
    caps = base + r"\ETRATI\Capabilities"
    for path in (app, caps):
        _set(path, "ApplicationName", "ETRATI")
        _set(path, "ApplicationIcon", f"{icon},0")
        _set(path, "ApplicationDescription", "PDF viewer and editor")
    _set(app, "AppUserModelID", "ETRATI")
    _set(caps + r"\FileAssociations", ".pdf", PROG)
    _set(base + r"\RegisteredApplications", "ETRATI", r"Software\ETRATI\Capabilities")
    _set(classes + r"\.pdf\OpenWithProgids", PROG, "")
    _notify()


def is_default(base: str = "Software") -> bool:
    """Has the person chosen ETRATI for PDF files in Windows?"""
    key = base + r"\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice"
    try:
        with winreg.OpenKey(HKCU, key) as k:
            return winreg.QueryValueEx(k, "ProgId")[0] == PROG
    except OSError:
        return False


def open_settings() -> None:
    os.startfile(SETTINGS_PAGE)


def _notify():
    """Explorer learns about the new program without a restart."""
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


def _delete_tree(hive, path):
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(hive, path + "\\" + sub)
        winreg.DeleteKey(hive, path)
    except FileNotFoundError:
        pass
