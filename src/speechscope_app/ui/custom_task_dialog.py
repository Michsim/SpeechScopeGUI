"""Dialog Vlastní úloha: název, základní úloha knihovny, výchozí protokol, pokyn.

Otevírá ho průvodce Nová norma (+ Přidat vlastní úlohu…). Výsledek je
`CustomTask` (`backend/tasks.py`); klíč se odvodí z názvu přes registr,
aby nekolidoval s úlohami knihovny ani s jinou vlastní úlohou.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend import tasks
from ..backend.norm_build import default_protocol
from ..backend.protocol import Protocol
from ..i18n import tr


class CustomTaskDialog(QDialog):
    def __init__(
        self,
        protocols: list[Protocol],
        *,
        advanced: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Vlastní úloha"))
        self.resize(560, 420)
        self._protocols = protocols
        self._advanced = advanced

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("třeba Popis obrázku"))
        self.name.textChanged.connect(self._update_buttons)
        form.addRow(tr("Název"), self.name)
        self.base = QComboBox()
        for task in contract.TASKS:
            self.base.addItem(contract.TASK_SHORT.get(task, task), task)
        self.base.setCurrentIndex(self.base.findData("spontaneous"))
        self.base.currentIndexChanged.connect(self._fill_protocols)
        form.addRow(tr("Počítá se jako"), self.base)
        self.protocol = QComboBox()
        form.addRow(tr("Výchozí protokol"), self.protocol)
        self.instruction = QPlainTextEdit()
        self.instruction.setPlaceholderText(
            tr("nepovinné, třeba „Popište, co vidíte na obrázku, aspoň minutu.“")
        )
        self.instruction.setMaximumHeight(72)
        form.addRow(tr("Pokyn pro pacienta"), self.instruction)
        outer.addLayout(form)
        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        note2 = QLabel(
            tr(
                "Úloha se nabízí u norem a hodnocení, na Analýze zůstávají jen úlohy "
                "knihovny. Norma si úlohu zapíše, takže po importu normy na jiném počítači "
                "se založí sama."
            )
        )
        note2.setObjectName("muted")
        note2.setWordWrap(True)
        outer.addWidget(note2)
        outer.addStretch(1)
        buttons = QHBoxLayout()
        cancel = QPushButton(tr("Storno"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self.ok_btn = QPushButton(tr("Přidat úlohu"))
        self.ok_btn.setObjectName("primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        buttons.addWidget(self.ok_btn)
        outer.addLayout(buttons)
        self._fill_protocols()
        self._update_buttons()

    def base_task(self) -> str:
        return str(self.base.currentData() or "spontaneous")

    def _fill_protocols(self) -> None:
        base = self.base_task()
        self.protocol.clear()
        choices = [p for p in self._protocols if p.task == base and (p.builtin or self._advanced)]
        default = default_protocol(base, self._protocols)
        for proto in sorted(choices, key=lambda p: (not p.builtin, p.display_name)):
            suffix = tr(" (přibalený)") if proto.builtin else tr(" (vlastní)")
            self.protocol.addItem(proto.display_name + suffix, proto)
            if default is not None and proto.name == default.name:
                self.protocol.setCurrentIndex(self.protocol.count() - 1)
        self.note.setText(
            tr(
                "Knihovna počítá úlohu jako {task}, jen pod jiným jménem: stejné feature, "
                "stejná segmentace i přepis. Nabídka protokolů je podle toho; protokol jde "
                "u normy i hodnocení změnit."
            ).format(task=contract.TASK_SHORT.get(base, base))
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.ok_btn.setEnabled(bool(self.name.text().strip()) and self.protocol.count() > 0)

    def task(self) -> tasks.CustomTask:
        """Vlastní úloha z vyplněného dialogu; klíč z názvu, volný v registru."""
        name = self.name.text().strip()
        proto = self.protocol.currentData()
        return tasks.CustomTask(
            key=tasks.registry().new_key(name),
            name=name,
            base=self.base_task(),
            protocol=proto.name if isinstance(proto, Protocol) else "",
            instruction=self.instruction.toPlainText().strip(),
        )


__all__ = ["CustomTaskDialog"]
