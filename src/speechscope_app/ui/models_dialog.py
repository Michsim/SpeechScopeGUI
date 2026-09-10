"""Dialog stažení modelů: `models download --only …` s logem a tokeny.

Modely jdou do složky z Nastavení (`--models-dir`). phnrec se stáhnout
nedá, dialog jen řekne, kam ho zkopírovat. pyannote je za přihlášením
na Hugging Face, ONNX segmentace za tokenem GitHubu; obojí se zadává
tady a nikam se neukládá.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..backend import command
from ..backend.library import Library, LibraryError
from ..backend.runner import Runner
from . import theme

NOT_DOWNLOADABLE = {"phnrec"}
NEEDS_HF_TOKEN = {"pyannote"}
NEEDS_GITHUB_TOKEN = {"onnx"}


class ModelsDownloadDialog(QDialog):
    def __init__(self, library: Library, models_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stažení modelů")
        self.setMinimumSize(640, 520)
        self._library = library
        self._models_dir = models_dir
        self.downloaded = False  # aspoň jeden běh skončil úspěšně
        self._checks: dict[str, QCheckBox] = {}

        self.runner = Runner(self)
        self.runner.log.connect(self._on_log)
        self.runner.finished.connect(self._on_finished)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = QLabel(
            f"Modely se stáhnou do <b>{models_dir}</b>. Dohromady mají přes 4 GB, "
            "stažení trvá podle připojení desítky minut. Aplikaci mezitím nezavírej."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        layout.addLayout(self.rows)

        tokens = QFormLayout()
        self.hf_token = QLineEdit()
        self.hf_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_token.setPlaceholderText("hf_…  (jen pro pyannote; odsouhlas podmínky modelu)")
        self.hf_label = QLabel("Token Hugging Face:")
        tokens.addRow(self.hf_label, self.hf_token)
        self.gh_token = QLineEdit()
        self.gh_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.gh_token.setPlaceholderText("github_pat_…  (jen pro ONNX segmentaci)")
        self.gh_label = QLabel("Token GitHubu:")
        tokens.addRow(self.gh_label, self.gh_token)
        layout.addLayout(tokens)

        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        layout.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        self.status = QLabel("")
        buttons.addWidget(self.status, 1)
        self.cancel_btn = QPushButton("Zrušit stahování")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.runner.cancel)
        buttons.addWidget(self.cancel_btn)
        self.close_btn = QPushButton("Zavřít")
        self.close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.close_btn)
        self.start_btn = QPushButton("Stáhnout")
        theme.set_role(self.start_btn, "primary")
        self.start_btn.clicked.connect(self.start)
        buttons.addWidget(self.start_btn)
        layout.addLayout(buttons)

        self.refresh_models()

    # --- seznam modelů --------------------------------------------------------

    def refresh_models(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._checks.clear()
        try:
            models: list[dict[str, Any]] = self._library.models()["models"]
        except (LibraryError, KeyError, TypeError) as exc:
            self.rows.addWidget(QLabel(f"Seznam modelů nejde načíst: {exc}"))
            self.start_btn.setEnabled(False)
            return
        manual: list[str] = []
        for m in models:
            key = str(m.get("key", ""))
            present = bool(m.get("present"))
            size = m.get("size_mb")
            text = f"{m.get('name', key)}"
            if size:
                text += f"  ({size} MB)"
            if present:
                row = QLabel(f"<span style='color:{theme.OK}'>●</span>  {text}, je na místě")
                self.rows.addWidget(row)
                continue
            if key in NOT_DOWNLOADABLE:
                manual.append(f"{m.get('name', key)}: zkopíruj složku ručně do {m.get('path')}")
                continue
            box = QCheckBox(text)
            box.setChecked(True)
            box.toggled.connect(self._update_tokens)
            self._checks[key] = box
            self.rows.addWidget(box)
        self.note.setText("\n".join(manual))
        self.note.setVisible(bool(manual))
        self._update_tokens()
        if not self._checks:
            self.status.setText("Všechny stažitelné modely už jsou na místě.")
        self.start_btn.setEnabled(bool(self._checks))

    def selected(self) -> list[str]:
        return [key for key, box in self._checks.items() if box.isChecked()]

    def _update_tokens(self) -> None:
        chosen = set(self.selected())
        hf = bool(chosen & NEEDS_HF_TOKEN)
        gh = bool(chosen & NEEDS_GITHUB_TOKEN)
        self.hf_label.setVisible(hf)
        self.hf_token.setVisible(hf)
        self.gh_label.setVisible(gh)
        self.gh_token.setVisible(gh)
        self.start_btn.setEnabled(bool(chosen) and not self.runner.running)

    # --- běh ------------------------------------------------------------------

    def start(self) -> None:
        only = self.selected()
        if not only or self.runner.running:
            return
        env: dict[str, str] = {}
        if self.hf_token.text().strip():
            env["HF_TOKEN"] = self.hf_token.text().strip()
        if self.gh_token.text().strip():
            env["GITHUB_TOKEN"] = self.gh_token.text().strip()
        argv = self._library.argv(
            command.models_download_args(only=only, models_dir=self._models_dir)
        )
        self.log.clear()
        self.log.appendPlainText("$ " + " ".join(a for a in argv))
        self.bar.setRange(0, 0)
        self.status.setText("Stahuji " + ", ".join(only) + "…")
        self.start_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        for box in self._checks.values():
            box.setEnabled(False)
        self.runner.start(argv, parse_events=False, extra_env=env)

    def _on_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _on_finished(self, code: int, cancelled: bool) -> None:
        self.bar.setRange(0, 1)
        self.bar.setValue(1 if code == 0 and not cancelled else 0)
        self.cancel_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        for box in self._checks.values():
            box.setEnabled(True)
        if cancelled:
            self.status.setText("Zrušeno. Co se stihlo stáhnout, zůstává.")
        elif code != 0:
            self.status.setText(f"Stahování skončilo chybou (kód {code}), viz log.")
        else:
            self.downloaded = True
            self.status.setText("Hotovo.")
        self.refresh_models()

    def reject(self) -> None:
        if self.runner.running:
            return  # během stahování se dialog nezavírá, je tu Zrušit
        super().reject()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self.runner.running:
            return
        super().keyPressEvent(event)
