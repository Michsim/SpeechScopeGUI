"""Hlavní okno: boční menu a stránky. Tady se skládá běh z protokolu."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
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
from ..backend.command import PrepareRequest, extract_args, segment_args, transcribe_args
from ..backend.diagnostics import build_bundle, default_name
from ..backend.history import write_run_file
from ..backend.manifest import Manifest, write_normalized
from ..backend.protocol import Protocol, all_protocols, slugify
from ..backend.settings import AppSettings
from ..i18n import tr
from .models_dialog import ModelsDownloadDialog
from .models_install_dialog import ModelsInstallDialog
from .pages.batch import BatchPage
from .pages.environment import EnvironmentPage
from .pages.protocols import ProtocolsPage
from .pages.results import ResultsPage
from .pages.run import RunPage
from .settings_dialog import SettingsDialog

# Pořadí v nabídce: od každodenního (analýza) k jednorázovému (prostředí).
PAGE_BATCH, PAGE_RUN, PAGE_RESULTS, PAGE_PROTOCOLS, PAGE_ENV = range(5)


@dataclass(slots=True)
class Job:
    """Jedna dávka ve frontě: co spustit, s čím a nad čím."""

    kind: str  # "extract" | "segments" | "transcript"
    proto: Protocol
    inputs: list[Path]
    options: dict = field(default_factory=dict)
    manifest: Manifest | None = None
    language: str = ""

    def title(self) -> str:
        name = self.proto.display_name
        if self.kind != "extract":
            what = contract.PROVIDER_SHORT.get(self.kind, self.kind)
            name = tr("Jen {what} · {name}").format(what=what, name=name)
        return tr("{name} ({n} nahrávek)").format(name=name, n=len(self.inputs))


def _has_models(models_dir: Path) -> bool:
    """Laciný test bez volání knihovny: složka existuje a není prázdná."""
    return models_dir.is_dir() and any(models_dir.iterdir())


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle(tr("SpeechScope"))
        self.resize(1100, 720)

        self._queue: list[Job] = []
        self._last_progress = ""
        self.env_page = EnvironmentPage()
        self.batch_page = BatchPage()
        self.protocols_page = ProtocolsPage()
        self.run_page = RunPage()
        self.results_page = ResultsPage()

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav_labels = (
            tr("Analýza"),
            tr("Výpočet"),
            tr("Výsledky"),
            tr("Protokoly"),
            tr("Prostředí"),
        )
        for label in self.nav_labels:
            self.nav.addItem(label)
        self.pages = QStackedWidget()
        for page in (
            self.batch_page,
            self.run_page,
            self.results_page,
            self.protocols_page,
            self.env_page,
        ):
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.currentRowChanged.connect(self._page_shown)

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
            brand.setText(tr("SpeechScope"))
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

        menu = self.menuBar().addMenu(tr("Aplikace"))
        menu.addAction(tr("Nastavení…"), self._open_settings)
        menu.addAction(tr("Stáhnout modely…"), self.download_models)
        menu.addAction(tr("Nainstalovat modely ze souboru…"), self.install_models)
        menu.addSeparator()
        menu.addAction(tr("Konec"), self.close)

        self.env_page.settings_requested.connect(self._open_settings)
        self.env_page.download_requested.connect(self.download_models)
        self.env_page.install_requested.connect(self.install_models)
        self.env_page.diagnostics_requested.connect(self.save_diagnostics)
        self.env_page.report_changed.connect(self.batch_page.set_doctor)
        self.env_page.report_changed.connect(self.protocols_page.set_doctor)
        self.batch_page.set_stats_lookup(self.settings.seconds_per_file)
        self.protocols_page.set_stats_lookup(self.settings.seconds_per_file)
        self.results_page.set_work_root(self.settings.work_root)
        self.protocols_page.protocols_changed.connect(self.reload_protocols)
        self.batch_page.run_requested.connect(self._start_batch)
        self.batch_page.prepare_requested.connect(self._start_prepare)
        self.batch_page.save_requested.connect(self.save_protocol)
        self.run_page.finished.connect(self._batch_finished)
        self.run_page.progress_changed.connect(self._show_progress)
        self.run_page.queue_remove.connect(self.remove_queued)
        self.run_page.queue_clear.connect(self.clear_queue)

        self._apply_settings()
        page = self.start_page()
        self.nav.setCurrentRow(page)
        if page == PAGE_ENV and self.library is not None:
            self.env_page.refresh()  # první spuštění: rovnou ukázat, co chybí

    def _page_shown(self, index: int) -> None:
        if index == PAGE_RESULTS:
            self.results_page.refresh()

    def start_page(self) -> int:
        """Klinik začíná na Analýze; bez knihovny nebo bez modelů na Prostředí."""
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
        sub = tr("aplikace {version}").format(version=__version__)
        if self.settings.use_fake_library:
            title += tr(" [falešná knihovna]")
            sub += tr(" · falešná knihovna")
        self._base_title = title
        self.setWindowTitle(title)
        self.brand_sub.setText(sub)

    def _show_progress(self, text: str) -> None:
        """Postup běhu v nabídce a v titulku, ať je vidět i z jiné stránky."""
        self._last_progress = text
        if self._queue:
            text = f"{text} (+{len(self._queue)})" if text else f"+{len(self._queue)}"
        label = self.nav_labels[PAGE_RUN]
        self.nav.item(PAGE_RUN).setText(f"{label} · {text}" if text else label)
        self.setWindowTitle(f"{text} · {self._base_title}" if text else self._base_title)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self._apply_settings()

    def download_models(self) -> None:
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return
        dialog = ModelsDownloadDialog(self.library, self.settings.models_dir, self)
        dialog.exec()
        if dialog.downloaded:
            self.library.clear_cache()
            self.env_page.refresh()

    def install_models(self, archive: Path | None = None) -> None:
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return
        dialog = ModelsInstallDialog(self.library, self.settings.models_dir, self, archive=archive)
        dialog.exec()
        if dialog.installed:
            self.library.clear_cache()
            self.env_page.refresh()

    # --- diagnostika -----------------------------------------------------------

    def save_diagnostics(self, target: Path | None = None) -> Path | None:
        """Zip pro podporu: bez dialogu, když je `target` daný (testy)."""
        if target is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                tr("Uložit diagnostický balíček"),
                str(self.settings.work_root / default_name()),
                tr("Zip (*.zip)"),
            )
            if not chosen:
                return None
            target = Path(chosen)
        try:
            members = build_bundle(
                target,
                settings=self.settings,
                library=self.library,
                report=self.env_page.report,
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr("SpeechScope"),
                tr("Balíček se nepodařilo uložit: {error}").format(error=exc),
            )
            return None
        self.statusBar().showMessage(
            tr("Diagnostika uložena: {path} ({n} souborů)").format(path=target, n=len(members)),
            10000,
        )
        return target

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
        self.statusBar().showMessage(tr("Protokol uložen: {path}").format(path=path), 8000)
        return path

    # --- běh ------------------------------------------------------------------

    # --- běh a fronta ------------------------------------------------------------

    # --- běh a fronta ------------------------------------------------------------

    # --- běh a fronta ------------------------------------------------------------

    def _start_batch(self, proto: Protocol, inputs: list[Path]) -> None:
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return
        self.settings.last_protocol = proto.name
        self.settings.last_language = self.batch_page.language_code()
        if inputs:
            self.settings.last_input_dir = inputs[0].parent
        self._submit(
            Job(
                kind="extract",
                proto=proto,
                inputs=list(inputs),
                manifest=self.batch_page.manifest(),
                language=self.batch_page.language_code(),
            )
        )

    def _start_prepare(self, kind: str, proto: Protocol, inputs: list[Path], options: dict) -> None:
        """Jen segmentace nebo jen přepis do pracovní složky (rozšířený režim)."""
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return
        self.settings.last_language = self.batch_page.language_code()
        self._submit(
            Job(
                kind=kind,
                proto=proto,
                inputs=list(inputs),
                options=dict(options),
                language=self.batch_page.language_code(),
            )
        )

    def _submit(self, job: Job) -> None:
        """Spustí hned, nebo se zeptá a zařadí za běžící dávku."""
        if self.run_page.runner.running:
            answer = QMessageBox.question(
                self,
                tr("Zařadit do fronty"),
                tr(
                    "Právě běží: {running}.\n"
                    "Zařadit {name} do fronty? Spustí se sama, až běžící dávka skončí "
                    "(bude {n}. v pořadí)."
                ).format(running=self._running_name, name=job.title(), n=len(self._queue) + 1),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._queue.append(job)
            self._show_queue()
            self.statusBar().showMessage(
                tr("Zařazeno do fronty jako {n}.: {name}").format(
                    n=len(self._queue), name=job.title()
                ),
                6000,
            )
            return
        self._launch(job)

    def queued_jobs(self) -> list[Job]:
        return list(self._queue)

    def remove_queued(self, index: int) -> None:
        if 0 <= index < len(self._queue):
            del self._queue[index]
            self._show_queue()

    def clear_queue(self) -> None:
        self._queue.clear()
        self._show_queue()

    def _show_queue(self) -> None:
        self.run_page.set_queue([job.title() for job in self._queue])
        self._show_progress(self._last_progress)

    def _launch_next(self) -> None:
        if self._queue and not self.run_page.runner.running:
            self._launch(self._queue.pop(0))
            self._show_queue()

    def _launch(self, job: Job) -> None:
        assert self.library is not None
        proto, inputs = job.proto, job.inputs
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        slug = proto.slug()
        label = contract.PROVIDER_SHORT.get(job.kind, job.kind)
        suffix = "" if job.kind == "extract" else f"-{slugify(label)}"
        run_dir = self.settings.work_root / f"{stamp}_{slug}{suffix}"
        run_dir.mkdir(parents=True, exist_ok=True)
        work_dir = self.settings.work_root / "work"
        config_path: Path | None = None
        if proto.config:
            config_path = run_dir / "config.yaml"
            config_path.write_text(proto.config_yaml(), encoding="utf-8")
        log_file = run_dir / "speechscope.log"

        if job.kind == "extract":
            proto.save(run_dir / "protocol.yaml")  # co přesně se spustilo
            manifest_path: Path | None = None
            if job.manifest is not None:
                manifest_path = write_normalized(
                    run_dir / "manifest.csv", inputs, proto.task, job.manifest
                )
            req = proto.to_request(
                inputs,
                out=run_dir / "features.csv",
                work_dir=work_dir,
                config_path=config_path,
                log_file=log_file,
                manifest=manifest_path,
            )
            argv = self.library.argv(extract_args(req, models_dir=self.settings.models_dir))
            self._running_slug = slug
            self._running_out = req.out
            title = proto.display_name
            expected = self.settings.seconds_per_file(slug)
        else:
            preq = PrepareRequest(
                inputs=inputs, work_dir=work_dir, config=config_path, log_file=log_file
            )
            if job.kind == "segments":
                args = segment_args(
                    preq,
                    model=str(job.options.get("model") or "conformer"),
                    cut_audio=bool(job.options.get("cut_audio")),
                    models_dir=self.settings.models_dir,
                )
            else:
                transcript = proto.config.get("transcript", {})
                args = transcribe_args(
                    preq,
                    language=str(job.options.get("language") or job.language or "cs"),
                    model=str(transcript.get("model", "large-v3")),
                    models_dir=self.settings.models_dir,
                )
            argv = self.library.argv(args)
            self._running_slug = None
            self._running_out = None
            title = tr("Jen {what} · {name}").format(what=label, name=proto.display_name)
            expected = None

        self._running_dir = run_dir
        self._running_kind = "extract" if job.kind == "extract" else "prepare"
        self._running_name = proto.display_name
        self.nav.setCurrentRow(PAGE_RUN)
        self.run_page.start(
            argv, title=title, log_file=log_file, inputs=inputs, expected_seconds=expected
        )

    def _batch_finished(self, state: contract.BatchState, code: int, cancelled: bool) -> None:
        run_dir = getattr(self, "_running_dir", None)
        if run_dir is not None and run_dir.is_dir():
            if cancelled:
                status = "cancelled"
            elif code != 0:
                status = "error"
            elif self._running_kind == "prepare":
                status = "prepare"
            else:
                status = "ok"
            write_run_file(
                run_dir,
                status=status,
                kind=self._running_kind,
                protocol=self._running_name,
                processed=state.processed,
                total=state.total,
                seconds=self.run_page.elapsed_seconds(),
                out=state.out,
            )
        per_file = self.run_page.seconds_per_file()
        if per_file is not None and not cancelled and code == 0 and self._running_slug:
            self.settings.set_seconds_per_file(self._running_slug, per_file)
            self.batch_page.refresh_summary()
        # další dávka z fronty; na Výsledky se skáče, jen když už nic nečeká
        more = bool(self._queue)
        if more:
            QTimer.singleShot(0, self._launch_next)
        if cancelled or code != 0:
            partial = self._running_out
            if partial is not None and partial.is_file() and state.processed:
                why = tr("zrušení") if cancelled else tr("chybě (kód {code})").format(code=code)
                self.results_page.load(
                    partial,
                    note=tr("Částečný výsledek po {why}: {n} z {total} nahrávek.").format(
                        why=why, n=state.processed, total=state.total
                    ),
                )
                if not more:
                    self.nav.setCurrentRow(PAGE_RESULTS)
            return
        if not state.out:
            return
        out = Path(state.out)
        if out.is_file():
            self.results_page.load(out)
            if not more:
                self.nav.setCurrentRow(PAGE_RESULTS)
        elif out.is_dir():  # jen segmentace nebo přepis: mezivýsledky v pracovní složce
            self.statusBar().showMessage(tr("Mezivýsledky jsou v {path}").format(path=out), 10000)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F5:
            self.env_page.refresh()
        super().keyPressEvent(event)
