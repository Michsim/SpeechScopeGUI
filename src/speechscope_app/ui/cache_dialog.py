"""Dialog úklidu mezivýsledků: starší než N dní, nebo všechny."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..backend.cache import CacheInfo, format_size, purge, scan
from ..i18n import tr


class CacheDialog(QDialog):
    def __init__(self, work_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work_dir = work_dir
        self.removed: CacheInfo | None = None
        self.setWindowTitle(tr("Uvolnit místo"))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = QLabel(
            tr(
                "Mezivýsledky (segmentace a přepisy) ve složce work si knihovna ukládá, "
                "aby je příště nemusela počítat znovu. Smazané se při dalším běhu dopočítají, "
                "výsledky ve složkách běhů se nemažou."
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.older = QRadioButton(tr("smazat starší než"))
        self.older.setChecked(True)
        self.days = QSpinBox()
        self.days.setRange(1, 3650)
        self.days.setValue(30)
        self.days.setSuffix(tr(" dní"))
        row = QHBoxLayout()
        row.addWidget(self.older)
        row.addWidget(self.days)
        row.addStretch(1)
        layout.addLayout(row)
        self.everything = QRadioButton(tr("smazat všechny mezivýsledky"))
        layout.addWidget(self.everything)

        self.preview = QLabel("")
        self.preview.setObjectName("muted")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)

        buttons = QDialogButtonBox()
        self.delete_btn = buttons.addButton(tr("Smazat"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._purge)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.older.toggled.connect(self._update_preview)
        self.days.valueChanged.connect(self._update_preview)
        self._update_preview()

    def older_than_days(self) -> int | None:
        return self.days.value() if self.older.isChecked() else None

    def _update_preview(self) -> None:
        info = scan(self.work_dir, older_than_days=self.older_than_days())
        self.delete_btn.setEnabled(not info.empty)
        self.preview.setText(
            tr("Smaže se {n} souborů, {size}.").format(n=info.files, size=format_size(info.size))
            if not info.empty
            else tr("Nic k smazání.")
        )

    def _purge(self) -> None:
        self.removed = purge(self.work_dir, older_than_days=self.older_than_days())
        self.accept()
