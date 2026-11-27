"""The program is called "Etrati" wherever a person reads it; identifiers keep "ETRATI"
(file names, registry keys, keys inside PDFs, the update's file names) so nothing that
exists stops working."""
import re
import pathlib

from furceli import APP_ID, APP_NAME, DATA_ID

ROOT = pathlib.Path(__file__).resolve().parent.parent
# "ETRATI" as a word a person reads: not part of a file name (ETRATI.exe, ETRATI-0.1.zip,
# ETRATI-selftest), a path (\ETRATI\), a registry key or a PDF key (ETRATIscale)
# (after "\n" in source: a new line of a message)
SHOWN = re.compile(r"(?:(?<![\w\\/.])|(?<=\\n))ETRATI(?![\w\\/.]|-[A-Za-z0-9])")


def test_the_name_and_what_stays():
    assert APP_NAME == "Etrati"
    assert APP_ID == "ETRATI"      # Windows' id of the program: pinned taskbar icons, registry
    assert DATA_ID == "Furceli"    # settings and backups stay where they are


def test_no_message_says_etrati_in_capitals():
    for name in ("window.py", "updates.py", "tools.py", "scan.py"):
        for n, line in enumerate((ROOT / "furceli" / name).read_text(encoding="utf8").splitlines(), 1):
            if line.lstrip().startswith("#") or "design canvas" in line or "User-Agent" in line:
                continue
            code = line.split('"""')[0] if '"""' not in line.strip()[:3] else ""
            for s in re.findall(r'"([^"]*)"', code):
                assert not SHOWN.search(s), f"{name}:{n}: {s}"


def test_windows_shows_the_new_name_under_the_same_keys():
    src = (ROOT / "furceli" / "default_app.py").read_text(encoding="utf8")
    assert '"PDF (Etrati)"' in src and '"ApplicationName", "Etrati"' in src
    assert 'PROG = "ETRATI.pdf"' in src and r'Software\ETRATI\Capabilities' in src


def test_pdfs_made_say_etrati():
    assert '"producer": "Etrati", "creator": "Etrati"' in (ROOT / "furceli" / "scan.py").read_text(encoding="utf8")
    assert 'subject="Etrati measure"' in (ROOT / "furceli" / "features.py").read_text(encoding="utf8")


def test_the_exe_describes_itself_as_etrati_but_keeps_its_file_name():
    spec = (ROOT / "ETRATI-folder.spec").read_text(encoding="utf8")
    assert 'name="ETRATI"' in spec and "version=" in spec
    info = (ROOT / "version_info.py").read_text(encoding="utf8")
    assert "'FileDescription', 'Etrati'" in info and "'ProductName', 'Etrati'" in info
    assert "'OriginalFilename', 'ETRATI.exe'" in info
