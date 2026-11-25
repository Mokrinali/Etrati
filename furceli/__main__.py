import multiprocessing
import os
import sys


def main() -> int:
    # Imported here: the engine child process re-imports this module and must
    # not load the GUI.
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from furceli import APP_ID, APP_NAME, files
    from furceli.window import MainWindow

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(__file__), "assets", "furceli.ico")))
    files.sweep_sessions()
    win = MainWindow()
    win.show()
    for arg in sys.argv[1:]:
        if os.path.isfile(arg):
            win.open_file(arg)
    return app.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
