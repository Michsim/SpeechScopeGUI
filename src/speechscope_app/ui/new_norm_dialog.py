"""Průvodce Nová norma: složky po úlohách, tabulka kontrol, souhrn a Spustit.

Tři kroky podle mockupu (docs/mockups/normy): 1 jméno, popis a pro každou
úlohu složka s nahrávkami a protokol (přibalený; vlastní jen v rozšířeném
režimu), 2 jedna tabulka kontrol pro všechny úlohy (kontrola z názvu
souboru nebo z manifest.csv ve složce, věk a pohlaví ručně nebo z CSV),
3 souhrn dávek s odhadem času. Výsledek je `NormPlan`; dávky spouští
hlavní okno. Do složek s nahrávkami se nic nezapisuje.

Zdroj „z hotových běhů“ (mockup docs/mockups/normy-porovnani-dimenze): místo
složek se po úlohách vyberou hotové běhy, volitelně jen řádky jedné skupiny
(sloupec metadat = hodnota); kontroly, věk a pohlaví jsou z tabulek běhů a nic
se nepočítá. Týž průvodce s `extend` je Přidat kontroly…: protokoly jsou dané
normou, souhrn ukáže, co v normě už je, a volbu přeskočit / nahradit.

Kdo je kdo (3. 11. 2026, mockup docs/mockups/norma-manifest): kontrola jen
z manifestu (ve složce úlohy, nebo jeden pro všechny úlohy přes Načíst manifest…
se sloupcem úlohy), nahrávky bez pacienta jsou v oddílu Bez kontroly k doplnění
nebo vynechání; odhad z názvu souboru jen jako výslovná nouzová volba
(`from_names`), norma si zdroj zapíše (`NormInfo.controls_from`).
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend import meta
from ..backend import tasks as tasks_mod
from ..backend.assessment_build import write_manifest_template
from ..backend.assessments import patient_key
from ..backend.audio import format_duration as audio_duration
from ..backend.history import KIND_ASSESSMENT, RunInfo
from ..backend.norm_build import (
    FROM_MANIFEST,
    FROM_MANUAL,
    FROM_NAME,
    SUBJECT_RULES,
    Control,
    Extension,
    FolderScan,
    ManifestSource,
    NormPlan,
    RunsScan,
    TaskPlan,
    assign_controls,
    controls_from_scan,
    default_protocol,
    manifest_source,
    plan_from_runs,
    read_controls_csv,
    rekey_manifest,
    run_columns,
    runs_problems,
    scan_folder,
    scan_runs,
    subject_from_name,
    tasks_from_runs,
    with_language,
)
from ..backend.norms import RECOMMENDED_CONTROLS, NormInfo, unique_norm_dir
from ..backend.protocol import Protocol
from ..i18n import tr
from . import theme
from .custom_task_dialog import CustomTaskDialog
from .widgets.clean_table import (
    card,
    cell,
    fit_height,
    fit_widget_column,
    right_header,
    set_pill,
    set_text,
    style_table,
)
from .widgets.inputs import Segmented
from .widgets.status_header import Pill
from .widgets.task_rows import SummaryBar, TaskRowWidget

FOLDERS, RUNS = "folders", "runs"  # zdroj normy v kroku 1
ALL_ROWS = ""  # filtr skupiny vypnutý

SEX_CHOICES = (("", "–"), (meta.FEMALE, "žena"), (meta.MALE, "muž"))
CSV_FILTER = "CSV (*.csv)"


def format_duration(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return tr("{m} min").format(m=minutes)
    return tr("{h} h {m} min").format(h=minutes // 60, m=minutes % 60)


def _scrolled(page: QWidget) -> QScrollArea:
    """Krok průvodce v posuvné ploše bez rámečku (malá minimální výška okna)."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setStyleSheet("QScrollArea { background: transparent; }")
    page.setObjectName("wizard_page")
    page.setStyleSheet("#wizard_page { background: transparent; }")
    area.setWidget(page)
    return area


def _run_title(run: RunInfo) -> str:
    """Název běhu s datem, aby šly rozlišit běhy téhož protokolu."""
    when = f"{run.started:%d. %m. %Y}" if run.started else ""
    return f"{run.display_name} ({when})" if when else run.display_name


class TaskRow:
    """Řádek kroku 1: úloha, složka, protokol, počet nahrávek.

    Vzhled je sdílený řádek úlohy (`widgets/task_rows.py`, podoba 1 z mockupu
    31. 10. 2026, stejně jako krok 2 Analýzy); `widget` jde do rozvržení průvodce.
    Vlastní úloha (`custom`) má pod názvem, jako která úloha knihovny se počítá,
    protokoly základní úlohy a výchozí protokol z vlastní úlohy; jde ji odebrat.
    """

    def __init__(
        self,
        task: str,
        protocols: list[Protocol],
        advanced: bool,
        custom: tasks_mod.CustomTask | None = None,
    ) -> None:
        self.task = task
        self.custom = custom
        base = custom.base if custom is not None else task
        self.widget = TaskRowWidget(
            task,
            title=tasks_mod.label(task) if custom is None else custom.name,
            folder=True,
            removable=custom is not None,
        )
        self.label = self.widget.label
        self.name_box = self.widget.name_box
        self.sub = self.widget.sub
        self._custom_sub = (
            tr("vlastní · jako {task}").format(task=contract.TASK_SHORT.get(base, base))
            if custom is not None
            else ""
        )
        self.sub.setText(self._custom_sub)
        assert self.widget.folder is not None and self.widget.browse is not None
        self.folder = self.widget.folder
        self.placeholder = (
            self.folder.placeholderText()
        )  # bez manifestu se úloha bez složky vynechá
        self.browse = self.widget.browse
        self.remove = self.widget.remove
        self.protocol = self.widget.protocol
        choices = [p for p in protocols if p.task == base and (p.builtin or advanced)]
        default = default_protocol(base, protocols)
        if custom is not None and custom.protocol:
            default = next((p for p in protocols if p.name == custom.protocol), default)
        self.protocol.set_protocols(
            choices,
            current=default.name if default is not None else "",
            subs={p.name: tr("přibalený") if p.builtin else tr("vlastní") for p in choices},
        )
        self.protocol.setEnabled(advanced and self.protocol.count() > 1)
        self.count = self.widget.count
        self.count.setText("–")
        self.scan = FolderScan([])
        self.refresh_look()

    def current_protocol(self) -> Protocol | None:
        data = self.protocol.currentData()
        return data if isinstance(data, Protocol) else None

    def refresh_look(self) -> None:
        """Pod názvem počet a délka nahrávek; bez složky šedý řádek (úloha se vynechá)."""
        n = len(self.scan.recordings)
        if n:
            text = tr("{n} nahrávek · {audio}").format(
                n=n, audio=audio_duration(self.scan.audio_seconds)
            )
        elif self.folder.text().strip():
            text = tr("žádné nahrávky")
        else:
            text = tr("bez složky")
        self.sub.setText(f"{self._custom_sub} · {text}" if self._custom_sub else text)
        self.widget.set_off_look(not n)


