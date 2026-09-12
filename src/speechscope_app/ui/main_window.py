"""Hlavní okno: boční menu a stránky. Tady se skládá běh z protokolu."""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from pandas import errors as pd_errors
from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, contract
from ..backend.audio import duration_seconds
from ..backend.command import PrepareRequest, extract_args, segment_args, transcribe_args
from ..backend.diagnostics import build_bundle, default_name
from ..backend.history import RunInfo, mark_orphans, trash_all, trash_run, write_run_file
from ..backend.manifest import Manifest, read_manifest, write_normalized
from ..backend.protocol import Protocol, all_protocols, slugify
from ..backend.results import merge_results
from ..backend.settings import AppSettings
from ..i18n import tr
from . import theme
from .models_dialog import ModelsDownloadDialog
from .models_install_dialog import ModelsInstallDialog
from .pages.batch import BatchPage
from .pages.environment import EnvironmentPage
from .pages.protocols import ProtocolsPage
from .pages.results import ResultsPage
from .pages.run import RunPage
from .pages.welcome import WelcomePage
from .settings_dialog import SettingsDialog
from .widgets.elided_label import ElidedLabel
from .widgets.sidebar_nav import SidebarNav

# Pořadí v nabídce: od každodenního (analýza) k jednorázovému (prostředí).
PAGE_BATCH, PAGE_RUN, PAGE_RESULTS, PAGE_PROTOCOLS, PAGE_ENV = range(5)
PAGE_WELCOME = 5  # uvítání po startu; v zásobníku stránek je, v nabídce ne


@dataclass(slots=True)
class Job:
    """Jedna dávka ve frontě: co spustit, s čím a nad čím."""

    kind: str  # "extract" | "segments" | "transcript"
    proto: Protocol
    inputs: list[Path]
    options: dict = field(default_factory=dict)
    manifest: Manifest | None = None
    language: str = ""
    durations: list[float | None] = field(default_factory=list)  # délky zvuku k inputs
    merge_into: Path | None = None  # znovu chybné: tabulka, do které se řádky vrátí

    def title(self) -> str:
        name = self.proto.display_name
        if self.kind != "extract":
            what = contract.PROVIDER_SHORT.get(self.kind, self.kind)
            name = tr("Jen {what} · {name}").format(what=what, name=name)
        elif self.merge_into is not None:
            name = tr("Znovu chybné · {name}").format(name=name)
        return tr("{name} ({n} nahrávek)").format(name=name, n=len(self.inputs))


