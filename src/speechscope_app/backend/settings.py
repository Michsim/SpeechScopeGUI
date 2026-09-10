"""Uživatelské nastavení aplikace přes QSettings.

Na Windows končí v registru pod HKCU\\Software\\SAMI\\SpeechScopeApp.
Protokoly se drží jako soubory zvlášť (viz `protocol.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

from . import library

ORG = "SAMI"
APP = "SpeechScopeApp"


def documents_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(base or Path.home())


def app_data_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base or (Path.home() / ".speechscope-app"))


class AppSettings:
    def __init__(self) -> None:
        self._q = QSettings(ORG, APP)

    # --- knihovna -------------------------------------------------------------

    @property
    def library_command(self) -> list[str] | None:
        raw = self._q.value("library/command", "")
        if raw:
            try:
                value = json.loads(raw)
                if isinstance(value, list) and value:
                    return [str(v) for v in value]
            except json.JSONDecodeError:
                pass
        return library.find_default_command()

    @library_command.setter
    def library_command(self, value: list[str] | None) -> None:
        self._q.setValue("library/command", json.dumps(value) if value else "")

    @property
    def use_fake_library(self) -> bool:
        return self._q.value("library/use_fake", False, type=bool)

    @use_fake_library.setter
    def use_fake_library(self, value: bool) -> None:
        self._q.setValue("library/use_fake", bool(value))

    def effective_command(self) -> list[str] | None:
        if self.use_fake_library:
            return library.fake_command()
        return self.library_command

    @property
    def models_dir(self) -> Path:
        raw = self._q.value("library/models_dir", "")
        return Path(raw) if raw else app_data_dir() / "models"

    @models_dir.setter
    def models_dir(self, value: Path) -> None:
        self._q.setValue("library/models_dir", str(value))

    # --- práce ----------------------------------------------------------------

    @property
    def work_root(self) -> Path:
        """Kam jdou výstupy a mezivýsledky. Do složky s nahrávkami nikdy."""
        raw = self._q.value("work/root", "")
        return Path(raw) if raw else documents_dir() / "SpeechScope"

    @work_root.setter
    def work_root(self, value: Path) -> None:
        self._q.setValue("work/root", str(value))

    @property
    def last_input_dir(self) -> Path | None:
        raw = self._q.value("work/last_input", "")
        return Path(raw) if raw else None

    @last_input_dir.setter
    def last_input_dir(self, value: Path) -> None:
        self._q.setValue("work/last_input", str(value))

    @property
    def last_protocol(self) -> str:
        return str(self._q.value("work/last_protocol", ""))

    @last_protocol.setter
    def last_protocol(self, value: str) -> None:
        self._q.setValue("work/last_protocol", value)

    # --- UI -------------------------------------------------------------------

    @property
    def advanced(self) -> bool:
        """Rozšířený režim pro výzkumníky: strom feature a všechny parametry."""
        return self._q.value("ui/advanced", False, type=bool)

    @advanced.setter
    def advanced(self, value: bool) -> None:
        self._q.setValue("ui/advanced", bool(value))

    def protocols_dir(self) -> Path:
        return app_data_dir() / "protocols"

    def make_library(self) -> library.Library | None:
        cmd = self.effective_command()
        if not cmd:
            return None
        return library.Library(cmd, models_dir=self.models_dir)
