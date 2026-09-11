"""Dialog pro uložení upraveného výběru jako nový protokol."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..backend.protocol import Protocol
from ..i18n import tr


class SaveProtocolDialog(QDialog):
    """Zeptá se na jméno a popis; vrátí kopii protokolu s novým jménem."""

    def __init__(self, proto: Protocol, taken: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Uložit jako protokol"))
        self.setMinimumWidth(460)
        self._proto = proto
        self._taken = taken

        layout = QVBoxLayout(self)
        hint = QLabel(
            tr(
                "Uloží se úloha, {n} vybraných feature a upravené parametry. "
                "Protokol pak uvidí každý, kdo aplikaci na tomhle počítači spustí."
            ).format(n=len(proto.features))
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        self.name = QLineEdit(proto.name)
        self.name.selectAll()
        self.name.textChanged.connect(self._validate)
        form.addRow(tr("Jméno:"), self.name)
        self.description = QPlainTextEdit(proto.description)
        self.description.setFixedHeight(70)
        form.addRow(tr("Popis:"), self.description)
        layout.addLayout(form)

        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate()

    def _validate(self) -> None:
        name = self.name.text().strip()
        ok = bool(name)
        if not name:
            self.note.setText(tr("Zadej jméno."))
        elif name in self._taken:
            self.note.setText(tr("Protokol s tímhle jménem už existuje, přepíše se."))
        else:
            self.note.setText("")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(ok)

    def result_protocol(self) -> Protocol:
        return Protocol(
            name=self.name.text().strip(),
            task=self._proto.task,
            description=self.description.toPlainText().strip(),
            features=list(self._proto.features),
            domain=self._proto.domain,
            vad=self._proto.vad,
            config={k: dict(v) for k, v in self._proto.config.items()},
        )
