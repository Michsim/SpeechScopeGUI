"""Hlavní okno: boční menu a stránky. Tady se skládá běh z protokolu."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, contract
from ..backend.command import extract_args
from ..backend.protocol import Protocol, all_protocols
from ..backend.settings import AppSettings
from .pages.batch import BatchPage
from .pages.environment import EnvironmentPage
from .pages.results import ResultsPage
from .pages.run import RunPage
from .settings_dialog import SettingsDialog

PAGE_ENV, PAGE_BATCH, PAGE_RUN, PAGE_RESULTS = range(4)


def _slug(text: str) -> str:
    """Jméno protokolu jako část názvu složky: bez diakritiky a mezer."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "davka"


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle("SpeechScope")
        self.resize(1100, 720)

        self.env_page = EnvironmentPage()
        self.batch_page = BatchPage()
        self.run_page = RunPage()
        self.results_page = ResultsPage()

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        for label in ("Prostředí", "Dávka", "Běh", "Výsledky"):
            self.nav.addItem(label)
        self.pages = QStackedWidget()
        for page in (self.env_page, self.batch_page, self.run_page, self.results_page):
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(0)
        brand = QLabel("SpeechScope")
        brand.setObjectName("brand")
        self.brand_sub = QLabel("")
        self.brand_sub.setObjectName("brand_sub")
        side.addWidget(brand)
        side.addWidget(self.brand_sub)
        side.addWidget(self.nav, 1)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        menu = self.menuBar().addMenu("Aplikace")
        menu.addAction("Nastavení…", self._open_settings)
        menu.addSeparator()
        menu.addAction("Konec", self.close)

        self.env_page.settings_requested.connect(self._open_settings)
        self.batch_page.run_requested.connect(self._start_batch)
        self.run_page.finished.connect(self._batch_finished)

        self._apply_settings()
        self.nav.setCurrentRow(PAGE_BATCH if self.library is not None else PAGE_ENV)

    # --- nastavení ------------------------------------------------------------

    def _apply_settings(self) -> None:
        self.library = self.settings.make_library()
        self.env_page.set_library(self.library)
        self.batch_page.set_library(self.library)
        self.batch_page.set_advanced(self.settings.advanced)
        self.batch_page.set_protocols(
            all_protocols(self.settings.protocols_dir()), current=self.settings.last_protocol
        )
        if self.settings.last_input_dir and self.settings.last_input_dir.is_dir():
            self.batch_page.set_folder(self.settings.last_input_dir)
        title = "SpeechScope"
        sub = f"aplikace {__version__}"
        if self.settings.use_fake_library:
            title += " [falešná knihovna]"
            sub += " · falešná knihovna"
        self.setWindowTitle(title)
        self.brand_sub.setText(sub)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self._apply_settings()

    # --- běh ------------------------------------------------------------------

    def _start_batch(self, proto: Protocol, inputs: list[Path]) -> None:
        if self.library is None:
            QMessageBox.warning(self, "SpeechScope", "Knihovna není nastavená.")
            return
        if self.run_page.runner.running:
            QMessageBox.information(self, "SpeechScope", "Jiný běh ještě neskončil.")
            return

        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        run_dir = self.settings.work_root / f"{stamp}_{_slug(proto.name)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        work_dir = self.settings.work_root / "work"
        config_path: Path | None = None
        if proto.config:
            config_path = run_dir / "config.yaml"
            config_path.write_text(proto.config_yaml(), encoding="utf-8")
        proto.save(run_dir / "protocol.yaml")  # co přesně se spustilo

        req = proto.to_request(
            inputs,
            out=run_dir / "features.csv",
            work_dir=work_dir,
            config_path=config_path,
            log_file=run_dir / "speechscope.log",
        )
        argv = self.library.argv(extract_args(req, models_dir=self.settings.models_dir))

        self.settings.last_protocol = proto.name
        if inputs:
            self.settings.last_input_dir = inputs[0].parent
        self.nav.setCurrentRow(PAGE_RUN)
        self.run_page.start(argv, title=f"Spouštím {proto.name}…")

    def _batch_finished(self, state: contract.BatchState, code: int, cancelled: bool) -> None:
        if cancelled or code != 0 or not state.out:
            return
        out = Path(state.out)
        if out.is_file():
            self.results_page.load(out)
            self.nav.setCurrentRow(PAGE_RESULTS)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F5:
            self.env_page.refresh()
        super().keyPressEvent(event)
