"""Vstupní bod aplikace.

Přepínače: `--fake` použije falešnou knihovnu, `--smoke` okno jen otevře
a hned zavře (kouřový test zabalené aplikace, návratový kód 0).
"""

from __future__ import annotations

import sys
from importlib import resources

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import __version__
from .backend.settings import APP, ORG, AppSettings
from .ui import theme
from .ui.main_window import MainWindow


def app_icon() -> QIcon:
    path = resources.files("speechscope_app") / "assets" / "speechscope.ico"
    return QIcon(str(path))


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(ORG)
    app.setApplicationName(APP)
    app.setApplicationDisplayName("SpeechScope")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    theme.apply(app)

    settings = AppSettings()
    if "--fake" in sys.argv:
        settings.use_fake_library = True
    window = MainWindow(settings)
    window.show()
    if "--smoke" in sys.argv:
        QTimer.singleShot(500, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
