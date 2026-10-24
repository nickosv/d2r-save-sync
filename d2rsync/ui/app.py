"""Application entry for the graphical window."""

from __future__ import annotations

import sys

from d2rsync.ui.theme import STYLESHEET
from d2rsync.ui.window import MainWindow


def prepare(app) -> None:
    from PySide6.QtGui import QFont, QFontDatabase

    app.setApplicationName("D2R Save Sync")
    app.setStyle("Fusion")
    families = set(QFontDatabase.families())
    if "Liberation Sans" in families:
        app.setFont(QFont("Liberation Sans", 12))
    elif "Segoe UI" in families:
        app.setFont(QFont("Segoe UI", 12))
    app.setStyleSheet(STYLESHEET)


def run() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    if sys.platform.startswith("linux"):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication(sys.argv)
    prepare(app)
    window = MainWindow()
    window.show()
    return app.exec()
