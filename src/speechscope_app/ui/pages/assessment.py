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
Bez normy stránka ukáže jen nahrávky a řekne, kde normu vzít.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (
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
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend import tasks
from ...backend.assessment_build import AssessmentPlan, pending_plans, plan_from_assessment
from ...backend.assessments import (
    Assessment,
    AssessmentStore,
    RecordingRef,
    norm_match,
    resolve,
)
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
from ...backend.norm_build import RUNNING
from ...backend.norms import BOTH, DEFAULT_WINDOW, Norm, Selection, all_norms, version_changes
from ...backend.visits import Recording, Visit, VisitIndex
from ...i18n import tr
from .. import theme
from ..assessment_build_dialog import NewAssessmentChoiceDialog
from ..assessment_dialog import AssessmentDialog, match_text
from ..feature_detail import split_unit
from ..file_actions import file_menu, open_file
from ..recording_detail import ColumnInfo, RecordingDetailDialog, column_index
from ..widgets.elided_label import ElidedLabel
from ..widgets.spider_chart import (
    BAND_SOFT,
    INWARD,
    OUTWARD,
    Axis,
    ScaleLegend,
    ScoreBar,
    SpiderChart,
    format_sd,
)
from ..widgets.status_header import StatusHeader

BAND_LABELS: dict[str, str] = {
    "ok": tr("v normě"),
    "warn": tr("hraniční"),
    "bad": tr("mimo normu"),
}
BAND_ROLES: dict[str, str] = {"ok": "ok", "warn": "warn", "bad": "missing"}
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


class VisitCard(QFrame):
    def __init__(
        self,
        visit: Visit,
        band: str,
        assessment: Assessment | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.visit = visit
        self.assessment = assessment
        self.setObjectName("card")
        self._role = BAND_ROLES.get(band, "neutral")
        theme.set_role(self, self._role)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        name = (
            visit.subject
            if visit.explicit
            else tr("{file} (bez pacienta)").format(file=visit.subject)
        )
        self.title = ElidedLabel(name)
        self.title.setObjectName("card_title")
        head.addWidget(self.title, 1)
        self.pill = QLabel(BAND_LABELS.get(band, tr("bez normy")))
        self.pill.setObjectName("pill")
        theme.set_role(self.pill, BAND_ROLES.get(band, "neutral"))
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        self.detail = QLabel(visit_subtitle(visit))
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

    def set_current(self, current: bool) -> None:
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
    open_run_requested = Signal(object)  # složka běhu: Otevřít běh ve Výsledcích
    orientation_changed = Signal(str)  # orientace pavouka (hlavní okno uloží)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index: VisitIndex | None = None
        self._store: AssessmentStore | None = None
        self._entries: list[tuple[Assessment, Visit, list[RecordingRef]]] = []
        self._shown: list[tuple[Assessment, Visit, list[RecordingRef]]] = []
        self._missing: list[RecordingRef] = []
        self._plans: list[AssessmentPlan] = []
        self._shown_plans: list[AssessmentPlan] = []
        self._norms: list[Norm] = []
        self._norms_dir: Path | None = None
        self._library: Library | None = None
        self._columns: dict[str, ColumnInfo] | None = None
        self._dimensions: list[Dimension] = load_dimensions()
        self._cards: list[VisitCard] = []
        self._eval_cache: dict[str, Evaluation] = {}  # id hodnocení → vyhodnocení
        self._eval_key: tuple = ()  # (norma, okno, pohlaví), pro které cache platí
        self._current: Visit | None = None
        self._evaluation: Evaluation | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        title = QLabel(tr("Hodnocení"))
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
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)

        # --- seznam vyšetření -----------------------------------------------------------
        left = QWidget()
        left.setMinimumWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        head = QHBoxLayout()
        self.list_title = QLabel(tr("Hodnocení"))
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

        # --- pravá část -------------------------------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(8)
        self.header = StatusHeader()
        self.header.title.setText(tr("Hodnocení"))
        self.header.subtitle.setText(tr("Vyberte hodnocení vlevo, nebo založte nové."))
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
        self.norm_combo.currentIndexChanged.connect(self._recompute)
        norm_row.addWidget(self.norm_combo)
        norm_row.addWidget(QLabel(tr("Věkové okno")))
        self.window_combo = QComboBox()
        for label, value in WINDOWS:
            self.window_combo.addItem(label, value)
        self.window_combo.setCurrentIndex(self.window_combo.findData(DEFAULT_WINDOW))
        self.window_combo.currentIndexChanged.connect(self._recompute)
        norm_row.addWidget(self.window_combo)
        self.same_sex = QCheckBox(tr("jen stejné pohlaví"))
        self.same_sex.setChecked(True)
        self.same_sex.toggled.connect(self._recompute)
        norm_row.addWidget(self.same_sex)
        self.selection_label = QLabel("")
        self.selection_label.setObjectName("muted")
        self.selection_label.setWordWrap(True)
        norm_row.addStretch(1)
        norm_block = QVBoxLayout()
        norm_block.setContentsMargins(0, 0, 0, 0)
        norm_block.setSpacing(2)
        norm_block.addLayout(norm_row)
        norm_block.addWidget(self.selection_label)  # věta pod ovládáním, ne vedle něj
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
        chart_card = QFrame()
        chart_card.setObjectName("card")
        chart_card.setMinimumHeight(420)
        chart_layout = QVBoxLayout(chart_card)
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
        middle.addWidget(chart_card)

        tree_card = QFrame()
        tree_card.setObjectName("card")
        tree_card.setMinimumHeight(380)
        tree_layout = QVBoxLayout(tree_card)
        tree_layout.setContentsMargins(12, 10, 12, 10)
        tree_layout.setSpacing(6)
        tree_title = QLabel(tr("Dimenze a jejich položky"))
        tree_title.setObjectName("card_title")
        tree_layout.addWidget(tree_title)
        tree_hint = QLabel(tr("z = o kolik SD horším směrem, percentil mezi kontrolami"))
        tree_hint.setObjectName("muted")
        tree_layout.addWidget(tree_hint)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(
            [
                tr("dimenze / položka"),
                tr("úloha"),
                tr("pacient"),
                tr("norma"),
                "z",
                tr("percentil"),
                "",
            ]
        )
        self.tree.setAlternatingRowColors(False)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        # první sloupec bere zbytek šířky, ostatní podle obsahu
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(40)
        for col in range(1, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(6, 90)
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)
        tree_layout.addWidget(self.tree, 1)
        middle.addWidget(tree_card, 1)
        self.middle = QWidget()
        self.middle.setLayout(middle)
        right_layout.addWidget(self.middle, 1)

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
        self.recordings.setColumnCount(4)
        self.recordings.setHorizontalHeaderLabels(
            [tr("úloha"), tr("nahrávka"), tr("běh"), tr("s normou")]
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
        self.plan_header = StatusHeader()
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
                "čím větší plocha, tím blíž zdravým: silná kružnice je průměr kontrol, "
                "blíž ke středu = horší, u okraje = lepší než kontroly"
            )
            if orientation == INWARD
            else tr(
                "silná kružnice je průměr kontrol, zelený prstenec ±1 SD je v normě, "
                "dál od středu = horší, bílý střed = lepší než kontroly"
            )
        )

    def set_work_root(self, root: Path) -> None:
        if self._index is None:
            self._index = VisitIndex(root)
        else:
            self._index.set_work_root(root)

    def set_assessments_dir(self, folder: Path) -> None:
        self._store = AssessmentStore(folder)
        self.refresh()

    def set_norms_dir(self, folder: Path) -> None:
        self._norms_dir = folder
        self.reload_norms()

    def reload_norms(self) -> None:
        """Znovu načte normy (po importu, uložení nebo smazání) a zachová výběr."""
        current = self.current_norm()
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
        if current is not None:
            for i, norm in enumerate(self._norms):
                if norm.path == current.path and norm.info.name == current.info.name:
                    self.norm_combo.setCurrentIndex(i)
                    break
        self.norm_combo.blockSignals(False)
        has = bool(self._norms)
        self.norm_row.setVisible(has)
        self.no_norm.setVisible(not has)
        self._recompute()

    def current_norm(self) -> Norm | None:
        data = self.norm_combo.currentData()
        return data if isinstance(data, Norm) else None

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
        self._eval_cache.clear()  # vyšetření se složila znovu, tabulky mohly přibýt
        self._plans = pending_plans(self._store.folder) if self._store is not None else []
        self._fill_list(keep=keep)

    def recordings_pool(self) -> list[Recording]:
        """Nahrávky z hotových běhů (bez běhů norem) pro dialog hodnocení."""
        if self._index is None:
            return []
        self._index.refresh()
        return self._index.all_recordings()

    def _visit_band(self, assessment: Assessment, visit: Visit) -> str:
        norm = self.current_norm()
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
        for entry in self._entries:
            assessment, visit, _missing = entry
            if needle and needle not in assessment.subject.lower():
                continue
            if assessment.id in pending_ids:
                continue
            item = QListWidgetItem()
            card = VisitCard(visit, self._visit_band(assessment, visit), assessment)
            item.setSizeHint(card.sizeHint())
            self.visits.addItem(item)
            self.visits.setItemWidget(item, card)
            self._cards.append(card)
            if keep is not None and assessment.id == keep:
                select_row = len(self._shown_plans) + len(shown)
            shown.append(entry)
        self._shown = shown
        self._fit_cards()
        self.visits.blockSignals(False)
        total = len(self._plans) + sum(1 for a, _v, _m in self._entries if a.id not in pending_ids)
        self.list_title.setText(tr("Hodnocení ({n})").format(n=total) if total else tr("Hodnocení"))
        self.empty.setVisible(not total)
        if select_row is not None:
            self.visits.setCurrentRow(select_row)
        elif shown or self._shown_plans:
            self.visits.setCurrentRow(0)
        else:
            self._current = None
            self._missing = []
            self._show_empty_state()

    def count(self) -> int:
        return self.visits.count()

    def current_plan(self) -> AssessmentPlan | None:
        row = self.visits.currentRow()
        return self._shown_plans[row] if 0 <= row < len(self._shown_plans) else None

    def plans(self) -> list[AssessmentPlan]:
        return list(self._plans)

    def card(self, row: int) -> VisitCard | PendingCard | None:
        return self._cards[row] if 0 <= row < len(self._cards) else None

    def select_row(self, row: int) -> None:
        if 0 <= row < self.visits.count():
            self.visits.setCurrentRow(row)

    def select_assessment(self, assessment_id: str) -> bool:
        offset = len(self._shown_plans)
        for row, (assessment, _v, _m) in enumerate(self._shown):
            if assessment.id == assessment_id:
                self.visits.setCurrentRow(offset + row)
                return True
        for row, plan in enumerate(self._shown_plans):
            if plan.id == assessment_id:
                self.visits.setCurrentRow(row)
                return True
        return False

    # --- založení, úprava, smazání ----------------------------------------------------

    def make_dialog(
        self, assessment: Assessment | None = None, initial: Recording | None = None
    ) -> AssessmentDialog:
        norm = self.current_norm()
        return AssessmentDialog(
            self.recordings_pool(),
            assessment=assessment,
            initial=initial,
            norm_protocols=norm.info.protocols if norm is not None else {},
            parent=self,
        )

    def save_from(self, dialog: AssessmentDialog) -> Assessment | None:
        """Uloží hodnocení z potvrzeného dialogu a vybere ho."""
        if self._store is None:
            return None
        assessment = dialog.assessment()
        self._store.save(assessment)
        self.search.clear()
        self.refresh(select=assessment.id)
        return assessment

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
        self._recompute()

    # --- vyhodnocení --------------------------------------------------------------------

    def _evaluate(self, visit: Visit, norm: Norm) -> Evaluation:
        return evaluate(
            self._dimensions,
            visit.rows_by_task(),
            norm,
            age=visit.age,
            sex=visit.sex,
            window=self.window_combo.currentData(),
            same_sex=self.same_sex.isChecked(),
            versions=visit.versions_by_task(),
        )

    def _cached_evaluation(self, assessment: Assessment, visit: Visit, norm: Norm) -> Evaluation:
        """Vyhodnocení hodnocení; platí, dokud se nezmění norma, okno nebo pohlaví.

        Štítky karet v seznamu na vybraném pacientovi nezávisí, takže kliknutí
        na kartu nesmí přepočítávat všechny ostatní (60 ms na pacienta).
        """
        key = (id(norm), self.window_combo.currentData(), self.same_sex.isChecked())
        if key != self._eval_key:
            self._eval_cache.clear()
            self._eval_key = key
        cached = self._eval_cache.get(assessment.id)
        if cached is None:
            cached = self._eval_cache[assessment.id] = self._evaluate(visit, norm)
        return cached

    def _recompute(self) -> None:
        visit, norm = self._current, self.current_norm()
        if visit is None:
            self._show_empty_state()
            return
        self.header.title.setText(visit.subject)
        self.header.subtitle.setText(visit_subtitle(visit))
        self._fill_recordings(visit)
        self.edit_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
        self._update_recompute()
        if norm is None:
            self._evaluation = None
            self.header.set_role("neutral")
            self.header.set_pills([])
            self.middle.hide()
            self.image_btn.setEnabled(False)
            self.about.setText("")
            return
        self.middle.show()
        current = self.current_assessment()
        self._evaluation = (
            self._cached_evaluation(current, visit, norm)
            if current is not None
            else self._evaluate(visit, norm)
        )
        band = worst_band(self._evaluation)
        self.header.set_role(BAND_ROLES.get(band, "neutral"))
        pills = []
        for result in self._evaluation.shown:
            pills.append(
                (
                    f"{_dimension_label(result)} {format_sd(result.score or 0.0)}",
                    BAND_ROLES.get(result.band, "neutral"),
                    "",
                    None,
                )
            )
        self.header.set_pills(pills)
        self._fill_chart(self._evaluation)
        self._fill_tree(self._evaluation)
        self.selection_label.setText(self._selection_text(visit, self._evaluation))
        self.same_sex.setEnabled(visit.sex in ("f", "m"))
        self.about.setText(self._about_text(norm))
        self.image_btn.setEnabled(bool(self._evaluation.shown))
        # štítky v seznamu podle téže normy a téhož okna
        for card in self._cards:
            if not isinstance(card, VisitCard):
                continue  # rozpracované hodnocení má štítek z plánu, na normě nezávisí
            band = (
                worst_band(self._cached_evaluation(card.assessment, card.visit, norm))
                if card.visit is not visit
                else worst_band(self._evaluation)
            )
            card.pill.setText(BAND_LABELS.get(band, tr("bez normy")))
            theme.set_role(card.pill, BAND_ROLES.get(band, "neutral"))
            card._role = BAND_ROLES.get(band, "neutral")
            card.set_current(card.visit is visit)

    def _show_empty_state(self) -> None:
        self._evaluation = None
        self.plan_detail.setVisible(False)
        self.right.setVisible(True)
        self.header.title.setText(tr("Hodnocení"))
        self.header.subtitle.setText(tr("Vyberte hodnocení vlevo, nebo založte nové."))
        self.header.set_role("neutral")
        self.header.set_pills([])
        self.middle.hide()
        self.recordings.setRowCount(0)
        self.recordings_note.setText("")
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

    def _fill_tree(self, evaluation: Evaluation) -> None:
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
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            if result.shown:
                _colorize(top, result.band)
                self.tree.addTopLevelItem(top)
                self.tree.setItemWidget(top, 6, ScoreBar(result.score, result.band))
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
                        norm_text, z_text, pct = item.reason, "", ""
                    if item.versions is not None:
                        label += " \u26a0"  # feature se od spočítání normy změnila
                    child = QTreeWidgetItem(
                        [
                            label,
                            tasks.label(item.task),
                            value,
                            norm_text,
                            z_text,
                            pct,
                            "",
                        ]
                    )
                    child.setData(0, Qt.ItemDataRole.UserRole, item)
                    child.setToolTip(0, item.item.column + _versions_tooltip(item))
                    child.setToolTip(4, _item_z_tooltip(item))
                    if item.score is not None:
                        _colorize(child, item.band)
                    else:
                        child.setForeground(0, QColor(theme.MUTED))
                    top.addChild(child)
                    if item.score is not None:
                        self.tree.setItemWidget(child, 6, ScoreBar(item.score.z, item.band))
                top.setExpanded(result.band == "bad")
            else:
                top.setText(2, tr("bez dat: {reason}").format(reason=result.reason))
                for col in range(7):
                    top.setForeground(col, QColor(theme.MUTED))
                self.tree.addTopLevelItem(top)
        # nejhorší dimenze rozbalená, aby bylo hned vidět proč
        shown = [r for r in evaluation.shown]
        if shown:
            worst = max(shown, key=lambda r: r.score or 0.0)
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item.data(0, Qt.ItemDataRole.UserRole) is worst:
                    item.setExpanded(True)

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
            cells = [task, rec.file, started or rec.run.display_name, with_norm]
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
        """Pavouk se stupnicí vedle, aby PNG do zprávy neslo i význam kružnic."""
        return _side_by_side(self.chart.grab(self.chart.content_rect()), self.scale.grab())

    def save_image(self, path: Path | None = None) -> Path | None:
        if path is None:
            name = (self._current.subject if self._current else "pavouk") + ".png"
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


def _colorize(item: QTreeWidgetItem, band: str) -> None:
    color = QColor(BAND_SOFT.get(band, theme.NEUTRAL_SOFT))
    for col in range(item.columnCount()):
        item.setBackground(col, color)


__all__ = [
    "AssessmentPage",
    "VisitCard",
    "PendingCard",
    "worst_band",
    "format_number",
    "task_count",
]