class NewNormDialog(QDialog):
    def __init__(
        self,
        protocols: list[Protocol],
        norms_dir: Path,
        *,
        advanced: bool = False,
        languages: list[str] | None = None,
        language: str = "cs",
        rate_lookup: Callable[[str], float | None] = lambda _slug: None,
        runs: list[RunInfo] | None = None,
        extend: NormPlan | None = None,
        patients: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._extend = extend
        self._patients = set(patients or ())  # klíče pacientů ze stránky Pacienti (pojistka)
        self.setWindowTitle(
            tr("Přidat kontroly · {name}").format(name=extend.info.display_name)
            if extend is not None
            else tr("Nová norma")
        )
        self.resize(980, 700)
        self._norms_dir = norms_dir
        self._rate = rate_lookup
        self._files: dict[Path, str] = {}
        self._controls: dict[str, Control] = {}
        self._filling = False
        self._source = FOLDERS
        self._all_runs = list(runs or [])
        self._chosen: dict[str, list[RunInfo]] = {}  # úloha → vybrané běhy
        self._scan = RunsScan()
        self._existing = set(extend.existing_pairs()) if extend is not None else set()
        self._columns: dict[str, list[str]] = {}  # sloupce metadat vybraných běhů
        self._shared: ManifestSource | None = None  # jeden manifest pro všechny úlohy
        self._assigned: dict[Path, str] = {}  # ručně doplněná kontrola nahrávky
        self._excluded: set[Path] = set()  # nahrávky bez kontroly, které se vynechají
        self._loose: list[tuple[str, Path, str]] = []  # nahrávky bez kontroly v manifestu
        self._sources: dict[str, set[str]] = {}  # kontrola -> odkud (manifest, name, manual)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)
        steps = QHBoxLayout()
        steps.setSpacing(8)
        # Dva kroky (31. 10. 2026, dřív tři se Souhrnem): souhrn je v patičce, varování
        # pod tabulkou kontrol. Vpravo volba zdroje z nahrávek / z hotových běhů.
        self.step_pills = [
            Pill(tr("1 Zdroj a úlohy"), "neutral"),
            Pill(tr("2 Kontroly"), "neutral"),
        ]
        for i, pill in enumerate(self.step_pills):
            if i:
                arrow = QLabel("›")
                arrow.setObjectName("muted")
                steps.addWidget(arrow)
            steps.addWidget(pill)
        steps.addStretch(1)
        self._steps_row = steps
        outer.addLayout(steps)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        # kroky v posuvné ploše: jinak by obsah kroku 1 (karty, formulář, tabulka běhů)
        # vynutil minimální výšku okna přes obrazovku (QWindowsWindow::setGeometry)
        self.stack.addWidget(
            _scrolled(self._build_sources(protocols, advanced, languages or [], language))
        )
        self.stack.addWidget(self._build_controls())

        foot = QFrame()
        foot.setObjectName("card")
        footer = QHBoxLayout(foot)
        footer.setContentsMargins(10, 8, 10, 8)
        footer.setSpacing(12)
        self.back_btn = QPushButton("")
        self.back_btn.clicked.connect(self.go_back)
        footer.addWidget(self.back_btn)
        self.summary = SummaryBar()
        self.summary.setStyleSheet("QFrame#summarybar { background: transparent; border: none; }")
        footer.addWidget(self.summary, 1)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        footer.addWidget(self.hint, 1)
        self.next_btn = QPushButton("")
        theme.set_role(self.next_btn, "primary")
        self.next_btn.clicked.connect(self.go_next)
        footer.addWidget(self.next_btn)
        outer.addWidget(foot)
        self._show_step(0)

    # --- krok 1: nahrávky ---------------------------------------------------------

    def _build_sources(
        self, protocols: list[Protocol], advanced: bool, languages: list[str], language: str
    ) -> QWidget:

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        # Zdroj: přepínač v řádku kroků (dřív dvě velké karty), popis v tooltipu.
        self.folders_card = QPushButton(tr("z nahrávek"))
        self.folders_card.setCheckable(True)
        self.folders_card.setToolTip(
            tr("Složka na úlohu, nahrávky se spočítají jako dávky ve frontě.")
        )
        self.runs_card = QPushButton(tr("z hotových běhů"))
        self.runs_card.setCheckable(True)
        self.runs_card.setToolTip(
            tr("Použije tabulky běhů, které už jsou spočítané. Nic se nepočítá.")
            if self._all_runs
            else tr("Zatím žádný hotový běh.")
        )
        self.runs_card.setEnabled(bool(self._all_runs))
        self.source_group = QButtonGroup(self)
        self.source_group.setExclusive(True)
        self.source_group.addButton(self.folders_card)
        self.source_group.addButton(self.runs_card)
        self.folders_card.setChecked(True)
        self.runs_card.toggled.connect(lambda on: self.set_source(RUNS if on else FOLDERS))
        for btn in (self.folders_card, self.runs_card):
            self._steps_row.addWidget(btn)
        # O normě: název velký, zdroj a podmínky menší vedle sebe (nepovinné)
        self.about_box = QFrame()
        self.about_box.setObjectName("card")
        about = QVBoxLayout(self.about_box)
        about.setContentsMargins(14, 12, 14, 12)
        about.setSpacing(8)
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("Název normy, třeba Zdravé kontroly SAMI 2026"))
        name_font = self.name.font()
        name_font.setPointSizeF(name_font.pointSizeF() * 1.15)
        self.name.setFont(name_font)
        self.name.textChanged.connect(self._update_buttons)
        about.addWidget(self.name)
        about_row = QHBoxLayout()
        about_row.setSpacing(8)
        self.source = QLineEdit()
        self.source.setPlaceholderText(tr("Odkud (pracoviště, studie) · nepovinné"))
        self.conditions = QLineEdit()
        self.conditions.setPlaceholderText(tr("Podmínky (místnost, mikrofon) · nepovinné"))
        self.conditions.setToolTip(tr("Pomůže posoudit, k čemu normu použít."))
        about_row.addWidget(self.source, 1)
        about_row.addWidget(self.conditions, 1)
        about.addLayout(about_row)
        layout.addWidget(self.about_box)
        self.language = QComboBox()
        for code in languages or [language]:
            self.language.addItem(contract.language_label(code), code)
        idx = self.language.findData(language)
        self.language.setCurrentIndex(max(idx, 0))
        if self._extend is not None:  # norma už má jméno, popis i jazyk
            self.about_box.hide()
            self.name.blockSignals(True)  # tlačítka ještě nejsou
            self.name.setText(self._extend.info.name)
            self.name.blockSignals(False)
            idx = self.language.findData(self._extend.info.language)
            if idx >= 0:
                self.language.setCurrentIndex(idx)

        self.folders_box = QWidget()
        folders_layout = QVBoxLayout(self.folders_box)
        folders_layout.setContentsMargins(0, 0, 0, 0)
        folders_layout.setSpacing(12)
        folders_layout.addWidget(self._build_manifest_bar())
        tasks_head = QHBoxLayout()
        tasks_head.setSpacing(10)
        tasks_title = QLabel(tr("Úlohy"))
        tasks_title.setObjectName("card_title")
        tasks_head.addWidget(tasks_title)
        tasks_hint = QLabel(tr("složka s nahrávkami kontrol pro každou úlohu"))
        tasks_hint.setObjectName("muted")
        tasks_head.addWidget(tasks_hint)
        info = QLabel("ⓘ")
        info.setObjectName("muted")
        info.setToolTip(
            tr(
                "Protokol je přibalený pro danou úlohu; jiný jde vybrat jen v rozšířeném "
                "režimu. Vlastní úloha má svůj výchozí protokol a počítá se jako jedna z úloh "
                "knihovny; norma si ji zapíše, takže ji Hodnocení nabídne pacientům. Norma si "
                "zapíše protokol i verzi knihovny. Do složek s nahrávkami se nic nezapisuje."
            )
            if self._extend is None
            else tr(
                "Přidané nahrávky se spočítají protokolem, kterým je spočítaná norma. "
                "Do složek s nahrávkami se nic nezapisuje."
            )
        )
        tasks_head.addWidget(info)
        tasks_head.addStretch(1)
        self.language_label = QLabel(tr("Jazyk"))
        self.language_label.setObjectName("muted")
        tasks_head.addWidget(self.language_label)
        tasks_head.addWidget(self.language)
        if self._extend is not None:
            self.language_label.hide()
            self.language.hide()
        folders_layout.addLayout(tasks_head)
        # řádky úloh jako na Analýze (widgets/task_rows.py), pod sebou
        rows_box = QWidget()
        self.task_rows_layout = QVBoxLayout(rows_box)
        self.task_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.task_rows_layout.setSpacing(6)
        self._protocols = protocols
        self._advanced = advanced
        self.rows: dict[str, TaskRow] = {}
        for task in contract.TASKS:
            self._add_row(TaskRow(task, protocols, advanced))
        folders_layout.addWidget(rows_box)
        options = QHBoxLayout()
        self.recursive = QCheckBox(tr("i podsložky"))
        self.recursive.toggled.connect(self._rescan_all)
        options.addWidget(self.recursive)
        options.addStretch(1)
        self.add_task_btn = QPushButton(tr("+ Vlastní úloha…"))
        self.add_task_btn.setFlat(True)
        self.add_task_btn.setToolTip(
            tr("Další úloha pod vlastním jménem, počítá se jako jedna z úloh knihovny.")
        )
        self.add_task_btn.clicked.connect(lambda: self._ask_custom_task())
        options.addWidget(self.add_task_btn)
        options.addStretch(1)
        folders_layout.addLayout(options)
        layout.addWidget(self.folders_box)
        if self._extend is not None:
            self.add_task_btn.hide()
            for task, row in self.rows.items():
                proto = self._extend.protocol_for(task)
                if proto is not None:
                    self.set_protocol(task, str(proto.get("name", "")))
                row.protocol.setEnabled(False)

        layout.addWidget(self._build_runs_box())
        layout.addStretch(1)
        return page

    def _build_manifest_bar(self) -> QFrame:
        """Jeden manifest pro všechny úlohy (sloupec úloha rozdělí nahrávky do řádků)."""
        bar = QFrame()
        bar.setObjectName("card")
        theme.set_role(bar, "accent")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        title = QLabel(tr("Manifest"))
        title.setObjectName("card_title")
        row.addWidget(title)
        self.manifest_path = QLabel(tr("žádný · bere se manifest.csv ve složce úlohy"))
        self.manifest_path.setObjectName("muted")
        row.addWidget(self.manifest_path, 1)
        self.manifest_pill = QLabel("")
        self.manifest_pill.setObjectName("pill")
        self.manifest_pill.hide()
        row.addWidget(self.manifest_pill)
        self.manifest_btn = QPushButton(tr("Načíst manifest…"))
        self.manifest_btn.clicked.connect(lambda: self._ask_manifest())
        row.addWidget(self.manifest_btn)
        self.manifest_clear = QPushButton(tr("Nepoužít"))
        self.manifest_clear.clicked.connect(lambda: self.clear_manifest())
        self.manifest_clear.hide()
        row.addWidget(self.manifest_clear)
        layout.addLayout(row)
        self.manifest_hint = QLabel(
            tr(
                "Jeden soubor pro všechny úlohy: sloupce path, pacient, task, věk, pohlaví. "
                "Úloha rozdělí nahrávky do řádků níže; bez úlohy jen doplní údaje k nahrávkám "
                "ve složkách."
            )
        )
        self.manifest_hint.setObjectName("muted")
        self.manifest_hint.setWordWrap(True)
        layout.addWidget(self.manifest_hint)
        return bar

    def _ask_manifest(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, tr("Manifest kontrol"), "", CSV_FILTER)
        if source:
            self.load_manifest(Path(source))

    def load_manifest(self, path: Path) -> bool:
        """Manifest odkudkoli pro všechny úlohy; False, když se nedá použít."""
        source = manifest_source(path)
        if not source.manifest.rows:
            QMessageBox.warning(
                self,
                tr("SpeechScope"),
                "\n".join(source.manifest.problems) or tr("Manifest neobsahuje žádný řádek."),
            )
            return False
        self._shared = source
        for key in source.scans:
            custom = tasks_mod.registry().get(key) if key not in self.rows else None
            if isinstance(custom, tasks_mod.CustomTask):
                self.add_custom_task(custom)
        self._update_manifest_bar()
        self._rescan_all()
        if self.step() == 1:
            self._regroup()
        return True

    def clear_manifest(self) -> None:
        self._shared = None
        self._update_manifest_bar()
        self._rescan_all()
        if self.step() == 1:
            self._regroup()

    def shared_manifest(self) -> ManifestSource | None:
        return self._shared

    def _tasks_from_manifest(self) -> bool:
        """Nahrávky po úlohách dává manifest (má sloupec úlohy), ne složky."""
        return self._shared is not None and bool(self._shared.scans)

    def _update_manifest_bar(self) -> None:
        shared = self._shared
        self.manifest_clear.setVisible(shared is not None)
        self.manifest_btn.setText(tr("Změnit…") if shared is not None else tr("Načíst manifest…"))
        if shared is None:
            self.manifest_path.setText(tr("žádný · bere se manifest.csv ve složce úlohy"))
            self.manifest_path.setToolTip("")
            self.manifest_pill.hide()
            return
        self.manifest_path.setText(shared.manifest.path.name)
        self.manifest_path.setToolTip(str(shared.manifest.path))
        if shared.scans:
            text = tr("✓ {n} nahrávek · {k} kontrol").format(n=shared.recordings, k=shared.subjects)
        else:
            text = tr("bez úlohy: jen údaje k nahrávkám ve složkách")
        if shared.missing:
            text += " · " + tr("{n} souborů nenalezeno").format(n=shared.missing)
        self.manifest_pill.setText(text)
        theme.set_role(self.manifest_pill, "warn" if shared.missing else "ok")
        self.manifest_pill.show()

    def _build_runs_box(self) -> QWidget:
        """Zdroj z hotových běhů: běhy po úlohách, filtr skupiny, varování."""
        self.runs_box = QWidget()
        box = QVBoxLayout(self.runs_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(tr("Jen řádky, kde")))
        self.group_column = QComboBox()
        self.group_column.addItem(tr("(všechny řádky)"), ALL_ROWS)
        self.group_column.currentIndexChanged.connect(lambda _i: self._group_column_changed())
        filter_row.addWidget(self.group_column)
        filter_row.addWidget(QLabel("="))
        self.group_value = QComboBox()
        self.group_value.setEnabled(False)
        self.group_value.currentIndexChanged.connect(lambda _i: self._refresh_runs())
        filter_row.addWidget(self.group_value)
        self.group_count = QLabel("")
        self.group_count.setObjectName("muted")
        filter_row.addWidget(self.group_count)
        filter_row.addStretch(1)
        box.addLayout(filter_row)

        self.run_tasks = list(contract.TASKS)
        for run in self._all_runs:
            if run.task and run.task not in self.run_tasks:
                self.run_tasks.append(run.task)  # vlastní úloha z běhu
        self.runs_table = QTableWidget()
        style_table(
            self.runs_table,
            [tr("úloha"), tr("běhy"), tr("protokol"), tr("nahrávek"), tr("kontrol"), ""],
            row_height=44,  # místo na tlačítko
        )
        right_header(self.runs_table, 3, 4)
        header = self.runs_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.runs_table.setRowCount(len(self.run_tasks))
        for r, task in enumerate(self.run_tasks):
            pick = QPushButton(tr("Vybrat…"))
            pick.setEnabled(any(run.task == task for run in self._all_runs))
            pick.clicked.connect(lambda _=False, t=task: self._pick_runs(t))
            # obal tlačítka nesmí být průhledný pro myš (výchozí u štítků), jinak klik propadne
            self.runs_table.setCellWidget(r, 5, cell(pick, transparent=False))
        fit_widget_column(self.runs_table, 5)
        box.addWidget(card(self.runs_table))
        self._fill_runs_table()
        self.runs_warning = QLabel("")
        self.runs_warning.setObjectName("card")
        theme.set_role(self.runs_warning, "warn")
        self.runs_warning.setWordWrap(True)
        self.runs_warning.setContentsMargins(12, 8, 12, 8)
        self.runs_warning.hide()
        box.addWidget(self.runs_warning)
        note = QLabel(
            tr(
                "Kontroly, věk a pohlaví se vezmou z metadat v tabulkách běhů; co chybí, "
                "doplníte v dalším kroku. Táž nahrávka ve dvou bězích úlohy se vezme "
                "z novějšího. Norma si zapíše zdrojové nahrávky, takže ji půjde přepočítat."
            )
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        box.addWidget(note)
        self.runs_box.hide()
        return self.runs_box

    # --- zdroj z hotových běhů -------------------------------------------------------

    def source(self) -> str:
        return self._source

    def set_source(self, source: str) -> None:
        """Z nahrávek (složky) nebo z hotových běhů."""
        if source == RUNS and not self._all_runs:
            return
        self._source = source
        (self.runs_card if source == RUNS else self.folders_card).setChecked(True)
        self.folders_box.setVisible(source == FOLDERS)
        self.runs_box.setVisible(source == RUNS)
        self._update_buttons()

    def runs_for(self, task: str) -> list[RunInfo]:
        """Hotové běhy, které jde pro úlohu vybrat (nejnovější napřed)."""
        found = [r for r in self._all_runs if r.task == task]
        return sorted(found, key=lambda r: r.started or dt.datetime.min, reverse=True)

    def _pick_runs(self, task: str) -> None:
        dialog = PickRunsDialog(task, self.runs_for(task), self._chosen.get(task, []), self)
        if dialog.exec():
            self.set_runs(task, dialog.chosen())

    def set_runs(self, task: str, runs: list[RunInfo]) -> None:
        """Vybrané běhy úlohy (prázdné = úloha se vynechá)."""
        if runs:
            self._chosen[task] = list(runs)
        else:
            self._chosen.pop(task, None)
        self._refill_group_columns()  # obnoví i tabulku přes _refresh_runs

    def _refresh_runs(self) -> None:
        """Řádky vybraných běhů znovu (po výběru běhů nebo změně filtru) a tabulka."""
        column, value = self.group_filter()
        self._scan = scan_runs(self._chosen, column, value, self._rule())
        total = len(self._scan.rows) + self._scan.outside
        self.group_count.setText(
            tr("({n} z {total})").format(n=len(self._scan.rows), total=total) if column else ""
        )
        self._fill_runs_table()
        self._update_runs_warning()
        self._update_buttons()

    def _fill_runs_table(self) -> None:
        by_task = self._scan.by_task()
        table = self.runs_table
        for r, task in enumerate(self.run_tasks):
            runs = self._chosen.get(task, [])
            set_text(table, r, 0, self._task_name(task), bold=True)
            if not runs:
                set_text(table, r, 1, tr("nevybráno, úloha se vynechá"), muted=True)
                set_text(table, r, 2, "")
                set_text(table, r, 3, "–", muted=True, right=True)
                set_text(table, r, 4, "–", muted=True, right=True)
                continue
            titles = " · ".join(_run_title(run) for run in runs)
            set_text(table, r, 1, titles, tooltip=titles)
            names = sorted({run.protocol_name for run in runs if run.protocol_name})
            if len(names) > 1:
                set_pill(
                    table, r, 2, tr("{n} protokoly").format(n=len(names)), "warn",
                    tooltip=", ".join(names),
                )  # fmt: skip
            else:
                old = table.cellWidget(r, 2)
                if old is not None:
                    old.hide()
                    table.removeCellWidget(r, 2)
                set_text(table, r, 2, names[0] if names else "", muted=True)
            rows = by_task.get(task, [])
            set_text(table, r, 3, str(len(rows)), right=True)
            set_text(table, r, 4, str(len({row.subject for row in rows})), right=True)
        fit_height(table)

    def chosen_runs(self) -> dict[str, list[RunInfo]]:
        return {t: list(r) for t, r in self._chosen.items()}

    def _refill_group_columns(self) -> None:
        self._columns = run_columns([r for runs in self._chosen.values() for r in runs])
        current = self.group_column.currentData()
        self.group_column.blockSignals(True)
        self.group_column.clear()
        self.group_column.addItem(tr("(všechny řádky)"), ALL_ROWS)
        for column in self._columns:
            self.group_column.addItem(column, column)
        idx = self.group_column.findData(current)
        self.group_column.setCurrentIndex(max(idx, 0))
        self.group_column.blockSignals(False)
        self._group_column_changed()

    def _group_column_changed(self) -> None:
        column = str(self.group_column.currentData() or "")
        current = self.group_value.currentText()
        self.group_value.blockSignals(True)
        self.group_value.clear()
        for value in getattr(self, "_columns", {}).get(column, []):
            self.group_value.addItem(value, value)
        idx = self.group_value.findText(current)
        self.group_value.setCurrentIndex(max(idx, 0))
        self.group_value.setEnabled(bool(column))
        self.group_value.blockSignals(False)
        self._refresh_runs()

    def set_group_filter(self, column: str, value: str) -> bool:
        """Jen řádky, kde `column` = `value`; prázdný sloupec filtr vypne."""
        idx = self.group_column.findData(column or ALL_ROWS)
        if idx < 0:
            return False
        self.group_column.setCurrentIndex(idx)
        if not column:
            return True
        idx = self.group_value.findText(value)
        if idx < 0:
            return False
        self.group_value.setCurrentIndex(idx)
        return True

    def group_filter(self) -> tuple[str, str]:
        column = str(self.group_column.currentData() or "")
        return (column, self.group_value.currentText()) if column else ("", "")

    def _update_runs_warning(self) -> None:
        problems = runs_problems(self._chosen)
        if self._extend is not None:  # jiný protokol než norma
            for task, runs in self._chosen.items():
                proto = self._extend.protocol_for(task)
                wanted = str(proto.get("name", "")) if proto else ""
                other = sorted(
                    {r.protocol_name for r in runs if wanted and r.protocol_name != wanted}
                )
                if other:
                    problems.append(
                        tr("{task}: norma je spočítaná protokolem {norm}, běhy {runs}.").format(
                            task=tasks_mod.label(task), norm=wanted, runs=", ".join(other)
                        )
                    )
        self.runs_warning.setText("\n".join(problems))
        self.runs_warning.setVisible(bool(problems))

    def _add_row(self, row: TaskRow) -> None:
        row.folder.editingFinished.connect(lambda row=row: self._rescan(row))
        row.browse.clicked.connect(lambda _=False, row=row: self._browse(row))
        row.remove.clicked.connect(lambda _=False, row=row: self.remove_custom_task(row.task))
        self.task_rows_layout.addWidget(row.widget)
        self.rows[row.task] = row

    def _ask_custom_task(self) -> None:
        dialog = CustomTaskDialog(self._protocols, advanced=self._advanced, parent=self)
        if dialog.exec():
            self.add_custom_task(dialog.task())

    def add_custom_task(self, task: tasks_mod.CustomTask) -> TaskRow:
        """Řádek vlastní úlohy v kroku 1 (klíč musí být volný); vrátí ho kvůli složce."""
        if task.key in self.rows:
            return self.rows[task.key]
        row = TaskRow(task.key, self._protocols, self._advanced, custom=task)
        self._add_row(row)
        self._update_buttons()
        return row

    def remove_custom_task(self, key: str) -> None:
        row = self.rows.get(key)
        if row is None or row.custom is None:
            return
        self.task_rows_layout.removeWidget(row.widget)
        row.widget.hide()
        row.widget.deleteLater()
        del self.rows[key]
        self._update_buttons()

    def custom_tasks(self) -> list[tasks_mod.CustomTask]:
        return [r.custom for r in self.rows.values() if r.custom is not None]

    def _browse(self, row: TaskRow) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, tr("Složka s nahrávkami: {task}").format(task=row.label.text()), row.folder.text()
        )
        if folder:
            self.set_folder(row.task, Path(folder))

    def set_folder(self, task: str, folder: Path | None) -> None:
        row = self.rows[task]
        row.folder.setText(str(folder) if folder else "")
        self._rescan(row)

    def set_protocol(self, task: str, name: str) -> bool:
        row = self.rows[task]
        for i in range(row.protocol.count()):
            proto = row.protocol.itemData(i)
            if isinstance(proto, Protocol) and proto.name == name:
                row.protocol.setCurrentIndex(i)
                return True
        return False

    def _rescan(self, row: TaskRow) -> None:
        from_manifest = self._tasks_from_manifest()
        row.folder.setEnabled(not from_manifest)
        row.browse.setEnabled(not from_manifest)
        row.folder.setPlaceholderText(tr("z manifestu") if from_manifest else row.placeholder)
        if from_manifest:
            assert self._shared is not None
            found = self._shared.scans.get(row.task, FolderScan([]))
            row.scan = FolderScan(list(found.recordings), found.manifest, dict(found.durations))
            row.count.setText(f"{len(row.scan.recordings)} ✓" if row.scan.recordings else "–")
            row.refresh_look()
            self._update_buttons()
            return
        text = row.folder.text().strip()
        row.scan = (
            scan_folder(Path(text), recursive=self.recursive.isChecked())
            if text
            else FolderScan([])
        )
        if self._shared is not None:  # manifest bez úlohy: údaje k nahrávkám ze složek
            row.scan.manifest = rekey_manifest(self._shared.manifest, row.scan.recordings)
        n = len(row.scan.recordings)
        if not text:
            row.count.setText("–")
        elif n:
            row.count.setText(f"{n} ✓")
        else:
            row.count.setText(tr("žádné"))
        row.refresh_look()
        self._update_buttons()

    def _rescan_all(self) -> None:
        for row in self.rows.values():
            self._rescan(row)

    def active_rows(self) -> list[TaskRow]:
        return [
            r for r in self.rows.values() if r.scan.recordings and r.current_protocol() is not None
        ]

    # --- krok 2: kontroly ---------------------------------------------------------

    def _build_controls(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        top = QHBoxLayout()
        self.source_pill = QLabel("")
        self.source_pill.setObjectName("pill")
        self.source_pill.hide()
        top.addWidget(self.source_pill)
        self.source_text = QLabel("")
        self.source_text.setObjectName("muted")
        top.addWidget(self.source_text, 1)
        self.csv_btn = QPushButton(tr("Načíst věk a pohlaví z CSV…"))
        self.csv_btn.clicked.connect(self._load_csv)
        self.csv_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        top.addWidget(self.csv_btn)
        layout.addLayout(top)
        layout.addWidget(self._build_names_box())
        layout.addWidget(self._build_loose_box())
        # Přidat kontroly: co s kontrolou, která úlohu v normě už má (štítky v tabulce)
        self.policy_row = QWidget()
        policy = QHBoxLayout(self.policy_row)
        policy.setContentsMargins(0, 0, 0, 0)
        policy.addWidget(QLabel(tr("Kontrola, která úlohu v normě už má:")))
        self.SKIP, self.REPLACE = tr("přeskočit"), tr("nahradit")
        self.policy = Segmented([self.SKIP, self.REPLACE])
        self.policy.currentChanged.connect(lambda _v: self._fill_table())
        policy.addWidget(self.policy)
        policy.addStretch(1)
        self.policy_row.setVisible(self._extend is not None)
        layout.addWidget(self.policy_row)
        self.counts = QLabel("")
        self.counts.setObjectName("muted")
        layout.addWidget(self.counts)
        self.table = QTableWidget()
        style_table(
            self.table,
            [tr("kontrola"), tr("věk"), tr("pohlaví"), tr("nahrávky"), tr("zdroj"), tr("stav")],
            row_height=44,  # místo na výběr pohlaví
        )
        # věk se píše přímo do buňky (style_table úpravy vypíná)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(1, 80)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(2, 110)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._age_edited)
        layout.addWidget(card(self.table), 1)
        self.extend_summary = QLabel("")
        self.extend_summary.setWordWrap(True)
        self.extend_summary.setVisible(self._extend is not None)
        layout.addWidget(self.extend_summary)
        self.counts.setToolTip(
            tr(
                "Kontrolu bez věku nebo pohlaví jde použít, jen se nevybere podle věkového "
                "okna nebo pohlaví. Pacient, věk a pohlaví se berou z manifestu; z názvu "
                "souboru jen nouzově, když ho zapnete."
            )
        )
        # varování (dřív krok Souhrn): málo kontrol, vynechaná úloha, pacient v normě…
        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet(f"color: {theme.WARN};")
        self.warnings.hide()
        layout.addWidget(self.warnings)
        return page

    def _build_names_box(self) -> QFrame:
        """Bez manifestu: šablona a načtení manifestu, nebo nouzově odhad z názvu souboru."""
        box = QFrame()
        box.setObjectName("card")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        self.names_title = QLabel(tr("Ve složkách není manifest. Kdo je na které nahrávce?"))
        self.names_title.setObjectName("card_title")
        layout.addWidget(self.names_title)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.manifest_card = QFrame()
        self.manifest_card.setObjectName("card")
        theme.set_role(self.manifest_card, "accent")
        left = QVBoxLayout(self.manifest_card)
        left.setContentsMargins(12, 10, 12, 10)
        left.setSpacing(6)
        head = QLabel(tr("Doporučeno: manifest"))
        head.setObjectName("card_title")
        left.addWidget(head)
        hint = QLabel(
            tr(
                "Šablona má všechny nahrávky; doplníte pacienta, věk a pohlaví v Excelu "
                "a načtete ji zpět."
            )
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        left.addWidget(hint)
        buttons = QHBoxLayout()
        self.template_btn = QPushButton(tr("Uložit šablonu manifestu…"))
        theme.set_role(self.template_btn, "primary")
        self.template_btn.clicked.connect(lambda: self._ask_template())
        buttons.addWidget(self.template_btn)
        self.load_btn = QPushButton(tr("Načíst manifest…"))
        self.load_btn.clicked.connect(lambda: self._ask_manifest())
        buttons.addWidget(self.load_btn)
        buttons.addStretch(1)
        left.addLayout(buttons)
        cards.addWidget(self.manifest_card, 1)
        self.guess_card = QFrame()
        self.guess_card.setObjectName("card")
        theme.set_role(self.guess_card, "warn")
        right = QVBoxLayout(self.guess_card)
        right.setContentsMargins(12, 10, 12, 10)
        right.setSpacing(6)
        self.from_names = QCheckBox(tr("Nouzově: odhadnout z názvu souboru"))
        self.from_names.toggled.connect(lambda _on: self._regroup())
        right.addWidget(self.from_names)
        rule_row = QHBoxLayout()
        self.rule_label = QLabel(tr("Pravidlo"))
        rule_row.addWidget(self.rule_label)
        self.rule = QComboBox()
        for key, label in SUBJECT_RULES.items():
            self.rule.addItem(label, key)
        self.rule.currentIndexChanged.connect(lambda _i: self._regroup())
        rule_row.addWidget(self.rule)
        rule_row.addStretch(1)
        right.addLayout(rule_row)
        self.example = QLabel("")
        self.example.setObjectName("muted")
        right.addWidget(self.example)
        self.guess_hint = QLabel(
            tr("Odhad: zkontrolujte kontroly v tabulce. Norma si zapíše, že jsou z názvu.")
        )
        self.guess_hint.setStyleSheet(f"color: {theme.WARN};")
        self.guess_hint.setWordWrap(True)
        right.addWidget(self.guess_hint)
        cards.addWidget(self.guess_card, 1)
        layout.addLayout(cards)
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.names_box = box
        box.hide()
        return box

    def _build_loose_box(self) -> QFrame:
        """Nahrávky, které manifest nezná nebo nemají pacienta: doplnit, nebo vynechat."""
        box = QFrame()
        box.setObjectName("card")
        theme.set_role(box, "warn")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        head = QHBoxLayout()
        self.loose_title = QLabel("")
        self.loose_title.setObjectName("card_title")
        head.addWidget(self.loose_title)
        hint = QLabel(tr("nic se nehádá: doplňte kontrolu dvojklikem, nebo nahrávky vynechte"))
        hint.setObjectName("muted")
        head.addWidget(hint, 1)
        self.loose_fill_btn = QPushButton(tr("Vyplnit…"))
        self.loose_fill_btn.clicked.connect(lambda: self._ask_fill_loose())
        head.addWidget(self.loose_fill_btn)
        self.loose_skip_btn = QPushButton(tr("Vynechat"))
        self.loose_skip_btn.setToolTip(tr("Vybrané nahrávky do normy nepůjdou (znovu = vrátit)"))
        self.loose_skip_btn.clicked.connect(lambda: self.skip_loose(self._selected_loose()))
        head.addWidget(self.loose_skip_btn)
        layout.addLayout(head)
        self.loose_table = QTableWidget()
        style_table(self.loose_table, [tr("úloha"), tr("nahrávka"), tr("kontrola"), tr("důvod")])
        self.loose_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.loose_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        self.loose_table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        header = self.loose_table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in (0, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.loose_table.itemChanged.connect(self._loose_edited)
        layout.addWidget(self.loose_table)
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.loose_box = box
        box.hide()
        return box

    def _regroup(self) -> None:
        if not hasattr(self, "loose_box"):  # krok 1 se staví dřív než krok 2
            return
        from_runs = self._source == RUNS
        rule = self._rule() if self.from_names.isChecked() else ""
        if from_runs:
            column, value = self.group_filter()
            self._scan = scan_runs(self._chosen, column, value, rule)
            self._files = {r.path: r.subject for r in self._scan.rows}
            self._sources = {}
            for r in self._scan.rows:
                self._sources.setdefault(r.subject, set()).add(
                    FROM_NAME if r.from_name else FROM_MANIFEST
                )
            # kontroly jen z metadat: ručně doplněné zůstávají, jména z jiného pravidla ne
            known = {s: c for s, c in self._controls.items() if s in self._files.values()}
            self._controls = controls_from_scan(self._scan, known)
            self._loose = []
            # odhad z názvu jen u běhů bez pacienta v metadatech; šablona tu nedává smysl
            needs = bool(self._scan.unnamed) or self.from_names.isChecked()
            self.names_box.setVisible(needs)
            self.manifest_card.hide()
            self.names_title.setText(
                tr(
                    "{n} nahrávek nemá pacienta v metadatech běhu. Doplňte ho na Výsledcích "
                    "(Údaje nahrávek…), nebo nouzově:"
                ).format(n=self._scan.unnamed)
                if self._scan.unnamed
                else tr("Kontroly odhadnuté z názvu souboru:")
            )
            named = next((r for r in self._scan.rows if r.from_name), None)
            self.example.setText(f"{named.path.name} → {named.subject}" if named else "")
        else:
            scans = {r.task: r.scan for r in self.active_rows()}
            found = assign_controls(
                scans,
                rule=rule,
                known=self._controls,
                assigned=self._assigned,
                excluded=self._excluded,
            )
            self._files, self._controls = found.files, found.controls
            self._sources, self._loose = found.sources, found.loose
            any_manifest = any(s.manifest is not None for s in scans.values())
            self._any_manifest = any_manifest
            self.names_box.setVisible(not any_manifest or self.from_names.isChecked())
            self.manifest_card.setVisible(True)
            self.names_title.setText(
                tr("Ve složkách není manifest. Kdo je na které nahrávce?")
                if not any_manifest
                else tr("Kontroly, které manifest nezná, odhadnuté z názvu souboru:")
            )
            first = next((p for sc in scans.values() for p in sc.recordings), None)
            self.example.setText(
                f"{first.name} → {subject_from_name(first, self._rule())}" if first else ""
            )
        for widget in (self.rule_label, self.rule, self.example, self.guess_hint):
            widget.setEnabled(self.from_names.isChecked())
        self._update_source_pill()
        self._fill_loose()
        self._fill_table()

    def _update_source_pill(self) -> None:
        source = self.controls_from()
        if source == FROM_NAME:
            n = sum(1 for v in self._sources.values() if FROM_NAME in v)
            text, role = tr("odhad z názvu souboru"), "warn"
            detail = tr("{n} z {total} kontrol je odhadnutých z názvu souboru.").format(
                n=n, total=len(self._controls)
            )
        elif source in (FROM_MANIFEST, FROM_MANUAL) and self._source == RUNS:
            text, role, detail = tr("kontroly z metadat běhů"), "ok", ""
        elif source in (FROM_MANIFEST, FROM_MANUAL):
            text, role = tr("kontroly z manifestu"), "ok"
            detail = self._shared.manifest.path.name if self._shared is not None else ""
        else:
            self.source_pill.hide()
            self.source_text.setText("")
            return
        self.source_pill.setText(text)
        theme.set_role(self.source_pill, role)
        self.source_pill.show()
        self.source_text.setText(detail)

    def controls_from(self) -> str:
        """Souhrnný zdroj kontrol pro `NormInfo.controls_from` (name vyhrává)."""
        found: set[str] = set().union(*self._sources.values()) if self._sources else set()
        for source in (FROM_NAME, FROM_MANIFEST, FROM_MANUAL):
            if source in found:
                return source
        return ""

    def loose(self) -> list[tuple[str, Path, str]]:
        """Nahrávky bez kontroly v manifestu (úloha, nahrávka, důvod), i už doplněné."""
        return list(self._loose)

    def _fill_loose(self) -> None:
        table = self.loose_table
        self._filling = True
        table.setRowCount(len(self._loose))
        open_count = 0
        for r, (task, path, reason) in enumerate(self._loose):
            subject = self._assigned.get(path, "")
            skipped = path in self._excluded
            if not subject and not skipped:
                open_count += 1
            set_text(table, r, 0, self._task_name(task), muted=True)
            name = set_text(table, r, 1, path.name, bold=True)
            name.setToolTip(str(path))
            name.setData(Qt.ItemDataRole.UserRole, str(path))
            if skipped:
                item = set_text(table, r, 2, tr("vynecháno"), muted=True)
            else:
                item = QTableWidgetItem(f"{subject} ✓" if subject else "")
                item.setToolTip(tr("doplnit kontrolu (dvojklik)"))
                if not subject:
                    item.setForeground(QColor(theme.WARN))
                table.setItem(r, 2, item)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setFlags(
                (item.flags() | Qt.ItemFlag.ItemIsEditable)
                if not skipped
                else (item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            )
            set_text(table, r, 3, reason, muted=True)
            for c in (0, 1, 3):
                cell_item = table.item(r, c)
                if cell_item is not None:
                    cell_item.setFlags(cell_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._filling = False
        self.loose_title.setText(
            tr("Bez kontroly · {n} nahrávek").format(n=open_count)
            if open_count
            else tr("Bez kontroly · vše vyřešeno")
        )
        visible = bool(self._loose) and self._source != RUNS
        self.loose_box.setVisible(visible)
        if visible:  # bez manifestu jen pro pár ručních kontrol, hlavní je volba nahoře
            fit_height(table, max_rows=6 if getattr(self, "_any_manifest", True) else 3)
            # rozvržení kroku jinak dávalo oddílu volné místo místo tabulce kontrol
            self.loose_box.setMaximumHeight(self.loose_box.sizeHint().height())

    def _loose_edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 2:
            return
        path = Path(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        text = item.text().replace("✓", "").strip()
        self.fill_loose([path], text)

    def _selected_loose(self) -> list[Path]:
        rows = sorted({i.row() for i in self.loose_table.selectedIndexes()})
        return [self._loose[r][1] for r in rows if 0 <= r < len(self._loose)]

    def fill_loose(self, paths: list[Path], subject: str) -> None:
        """Kontrola pro nahrávky bez pacienta (prázdné = zrušit doplnění), jen pro výpočet."""
        for path in paths:
            if subject.strip():
                self._assigned[path] = subject.strip()
                self._excluded.discard(path)
            else:
                self._assigned.pop(path, None)
        self._regroup()

    def skip_loose(self, paths: list[Path]) -> None:
        """Vynechat nahrávky bez kontroly; vynechané znovu = vrátit."""
        if not paths:
            return
        if all(p in self._excluded for p in paths):
            self._excluded.difference_update(paths)
        else:
            self._excluded.update(paths)
        self._regroup()

    def _ask_fill_loose(self) -> None:
        paths = self._selected_loose()
        if not paths:
            QMessageBox.information(
                self, tr("Vyplnit"), tr("Vyberte nahrávky v oddílu Bez kontroly.")
            )
            return
        subject, ok = QInputDialog.getText(
            self, tr("Vyplnit"), tr("Kontrola pro {n} vybraných nahrávek:").format(n=len(paths))
        )
        if ok:
            self.fill_loose(paths, subject)

    def _ask_template(self) -> None:
        recordings = {rec: row.task for row in self.active_rows() for rec in row.scan.recordings}
        if not recordings:
            return
        base = Path(os.path.commonpath([str(p.parent) for p in recordings]))
        target, _ = QFileDialog.getSaveFileName(
            self, tr("Šablona manifestu"), str(base / "manifest.csv"), CSV_FILTER
        )
        if not target:
            return
        try:
            write_manifest_template(Path(target), recordings, Path(target).parent)
        except OSError as exc:
            QMessageBox.warning(self, tr("SpeechScope"), str(exc))
            return
        QMessageBox.information(
            self,
            tr("Šablona manifestu"),
            tr(
                "Šablona je uložená. Doplňte v ní pacienta, věk a pohlaví a načtěte ji "
                "tlačítkem Načíst manifest…."
            ),
        )

    def _rule(self) -> str:
        """Jak z názvu souboru poznat kontrolu (nouzová volba v kroku 2)."""
        rule = getattr(self, "rule", None)  # krok 1 se staví dřív než krok 2
        return str(rule.currentData() or "prefix") if rule is not None else "prefix"

    def set_from_names(self, on: bool, rule: str = "") -> None:
        """Nouzový odhad kontroly z názvu souboru (pro testy i průvodce)."""
        if rule:
            idx = self.rule.findData(rule)
            if idx >= 0:
                self.rule.blockSignals(True)
                self.rule.setCurrentIndex(idx)
                self.rule.blockSignals(False)
        self.from_names.setChecked(on)
        self._regroup()

    def recordings_by_task(self) -> dict[str, list[Path]]:
        """Nahrávky po úlohách podle zdroje (složky, nebo řádky vybraných běhů)."""
        if self._source == RUNS:
            return {t: [r.path for r in rows] for t, rows in self._scan.by_task().items()}
        out = {
            r.task: [p for p in r.scan.recordings if p in self._files] for r in self.active_rows()
        }
        return {t: recs for t, recs in out.items() if recs}

    def _task_name(self, task: str) -> str:
        row = self.rows.get(task)
        return row.label.text() if row is not None else tasks_mod.label(task)

    def _fill_table(self) -> None:
        if self._extend is not None:
            self._fill_extend_table()
            return
        self._filling = True
        per_subject: dict[str, dict[str, int]] = {}
        by_task = self.recordings_by_task()
        for task, recordings in by_task.items():
            for rec in recordings:
                tasks = per_subject.setdefault(self._files.get(rec, ""), {})
                tasks[task] = tasks.get(task, 0) + 1
        all_tasks = list(by_task)
        for r in range(self.table.rowCount()):  # staré výběry a štítky hned pryč, ne až později
            for c in (2, 4):
                old = self.table.cellWidget(r, c)
                if old is not None:
                    old.hide()
                    self.table.removeCellWidget(r, c)
        self.table.setRowCount(len(self._controls))
        for r, (subject, control) in enumerate(self._controls.items()):
            name = QTableWidgetItem(subject)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            bold = name.font()
            bold.setBold(True)
            name.setFont(bold)
            self.table.setItem(r, 0, name)
            age = QTableWidgetItem("" if control.age is None else f"{control.age:g}")
            age.setData(Qt.ItemDataRole.UserRole, subject)
            self.table.setItem(r, 1, age)
            sex = QComboBox()
            for code, label in SEX_CHOICES:
                sex.addItem(tr(label), code)
            sex.setCurrentIndex(max(0, sex.findData(control.sex)))
            sex.currentIndexChanged.connect(
                lambda _i, s=subject, box=sex: self.set_sex(s, str(box.currentData()))
            )
            self.table.setCellWidget(r, 2, sex)
            tasks = per_subject.get(subject, {})
            recs = QTableWidgetItem(
                " · ".join(f"{contract.TASK_SHORT.get(t, t)} {n}" for t, n in tasks.items())
            )
            recs.setFlags(recs.flags() & ~Qt.ItemFlag.ItemIsEditable)
            recs.setForeground(QColor(theme.MUTED))
            self.table.setItem(r, 3, recs)
            sources = self._sources.get(subject, set())
            text, role = self._source_label(sources)
            if text:
                set_pill(self.table, r, 4, text, role)
            else:
                set_text(self.table, r, 4, "–", muted=True)
            state = QTableWidgetItem(self._state_text(control, tasks, all_tasks, subject))
            state.setFlags(state.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, 5, state)
        self._filling = False
        self._update_counts()

    @staticmethod
    def _source_label(sources: set[str]) -> tuple[str, str]:
        if FROM_NAME in sources:
            return tr("z názvu"), "warn"
        if FROM_MANIFEST in sources and FROM_MANUAL in sources:
            return tr("manifest + ručně"), "accent"
        if FROM_MANUAL in sources:
            return tr("ručně"), "accent"
        if FROM_MANIFEST in sources:
            return tr("manifest"), "neutral"
        return "", ""

    def _sex_box(self, subject: str, sex: str) -> QComboBox:
        box = QComboBox()
        for code, label in SEX_CHOICES:
            box.addItem(tr(label), code)
        box.setCurrentIndex(max(0, box.findData(sex)))
        box.currentIndexChanged.connect(
            lambda _i, s=subject, b=box: self.set_sex(s, str(b.currentData()))
        )
        return box

    def addition_state(self, subject: str, task: str, recordings: list[Path]) -> tuple[str, str]:
        """Přidat kontroly: štítek (text, role) pro kontrolu × úlohu podle volby."""
        if not recordings:
            return "–", ""
        known = set(self._extend.files) if self._extend is not None else set()
        if all(rec in known for rec in recordings):
            return tr("už v normě"), "neutral"
        if (subject, task) in self._existing:
            if self.replace_existing():
                return tr("nahradí se"), "warn"
            return tr("už v normě · přeskočí se"), "accent"
        return tr("nová"), "ok"

    def _fill_extend_table(self) -> None:
        """Přidat kontroly (mockup 1b): kontrola, věk, pohlaví a štítek po úlohách."""
        self._filling = True
        by_task = self.recordings_by_task()
        task_keys = list(by_task)
        per: dict[tuple[str, str], list[Path]] = {}
        for task, recordings in by_task.items():
            for rec in recordings:
                per.setdefault((self._files.get(rec, ""), task), []).append(rec)
        table = self.table
        for r in range(table.rowCount()):  # staré buňky s poli a štítky hned pryč
            for c in range(table.columnCount()):
                old = table.cellWidget(r, c)
                if old is not None:
                    old.hide()
                    table.removeCellWidget(r, c)
        headers = [tr("kontrola"), tr("věk"), tr("pohlaví")]
        headers += [self._task_name(t) for t in task_keys]
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels([h.upper() for h in headers])
        header = table.horizontalHeader()
        for col in range(3, len(headers)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        if len(headers) > 3:
            header.setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
        table.setRowCount(len(self._controls))
        for r, (subject, control) in enumerate(self._controls.items()):
            name = QTableWidgetItem(subject)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            bold = name.font()
            bold.setBold(True)
            name.setFont(bold)
            table.setItem(r, 0, name)
            age = QTableWidgetItem("" if control.age is None else f"{control.age:g}")
            age.setData(Qt.ItemDataRole.UserRole, subject)
            if control.age is None:
                age.setToolTip(tr("doplnit věk (dvojklik)"))
            table.setItem(r, 1, age)
            table.setCellWidget(r, 2, self._sex_box(subject, control.sex))
            for c, task in enumerate(task_keys, start=3):
                text, role = self.addition_state(subject, task, per.get((subject, task), []))
                if role:
                    set_pill(table, r, c, text, role)
                else:
                    set_text(table, r, c, text, muted=True)
        self._filling = False
        self._update_counts()
        self._update_extend_summary()

    def _update_extend_summary(self) -> None:
        if self._extend is None:
            return
        add, dup = self._addition_counts()
        text = tr("Přidá se {a} nahrávek, {d} se přeskočí (v normě už jsou).").format(a=add, d=dup)
        text += " " + (
            tr("Norma se sestaví znovu hned, nic se nepočítá.")
            if self._source == RUNS
            else tr("Přidané nahrávky se spočítají, pak se norma sestaví znovu.")
        )
        for warning in self._patient_warning():
            text += "\n" + warning
        self.extend_summary.setText(text)
        if self.step() == 1:
            self._update_buttons()

    def _state_text(
        self, control: Control, tasks: dict[str, int], all_tasks: list[str], subject: str = ""
    ) -> str:
        missing = []
        if control.age is None:
            missing.append(tr("chybí věk"))
        if not control.sex:
            missing.append(tr("chybí pohlaví"))
        lacking = [self._task_name(t) for t in all_tasks if t not in tasks]
        if lacking:
            missing.append(tr("bez {tasks}").format(tasks=", ".join(lacking)))
        already = [self._task_name(t) for t in tasks if (subject, t) in self._existing]
        if already:
            missing.append(tr("už v normě: {tasks}").format(tasks=", ".join(already)))
        return " · ".join(missing) or "✓"

    def _age_edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 1:
            return
        subject = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self.set_age(subject, meta.parse_age(item.text()))

    def set_age(self, subject: str, age: float | None) -> None:
        control = self._controls.get(subject)
        if control is None:
            return
        control.age = age
        self._refresh_state(subject)

    def set_sex(self, subject: str, sex: str) -> None:
        control = self._controls.get(subject)
        if control is None:
            return
        control.sex = sex
        self._refresh_state(subject)

    def _refresh_state(self, subject: str) -> None:
        """Jen sloupec stav a počty; tabulka se nepřekresluje (smazala by právě měněný výběr)."""
        if self._extend is not None:  # štítky po úlohách na věku a pohlaví nezávisí
            self._update_counts()
            return
        tasks: dict[str, int] = {}
        by_task = self.recordings_by_task()
        for task, recordings in by_task.items():
            for rec in recordings:
                if self._files.get(rec) == subject:
                    tasks[task] = tasks.get(task, 0) + 1
        all_tasks = list(by_task)
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.text() == subject:
                state = self.table.item(r, 5)
                if state is not None:
                    state.setText(
                        self._state_text(self._controls[subject], tasks, all_tasks, subject)
                    )
                break
        self._update_counts()

    def _update_counts(self) -> None:
        controls = list(self._controls.values())
        complete = sum(1 for c in controls if c.age is not None and c.sex)
        no_age = sum(1 for c in controls if c.age is None)
        parts = [
            tr("{n} kontrol").format(n=len(controls)),
            tr("{n} kompletních").format(n=complete),
        ]
        if no_age:
            parts.append(tr("{n} bez věku").format(n=no_age))
        self.counts.setText(" · ".join(parts))
        if hasattr(self, "next_btn"):  # souhrn v patičce a varování podle kontrol
            self._update_buttons()

    def _load_csv(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, tr("Věk a pohlaví kontrol"), "", CSV_FILTER)
        if source:
            self.load_controls_csv(Path(source))

    def load_controls_csv(self, path: Path) -> int:
        """Doplní věk a pohlaví z CSV; vrátí, kolika kontrolám."""
        try:
            loaded = read_controls_csv(path)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("SpeechScope"), str(exc))
            return 0
        n = 0
        for subject, row in loaded.items():
            control = self._controls.get(subject)
            if control is None:
                continue
            if row.age is not None:
                control.age = row.age
            if row.sex:
                control.sex = row.sex
            n += 1
        self._fill_table()
        return n

    def controls(self) -> dict[str, Control]:
        return dict(self._controls)

    # --- krok 3: souhrn -----------------------------------------------------------

    def replace_existing(self) -> bool:
        """Přidat kontroly: nahrávky kontroly, která úlohu v normě má, nahradit."""
        return self.policy.current() == self.REPLACE

    def set_replace(self, replace: bool) -> None:
        self.policy.set_current(self.REPLACE if replace else self.SKIP)
        self._fill_table()

    def _addition_counts(self) -> tuple[int, int]:
        """Přidat kontroly: (přidá se, už v normě) podle zvolené volby."""
        known = set(self._extend.files) if self._extend is not None else set()
        add = dup = 0
        for task, recordings in self.recordings_by_task().items():
            for rec in recordings:
                subject = self._files.get(rec, "")
                if rec in known or not subject:
                    dup += 1
                elif (subject, task) in self._existing and not self.replace_existing():
                    dup += 1
                else:
                    add += 1
        return add, dup

    def _fill_summary(self) -> None:
        """Souhrn v patičce a varování pod kontrolami (krok 2; dřív samostatný krok 3)."""
        if self.step() != 1:
            return
        from_runs = self._source == RUNS
        controls = list(self._controls.values())
        by_task = self.recordings_by_task()
        n_rec = sum(len(v) for v in by_task.values())
        figures = [
            (tr("{n} kontrol").format(n=len(controls)), tr("{k} úloh").format(k=len(by_task))),
            (tr("{n} nahrávek").format(n=n_rec), tr("z hotových běhů") if from_runs else ""),
        ]
        if self._extend is not None:
            add, dup = self._addition_counts()
            text = tr("Přidá se {a} nahrávek, {d} se přeskočí (v normě už jsou).").format(
                a=add, d=dup
            )
            text += " " + (
                tr("Norma se sestaví znovu hned, nic se nepočítá.")
                if from_runs
                else tr("Přidané nahrávky se spočítají, pak se norma sestaví znovu.")
            )
        elif from_runs:
            text = tr("Nic se nepočítá, norma se sestaví hned.")
        else:
            total, known = 0.0, True
            for row in self.active_rows():
                proto = row.current_protocol()
                rate = self._rate(proto.slug()) if proto is not None else None
                if rate and row.scan.audio_seconds:
                    total += rate / 60 * row.scan.audio_seconds
                else:
                    known = False
            if total:
                figures.append(
                    ("~" + format_duration(total), tr("odhad") if known else tr("odhad (část)"))
                )
            text = tr("po výpočtu se norma sestaví sama")
        self.summary.set_figures(figures)
        self.summary.show()
        self.hint.setText(text)
        self.next_btn.setText(
            (tr("Přidat") if from_runs else tr("Spustit"))
            if self._extend is not None
            else (tr("Sestavit") if from_runs else tr("Spustit"))
        )
        problems = []
        if from_runs:
            problems += self._runs_problems_text()
        else:
            for row in self.rows.values():
                if not row.scan.recordings and self._extend is None:
                    problems.append(
                        tr(
                            "{task} se vynechá (bez nahrávek). Dimenze, které ji potřebují, s "
                            "touto normou porovnat nepůjdou."
                        ).format(task=row.label.text())
                    )
        if self._extend is None:
            problems += self._few_controls(by_task)
        if not from_runs:
            open_loose = [
                p for _t, p, _r in self._loose if p not in self._files and p not in self._excluded
            ]
            if open_loose:
                problems.append(
                    tr("{n} nahrávek bez kontroly se vynechá (oddíl Bez kontroly).").format(
                        n=len(open_loose)
                    )
                )
        if self.controls_from() == FROM_NAME:
            n = sum(1 for v in self._sources.values() if FROM_NAME in v)
            problems.append(
                tr(
                    "{n} kontrol je odhadnutých z názvu souboru; norma si to zapíše (O normě)."
                ).format(n=n)
            )
        problems += self._patient_warning()
        self.warnings.setText("\n".join("⚠ " + p for p in problems))
        self.warnings.setVisible(bool(problems))

    def patients_in_norm(self) -> list[str]:
        """Kontroly, které jsou zároveň pacienty na stránce Pacienti (jen varování)."""
        return sorted(s for s in self._controls if patient_key(s) in self._patients)

    def _patient_warning(self) -> list[str]:
        found = self.patients_in_norm()
        if not found:
            return []
        shown = ", ".join(found[:5]) + (" …" if len(found) > 5 else "")
        return [
            tr(
                "{n} kontrol je zároveň na stránce Pacienti ({names}). Do normy patří jen "
                "zdraví; pacienta v normě by hodnocení srovnávalo se sebou samým."
            ).format(n=len(found), names=shown)
        ]

    def _few_controls(self, by_task: dict[str, list[Path]]) -> list[str]:
        out = []
        for task, recordings in by_task.items():
            n = len({self._files.get(rec, "") for rec in recordings})
            if n < RECOMMENDED_CONTROLS:
                out.append(
                    tr("{task}: jen {n} kontrol, doporučeno aspoň {min}.").format(
                        task=self._task_name(task), n=n, min=RECOMMENDED_CONTROLS
                    )
                )
        return out

    def _runs_problems_text(self) -> list[str]:
        """Varování zdroje z běhů (jiný protokol, kontrola z názvu souboru, chybné řádky)."""
        problems = runs_problems(self._chosen)
        if self.runs_warning.text():
            problems = self.runs_warning.text().split("\n")
        scan = self._scan
        if scan.unnamed:
            problems.append(
                tr(
                    "{n} nahrávek nemá pacienta v metadatech běhu a do normy nepůjde "
                    "(Údaje nahrávek… na Výsledcích, nebo nouzově odhad z názvu)."
                ).format(n=scan.unnamed)
            )
        if scan.no_subject:
            problems.append(
                tr("{n} řádků běhů nemá cestu k nahrávce, do normy nepůjdou.").format(
                    n=scan.no_subject
                )
            )
        if scan.errors:
            problems.append(
                tr("{n} chybných řádků (knihovna vrátila chybu) se vynechá.").format(n=scan.errors)
            )
        return problems

    # --- kroky ---------------------------------------------------------------------

    def step(self) -> int:
        return self.stack.currentIndex()

    def _show_step(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, pill in enumerate(self.step_pills):
            theme.set_role(pill, "accent" if i == index else ("ok" if i < index else "neutral"))
        self.back_btn.setText(tr("Storno") if index == 0 else tr("‹ Zpět"))
        self.next_btn.setText(tr("Další › Kontroly"))
        self.summary.setVisible(index == 1)
        if index == 1:
            self._regroup()
            self._fill_summary()
        self._update_buttons()

    def last_step(self) -> int:
        """Dva kroky: zdroj a úlohy, kontroly (dřív nová norma tři se Souhrnem)."""
        return 1

    def _update_buttons(self) -> None:
        hint = ""
        ok = True
        if self.step() == 0:
            rows = self.active_rows()
            if not self.name.text().strip():
                ok, hint = False, tr("Vyplňte název normy.")
            elif self._source == RUNS:
                runs = [r for chosen in self._chosen.values() for r in chosen]
                if not runs:
                    ok, hint = False, tr("Vyberte běhy aspoň pro jednu úlohu.")
                elif self.group_column.currentData() and not self.group_value.currentText():
                    ok, hint = False, tr("Vyberte hodnotu skupiny.")
                elif not self._scan.rows:
                    ok, hint = False, tr("Vybrané běhy nemají žádný řádek s pacientem.")
                else:
                    hint = tr("{n} nahrávek · {k} kontrol").format(
                        n=len(self._scan.rows), k=len({r.subject for r in self._scan.rows})
                    )
            elif not rows:
                ok, hint = False, tr("Vyberte složku aspoň pro jednu úlohu.")
            else:
                n = sum(len(r.scan.recordings) for r in rows)
                hint = tr("úloh: {k} · nahrávek: {n}").format(k=len(rows), n=n)
        elif self.step() == 1:
            ok = bool(self._controls)
            if ok and self._extend is not None:
                add, _dup = self._addition_counts()
                ok = add > 0
            self.next_btn.setEnabled(ok)
            self._fill_summary()
            if not self._controls:
                self.hint.setText(tr("Žádná kontrola."))
            return
        self.next_btn.setEnabled(ok)
        self.hint.setText(hint)

    def go_next(self) -> None:
        if not self.next_btn.isEnabled():
            return
        if self.step() < self.last_step():
            self._show_step(self.step() + 1)
        else:
            self.accept()

    def go_back(self) -> None:
        if self.step() == 0:
            self.reject()
        else:
            self._show_step(self.step() - 1)

    # --- výsledek ----------------------------------------------------------------

    def plan(self) -> NormPlan:
        """Plán normy z vyplněného průvodce (složka v norms_dir je volná, ještě nevznikla).

        Z hotových běhů má plán všechny dávky hotové a hlavní okno ho rovnou sestaví."""
        if not self._controls:
            self._regroup()
        language = str(self.language.currentData() or "")
        name = self.name.text().strip()
        info = NormInfo(
            name=name,
            source=self.source.text().strip(),
            conditions=self.conditions.text().strip(),
            language=language,
            controls_from=self.controls_from(),
        )
        folder = unique_norm_dir(self._norms_dir, name)
        if self._source == RUNS:
            info.language = self._runs_language()
            used = set(self._chosen)
            info.custom_tasks = [c for c in tasks_mod.registry().custom if c.key in used]
            plan, _problems = plan_from_runs(
                info, folder, self._chosen, self._scan, dict(self._controls)
            )
            return plan
        tasks = []
        for row in self.active_rows():
            proto = row.current_protocol()
            assert proto is not None
            inputs = [p for p in row.scan.recordings if p in self._files]  # jen s kontrolou
            if not inputs:
                continue
            tasks.append(
                TaskPlan(
                    task=row.task,
                    protocol=with_language(proto, language).to_dict(),
                    inputs=inputs,
                )
            )
        files = {p: s for p, s in self._files.items() if any(p in t.inputs for t in tasks)}
        used = {t.task for t in tasks}
        info.custom_tasks = [c for c in self.custom_tasks() if c.key in used]
        for custom in info.custom_tasks:
            tasks_mod.registry().register(custom, save=True)  # příště se nabídne znovu
        return NormPlan(
            info=info,
            folder=folder,
            tasks=tasks,
            files=files,
            controls=dict(self._controls),
        )

    def _runs_language(self) -> str:
        """Jazyk normy z protokolů vybraných běhů (přepis), jinak z nabídky."""
        for runs in self._chosen.values():
            for run in runs:
                try:
                    proto = Protocol.load(run.dir / "protocol.yaml")
                except Exception:  # noqa: BLE001  chybějící nebo rozbitý soubor
                    continue
                language = str(proto.config.get("transcript", {}).get("language") or "")
                if language:
                    return language
        return str(self.language.currentData() or "")

    def extension(self) -> tuple[Extension, bool]:
        """Přidat kontroly: co přidat a jestli nahradit kontroly, které úlohu v normě mají."""
        assert self._extend is not None
        if not self._controls:
            self._regroup()
        if self._source == RUNS:
            plan_tasks, _problems = tasks_from_runs(self._chosen, self._scan)
        else:
            language = self._extend.info.language
            plan_tasks = []
            for row in self.active_rows():
                proto = self._extend.protocol_for(row.task)
                if proto is None:  # úloha, kterou norma zatím nemá
                    current = row.current_protocol()
                    assert current is not None
                    proto = with_language(current, language).to_dict()
                inputs = [p for p in row.scan.recordings if p in self._files]
                if inputs:
                    plan_tasks.append(TaskPlan(row.task, proto, inputs))
        used = {p for t in plan_tasks for p in t.inputs}
        files = {p: s for p, s in self._files.items() if p in used}
        ext = Extension(plan_tasks, files, dict(self._controls), self.controls_from())
        return ext, self.replace_existing()


class PickRunsDialog(QDialog):
    """Výběr hotových běhů jedné úlohy pro normu (zaškrtávací seznam, nejnovější napřed).

    Přepínač Analýzy · Hodnocení jako v historii, výchozí Analýzy: hodnocení jsou většinou
    pacienti, do normy patří jen zdraví (třeba kontroly vyšetřené přes Hodnocení).
    Hledání filtruje podle běhu i pacienta, Vybrat vše bere jen viditelné řádky."""

    def __init__(
        self,
        task: str,
        runs: list[RunInfo],
        chosen: list[RunInfo],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Běhy pro normu: {task}").format(task=tasks_mod.label(task)))
        self.resize(720, 480)
        self._runs = runs
        self._checked = {r.dir for r in chosen}
        self._shown: list[RunInfo] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        bar = QHBoxLayout()
        n_assess = sum(1 for r in runs if r.kind == KIND_ASSESSMENT)
        self.ANALYSES = tr("Analýzy {n}").format(n=len(runs) - n_assess)
        self.ASSESSMENTS = tr("Hodnocení {n}").format(n=n_assess)
        self.kind = Segmented([self.ANALYSES, self.ASSESSMENTS])
        if chosen and all(r.kind == KIND_ASSESSMENT for r in chosen):
            self.kind.set_current(self.ASSESSMENTS)
        self.kind.currentChanged.connect(lambda _v: self._fill())
        bar.addWidget(self.kind)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat běh nebo pacienta…"))
        self.search.textChanged.connect(lambda _t: self._fill())
        bar.addWidget(self.search, 1)
        all_btn = QPushButton(tr("Vybrat vše"))
        all_btn.clicked.connect(lambda: self.check_all(True))
        none_btn = QPushButton(tr("Nic"))
        none_btn.clicked.connect(lambda: self.check_all(False))
        bar.addWidget(all_btn)
        bar.addWidget(none_btn)
        layout.addLayout(bar)
        self.table = QTableWidget()
        style_table(
            self.table,
            ["", tr("běh"), tr("pacient"), tr("protokol"), tr("nahrávek"), tr("spuštěno")],
        )
        right_header(self.table, 4)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        self.table.cellClicked.connect(self._toggle)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(card(self.table), 1)
        self.note = QLabel(
            tr(
                "Hodnocení jsou většinou pacienti. Do normy patří jen zdraví, třeba kontroly "
                "vyšetřené stejným protokolem přes Hodnocení."
            )
        )
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        self.count = QLabel("")
        self.count.setObjectName("muted")
        buttons.addWidget(self.count)
        buttons.addStretch(1)
        cancel = QPushButton(tr("Storno"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("Vybrat"))
        theme.set_role(ok, "primary")
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        self._fill()

    def show_assessments(self, on: bool) -> None:
        self.kind.set_current(self.ASSESSMENTS if on else self.ANALYSES)
        self._fill()

    def _fill(self) -> None:
        assessments = self.kind.current() == self.ASSESSMENTS
        needle = self.search.text().strip().casefold()
        self._shown = [
            r
            for r in self._runs
            if (r.kind == KIND_ASSESSMENT) == assessments
            and (
                not needle
                or needle in r.display_name.casefold()
                or needle in r.patient.casefold()
                or needle in r.protocol_name.casefold()
            )
        ]
        self.note.setVisible(assessments)
        table = self.table
        table.blockSignals(True)
        table.setRowCount(len(self._shown))
        for r, run in enumerate(self._shown):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(
                Qt.CheckState.Checked if run.dir in self._checked else Qt.CheckState.Unchecked
            )
            table.setItem(r, 0, check)
            set_text(table, r, 1, run.display_name, bold=True)
            set_text(table, r, 2, run.patient or "–", muted=not run.patient)
            set_text(table, r, 3, run.protocol_name, muted=True)
            set_text(table, r, 4, str(run.rows or 0), right=True)
            when = run.started.strftime("%d.%m.%Y %H:%M") if run.started else ""
            set_text(table, r, 5, when, muted=True)
        table.blockSignals(False)
        self._update_count()

    def _update_count(self) -> None:
        self.count.setText(tr("vybráno {n}").format(n=len(self._checked)))

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or not 0 <= item.row() < len(self._shown):
            return
        run = self._shown[item.row()]
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(run.dir)
        else:
            self._checked.discard(run.dir)
        self._update_count()

    def _toggle(self, row: int, column: int) -> None:
        if column == 0:
            return  # políčko se přepne samo
        item = self.table.item(row, 0)
        if item is not None:
            checked = item.checkState() == Qt.CheckState.Checked
            item.setCheckState(Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)

    def check_all(self, on: bool) -> None:
        """Zaškrtne nebo odškrtne viditelné řádky (podle přepínače a hledání)."""
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None:
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def chosen(self) -> list[RunInfo]:
        return [run for run in self._runs if run.dir in self._checked]


__all__ = ["NewNormDialog", "PickRunsDialog", "format_duration"]
