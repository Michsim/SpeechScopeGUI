"""Dialogy před samostatnou segmentací nebo přepisem (rozšířený režim).

Segmentace: jaký model se použije a jestli uložit i vyříznutou řeč.
Přepis: upozornění, že se jen přepisuje, a volba jazyka.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import contract

# Modely segmentace, které samostatný běh umí spočítat. `auto` chybí schválně:
# bere jen hotovou cache, takže by samostatná segmentace nic neudělala.
SEGMENT_MODELS: tuple[str, ...] = ("conformer", "pyannote", "labels")
SEGMENT_MODEL_LABELS: dict[str, str] = {
    "conformer": "conformer (ONNX, doporučený)",
    "pyannote": "pyannote (potřebuje torch a model pyannote)",
    "labels": "jen ruční labely vedle nahrávek",
}


class _PrepareDialog(QDialog):
    def __init__(self, title: str, note: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        text = QLabel(note)
        text.setWordWrap(True)
        layout.addWidget(text)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.buttons = QDialogButtonBox()
        self.buttons.addButton("Spustit", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton("Zrušit", QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)


class SegmentDialog(_PrepareDialog):
    def __init__(
        self,
        n_inputs: int,
        *,
        model: str,
        choices: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            "Jen segmentace",
            f"Segmentace řeči {n_inputs} nahrávek do pracovní složky. Žádné feature se "
            "nepočítají; výpočet je pak vezme z cache.",
            parent,
        )
        self.model = QComboBox()
        for name in choices or list(SEGMENT_MODELS):
            if name == "auto":
                continue
            self.model.addItem(SEGMENT_MODEL_LABELS.get(name, name), name)
        idx = self.model.findData(model if model != "auto" else "conformer")
        self.model.setCurrentIndex(max(0, idx))
        self.form.addRow("Model:", self.model)
        self.cut_audio = QCheckBox("uložit i vyříznutou řeč (VAD) jako wav do pracovní složky")
        self.cut_audio.setToolTip("Do složky s nahrávkami se nikdy nezapisuje.")
        self.form.addRow("", self.cut_audio)

    def options(self) -> dict[str, object]:
        return {"model": str(self.model.currentData()), "cut_audio": self.cut_audio.isChecked()}


class TranscribeDialog(_PrepareDialog):
    def __init__(
        self,
        n_inputs: int,
        *,
        language: str,
        languages: list[str],
        model: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            "Jen přepis",
            f"Nahrávky ({n_inputs}) se jen přepíší Whisperem do pracovní složky. Žádné "
            "feature se nepočítají; přepisy jde před výpočtem ručně zkontrolovat.",
            parent,
        )
        self.language = QComboBox()
        for code in languages or list(contract.DEFAULT_LANGUAGES):
            self.language.addItem(contract.language_label(code), code)
        idx = self.language.findData(language)
        self.language.setCurrentIndex(max(0, idx))
        self.form.addRow("Jazyk:", self.language)
        model_label = QLabel(model)
        model_label.setObjectName("muted")
        self.form.addRow("Model:", model_label)

    def options(self) -> dict[str, object]:
        return {"language": str(self.language.currentData())}
