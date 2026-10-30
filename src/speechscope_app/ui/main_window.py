"""Hlavní okno: boční menu a stránky. Tady se skládá běh z protokolu."""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from pandas import errors as pd_errors
from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, contract
from ..backend import tasks
from ..backend.assessment_build import PLAN_SUFFIX, AssessmentPlan, patients_text, pending_plans
from ..backend.assessment_build import finish_plan as finish_assessment_plan
from ..backend.assessments import AssessmentStore
from ..backend.audio import duration_seconds
from ..backend.catalog import Catalog
from ..backend.command import PrepareRequest, extract_args, segment_args, transcribe_args
from ..backend.diagnostics import build_bundle, default_name
from ..backend.history import (
    RunGroup,
    RunInfo,
    group_runs,
    list_runs,
    mark_orphans,
    merge_assessment_runs,
    read_run,
    rename_run,
    rename_run_dir,
    retire_assessment_runs,
    trash_all,
    trash_run,
    unique_run_dir,
    write_run_file,
)
from ..backend.library import LibraryError
from ..backend.manifest import Manifest, read_manifest, write_normalized
from ..backend.norm_build import (
    CANCELLED,
    ERROR,
    OK,
    RUNNING,
    NormPlan,
    finish_plan,
    load_plan,
)
from ..backend.norms import all_norms, outdated_features, register_custom_tasks
from ..backend.protocol import Protocol, all_protocols, slugify
from ..backend.results import merge_results
from ..backend.settings import AppSettings
from ..backend.trash import send_to_trash
from ..backend.work import collect_prepare_outputs, work_dir_for
from ..i18n import tr
from . import theme
from .assessment_build_dialog import AssessmentBuildWidget, AssessmentFromRecordingsDialog
from .models_dialog import ModelsDownloadDialog
from .models_install_dialog import ModelsInstallDialog
from .new_norm_dialog import NewNormDialog
from .pages.assessment import AssessmentPage
from .pages.batch import BatchPage
from .pages.environment import EnvironmentPage
from .pages.home import HomePage
from .pages.norms import NormsPage
from .pages.protocols import ProtocolsPage
from .pages.results import ResultsPage
from .pages.run import RunPage
from .settings_dialog import SettingsDialog
from .widgets.elided_label import ElidedLabel
from .widgets.sidebar_nav import SidebarNav

