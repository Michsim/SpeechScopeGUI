"""Hlavní okno: boční menu a stránky. Tady se skládá běh z protokolu."""

from __future__ import annotations

import datetime as dt
from importlib import resources
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
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
from .models_dialog import ModelsDownloadDialog
from .models_install_dialog import ModelsInstallDialog
from .pages.batch import BatchPage
from .pages.environment import EnvironmentPage
from .pages.protocols import ProtocolsPage
from .pages.results import ResultsPage
from .pages.run import RunPage
from .settings_dialog import SettingsDialog

PAGE_ENV, PAGE_BATCH, PAGE_PROTOCOLS, PAGE_RUN, PAGE_RESULTS = range(5)


def _has_models(models_dir: Path) -> bool:
    """Laciný test bez volání knihovny: složka existuje a není prázdná."""
    return models_dir.is_dir() and any(models_dir.iterdir())


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle("SpeechScope")
        self.resize(1100, 720)

        self.env_page = EnvironmentPage()
        self.batch_page = BatchPage()
        self.protocols_page = ProtocolsPage()
        self.run_page = RunPage()
        self.results_page = ResultsPage()

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav_labels = ("Prostředí", "Data", "Protokoly", "Běh", "Výsledky")
        for label in self.nav_labels:
            self.nav.addItem(label)
        self.pages = QStackedWidget()
        for page in (
            self.env_page,
            self.batch_page,
            self.protocols_page,
            self.run_page,
            self.results_page,
        ):
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(0)
        brand_panel = QWidget()
        brand_panel.setObjectName("brand_panel")
        brand_layout = QVBoxLayout(brand_panel)
        brand_layout.setContentsMargins(14, 14, 14, 10)
        brand_layout.setSpacing(4)
        brand = QLabel()
        brand.setObjectName("brand")
        wordmark = QPixmap(str(resources.files("speechscope_app") / "assets" / "wordmark.png"))
        if wordmark.isNull():
            brand.setText("SpeechScope")
        else:
            # škálovat na fyzické pixely, jinak je logo na HiDPI rozmazané
            dpr = self.devicePixelRatioF()
            scaled = wordmark.scaledToWidth(
                round(172 * dpr), Qt.TransformationMode.SmoothTransformation
            )
            scaled.setDevicePixelRatio(dpr)
            brand.setPixmap(scaled)
        brand_layout.addWidget(brand)
        self.brand_sub = QLabel("")
        self.brand_sub.setObjectName("brand_sub")
        brand_layout.addWidget(self.brand_sub)
        side.addWidget(brand_panel)
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
        menu.addAction("Stáhnout modely…", self.download_models)
        menu.addAction("Nainstalovat modely ze souboru…", self.install_models)
        menu.addSeparator()
        menu.addAction("Konec", self.close)

        self.env_page.settings_requested.connect(self._open_settings)
        self.env_page.download_requested.connect(self.download_models)
        self.env_page.install_requested.connect(self.install_models)
        self.env_page.report_changed.connect(self.batch_page.set_doctor)
        self.env_page.report_changed.connect(self.protocols_page.set_doctor)
        self.batch_page.set_stats_lookup(self.settings.seconds_per_file)
        self.protocols_page.set_stats_lookup(self.settings.seconds_per_file)
        self.protocols_page.protocols_changed.connect(self.reload_protocols)
        self.batch_page.run_requested.connect(self._start_batch)
        self.batch_page.save_requested.connect(self.save_protocol)
        self.run_page.finished.connect(self._batch_finished)
        self.run_page.progress_changed.connect(self._show_progress)

        self._apply_settings()
        page = self.start_page()
        self.nav.setCurrentRow(page)
        if page == PAGE_ENV and self.library is not None:
            self.env_page.refresh()  # první spuštění: rovnou ukázat, co chybí

    def start_page(self) -> int:
        """Klinik začíná na Datech; bez knihovny nebo bez modelů na Prostředí."""
        if self.library is None:
            return PAGE_ENV
        if not self.settings.use_fake_library and not _has_models(self.settings.models_dir):
            return PAGE_ENV
        return PAGE_BATCH

    # --- nastavení ------------------------------------------------------------

    def _apply_settings(self) -> None:
        self.library = self.settings.make_library()
        self.env_page.set_library(self.library)
        self.batch_page.set_library(self.library)
        self.batch_page.set_advanced(self.settings.advanced)
        self.protocols_page.set_library(self.library)
        self.protocols_page.set_advanced(self.settings.advanced)
        self.reload_protocols(self.settings.last_protocol)
        if self.settings.last_input_dir and self.settings.last_input_dir.is_dir():
            self.batch_page.set_folder(self.settings.last_input_dir)
        self.batch_page.set_language(self.settings.last_language)
        title = "SpeechScope"
        sub = f"aplikace {__version__}"
        if self.settings.use_fake_library:
            title += " [falešná knihovna]"
            sub += " · falešná knihovna"
        self._base_title = title
        self.setWindowTitle(title)
        self.brand_sub.setText(sub)

    def _show_progress(self, text: str) -> None:
        """Postup běhu v nabídce a v titulku, ať je vidět i z jiné stránky."""
        label = self.nav_labels[PAGE_RUN]
        self.nav.item(PAGE_RUN).setText(f"{label} · {text}" if text else label)
        self.setWindowTitle(f"{text} · {self._base_title}" if text else self._base_title)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self._apply_settings()

    def download_models(self) -> None:
        if self.library is None:
            QMessageBox.warning(self, "SpeechScope", "Knihovna není nastavená.")
            return
        dialog = ModelsDownloadDialog(self.library, self.settings.models_dir, self)
        dialog.exec()
        if dialog.downloaded:
            self.library.clear_cache()
            self.env_page.refresh()

    def install_models(self, archive: Path | None = None) -> None:
        if self.library is None:
            QMessageBox.warning(self, "SpeechScope", "Knihovna není nastavená.")
            return
        dialog = ModelsInstallDialog(self.library, self.settings.models_dir, self, archive=archive)
        dialog.exec()
        if dialog.installed:
            self.library.clear_cache()
            self.env_page.refresh()

    # --- protokoly ------------------------------------------------------------

    def reload_protocols(self, current: str = "") -> None:
        """Znovu načte protokoly z disku do stránek Data i Protokoly."""
        folder = self.settings.protocols_dir()
        protocols = all_protocols(folder)
        current = current or self.batch_page.protocols.current_name()
        self.batch_page.set_protocols(protocols, current=current)
        self.protocols_page.set_protocols(
            protocols, folder, current=self.protocols_page.current_name()
        )

    def save_protocol(self, proto: Protocol) -> Path:
        """Uloží protokol mezi uživatelské a znovu načte nabídku."""
        folder = self.settings.protocols_dir()
        path = folder / f"{proto.slug()}.yaml"
        existing = {p.name: p for p in all_protocols(folder) if not p.builtin}
        if proto.name in existing and existing[proto.name].path is not None:
            path = existing[proto.name].path  # přepis stejného jména, ne druhý soubor
        proto.save(path)
        self.settings.last_protocol = proto.name
        self.reload_protocols(proto.name)
        self.statusBar().showMessage(f"Protokol uložen: {path}", 8000)
        return path

    # --- běh ------------------------------------------------------------------

    def _start_batch(self, proto: Protocol, inputs: list[Path]) -> None:
        if self.library is None:
            QMessageBox.warning(self, "SpeechScope", "Knihovna není nastavená.")
            return
        if self.run_page.runner.running:
            QMessageBox.information(self, "SpeechScope", "Jiný běh ještě neskončil.")
            return

        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        slug = proto.slug()
        run_dir = self.settings.work_root / f"{stamp}_{slug}"
        run_dir.mkdir(parents=True, exist_ok=True)
        work_dir = self.settings.work_root / "work"
        config_path: Path | None = None
        if proto.config:
            config_path = run_dir / "config.yaml"
            config_path.write_text(proto.config_yaml(), encoding="utf-8")
        proto.save(run_dir / "protocol.yaml")  # co přesně se spustilo

        log_file = run_dir / "speechscope.log"
        req = proto.to_request(
            inputs,
            out=run_dir / "features.csv",
            work_dir=work_dir,
            config_path=config_path,
            log_file=log_file,
        )
        argv = self.library.argv(extract_args(req, models_dir=self.settings.models_dir))

        self.settings.last_protocol = proto.name
        self.settings.last_language = self.batch_page.language_code()
        if inputs:
            self.settings.last_input_dir = inputs[0].parent
        self.nav.setCurrentRow(PAGE_RUN)
        self._running_slug = slug
        self._running_out = req.out
        self.run_page.start(
            argv,
            title=proto.name,
            log_file=log_file,
            inputs=inputs,
            expected_seconds=self.settings.seconds_per_file(slug),
        )

    def _batch_finished(self, state: contract.BatchState, code: int, cancelled: bool) -> None:
        per_file = self.run_page.seconds_per_file()
        if per_file is not None and not cancelled and code == 0:
            self.settings.set_seconds_per_file(self._running_slug, per_file)
            self.batch_page.refresh_summary()
        if cancelled or code != 0:
            # knihovna zapisuje průběžně: co je hotové, je v tabulce
            partial = self._running_out
            if partial is not None and partial.is_file() and state.processed:
                why = "zrušení" if cancelled else f"chybě (kód {code})"
                self.results_page.load(
                    partial,
                    note=f"Částečný výsledek po {why}: {state.processed} z {state.total} nahrávek.",
                )
                self.nav.setCurrentRow(PAGE_RESULTS)
            return
        if not state.out:
            return
        out = Path(state.out)
        if out.is_file():
            self.results_page.load(out)
            self.nav.setCurrentRow(PAGE_RESULTS)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F5:
            self.env_page.refresh()
        super().keyPressEvent(event)
