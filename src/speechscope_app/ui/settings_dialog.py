"""Dialog Nastavení: cesta ke knihovně, modely, pracovní složka, režim."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from .. import i18n
from ..backend.settings import AppSettings
from ..i18n import tr


def _path_row(edit: QLineEdit, on_browse) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    btn = QPushButton("…")
    btn.setFixedWidth(32)
    btn.clicked.connect(on_browse)
    layout.addWidget(edit, 1)
    layout.addWidget(btn)
    return row


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Nastavení"))
        self.settings = settings

        form = QFormLayout(self)
        self.library = QLineEdit(" ".join(settings.library_command or []))
        self.library.setPlaceholderText(tr("speechscope.exe z prostředí knihovny"))
        form.addRow(tr("Knihovna SpeechScope"), _path_row(self.library, self._browse_library))

        self.use_fake = QCheckBox(tr("použít falešnou knihovnu (vývoj, bez modelů)"))
        self.use_fake.setChecked(settings.use_fake_library)
        form.addRow("", self.use_fake)

        self.models = QLineEdit(str(settings.models_dir))
        form.addRow(tr("Složka s modely"), _path_row(self.models, self._browse_models))

        self.work = QLineEdit(str(settings.work_root))
        form.addRow(tr("Výstupy a mezivýsledky"), _path_row(self.work, self._browse_work))

        self.advanced = QCheckBox(tr("rozšířený režim (výběr feature a parametrů)"))
        self.advanced.setChecked(settings.advanced)
        form.addRow("", self.advanced)

        self.language = QComboBox()
        self.language.addItem(tr("podle systému"), "")
        for code in i18n.available():
            self.language.addItem(i18n.LANGUAGE_NAMES.get(code, code), code)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.ui_language)))
        self.language.setToolTip(tr("Projeví se po novém spuštění aplikace."))
        form.addRow(tr("Jazyk aplikace"), self.language)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_library(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, tr("Spustitelný soubor knihovny"), "", tr("speechscope (speechscope*.exe *.py *)")
        )
        if chosen:
            if chosen.endswith(".py"):
                self.library.setText(f"{sys.executable} {chosen}")
            else:
                self.library.setText(chosen)

    def _browse_models(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, tr("Složka s modely"), self.models.text())
        if chosen:
            self.models.setText(chosen)

    def _browse_work(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, tr("Pracovní složka"), self.work.text())
        if chosen:
            self.work.setText(chosen)

    def accept(self) -> None:
        text = self.library.text().strip()
        self.settings.library_command = text.split() if text else None
        self.settings.use_fake_library = self.use_fake.isChecked()
        self.settings.models_dir = Path(self.models.text().strip())
        self.settings.work_root = Path(self.work.text().strip())
        self.settings.advanced = self.advanced.isChecked()
        self.settings.ui_language = str(self.language.currentData() or "")
        super().accept()