def restart_command() -> tuple[str, list[str]]:
    """Program a argumenty pro nové spuštění: zabalené exe, nebo modul při vývoji.

    Přepínače (`--fake`) se předají dál, `--smoke` ne.
    """
    args = [a for a in sys.argv[1:] if a != "--smoke"]
    if getattr(sys, "frozen", False):
        return sys.executable, args
    return sys.executable, ["-m", "speechscope_app.main", *args]


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
        self.welcome_page = WelcomePage()

        self.nav_labels = (
            tr("Analýza"),
            tr("Výpočet"),
            tr("Výsledky"),
            tr("Protokoly"),
            tr("Prostředí"),
        )
        # Protokoly a Prostředí sedí u dolního okraje, nad nimi je mezera.
        self.nav = SidebarNav(self.nav_labels, bottom_from=PAGE_PROTOCOLS)
        self.pages = QStackedWidget()
        for page in (
            self.batch_page,
            self.run_page,
            self.results_page,
            self.protocols_page,
            self.env_page,
            self.welcome_page,
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
        self.brand_sub = ElidedLabel("")  # při větším písmu se zkrátí, nerozšíří panel
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
        menu.addAction(tr("Smazat všechny běhy…"), self.delete_all_runs)
        menu.addSeparator()
        menu.addAction(tr("Konec"), self.close)

        self.env_page.settings_requested.connect(self._open_settings)
        self.env_page.download_requested.connect(self.download_models)
        self.env_page.install_requested.connect(self.install_models)
        self.env_page.diagnostics_requested.connect(self.save_diagnostics)
        self.env_page.report_changed.connect(self.batch_page.set_doctor)
        self.env_page.report_changed.connect(self.welcome_page.set_report)
        self.env_page.models_dir_requested.connect(self.change_models_dir)
        self.env_page.work_root_requested.connect(self.change_work_root)
        self.welcome_page.models_dir_requested.connect(self.change_models_dir)
        self.welcome_page.work_root_requested.connect(self.change_work_root)
        self.welcome_page.install_requested.connect(self.install_models)
        self.welcome_page.download_requested.connect(self.download_models)
        self.welcome_page.analysis_requested.connect(lambda: self.nav.setCurrentRow(PAGE_BATCH))
        self.welcome_page.environment_requested.connect(lambda: self.nav.setCurrentRow(PAGE_ENV))
        self.env_page.report_changed.connect(self.protocols_page.set_doctor)
        self.batch_page.set_stats_lookup(self.settings.seconds_per_file)
        self.batch_page.set_rate_lookup(self.settings.seconds_per_audio_minute)
        self.protocols_page.set_stats_lookup(self.settings.seconds_per_file)
        self.results_page.rerun_requested.connect(self.rerun_failed)
        self.results_page.history.delete_requested.connect(self.delete_run)
        self.run_page.history.delete_requested.connect(self.delete_run)
        self.protocols_page.protocols_changed.connect(self.reload_protocols)
        self.batch_page.run_requested.connect(self._start_batch)
        self.batch_page.prepare_requested.connect(self._start_prepare)
        self.batch_page.save_requested.connect(self.save_protocol)
        self.run_page.finished.connect(self._batch_finished)
        for page in (self.batch_page, self.run_page, self.results_page):
            page.notice.connect(lambda text: self.statusBar().showMessage(text, 6000))
        self.run_page.progress_changed.connect(self._show_progress)
        self.run_page.file_done.connect(self._file_done)
        self.run_page.queue_remove.connect(self.remove_queued)
        self.run_page.queue_clear.connect(self.clear_queue)

        self._apply_settings()
        mark_orphans(self.settings.work_root)  # po pádu nesmí v historii zůstat „běží“
        self.show_welcome()

    def show_welcome(self) -> None:
        """Uvítání při každém startu: žádná záložka, doporučení podle prostředí."""
        recommended = self.start_page()
        self.welcome_page.set_state(
            library_ok=self.library is not None, models_ok=recommended != PAGE_ENV
        )
        self.nav.setCurrentRow(-1)
        self.pages.setCurrentIndex(PAGE_WELCOME)
        if recommended == PAGE_ENV and self.library is not None:
            self.env_page.refresh()  # bez modelů: rovnou zjistit, co chybí

    def _page_shown(self, index: int) -> None:
        if index == PAGE_RESULTS:
            self.results_page.refresh()
        elif index == PAGE_RUN:
            self.run_page.refresh_history()
        elif index == PAGE_ENV:
            self.env_page.refresh_cache()

    def start_page(self) -> int:
        """Kam uvítání doporučí: Analýza; bez knihovny nebo bez modelů Prostředí."""
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
        self.results_page.set_library(self.library)
        self._apply_folders()
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

    def _file_done(self, processed: int, total: int) -> None:
        """Počet hotových do run.json a do historie; tabulku běhu GUI nečte
        (Windows by knihovně zablokovaly přejmenování `.part`)."""
        run_dir = getattr(self, "_running_dir", None)
        if run_dir is None or not run_dir.is_dir():
            return
        try:
            write_run_file(
                run_dir,
                status="running",
                kind=self._running_kind,
                protocol=self._running_name,
                processed=processed,
                total=total,
            )
        except OSError:
            return
        self.run_page.refresh_history()

    def _apply_folders(self) -> None:
        """Složky z nastavení do všech stránek, které je ukazují nebo používají."""
        models_dir, work_root = self.settings.models_dir, self.settings.work_root
        self.results_page.set_work_root(work_root)
        self.run_page.set_work_root(work_root)
        self.batch_page.set_work_root(work_root)
        self.env_page.set_work_dir(work_root / "work")
        self.env_page.set_folders(models_dir, work_root)
        self.welcome_page.set_folders(models_dir, work_root)

    def change_models_dir(self, chosen: Path | None = None) -> None:
        """Složka modelů: dialog (nebo daná cesta v testech), uložit, znovu spojit knihovnu."""
        if chosen is None:
            text = QFileDialog.getExistingDirectory(
                self, tr("Složka s modely"), str(self.settings.models_dir)
            )
            if not text:
                return
            chosen = Path(text)
        self.settings.models_dir = chosen
        self._apply_settings()  # knihovna dostává --models-dir, protokoly se přepočítají
        self.welcome_page.set_state(
            library_ok=self.library is not None, models_ok=self.start_page() != PAGE_ENV
        )
        if self.library is not None:
            self.env_page.refresh()

    def change_work_root(self, chosen: Path | None = None) -> None:
        if chosen is None:
            text = QFileDialog.getExistingDirectory(
                self, tr("Složka výsledků"), str(self.settings.work_root)
            )
            if not text:
                return
            chosen = Path(text)
        self.settings.work_root = chosen
        self._apply_folders()
        self.results_page.refresh()
        self.run_page.refresh_history()

    def _show_progress(self, text: str) -> None:
        """Postup běhu v nabídce a v titulku, ať je vidět i z jiné stránky."""
        self._last_progress = text
        if self._queue:
            text = f"{text} (+{len(self._queue)})" if text else f"+{len(self._queue)}"
        label = self.nav_labels[PAGE_RUN]
        self.nav.item(PAGE_RUN).setText(f"{label} · {text}" if text else label)
        self.setWindowTitle(f"{text} · {self._base_title}" if text else self._base_title)

    def _open_settings(self) -> None:
        language_before = self.settings.ui_language
        scale_before = self.settings.font_scale
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self._apply_settings()
            if self.settings.font_scale != scale_before:
                app = QApplication.instance()
                if app is not None:
                    theme.apply_scale(app, theme.FONT_SCALES.get(self.settings.font_scale, 1.0))
            if self.settings.ui_language != language_before:
                self._offer_restart()

    def _offer_restart(self) -> None:
        """Jazyk se projeví až po novém spuštění; nabídnout ho hned, ne během výpočtu."""
        if self.run_page.runner.running or self._queue:
            QMessageBox.information(
                self,
                tr("SpeechScope"),
                tr("Jazyk aplikace se změní po novém spuštění, až doběhne výpočet."),
            )
            return
        answer = QMessageBox.question(
            self,
            tr("Změna jazyka"),
            tr("Jazyk aplikace se projeví po novém spuštění. Restartovat teď?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.restart()

    def restart(self) -> bool:
        """Spustí novou instanci se stejnými přepínači a tuhle zavře."""
        program, args = restart_command()
        if not QProcess.startDetached(program, args):
            QMessageBox.warning(
                self, tr("SpeechScope"), tr("Novou instanci se nepodařilo spustit.")
            )
            return False
        QTimer.singleShot(0, self.close)
        return True

    def download_models(self) -> None:
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return
        dialog = ModelsDownloadDialog(self.library, self.settings.models_dir, self)
        dialog.exec()
        if dialog.downloaded:
            self.library.clear_cache()
        self.env_page.refresh()  # i po neúspěchu: uvítání a Prostředí mají ukázat pravdu

    def install_models(self, archive: Path | None = None) -> None:
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return
        dialog = ModelsInstallDialog(self.library, self.settings.models_dir, self, archive=archive)
        dialog.exec()
        if dialog.installed:
            self.library.clear_cache()
        self.env_page.refresh()  # i po neúspěchu: uvítání a Prostředí mají ukázat pravdu

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
        path = proto.path or folder / f"{proto.slug()}.yaml"
        existing = {p.name: p for p in all_protocols(folder) if not p.builtin}
        if proto.path is None and proto.name in existing and existing[proto.name].path:
            path = existing[proto.name].path  # přepis stejného jména, ne druhý soubor
        proto.path = None  # do YAML nepatří
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
                durations=self.batch_page.durations_for(list(inputs)),
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

    def rerun_failed(self, run_dir: Path, paths: list[Path]) -> bool:
        """Stejný protokol jen nad nahrávkami s chybou; výsledek se vloží do původní tabulky."""
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return False
        try:
            proto = Protocol.load(run_dir / "protocol.yaml")
        except Exception as exc:  # rozbitý soubor  # noqa: BLE001
            QMessageBox.warning(
                self,
                tr("SpeechScope"),
                tr("Protokol běhu nejde načíst: {error}").format(error=exc),
            )
            return False
        missing = [p for p in paths if not p.is_file()]
        inputs = [p for p in paths if p.is_file()]
        if missing:
            QMessageBox.information(
                self,
                tr("SpeechScope"),
                tr("Tyto nahrávky už neexistují a přeskočí se:\n{names}").format(
                    names="\n".join(str(p) for p in missing[:10])
                ),
            )
        if not inputs:
            return False
        manifest_path = run_dir / "manifest.csv"
        manifest = read_manifest(manifest_path) if manifest_path.is_file() else None
        transcript = proto.config.get("transcript", {}) if isinstance(proto.config, dict) else {}
        self._submit(
            Job(
                kind="extract",
                proto=proto,
                inputs=inputs,
                manifest=manifest,
                language=str(transcript.get("language") or ""),
                durations=[duration_seconds(p) for p in inputs],
                merge_into=run_dir / "features.csv",
            )
        )
        return True

    # --- mazání běhů ---------------------------------------------------------------

    def delete_run(self, info: RunInfo, *, confirm: bool = True) -> bool:
        """Složka běhu do Koše po potvrzení; běžící běh smazat nejde."""
        running_dir = getattr(self, "_running_dir", None)
        if self.run_page.runner.running and running_dir == info.dir:
            self.statusBar().showMessage(tr("Běžící výpočet smazat nejde."), 6000)
            return False
        if confirm:
            when = info.started.strftime("%d.%m. %H:%M") if info.started else info.dir.name
            answer = QMessageBox.question(
                self,
                tr("Smazat běh"),
                tr(
                    "Smazat běh {name} z {when}?\n"
                    "Celá složka s tabulkou výsledků, protokolem a logem půjde do Koše. "
                    "Nahrávek se to netýká."
                ).format(name=info.protocol_name, when=when),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            trash_run(info.dir)
        except OSError as exc:
            self.statusBar().showMessage(
                tr("Běh se nepodařilo smazat (otevřený soubor?): {error}").format(error=exc),
                8000,
            )
            return False
        self._after_runs_deleted({info.dir})
        self.statusBar().showMessage(
            tr("Běh přesunut do Koše: {name}").format(name=info.dir.name), 6000
        )
        return True

    def delete_all_runs(self, *, confirm: bool = True) -> int:
        """Všechny složky běhů do Koše; běžící zůstane, mezivýsledky ve work také."""
        runs = self.results_page.history.all_runs()
        running_dir = getattr(self, "_running_dir", None) if self.run_page.runner.running else None
        candidates = [r for r in runs if r.dir != running_dir and r.status != "running"]
        if not candidates:
            self.statusBar().showMessage(tr("Žádné běhy ke smazání."), 6000)
            return 0
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("Smazat všechny běhy"),
                tr(
                    "Smazat všech {n} běhů ze složky {path}?\n"
                    "Složky půjdou do Koše. Mezivýsledky ve složce work zůstanou, "
                    "ty se uklízejí v Prostředí."
                ).format(n=len(candidates), path=self.settings.work_root),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return 0
        done, failed = trash_all(
            self.settings.work_root, keep={running_dir} if running_dir else None
        )
        self._after_runs_deleted({r.dir for r in candidates if r.dir not in failed})
        if failed:
            self.statusBar().showMessage(
                tr("Smazáno {n} běhů, {k} se nepodařilo (otevřený soubor?).").format(
                    n=done, k=len(failed)
                ),
                8000,
            )
        else:
            self.statusBar().showMessage(tr("Do Koše přesunuto {n} běhů.").format(n=done), 6000)
        return done

    def _after_runs_deleted(self, dirs: set[Path]) -> None:
        """Historie obou stránek znovu; smazaný běh nesmí zůstat otevřený."""
        if self.results_page.current_dir() in dirs:
            self.results_page.clear()
        else:
            self.results_page.refresh()
        if self.run_page.viewing_dir() in dirs:
            self.run_page.clear_view()
        self.run_page.refresh_history()

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
        if job.merge_into is not None:
            suffix = "-znovu"
        self._running_merge = job.merge_into
        run_dir = self.settings.work_root / f"{stamp}_{slug}{suffix}"
        n = 2
        while run_dir.exists():  # dva běhy v téže vteřině (fronta, testy) nesmí sdílet složku
            run_dir = self.settings.work_root / f"{stamp}_{slug}{suffix}-{n}"
            n += 1
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
            expected_total = self._expected_total(slug, job.durations, expected)
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
            expected_total = None

        self._running_dir = run_dir
        self._running_kind = "extract" if job.kind == "extract" else "prepare"
        self._running_name = proto.display_name
        # Stav „běží“ hned: tabulka roste průběžně a bez záznamu by běh vypadal hotový.
        write_run_file(
            run_dir,
            status="running",
            kind=self._running_kind,
            protocol=proto.display_name,
            total=len(inputs),
        )
        self.nav.setCurrentRow(PAGE_RUN)
        self.run_page.start(
            argv,
            title=title,
            log_file=log_file,
            inputs=inputs,
            expected_seconds=expected,
            audio_seconds=job.durations,
            expected_total=expected_total,
        )

    def _expected_total(
        self, slug: str, durations: list[float | None], per_file: float | None
    ) -> float | None:
        """Odhad dávky z minut zvuku (přesnější), doplněný dobou na nahrávku tam,
        kde délka není známá. None, když chybí statistika."""
        rate = self.settings.seconds_per_audio_minute(slug)
        known = [d for d in durations if d]
        if not rate or not known:
            return None
        total = rate / 60 * sum(known)
        unknown = len(durations) - len(known)
        if unknown:
            if per_file is None:
                return None
            total += per_file * unknown
        return total

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
        self.run_page.refresh_history(select=run_dir)
        merged = self._merge_rerun(state)
        per_file = self.run_page.seconds_per_file()
        if per_file is not None and not cancelled and code == 0 and self._running_slug:
            self.settings.set_seconds_per_file(self._running_slug, per_file)
            per_minute = self.run_page.seconds_per_audio_minute()
            if per_minute is not None:
                self.settings.set_seconds_per_audio_minute(self._running_slug, per_minute)
            self.batch_page.refresh_summary()
        # další dávka z fronty; na Výsledky se skáče, jen když už nic nečeká
        more = bool(self._queue)
        if more:
            QTimer.singleShot(0, self._launch_next)
        if merged is not None:
            target, n = merged
            self.results_page.load(
                target,
                note=tr("Znovu spočítané chybné nahrávky: {n} řádků nahrazeno.").format(n=n),
            )
            if not more:
                self.nav.setCurrentRow(PAGE_RESULTS)
            return
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

    def _merge_rerun(self, state: contract.BatchState) -> tuple[Path, int] | None:
        """Po běhu „znovu chybné“ vrátí řádky do původní tabulky; None jinak."""
        target = getattr(self, "_running_merge", None)
        self._running_merge = None
        if target is None or not target.is_file():
            return None
        source = Path(state.out) if state.out else self._running_out
        if source is None or not source.is_file():
            return None
        try:
            n = merge_results(target, source)
        except (OSError, ValueError, pd_errors.ParserError) as exc:
            QMessageBox.warning(
                self,
                tr("SpeechScope"),
                tr("Výsledky se nepodařilo vložit do původní tabulky: {error}").format(error=exc),
            )
            return None
        return target, n

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F5:
            self.env_page.refresh()
        super().keyPressEvent(event)
