"""Stránka Hodnocení: vyšetření pacienta proti normě zdravých kontrol.

Vlevo seznam vyšetření z indexu (`backend/visits.py`), vpravo norma s
věkovým oknem a pohlavím, pavouk po dimenzích, strom dimenzí a položek,
nahrávky vyšetření a popis normy. Skóre počítá `dimensions.evaluate`.
Bez normy stránka ukáže jen nahrávky a řekne, kde normu vzít.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.dimensions import Dimension, DimensionResult, Evaluation, evaluate, load_dimensions
from ...backend.library import FeatureInfo, Library, LibraryError
from ...backend.norms import Norm, Selection, all_norms
from ...backend.visits import Recording, Visit, VisitIndex
from ...i18n import tr
from .. import theme
from ..feature_detail import split_unit
from ..file_actions import file_menu, open_file
from ..recording_detail import ColumnInfo, RecordingDetailDialog, column_index
from ..widgets.elided_label import ElidedLabel
from ..widgets.spider_chart import BAND_SOFT, Axis, ScoreBar, SpiderChart, format_sd
from ..widgets.status_header import StatusHeader

BAND_LABELS: dict[str, str] = {
    "ok": tr("v normě"),
    "warn": tr("hraniční"),
    "bad": tr("mimo normu"),
}
BAND_ROLES: dict[str, str] = {"ok": "ok", "warn": "warn", "bad": "missing"}
WINDOWS: tuple[tuple[str, int | None], ...] = (
    ("± 10 let", 10),
    ("± 15 let", 15),
    ("± 20 let", 20),
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
    parts.append(", ".join(contract.TASK_SHORT.get(t, t) for t in visit.tasks))
    return " · ".join(parts)


class VisitCard(QFrame):
    def __init__(self, visit: Visit, band: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.visit = visit
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


class AddRecordingDialog(QDialog):
    """Výběr nahrávky z jiného běhu, která patří k tomuto vyšetření."""

    def __init__(
        self, visit: Visit, candidates: list[Recording], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Přidat nahrávku k vyšetření"))
        self.resize(640, 480)
        self._candidates = candidates
        layout = QVBoxLayout(self)
        hint = QLabel(
            tr(
                "Nahrávky z ostatních běhů. Vybraná se zařadí k vyšetření {name}; "
                "platí to i po restartu."
            ).format(name=visit.subject)
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat soubor nebo pacienta"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._fill()

    def _fill(self) -> None:
        needle = self.search.text().strip().lower()
        self.list.clear()
        for rec in self._candidates:
            subject = str(rec.data.get("subject") or "")
            text = tr("{file} · {task} · {run}").format(
                file=rec.file,
                task=contract.TASK_SHORT.get(rec.task, rec.task),
                run=rec.run.display_name
                + (f" · {rec.run.started:%d.%m.%Y}" if rec.run.started else ""),
            )
            if subject:
                text += f" · {subject}"
            if needle and needle not in text.lower():
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, rec)
            self.list.addItem(item)

    def chosen(self) -> Recording | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None


class AssessmentPage(QWidget):
    notice = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index: VisitIndex | None = None
        self._norms: list[Norm] = []
        self._norms_dir: Path | None = None
        self._library: Library | None = None
        self._columns: dict[str, ColumnInfo] | None = None
        self._dimensions: list[Dimension] = load_dimensions()
        self._visits: list[Visit] = []
        self._cards: list[VisitCard] = []
        self._current: Visit | None = None
        self._evaluation: Evaluation | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        title = QLabel(tr("Hodnocení"))
        title.setObjectName("page_title")
        layout.addWidget(title)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)

        # --- seznam vyšetření -----------------------------------------------------------
        left = QWidget()
        left.setMinimumWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        head = QHBoxLayout()
        self.list_title = QLabel(tr("Vyšetření"))
        self.list_title.setObjectName("section")
        self.refresh_btn = QPushButton(tr("Obnovit"))
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.list_title, 1)
        head.addWidget(self.refresh_btn)
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
                "Zatím žádné vyšetření. Vyšetření vznikají z hotových běhů; pacienta a "
                "návštěvu určují sloupce subject a visit v manifestu."
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
        self.header.subtitle.setText(tr("Vyber vyšetření vlevo."))
        self.image_btn = QPushButton(tr("Uložit obrázek…"))
        self.image_btn.setToolTip(tr("Pavouk jako PNG, třeba do zprávy."))
        self.image_btn.setEnabled(False)
        self.image_btn.clicked.connect(self.save_image)
        self.header.add_button(self.image_btn)
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
        chart_hint = QLabel(tr("kružnice = 1 a 2 směrodatné odchylky, dál od středu = horší"))
        chart_hint.setObjectName("muted")
        chart_hint.setWordWrap(True)
        chart_layout.addWidget(chart_hint)
        chart_row = QHBoxLayout()
        chart_row.setSpacing(16)
        self.chart = SpiderChart()
        chart_row.addWidget(self.chart, 1)
        side = QVBoxLayout()
        side.setSpacing(10)
        legend = tr("● do 1 SD v normě · ● 1 až 2 SD hraniční · ● nad 2 SD mimo normu")
        colors = (theme.OK, theme.WARN, theme.MISSING)  # tečky v barvách pásem pavouka
        self.legend = QLabel(
            "<br>".join(
                part.replace("●", f"<span style='color:{color}'>●</span>", 1)
                for part, color in zip(legend.split(" · "), colors, strict=False)
            )
        )
        self.legend.setObjectName("muted")
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend.setWordWrap(True)
        side.addWidget(self.legend)
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
        rec_head = QHBoxLayout()
        rec_title = QLabel(tr("Nahrávky vyšetření"))
        rec_title.setObjectName("card_title")
        rec_head.addWidget(rec_title, 1)
        self.add_btn = QPushButton(tr("Přidat nahrávku z jiného běhu…"))
        self.add_btn.setToolTip(
            tr("Pro data bez manifestu: zařadí nahrávku z jiného běhu k tomuto vyšetření.")
        )
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self.add_recording)
        rec_head.addWidget(self.add_btn)
        self.remove_btn = QPushButton(tr("Vyřadit"))
        self.remove_btn.setToolTip(tr("Zruší ruční zařazení vybrané nahrávky."))
        self.remove_btn.hide()
        self.remove_btn.clicked.connect(self.remove_recording)
        rec_head.addWidget(self.remove_btn)
        rec_layout.addLayout(rec_head)
        self.recordings = QListWidget()
        self.recordings.setMaximumHeight(96)
        self.recordings.itemDoubleClicked.connect(self._open_recording)
        self.recordings.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recordings.customContextMenuRequested.connect(self._recording_menu)
        self.recordings.currentItemChanged.connect(self._recording_changed)
        rec_layout.addWidget(self.recordings)
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

        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([320, 800])
        self._show_empty_state()

    # --- napojení --------------------------------------------------------------------------

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self._columns = None

    def set_work_root(self, root: Path, assignments: Path | None = None) -> None:
        if self._index is None:
            self._index = VisitIndex(root, assignments)
        else:
            self._index.set_work_root(root)

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

    def refresh(self, select: tuple[str, str] | None = None) -> None:
        """Znovu projde běhy; výběr zůstane na témže vyšetření."""
        keep = select or (self._current.key if self._current else None)
        self._visits = self._index.refresh() if self._index is not None else []
        self._fill_list(keep=keep)

    def _visit_band(self, visit: Visit) -> str:
        norm = self.current_norm()
        if norm is None:
            return ""
        return worst_band(self._evaluate(visit, norm))

    def _fill_list(self, keep: tuple[str, str] | None = None) -> None:
        if keep is None and self._current is not None:
            keep = self._current.key
        needle = self.search.text().strip().lower()
        self.visits.blockSignals(True)
        self.visits.clear()
        self._cards = []
        shown: list[Visit] = []
        select_row: int | None = None
        for visit in self._visits:
            if needle and needle not in visit.subject.lower():
                continue
            item = QListWidgetItem()
            card = VisitCard(visit, self._visit_band(visit))
            item.setSizeHint(card.sizeHint())
            self.visits.addItem(item)
            self.visits.setItemWidget(item, card)
            self._cards.append(card)
            if keep is not None and visit.key == keep:
                select_row = len(shown)
            shown.append(visit)
        self._shown = shown
        self._fit_cards()
        self.visits.blockSignals(False)
        self.list_title.setText(
            tr("Vyšetření ({n})").format(n=len(self._visits)) if self._visits else tr("Vyšetření")
        )
        self.empty.setVisible(not self._visits)
        if select_row is not None:
            self.visits.setCurrentRow(select_row)
        elif shown:
            self.visits.setCurrentRow(0)
        else:
            self._current = None
            self._show_empty_state()

    def count(self) -> int:
        return self.visits.count()

    def card(self, row: int) -> VisitCard | None:
        return self._cards[row] if 0 <= row < len(self._cards) else None

    def select_row(self, row: int) -> None:
        if 0 <= row < self.visits.count():
            self.visits.setCurrentRow(row)

    def select_visit(self, key: tuple[str, str]) -> bool:
        for row, card in enumerate(self._cards):
            if card.visit.key == key:
                self.visits.setCurrentRow(row)
                return True
        return False

    def show_row(self, run_dir: Path, row: int) -> bool:
        """Z Výsledků: vybere vyšetření, do kterého řádek běhu patří."""
        if self._index is None:
            return False
        self.refresh()
        visit = self._index.visit_of(run_dir, row)
        if visit is None:
            return False
        self.search.clear()
        return self.select_visit(visit.key)

    def _item_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        for row, card in enumerate(self._cards):
            card.set_current(self.visits.item(row) is current)
        row = self.visits.currentRow()
        visit = self._shown[row] if 0 <= row < len(self._shown) else None
        self._current = visit
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
        )

    def _recompute(self) -> None:
        visit, norm = self._current, self.current_norm()
        if visit is None:
            self._show_empty_state()
            return
        self.header.title.setText(
            visit.subject
            if visit.explicit
            else tr("{file} (bez pacienta)").format(file=visit.subject)
        )
        self.header.subtitle.setText(visit_subtitle(visit))
        self._fill_recordings(visit)
        self.add_btn.setEnabled(self._index is not None)
        if norm is None:
            self._evaluation = None
            self.header.set_role("neutral")
            self.header.set_pills([])
            self.middle.hide()
            self.image_btn.setEnabled(False)
            self.about.setText("")
            return
        self.middle.show()
        self._evaluation = self._evaluate(visit, norm)
        band = worst_band(self._evaluation)
        self.header.set_role(BAND_ROLES.get(band, "neutral"))
        pills = []
        for result in self._evaluation.shown:
            pills.append(
                (
                    f"{result.dimension.display_name} {format_sd(result.score or 0.0)}",
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
            band = (
                worst_band(self._evaluate(card.visit, norm))
                if card.visit is not visit
                else worst_band(self._evaluation)
            )
            card.pill.setText(BAND_LABELS.get(band, tr("bez normy")))
            theme.set_role(card.pill, BAND_ROLES.get(band, "neutral"))
            card._role = BAND_ROLES.get(band, "neutral")
            card.set_current(card.visit is visit)

    def _show_empty_state(self) -> None:
        self._evaluation = None
        self.header.title.setText(tr("Hodnocení"))
        self.header.subtitle.setText(tr("Vyber vyšetření vlevo."))
        self.header.set_role("neutral")
        self.header.set_pills([])
        self.middle.hide()
        self.recordings.clear()
        self.add_btn.setEnabled(False)
        self.remove_btn.hide()
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
        axes = [Axis(r.dimension.display_name, r.score or 0.0, r.band) for r in evaluation.shown]
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
        label = text or f"{feature_name} {info.short}"
        return label, unit

    def _fill_tree(self, evaluation: Evaluation) -> None:
        self.tree.clear()
        for result in evaluation.results:
            top = QTreeWidgetItem(
                [
                    result.dimension.display_name,
                    ", ".join(
                        contract.TASK_SHORT.get(t, t)
                        for t in sorted({i.task for i in result.items})
                    )
                    or ", ".join(contract.TASK_SHORT.get(t, t) for t in result.dimension.tasks),
                    "",
                    "",
                    format_sd(result.score).removesuffix(" SD") if result.score is not None else "",
                    _percentile_text(result),
                    "",
                ]
            )
            top.setData(0, Qt.ItemDataRole.UserRole, result)
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
                    else:
                        norm_text, z_text, pct = item.reason, "", ""
                    child = QTreeWidgetItem(
                        [
                            label,
                            contract.TASK_SHORT.get(item.task, item.task),
                            value,
                            norm_text,
                            z_text,
                            pct,
                            "",
                        ]
                    )
                    child.setData(0, Qt.ItemDataRole.UserRole, item)
                    child.setToolTip(0, item.item.column)
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
        self.recordings.clear()
        for rec in visit.recordings:
            text = tr("{file} · {task} · {run}").format(
                file=rec.file,
                task=contract.TASK_SHORT.get(rec.task, rec.task),
                run=rec.run.display_name
                + (f" · {rec.run.started:%d.%m.%Y %H:%M}" if rec.run.started else ""),
            )
            if self._index is not None and rec.ref in self._index.assignments:
                text += tr(" (zařazeno ručně)")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, rec)
            item.setToolTip(str(rec.path) if rec.path else "")
            self.recordings.addItem(item)
        self.remove_btn.hide()

    def _recording_changed(self, current: QListWidgetItem | None, _previous) -> None:  # noqa: ANN001
        rec = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        manual = rec is not None and self._index is not None and rec.ref in self._index.assignments
        self.remove_btn.setVisible(bool(manual))

    def _open_recording(self, item: QListWidgetItem) -> None:
        rec = item.data(Qt.ItemDataRole.UserRole)
        if rec is not None and rec.path is not None:
            open_file(rec.path, self.notice.emit)

    def _recording_menu(self, pos) -> None:  # noqa: ANN001
        item = self.recordings.itemAt(pos)
        if item is None:
            return
        rec = item.data(Qt.ItemDataRole.UserRole)
        if rec is None or rec.path is None:
            return
        menu = file_menu(self, rec.path, self.notice.emit)
        detail = menu.addAction(tr("Detail nahrávky…"))
        detail.triggered.connect(lambda: self._open_detail(rec))
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

    def add_recording(self) -> AddRecordingDialog | None:
        if self._current is None or self._index is None:
            return None
        mine = {r.ref for r in self._current.recordings}
        candidates = [r for r in self._index.all_recordings() if r.ref not in mine]
        dialog = AddRecordingDialog(self._current, candidates, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rec = dialog.chosen()
            if rec is not None:
                self._index.assign(rec, self._current)
                self._visits = self._index.visits
                self._fill_list(keep=self._current.key)
        return dialog

    def remove_recording(self) -> None:
        item = self.recordings.currentItem()
        rec = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if rec is None or self._index is None or self._current is None:
            return
        self._index.unassign(rec)
        self._visits = self._index.visits
        self._fill_list(keep=self._current.key)

    # --- export -----------------------------------------------------------------------

    def save_image(self, path: Path | None = None) -> Path | None:
        if path is None:
            name = (self._current.subject if self._current else "pavouk") + ".png"
            chosen, _ = QFileDialog.getSaveFileName(
                self, tr("Uložit obrázek"), name, tr("Obrázek PNG (*.png)")
            )
            if not chosen:
                return None
            path = Path(chosen)
        pixmap = self.chart.grab()
        if not pixmap.save(str(path), "PNG"):
            self.notice.emit(tr("Obrázek se nepodařilo uložit."))
            return None
        self.notice.emit(tr("Obrázek uložen: {path}").format(path=path))
        return path


def _open_folder(folder: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _selection_part(task: str, sel: Selection) -> str:
    text = tr("{task}: {n} kontrol").format(task=contract.TASK_SHORT.get(task, task), n=sel.n)
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
    scored = result.scored
    if not scored:
        return ""
    return f"{sum(i.score.percentile for i in scored) / len(scored):.0f}"


def _colorize(item: QTreeWidgetItem, band: str) -> None:
    color = QColor(BAND_SOFT.get(band, theme.NEUTRAL_SOFT))
    for col in range(item.columnCount()):
        item.setBackground(col, color)


__all__ = ["AssessmentPage", "VisitCard", "AddRecordingDialog", "worst_band", "format_number"]
