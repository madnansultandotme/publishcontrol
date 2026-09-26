#!/usr/bin/env python3
import sys
from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox
from app.ui import MainWindow


def main():
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('PublishControl')
    app = QApplication(sys.argv)
    app.setApplicationName('PublishControl')
    asset_name = 'logo.ico' if sys.platform == 'win32' else 'logo.svg'
    app.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets' / asset_name)))
    app.setOrganizationName('PublishControl')
    try:
        window = MainWindow()
    except Exception as exc:
        QMessageBox.critical(None, 'Unable to start PublishControl', str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
