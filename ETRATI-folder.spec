# PyInstaller recipe for a folder with ETRATI.exe (starts faster than one file):
#   .\.venv\Scripts\pip install -r requirements-build.txt
#   .\.venv\Scripts\pyinstaller --noconfirm --distpath D:\Acrobat\dist --workpath D:\Acrobat\build ETRATI-folder.spec
# Needs data\tessdata first (scripts\get_tessdata.ps1).
from PyInstaller.utils.hooks import collect_all, collect_submodules

import sys

sys.path.insert(0, SPECPATH)  # noqa: F821 (PyInstaller's): furceli and version_info are here
from furceli import __version__  # noqa: E402
from version_info import info  # noqa: E402

datas = [("furceli/assets", "furceli/assets"), ("data/tessdata", "data/tessdata"),
         ("design/tokens.json", "design")]
binaries, hidden = [], collect_submodules("furceli") + collect_submodules("win32com")
for pkg in ("pymupdf", "pyhanko", "pyhanko_certvalidator", "pillow_heif"):
    d, b, h = collect_all(pkg)
    datas, binaries, hidden = datas + d, binaries + b, hidden + h

a = Analysis(
    ["etrati.pyw"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="ETRATI",
    icon="furceli/assets/furceli.ico",
    version=info(__version__),  # Task Manager and Properties say "Etrati"
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="ETRATI")