# Pořadí v nabídce: od každodenního (analýza) k jednorázovému (prostředí).
(
    PAGE_HOME,
    PAGE_BATCH,
    PAGE_RUN,
    PAGE_RESULTS,
    PAGE_ASSESSMENT,
    PAGE_PROTOCOLS,
    PAGE_NORMS,
    PAGE_ENV,
) = range(8)


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
    label: str = ""  # vlastní název běhu z Analýzy; prázdné = podle protokolu
    plan_kind: str = ""  # dávka plánu: "norm" (nová norma), "assessment"; prázdné = běžná
    plan: Path | None = None  # klíč plánu (složka normy, soubor plánu hodnocení)
    plan_task: str = ""  # klíč dávky v plánu (`TaskPlan.key`: úloha, nebo `úloha@datum`)
    subject: str = ""  # pacient dávky hodnocení (do run.json jako `patient`)
    # běhy dávky patří pod kartu hodnocení v historii (jeden pacient); dávka s víc
    # pacienty zůstává mezi analýzami, vyšetření na ni jen ukazují
    plan_group: bool = True

    def title(self) -> str:
        name = self.label or self.proto.display_name
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
        tasks.registry().load(settings.tasks_file())  # vlastní úlohy uživatele
        register_custom_tasks(settings.norms_dir())  # a z norem, dřív než se čtou běhy
        self.setWindowTitle(tr("SpeechScope"))
        self.resize(1100, 720)

        self._queue: list[Job] = []
        self._last_progress = ""
        self.catalog = Catalog(settings.catalog_path())  # pacienti, hodnocení, index běhů
        self.env_page = EnvironmentPage()
        self.batch_page = BatchPage()
        self.protocols_page = ProtocolsPage()
        self.run_page = RunPage()
        self.results_page = ResultsPage()
        self.assessment_page = AssessmentPage()
        self.norms_page = NormsPage()
        self.home_page = HomePage()

        self.nav_labels = (
            tr("Domů"),
            tr("Analýza nahrávek"),
            tr("Výpočet"),
            tr("Výsledky"),
            tr("Pacienti"),
            tr("Protokoly"),
            tr("Normy"),
            tr("Prostředí"),
        )
        # Protokoly, Normy a Prostředí sedí u dolního okraje, nad nimi je mezera.
        self.nav = SidebarNav(self.nav_labels, bottom_from=PAGE_PROTOCOLS)
        self.pages = QStackedWidget()
        for page in (
            self.home_page,
            self.batch_page,
            self.run_page,
            self.results_page,
            self.assessment_page,
            self.protocols_page,
            self.norms_page,
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
        self.batch_page.set_check_requester(self.env_page.refresh)
        self.env_page.report_changed.connect(self.home_page.set_report)
        self.env_page.models_dir_requested.connect(self.change_models_dir)
        self.env_page.work_root_requested.connect(self.change_work_root)
        self.home_page.models_dir_requested.connect(self.change_models_dir)
        self.home_page.work_root_requested.connect(self.change_work_root)
        self.home_page.install_requested.connect(self.install_models)
        self.home_page.download_requested.connect(self.download_models)
        self.home_page.analysis_requested.connect(lambda: self.nav.setCurrentRow(PAGE_BATCH))
        self.home_page.assessment_requested.connect(self.new_assessment_from_home)
        self.home_page.runs_requested.connect(self.new_assessment_from_runs)
        self.home_page.norm_requested.connect(self.new_norm_from_home)
        self.home_page.environment_requested.connect(lambda: self.nav.setCurrentRow(PAGE_ENV))
        self.home_page.norms_requested.connect(lambda: self.nav.setCurrentRow(PAGE_NORMS))
        self.home_page.run_requested.connect(lambda: self.nav.setCurrentRow(PAGE_RUN))
        self.env_page.report_changed.connect(self.protocols_page.set_doctor)
        self.batch_page.set_stats_lookup(self.settings.seconds_per_file)
        self.batch_page.set_rate_lookup(self.settings.seconds_per_audio_minute)
        self.protocols_page.set_stats_lookup(self.settings.seconds_per_file)
        self.results_page.rerun_requested.connect(self.rerun_failed)
        self.results_page.history.delete_requested.connect(self.delete_run)
        self.run_page.history.delete_requested.connect(self.delete_run)
        self.results_page.history.rename_requested.connect(self.rename_run)
        self.run_page.history.rename_requested.connect(self.rename_run)
        for history in (self.results_page.history, self.run_page.history):
            history.set_plans_lookup(self._pending_assessment_tasks)
            history.set_filter(self.settings.history_filter)
            history.filter_changed.connect(self._history_filter_changed)
        self.results_page.open_assessment_requested.connect(self.open_assessment)
        self.assessment_page.open_run_requested.connect(self.open_run)
        self.assessment_page.set_orientation(self.settings.spider_orientation)
        self.assessment_page.orientation_changed.connect(self._spider_orientation_changed)
        self.protocols_page.protocols_changed.connect(self.reload_protocols)
        self.batch_page.run_requested.connect(self._start_batch)
        self.batch_page.prepare_requested.connect(self._start_prepare)
        self.batch_page.save_requested.connect(self.save_protocol)
        self.run_page.finished.connect(self._batch_finished)
        for page in (
            self.batch_page,
            self.run_page,
            self.results_page,
            self.assessment_page,
            self.norms_page,
        ):
            page.notice.connect(lambda text: self.statusBar().showMessage(text, 6000))
        self.results_page.assess_requested.connect(self.show_assessment)
        self.norms_page.norms_changed.connect(self.assessment_page.reload_norms)
        self.norms_page.new_norm_requested.connect(self.new_norm)
        self.norms_page.build_requested.connect(self.build_norm)
        self.norms_page.resume_requested.connect(self.resume_norm)
        self.norms_page.discard_requested.connect(self.discard_norm)
        self.norms_page.recompute_requested.connect(self.recompute_norm)
        self.assessment_page.new_from_recordings_requested.connect(
            self.new_assessment_from_recordings
        )
        self.assessment_page.build_requested.connect(lambda key: self.build_plan("assessment", key))
        self.assessment_page.resume_requested.connect(
            lambda key: self.resume_plan("assessment", key)
        )
        self.assessment_page.discard_requested.connect(self.discard_assessment_plan)
        self.assessment_page.recompute_requested.connect(self.recompute_assessment)
        self.run_page.progress_changed.connect(self._show_progress)
        self.run_page.file_done.connect(self._file_done)
        self.run_page.queue_remove.connect(self.remove_queued)
        self.run_page.queue_clear.connect(self.clear_queue)

        self._apply_settings()
        mark_orphans(self.settings.work_root)  # po pádu nesmí v historii zůstat „běží“
        self.show_home()

    def show_home(self) -> None:
        """Domů při každém startu; bez knihovny nebo modelů ukáže První nastavení."""
        recommended = self.start_page()
        self.home_page.set_state(
            library_ok=self.library is not None, models_ok=recommended != PAGE_ENV
        )
        self.nav.setCurrentRow(PAGE_HOME)
        self._refresh_home()
        if self.library is not None:
            # Kontrola prostředí na pozadí hned po startu: Spustit pak nečeká na doctor
            # a lišta jazyků i stav providerů sedí bez návštěvy Prostředí.
            self.env_page.refresh()

    def _page_shown(self, index: int) -> None:
        if index == PAGE_HOME:
            self._refresh_home()
        elif index == PAGE_RESULTS:
            self._merge_finished_groups()
            self.results_page.refresh()
        elif index == PAGE_ASSESSMENT:
            self.assessment_page.refresh()
        elif index == PAGE_NORMS:
            self.norms_page.reload()
        elif index == PAGE_RUN:
            self.run_page.refresh_history()
        elif index == PAGE_ENV:
            self.env_page.refresh_cache()

    def _refresh_home(self) -> None:
        """Stav na Domů: normy, kterým se od spočítání změnila feature."""
        versions = self.norms_page.current_versions()
        names = [
            norm.info.display_name
            for norm in all_norms(self.settings.norms_dir())
            if versions and outdated_features(norm, versions)
        ]
        self.home_page.set_norm_warnings(names)

    def new_assessment_from_home(self) -> None:
        """Karta Hodnocení pacienta: průvodce z nahrávek v kroku 2 na Domů."""
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return
        builder = self.make_assessment_build_widget()
        builder.accepted.connect(lambda: self._start_home_plan(builder))
        builder.rejected.connect(self.home_page.show_cards)
        self.home_page.show_builder(builder)

    def _start_home_plan(self, builder: AssessmentBuildWidget) -> None:
        plan = builder.plan()
        self.home_page.show_cards()  # průvodce pryč dřív, než se přepne na Výpočet
        self.start_plan("assessment", plan)

    def new_assessment_from_runs(self) -> None:
        """Odkaz „…nebo z hotových běhů“ na Domů: dialog nad hotovými běhy, pak Pacienti."""
        self.home_page.show_cards()
        self.nav.setCurrentRow(PAGE_ASSESSMENT)
        self.assessment_page.new_from_runs()

    def new_norm_from_home(self) -> None:
        self.nav.setCurrentRow(PAGE_NORMS)
        self.new_norm()

    def show_assessment(self, run_dir: Path, row: int) -> bool:
        """Z Výsledků: nové hodnocení s nahrávkou řádku; po uložení přepne na Hodnocení."""
        if self.assessment_page.find_recording(run_dir, row) is None:
            self.statusBar().showMessage(
                tr("Nahrávka není v hotovém běhu pacienta (běží, nebo je to běh normy)."), 6000
            )
            return False
        if not self.assessment_page.show_row(run_dir, row):
            return False
        self.nav.setCurrentRow(PAGE_ASSESSMENT)
        return True

    def _pending_assessment_tasks(self) -> dict[str, list[tuple[str, str]]]:
        """Rozpracovaná hodnocení pro historii: id → (úloha, stav dávky)."""
        return {
            plan.id: [(t.task, t.status) for t in plan.tasks]
            for plan in pending_plans(self.settings.assessments_dir())
        }

    def _history_filter_changed(self, kind: str) -> None:
        """Přepínač Analýzy · Hodnocení · Normy je společný oběma historiím a pamatuje se."""
        self.settings.history_filter = kind
        for history in (self.results_page.history, self.run_page.history):
            if history.filter() != kind:
                history.set_filter(kind)

    def _spider_orientation_changed(self, orientation: str) -> None:
        self.settings.spider_orientation = orientation

    def open_assessment(self, assessment_id: str) -> None:
        """Z Výsledků (Otevřít v Hodnocení): stránka Hodnocení s tím hodnocením."""
        self.nav.setCurrentRow(PAGE_ASSESSMENT)
        self.assessment_page.refresh(select=assessment_id)

    def open_run(self, run_dir: Path) -> bool:
        """Z Hodnocení (Otevřít běh ve Výsledcích): karta běhu, u vyšetření jeho úloha."""
        self.nav.setCurrentRow(PAGE_RESULTS)
        if not self.results_page.open_run(run_dir):
            self.statusBar().showMessage(tr("Běh už v historii není."), 6000)
            return False
        return True

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
        self._library_version: str | None = None
        self.env_page.set_library(self.library)
        self.batch_page.set_library(self.library)
        self.batch_page.set_advanced(self.settings.advanced)
        self.protocols_page.set_library(self.library)
        self.results_page.set_library(self.library)
        self.assessment_page.set_library(self.library)
        self.norms_page.set_library(self.library)
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
        self.home_page.set_progress(processed, total)
        try:
            write_run_file(
                run_dir,
                status="running",
                kind=self._running_kind,
                protocol=self._running_name,
                label=getattr(self, "_running_label", ""),
                **self._plan_run_fields(),
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
        self.assessment_page.set_catalog(self.catalog)
        self.assessment_page.set_work_root(work_root)
        self.assessment_page.set_assessments_dir(self.settings.assessments_dir())
        self.assessment_page.set_norms_dir(self.settings.norms_dir())
        self.norms_page.set_norms_dir(self.settings.norms_dir())
        self.run_page.set_work_root(work_root)
        self.batch_page.set_work_root(work_root)
        self.env_page.set_work_dir(work_root / "work")
        self.env_page.set_folders(models_dir, work_root)
        self.home_page.set_folders(models_dir, work_root)

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
        self.home_page.set_state(
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
        if text:
            self.home_page.set_running(
                getattr(self, "_running_label", "") or getattr(self, "_running_name", ""),
                tr("{progress} nahrávek").format(progress=text) if "/" in text else text,
                len(self._queue),
            )
        else:
            self.home_page.clear_running()
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
                label=self.batch_page.run_label(),
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
                label=self.batch_page.run_label(),
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
                label=tr("Znovu chybné · {name}").format(name=origin.display_name)
                if (origin := read_run(run_dir)) is not None
                else "",
            )
        )
        return True

    # --- nová norma -------------------------------------------------------------

    def make_new_norm_dialog(self) -> NewNormDialog:
        combo = self.batch_page.language
        languages = [str(combo.itemData(i)) for i in range(combo.count())]
        return NewNormDialog(
            all_protocols(self.settings.protocols_dir()),
            self.settings.norms_dir(),
            advanced=self.settings.advanced,
            languages=languages,
            language=self.batch_page.language_code() or "cs",
            rate_lookup=self.settings.seconds_per_audio_minute,
            parent=self,
        )

    def new_norm(self) -> None:
        """Průvodce Nová norma; po Spustit dávky po úlohách do fronty."""
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return
        dialog = self.make_new_norm_dialog()
        if dialog.exec():
            self.start_norm(dialog.plan())

    def start_norm(self, plan: NormPlan) -> int:
        return self.start_plan("norm", plan)

    # --- plány dávek (norma) ---------------------------------------------------

    def _plan_load(self, kind: str, key: Path):  # noqa: ANN202
        """Plán podle druhu a klíče; výjimka, když plán mezitím zmizel."""
        if kind == "norm":
            return NormPlan.load(key)
        if kind == "assessment":
            return AssessmentPlan.load(key)
        raise ValueError(tr("Neznámý druh plánu: {kind}").format(kind=kind))

    def _plans_changed(self, kind: str, select: Path | None = None) -> None:
        if kind == "norm":
            self.norms_page.reload(select=select)
        elif kind == "assessment":
            self.assessment_page.refresh(
                select=select.name.removesuffix(PLAN_SUFFIX) if select is not None else None
            )

    def start_plan(self, kind: str, plan) -> int:  # noqa: ANN001
        """Uloží plán a zařadí jeho nespočítané dávky; vrátí, kolik jich zařadil."""
        plan.save()
        queued = {(j.plan, j.plan_task) for j in self._queue if j.plan_kind == kind}
        running = getattr(self, "_running_plan", None) if self.run_page.runner.running else None
        running_key = (running[1], running[2]) if running and running[0] == kind else None
        jobs = [
            self._plan_job(kind, plan, task)
            for task in plan.remaining()
            if (plan.key, task.key) not in queued and (plan.key, task.key) != running_key
        ]
        self._submit_all(jobs)
        self._plans_changed(kind, select=plan.key)
        return len(jobs)

    def _plan_job(self, kind: str, plan, task) -> Job:  # noqa: ANN001
        return Job(
            kind="extract",
            proto=task.proto(),
            inputs=list(task.inputs),
            manifest=plan.manifest_for(task),
            language=plan.language,
            durations=[duration_seconds(p) for p in task.inputs],
            label=plan.title(task),
            plan_kind=kind,
            plan=plan.key,
            plan_task=task.key,
            subject=plan.patient.subject if kind == "assessment" and not plan.multi else "",
            plan_group=not getattr(plan, "multi", False),
        )

    def _submit_all(self, jobs: list[Job]) -> None:
        """Víc dávek naráz (nová norma): jedna otázka na frontu, ne za každou dávku."""
        if not jobs:
            return
        if self.run_page.runner.running:
            answer = QMessageBox.question(
                self,
                tr("Zařadit do fronty"),
                tr(
                    "Právě běží: {running}.\n"
                    "Zařadit {n} dávek do fronty? Spustí se samy, až běžící dávka skončí."
                ).format(running=self._running_name, n=len(jobs)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._queue.extend(jobs)
        else:
            self._queue.extend(jobs[1:])
            self._launch(jobs[0])
        self._show_queue()

    def _running_norm_text(self) -> str:
        """Složka plánu normy pro run.json; Hodnocení takové běhy nenabízí."""
        running = getattr(self, "_running_plan", None)
        return str(running[1]) if running and running[0] == "norm" else ""

    def _plan_run_fields(self) -> dict[str, Any]:
        """Do run.json: `norm` u dávky normy, `assessment` a `patient` u dávky hodnocení,
        verze knihovny a feature, kterými se počítalo."""
        running = getattr(self, "_running_plan", None)
        assessment = ""
        if running and running[0] == "assessment" and getattr(self, "_running_group", True):
            assessment = Path(running[1]).name.removesuffix(PLAN_SUFFIX)
        task = str(running[2]).split("@", 1)[0] if running else ""  # klíč dávky bez data
        return {
            "norm": self._running_norm_text(),
            "assessment": assessment,
            "patient": getattr(self, "_running_subject", "") if assessment else "",
            # dávka vlastní úlohy: knihovna zná jen základní úlohu, historie a index vlastní
            "task": task if tasks.is_custom(task) else "",
            "library_version": self._library_version_text(),
            "feature_versions": dict(getattr(self, "_running_versions", {}) or {}),
        }

    def _library_version_text(self) -> str:
        """Verze knihovny jednou za nastavení (podproces), do run.json a normy."""
        if self._library_version is None:
            version = ""
            if self.library is not None:
                try:
                    version = self.library.version()
                except LibraryError:
                    version = ""
            self._library_version = version
        return self._library_version

    def _feature_versions(self, proto: Protocol) -> dict[str, str]:
        """Verze feature, které dávka počítá, z katalogu knihovny (do run.json).

        Prázdný výběr v protokolu = všechny feature úlohy. Norma si je pak
        opíše a Hodnocení pozná, že se feature od spočítání kontrol změnila."""
        if self.library is None:
            return {}
        try:
            catalog = self.library.features(proto.task)
        except LibraryError:
            return {}
        wanted = set(proto.features)
        return {
            f.name: f.version for f in catalog if f.version and (not wanted or f.name in wanted)
        }

    def _plan_status(self, status: str, run_dir: Path | None):  # noqa: ANN202
        kind, key, task = self._running_plan or ("", None, "")
        if key is None:
            return None
        try:
            plan = self._plan_load(kind, key)
        except (OSError, ValueError, KeyError):
            return None  # plán mezitím zahozený
        plan.set_status(task, status, run_dir)
        return plan

    def _plan_finished(self, status: str, run_dir: Path | None) -> None:
        kind = self._running_plan[0] if self._running_plan else ""
        plan = self._plan_status(status, run_dir)
        self._running_plan = None
        if plan is not None and plan.all_finished and plan.all_ok:
            self._finish_plan(kind, plan)
        self._plans_changed(kind)

    def _finish_plan(self, kind: str, plan) -> bool:  # noqa: ANN001
        if kind == "norm":
            return self._finish_norm(plan)
        if kind == "assessment":
            return self._finish_assessment(plan)
        return False

    def _finish_norm(self, plan: NormPlan) -> bool:
        try:
            norm = finish_plan(plan, self._library_version_text())
        except (ValueError, OSError) as exc:
            self.statusBar().showMessage(
                tr("Normu se nepodařilo sestavit: {error}").format(error=exc), 10000
            )
            return False
        self.statusBar().showMessage(
            tr("Norma {name} je hotová: {n} kontrol.").format(
                name=norm.info.display_name, n=norm.subjects
            ),
            10000,
        )
        self.norms_page.reload(select=plan.folder)
        self.assessment_page.reload_norms()
        return True

    # --- hodnocení z nahrávek -----------------------------------------------------

    def _assessment_build_kwargs(self) -> dict[str, Any]:
        combo = self.batch_page.language
        return {
            "advanced": self.settings.advanced,
            "languages": [str(combo.itemData(i)) for i in range(combo.count())],
            "language": self.batch_page.language_code() or "cs",
            "rate_lookup": self.settings.seconds_per_audio_minute,
        }

    def make_assessment_build_dialog(self) -> AssessmentFromRecordingsDialog:
        return AssessmentFromRecordingsDialog(
            all_protocols(self.settings.protocols_dir()),
            self.settings.assessments_dir(),
            parent=self,
            **self._assessment_build_kwargs(),
        )

    def make_assessment_build_widget(self) -> AssessmentBuildWidget:
        return AssessmentBuildWidget(
            all_protocols(self.settings.protocols_dir()),
            self.settings.assessments_dir(),
            **self._assessment_build_kwargs(),
        )

    def new_assessment_from_recordings(self) -> None:
        """Průvodce Hodnocení z nahrávek; po Spustit dávky po úlohách do fronty."""
        if self.library is None:
            QMessageBox.warning(self, tr("SpeechScope"), tr("Knihovna není nastavená."))
            return
        dialog = self.make_assessment_build_dialog()
        if dialog.exec():
            self.start_plan("assessment", dialog.plan())

    def recompute_assessment(self, assessment_id: str, *, confirm: bool = True) -> int:
        """Hotové hodnocení spočítat znovu současnou knihovnou; vrátí počet dávek.

        Plán má id hodnocení, po poslední dávce `finish_plan` hodnocení přepíše
        (stejné id, nové odkazy na běhy). Dosavadní běhy zůstávají ve Výsledcích,
        jen už ne pod kartou hodnocení (`retire_assessment_runs`)."""
        plan_path = self.settings.assessments_dir() / f"{assessment_id}{PLAN_SUFFIX}"
        if plan_path.exists() or self._plan_busy("assessment", plan_path):
            self.statusBar().showMessage(tr("Hodnocení se už počítá."), 6000)
            return 0
        plan, problems = self.assessment_page.recompute_plan(assessment_id)
        if plan is None:
            self.statusBar().showMessage(
                tr("Hodnocení nejde přepočítat: {why}").format(why="; ".join(problems)), 10000
            )
            return 0
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("Přepočítat hodnocení"),
                tr(
                    "Nahrávky hodnocení {subject} se spočítají znovu současnou knihovnou "
                    "({n} dávek). Hodnocení se po skončení nahradí, dosavadní běhy zůstanou "
                    "ve Výsledcích mezi analýzami."
                ).format(subject=plan.patient.subject, n=len(plan.tasks)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return 0
        retire_assessment_runs(self.settings.work_root, assessment_id)
        return self.start_plan("assessment", plan)

    def _finish_assessment(self, plan: AssessmentPlan) -> bool:
        try:
            assessments = finish_assessment_plan(
                plan, AssessmentStore(self.settings.assessments_dir(), self.catalog)
            )
        except (ValueError, OSError) as exc:
            self.statusBar().showMessage(
                tr("Hodnocení se nepodařilo založit: {error}").format(error=exc), 10000
            )
            return False
        if len(assessments) == 1:
            self.statusBar().showMessage(
                tr("Hodnocení {subject} je hotové.").format(subject=assessments[0].subject), 10000
            )
            self._merge_finished_groups(only=assessments[0].id)
        else:
            self.statusBar().showMessage(
                tr("Založeno {n} vyšetření ({p}).").format(
                    n=len(assessments), p=patients_text(len({a.subject for a in assessments}))
                ),
                10000,
            )
        self.assessment_page.refresh(select=assessments[0].id)
        if not self._queue:
            self._after_batch_page = PAGE_ASSESSMENT  # _batch_finished místo Výsledků
        return True

    def _merge_finished_groups(self, *, only: str | None = None) -> int:
        """Hotová vyšetření s plochými běhy přesune do jedné složky; vrátí, kolik jich sloučil.

        Rozpracované (plán ještě existuje, dávka běží nebo čeká) se nechají. Když
        přesun nejde (otevřený soubor), zůstanou ploché a zkusí se při příštím
        otevření Výsledků."""
        merged = 0
        running = getattr(self, "_running_plan", None) if self.run_page.runner.running else None
        for entry in group_runs(list_runs(self.settings.work_root)):
            if not isinstance(entry, RunGroup) or entry.dir is not None:
                continue
            if only is not None and entry.key != only:
                continue
            plan_path = self.settings.assessments_dir() / f"{entry.key}{PLAN_SUFFIX}"
            if plan_path.exists() or entry.status == "running":
                continue
            if running and running[0] == "assessment" and running[1] == plan_path:
                continue
            if any(j.plan_kind == "assessment" and j.plan == plan_path for j in self._queue):
                continue
            try:
                _group_dir, moved = merge_assessment_runs(self.settings.work_root, entry)
            except OSError as exc:
                self.statusBar().showMessage(
                    tr("Běhy vyšetření {name} zatím nejde sloučit do jedné složky: {error}").format(
                        name=entry.display_name, error=exc
                    ),
                    8000,
                )
                continue
            for old, new in moved:
                self._run_moved(old, new)
            merged += 1
        return merged

    def discard_assessment_plan(self, key: Path, *, confirm: bool = True) -> bool:
        """Rozpracované hodnocení zahodit; spočítané běhy zůstávají ve Výsledcích."""
        if self._plan_busy("assessment", key):
            self.statusBar().showMessage(tr("Dávka hodnocení ještě běží, nejdřív ji zrušte."), 6000)
            return False
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("Zahodit hodnocení"),
                tr("Zahodit rozpracované hodnocení? Spočítané běhy zůstanou ve Výsledcích."),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._drop_queued("assessment", key)
        try:
            key.unlink(missing_ok=True)
        except OSError as exc:
            self.statusBar().showMessage(str(exc), 8000)
            return False
        self.assessment_page.refresh()
        return True

    def _plan_busy(self, kind: str, key: Path) -> bool:
        running = getattr(self, "_running_plan", None)
        return bool(
            self.run_page.runner.running and running and running[0] == kind and running[1] == key
        )

    def _drop_queued(self, kind: str, key: Path) -> None:
        self._queue = [j for j in self._queue if not (j.plan_kind == kind and j.plan == key)]
        self._show_queue()

    def build_norm(self, folder: Path) -> bool:
        return self.build_plan("norm", folder)

    def build_plan(self, kind: str, key: Path) -> bool:
        """Sestavit z hotových dávek (rozpracovaná norma)."""
        if self._plan_busy(kind, key):
            self.statusBar().showMessage(tr("Dávka plánu ještě běží."), 6000)
            return False
        try:
            plan = self._plan_load(kind, key)
        except (OSError, ValueError, KeyError) as exc:
            self.statusBar().showMessage(str(exc), 6000)
            return False
        self._drop_queued(kind, key)
        return self._finish_plan(kind, plan)

    def resume_norm(self, folder: Path) -> int:
        return self.resume_plan("norm", folder)

    def resume_plan(self, kind: str, key: Path) -> int:
        try:
            plan = self._plan_load(kind, key)
        except (OSError, ValueError, KeyError) as exc:
            self.statusBar().showMessage(str(exc), 6000)
            return 0
        queued = self.start_plan(kind, plan)
        if not queued:
            self.statusBar().showMessage(tr("Zbývající dávky už běží nebo čekají ve frontě."), 6000)
        return queued

    def recompute_norm(self, folder: Path, *, confirm: bool = True) -> int:
        """Hotovou normu spočítat znovu současnou knihovnou; vrátí počet zařazených dávek.

        Dávky jdou stejnou cestou jako u nové normy (tytéž protokoly i tabulka
        kontrol), stará norma zůstává použitelná, dokud ji `finish_plan` nepřepíše.
        """
        if self._plan_busy("norm", folder):
            self.statusBar().showMessage(tr("Dávka normy ještě běží."), 6000)
            return 0
        plan = load_plan(folder)
        if plan is None:
            self.statusBar().showMessage(tr("Norma nemá uložené zdroje, přepočítat nejde."), 8000)
            return 0
        missing = plan.missing_inputs()
        if missing:
            self.statusBar().showMessage(
                tr("Chybí {n} nahrávek normy, přepočítat nejde: {first}").format(
                    n=len(missing), first=missing[0]
                ),
                10000,
            )
            return 0
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("Přepočítat normu"),
                tr(
                    "Všechny dávky normy {name} se spočítají znovu současnou knihovnou "
                    "({n} dávek). Nynější norma zůstane použitelná, dokud přepočet "
                    "neskončí, pak se přepíše."
                ).format(name=plan.info.display_name, n=len(plan.tasks)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return 0
        self._drop_queued("norm", folder)
        plan.reset_for_recompute()
        return self.start_plan("norm", plan)

    def discard_norm(self, folder: Path, *, confirm: bool = True) -> bool:
        """Rozpracovanou normu do Koše; spočítané běhy zůstávají ve Výsledcích.

        U normy, která se počítá znovu, jen zruší přepočet a starou normu nechá."""
        if self._plan_busy("norm", folder):
            self.statusBar().showMessage(tr("Dávka normy ještě běží, nejdřív ji zrušte."), 6000)
            return False
        plan = load_plan(folder)
        if plan is not None and plan.recomputing:
            if confirm:
                answer = QMessageBox.question(
                    self,
                    tr("Zrušit přepočet"),
                    tr(
                        "Zrušit přepočet normy {name}? Dosavadní norma zůstane, dávky "
                        "ve frontě se zruší, spočítané běhy zůstanou ve Výsledcích."
                    ).format(name=plan.info.display_name),
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False
            self._drop_queued("norm", folder)
            plan.cancel_recompute()
            self.norms_page.reload(select=folder)
            return True
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("Zahodit normu"),
                tr(
                    "Zahodit rozpracovanou normu? Plán půjde do Koše, spočítané běhy "
                    "zůstanou ve Výsledcích.\n{path}"
                ).format(path=folder),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._drop_queued("norm", folder)
        try:
            send_to_trash(folder)
        except OSError as exc:
            self.statusBar().showMessage(str(exc), 8000)
            return False
        self.norms_page.reload()
        return True

    # --- mazání běhů ---------------------------------------------------------------

    def rename_run(self, info: RunInfo | RunGroup, label: str | None = None) -> bool:
        """Vlastní název běhu (dialog, nebo daný text v testech); prázdný = podle protokolu."""
        if isinstance(info, RunGroup):
            return False  # vyšetření se jmenuje podle pacienta
        if label is None:
            text, ok = QInputDialog.getText(
                self,
                tr("Přejmenovat běh"),
                tr("Název běhu (prázdný = podle protokolu):"),
                text=info.label,
            )
            if not ok:
                return False
            label = text
        old = info.dir
        try:
            rename_run(old, label)
        except OSError as exc:
            self.statusBar().showMessage(
                tr("Běh se nepodařilo přejmenovat: {error}").format(error=exc), 8000
            )
            return False
        running = self.run_page.runner.running and getattr(self, "_running_dir", None) == old
        new = old
        if running:
            self._running_label = label.strip()  # složku nelze: knihovna do ní právě píše
        else:
            try:
                new = rename_run_dir(old, label)
            except OSError as exc:
                # Typicky features.csv otevřené v Excelu: název zůstane aspoň v run.json.
                self.statusBar().showMessage(
                    tr("Název je uložený, složku ale nejde přejmenovat: {error}").format(error=exc),
                    8000,
                )
        if new != old:
            self._run_moved(old, new)
        self.results_page.refresh()
        self.run_page.refresh_history(select=new if self.run_page.viewing_dir() == new else None)
        if self.results_page.current_dir() == new:
            self.results_page.title_label.setText(label.strip() or info.protocol_name)
        if self.run_page.viewing_dir() == new:
            self.run_page.headline.setText(label.strip() or info.protocol_name)
        return True

    def _run_moved(self, old: Path, new: Path) -> None:
        """Po přejmenování složky běhu: všechny cesty, které na ni ukazovaly, na novou."""
        if getattr(self, "_running_dir", None) == old:
            self._running_dir = new
        out = getattr(self, "_running_out", None)
        if out is not None and out.parent == old:
            self._running_out = new / out.name
        for job in self._queue:
            if job.merge_into is not None and job.merge_into.parent == old:
                job.merge_into = new / job.merge_into.name
        self.results_page.relocate(old, new)
        self.run_page.relocate(old, new)
        self.assessment_page.relocate(old, new)

    def delete_run(self, info: RunInfo | RunGroup, *, confirm: bool = True) -> bool:
        """Složka běhu do Koše po potvrzení; běžící běh smazat nejde."""
        if isinstance(info, RunGroup):
            return self._delete_group(info, confirm=confirm)
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

    def _delete_group(self, group: RunGroup, *, confirm: bool = True) -> bool:
        """Všechny běhy vyšetření (nebo jeho složku) do Koše; hodnocení samo zůstává."""
        if group.status == "running":
            self.statusBar().showMessage(tr("Běžící výpočet smazat nejde."), 6000)
            return False
        if confirm:
            when = group.started.strftime("%d.%m. %H:%M") if group.started else ""
            answer = QMessageBox.question(
                self,
                tr("Smazat vyšetření"),
                tr(
                    "Smazat běhy vyšetření {name} z {when}?\n"
                    "Složky {n} běhů s tabulkami, protokoly a logy půjdou do Koše. "
                    "Uložené hodnocení na stránce Hodnocení zůstane, ale bez nahrávek. "
                    "Nahrávek na disku se to netýká."
                ).format(name=group.display_name, when=when, n=len(group.runs)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        targets = [group.dir] if group.dir is not None else [r.dir for r in group.runs]
        failed: list[str] = []
        for target in targets:
            try:
                trash_run(target)
            except OSError as exc:
                failed.append(str(exc))
        self._after_runs_deleted({r.dir for r in group.runs} | set(targets))
        if failed:
            self.statusBar().showMessage(
                tr("Běh se nepodařilo smazat (otevřený soubor?): {error}").format(error=failed[0]),
                8000,
            )
            return False
        self.statusBar().showMessage(
            tr("Vyšetření {name} přesunuto do Koše.").format(name=group.display_name), 6000
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
            self.batch_page.clear_run_label()
            self._show_queue()
            self.statusBar().showMessage(
                tr("Zařazeno do fronty jako {n}.: {name}").format(
                    n=len(self._queue), name=job.title()
                ),
                6000,
            )
            return
        self._launch(job)
        self.batch_page.clear_run_label()

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
        slug = proto.slug()  # klíč statistik; složka se jmenuje podle názvu běhu, je-li
        label = contract.PROVIDER_SHORT.get(job.kind, job.kind)
        suffix = "" if job.kind == "extract" else f"-{slugify(label)}"
        if job.merge_into is not None:
            suffix = "-znovu"
        self._running_merge = job.merge_into
        self._running_plan = (
            (job.plan_kind, job.plan, job.plan_task or proto.task) if job.plan else None
        )
        self._running_group = job.plan_group
        dir_slug = slugify(job.label) if job.label.strip() else slug
        run_dir = unique_run_dir(self.settings.work_root, f"{stamp}_{dir_slug}{suffix}")
        run_dir.mkdir(parents=True, exist_ok=True)
        work_dir = work_dir_for(self.settings.work_root, proto.task)
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
            self._running_versions = self._feature_versions(proto)
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
            self._running_versions = {}
            title = tr("Jen {what} · {name}").format(what=label, name=proto.display_name)
            expected = None
            expected_total = None

        self._running_dir = run_dir
        self._running_kind = "extract" if job.kind == "extract" else "prepare"
        self._running_prepare = "" if job.kind == "extract" else job.kind
        self._running_work_dir = work_dir
        self._running_inputs = list(inputs)
        self._running_name = proto.display_name
        self._running_label = job.label.strip()
        self._running_subject = job.subject
        # Stav „běží“ hned: tabulka roste průběžně a bez záznamu by běh vypadal hotový.
        write_run_file(
            run_dir,
            status="running",
            kind=self._running_kind,
            prepare=self._running_prepare,
            work_dir=str(work_dir),
            protocol=proto.display_name,
            label=self._running_label,
            **self._plan_run_fields(),
            total=len(inputs),
        )
        if self._running_plan is not None:
            self._plan_status(RUNNING, run_dir)
        # karta běhu hned, ať vyšetření neukazuje „hotovo“, dokud běží další úloha
        self.run_page.refresh_history(select=run_dir)
        self.results_page.refresh()
        self.nav.setCurrentRow(PAGE_RUN)
        self.run_page.start(
            argv,
            title=self._running_label or title,
            protocol=title if self._running_label else "",
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

    def _page_after_batch(self) -> int:
        """Kam po poslední dávce: Výsledky, po hotovém hodnocení z nahrávek Hodnocení."""
        page = getattr(self, "_after_batch_page", PAGE_RESULTS)
        self._after_batch_page = PAGE_RESULTS
        return page

    def _batch_finished(self, state: contract.BatchState, code: int, cancelled: bool) -> None:
        self._after_batch_page = PAGE_RESULTS
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
            collected = None
            if self._running_kind == "prepare" and self._running_prepare:
                # Snímek přepisů/segmentace do složky běhu, vyříznutý zvuk tam přesunout.
                collected = collect_prepare_outputs(
                    self._running_work_dir, self._running_prepare, self._running_inputs, run_dir
                )
            write_run_file(
                run_dir,
                status=status,
                kind=self._running_kind,
                prepare=self._running_prepare,
                work_dir=str(self._running_work_dir),
                protocol=self._running_name,
                label=getattr(self, "_running_label", ""),
                **self._plan_run_fields(),
                processed=state.processed,
                total=state.total,
                seconds=self.run_page.elapsed_seconds(),
                out=state.out,
                copied=collected.copied if collected else None,
                moved_cut=collected.moved_cut if collected else None,
            )
        if getattr(self, "_running_plan", None) is not None:
            # stav dávky do plánu dřív, než se karta vyšetření překreslí
            self._plan_finished(
                OK if code == 0 and not cancelled else (CANCELLED if cancelled else ERROR), run_dir
            )
            run_dir = getattr(self, "_running_dir", run_dir)  # po sloučení do složky vyšetření
        self.run_page.refresh_history(select=run_dir)
        self.assessment_page.refresh()
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
                self.nav.setCurrentRow(self._page_after_batch())
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
                    self.nav.setCurrentRow(self._page_after_batch())
            return
        if not state.out:
            return
        # po sloučení vyšetření do jedné složky je tabulka jinde než hlásí knihovna
        out = self._running_out if self._running_out is not None else Path(state.out)
        if out.is_file():
            self.results_page.load(out)
            if not more:
                self.nav.setCurrentRow(self._page_after_batch())
        elif out.is_dir():  # jen segmentace nebo přepis: kopie v běhu, živé ve work
            self.statusBar().showMessage(
                tr(
                    "Kopie mezivýsledků jsou ve složce běhu, výpočet feature je bere z {path}"
                ).format(path=out),
                10000,
            )

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

    def closeEvent(self, event) -> None:  # noqa: N802
        self.env_page.cancel_check()
        super().closeEvent(event)
