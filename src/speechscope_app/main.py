"""Vstupní bod aplikace.

Přepínače: `--fake` použije falešnou knihovnu (jen pro tento běh),
`--smoke` okno jen otevře a hned zavře (kouřový test zabalené aplikace,
návratový kód 0), `--fake-cli ARGS...` nespustí GUI, ale falešné CLI
knihovny (zabalené exe nemá jiný Python, kterým by ho spustilo).

Pojistka: když GUI běží jako podproces sebe sama (značka
`library.CHILD_ENV` v prostředí), skončí hned kódem 2. Bez ní by se
chybně složený příkaz knihovny množil do nekonečna.
"""

from __future__ import annotations

import os
import sys
from importlib import resources

from PySide6.QtCore import QLibraryInfo, QLocale, QTimer, QTranslator
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import __version__, i18n
from .backend.library import CHILD_ENV
from .backend.settings import APP, ORG, AppSettings
from .ui import theme
from .ui.main_window import MainWindow


def app_icon() -> QIcon:
    path = resources.files("speechscope_app") / "assets" / "speechscope.ico"
    return QIcon(str(path))


EXIT_CHILD_GUARD = 2


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--fake-cli":
        from .fake.cli import main as fake_main

        return fake_main(args[1:])
    if os.environ.get(CHILD_ENV):
        print(
            "SpeechScope: GUI bylo spuštěno jako podproces knihovny, to je chyba "
            "v nastavení příkazu knihovny; končím, aby se procesy nemnožily.",
            file=sys.stderr,
        )
        return EXIT_CHILD_GUARD

    app = QApplication(sys.argv)
    app.setOrganizationName(ORG)
    app.setApplicationName(APP)
    app.setApplicationDisplayName("SpeechScope")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    theme.apply(app)

    settings = AppSettings()
    if "--fake" in args:
        settings.fake_override = True
    language = i18n.activate(settings.ui_language)
    translator = QTranslator(app)  # texty samotného Qt: Ano/Ne, Storno, dialogy souborů
    qt_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(QLocale(language), "qtbase", "_", qt_dir):
        app.installTranslator(translator)
    window = MainWindow(settings)
    window.show()
    if "--smoke" in args:
        QTimer.singleShot(500, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
