"""Stránka Hodnocení: ručně založená hodnocení pacientů proti normě zdravých kontrol.

Vlevo uložená hodnocení (`backend/assessments.py`); nic se neskládá samo
ze všech běhů. Nové hodnocení… (`ui/assessment_dialog.py`) vybere pacienta,
věk, pohlaví, datum a nahrávky po úlohách, nebo rovnou z nahrávek
(`ui/assessment_build_dialog.py`: dávky po úlohách do fronty, po poslední se
hodnocení založí samo; rozpracované je v seznamu jako „počítá se“ s Dopočítat
zbývající, Založit z hotových a Zahodit, plány `assessments/<id>.plan.json`);
z Výsledků (pravé tlačítko na nahrávce) se otevře dialog s nahrávkou předvyplněnou. Vpravo norma
s věkovým oknem a pohlavím (volí se při prohlížení), pavouk po dimenzích,
strom dimenzí a položek, nahrávky hodnocení se stavem vůči normě a popis
normy. Skóre počítá `dimensions.evaluate` z `assessments.resolve`.
Bez normy stránka ukáže strom jen s hodnotami položek (bez z-skóre a pásem),
nahrávky a řekne, kde normu vzít.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend import quality, tasks
from ...backend.assessment_build import AssessmentPlan, pending_plans, plan_from_assessment
from ...backend.assessments import (
    Assessment,
    AssessmentStore,
    RecordingRef,
    norm_match,
    patient_key,
    resolve,
)
from ...backend.catalog import Catalog, PatientPrefs
from ...backend.dimensions import (
    Dimension,
    DimensionResult,
    Evaluation,
    ItemResult,
    evaluate,
    load_dimensions,
)
from ...backend.history import run_key
from ...backend.library import FeatureInfo, Library, LibraryError
from ...backend.longitudinal import (
    Timeline,
    ValueTimeline,
    build_timeline,
    build_value_timeline,
    format_change,
)
from ...backend.norm_build import RUNNING
from ...backend.norms import (
    BOTH,
    DEFAULT_WINDOW,
    HIGHER,
    LOWER,
    Norm,
    Selection,
    all_norms,
    version_changes,
)
from ...backend.visits import Recording, Visit, VisitIndex
from ...i18n import tr
from .. import theme
from ..assessment_build_dialog import NewAssessmentChoiceDialog
from ..assessment_dialog import AssessmentDialog, match_text
from ..feature_detail import split_unit
from ..file_actions import file_menu, open_file
from ..recording_detail import ColumnInfo, RecordingDetailDialog, column_index
from ..widgets.dimension_tree import (
    BAND_ROLE,
    BAND_STRONG,
    DIMENSION_ROW_BG,
    PILL_ROLE,
    TASK_PILL_ROLE,
    DeviationAxis,
    DimensionTree,
    DimensionTreeDelegate,
    band_dot,
)
from ..widgets.elided_label import ElidedLabel
from ..widgets.patient_overview import OverviewRow, PatientOverview
from ..widgets.spider_chart import (
    BAND_COLORS,
    INWARD,
    OUTWARD,
    Axis,
    Overlay,
    ScaleLegend,
    SpiderChart,
    format_sd,
)
from ..widgets.status_header import StatusHeader
from ..widgets.trend_chart import TrendGrid, TrendSeries, format_tick, nice_axis
from ..widgets.trend_map import NAME_MAX, MapRow, TrendMap

NO_NORM = "none"  # volba „žádná norma“ v nabídce i v nastavení (`ui/norm`)

BAND_LABELS: dict[str, str] = {
    "ok": tr("v normě"),
    "warn": tr("hraniční"),
    "bad": tr("mimo normu"),
}
BAND_ROLES: dict[str, str] = {"ok": "ok", "warn": "warn", "bad": "missing"}
TREE_AXIS = 6  # logický sloupec osy odchylky ve stromu dimenzí (zobrazený před z)
PENDING_BAND = "pending"  # štítek karty se teprve spočítá (líně, po zobrazení seznamu)
BADGE_CHUNK = 4  # kolik karet se vyhodnotí v jednom tiku časovače
WINDOWS: tuple[tuple[str, int | None], ...] = (  # výchozí ±5 (30. 10. 2026)
    ("± 3 let", 3),
    ("± 5 let", 5),
    ("± 10 let", 10),
    (tr("bez omezení"), None),
)


def _band_order(band: str) -> int:
    return {"bad": 3, "warn": 2, "ok": 1}.get(band, 0)


def worst_band(evaluation: Evaluation | None) -> str:
    if evaluation is None:
        return ""
    bands = [r.band for r in evaluation.shown]
    return max(bands, key=_band_order) if bands else ""


def format_number(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "–"
    if abs(value) >= 100:
        text = f"{value:.0f}"
    elif abs(value) >= 10:
        text = f"{value:.1f}"
    else:
        text = f"{value:.2f}"
    return text.replace(".", ",")


def format_visit_date(text: str) -> str:
    """ISO datum jako „14. 10. 2026“; cokoli jiného beze změny."""
    try:
        date = dt.date.fromisoformat(text[:10])
    except ValueError:
        return text
    return f"{date.day}. {date.month}. {date.year}"


def visit_subtitle(visit: Visit) -> str:
    parts = []
    person = []
    if visit.sex:
        person.append(tr("žena") if visit.sex == "f" else tr("muž"))
    if visit.age is not None:
        person.append(tr("{age} let").format(age=int(round(visit.age))))
    if person:
        parts.append(", ".join(person))
    if visit.visit:
        parts.append(tr("vyšetření {date}").format(date=format_visit_date(visit.visit)))
    parts.append(", ".join(task_count(visit, t) for t in visit.tasks))
    return " · ".join(parts)


def task_count(visit: Visit, task: str) -> str:
    """„Fonace“, nebo „Fonace (2)“ u víc nahrávek jedné úlohy."""
    label = tasks.label(task)
    n = visit.count(task)
    return f"{label} ({n})" if n > 1 else label


Entry = tuple[Assessment, Visit, list[RecordingRef]]  # hodnocení, vyšetření, chybějící odkazy


def _sex_text(sex: str) -> str:
    return (tr("žena") if sex == "f" else tr("muž")) if sex in ("f", "m") else ""


def _age_text(age: float | None) -> str:
    return str(int(round(age))) if age is not None else ""


def patient_subtitle(entries: list[Entry]) -> str:
    """Řádek karty pacienta: pohlaví a věk z posledního vyšetření, počet a rozpětí dat."""
    latest = entries[0][1]
    if len(entries) == 1:
        return visit_subtitle(latest)
    person = []
    if latest.sex:
        person.append(tr("žena") if latest.sex == "f" else tr("muž"))
    if latest.age is not None:
        person.append(tr("{age} let").format(age=int(round(latest.age))))
    parts = [", ".join(person)] if person else []
    parts.append(tr("{n} vyšetření").format(n=len(entries)))
    dates = [e[0].date for e in entries if e[0].date]
    if dates:
        first, last = format_visit_date(min(dates)), format_visit_date(max(dates))
        parts.append(f"{first} → {last}" if first != last else last)
    return " · ".join(parts)


class PatientCard(QFrame):
    """Karta pacienta v seznamu: štítek je pásmo posledního vyšetření."""

    def __init__(self, entries: list[Entry], band: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entries = entries
        self.visit = entries[0][1]
        self.assessment = entries[0][0]
        self.setObjectName("card")
        self._role = BAND_ROLES.get(band, "neutral")
        theme.set_role(self, self._role)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        visit = self.visit
        name = (
            visit.subject
            if visit.explicit
            else tr("{file} (bez pacienta)").format(file=visit.subject)
        )
        self.title = ElidedLabel(name)
        self.title.setObjectName("card_title")
        head.addWidget(self.title, 1)
        self.pill = QLabel("")
        self.pill.setObjectName("pill")
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        self.detail = QLabel(patient_subtitle(entries))
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.band = ""
        self._current = False
        self.on_band = None  # stránka: přepsat řádek přehledu po dopočítání štítku
        self.set_band(band)

    @property
    def pending(self) -> bool:
        return self.band == PENDING_BAND

    def set_band(self, band: str) -> None:
        """Štítek pásma; `PENDING_BAND` = „…“, dokud se pacient nevyhodnotí."""
        self.band = band
        if band == PENDING_BAND:
            self.pill.setText("…")
            role = "neutral"
        else:
            self.pill.setText(BAND_LABELS.get(band, tr("bez normy")))
            role = BAND_ROLES.get(band, "neutral")
        theme.set_role(self.pill, role)
        self._role = role
        theme.set_role(self, "selected" if self._current else self._role)
        if self.on_band is not None:
            self.on_band(self)

    def set_current(self, current: bool) -> None:
        self._current = current
        theme.set_role(self, "selected" if current else self._role)


def plan_pill(plan: AssessmentPlan) -> tuple[str, str]:
    n, k = len(plan.tasks), plan.done
    if any(t.status == RUNNING for t in plan.tasks):
        return tr("počítá se {k} z {n}").format(k=k, n=n), "accent"
    if plan.all_finished:
        return tr("čeká na založení"), "warn"
    return tr("dávky {k} z {n}").format(k=k, n=n), "neutral"


class PendingCard(QFrame):
    """Karta rozpracovaného hodnocení (dávky se počítají nebo čekají)."""

    def __init__(self, plan: AssessmentPlan, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setObjectName("card")
        text, self._role = plan_pill(plan)
        theme.set_role(self, self._role)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = ElidedLabel(plan.patient.subject)
        self.title.setObjectName("card_title")
        head.addWidget(self.title, 1)
        self.pill = QLabel(text)
        self.pill.setObjectName("pill")
        theme.set_role(self.pill, self._role)
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        visit = Visit(
            plan.patient.subject,
            plan.patient.date,
            True,
            age=plan.patient.age,
            sex=plan.patient.sex,
        )
        task_names = ", ".join(tasks.label(t.task) for t in plan.tasks)
        self.detail = QLabel(visit_subtitle(visit).rsplit(" · ", 1)[0] + " · " + task_names)
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

    def set_current(self, current: bool) -> None:
        theme.set_role(self, "selected" if current else self._role)


class AssessmentPage(QWidget):
    notice = Signal(str)
    new_from_recordings_requested = Signal()  # průvodce Hodnocení z nahrávek (hlavní okno)
    build_requested = Signal(object)  # plán: založit z hotových dávek
    resume_requested = Signal(object)  # plán: dopočítat zbývající dávky
    discard_requested = Signal(object)  # plán: zahodit rozpracované hodnocení
    recompute_requested = Signal(str)  # id hodnocení: nahrávky znovu současnou knihovnou
    # Nové vyšetření… z nahrávek: hlavní okno otevře průvodce s předvyplněným pacientem
    new_visit_from_recordings_requested = Signal(object)
    open_run_requested = Signal(object)  # složka běhu: Otevřít běh ve Výsledcích
    orientation_changed = Signal(str)  # orientace pavouka (hlavní okno uloží)
    scale_changed = Signal(str)  # měřítko Vývoje v čase: sd | values (hlavní okno uloží)
    form_changed = Signal(str)  # podoba Vývoje: spider | charts (hlavní okno uloží)
    trend_selection_changed = Signal(str, object)  # ("dims" | "items", seznam klíčů s grafem)
    norm_choice_changed = Signal(str)  # cesta k normě, NO_NORM, nebo "" = první (hlavní okno uloží)
    eval_defaults_changed = Signal(int, bool)  # výchozí okno (0 = bez omezení) a pohlaví

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index: VisitIndex | None = None
        self._store: AssessmentStore | None = None
        self._catalog: Catalog | None = None
        self._entries: list[Entry] = []  # všechna hodnocení, nejnovější první
        self._groups: list[list[Entry]] = []  # karta pacienta → jeho vyšetření (nejnovější první)
        self._shown: list[Entry] = []  # karta pacienta → právě vybrané vyšetření
        self._missing: list[RecordingRef] = []
        self._task_quality: dict[str, str] = {}  # úloha → kontrola kvality nahrávek
        self._plans: list[AssessmentPlan] = []
        self._shown_plans: list[AssessmentPlan] = []
        self._norms: list[Norm] = []
        self._norms_dir: Path | None = None
        self._library: Library | None = None
        self._columns: dict[str, ColumnInfo] | None = None
        self._dimensions: list[Dimension] = load_dimensions()
        self._cards: list[PatientCard | PendingCard] = []
        self.visit_buttons: list[QPushButton] = []
        # id hodnocení → (otisk vyšetření, vyhodnocení); otisk = odkazy a čas změny tabulek,
        # takže obnova seznamu nepřepočítává pacienty, kterým se nic nezměnilo
        self._eval_cache: dict[str, tuple[tuple, Evaluation]] = {}
        self._eval_key: tuple = ()  # (norma, okno, pohlaví), pro které cache platí
        self._badge_generation = 0  # líné štítky: starší tik po novém seznamu nic nedělá
        self._current: Visit | None = None
        self._evaluation: Evaluation | None = None
        self._view = "visit"  # „visit“ = pavouk a strom vybraného vyšetření, „trend“ = vývoj
        self._timeline: Timeline | None = None
        self._scale = "sd"  # měřítko Vývoje: „sd“ proti normě, „values“ jednotky feature
        self._form = "charts"  # podoba Vývoje: „charts“ (přehled + grafy), „spider“ (překryv)
        # které řádky přehledu mají graf; None = výchozí (mimo normu / největší změna)
        self._dims_selected: set[str] | None = None
        self._items_selected: set[str] | None = None
        self._trend_visit_ids: list[str] = []  # vyšetření ve Vývoji v pořadí sloupců
        self._norm_choice = ""  # naposledy vybraná norma (cesta), NO_NORM, nebo "" = první
        # okno a pohlaví: výchozí z nastavení (0 = bez omezení), odchylky po pacientech z katalogu
        self._defaults: tuple[int, bool] = (DEFAULT_WINDOW, True)
        self._prefs: dict[str, PatientPrefs] = {}
        self._syncing_prefs = False
        self._value_timeline: ValueTimeline | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        title = QLabel(tr("Pacienti"))
        title.setObjectName("page_title")
        title_row.addWidget(title, 1)
        self.new_btn = QPushButton(tr("Nové hodnocení…"))
        self.new_btn.setToolTip(tr("Pacient, věk, pohlaví a nahrávky po úlohách z hotových běhů"))
        theme.set_role(self.new_btn, "primary")
        self.new_btn.clicked.connect(
            lambda: self.new_assessment()
        )  # ne clicked(checked) do initial
        title_row.addWidget(self.new_btn)
        layout.addLayout(title_row)
        # varianta B (31. 10. 2026): přehled tabulkou přes celou šířku, dvojklik = detail
        self.page_stack = QStackedWidget()
        layout.addWidget(self.page_stack, 1)
        self.overview = PatientOverview()
        self.overview.open_requested.connect(self.open_overview_row)
        self.overview.search_changed.connect(lambda text: self.search.setText(text))
        self.page_stack.addWidget(self.overview)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(8)
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.back_btn = QPushButton(tr("‹ Pacienti"))
        self.back_btn.setFlat(True)
        self.back_btn.setToolTip(tr("Zpět na přehled pacientů (Esc)"))
        self.back_btn.clicked.connect(lambda: self.show_overview())
        nav.addWidget(self.back_btn)
        self.crumb = QLabel("")
        crumb_font = self.crumb.font()
        crumb_font.setBold(True)
        self.crumb.setFont(crumb_font)
        nav.addWidget(self.crumb, 1)
        self.position = QLabel("")
        self.position.setObjectName("muted")
        nav.addWidget(self.position)
        self.prev_btn = QPushButton("‹")
        self.prev_btn.setToolTip(tr("Předchozí pacient (Alt+←)"))
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn = QPushButton("›")
        self.next_btn.setToolTip(tr("Další pacient (Alt+→)"))
        self.next_btn.clicked.connect(lambda: self.step(1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        detail_layout.addLayout(nav)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), detail, lambda: self.show_overview())
        QShortcut(QKeySequence("Alt+Left"), detail, lambda: self.step(-1))
        QShortcut(QKeySequence("Alt+Right"), detail, lambda: self.step(1))
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        detail_layout.addWidget(self.splitter, 1)
        self.page_stack.addWidget(detail)

        # --- seznam vyšetření -----------------------------------------------------------
        left = QWidget()
        left.setMinimumWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        head = QHBoxLayout()
        self.list_title = QLabel(tr("Pacienti"))
        self.list_title.setObjectName("section")
        head.addWidget(self.list_title, 1)
        left_layout.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat pacienta"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill_list)
        left_layout.addWidget(self.search)
        self.visits = QListWidget()
        self.visits.setObjectName("cards")
        self.visits.setSpacing(3)
        self.visits.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.visits.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.visits.currentItemChanged.connect(self._item_changed)
        self.visits.viewport().installEventFilter(self)
        left_layout.addWidget(self.visits, 1)
        self.empty = QLabel(
            tr(
                "Zatím žádné hodnocení. Založte ho tlačítkem Nové hodnocení…, nebo na "
                "Výsledcích pravým tlačítkem na nahrávce."
            )
        )
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.empty)
        self.empty.hide()
        self.splitter.addWidget(left)
        left.hide()  # seznam karet zůstává jako zdroj výběru, ukazuje se přehled tabulkou
        self.list_panel = left

        # --- pravá část -------------------------------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(8)
        self.header = StatusHeader(subtitle_below=True)  # podtitul pod tlačítky
        self.header.title.setText(tr("Pacient"))
        self.header.subtitle.setText(tr("Vyberte pacienta vlevo, nebo založte nové hodnocení."))
        self.edit_btn = QPushButton(tr("Upravit…"))
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self.edit_current)
        self.header.add_button(self.edit_btn)
        self.recompute_btn = QPushButton(tr("Přepočítat…"))
        self.recompute_btn.setEnabled(False)
        self.recompute_btn.clicked.connect(lambda: self._recompute_current())
        self.header.add_button(self.recompute_btn)
        self.image_btn = QPushButton(tr("Uložit obrázek…"))
        self.image_btn.setToolTip(tr("Pavouk jako PNG, třeba do zprávy."))
        self.image_btn.setEnabled(False)
        self.image_btn.clicked.connect(lambda: self.save_image())  # bez `checked` jako cesty
        self.header.add_button(self.image_btn)
        self.delete_btn = QPushButton(tr("Smazat…"))
        theme.set_role(self.delete_btn, "danger")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self.delete_current)
        self.header.add_button(self.delete_btn)
        right_layout.addWidget(self.header)
        # řada vyšetření pacienta: vybrané je modré, ostatní obyčejná, vpravo Nové vyšetření…
        self.visit_bar = QWidget()
        visit_layout = QHBoxLayout(self.visit_bar)
        visit_layout.setContentsMargins(4, 0, 0, 0)
        visit_layout.setSpacing(6)
        visit_label = QLabel(tr("Vyšetření:"))
        visit_label.setObjectName("muted")
        visit_layout.addWidget(visit_label)
        self.visit_layout = QHBoxLayout()
        self.visit_layout.setSpacing(6)
        visit_layout.addLayout(self.visit_layout)
        visit_layout.addStretch(1)
        self.visit_btn = QPushButton(tr("Nové vyšetření…"))
        self.visit_btn.setToolTip(tr("Další vyšetření téhož pacienta z hotových běhů"))
        self.visit_btn.clicked.connect(lambda: self.new_visit())
        visit_layout.addWidget(self.visit_btn)
        right_layout.addWidget(self.visit_bar)
        self.visit_bar.hide()
        # přepínač Vyšetření · Vývoj v čase (vývoj až od dvou vyšetření s datem a s normou)
        self.view_bar = QWidget()
        view_layout = QHBoxLayout(self.view_bar)
        view_layout.setContentsMargins(4, 0, 0, 0)
        view_layout.setSpacing(6)
        self.view_visit_btn = QPushButton(tr("Vyšetření"))
        self.view_trend_btn = QPushButton(tr("Vývoj v čase"))
        self.view_group = QButtonGroup(self)
        for button, view in ((self.view_visit_btn, "visit"), (self.view_trend_btn, "trend")):
            button.setCheckable(True)
            self.view_group.addButton(button)
            button.clicked.connect(lambda _c=False, v=view: self.set_view(v))
            view_layout.addWidget(button)
        self.view_visit_btn.setChecked(True)
        self.view_trend_btn.setEnabled(False)
        self.view_trend_btn.setToolTip(tr("Vývoj je k dispozici od dvou vyšetření s datem."))
        view_layout.addStretch(1)
        # druhý řádek jen pro Vývoj: podoba, měřítko, filtr (v jedné řadě se nevešly)
        self.trend_bar = QWidget()
        trend_layout = QHBoxLayout(self.trend_bar)
        trend_layout.setContentsMargins(4, 0, 0, 0)
        trend_layout.setSpacing(6)
        view_layout = trend_layout  # další ovládání jde na druhý řádek
        # podoba Vývoje: Pavouk (překryv, jen s normou do tří vyšetření) · Mapa · Grafy
        self.form_label = QLabel(tr("Podoba:"))
        self.form_label.setObjectName("muted")
        view_layout.addWidget(self.form_label)
        self.form_spider_btn = QPushButton(tr("Pavouk"))
        self.form_charts_btn = QPushButton(tr("Grafy"))
        self.form_group = QButtonGroup(self)
        for button, form in (
            (self.form_spider_btn, "spider"),
            (self.form_charts_btn, "charts"),
        ):
            button.setCheckable(True)
            self.form_group.addButton(button)
            button.clicked.connect(lambda _c=False, f=form: self.set_form(f))
            button.hide()
            view_layout.addWidget(button)
        self.form_label.hide()
        self.form_charts_btn.setChecked(True)
        self.form_charts_btn.setToolTip(
            tr("Přehled dimenzí po vyšetřeních a grafy zaškrtnutých dimenzí.")
        )
        # měřítko Vývoje: SD proti normě (jen s normou) · Hodnoty v jednotkách feature
        view_layout.addSpacing(12)
        self.scale_label = QLabel(tr("Měřítko:"))
        self.scale_label.setObjectName("muted")
        view_layout.addWidget(self.scale_label)
        self.scale_sd_btn = QPushButton(tr("SD proti normě"))
        self.scale_sd_btn.setToolTip(tr("Skóre dimenzí v SD proti vybrané normě."))
        self.scale_values_btn = QPushButton(tr("Hodnoty"))
        self.scale_values_btn.setToolTip(
            tr("Hodnoty položek v jednotkách feature, jde i bez normy.")
        )
        self.scale_group = QButtonGroup(self)
        for button, scale in ((self.scale_sd_btn, "sd"), (self.scale_values_btn, "values")):
            button.setCheckable(True)
            self.scale_group.addButton(button)
            button.clicked.connect(lambda _c=False, s=scale: self.set_scale(s))
            view_layout.addWidget(button)
        self.scale_sd_btn.setChecked(True)
        self.scale_note = QLabel(tr("potřebuje normu"))  # krátké, ať lišta neroste
        self.scale_note.setObjectName("muted")
        view_layout.addWidget(self.scale_note)
        for widget in (self.scale_label, self.scale_sd_btn, self.scale_values_btn, self.scale_note):
            widget.hide()
        view_layout.addStretch(1)
        # přepínač a řádek Vývoje nad řadou vyšetření: řada se ve Vývoji schovává, a kdyby
        # byla nad přepínačem, přepínač by při každém přepnutí poskočil (31. 10. 2026)
        anchor = right_layout.indexOf(self.visit_bar)
        right_layout.insertWidget(anchor, self.view_bar)
        self.view_bar.hide()
        right_layout.insertWidget(anchor + 1, self.trend_bar)
        self.trend_bar.hide()
        # pod hlavičkou se roluje: pavouk a strom pod sebou se na menší okno nevejdou
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_layout.addWidget(self.scroll, 1)
        content = QWidget()
        self.scroll.setWidget(content)
        right_layout = QVBoxLayout(content)
        right_layout.setContentsMargins(0, 0, 8, 0)
        right_layout.setSpacing(8)

        norm_row = QHBoxLayout()
        norm_row.setSpacing(10)
        norm_row.addWidget(QLabel(tr("Norma")))
        self.norm_combo = QComboBox()
        self.norm_combo.setMinimumWidth(260)
        self.norm_combo.currentIndexChanged.connect(self._norm_changed)
        norm_row.addWidget(self.norm_combo)
        norm_row.addWidget(QLabel(tr("Věkové okno")))
        self.window_combo = QComboBox()
        for label, value in WINDOWS:
            self.window_combo.addItem(label, value)
        self.window_combo.setCurrentIndex(self.window_combo.findData(DEFAULT_WINDOW))
        self.window_combo.setToolTip(tr("Platí pro vybraného pacienta a všechna jeho vyšetření."))
        self.window_combo.currentIndexChanged.connect(self._prefs_changed)
        norm_row.addWidget(self.window_combo)
        self.same_sex = QCheckBox(tr("jen stejné pohlaví"))
        self.same_sex.setChecked(True)
        self.same_sex.setToolTip(tr("Platí pro vybraného pacienta a všechna jeho vyšetření."))
        self.same_sex.toggled.connect(self._prefs_changed)
        norm_row.addWidget(self.same_sex)
        self.selection_label = QLabel("")
        self.selection_label.setObjectName("muted")
        self.selection_label.setWordWrap(True)
        # odchylka od výchozího: řekne to a nabídne z ní udělat výchozí pro všechny
        self.prefs_label = QLabel(
            tr(
                'Norma, okno a pohlaví platí jen pro tohoto pacienta · <a href="default">nastavit '
                "jako výchozí pro všechny</a>"
            )
        )
        self.prefs_label.setObjectName("muted")
        self.prefs_label.setTextFormat(Qt.TextFormat.RichText)
        self.prefs_label.setOpenExternalLinks(False)
        self.prefs_label.linkActivated.connect(lambda _link: self._make_default())
        self.prefs_label.hide()
        norm_row.addStretch(1)
        norm_block = QVBoxLayout()
        norm_block.setContentsMargins(0, 0, 0, 0)
        norm_block.setSpacing(2)
        norm_block.addLayout(norm_row)
        norm_block.addWidget(self.selection_label)  # věta pod ovládáním, ne vedle něj
        norm_block.addWidget(self.prefs_label)
        self.norm_row = QWidget()
        self.norm_row.setLayout(norm_block)
        right_layout.addWidget(self.norm_row)

        self.no_norm = QLabel(
            tr(
                "Není k dispozici žádná norma zdravých kontrol. Normu dodá pracoviště "
                "jako soubor, nebo ji vyrobíte z nahrávek zdravých kontrol; obojí je "
                "na stránce Normy."
            )
        )
        self.no_norm.setObjectName("muted")
        self.no_norm.setWordWrap(True)
        right_layout.addWidget(self.no_norm)
        self.no_norm.hide()

        # pavouk přes celou šířku, vysvětlivky vedle něj, strom pod ním (celá šířka)
        middle = QVBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(12)
        self.chart_card = QFrame()
        self.chart_card.setObjectName("card")
        self.chart_card.setMinimumHeight(420)
        chart_layout = QVBoxLayout(self.chart_card)
        chart_layout.setContentsMargins(12, 10, 12, 10)
        chart_layout.setSpacing(6)
        chart_title = QLabel(tr("Odchylka od normy po dimenzích"))
        chart_title.setObjectName("card_title")
        chart_layout.addWidget(chart_title)
        self.chart_hint = QLabel("")
        self.chart_hint.setObjectName("muted")
        self.chart_hint.setWordWrap(True)
        chart_layout.addWidget(self.chart_hint)
        chart_row = QHBoxLayout()
        chart_row.setSpacing(16)
        self.chart = SpiderChart()
        chart_row.addWidget(self.chart, 1)
        side = QVBoxLayout()
        side.setSpacing(10)
        self.scale = ScaleLegend()  # co znamenají kružnice; pavouk má uvnitř jen „norma“
        side.addWidget(self.scale)
        legend = tr(
            "● do 1 SD v normě · ● 1 až 2 SD hraniční · ● nad 2 SD mimo normu · "
            "○ pod −1 SD lepší než kontroly"
        )
        colors = (theme.OK, theme.WARN, theme.MISSING, theme.NEUTRAL)  # tečky v barvách pásem
        self.legend = QLabel(
            "<br>".join(
                part.replace(part[0], f"<span style='color:{color}'>{part[0]}</span>", 1)
                for part, color in zip(legend.split(" · "), colors, strict=False)
            )
        )
        self.legend.setObjectName("muted")
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend.setWordWrap(True)
        side.addWidget(self.legend)
        # orientace pavouka: rozhodnuto 29. 10. 2026 ven (varianta I), přepínač je skrytý,
        # dovnitř (2b) zůstává v kódu pro případnou diskusi s týmem
        self.orientation_combo = QComboBox()
        self.orientation_combo.hide()
        self.orientation_combo.addItem(tr("větší plocha = zdravější"), INWARD)
        self.orientation_combo.addItem(tr("dál od středu = horší"), OUTWARD)
        self.orientation_combo.setToolTip(
            tr("Směr pavouka: patologie dovnitř (střed), nebo ven (okraj).")
        )
        self.orientation_combo.currentIndexChanged.connect(self._orientation_picked)
        side.addWidget(self.orientation_combo)
        self.missing_label = QLabel("")
        self.missing_label.setObjectName("muted")
        self.missing_label.setWordWrap(True)
        side.addWidget(self.missing_label)
        side.addStretch(1)
        side_box = QWidget()
        side_box.setLayout(side)
        side_box.setFixedWidth(280)
        chart_row.addWidget(side_box)
        chart_layout.addLayout(chart_row, 1)
        middle.addWidget(self.chart_card)

        tree_card = QFrame()
        tree_card.setObjectName("card")
        tree_layout = QVBoxLayout(tree_card)
        tree_layout.setContentsMargins(12, 10, 12, 10)
        tree_layout.setSpacing(6)
        tree_top = QHBoxLayout()
        tree_top.setSpacing(8)
        tree_title = QLabel(tr("Dimenze a jejich položky"))
        tree_title.setObjectName("card_title")
        tree_top.addWidget(tree_title)
        tree_top.addStretch(1)
        self.expand_all_btn = QPushButton(tr("rozbalit vše"))
        self.expand_all_btn.clicked.connect(lambda: self.tree.expandAll())
        tree_top.addWidget(self.expand_all_btn)
        self.collapse_all_btn = QPushButton(tr("sbalit vše"))
        self.collapse_all_btn.clicked.connect(lambda: self.tree.collapseAll())
        tree_top.addWidget(self.collapse_all_btn)
        tree_layout.addLayout(tree_top)
        self.tree_hint = QLabel("")
        self.tree_hint.setObjectName("muted")
        self.tree_hint.setWordWrap(True)
        tree_layout.addWidget(self.tree_hint)
        # podoba A z mockupu docs/mockups/strom-dimenzi: osa odchylky v řádku, stupnice
        # v záhlaví; logické sloupce zůstávají (osa = 6), osa se jen přesune před z
        self.tree = DimensionTree(TREE_AXIS)
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(
            [
                tr("dimenze / položka").upper(),
                tr("úloha").upper(),
                tr("pacient").upper(),
                tr("norma").upper(),
                "SD",
                tr("percentil").upper(),
                "",
            ]
        )
        self.tree.setAlternatingRowColors(False)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setFrameShape(QFrame.Shape.NoFrame)
        self.tree.setItemDelegate(DimensionTreeDelegate(self.tree))
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background: {theme.CARD}; border: none; }}"
            f"QTreeWidget::item {{ border-bottom: 1px solid {theme.NEUTRAL_SOFT}; }}"
            f"QHeaderView::section {{ background: {theme.CARD}; color: {theme.MUTED};"
            f" border: none; border-bottom: 1px solid {theme.BORDER}; padding: 6px;"
            " font-weight: 600; }"
        )
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(40)
        header.moveSection(header.visualIndex(TREE_AXIS), 4)
        for col in (2, 3, 4, 5):
            self.tree.headerItem().setTextAlignment(
                col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        self._set_norm_columns(True)
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)
        # strom je vysoký podle obsahu (`fit_height`), roluje celá stránka, ne blok
        tree_layout.addWidget(self.tree)
        middle.addWidget(tree_card)
        self.middle = QWidget()
        self.middle.setLayout(middle)
        right_layout.addWidget(self.middle, 1)

        # vývoj v čase: tři podoby (mockup https://claude.ai/artifact/DgxUjxJmh5SAihwZ4SkQog):
        # překryv v pavouku, mapa dimenze × vyšetření, mřížka malých grafů s tabulkou
        trend = QVBoxLayout()
        trend.setContentsMargins(0, 0, 0, 0)
        trend.setSpacing(12)
        self.spider_card = QFrame()
        self.spider_card.setObjectName("card")
        spider_layout = QVBoxLayout(self.spider_card)
        spider_layout.setContentsMargins(12, 10, 12, 10)
        spider_layout.setSpacing(6)
        spider_title = QLabel(tr("Vyšetření v jednom pavouku"))
        spider_title.setObjectName("card_title")
        spider_layout.addWidget(spider_title)
        spider_hint = QLabel(
            tr(
                "Poslední vyšetření černě s body barvou pásma, starší modře čárkovaně "
                "s dutými body. Čitelné do tří vyšetření."
            )
        )
        spider_hint.setObjectName("muted")
        spider_hint.setWordWrap(True)
        spider_layout.addWidget(spider_hint)
        spider_row = QHBoxLayout()
        spider_row.setSpacing(12)
        self.trend_spider = SpiderChart()
        self.trend_spider.setMinimumHeight(440)
        spider_row.addWidget(self.trend_spider, 1)
        self.spider_legend = QLabel("")
        self.spider_legend.setObjectName("muted")
        self.spider_legend.setTextFormat(Qt.TextFormat.RichText)
        self.spider_legend.setWordWrap(True)
        self.spider_legend.setFixedWidth(220)
        self.spider_legend.setAlignment(Qt.AlignmentFlag.AlignTop)
        spider_row.addWidget(self.spider_legend)
        spider_layout.addLayout(spider_row)
        trend.addWidget(self.spider_card)
        self.spider_card.hide()
        self.map_card = QFrame()
        self.map_card.setObjectName("card")
        map_layout = QVBoxLayout(self.map_card)
        map_layout.setContentsMargins(12, 10, 12, 10)
        map_layout.setSpacing(6)
        map_head = QHBoxLayout()
        map_head.setSpacing(8)
        self.map_title = QLabel(tr("Dimenze po vyšetřeních"))
        self.map_title.setObjectName("card_title")
        map_head.addWidget(self.map_title)
        map_head.addStretch(1)
        self.map_all_btn = QPushButton(tr("vše"))
        self.map_all_btn.setToolTip(tr("Graf pro všechny řádky"))
        self.map_all_btn.clicked.connect(lambda: self.trend_map.set_all(True))
        self.map_none_btn = QPushButton(tr("nic"))
        self.map_none_btn.setToolTip(tr("Schovat všechny grafy"))
        self.map_none_btn.clicked.connect(lambda: self.trend_map.set_all(False))
        for button in (self.map_all_btn, self.map_none_btn):
            button.setFlat(True)
            map_head.addWidget(button)
        map_layout.addLayout(map_head)
        self.map_hint = QLabel("")
        self.map_hint.setObjectName("muted")
        self.map_hint.setWordWrap(True)
        map_layout.addWidget(self.map_hint)
        self.trend_map = TrendMap()
        self.trend_map.visit_clicked.connect(self._trend_visit_clicked)
        self.trend_map.selection_changed.connect(self._map_selection_changed)
        map_layout.addWidget(self.trend_map)
        trend.addWidget(self.map_card)
        self.grid_card = QFrame()
        grid_card = self.grid_card
        grid_card.setObjectName("card")
        grid_layout = QVBoxLayout(grid_card)
        grid_layout.setContentsMargins(12, 10, 12, 10)
        grid_layout.setSpacing(6)
        self.grid_title = QLabel(tr("Vývoj po dimenzích"))
        self.grid_title.setObjectName("card_title")
        grid_layout.addWidget(self.grid_title)
        self.grid_hint = QLabel("")
        self.grid_hint.setObjectName("muted")
        self.grid_hint.setWordWrap(True)
        grid_layout.addWidget(self.grid_hint)
        self.trend_grid = TrendGrid()
        grid_layout.addWidget(self.trend_grid)
        trend.addWidget(grid_card)
        self.table_card = QFrame()
        table_card = self.table_card
        table_card.setObjectName("card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(12, 10, 12, 10)
        table_layout.setSpacing(6)
        self.table_title = QLabel(tr("Dimenze po vyšetřeních"))
        self.table_title.setObjectName("card_title")
        table_layout.addWidget(self.table_title)
        self.trend_table = QTableWidget()
        self.trend_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.trend_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.trend_table.verticalHeader().setVisible(False)
        trend_header = self.trend_table.horizontalHeader()
        trend_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        trend_header.setStretchLastSection(False)
        table_layout.addWidget(self.trend_table)
        trend.addWidget(table_card)
        self.trend_note = QLabel("")  # poznámky pod všemi podobami
        self.trend_note.setObjectName("muted")
        self.trend_note.setWordWrap(True)
        trend.addWidget(self.trend_note)
        trend.addStretch(1)
        self.trend_panel = QWidget()
        self.trend_panel.setLayout(trend)
        right_layout.addWidget(self.trend_panel, 1)
        self.trend_panel.hide()

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        rec_card = QFrame()
        rec_card.setObjectName("card")
        rec_layout = QVBoxLayout(rec_card)
        rec_layout.setContentsMargins(12, 10, 12, 10)
        rec_layout.setSpacing(6)
        rec_title = QLabel(tr("Nahrávky hodnocení"))
        rec_title.setObjectName("card_title")
        rec_layout.addWidget(rec_title)
        self.recordings = QTableWidget()
        self.recordings.setColumnCount(5)
        self.recordings.setHorizontalHeaderLabels(
            [tr("úloha"), tr("nahrávka"), tr("běh"), tr("s normou"), tr("kvalita")]
        )
        self.recordings.verticalHeader().setVisible(False)
        self.recordings.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recordings.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recordings.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        rec_header = self.recordings.horizontalHeader()
        rec_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        rec_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        rec_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        rec_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        rec_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        rec_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.recordings.setMaximumHeight(170)
        self.recordings.cellDoubleClicked.connect(lambda row, _col: self._detail_row(row))
        self.recordings.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recordings.customContextMenuRequested.connect(self._recording_menu)
        rec_layout.addWidget(self.recordings)
        self.recordings_note = QLabel("")
        self.recordings_note.setObjectName("muted")
        self.recordings_note.setWordWrap(True)
        rec_layout.addWidget(self.recordings_note)
        bottom.addWidget(rec_card, 1)
        about_card = QFrame()
        about_card.setObjectName("card")
        about_layout = QVBoxLayout(about_card)
        about_layout.setContentsMargins(12, 10, 12, 10)
        about_layout.setSpacing(6)
        about_title = QLabel(tr("O normě"))
        about_title.setObjectName("card_title")
        about_layout.addWidget(about_title)
        self.about = QLabel("")
        self.about.setObjectName("muted")
        self.about.setWordWrap(True)
        self.about.setAlignment(Qt.AlignmentFlag.AlignTop)
        about_layout.addWidget(self.about, 1)
        self.disclaimer = QLabel(
            tr(
                "Orientační srovnání s normou, ne diagnóza. Odchylky závisí na nahrávacích "
                "podmínkách a na kvalitě přepisu."
            )
        )
        self.disclaimer.setObjectName("muted")
        self.disclaimer.setWordWrap(True)
        about_layout.addWidget(self.disclaimer)
        bottom.addWidget(about_card, 1)
        self.bottom = QWidget()
        self.bottom.setLayout(bottom)
        right_layout.addWidget(self.bottom)

        self.right = right
        self.splitter.addWidget(right)
        self.plan_detail = self._build_plan_detail()
        self.splitter.addWidget(self.plan_detail)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 1)
        self.splitter.setSizes([320, 800, 800])
        self._show_empty_state()

    def _build_plan_detail(self) -> QWidget:
        """Detail rozpracovaného hodnocení: dávky po úlohách se stavem a akce."""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(10)
        self.plan_header = StatusHeader(subtitle_below=True)
        self.resume_btn = QPushButton(tr("Dopočítat zbývající"))
        theme.set_role(self.resume_btn, "primary")
        self.resume_btn.clicked.connect(lambda: self._plan_action(self.resume_requested))
        self.build_btn = QPushButton(tr("Založit z hotových"))
        self.build_btn.setToolTip(tr("Hodnocení jen z dávek, které doběhly"))
        self.build_btn.clicked.connect(lambda: self._plan_action(self.build_requested))
        self.discard_btn = QPushButton(tr("Zahodit…"))
        theme.set_role(self.discard_btn, "danger")
        self.discard_btn.clicked.connect(lambda: self._plan_action(self.discard_requested))
        for btn in (self.resume_btn, self.build_btn, self.discard_btn):
            self.plan_header.add_button(btn)
        layout.addWidget(self.plan_header)
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        title = QLabel(tr("Dávky hodnocení"))
        title.setObjectName("card_title")
        card_layout.addWidget(title)
        self.plan_table = QTableWidget()
        self.plan_table.setColumnCount(4)
        self.plan_table.setHorizontalHeaderLabels(
            [tr("úloha"), tr("nahrávek"), tr("protokol"), tr("stav")]
        )
        self.plan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.plan_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.plan_table.verticalHeader().setVisible(False)
        header = self.plan_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        card_layout.addWidget(self.plan_table)
        layout.addWidget(card)
        note = QLabel(
            tr(
                "Dávky se počítají na stránce Výpočet. Po poslední se hodnocení založí samo "
                "a ukáže se pavouk. Když některá dávka spadla nebo se zrušila, dopočítejte "
                "ji, nebo hodnocení založte z hotových."
            )
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return box

    def _plan_action(self, signal) -> None:  # noqa: ANN001
        plan = self.current_plan()
        if plan is not None:
            signal.emit(plan.key)

    def _show_plan(self, plan: AssessmentPlan) -> None:
        from .norms import plan_status_text  # stejné popisky stavů jako u normy

        p = plan.patient
        self.plan_header.title.setText(p.subject)
        visit = Visit(p.subject, p.date, True, age=p.age, sex=p.sex)
        self.plan_header.subtitle.setText(
            visit_subtitle(visit).rsplit(" · ", 1)[0] + tr(" · rozpracované hodnocení")
        )
        text, role = plan_pill(plan)
        self.plan_header.set_pills([(text, role, "", None)])
        self.plan_header.set_role(role)
        self.plan_table.setRowCount(len(plan.tasks))
        for row, task in enumerate(plan.tasks):
            cells = [
                tasks.long_label(task.task),
                str(len(task.inputs)),
                task.proto().display_name,
                plan_status_text(task.status),
            ]
            for col, value in enumerate(cells):
                self.plan_table.setItem(row, col, QTableWidgetItem(value))
        height = self.plan_table.horizontalHeader().height() + 4
        height += sum(self.plan_table.rowHeight(r) for r in range(len(plan.tasks)))
        self.plan_table.setFixedHeight(height)
        running = any(t.status == RUNNING for t in plan.tasks)
        self.build_btn.setEnabled(plan.buildable and not running)
        self.resume_btn.setEnabled(bool(plan.remaining()))
        self.discard_btn.setEnabled(not running)

    # --- napojení --------------------------------------------------------------------------

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self._columns = None

    def set_orientation(self, orientation: str) -> None:
        """Orientace pavouka z nastavení (bez signálu zpět)."""
        index = self.orientation_combo.findData(orientation)
        if index >= 0 and index != self.orientation_combo.currentIndex():
            self.orientation_combo.blockSignals(True)
            self.orientation_combo.setCurrentIndex(index)
            self.orientation_combo.blockSignals(False)
        self._apply_orientation(orientation)

    def _orientation_picked(self, _index: int) -> None:
        orientation = str(self.orientation_combo.currentData())
        self._apply_orientation(orientation)
        self.orientation_changed.emit(orientation)

    def _apply_orientation(self, orientation: str) -> None:
        self.chart.set_orientation(orientation)
        self.scale.set_orientation(orientation)
        self.chart_hint.setText(
            tr(
                "čím větší plocha, tím blíž zdravým: čárkovaná kružnice je průměr kontrol, "
                "blíž ke středu = horší, u okraje = lepší než kontroly"
            )
            if orientation == INWARD
            else tr(
                "čárkovaná kružnice je průměr kontrol, zelený prstenec ±1 SD je v normě, "
                "dál od středu = horší, bílý střed = lepší než kontroly"
            )
        )

    def set_catalog(self, catalog: Catalog) -> None:
        """Katalog dřív než složky: index běhů i úložiště hodnocení ho potřebují."""
        self._catalog = catalog

    def set_work_root(self, root: Path) -> None:
        if self._index is None:
            self._index = VisitIndex(root, catalog=self._catalog)
        else:
            self._index.set_work_root(root)

    def set_assessments_dir(self, folder: Path) -> None:
        if self._catalog is None:
            self._catalog = Catalog(None)  # bez hlavního okna (testy): katalog v paměti
        self._store = AssessmentStore(folder, self._catalog)
        self._prefs = self._catalog.patient_prefs()
        self.refresh()

    def set_norms_dir(self, folder: Path) -> None:
        self._norms_dir = folder
        self.reload_norms()

    def reload_norms(self) -> None:
        """Znovu načte normy (po importu, uložení nebo smazání) a zachová výběr."""
        self._norms = all_norms(self._norms_dir) if self._norms_dir is not None else []
        self.norm_combo.blockSignals(True)
        self.norm_combo.clear()
        for norm in self._norms:
            self.norm_combo.addItem(norm.info.display_name, norm)
            tip = norm.info.display_description
            tip += tr(" ({n} osob)").format(n=norm.subjects) if norm.subjects else ""
            self.norm_combo.setItemData(
                self.norm_combo.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
            )
        # výslovná volba „žádná“: surové hodnoty i tam, kde norma existuje (31. 10. 2026)
        self.norm_combo.addItem(tr("žádná norma (jen hodnoty)"), NO_NORM)
        self.norm_combo.setCurrentIndex(self._norm_index(self._norm_choice))
        self.norm_combo.blockSignals(False)
        has = bool(self._norms)
        self.norm_row.setVisible(has)
        self.no_norm.setVisible(not has)
        self._sync_norm_choice()
        self._recompute()

    def _norm_index(self, choice: str) -> int:
        """Index v nabídce pro volbu z nastavení: cesta normy, NO_NORM, jinak první norma."""
        if choice == NO_NORM:
            return self.norm_combo.count() - 1
        for i, norm in enumerate(self._norms):
            if str(norm.path) == choice:
                return i
        return 0

    def _sync_norm_choice(self) -> None:
        """Zapamatovaná volba = co je opravdu vybrané (zmizelá norma spadne na první)."""
        if not self._norms:
            return  # bez jediné normy není „žádná“ volba, ale nutnost; volbu nepřepisovat
        data = self.norm_combo.currentData()
        choice = NO_NORM if data == NO_NORM else str(data.path) if isinstance(data, Norm) else ""
        if choice != self._norm_choice:
            self._norm_choice = choice
            self.norm_choice_changed.emit(choice)

    def _norm_changed(self, _index: int) -> None:
        """Nabídka Norma: bez vybraného pacienta mění výchozí normu, jinak jen normu
        vybraného pacienta (odchylka v katalogu jako okno a pohlaví, 31. 10. 2026)."""
        if self._syncing_prefs:
            return
        if self._current is None or not self._norms:
            self._sync_norm_choice()
        else:
            self._store_norm_override()
        self._recompute()

    def _global_key(self) -> str:
        """Výchozí norma jako klíč: NO_NORM, cesta, nebo první norma (prázdná volba)."""
        if self._norm_choice == NO_NORM or not self._norms:
            return NO_NORM if self._norm_choice == NO_NORM or not self._norms else ""
        if any(str(n.path) == self._norm_choice for n in self._norms):
            return self._norm_choice
        return str(self._norms[0].path)

    def _norm_key_for(self, subject: str) -> str:
        """Norma pacienta jako klíč: jeho odchylka, pokud ta norma existuje, jinak výchozí."""
        prefs = self._prefs.get(patient_key(subject), PatientPrefs())
        if prefs.norm == NO_NORM:
            return NO_NORM
        if prefs.norm and any(str(n.path) == prefs.norm for n in self._norms):
            return prefs.norm
        return self._global_key()

    def _norm_by_key(self, key: str) -> Norm | None:
        if key == NO_NORM:
            return None
        return next((n for n in self._norms if str(n.path) == key), None)

    def _norm_for(self, subject: str) -> Norm | None:
        return self._norm_by_key(self._norm_key_for(subject))

    def _store_norm_override(self) -> None:
        data = self.norm_combo.currentData()
        chosen = NO_NORM if data == NO_NORM else str(data.path) if isinstance(data, Norm) else ""
        key = patient_key(self._current.subject)
        existing = self._prefs.get(key, PatientPrefs())
        prefs = PatientPrefs(
            existing.window,
            existing.same_sex,
            None if chosen == self._global_key() else chosen,
        )
        if prefs.empty:
            self._prefs.pop(key, None)
        else:
            self._prefs[key] = prefs
        if self._catalog is not None:
            self._catalog.set_patient_prefs(key, prefs)

    def norm_choice(self) -> str:
        return self._norm_choice

    def set_norm_choice(self, choice: str) -> None:
        """Volba z nastavení při startu (bez signálu zpět); normy se mohou načíst až potom."""
        self._norm_choice = choice
        if self.norm_combo.count():
            self.norm_combo.blockSignals(True)
            self.norm_combo.setCurrentIndex(self._norm_index(choice))
            self.norm_combo.blockSignals(False)
            self._sync_norm_choice()
            self._recompute()

    def current_norm(self) -> Norm | None:
        """Norma vybraného pacienta; bez pacienta výchozí norma."""
        if self._current is not None:
            return self._norm_for(self._current.subject)
        return self._norm_by_key(self._global_key())

    def current_visit(self) -> Visit | None:
        return self._current

    def current_assessment(self) -> Assessment | None:
        row = self.visits.currentRow() - len(self._shown_plans)
        return self._shown[row][0] if 0 <= row < len(self._shown) else None

    def assessments(self) -> list[Assessment]:
        return [a for a, _v, _m in self._entries]

    def evaluation(self) -> Evaluation | None:
        return self._evaluation

    def columns(self) -> dict[str, ColumnInfo]:
        if self._columns is None:
            catalog: list[FeatureInfo] = []
            if self._library is not None:
                try:
                    catalog = self._library.features()
                except LibraryError:
                    catalog = []
            self._columns = column_index(catalog)
        return self._columns

    # --- seznam -------------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.visits.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_cards()
        return super().eventFilter(obj, event)

    def _fit_cards(self) -> None:
        width = self.visits.viewport().width() - 2 * self.visits.spacing() - 4
        if width <= 0:
            return
        for row in range(self.visits.count()):
            item = self.visits.item(row)
            card = self.visits.itemWidget(item)
            if card is not None:
                card.setFixedWidth(width)
                item.setSizeHint(QSize(width, card.sizeHint().height()))

    def refresh(self, select: str | None = None) -> None:
        """Znovu načte hodnocení a běhy; výběr zůstane na témže hodnocení (podle id)."""
        current = self.current_assessment()
        keep = select or (current.id if current is not None else None)
        recordings = []
        if self._index is not None:
            self._index.refresh()
            recordings = self._index.all_recordings()
        stored = self._store.all() if self._store is not None else []
        self._entries = [(a, *resolve(a, recordings)) for a in stored]
        known = {a.id for a in stored}
        for gone in [k for k in self._eval_cache if k not in known]:
            del self._eval_cache[gone]  # změněné tabulky pozná otisk, smazaná hodnocení tady
        self._plans = pending_plans(self._store.folder) if self._store is not None else []
        self._fill_list(keep=keep)
        if select is not None:
            self.show_detail()  # odkaz odjinud nebo nově uložené hodnocení: rovnou pacient

    def recordings_pool(self) -> list[Recording]:
        """Nahrávky z hotových běhů (bez běhů norem) pro dialog hodnocení."""
        if self._index is None:
            return []
        self._index.refresh()
        return self._index.all_recordings()

    def _visit_band(self, assessment: Assessment, visit: Visit) -> str:
        norm = self._norm_for(visit.subject)
        if norm is None:
            return ""
        return worst_band(self._cached_evaluation(assessment, visit, norm))

    def _fill_list(self, keep: str | None = None) -> None:
        if keep is None:
            current = self.current_assessment()
            keep = current.id if current is not None else None
        needle = self.search.text().strip().lower()
        self.visits.blockSignals(True)
        self.visits.clear()
        self._cards = []
        shown: list[tuple[Assessment, Visit, list[RecordingRef]]] = []
        select_row: int | None = None
        self._shown_plans = []
        for plan in self._plans:  # rozpracovaná nahoře
            if needle and needle not in plan.patient.subject.lower():
                continue
            item = QListWidgetItem()
            card = PendingCard(plan)
            item.setSizeHint(card.sizeHint())
            self.visits.addItem(item)
            self.visits.setItemWidget(item, card)
            self._cards.append(card)
            if keep is not None and plan.id == keep:
                select_row = len(self._shown_plans)
            self._shown_plans.append(plan)
        pending_ids = {plan.id for plan in self._plans}  # přepočet: jen karta „počítá se“
        # karta na pacienta; `_entries` jsou od nejnovějšího, takže první je poslední vyšetření
        groups: dict[str, list[Entry]] = {}
        patients: set[str] = set()
        for entry in self._entries:
            assessment = entry[0]
            if assessment.id in pending_ids:
                continue
            key = patient_key(assessment.subject)
            patients.add(key)
            if needle and needle not in assessment.subject.lower():
                continue
            groups.setdefault(key, []).append(entry)
        self._groups = []
        for entries in groups.values():
            chosen = next((e for e in entries if e[0].id == keep), entries[0])
            item = QListWidgetItem()
            card = PatientCard(entries, self._known_band(entries[0][0], entries[0][1]))
            item.setSizeHint(card.sizeHint())
            self.visits.addItem(item)
            self.visits.setItemWidget(item, card)
            self._cards.append(card)
            if keep is not None and chosen[0].id == keep:
                select_row = len(self._shown_plans) + len(shown)
            shown.append(chosen)
            self._groups.append(entries)
        self._shown = shown
        self._fit_cards()
        self.visits.blockSignals(False)
        for card in self._cards:
            if isinstance(card, PatientCard):
                card.on_band = self._card_band_changed
        self._fill_overview(select_row if select_row is not None else 0)
        total = len(self._plans) + len(patients)
        self.list_title.setText(tr("Pacienti ({n})").format(n=total) if total else tr("Pacienti"))
        self.empty.setVisible(not total)
        if select_row is not None:
            self.visits.setCurrentRow(select_row)
        elif shown or self._shown_plans:
            self.visits.setCurrentRow(0)
        else:
            self._current = None
            self._missing = []
            self._show_empty_state()
        self._schedule_badges()

    # --- líné štítky karet ------------------------------------------------------------------

    def _known_band(self, assessment: Assessment, visit: Visit) -> str:
        """Pásmo z cache; bez normy prázdné, jinak `PENDING_BAND` (dopočítá se po zobrazení)."""
        norm = self._norm_for(visit.subject)
        if norm is None:
            return ""
        cached = self._lookup(assessment, visit, norm)
        return worst_band(cached) if cached is not None else PENDING_BAND

    def _schedule_badges(self) -> None:
        """Štítky, které chybí, spočítá časovač po kouskách; seznam je vidět hned."""
        self._badge_generation += 1
        if any(isinstance(c, PatientCard) and c.pending for c in self._cards):
            generation = self._badge_generation
            QTimer.singleShot(0, lambda: self._badge_tick(generation))

    def _badge_tick(self, generation: int) -> None:
        if generation != self._badge_generation:
            return  # seznam se mezitím postavil znovu
        done = 0
        for card in self._cards:
            if not isinstance(card, PatientCard) or not card.pending:
                continue
            card.set_band(self._visit_band(card.assessment, card.visit))
            done += 1
            if done >= BADGE_CHUNK:
                break
        if any(isinstance(c, PatientCard) and c.pending for c in self._cards):
            QTimer.singleShot(0, lambda: self._badge_tick(generation))

    def flush_badges(self) -> None:
        """Dopočítá všechny chybějící štítky hned (testy, export)."""
        self._badge_generation += 1
        for card in self._cards:
            if isinstance(card, PatientCard) and card.pending:
                card.set_band(self._visit_band(card.assessment, card.visit))

    def badges_pending(self) -> int:
        return sum(1 for c in self._cards if isinstance(c, PatientCard) and c.pending)

    def count(self) -> int:
        return self.visits.count()

    def current_plan(self) -> AssessmentPlan | None:
        row = self.visits.currentRow()
        return self._shown_plans[row] if 0 <= row < len(self._shown_plans) else None

    def plans(self) -> list[AssessmentPlan]:
        return list(self._plans)

    # --- přehled a detail (varianta B) -----------------------------------------------

    def in_detail(self) -> bool:
        return self.page_stack.currentIndex() == 1

    def show_overview(self) -> None:
        self.overview.select(self.visits.currentRow())
        self.page_stack.setCurrentIndex(0)

    def show_detail(self) -> None:
        self.page_stack.setCurrentIndex(1)
        self._update_nav()

    def open_overview_row(self, row: int) -> None:
        """Dvojklik v přehledu: pacient (nebo rozpracované hodnocení) přes celou šířku."""
        if not 0 <= row < self.visits.count():
            return
        if self.visits.currentRow() == row:
            self._item_changed(self.visits.currentItem(), None)
        else:
            self.visits.setCurrentRow(row)
        self.show_detail()

    def step(self, delta: int) -> None:
        """Šipky v detailu: další pacient v pořadí a filtru přehledu."""
        rows = self.overview.visible_rows()
        row = self.visits.currentRow()
        if row not in rows:
            return
        index = rows.index(row) + delta
        if 0 <= index < len(rows):
            self.open_overview_row(rows[index])

    def _update_nav(self) -> None:
        card = self.card(self.visits.currentRow())
        self.crumb.setText(card.title.text() if card is not None else "")
        rows = self.overview.visible_rows()
        row = self.visits.currentRow()
        position = rows.index(row) if row in rows else -1
        self.position.setText(
            tr("{i} z {n}").format(i=position + 1, n=len(rows)) if position >= 0 else ""
        )
        self.prev_btn.setEnabled(position > 0)
        self.next_btn.setEnabled(0 <= position < len(rows) - 1)

    def _overview_row(self, card: PatientCard | PendingCard) -> OverviewRow:
        """Řádek přehledu (podoba D): bez výsledků dimenzí, ty jsou v detailu."""
        from .norms import PLAN_STATUS  # stejné popisky stavů dávek jako u normy

        if isinstance(card, PendingCard):
            plan = card.plan
            chips = []
            for task in plan.tasks:
                label, role = PLAN_STATUS.get(task.status, (task.status, "neutral"))
                chips.append((f"{tasks.label(task.task)} · {tr(label)}", role))
            return OverviewRow(
                plan.patient.subject,
                _sex_text(plan.patient.sex),
                _age_text(plan.patient.age),
                card.pill.text(),
                card._role,
                "",
                format_visit_date(plan.patient.date) if plan.patient.date else tr("dnes"),
                "",
                kind="pending",
                sort_date=plan.patient.date or dt.date.today().isoformat(),
                chips=chips,
            )
        latest = card.visit
        dates = [e[0].date for e in card.entries if e[0].date]
        task_keys = sorted(
            {r.task for e in card.entries for r in e[1].recordings}, key=tasks.task_order
        )
        return OverviewRow(
            card.title.text(),
            _sex_text(latest.sex),
            _age_text(latest.age),
            card.pill.text(),
            BAND_ROLES.get(card.band, "neutral") if not card.pending else "neutral",
            str(len(card.entries)),
            format_visit_date(max(dates)) if dates else "",
            ", ".join(tasks.label(t) for t in task_keys),
            band=card.band,
            sort_date=max(dates) if dates else "",
        )

    def _fill_overview(self, current: int = -1) -> None:
        self.overview.set_empty_text(self.empty.text())
        self.overview.set_rows([self._overview_row(c) for c in self._cards], current)
        if self.in_detail():
            self._update_nav()

    def _card_band_changed(self, card: PatientCard) -> None:
        if card in self._cards:
            self.overview.update_row(self._cards.index(card), self._overview_row(card))

    def card(self, row: int) -> PatientCard | PendingCard | None:
        return self._cards[row] if 0 <= row < len(self._cards) else None

    def patient_entries(self) -> list[Entry]:
        """Všechna vyšetření vybraného pacienta, nejnovější první."""
        row = self.visits.currentRow() - len(self._shown_plans)
        return list(self._groups[row]) if 0 <= row < len(self._groups) else []

    def select_row(self, row: int) -> None:
        if 0 <= row < self.visits.count():
            self.visits.setCurrentRow(row)

    def select_assessment(self, assessment_id: str) -> bool:
        """Vybere pacienta a v jeho řadě dané vyšetření (a ukáže detail)."""
        self.show_detail()
        offset = len(self._shown_plans)
        for row, entries in enumerate(self._groups):
            entry = next((e for e in entries if e[0].id == assessment_id), None)
            if entry is None:
                continue
            self._shown[row] = entry
            if self.visits.currentRow() == offset + row:
                self._item_changed(self.visits.currentItem(), None)
            else:
                self.visits.setCurrentRow(offset + row)
            return True
        for row, plan in enumerate(self._shown_plans):
            if plan.id == assessment_id:
                self.visits.setCurrentRow(row)
                return True
        return False

    # --- založení, úprava, smazání ----------------------------------------------------

    def make_dialog(
        self,
        assessment: Assessment | None = None,
        initial: Recording | None = None,
        template: Assessment | None = None,
    ) -> AssessmentDialog:
        norm = self.current_norm()
        return AssessmentDialog(
            self.recordings_pool(),
            assessment=assessment,
            initial=initial,
            template=template,
            norm_protocols=norm.info.protocols if norm is not None else {},
            parent=self,
        )

    def new_visit(self) -> Assessment | None:
        """Další vyšetření téhož pacienta: z nahrávek (průvodce s předvyplněným pacientem,
        řeší hlavní okno), nebo z hotových běhů (dialog s předvyplněným pacientem)."""
        current = self.current_assessment()
        if current is None:
            return None
        choice = NewAssessmentChoiceDialog(self)
        if choice.exec() != QDialog.DialogCode.Accepted:
            return None
        if choice.choice() == "recordings":
            self.new_visit_from_recordings_requested.emit(current)
            return None
        dialog = self.make_dialog(template=current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.save_from(dialog)

    def save_from(self, dialog: AssessmentDialog) -> Assessment | None:
        """Uloží hodnocení z potvrzeného dialogu a vybere ho."""
        if self._store is None:
            return None
        assessment = dialog.assessment()
        self._store.save(assessment)
        self.search.clear()
        self.refresh(select=assessment.id)
        return assessment

    def new_from_runs(self) -> Assessment | None:
        """Nové hodnocení z hotových běhů rovnou, bez volby zdroje (odkaz z Domů)."""
        dialog = self.make_dialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.save_from(dialog)

    def new_assessment(self, initial: Recording | None = None) -> Assessment | None:
        """Nové hodnocení: s nahrávkou z Výsledků rovnou dialog, jinak volba zdroje."""
        if initial is None:
            choice = NewAssessmentChoiceDialog(self)
            if choice.exec() != QDialog.DialogCode.Accepted:
                return None
            if choice.choice() == "recordings":
                self.new_from_recordings_requested.emit()
                return None
        dialog = self.make_dialog(initial=initial)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.save_from(dialog)

    def recompute_plan(self, assessment_id: str) -> tuple[AssessmentPlan | None, list[str]]:
        """Plán přepočtu hodnocení (nebo důvody, proč nejde), pro hlavní okno."""
        for assessment, visit, missing in self._entries:
            if assessment.id == assessment_id and self._store is not None:
                return plan_from_assessment(assessment, visit, missing, self._store.folder)
        return None, [tr("hodnocení už neexistuje")]

    def _update_recompute(self) -> None:
        current = self.current_assessment()
        if current is None:
            self.recompute_btn.setEnabled(False)
            self.recompute_btn.setToolTip("")
            return
        plan, problems = self.recompute_plan(current.id)
        self.recompute_btn.setEnabled(plan is not None)
        self.recompute_btn.setToolTip(
            tr(
                "Nahrávky hodnocení znovu současnou knihovnou (po změně feature); "
                "hodnocení se po skončení nahradí, dosavadní běhy zůstanou ve Výsledcích."
            )
            if plan is not None
            else tr("Přepočítat nejde: {why}").format(why="; ".join(problems))
        )

    def _recompute_current(self) -> None:
        current = self.current_assessment()
        if current is not None:
            self.recompute_requested.emit(current.id)

    def edit_current(self) -> Assessment | None:
        current = self.current_assessment()
        if current is None:
            return None
        dialog = self.make_dialog(assessment=current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.save_from(dialog)

    def delete_current(self, *, confirm: bool = True) -> bool:
        current = self.current_assessment()
        if current is None or self._store is None:
            return False
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("Smazat hodnocení"),
                tr(
                    "Smazat hodnocení pacienta {subject} z {date}? Běhy a nahrávky zůstanou."
                ).format(subject=current.subject, date=format_visit_date(current.date)),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._store.delete(current)
        self.refresh()
        return True

    def relocate(self, old: Path, new: Path) -> None:
        """Přejmenovaný nebo přesunutý běh: odkazy hodnocení na něj se přepíšou (`RunInfo.key`)."""
        if self._store is None or self._index is None:
            return
        root = self._index.work_root
        if self._store.relocate(run_key(root, old), run_key(root, new)):
            self.refresh()

    def show_row(self, run_dir: Path, row: int) -> bool:
        """Z Výsledků: nové hodnocení s touto nahrávkou předvyplněnou."""
        rec = self.find_recording(run_dir, row)
        if rec is None:
            return False
        return self.new_assessment(initial=rec) is not None

    def find_recording(self, run_dir: Path, row: int) -> Recording | None:
        return next(
            (r for r in self.recordings_pool() if r.run.dir == run_dir and r.row == row), None
        )

    def _item_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        for row, card in enumerate(self._cards):
            card.set_current(self.visits.item(row) is current)
        plan = self.current_plan()
        if plan is not None:
            self._current = None
            self._missing = []
            self.right.setVisible(False)
            self.plan_detail.setVisible(True)
            self._show_plan(plan)
            return
        self.plan_detail.setVisible(False)
        self.right.setVisible(True)
        row = self.visits.currentRow() - len(self._shown_plans)
        entry = self._shown[row] if 0 <= row < len(self._shown) else None
        self._current = entry[1] if entry else None
        self._missing = entry[2] if entry else []
        self._fill_visit_bar(row)
        self._recompute()

    def _fill_visit_bar(self, row: int) -> None:
        """Tlačítka s daty vyšetření pacienta; vidět jen při víc vyšetřeních."""
        for btn in self.visit_buttons:
            self.visit_layout.removeWidget(btn)
            btn.hide()  # Qt ho maže až z fronty událostí; do té doby by se kreslil přes řadu
            btn.setParent(None)
            btn.deleteLater()
        self.visit_buttons = []
        entries = self._groups[row] if 0 <= row < len(self._groups) else []
        current = self._shown[row][0] if 0 <= row < len(self._shown) else None
        for index, (assessment, _visit, _missing) in enumerate(entries):
            text = format_visit_date(assessment.date) if assessment.date else tr("bez data")
            btn = QPushButton(text)
            btn.setToolTip(visit_subtitle(_visit))
            chosen = current is not None and assessment.id == current.id
            theme.set_role(btn, "primary" if chosen else "")
            btn.clicked.connect(lambda _checked=False, i=index: self._select_visit(i))
            self.visit_layout.addWidget(btn)
            self.visit_buttons.append(btn)
        self.visit_bar.setVisible(bool(entries))

    def _select_visit(self, index: int) -> None:
        row = self.visits.currentRow() - len(self._shown_plans)
        if not (0 <= row < len(self._groups)) or not (0 <= index < len(self._groups[row])):
            return
        self._shown[row] = self._groups[row][index]
        self._current = self._shown[row][1]
        self._missing = self._shown[row][2]
        self._fill_visit_bar(row)
        self._recompute()

    # --- vyhodnocení --------------------------------------------------------------------

    def _evaluate(self, visit: Visit, norm: Norm) -> Evaluation:
        window, same_sex = self._effective(visit.subject)
        return evaluate(
            self._dimensions,
            visit.rows_by_task(),
            norm,
            age=visit.age,
            sex=visit.sex,
            window=window,
            same_sex=same_sex,
            versions=visit.versions_by_task(),
        )

    # --- okno a pohlaví per pacient (31. 10. 2026) -------------------------------------------

    def set_eval_defaults(self, window: int, same_sex: bool) -> None:
        """Výchozí okno (0 = bez omezení) a pohlaví z nastavení, bez signálu zpět."""
        self._defaults = (int(window), bool(same_sex))
        if self._current is not None:
            self._recompute()

    def eval_defaults(self) -> tuple[int, bool]:
        return self._defaults

    def _effective(self, subject: str) -> tuple[int | None, bool]:
        """(okno pro `evaluate`, None = bez omezení; jen stejné pohlaví) pro pacienta."""
        prefs = self._prefs.get(patient_key(subject), PatientPrefs())
        window = self._defaults[0] if prefs.window is None else prefs.window
        same_sex = self._defaults[1] if prefs.same_sex is None else prefs.same_sex
        return (window or None), same_sex

    def has_override(self, subject: str) -> bool:
        return patient_key(subject) in self._prefs

    def _show_prefs(self, visit: Visit) -> None:
        """Ovládání okna a pohlaví ukazuje volbu vybraného pacienta (bez přepočtu)."""
        window, same_sex = self._effective(visit.subject)
        self._syncing_prefs = True
        for i in range(self.window_combo.count()):
            if self.window_combo.itemData(i) == window:
                self.window_combo.setCurrentIndex(i)
                break
        self.same_sex.setChecked(same_sex)
        self.norm_combo.blockSignals(True)
        self.norm_combo.setCurrentIndex(self._norm_index(self._norm_key_for(visit.subject)))
        self.norm_combo.blockSignals(False)
        self._syncing_prefs = False
        self.prefs_label.setVisible(self.has_override(visit.subject))

    def _prefs_changed(self, *_args: object) -> None:
        """Změna okna nebo pohlaví platí jen pro vybraného pacienta; shoda s výchozím
        odchylku zruší. Přepočítá se jen on, otisk ostatních se nezměnil."""
        if self._syncing_prefs or self._current is None:
            return
        key = patient_key(self._current.subject)
        window = int(self.window_combo.currentData() or 0)
        same_sex = self.same_sex.isChecked()
        prefs = PatientPrefs(
            None if window == self._defaults[0] else window,
            None if same_sex == self._defaults[1] else same_sex,
            self._prefs.get(key, PatientPrefs()).norm,
        )
        if prefs.empty:
            self._prefs.pop(key, None)
        else:
            self._prefs[key] = prefs
        if self._catalog is not None:
            self._catalog.set_patient_prefs(key, prefs)
        self._recompute()

    def _make_default(self) -> None:
        """Volba vybraného pacienta se stane výchozí pro všechny; odchylky se smažou."""
        if self._current is None:
            return
        window, same_sex = self._effective(self._current.subject)
        self._defaults = (window or 0, same_sex)
        norm_key = self._norm_key_for(self._current.subject)
        self._prefs.clear()
        if self._catalog is not None:
            self._catalog.clear_patient_prefs()
        self.eval_defaults_changed.emit(self._defaults[0], same_sex)
        if norm_key != self._norm_choice:
            self._norm_choice = norm_key
            self.norm_choice_changed.emit(norm_key)
        self._recompute()

    def _visit_signature(self, assessment: Assessment, visit: Visit) -> tuple:
        """Co může změnit vyhodnocení: údaje pacienta a odkazy s otiskem tabulek běhů."""
        index = self._index
        return (
            assessment.date,
            assessment.age,
            assessment.sex,
            self._effective(assessment.subject),  # okno a pohlaví pacienta
            self._norm_key_for(assessment.subject),  # a jeho norma
            tuple(
                (rec.run.key, rec.row, index.signature(rec.run) if index is not None else ())
                for rec in visit.recordings
            ),
        )

    def _lookup(self, assessment: Assessment, visit: Visit, norm: Norm) -> Evaluation | None:
        """Vyhodnocení z cache, když sedí otisk vyšetření (nese normu, okno a pohlaví
        pacienta, takže změna u jednoho pacienta ostatní nepřepočítá); po novém načtení
        norem se cache zahodí celá."""
        key = tuple(id(n) for n in self._norms)
        if key != self._eval_key:
            self._eval_cache.clear()
            self._eval_key = key
        cached = self._eval_cache.get(assessment.id)
        if cached is None or cached[0] != self._visit_signature(assessment, visit):
            return None
        return cached[1]

    def _cached_evaluation(self, assessment: Assessment, visit: Visit, norm: Norm) -> Evaluation:
        """Vyhodnocení hodnocení; platí, dokud se nezmění norma, okno, pohlaví ani běhy.

        Štítky karet v seznamu na vybraném pacientovi nezávisí, takže kliknutí
        na kartu nesmí přepočítávat všechny ostatní (60 ms na pacienta), a
        obnova seznamu přepočítá jen pacienty se změněnou tabulkou.
        """
        cached = self._lookup(assessment, visit, norm)
        if cached is None:
            cached = self._evaluate(visit, norm)
            self._eval_cache[assessment.id] = (self._visit_signature(assessment, visit), cached)
        return cached

    def _recompute(self) -> None:
        visit, norm = self._current, self.current_norm()
        if visit is None:
            self._show_empty_state()
            return
        self.header.title.setText(visit.subject)
        self.header.subtitle.setText(visit_subtitle(visit))
        self._fill_recordings(visit)
        self._show_prefs(visit)
        self.edit_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
        self._update_recompute()
        if norm is None:
            # bez normy: žádný pavouk ani pásma, ale hodnoty položek klinik vidět má
            self._evaluation = None
            self.header.set_role("neutral")
            self.header.set_pills([])
            self.middle.setVisible(self._view != "trend")
            self.chart_card.hide()
            self._fill_values_tree(visit)
            self.image_btn.setEnabled(False)
            self.about.setText("")
            self.window_combo.setEnabled(False)
            self.same_sex.setEnabled(False)
            self.selection_label.setText("")
            current_row = self.visits.currentRow()
            for row, card in enumerate(self._cards):
                if isinstance(card, PatientCard):
                    card.set_band(self._known_band(card.assessment, card.visit))
                    card.set_current(row == current_row)
            self._schedule_badges()
            self._update_view_bar()
            return
        self.middle.setVisible(self._view != "trend")
        self.chart_card.show()
        self.window_combo.setEnabled(True)
        current = self.current_assessment()
        self._evaluation = (
            self._cached_evaluation(current, visit, norm)
            if current is not None
            else self._evaluate(visit, norm)
        )
        band = worst_band(self._evaluation)
        self.header.set_role(BAND_ROLES.get(band, "neutral"))
        # štítky dimenzí v hlavičce odstraněny (31. 10. 2026): totéž říká pavouk a strom
        self.header.set_pills([])
        self._fill_chart(self._evaluation)
        self._fill_tree(self._evaluation)
        self.selection_label.setText(self._selection_text(visit, self._evaluation))
        self.same_sex.setEnabled(visit.sex in ("f", "m"))
        self.about.setText(self._about_text(norm))
        self.image_btn.setEnabled(bool(self._evaluation.shown))
        self._update_view_bar()
        # štítky v seznamu podle téže normy a téhož okna
        current_row = self.visits.currentRow()
        for row, card in enumerate(self._cards):
            if not isinstance(card, PatientCard):
                continue  # rozpracované hodnocení má štítek z plánu, na normě nezávisí
            # štítek karty = poslední vyšetření pacienta, i když je vybrané starší;
            # co není v cache, dopočítá časovač, ne tohle kliknutí
            if card.visit is visit:
                band = worst_band(self._evaluation)
            else:
                band = self._known_band(card.assessment, card.visit)  # jeho norma a okno
            card.set_band(band)
            card.set_current(row == current_row)
        self._schedule_badges()

    # --- vývoj v čase ---------------------------------------------------------------------

    def view(self) -> str:
        return self._view

    def set_view(self, view: str) -> None:
        """„visit“ = pavouk a strom vybraného vyšetření, „trend“ = vývoj v čase pacienta."""
        if view == "trend" and not self.view_trend_btn.isEnabled():
            view = "visit"
        self._view = view
        (self.view_trend_btn if view == "trend" else self.view_visit_btn).setChecked(True)
        trend = view == "trend"
        self._update_trend_controls()
        self.trend_panel.setVisible(trend)
        self.middle.setVisible(not trend and self._current is not None)
        self.bottom.setVisible(not trend)
        self.visit_bar.setVisible(not trend and len(self.patient_entries()) > 0)
        self.image_btn.setToolTip(
            tr("Grafy vývoje a tabulka jako PNG, třeba do zprávy.")
            if trend
            else tr("Pavouk jako PNG, třeba do zprávy.")
        )
        if trend:
            self._fill_trend()

    def _dated_entries(self) -> list[Entry]:
        return [e for e in self.patient_entries() if e[0].date]

    def _update_view_bar(self) -> None:
        """Přepínač je vidět u pacienta; Vývoj jde od dvou vyšetření s datem (bez normy
        jen v měřítku Hodnoty)."""
        entries = self.patient_entries()
        self.view_bar.setVisible(bool(entries))
        can = len(self._dated_entries()) >= 2
        self.view_trend_btn.setEnabled(can)
        if self._view == "trend" and not can:
            self.set_view("visit")
        elif self._view == "trend":
            self._update_trend_controls()
            self._fill_trend()

    # --- měřítko: SD proti normě · Hodnoty ------------------------------------------------

    def trend_scale(self) -> str:
        """Zvolené měřítko Vývoje (uložené v nastavení), i když se zrovna nedá použít."""
        return self._scale

    def effective_scale(self) -> str:
        """Měřítko, které se opravdu kreslí: bez normy vždy Hodnoty."""
        return "values" if self.current_norm() is None else self._scale

    def set_scale(self, scale: str, *, emit: bool = True) -> None:
        if scale not in ("sd", "values"):
            return
        changed = scale != self._scale
        self._scale = scale
        if self._view == "trend":
            self._update_trend_controls()
            self._fill_trend()
        if changed and emit:
            self.scale_changed.emit(scale)

    # --- podoba: Pavouk · Mapa · Grafy --------------------------------------------------------

    def trend_form(self) -> str:
        """Zvolená podoba Vývoje (uložená v nastavení), i když se zrovna nedá použít."""
        return self._form

    def can_overlay(self) -> bool:
        """Překryv v pavouku: jen v SD proti normě a do tří vyšetření s datem."""
        return self.effective_scale() == "sd" and 2 <= len(self._dated_entries()) <= 3

    def effective_form(self) -> str:
        return "charts" if self._form == "spider" and not self.can_overlay() else self._form

    def set_form(self, form: str, *, emit: bool = True) -> None:
        if form not in ("spider", "charts"):
            return
        changed = form != self._form
        self._form = form
        if self._view == "trend":
            self._update_trend_controls()
            self._fill_trend()
        if changed and emit:
            self.form_changed.emit(form)

    def set_trend_selection(self, dims: list[str] | None, items: list[str] | None) -> None:
        """Zapamatované řádky s grafem z nastavení (None = výchozí), bez signálu zpět."""
        self._dims_selected = set(dims) if dims is not None else None
        self._items_selected = set(items) if items is not None else None
        if self._view == "trend":
            self._fill_trend()

    def trend_selection(self) -> list[str]:
        """Klíče řádků přehledu, které mají teď graf."""
        return self.trend_map.selected_keys()

    def _map_selection_changed(self, keys: list) -> None:
        """Zaškrtnutí v přehledu: grafy jen pro vybrané řádky, výběr se pamatuje zvlášť
        pro dimenze (SD) a položky (Hodnoty)."""
        chosen = {str(k) for k in keys}
        kind = "dims" if self.effective_scale() == "sd" else "items"
        if kind == "dims":
            self._dims_selected = chosen
        else:
            self._items_selected = chosen
        self.trend_selection_changed.emit(kind, sorted(chosen))
        self._fill_trend()

    def _trend_visit_clicked(self, index: int) -> None:
        """Klik na sloupec mapy přepne na to vyšetření na záložce Vyšetření."""
        if 0 <= index < len(self._trend_visit_ids):
            self.select_assessment(self._trend_visit_ids[index])
            self.set_view("visit")

    def _update_trend_controls(self) -> None:
        """Přepínače podoby a měřítka jen ve Vývoji; SD a Pavouk bez normy neaktivní."""
        trend = self._view == "trend"
        has_norm = self.current_norm() is not None
        scale = self.effective_scale()
        self.trend_bar.setVisible(trend)
        for widget in (self.form_label, self.form_spider_btn, self.form_charts_btn):
            widget.setVisible(trend)
        overlay = self.can_overlay()
        self.form_spider_btn.setEnabled(overlay)
        self.form_spider_btn.setToolTip(
            tr("Překryv vyšetření v pavouku.")
            if overlay
            else tr("Překryv v pavouku potřebuje normu a nejvýš tři vyšetření.")
        )
        form = self.effective_form()
        (self.form_spider_btn if form == "spider" else self.form_charts_btn).setChecked(True)
        for widget in (self.scale_label, self.scale_sd_btn, self.scale_values_btn):
            widget.setVisible(trend)
        self.scale_note.setVisible(trend and not has_norm)
        self.scale_note.setText(tr("vyberte normu") if self._norms else tr("potřebuje normu"))
        self.scale_sd_btn.setEnabled(has_norm)
        (self.scale_values_btn if scale == "values" else self.scale_sd_btn).setChecked(True)
        if trend:
            self.image_btn.setEnabled(True)  # mřížka s tabulkou jde uložit i bez normy
        else:
            self.image_btn.setEnabled(self._evaluation is not None and bool(self._evaluation.shown))

    def timeline(self) -> Timeline | None:
        return self._timeline

    def value_timeline(self) -> ValueTimeline | None:
        return self._value_timeline

    def _fill_trend(self) -> None:
        norm = self.current_norm()
        entries = self._dated_entries()
        if len(entries) < 2:
            self._timeline = None
            self._value_timeline = None
            return
        form = self.effective_form()
        # Pavouk: překryv a tabulka změn; Grafy: přehled s výběrem a grafy vybraných řádků
        self.spider_card.setVisible(form == "spider")
        self.table_card.setVisible(form == "spider")
        self.map_card.setVisible(form == "charts")
        self.grid_card.setVisible(form == "charts")
        if norm is None or self._scale == "values":
            self._timeline = None
            self._fill_values(entries)
            return
        self._value_timeline = None
        self.grid_title.setText(tr("Vývoj po dimenzích"))
        self.grid_hint.setText(
            tr(
                "Osa y v SD proti normě, patologie nahoru; pásma v pozadí: zelené v normě, "
                "oranžové hraniční, červené mimo normu, čárkovaně norma. Hodnota u každého "
                "bodu; dutý bod = vyšetření počítané jinou verzí feature než norma."
            )
        )
        self.table_title.setText(tr("Dimenze po vyšetřeních"))
        self.map_title.setText(tr("Dimenze × vyšetření"))
        self.map_hint.setText(
            tr(
                "Barva = pásmo, číslo = SD proti normě, poslední vyšetření tučně; průběh "
                "vpředu, změna za období vzadu. Klik na sloupec otevře to vyšetření."
            )
        )
        evaluations = [(a, self._cached_evaluation(a, v, norm)) for a, v, _m in entries]
        timeline = build_timeline(evaluations)
        self._timeline = timeline
        self._trend_visit_ids = [v.assessment_id for v in timeline.visits]
        labels = _visit_labels([v.date for v in timeline.visits])
        series: list[TrendSeries] = []
        rows = timeline.shown_rows()
        # graf mají zaškrtnuté dimenze; bez volby ty hraniční nebo mimo normu naposledy
        selected = (
            self._dims_selected
            if self._dims_selected is not None
            else {r.dimension.key for r in rows if r.last_band in ("warn", "bad")}
        )
        for row in rows:
            if row.dimension.key not in selected:
                continue
            last = row.last
            headline = ""
            if last is not None:
                headline = format_sd(last).removesuffix(" SD")  # jednotka je v nápovědě
                if row.change_last is not None:
                    headline += " · " + format_change(row.change_last).removesuffix(" SD")
            series.append(
                TrendSeries(
                    row.dimension.display_name,
                    labels,
                    list(row.scores),
                    list(row.bands),
                    [v.outdated for v in timeline.visits],
                    headline,
                    BAND_COLORS.get(row.last_band, theme.TEXT),
                )
            )
        self.trend_grid.empty.setText(
            tr("Zaškrtněte v přehledu dimenze, které chcete vidět v grafech.")
            if rows
            else tr("Žádná dimenze se nedá spočítat.")
        )
        self.trend_grid.set_series(series)
        # mapa: řádek dimenze, buňka vyšetření barvou pásma, průběh a změna
        dates = [format_visit_date(v.date) + (" ⚠" if v.outdated else "") for v in timeline.visits]
        self.trend_map.set_rows(
            dates,
            [
                MapRow(
                    row.dimension.display_name,
                    list(row.scores),
                    [
                        format_sd(s).removesuffix(" SD") if s is not None else "–"
                        for s in row.scores
                    ],
                    list(row.bands),
                    [v.outdated for v in timeline.visits],
                    format_change(row.change_total),
                    _change_side(row.change_total),
                    key=row.dimension.key,
                )
                for row in rows
            ],
            scale="sd",
            date_tips=[_visit_tip(v, norm=True) for v in timeline.visits],
            selected=selected,
        )
        self._fill_spider(timeline, rows)
        # tabulka dimenze × datum se změnou za období
        table = self.trend_table
        table.clear()
        table.clearSpans()
        table.setColumnCount(len(timeline.visits) + 2)
        headers = [tr("dimenze")]
        for visit in timeline.visits:
            text = format_visit_date(visit.date)
            if visit.outdated:
                text += " \u26a0"
            headers.append(text)
        headers.append(tr("změna za období"))
        table.setHorizontalHeaderLabels(headers)
        for c, visit in enumerate(timeline.visits, start=1):
            item = table.horizontalHeaderItem(c)
            if item is not None:
                tip = []
                if visit.age is not None:
                    tip.append(tr("{age} let").format(age=int(round(visit.age))))
                if visit.outdated:
                    tip.append(tr("jiná verze feature než norma, přepočítejte vyšetření"))
                item.setToolTip(" · ".join(tip))
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            name = QTableWidgetItem(row.dimension.display_name)
            font = name.font()
            font.setBold(True)
            name.setFont(font)
            table.setItem(r, 0, name)
            for c, (score, band) in enumerate(zip(row.scores, row.bands, strict=True), start=1):
                cell = QTableWidgetItem(
                    format_sd(score).removesuffix(" SD") if score is not None else "–"
                )
                if score is not None:
                    cell.setForeground(QColor(BAND_COLORS.get(band, theme.TEXT)))
                table.setItem(r, c, cell)
            change = QTableWidgetItem(format_change(row.change_total))
            if row.change_total is not None and abs(row.change_total) >= 1.0:
                change.setForeground(QColor(theme.MISSING if row.change_total > 0 else theme.OK))
            table.setItem(r, len(timeline.visits) + 1, change)
        _fit_name_column(table)
        header = table.horizontalHeader()
        height = header.height() + 4 + sum(table.rowHeight(i) for i in range(len(rows)))
        table.setFixedHeight(max(height, 60))
        notes = [
            tr(
                "Kontroly se vybírají podle věku při každém vyšetření (okno z lišty nahoře), "
                "posun okna sám o sobě skóre mírně hýbe. Změna je rozdíl v SD, ne tvrzení "
                "o statistické významnosti."
            )
        ]
        if timeline.outdated:
            notes.append(
                tr(
                    "⚠ {dates}: vyšetření počítané jinou verzí feature než norma, hodnoty jsou "
                    "jen orientační; přepočítejte ho na záložce Vyšetření (Přepočítat…)."
                ).format(dates=", ".join(format_visit_date(v.date) for v in timeline.outdated))
            )
        if timeline.skipped:
            notes.append(
                tr("{n} vyšetření bez data se do vývoje nebere.").format(n=timeline.skipped)
            )
        self.trend_note.setText("\n".join(notes))

    def _fill_spider(self, timeline: Timeline, rows: list) -> None:
        """Překryv: osy = dimenze s hodnotou v posledním vyšetření, starší vyšetření modře."""
        visits = timeline.visits
        axes: list[Axis] = []
        kept = []
        for row in rows:
            last = row.scores[-1]
            if last is None:
                continue
            axes.append(Axis(row.dimension.display_name, last, row.bands[-1]))
            kept.append(row)
        overlays: list[Overlay] = []
        legend = [
            tr('<span style="color:{color}">━━</span> {date} (poslední, body barvou pásma)').format(
                color=theme.TEXT, date=format_visit_date(visits[-1].date)
            )
        ]
        older = list(range(len(visits) - 1))
        for index in reversed(older):
            oldest = index == 0 and len(visits) >= 3
            color = "#0284c7" if oldest else "#1d4ed8"
            overlays.append(
                Overlay(
                    format_visit_date(visits[index].date),
                    [r.scores[index] for r in kept],
                    color,
                    (3.0, 3.0) if oldest else (8.0, 4.0),
                    "circle" if oldest else "square",
                    0.12 if oldest else 0.0,
                )
            )
            legend.append(
                '<span style="color:{color}">{dash}</span> {date}{warn}'.format(
                    color=color,
                    dash="┈┈" if oldest else "╌╌",
                    date=format_visit_date(visits[index].date),
                    warn=" ⚠" if visits[index].outdated else "",
                )
            )
        self.trend_spider.set_axes(axes, overlays)
        self.spider_legend.setText("<br>".join(legend))

    def _fill_values(self, entries: list[Entry]) -> None:
        """Měřítko Hodnoty: položky dimenzí v jednotkách feature, po skupinách dimenzí."""
        timeline = build_value_timeline(
            self._dimensions,
            [(a, v.rows_by_task(), v.versions_by_task()) for a, v, _m in entries],
        )
        self._value_timeline = timeline
        self.grid_title.setText(tr("Vývoj po položkách"))
        self.grid_hint.setText(
            tr(
                "Osa y v jednotkách feature podle hodnot pacienta, bez pásem normy. "
                "▲ = posun k patologii podle směru položky, ▼ = od ní; položky s patologií "
                "oběma směry mají jen číslo. Dutý bod = jiná verze feature než u posledního "
                "vyšetření."
            )
        )
        self.table_title.setText(tr("Položky po vyšetřeních"))
        self.map_title.setText(tr("Položky × vyšetření"))
        self.map_hint.setText(
            tr(
                "Hodnoty v jednotkách feature bez pásem, poslední vyšetření tučně; šipka "
                "u změny = směr k patologii podle položky. Klik na sloupec otevře to vyšetření."
            )
        )
        self._trend_visit_ids = [v.assessment_id for v in timeline.visits]
        labels = _visit_labels([v.date for v in timeline.visits])
        groups: list[tuple[str, str, list[TrendSeries]]] = []
        map_rows: list[MapRow] = []
        # graf mají zaškrtnuté položky; bez volby dvě s největší změnou v procentech
        all_rows = timeline.shown_rows()
        if self._items_selected is not None:
            selected = self._items_selected
        else:
            ranked = sorted(all_rows, key=lambda r: abs(r.change_percent or 0.0), reverse=True)
            selected = {_item_key(r) for r in ranked[:2]}
        for dim, rows in timeline.groups():
            series: list[TrendSeries] = []
            for row in rows:
                label, unit = self._item_label(row.item.column)
                known = [v for v in row.values if v is not None]
                ymin, ymax, ticks = nice_axis(known)
                last, delta = row.last, row.change_last
                side = row.side(delta)
                series.append(
                    TrendSeries(
                        f"{label} · {tasks.label(row.task)}",
                        labels,
                        list(row.values),
                        [],
                        list(row.outdated),
                        _with_unit(format_number(last), unit) if last is not None else "",
                        theme.TEXT,
                        scale="values",
                        ymin=ymin,
                        ymax=ymax,
                        ticks=[(t, format_tick(t, ticks)) for t in ticks],
                        texts=[
                            _with_unit(format_number(v), unit) if v is not None else "–"
                            for v in row.values
                        ],
                        change=_format_delta(delta, last) if delta is not None else "",
                        arrow="▲" if side > 0 else "▼" if side < 0 else "",
                        arrow_color=theme.MISSING
                        if side > 0
                        else theme.OK
                        if side < 0
                        else theme.MUTED,
                    )
                )
                total = row.change_total
                total_side = row.side(total)
                pct = row.change_percent
                change_text = ""
                if total is not None:
                    change_text = "▲ " if total_side > 0 else "▼ " if total_side < 0 else ""
                    change_text += _with_unit(_format_delta(total, row.last), unit)
                    if pct:
                        change_text += f" · {pct:+.0f} %".replace("+0 %", "0 %")
                map_rows.append(
                    MapRow(
                        f"{label} · {tasks.label(row.task)}",
                        list(row.values),
                        [format_number(v) if v is not None else "–" for v in row.values],
                        [],
                        list(row.outdated),
                        change_text,
                        total_side,
                        unit=unit,
                        group=dim.display_name,
                        group_note=_group_subtitle(rows),
                        key=_item_key(row),
                        tooltip=row.item.column,
                    )
                )
                if _item_key(row) not in selected:
                    series.pop()
            if series:
                groups.append((dim.display_name, _group_subtitle(rows), series))
        self.trend_grid.empty.setText(
            tr("Zaškrtněte v přehledu položky, které chcete vidět v grafech.")
            if all_rows
            else tr("Žádná položka dimenzí nemá hodnotu.")
        )
        self.trend_grid.set_groups(groups)
        self.trend_map.set_rows(
            [format_visit_date(v.date) + (" ⚠" if v.outdated else "") for v in timeline.visits],
            map_rows,
            scale="values",
            date_tips=[_visit_tip(v, norm=False) for v in timeline.visits],
            selected=selected,
        )
        # tabulka položka × datum s jednotkou a změnou za období
        table = self.trend_table
        table.clear()
        table.clearSpans()
        n = len(timeline.visits)
        table.setColumnCount(n + 4)
        headers = [tr("položka"), tr("jednotka")]
        for visit in timeline.visits:
            text = format_visit_date(visit.date)
            if visit.outdated:
                text += " ⚠"
            headers.append(text)
        headers += [tr("změna za období"), tr("změna v %")]
        table.setHorizontalHeaderLabels(headers)
        for c, visit in enumerate(timeline.visits, start=2):
            item = table.horizontalHeaderItem(c)
            if item is not None:
                tip = []
                if visit.age is not None:
                    tip.append(tr("{age} let").format(age=int(round(visit.age))))
                if visit.outdated:
                    tip.append(tr("některá feature jinou verzí než ostatní vyšetření"))
                item.setToolTip(" · ".join(tip))
        grouped = timeline.groups()
        table.setRowCount(sum(1 + len(rows) for _d, rows in grouped))
        r = 0
        for dim, rows in grouped:
            head = QTableWidgetItem(dim.display_name)
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            head.setBackground(QColor(theme.NEUTRAL_SOFT))
            table.setItem(r, 0, head)
            for c in range(1, n + 4):
                filler = QTableWidgetItem("")
                filler.setBackground(QColor(theme.NEUTRAL_SOFT))
                table.setItem(r, c, filler)
            table.setSpan(r, 0, 1, n + 4)
            r += 1
            for row in rows:
                label, unit = self._item_label(row.item.column)
                table.setItem(r, 0, QTableWidgetItem(f"{label} · {tasks.label(row.task)}"))
                table.setItem(r, 1, QTableWidgetItem(unit or "–"))
                for c, (value, outdated) in enumerate(
                    zip(row.values, row.outdated, strict=True), start=2
                ):
                    text = format_number(value) if value is not None else "–"
                    if outdated and value is not None:
                        text += " ⚠"
                    table.setItem(r, c, QTableWidgetItem(text))
                total = row.change_total
                side = row.side(total)
                arrow = "▲ " if side > 0 else "▼ " if side < 0 else ""
                change = QTableWidgetItem(
                    arrow + _format_delta(total, row.last) if total is not None else "–"
                )
                if side:
                    change.setForeground(QColor(theme.MISSING if side > 0 else theme.OK))
                table.setItem(r, n + 2, change)
                pct = row.change_percent
                table.setItem(
                    r,
                    n + 3,
                    QTableWidgetItem(f"{pct:+.0f} %".replace("+0 %", "0 %") if pct else "–"),
                )
                r += 1
        _fit_name_column(table)
        header = table.horizontalHeader()
        height = header.height() + 4 + sum(table.rowHeight(i) for i in range(table.rowCount()))
        table.setFixedHeight(max(height, 60))
        notes = [
            tr("Hodnoty v jednotkách feature, u úlohy s víc nahrávkami průměr.")
            + " "
            + (
                tr(
                    "Změna není srovnaná s rozptylem kontrol: co je ještě běžné kolísání, "
                    "řekne jen měřítko SD proti normě."
                )
                if self.current_norm() is not None
                else tr(
                    "Bez normy není změna srovnaná s rozptylem kontrol, co je ještě běžné "
                    "kolísání, z hodnot samotných říct nejde. Pásma, pavouk a skóre dimenzí "
                    "budou po založení normy na stránce Normy."
                )
            )
        ]
        if timeline.outdated:
            notes.append(
                tr(
                    "⚠ {dates}: některá feature se počítala jinou verzí než u ostatních "
                    "vyšetření, její hodnoty nejsou srovnatelné; přepočítejte vyšetření na "
                    "záložce Vyšetření (Přepočítat…)."
                ).format(dates=", ".join(format_visit_date(v.date) for v in timeline.outdated))
            )
        if timeline.skipped:
            notes.append(
                tr("{n} vyšetření bez data se do vývoje nebere.").format(n=timeline.skipped)
            )
        self.trend_note.setText("\n".join(notes))

    def _show_empty_state(self) -> None:
        self._evaluation = None
        self._timeline = None
        self._value_timeline = None
        self.view_bar.hide()
        self.trend_bar.hide()
        self.trend_panel.hide()
        self.bottom.show()
        self.plan_detail.setVisible(False)
        self.right.setVisible(True)
        self.header.title.setText(tr("Pacient"))
        self.header.subtitle.setText(tr("Vyberte pacienta vlevo, nebo založte nové hodnocení."))
        self.header.set_role("neutral")
        self.header.set_pills([])
        self.middle.hide()
        self.recordings.setRowCount(0)
        self.recordings_note.setText("")
        self.visit_bar.hide()
        self.edit_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.recompute_btn.setEnabled(False)
        self.recompute_btn.setToolTip("")
        self.image_btn.setEnabled(False)
        self.selection_label.setText("")
        self.about.setText(self._about_text(self.current_norm()) if self.current_norm() else "")

    def _selection_text(self, visit: Visit, evaluation: Evaluation) -> str:
        if not evaluation.selections:
            return tr("Norma nemá žádnou z úloh tohoto vyšetření.")
        if visit.age is None:
            prefix = tr("Pacient nemá věk, bere se celá norma. ")
        else:
            prefix = ""
        parts = []
        for task, sel in evaluation.selections.items():
            parts.append(_selection_part(task, sel))
        return prefix + " · ".join(parts)

    def _about_text(self, norm: Norm | None) -> str:
        if norm is None:
            return ""
        lines = [norm.info.display_description or norm.info.display_name]
        facts = []
        if norm.subjects:
            facts.append(tr("{n} osob").format(n=norm.subjects))
        ages = norm.controls["age"].dropna() if "age" in norm.controls else None
        if ages is not None and len(ages):
            facts.append(tr("{lo} až {hi} let").format(lo=int(ages.min()), hi=int(ages.max())))
        if norm.info.language:
            facts.append(contract.language_label(norm.info.language))
        if norm.info.source:
            facts.append(norm.info.source)
        if facts:
            lines.append(", ".join(facts) + ".")
        if norm.info.conditions:
            lines.append(tr("Podmínky: {text}").format(text=norm.info.conditions))
        if (
            norm.info.library_version
            and norm.info.library_version != contract.KNOWN_LIBRARY_VERSION
        ):
            lines.append(
                tr("Norma spočítaná knihovnou {version}, aplikace čeká {known}.").format(
                    version=norm.info.library_version, known=contract.KNOWN_LIBRARY_VERSION
                )
            )
        return "\n".join(lines)

    def _fill_chart(self, evaluation: Evaluation) -> None:
        axes = [Axis(_dimension_label(r), r.score or 0.0, r.band) for r in evaluation.shown]
        self.chart.set_axes(axes)
        missing = [
            f"{r.dimension.display_name}: {r.reason}" for r in evaluation.missing if r.reason
        ]
        self.missing_label.setText(
            tr("V grafu není: {items}").format(items="; ".join(missing)) if missing else ""
        )
        self.missing_label.setVisible(bool(missing))

    def _item_label(self, column: str) -> tuple[str, str]:
        """Popisek položky z katalogu a jednotka; bez katalogu název sloupce."""
        info = self.columns().get(column)
        if info is None:
            return column, ""
        text, unit = split_unit(info.description)
        feature_name = info.feature.name.split(".")[-1]
        if text:
            return text, unit
        return (
            feature_name if info.short == feature_name else f"{feature_name} {info.short}"
        ), unit

    def _set_norm_columns(self, visible: bool) -> None:
        """Sloupce norma, z, percentil a pruh mají smysl jen s normou."""
        for col in (3, 4, 5, TREE_AXIS):
            self.tree.setColumnHidden(col, not visible)
        header = self.tree.header()
        if visible:
            # název a osu dopočítá strom (`fit_columns`), ostatní podle obsahu
            for col in range(1, 6):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        else:
            # dlouhé „bez dat: potřebuje úlohu…“ by jinak sebralo šířku názvu dimenze
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(2, 260)

    def _fill_values_tree(self, visit: Visit) -> None:
        """Bez normy: strom dimenze → položka jen s hodnotou v jednotkách feature."""
        self.tree_hint.setText(
            tr(
                "Bez normy jen hodnoty v jednotkách feature, bez z-skóre a pásem; "
                "normu založíte na stránce Normy."
            )
        )
        self._set_norm_columns(False)
        self.tree.clear()
        # jediné vyšetření: datum je jen kvůli řazení, bez něj by se vynechalo
        probe = Assessment("", visit.subject, date="2000-01-01")
        timeline = build_value_timeline(
            self._dimensions, [(probe, visit.rows_by_task(), visit.versions_by_task())]
        )
        grouped = dict((d.key, rows) for d, rows in timeline.groups())
        present = set(visit.rows_by_task())
        for dim in self._dimensions:
            rows = grouped.get(dim.key, [])
            task_text = ", ".join(
                tasks.label(t) for t in sorted({r.task for r in rows}, key=tasks.task_order)
            ) or ", ".join(tasks.label(t) for t in dim.tasks)
            top = QTreeWidgetItem([dim.display_name, task_text, "", "", "", "", ""])
            _style_dimension_row(top)
            self.tree.addTopLevelItem(top)
            if not rows:
                needed = [t for t in dim.tasks if t not in present]
                reason = (
                    tr("potřebuje úlohu {tasks}").format(
                        tasks=tr(" nebo ").join(tasks.label(t) for t in needed)
                    )
                    if needed
                    else tr("v tabulce chybí hodnoty")
                )
                top.setText(2, tr("bez dat: {reason}").format(reason=reason))
                top.setToolTip(2, top.text(2))
                for col in range(7):
                    top.setForeground(col, QColor(theme.MUTED))
                continue
            for row in rows:
                label, unit = self._item_label(row.item.column)
                value = row.last
                child = QTreeWidgetItem(
                    [
                        label,
                        tasks.label(row.task),
                        _with_unit(format_number(value), unit) if value is not None else "–",
                        "",
                        "",
                        "",
                        "",
                    ]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, row)
                child.setData(1, TASK_PILL_ROLE, child.text(1))
                child.setTextAlignment(
                    2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                child.setToolTip(0, row.item.column)
                top.addChild(child)
        self.tree.refit()

    def _fill_tree(self, evaluation: Evaluation) -> None:
        self.tree_hint.setText(
            tr(
                "Odchylka v SD od průměru kontrol, vpravo horší; zelený pás = v normě (±1 SD), "
                "◆ dimenze, ● položka. Percentil = pořadí mezi kontrolami, ▲▼ = strana "
                "odchylky u položek, kde vadí obě."
            )
        )
        self._set_norm_columns(True)
        self.tree.clear()
        for result in evaluation.results:
            top = QTreeWidgetItem(
                [
                    result.dimension.display_name,
                    ", ".join(
                        tasks.label(t)
                        for t in sorted({i.task for i in result.items}, key=tasks.task_order)
                    )
                    or ", ".join(tasks.label(t) for t in result.dimension.tasks),
                    "",
                    "",
                    format_sd(result.score).removesuffix(" SD") if result.score is not None else "",
                    _percentile_text(result),
                    "",
                ]
            )
            top.setData(0, Qt.ItemDataRole.UserRole, result)
            top.setToolTip(4, _score_tooltip(result))
            _style_dimension_row(top)
            top.setForeground(1, QColor(theme.MUTED))
            if result.shown:
                _style_score(top, result.band, large=True)
                top.setData(0, BAND_ROLE, result.band)
                top.setData(0, PILL_ROLE, BAND_LABELS.get(result.band, ""))
                self.tree.addTopLevelItem(top)
                self.tree.setItemWidget(
                    top, TREE_AXIS, DeviationAxis(result.score, result.band, dimension=True)
                )
                for item in result.items:
                    label, unit = self._item_label(item.item.column)
                    value = format_number(item.value) + (
                        f" {unit}" if unit and item.value is not None else ""
                    )
                    if item.score is not None:
                        norm_text = (
                            f"{format_number(item.score.mean)} ± {format_number(item.score.sd)}"
                        )
                        z_text = format_sd(item.score.z).removesuffix(" SD")
                        pct = f"{item.score.percentile:.0f}"
                        if item.item.direction == BOTH:
                            z_text += " \u25b2" if item.score.signed_z >= 0 else " \u25bc"
                    else:
                        norm_text, z_text, pct = "", "", ""
                    quality_tip = self._task_quality.get(item.task, "")
                    if item.versions is not None or quality_tip:
                        label += " \u26a0"  # změněná feature, nebo podezřelá nahrávka
                    child = QTreeWidgetItem(
                        [
                            label,
                            tasks.label(item.task),
                            value,
                            norm_text,
                            z_text,
                            pct,
                            "" if item.score is not None else item.reason,
                        ]
                    )
                    child.setData(0, Qt.ItemDataRole.UserRole, item)
                    child.setData(1, TASK_PILL_ROLE, child.text(1))
                    child.setToolTip(
                        0,
                        item.item.column
                        + _versions_tooltip(item)
                        + (f"\n{quality_tip}" if quality_tip else ""),
                    )
                    child.setToolTip(4, _item_z_tooltip(item))
                    for col in (2, 3, 4, 5):
                        child.setTextAlignment(
                            col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                    child.setForeground(3, QColor(theme.MUTED))
                    child.setForeground(5, QColor(theme.MUTED))
                    # tečka a název barvou pásma (zelená v normě), bez hodnoty šedě bez tečky
                    child.setIcon(0, band_dot(item.band if item.score is not None else ""))
                    if item.score is not None:
                        _style_score(child, item.band, large=False)
                        child.setForeground(0, QColor(BAND_STRONG.get(item.band, theme.TEXT)))
                    else:
                        child.setForeground(0, QColor(theme.MUTED))
                        child.setForeground(TREE_AXIS, QColor(theme.MUTED))
                        font = child.font(TREE_AXIS)
                        font.setItalic(True)
                        child.setFont(TREE_AXIS, font)
                    top.addChild(child)
                    if item.score is not None:
                        self.tree.setItemWidget(
                            child, TREE_AXIS, DeviationAxis(item.score.z, item.band)
                        )
            else:
                top.setText(TREE_AXIS, tr("bez dat: {reason}").format(reason=result.reason))
                top.setToolTip(TREE_AXIS, top.text(TREE_AXIS))
                for col in range(7):
                    top.setForeground(col, QColor(theme.MUTED))
                self.tree.addTopLevelItem(top)
        self.tree.refit()  # vše sbalené (31. 10. 2026), položky až po rozbalení

    def _tree_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        task = getattr(data, "task", None)
        if task is None or self._current is None:
            return
        rec = next((r for r in self._current.recordings if r.task == task), None)
        if rec is not None:
            self._open_detail(rec)

    # --- nahrávky -----------------------------------------------------------------------

    def _fill_recordings(self, visit: Visit) -> None:
        norm = self.current_norm()
        protocols = norm.info.protocols if norm is not None else {}
        recs = sorted(visit.recordings, key=lambda r: (tasks.task_order(r.task), r.file))
        self.recordings.setRowCount(len(recs))
        self._task_quality = _task_quality(recs)
        last_task = ""
        for row, rec in enumerate(recs):
            task = tasks.label(rec.task) if rec.task != last_task else ""
            last_task = rec.task
            started = f"{rec.run.started:%d. %m. %H:%M}" if rec.run.started else ""
            match = norm_match(rec, protocols)
            outdated = _changed_versions([rec], norm)
            with_norm = match_text(match)
            if match is not False and outdated:
                with_norm = tr("jiná verze feature")
            issues = quality.assess(rec.data, rec.task)
            level = quality.level_of(issues)
            quality_text = {quality.BAD: tr("nepoužitelná"), quality.WARN: tr("podezřelá")}.get(
                level, tr("ok") if quality.COLUMN_TOTAL in rec.data else ""
            )
            cells = [task, rec.file, started or rec.run.display_name, with_norm, quality_text]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setData(Qt.ItemDataRole.UserRole, rec)
                    item.setToolTip(str(rec.path) if rec.path else "")
                if col == 2:
                    item.setToolTip(rec.run.display_name)
                if col == 3 and (match is False or outdated):
                    item.setForeground(QColor(theme.WARN))
                    item.setToolTip("\n".join(outdated))
                if col == 4 and level:
                    item.setForeground(
                        QColor(theme.MISSING if level == quality.BAD else theme.WARN)
                    )
                    item.setToolTip(quality.summary(issues))
                self.recordings.setItem(row, col, item)
        notes = []
        if any(visit.count(t) > 1 for t in visit.tasks):
            notes.append(tr("Víc nahrávek jedné úlohy se zprůměruje po jednotlivých feature."))
        if any(norm_match(r, protocols) is False for r in recs):
            notes.append(
                tr(
                    "Jiný protokol než norma: nahrávka se počítala jinak než kontroly, "
                    "srovnání je jen orientační."
                )
            )
        changed = _changed_versions(recs, norm)
        if changed:
            notes.append(
                tr(
                    "Jiná verze feature než u kontrol normy: {items}. Normu nebo hodnocení "
                    "je třeba spočítat znovu, srovnání je jen orientační."
                ).format(items=", ".join(changed))
            )
        flagged = [r for r in recs if quality.level_of(quality.assess(r.data, r.task))]
        if flagged:
            notes.append(
                tr(
                    "Kontrola kvality: {files}. Hodnoty z takové nahrávky jsou ve stromu "
                    "označené ⚠ a mohou být zkreslené."
                ).format(
                    files="; ".join(
                        f"{r.file} ({quality.summary(quality.assess(r.data, r.task))})"
                        for r in flagged
                    )
                )
            )
        if self._missing:
            notes.append(
                tr("V bězích chybí: {files} (běh smazaný nebo přepočítaný).").format(
                    files=", ".join(r.file for r in self._missing)
                )
            )
        self.recordings_note.setText("\n".join(notes))
        self.recordings_note.setVisible(bool(notes))

    def recording_at(self, row: int) -> Recording | None:
        item = self.recordings.item(row, 1)
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return data if isinstance(data, Recording) else None

    def _open_recording(self, row: int) -> None:
        rec = self.recording_at(row)
        if rec is not None and rec.path is not None:
            open_file(rec.path, self.notice.emit)

    def _detail_row(self, row: int) -> None:
        """Dvojklik na nahrávku = detail hodnot (jako na Výsledcích); přehrání je v nabídce."""
        rec = self.recording_at(row)
        if rec is not None:
            self._open_detail(rec)

    def _recording_menu(self, pos) -> None:  # noqa: ANN001
        index = self.recordings.indexAt(pos)
        rec = self.recording_at(index.row()) if index.isValid() else None
        if rec is None or rec.path is None:
            return
        menu = file_menu(self, rec.path, self.notice.emit)
        detail = menu.addAction(tr("Detail nahrávky…"))
        detail.triggered.connect(lambda: self._open_detail(rec))
        run_action = menu.addAction(tr("Otevřít běh ve Výsledcích"))
        run_action.triggered.connect(lambda: self.open_run_requested.emit(rec.run.dir))
        folder = menu.addAction(tr("Otevřít složku běhu"))
        folder.triggered.connect(lambda: _open_folder(rec.run.dir))
        menu.exec(self.recordings.viewport().mapToGlobal(pos))

    def _open_detail(self, rec: Recording) -> None:
        catalog = [info.feature for info in self.columns().values()]
        seen: list[FeatureInfo] = []
        for feature in catalog:
            if feature not in seen:
                seen.append(feature)
        RecordingDetailDialog(rec.data, seen, self).exec()

    # --- export -----------------------------------------------------------------------

    def _chart_image(self) -> QPixmap:
        """Pavouk se stupnicí vedle, aby PNG do zprávy neslo i význam kružnic; ve Vývoji
        mřížka grafů s tabulkou."""
        if self._view == "trend":
            return self.trend_panel.grab()
        return _side_by_side(self.chart.grab(self.chart.content_rect()), self.scale.grab())

    def save_image(self, path: Path | None = None) -> Path | None:
        if path is None:
            suffix = ""
            if self._view == "trend":
                suffix = "-vyvoj" + ("-pavouk" if self.effective_form() == "spider" else "")
                if self.effective_scale() == "values":
                    suffix += "-hodnoty"
            name = (self._current.subject if self._current else "pavouk") + suffix + ".png"
            # výchozí místo: složka výsledků (Dokumenty\SpeechScope), ne pracovní adresář
            folder = self._index.work_root if self._index is not None else Path.home()
            chosen, _ = QFileDialog.getSaveFileName(
                self, tr("Uložit obrázek"), str(folder / name), tr("Obrázek PNG (*.png)")
            )
            if not chosen:
                return None
            path = Path(chosen)
        pixmap = self._chart_image()
        if not pixmap.save(str(path), "PNG"):
            self.notice.emit(tr("Obrázek se nepodařilo uložit."))
            return None
        self.notice.emit(tr("Obrázek uložen: {path}").format(path=path))
        return path


def _open_folder(folder: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _side_by_side(left: QPixmap, right: QPixmap, gap: int = 12) -> QPixmap:
    """Dva obrázky vedle sebe na bílém podkladu, svisle na střed.

    Počítá v logických bodech a zachová poměr pixelů displeje (`grab` při
    škálování 150 % vrací větší pixmapu), jinak by se obrázky zmenšily."""
    dpr = left.devicePixelRatio() or 1.0
    lw, lh = left.width() / dpr, left.height() / dpr
    rw, rh = right.width() / dpr, right.height() / dpr
    width, height = lw + gap + rw, max(lh, rh)
    out = QPixmap(int(round(width * dpr)), int(round(height * dpr)))
    out.setDevicePixelRatio(dpr)
    out.fill(QColor(theme.CARD))
    painter = QPainter(out)
    painter.drawPixmap(QPointF(0, (height - lh) / 2), left)
    painter.drawPixmap(QPointF(lw + gap, (height - rh) / 2), right)
    painter.end()
    return out


def _task_quality(recs: list[Recording]) -> dict[str, str]:
    """Úloha → text kontroly kvality, když některá její nahrávka neprošla."""
    out: dict[str, str] = {}
    for rec in recs:
        issues = quality.assess(rec.data, rec.task)
        if not quality.level_of(issues):
            continue
        text = tr("kvalita nahrávky {file}: {issues}").format(
            file=rec.file, issues=quality.summary(issues)
        )
        out[rec.task] = f"{out[rec.task]}\n{text}" if rec.task in out else text
    return out


def _versions_tooltip(item: ItemResult) -> str:
    if item.versions is None:
        return ""
    patient, control = item.versions
    return "\n" + tr("Verze feature {patient} u pacienta, {norm} u kontrol normy.").format(
        patient=patient, norm=control
    )


def _changed_versions(recs: list[Recording], norm: Norm | None) -> list[str]:
    """Popisy feature, které se u nahrávek počítaly jinou verzí než u kontrol normy."""
    if norm is None:
        return []
    out: list[str] = []
    for rec in recs:
        changes = version_changes(
            rec.run.feature_versions, norm.info.feature_versions.get(rec.task, {})
        )
        for feature, (patient, control) in sorted(changes.items()):
            text = tr("{feature} {patient} (norma {norm})").format(
                feature=feature, patient=patient, norm=control
            )
            if text not in out:
                out.append(text)
    return out


def _with_unit(text: str, unit: str) -> str:
    return f"{text} {unit}" if unit else text


def _item_key(row) -> str:  # noqa: ANN001
    """Klíč řádku položky v přehledu Hodnot: úloha a sloupec."""
    return f"{row.task}:{row.item.column}"


def _change_side(delta: float | None) -> int:
    """Změna skóre dimenze za období: od 1 SD k horšímu 1, k lepšímu -1, jinak 0."""
    if delta is None or abs(delta) < 1.0:
        return 0
    return 1 if delta > 0 else -1


def _visit_labels(dates: list[str]) -> list[str]:
    """Popisky pod body grafů: celé datum do čtyř vyšetření, pak „10/25“."""
    return [format_visit_date(d) if len(dates) <= 4 else _short_date(d) for d in dates]


def _visit_tip(visit, *, norm: bool) -> str:  # noqa: ANN001
    tip = []
    if visit.age is not None:
        tip.append(tr("{age} let").format(age=int(round(visit.age))))
    if visit.outdated:
        tip.append(
            tr("jiná verze feature než norma, přepočítejte vyšetření")
            if norm
            else tr("některá feature jinou verzí než ostatní vyšetření")
        )
    return " · ".join(tip)


def _format_delta(delta: float, reference: float | None) -> str:
    """Změna v jednotkách feature se znaménkem a s desetinnými místy podle velikosti
    hodnoty, ke které patří: „+17“ u stovek ms, „-0,9“ u decibelů."""
    scale = abs(reference) if reference is not None else abs(delta)
    decimals = 0 if scale >= 100 else 1 if scale >= 10 else 2
    text = f"{abs(delta):.{decimals}f}".replace(".", ",")
    if float(text.replace(",", ".")) == 0:
        return "0"
    return ("+" if delta > 0 else "-") + text


def _group_subtitle(rows: list) -> str:
    """Podtitulek skupiny v měřítku Hodnoty: počet položek a směr, když ho mají společný."""
    n = len(rows)
    count = (
        tr("1 položka")
        if n == 1
        else tr("{n} položky").format(n=n)
        if n < 5
        else tr("{n} položek").format(n=n)
    )
    directions = {row.item.direction for row in rows}
    if len(directions) != 1:
        return count
    direction = directions.pop()
    text = {
        HIGHER: tr("vyšší = horší"),
        LOWER: tr("nižší = horší"),
        BOTH: tr("patologie oběma směry, bez šipky"),
    }.get(direction, "")
    return f"{count} · {text}" if text else count


def _short_date(iso: str) -> str:
    """Popisek pod bodem grafu: „10/25“ z ISO data."""
    try:
        date = dt.date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{date.month}/{date.year % 100:02d}"


def _selection_part(task: str, sel: Selection) -> str:
    text = tr("{task}: {n} kontrol").format(task=tasks.label(task), n=sel.n)
    if sel.same_sex:
        text += tr(", jen {sex}").format(sex=tr("ženy") if sel.n_female else tr("muži"))
    rng = sel.age_range
    if rng is not None and sel.window is not None:
        text += tr(", {lo} až {hi} let").format(lo=int(rng[0]), hi=int(rng[1]))
    if sel.widened:
        text += (
            tr(" (okno rozšířeno na ±{w})").format(w=sel.window)
            if sel.window is not None
            else tr(" (celá norma, v okně bylo málo kontrol)")
        )
    return text


def _percentile_text(result: DimensionResult) -> str:
    if result.percentile is not None:
        return f"{result.percentile:.0f}"
    scored = result.scored
    if not scored:
        return ""
    return f"{sum(i.score.percentile for i in scored) / len(scored):.0f}"


def _dimension_label(result: DimensionResult) -> str:
    """Název dimenze; u dimenze z položek `both` se šipkou, kterým směrem pacient vybočil."""
    arrow = {1: " ▲", -1: " ▼"}.get(result.side, "")
    return result.dimension.display_name + arrow


def _item_z_tooltip(item: ItemResult) -> str:
    if item.score is None:
        return ""
    if item.item.direction == BOTH:
        side = tr("nad") if item.score.signed_z >= 0 else tr("pod")
        return tr(
            "Odchylka na obě strany je horší; pacient je {side} průměrem kontrol "
            "({z} SD bez otočení)."
        ).format(side=side, z=format_sd(item.score.signed_z).removesuffix(" SD"))
    return tr("Kladné = horším směrem.")


def _score_tooltip(result: DimensionResult) -> str:
    if result.score is None or result.raw is None:
        return ""
    if result.control_sd is None:
        return tr("Prostý průměr z-skóre položek; rozdělení u kontrol nešlo spočítat.")
    return tr(
        "Průměr z-skóre položek {raw} přepočtený na SD kontrol (u {n} kontrol má SD {sd})."
    ).format(
        raw=format_number(result.raw), n=result.n_controls, sd=format_number(result.control_sd)
    )


def _fit_name_column(table: QTableWidget) -> None:
    """Tabulka Vývoje: název podle obsahu (nejvýš NAME_MAX), data hned za ním, volné
    místo až za posledním sloupcem; Stretch dával mezeru mezi dimenzi a data."""
    header = table.horizontalHeader()
    for c in range(1, table.columnCount()):
        header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
    table.resizeColumnToContents(0)
    header.resizeSection(0, min(header.sectionSize(0) + 12, NAME_MAX))


def _style_dimension_row(item: QTreeWidgetItem) -> None:
    """Řádek dimenze: tučný název a světle šedé pozadí přes celou šířku."""
    font = item.font(0)
    font.setBold(True)
    item.setFont(0, font)
    for col in range(item.columnCount()):
        item.setBackground(col, QColor(DIMENSION_ROW_BG))
    for col in (4, 5):
        item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


def _style_score(item: QTreeWidgetItem, band: str, large: bool) -> None:
    """Barvu pásma nese jen číslo SD (podoba A, text řádku zůstává černý)."""
    item.setForeground(4, QColor(BAND_STRONG.get(band, theme.TEXT)))
    font = item.font(4)
    font.setBold(True)
    item.setFont(4, font)
    if not large:
        return
    font = item.font(4)
    font.setPointSizeF(font.pointSizeF() * 1.05)
    item.setFont(4, font)


__all__ = [
    "AssessmentPage",
    "PatientCard",
    "PendingCard",
    "worst_band",
    "format_number",
    "task_count",
]
