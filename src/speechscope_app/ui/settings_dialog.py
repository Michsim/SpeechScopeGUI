"""Dialog Nastavení: cesta ke knihovně, modely, pracovní složka, režim."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ..backend.settings import AppSettings


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
        self.setWindowTitle("Nastavení")
        self.settings = settings

        form = QFormLayout(self)
        self.library = QLineEdit(" ".join(settings.library_command or []))
        self.library.setPlaceholderText("speechscope.exe z prostředí knihovny")
        form.addRow("Knihovna SpeechScope", _path_row(self.library, self._browse_library))

        self.use_fake = QCheckBox("použít falešnou knihovnu (vývoj, bez modelů)")
        self.use_fake.setChecked(settings.use_fake_library)
        form.addRow("", self.use_fake)

        self.models = QLineEdit(str(settings.models_dir))
        form.addRow("Složka s modely", _path_row(self.models, self._browse_models))

        self.work = QLineEdit(str(settings.work_root))
        form.addRow("Výstupy a mezivýsledky", _path_row(self.work, self._browse_work))

        self.advanced = QCheckBox("rozšířený režim (výběr feature a parametrů)")
        self.advanced.setChecked(settings.advanced)
        form.addRow("", self.advanced)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_library(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Spustitelný soubor knihovny", "", "speechscope (speechscope*.exe *.py *)"
        )
        if chosen:
            if chosen.endswith(".py"):
                self.library.setText(f"{sys.executable} {chosen}")
            else:
                self.library.setText(chosen)

    def _browse_models(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Složka s modely", self.models.text())
        if chosen:
            self.models.setText(chosen)

    def _browse_work(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Pracovní složka", self.work.text())
        if chosen:
            self.work.setText(chosen)

    def accept(self) -> None:
        text = self.library.text().strip()
        self.settings.library_command = text.split() if text else None
        self.settings.use_fake_library = self.use_fake.isChecked()
        self.settings.models_dir = Path(self.models.text().strip())
        self.settings.work_root = Path(self.work.text().strip())
        self.settings.advanced = self.advanced.isChecked()
        super().accept()
