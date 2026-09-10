"""Vstupní bod aplikace."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .backend.settings import APP, ORG, AppSettings
from .ui import theme
from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(ORG)
    app.setApplicationName(APP)
    app.setApplicationDisplayName("SpeechScope")
    app.setApplicationVersion(__version__)
    theme.apply(app)

    settings = AppSettings()
    if "--fake" in sys.argv:
        settings.use_fake_library = True
    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
