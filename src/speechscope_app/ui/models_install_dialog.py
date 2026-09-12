"""Dialog instalace modelů ze souboru: `models unpack BALÍK` s logem.

Balík je zip z `speechscope models pack` (manifest s otisky souborů).
Klinik ho dostane na USB, ze sdíleného disku nebo odkazem; nepotřebuje
Hugging Face ani tokeny. Modely jdou do složky z Nastavení (`--models-dir`),
ty, které už tam jsou, balík nahradí.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..backend import command
from ..backend.library import Library, LibraryError
from ..backend.runner import Runner
from ..contract import MODELS_BUNDLE_FILTER
from ..i18n import tr
from . import theme


class ModelsInstallDialog(QDialog):
    def __init__(
        self,
        library: Library,
        models_dir: Path,
        parent: QWidget | None = None,
        *,
        archive: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Instalace modelů ze souboru"))
        self.setMinimumSize(640, 460)
        self._library = library
        self._models_dir = models_dir
        self.installed = False  # aspoň jeden běh skončil úspěšně

        self.runner = Runner(self)
        self.runner.log.connect(self._on_log)
        self.runner.finished.connect(self._on_finished)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = QLabel(
            tr(
                "Modely se rozbalí do <b>{path}</b>. Balík je zip vyrobený příkazem "
                "<code>speechscope models pack</code> (přes 4 GB); každý soubor se při "
                "rozbalení ověřuje. Modely, které už na místě jsou, balík nahradí."
            ).format(path=models_dir)
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.archive = QLineEdit()
        self.archive.setPlaceholderText(tr("cesta k balíku modelů (.zip)"))
        self.archive.textChanged.connect(self._update_start)
        row.addWidget(self.archive, 1)
        self.browse_btn = QPushButton(tr("Vybrat…"))
        self.browse_btn.clicked.connect(self._browse)
        row.addWidget(self.browse_btn)
        layout.addLayout(row)

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
        self.cancel_btn = QPushButton(tr("Zrušit"))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.runner.cancel)
        buttons.addWidget(self.cancel_btn)
        self.close_btn = QPushButton(tr("Zavřít"))
        self.close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.close_btn)
        self.start_btn = QPushButton(tr("Nainstalovat"))
        theme.set_role(self.start_btn, "primary")
        self.start_btn.clicked.connect(self.start)
        buttons.addWidget(self.start_btn)
        layout.addLayout(buttons)

        if archive is not None:
            self.archive.setText(str(archive))
        self._update_start()

    # --- výběr souboru --------------------------------------------------------

    def _browse(self) -> None:
        start = self.archive.text().strip() or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(
            self, tr("Balík modelů SpeechScope"), start, tr(MODELS_BUNDLE_FILTER)
        )
        if chosen:
            self.archive.setText(chosen)

    def archive_path(self) -> Path | None:
        text = self.archive.text().strip()
        return Path(text) if text else None

    def _update_start(self) -> None:
        path = self.archive_path()
        self.start_btn.setEnabled(path is not None and path.is_file() and not self.runner.running)

    # --- běh ------------------------------------------------------------------

    def bundle_keys(self, path: Path) -> list[str] | None:
        """Klíče modelů v balíku podle manifestu; None, když to není balík."""
        try:
            with zipfile.ZipFile(path) as zf:
                manifest = json.loads(zf.read("manifest.json"))
        except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError):
            return None
        models = manifest.get("models")
        return list(models) if isinstance(models, dict) else None

    def installed_keys(self) -> set[str]:
        """Modely, které už ve složce jsou (`models list --json`); bez knihovny prázdné."""
        try:
            payload = self._library.models()
        except LibraryError:
            return set()
        return {str(m.get("key")) for m in payload.get("models", []) if m.get("present")}

    def start(self, *, force: bool = False) -> None:
        path = self.archive_path()
        if path is None or not path.is_file() or self.runner.running:
            return
        if not force:
            keys = self.bundle_keys(path)
            present = self.installed_keys()
            if keys and all(k in present for k in keys):
                answer = QMessageBox.question(
                    self,
                    tr("Modely už jsou nainstalované"),
                    tr(
                        "Všech {n} modelů z balíku už je nainstalováno. Přeinstalovat? "
                        "Rozbalení trvá několik minut, hotové modely zůstanou použitelné."
                    ).format(n=len(keys)),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self.status.setText(tr("Modely už jsou nainstalované, nic se nedělalo."))
                    return
            elif keys:
                have = [k for k in keys if k in present]
                if have:
                    self.status.setText(
                        tr("{k} z {n} modelů z balíku už je nainstalováno, přepíší se.").format(
                            k=len(have), n=len(keys)
                        )
                    )
        argv = self._library.argv(command.models_unpack_args(path, models_dir=self._models_dir))
        self.log.clear()
        self.log.appendPlainText("$ " + " ".join(argv))
        self.bar.setRange(0, 0)
        self.status.setText(tr("Rozbaluji {name}…").format(name=path.name))
        self.start_btn.setEnabled(False)
        self.browse_btn.setEnabled(False)
        self.archive.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.runner.start(argv, parse_events=False)

    def _on_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _on_finished(self, code: int, cancelled: bool) -> None:
        self.bar.setRange(0, 1)
        self.bar.setValue(1 if code == 0 and not cancelled else 0)
        self.cancel_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.archive.setEnabled(True)
        self._update_start()
        if cancelled:
            self.status.setText(tr("Zrušeno. Rozpracovaný model se neuložil, hotové zůstávají."))
        elif code == 2:
            self.status.setText(tr("Soubor není balík modelů SpeechScope, viz log."))
        elif code != 0:
            self.status.setText(
                tr("Instalace skončila chybou (kód {code}), viz log.").format(code=code)
            )
        else:
            self.installed = True
            self.status.setText(tr("Hotovo."))

    def reject(self) -> None:
        if self.runner.running:
            return  # během rozbalování se dialog nezavírá, je tu Zrušit
        super().reject()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self.runner.running:
            return
        super().keyPressEvent(event)
