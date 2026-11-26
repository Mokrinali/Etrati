"""Colours, sizes and fonts of the design system (design/tokens.json), in two themes.

New interface code takes its colours from color() and its sizes from size(), never from
QColor("#..."). Colours written into a PDF (annotations, signatures, backgrounds) do not
change with the theme: those tokens have the same value in both.
"""
from __future__ import annotations

import json
import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS = os.path.join(ROOT, "design", "tokens.json")
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
MODES = ("light", "dark", "system")
FAMILIES = ["Noto Sans Georgian", "Noto Sans", "Segoe UI", "Sylfaen"]
MONO = ["Cascadia Mono", "Consolas"]

_colors: dict[str, dict[str, str]] = {}
_sizes: dict[str, int] = {}
_now = "light"
_fonts_loaded = False


def _load():
    if _colors:
        return
    with open(TOKENS, encoding="utf-8") as f:
        t = json.load(f)
    for tok in t["color"]["tokens"]:
        _colors[tok["name"]] = tok["value"]
    for group in ("spacing", "radius", "size"):
        for tok in t[group]["tokens"]:
            _sizes[tok["name"]] = int(re.match(r"\d+", tok["value"]).group())


def _parse(v: str) -> QColor:
    m = re.fullmatch(r"rgba\(([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)\)", v.strip())
    if m:
        r, g, b, a = (float(x) for x in m.groups())
        return QColor(int(r), int(g), int(b), round(a * 255))
    return QColor(v)


def _css(v: str) -> str:
    """A value QSS understands: rgba with alpha 0..255."""
    c = _parse(v)
    return c.name() if c.alpha() == 255 else f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def current() -> str:
    """The theme on screen now: "light" or "dark"."""
    return _now


def color(name: str) -> QColor:
    _load()
    return _parse(_colors[name][_now])


def size(name: str) -> int:
    _load()
    return _sizes[name]


def resolve(mode: str) -> str:
    if mode == "system":
        hints = QGuiApplication.styleHints()
        return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"
    return mode if mode in ("light", "dark") else "light"


def load_fonts(app) -> None:
    global _fonts_loaded
    if not _fonts_loaded:
        folder = os.path.join(ASSETS, "fonts")
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
            if name.lower().endswith(".ttf"):
                QFontDatabase.addApplicationFont(os.path.join(folder, name))
        _fonts_loaded = True
    f = QFont()
    f.setFamilies(FAMILIES)
    f.setPixelSize(13)
    app.setFont(f)


def mono_font(px: int = 12) -> QFont:
    f = QFont()
    f.setFamilies(MONO)
    f.setPixelSize(px)
    return f


def palette() -> QPalette:
    R, G = QPalette.ColorRole, QPalette.ColorGroup
    p = QPalette()
    for role, tok in ((R.Window, "surface-100"), (R.Base, "surface-raised"),
                      (R.AlternateBase, "surface-200"), (R.Mid, "canvas"),
                      (R.Dark, "line-strong"), (R.Shadow, "line"), (R.Text, "ink"),
                      (R.WindowText, "ink"), (R.ButtonText, "ink"), (R.BrightText, "on-brand"),
                      (R.PlaceholderText, "ink-muted"), (R.Button, "surface-raised"),
                      (R.Light, "surface-raised"), (R.Midlight, "surface-200"),
                      (R.Highlight, "brand"), (R.HighlightedText, "on-brand"),
                      (R.Link, "brand-text"), (R.ToolTipBase, "ink"),
                      (R.ToolTipText, "surface-100")):
        p.setColor(role, color(tok))
    for role in (R.Text, R.WindowText, R.ButtonText):
        p.setColor(G.Disabled, role, color("ink-disabled"))
    return p


def style_sheet() -> str:
    _load()
    with open(os.path.join(ASSETS, "etrati.qss"), encoding="utf-8") as f:
        qss = f.read()
    values = {k: _css(v[_now]) for k, v in _colors.items()}
    values.update({k: f"{v}px" for k, v in _sizes.items()})
    values["check"] = os.path.join(ASSETS, "check.svg").replace("\\", "/")
    close = os.path.join(ASSETS, f"close-{_now}.svg")
    values["close"] = close.replace("\\", "/")
    values["chevron"] = os.path.join(ASSETS, f"chevron-{_now}.svg").replace("\\", "/")
    return re.sub(r"\{([a-z0-9-]+)\}", lambda m: values.get(m.group(1), m.group(0)), qss)


def apply_theme(app, mode: str = "light") -> str:
    """Puts a theme on the whole program; returns the one now on screen."""
    global _now
    _load()
    _now = resolve(mode)
    load_fonts(app)
    app.setPalette(palette())
    app.setStyleSheet(style_sheet())
    return _now


def icon(name: str) -> "QIcon":
    """An icon of assets/icons (design/icons-v2): ink, red while checked, faint when off."""
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    with open(os.path.join(ASSETS, "icons", name + ".svg"), encoding="utf-8") as f:
        svg = f.read()
    out = QIcon()
    for tok, mode, state in (("ink", QIcon.Mode.Normal, QIcon.State.Off),
                             ("brand-text", QIcon.Mode.Normal, QIcon.State.On),
                             ("ink-disabled", QIcon.Mode.Disabled, QIcon.State.Off),
                             ("ink-disabled", QIcon.Mode.Disabled, QIcon.State.On)):
        r = QSvgRenderer(QByteArray(svg.replace("currentColor", color(tok).name()).encode()))
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(p, QRectF(0, 0, 64, 64))
        p.end()
        out.addPixmap(pix, mode, state)
    return out


def form_layout(parent=None):
    """A form whose labels stand above the fields: Georgian labels are long
    (design/components/FormControls)."""
    from PySide6.QtWidgets import QFormLayout
    form = QFormLayout(parent) if parent is not None else QFormLayout()
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setVerticalSpacing(6)
    return form


def variant(button, kind: str):
    """primary, ghost or danger: how a push button looks (design/components/Button)."""
    button.setProperty("variant", kind)
    return button
