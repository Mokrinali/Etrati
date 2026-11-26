# The same recipe with scripts/exe_selftest.py as start script: checks a packed program from inside.
#   .\.venv\Scripts\pip install -r requirements-build.txt
#   .\.venv\Scripts\pyinstaller --noconfirm --distpath D:\Acrobat\dist --workpath D:\Acrobat\build ETRATI-selftest.spec
# Needs data\tessdata first (scripts\get_tessdata.ps1).
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("furceli/assets", "furceli/assets"), ("data/tessdata", "data/tessdata"),
         ("design/tokens.json", "design")]
binaries, hidden = [], collect_submodules("furceli") + collect_submodules("win32com")
for pkg in ("pymupdf", "pyhanko", "pyhanko_certvalidator", "pillow_heif"):
    d, b, h = collect_all(pkg)
    datas, binaries, hidden = datas + d, binaries + b, hidden + h

a = Analysis(
    ["scripts/exe_selftest.py"],
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
    name="ETRATI-selftest",
    icon="furceli/assets/furceli.ico",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="ETRATI-selftest")
