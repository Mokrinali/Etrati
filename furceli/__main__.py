import multiprocessing
import os
import sys
import time


def take_turn(paths, wait=6.0):
    """(right to be the running program, were the files handed over instead).

    Explorer starts one program per selected file, all at once: the others wait until the
    first one's window can take their files.
    """
    from furceli import single
    end = time.monotonic() + wait
    while True:
        lock = single.claim()
        if lock is not None or time.monotonic() >= end:
            return lock, False
        time.sleep(.25)
        if single.hand_over(paths):
            return None, True


def main() -> int:
    # Imported here: the engine child process re-imports this module and must
    # not load the GUI.
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from furceli import APP_ID, APP_NAME, files, single

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    app = QApplication(sys.argv)
    paths = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if single.hand_over(paths):
        return 0  # the running window opens them as tabs
    lock, given = take_turn(paths)
    if given:
        return 0

    from furceli import theme
    from furceli.window import MainWindow, make_settings

    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    mode = make_settings().value("theme", "light")
    theme.apply_theme(app, mode)
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(__file__), "assets", "furceli.ico")))
    files.sweep_sessions()
    win = MainWindow()

    def follow_system(*_):
        if win.settings.value("theme", "light") == "system":
            theme.apply_theme(app, "system")
            win.repaint_theme()
    app.styleHints().colorSchemeChanged.connect(follow_system)
    receiver = single.Receiver(parent=win)

    def take(new):
        for p in new:
            if os.path.isfile(p):
                win.open_file(p)
        single.bring_to_front(win)
    receiver.arrived.connect(take)
    win.show()
    for path in paths:
        win.open_file(path)
    code = app.exec()
    if lock is not None:
        lock.unlock()
    return code


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
