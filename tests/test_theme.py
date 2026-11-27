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
    a.setStyle("Fusion")  # another style set later is replaced by the next theme
    theme.apply_theme(a, "light")
    img = a.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(36, 36).toImage()
    assert img.pixelColor(img.width() // 2, 3).alpha() > 200


def _footer_of(dlg):
    from PySide6.QtWidgets import QApplication, QFrame
    dlg.show()
    for _ in range(5):
        QApplication.processEvents()
    return dlg.findChild(QFrame, "dialogFooter")


def test_the_buttons_of_a_dialog_sit_on_a_band_of_their_own(app):  # noqa: F811
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
    theme.apply_theme(app, "light")
    dlg = QDialog()
    lay = QVBoxLayout(dlg)
    text = QLabel("ტექსტი")
    lay.addWidget(text)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    lay.addWidget(box)
    dlg.resize(420, 200)
    band = _footer_of(dlg)
    assert band is not None and band.isVisible()
    g = band.geometry()
    assert g.left() == 0 and g.width() == dlg.width() and g.bottom() == dlg.height() - 1
    assert text.geometry().bottom() < g.top() <= box.geometry().top()  # the whole row, nothing above
    img = dlg.grab().toImage()
    assert img.pixelColor(3, dlg.height() - 3).name() == theme.color("surface-200").name()
    assert img.pixelColor(3, 3).name() == theme.color("surface-100").name()
    dlg.resize(500, 260)  # follows the dialog
    _footer_of(dlg)
    assert band.geometry().width() == 500 and band.geometry().bottom() == 259
    dlg.close()


def test_message_boxes_have_the_band_too(app):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox
    theme.apply_theme(app, "light")
    box = QMessageBox(QMessageBox.Icon.Question, "კითხვა", "გავაგრძელო?")
    box.addButton("კი", QMessageBox.ButtonRole.AcceptRole)
    band = _footer_of(box)
    assert band is not None and band.geometry().bottom() == box.height() - 1
    box.close()


def test_a_dialog_without_buttons_has_no_band(app):  # noqa: F811
    from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
    theme.apply_theme(app, "light")
    dlg = QDialog()
    QVBoxLayout(dlg).addWidget(QLabel("მხოლოდ ტექსტი"))
    assert _footer_of(dlg) is None or not _footer_of(dlg).isVisible()
    dlg.close()


def test_the_primary_button_of_a_message_box_is_filled(app):  # noqa: F811
    from PySide6.QtWidgets import QApplication, QMessageBox
    theme.apply_theme(app, "light")
    box = QMessageBox(QMessageBox.Icon.Question, "კითხვა", "გავაგრძელო?")
    yes = theme.variant(box.addButton("კი", QMessageBox.ButtonRole.AcceptRole), "primary")
    box.addButton("არა", QMessageBox.ButtonRole.RejectRole)
    box.show()
    for _ in range(5):
        QApplication.processEvents()
    img = yes.grab().toImage()
    assert img.pixelColor(img.width() // 2, 4).name() == theme.color("brand").name()
    box.close()
