"""Otevření nahrávky a její složky ze seznamů souborů (Analýza, Výpočet, Výsledky).

Dvojklik otevře soubor v programu, který mu Windows přiřadily (přehrávač),
pravé tlačítko nabídne totéž plus ukázání ve složce a kopii cesty. Bez
dialogů; když soubor mezitím zmizel, jde krátká hláška do stavového řádku
(`notify`).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from ..i18n import tr

Notify = Callable[[str], None]


def open_file(path: Path, notify: Notify | None = None) -> bool:
    """Otevře soubor přiřazeným programem; False, když neexistuje."""
    if not path.is_file():
        if notify:
            notify(tr("Soubor už neexistuje: {path}").format(path=path))
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def reveal_file(path: Path, notify: Notify | None = None) -> bool:
    """Průzkumník s vybraným souborem (jinde aspoň otevře složku)."""
    if not path.exists():
        if notify:
            notify(tr("Soubor už neexistuje: {path}").format(path=path))
        return False
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603, S607
        return True
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def copy_path(path: Path) -> None:
    QApplication.clipboard().setText(str(path))


def file_menu(parent: QWidget, path: Path, notify: Notify | None = None) -> QMenu:
    """Nabídka pro jeden soubor: přehrát, ukázat ve složce, kopírovat cestu."""
    menu = QMenu(parent)
    play = menu.addAction(tr("Přehrát nahrávku"))
    play.triggered.connect(lambda: open_file(path, notify))
    reveal = menu.addAction(tr("Zobrazit ve složce"))
    reveal.triggered.connect(lambda: reveal_file(path, notify))
    menu.addSeparator()
    copy = menu.addAction(tr("Kopírovat cestu"))
    copy.triggered.connect(lambda: copy_path(path))
    return menu


def show_file_menu(parent: QWidget, path: Path, pos: QPoint, notify: Notify | None = None) -> None:
    file_menu(parent, path, notify).exec(pos)
