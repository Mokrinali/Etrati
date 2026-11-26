"""Design tokens and the two themes."""
import os
import re

from test_ui import app, win  # noqa: F401

from furceli import theme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_both_themes_and_back(win):  # noqa: F811
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication
    try:
        win.set_theme("dark")
        assert theme.current() == "dark"
        assert QApplication.instance().palette().color(QPalette.ColorRole.Window).name() == "#1c1a18"
        assert theme.color("paper").name() == "#ffffff"  # pages stay white
        assert theme.color("mark-highlight").name() == "#fff07a"  # marks too
        assert win.settings.value("theme") == "dark"
        assert win.theme_actions["dark"].isChecked()
    finally:
        win.set_theme("light")
    assert theme.color("surface-100").name() == "#faf8f4"
    assert not re.search(r"{[a-z0-9-]+}", theme.style_sheet())  # every token was replaced


def test_tokens_and_sizes():
    assert theme.size("control") == 32 and theme.size("radius-sm") == 6
    c = theme.color("mark-crop-fill")
    assert (c.red(), c.green(), c.blue()) == (47, 125, 225) and 25 <= c.alpha() <= 30


def test_no_hard_coded_screen_colours():
    """New screen colours come from the theme; only colours written into files stay."""
    found = []
    for name in os.listdir(os.path.join(ROOT, "furceli")):
        if name.endswith(".py") and name != "theme.py":
            with open(os.path.join(ROOT, "furceli", name), encoding="utf-8") as f:
                found += [(name, m) for m in re.findall(r'QColor\("#[0-9a-fA-F]+"\)', f.read())]
    assert found == [("window.py", 'QColor("#fffbe8")')]  # the page background written into the file


def test_message_icons_in_coloured_circles(app):  # noqa: F811
    """design/components/Dialog: warning, danger and info icons on their soft colours."""
    from PySide6.QtWidgets import QApplication, QStyle
    a = QApplication.instance()
    theme.apply_theme(a, "light")
    style = a.style()
    for sp, soft in ((QStyle.StandardPixmap.SP_MessageBoxWarning, "warning-soft"),
                     (QStyle.StandardPixmap.SP_MessageBoxCritical, "danger-soft"),
                     (QStyle.StandardPixmap.SP_MessageBoxInformation, "info-soft"),
                     (QStyle.StandardPixmap.SP_MessageBoxQuestion, "info-soft")):
        img = style.standardIcon(sp).pixmap(36, 36).toImage()
        edge = img.pixelColor(img.width() // 2, 3)  # just inside the circle, above the sign
        want = theme.color(soft)
        assert abs(edge.red() - want.red()) < 12 and abs(edge.blue() - want.blue()) < 12, sp
    first = a.style()
    theme.apply_theme(a, "light")  # stays one style, not wrapped again
    assert a.property("etrati_style") and a.style() is first
