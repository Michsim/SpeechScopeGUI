"""Tabulka nahrávek s upravitelnými sloupci metadat (Analýza krok 1 i průvodce z nahrávek).

Vlevo pevné sloupce jen ke čtení (název, délka, úloha…), vpravo sloupce
metadat, které jdou upravit dvojklikem nebo F2. Úpravy se drží podle cesty
nahrávky (`edits`), platí pro aktuální seznam a přežijí přeskenování téže
složky; hodnota shodná se základem (manifestem) úpravu ruší. Hodnoty se
normalizují jako v manifestu (`meta.normalize_value`: věk číslo, pohlaví
f/m, datum ISO). Upravená buňka je podbarvená s modrou tečkou a původní
hodnotou v tooltipu, prázdná buňka ve sloupci z `required` žlutě s „doplnit“.

Sloupce přidává `add_column` (název se kanonizuje, `vek` → `age`),
`fill_selected` vyplní jednu hodnotu všem vybraným řádkům. Stránka si pak
bere `merged(path)` nebo `rows_merged()` a skládá z nich manifest běhu;
do souboru ve složce se odsud nikdy nezapisuje.

Vzhled podle mockupu https://claude.ai/artifact/RbdMDqP2NUunQRc1RdH1VD (podoba A,
31. 10. 2026): záhlaví velkými písmeny, vzdušné řádky bez mřížky, tučný název.
`set_group_by` seskupí řádky podle sloupce metadat nebo podsložky (`GROUP_FOLDER`):
nad každou skupinou je nadpis přes celou šířku (hodnota, shodné údaje skupiny,
počet a délka), klik na něj označí nahrávky skupiny. S nadpisy už řádek tabulky
není index řádku dat: stránky převádějí přes `data_index`, `table_row`,
`selected_indices`. `set_filter` schová řádky podle hledání nebo jen neúplné.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QComboBox,
    QFrame,
    QHeaderView,
    QInputDialog,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
)

from ...backend import meta
from ...backend.audio import format_duration
from ...i18n import tr
from .. import theme
from .menu import ICON_PROP, Menu


@dataclass(slots=True)
class MetaRow:
    """Jeden řádek: klíč (cesta nahrávky), pevné buňky vlevo a základní metadata."""

    key: Path
    leading: list[str]
    base: dict[str, str] = field(default_factory=dict)
    tooltip: str = ""
    muted: bool = False  # bez řádku v manifestu: metadata šedě
    seconds: float | None = None  # délka nahrávky pro součet ve skupině
    folder: str = ""  # podsložka vůči vybrané složce (seskupení podle podsložky)


NAME_MAX = 360  # nejširší sloupec s názvem nahrávky v px
ROW_HEIGHT = 36
GROUP_FOLDER = "\x00folder"  # seskupení podle podsložky místo sloupce metadat
EDITED_ROLE = Qt.ItemDataRole.UserRole + 11
PLACEHOLDER_ROLE = Qt.ItemDataRole.UserRole + 12
HINT_ROLE = Qt.ItemDataRole.UserRole + 13  # šedá nápověda v prázdné buňce (úloha z kroku 2)
DONE_ROLE = Qt.ItemDataRole.UserRole + 14  # zelená fajfka: řádek seznamu k doplnění hotový
WARN_TEXT = "#92400e"  # tmavě oranžový text na žlutém podkladu (kontrast nad 4.5:1)
PAD = 10  # odsazení textu v buňce (padding-left ze stylu + okraj)
SELECTED_BG = "#eff6ff"  # výběr světlejší než upravená buňka (ACCENT_SOFT)
COLUMN_PROP = "meta_column"  # klíč sloupce u položky seznamu Přidat sloupec
HEADER_BUTTON = 22  # „+“ a ▾ v záhlaví
ADD_STYLE = (
    "QToolButton { color: #9ca3af; background: transparent; border: 1px dashed #d1d5db;"
    " border-radius: 6px; font-size: 13pt; padding: 0px; }"
    f"QToolButton:hover {{ color: {theme.ACCENT}; border: 1px solid {theme.ACCENT};"
    " background: #eff5ff; }"
)
CARET_STYLE = (
    f"QToolButton {{ color: {theme.ACCENT}; background: {theme.CARD};"
    f" border: 1px solid {theme.ACCENT_SOFT}; border-radius: 5px; padding: 0px; }}"
    f"QToolButton:hover {{ border: 1px solid {theme.ACCENT}; background: #eff5ff; }}"
)


class _CellDelegate(QStyledItemDelegate):
    """Podbarvení buňky (styl tabulky barvu položky přebíjí), modrá tečka za upravenou
    hodnotou, „doplnit“ v prázdné povinné buňce."""

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: ANN001
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        background = index.data(Qt.ItemDataRole.BackgroundRole)
        if not selected and isinstance(background, (QBrush, QColor)):
            color = background.color() if isinstance(background, QBrush) else background
            if color.alpha():
                painter.fillRect(rect, color)
        super().paint(painter, option, index)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if self._choices(index):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor("#9ca3af"), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            x = rect.right() - 14
            y = rect.center().y()
            chevron = QPainterPath()
            chevron.moveTo(x - 4, y - 2)
            chevron.lineTo(x, y + 2)
            chevron.lineTo(x + 4, y - 2)
            painter.drawPath(chevron)
            painter.restore()
        if index.data(DONE_ROLE):
            painter.save()
            font = QFont(option.font)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(theme.OK))
            painter.drawText(
                rect.adjusted(0, 0, -8, 0),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                "✓",
            )
            painter.restore()
        if index.data(EDITED_ROLE):
            x = rect.left() + PAD + option.fontMetrics.horizontalAdvance(str(text)) + 8
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawEllipse(QPointF(min(x, rect.right() - 6), rect.center().y() + 0.5), 3, 3)
            painter.restore()
        elif index.data(PLACEHOLDER_ROLE) and not text:
            painter.save()
            painter.setPen(QColor(WARN_TEXT))
            painter.drawText(
                rect.adjusted(PAD, 0, -4, 0),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                str(index.data(PLACEHOLDER_ROLE)),
            )
            painter.restore()
        elif index.data(HINT_ROLE) and not text:
            painter.save()
            font = QFont(option.font)
            font.setItalic(True)
            painter.setFont(font)
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(
                rect.adjusted(PAD, 0, -4, 0),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                str(index.data(HINT_ROLE)),
            )
            painter.restore()

    def _choices(self, index) -> list[tuple[str, str]]:  # noqa: ANN001
        table = self.parent()
        if not isinstance(table, MetaTable):
            return []
        return table.choices_at(index.row(), index.column())

    def createEditor(self, parent, option, index):  # noqa: ANN001, ANN201, N802
        choices = self._choices(index)
        if not choices:
            return super().createEditor(parent, option, index)
        combo = QComboBox(parent)
        # v buňce kompaktně (velký styl pole se do řádku nevešel a ořízl text)
        combo.setStyleSheet(
            "QComboBox { padding: 2px 24px 2px 8px; min-height: 0; border-radius: 6px; }"
        )
        combo.addItem(tr("(nevyplněno)"), "")
        for value, label in choices:
            combo.addItem(label, value)
        # vybraná hodnota se uloží hned, ne až po opuštění buňky
        combo.activated.connect(lambda _i, c=combo: self.commitData.emit(c))
        combo.activated.connect(lambda _i, c=combo: self.closeEditor.emit(c))
        return combo

    def setEditorData(self, editor, index) -> None:  # noqa: ANN001, N802
        if isinstance(editor, QComboBox):
            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            found = editor.findText(text) if text else 0
            editor.setCurrentIndex(max(found, 0))
            # seznam až v dalším kroku smyčky: editor ještě není na obrazovce a seznam
            # otevřený teď se neukázal (31. 10. 2026)
            QTimer.singleShot(0, editor.showPopup)
            return
        super().setEditorData(editor, index)

    def updateEditorGeometry(self, editor, option, index) -> None:  # noqa: ANN001, N802
        if isinstance(editor, QComboBox):
            rect = option.rect.adjusted(2, 3, 0, -3)
            rect.setWidth(max(rect.width(), editor.sizeHint().width()))
            editor.setGeometry(rect)
            return
        super().updateEditorGeometry(editor, option, index)

    def setModelData(self, editor, model, index) -> None:  # noqa: ANN001, N802
        if isinstance(editor, QComboBox):
            label = editor.currentText() if editor.currentData() else ""
            model.setData(index, label, Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)

    def sizeHint(self, option, index):  # noqa: ANN001, ANN201, N802
        hint = super().sizeHint(option, index)
        extra = ""
        if index.data(PLACEHOLDER_ROLE) and not index.data(Qt.ItemDataRole.DisplayRole):
            extra = str(index.data(PLACEHOLDER_ROLE))
        elif index.data(HINT_ROLE) and not index.data(Qt.ItemDataRole.DisplayRole):
            extra = str(index.data(HINT_ROLE))
        elif index.data(EDITED_ROLE):
            extra = str(index.data(Qt.ItemDataRole.DisplayRole)) + "   "
        if extra:
            width = option.fontMetrics.horizontalAdvance(extra) + PAD + 16
            hint.setWidth(max(hint.width(), width))
        if self._choices(index):
            hint.setWidth(hint.width() + 22)  # šipka rozbalení vpravo v buňce
        return hint


class MetaTable(QTableWidget):
    edited = Signal()  # po každé úpravě, přidání sloupce nebo zahození úprav

    def __init__(
        self,
        leading_headers: list[str],
        *,
        labels: bool = False,
        right_columns: tuple[int, ...] = (),
    ) -> None:
        super().__init__()
        self._leading = list(leading_headers)
        self._labels = labels  # hlavičky popisky vyhrazených sloupců, jinak klíče
        self._right = set(right_columns)  # pevné sloupce zarovnané vpravo (délka)
        self._rows: list[MetaRow] = []
        self._index: dict[Path, MetaRow] = {}  # klíč -> řádek (hledání bez procházení)
        self._columns: list[str] = []  # sloupce metadat ze základu
        self._extra: list[str] = []  # sloupce přidané uživatelem
        # sloupce základu (z manifestu), které uživatel odebral (1. 11. 2026); soubor se
        # nemění, sloupec jen není v tabulce ani v `rows_merged`
        self._removed: set[str] = set()
        # dotaz před odebráním (Analýza: úloha mění počet běhů); None = bez dotazu
        self.confirm_remove: Callable[[str], bool] | None = None
        self._edits: dict[Path, dict[str, str]] = {}
        self._required: set[str] = set()
        # sloupce s nabídkou hodnot (úloha): hodnota -> popisek v buňce, záhlaví
        self._choices: dict[str, list[tuple[str, str]]] = {}
        self._choice_headers: dict[str, str] = {}
        self._hints: dict[str, str] = {}  # sloupec -> šedá nápověda v prázdné buňce
        self._only_empty = ""  # filtr: jen řádky s prázdným sloupcem
        # Seznam k doplnění (31. 10. 2026): řádky, které filtr ukázal při zapnutí, zůstanou
        # vidět i po vyplnění (dřív zmizely pod rukou); hotové mají fajfku podle `_done`.
        self._frozen: set[Path] | None = None
        self._done: Callable[[Path], bool] | None = None
        self._group_by = ""
        self._view: list[int | None] = []  # řádek tabulky -> index řádku dat, None = nadpis
        self._groups: dict[int, list[int]] = {}  # řádek nadpisu -> indexy řádků dat
        self._search = ""
        self._only_missing = False
        self._filling = False
        # psaní do vybrané buňky začne úpravu jako v Excelu (1. 11. 2026)
        self.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setHighlightSections(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)  # konec názvu (číslo) zůstane
        self.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        self.setWordWrap(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"QTableWidget {{ background: {theme.CARD}; border: none; }}"
            f"QTableWidget::item {{ border-bottom: 1px solid {theme.NEUTRAL_SOFT};"
            " padding-left: 8px; }"
            f"QTableWidget::item:selected {{ background: {SELECTED_BG}; color: {theme.TEXT}; }}"
            f"QHeaderView::section {{ background: #f9fafb; color: {theme.MUTED};"
            f" border: none; border-bottom: 1px solid {theme.BORDER}; padding: 8px;"
            " font-weight: 600; }"
        )
        self.setItemDelegate(_CellDelegate(self))
        self.itemDelegate().closeEditor.connect(self._editor_closed)
        self.itemChanged.connect(self._item_changed)
        self.cellClicked.connect(self._cell_clicked)
        # pravé tlačítko na záhlaví sloupce metadat: Odebrat sloupec
        header = self.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        # „+“ za posledním sloupcem a ▾ při najetí na záhlaví (2. 11. 2026)
        self.add_menu: Menu | None = None
        self.group_column: Callable[[str], object] | None = None  # Seskupit podle sloupce
        self._last_natural = 0  # šířka posledního sloupce podle obsahu (před roztažením)
        self._caret_column = -1
        self.add_btn = QToolButton(header)
        self.add_btn.setText("+")
        self.add_btn.setToolTip(tr("Přidat sloupec"))
        self.add_btn.setFixedSize(HEADER_BUTTON, HEADER_BUTTON)
        self.add_btn.setStyleSheet(ADD_STYLE)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(lambda: self._add_clicked())
        self.add_btn.hide()
        self.caret_btn = QToolButton(header)
        self.caret_btn.setText("▾")
        self.caret_btn.setToolTip(tr("Nabídka sloupce"))
        self.caret_btn.setFixedSize(HEADER_BUTTON, HEADER_BUTTON)
        self.caret_btn.setStyleSheet(CARET_STYLE)
        self.caret_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.caret_btn.clicked.connect(lambda: self._caret_clicked())
        self.caret_btn.hide()
        self.caret_btn.installEventFilter(self)
        header.setMouseTracking(True)
        header.installEventFilter(self)
        header.sectionResized.connect(lambda *_a: self._place_header_buttons())
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self._place_header_buttons())

    # --- stav -----------------------------------------------------------------------------

    @property
    def meta_from(self) -> int:
        return len(self._leading)

    @property
    def columns(self) -> list[str]:
        """Sloupce metadat, jak jsou v tabulce (základ, pak přidané)."""
        out = [c for c in self._columns if c not in self._removed]
        for column in self._extra:
            if column not in out:
                out.append(column)
        for edits in self._edits.values():
            for column in edits:
                if column not in out:
                    out.append(column)
        return out

    def set_leading_headers(self, headers: list[str]) -> None:
        self._leading = list(headers)

    def set_required(self, columns: set[str]) -> None:
        self._required = set(columns)

    def set_choices(self, column: str, choices: list[tuple[str, str]], header: str = "") -> None:
        """Sloupec s nabídkou (hodnota, popisek): dvojklik otevře rozbalovací seznam,
        buňka ukazuje popisek, uloží se hodnota. Jde přidat i přes `add_column`."""
        self._choices[column] = list(choices)
        if header:
            self._choice_headers[column] = header

    def freeze(self, keys: set[Path] | None) -> None:
        """Zamrazí viditelné řádky na `keys` (seznam k doplnění); None = zase filtr."""
        self._frozen = set(keys) if keys is not None else None
        self.refresh_done()
        self._apply_filter()

    def frozen_keys(self) -> set[Path] | None:
        return set(self._frozen) if self._frozen is not None else None

    def set_done_check(self, check: Callable[[Path], bool] | None) -> None:
        """Kdy je řádek seznamu k doplnění hotový (fajfka u názvu)."""
        self._done = check

    def refresh_done(self) -> None:
        """Fajfky u řádků zamrazeného seznamu podle `_done` (po každé úpravě)."""
        for r, i in enumerate(self._view):
            if i is None:
                continue
            item = self.item(r, 0)
            if item is None:
                continue
            key = self._rows[i].key
            done = (
                self._frozen is not None
                and key in self._frozen
                and self._done is not None
                and self._done(key)
            )
            item.setData(DONE_ROLE, True if done else None)

    def set_hint(self, column: str, text: str) -> None:
        """Šedá nápověda v prázdných buňkách sloupce (třeba „jako Fonace“); bez překreslení."""
        self._hints[column] = text

    def display(self, column: str, value: str) -> str:
        """Text buňky: u sloupce s nabídkou popisek, jinak hodnota."""
        if column in self._choices and value:
            return next((label for v, label in self._choices[column] if v == value), value)
        return value

    def _value_of(self, column: str, text: str) -> str:
        """Opak `display`: z popisku hodnota (u sloupce bez nabídky text)."""
        if column in self._choices and text:
            return next((v for v, label in self._choices[column] if label == text), text)
        return text

    def choices_at(self, table_row: int, table_column: int) -> list[tuple[str, str]]:
        """Nabídka pro buňku tabulky (prázdná = obyčejné textové pole)."""
        c = table_column - self.meta_from
        columns = self.columns
        if self.data_index(table_row) is None or not (0 <= c < len(columns)):
            return []
        return list(self._choices.get(columns[c], []))

    def set_rows(self, rows: list[MetaRow], columns: list[str]) -> None:
        """Nový seznam řádků a sloupce základu; úpravy řádků, které zůstaly, platí dál."""
        self._rows = list(rows)
        self._index = {row.key: row for row in self._rows}
        self._columns = list(columns)
        keys = {row.key for row in rows}
        for gone in [k for k in self._edits if k not in keys]:
            del self._edits[gone]
        self.refill()

    def keys(self) -> list[Path]:
        return [row.key for row in self._rows]

    def row_count(self) -> int:
        """Počet řádků dat (bez nadpisů skupin)."""
        return len(self._rows)

    def edits(self) -> dict[Path, dict[str, str]]:
        return {k: dict(v) for k, v in self._edits.items()}

    def edit_count(self) -> int:
        return sum(len(v) for v in self._edits.values())

    def has_changes(self) -> bool:
        return bool(self._edits or self._extra or self._removed)

    def removed_columns(self) -> list[str]:
        """Sloupce základu odebrané uživatelem (v pořadí základu)."""
        return [c for c in self._columns if c in self._removed]

    def merged(self, key: Path) -> dict[str, str]:
        """Metadata řádku: základ přepsaný úpravami, pro všechny sloupce tabulky."""
        row = self._index.get(key)
        base = dict(row.base) if row else {}
        base.update(self._edits.get(key, {}))
        return {c: base.get(c, "") for c in self.columns}

    def rows_merged(self) -> dict[Path, dict[str, str]]:
        columns = self.columns
        return {row.key: self._row_merged(row, columns) for row in self._rows}

    def _row_merged(self, row: MetaRow, columns: list[str]) -> dict[str, str]:
        """Jako `merged`, ale bez hledání řádku a s předpočítanými sloupci (kreslení)."""
        base = dict(row.base)
        base.update(self._edits.get(row.key, {}))
        return {c: base.get(c, "") for c in columns}

    def missing_columns(self) -> list[str]:
        """Povinné sloupce, které v tabulce jsou a aspoň jednomu řádku chybí."""
        columns = self.columns
        merged = [self._row_merged(row, columns) for row in self._rows]
        return [c for c in columns if c in self._required and any(not m[c] for m in merged)]

    def missing_indices(self) -> list[int]:
        """Řádky dat s prázdnou hodnotou v některém povinném sloupci, který v tabulce je."""
        columns = self.columns
        required = [c for c in columns if c in self._required]
        if not required:
            return []
        return [
            i
            for i, row in enumerate(self._rows)
            if any(not self._row_merged(row, columns)[c] for c in required)
        ]

    # --- řádky tabulky vs. řádky dat ------------------------------------------------------

    def data_index(self, table_row: int) -> int | None:
        """Index řádku dat na řádku tabulky; nadpis skupiny nebo mimo = None."""
        if 0 <= table_row < len(self._view):
            return self._view[table_row]
        return None

    def table_row(self, index: int) -> int:
        """Řádek tabulky s řádkem dat `index`; -1, když tam není."""
        try:
            return self._view.index(index)
        except ValueError:
            return -1

    def selected_indices(self) -> list[int]:
        """Vybrané řádky dat (bez nadpisů), seřazené."""
        rows = {index.row() for index in self.selectionModel().selectedRows()}
        out = {self.data_index(r) for r in rows}
        return sorted(i for i in out if i is not None)

    def select_index(self, index: int) -> None:
        row = self.table_row(index)
        if row >= 0:
            self.selectRow(row)

    def group_indices(self, table_row: int) -> list[int]:
        """Řádky dat skupiny pod nadpisem na `table_row` (jiný řádek = prázdno)."""
        return list(self._groups.get(table_row, []))

    def is_group_header(self, table_row: int) -> bool:
        return table_row in self._groups

    # --- seskupení a filtr ----------------------------------------------------------------

    @property
    def group_by(self) -> str:
        return self._group_by

    def set_group_by(self, column: str) -> None:
        """Seskupit podle sloupce metadat, `GROUP_FOLDER` (podsložka) nebo nijak (prázdno)."""
        if column == self._group_by:
            return
        self._group_by = column
        self.refill()

    def set_filter(self, text: str = "", only_missing: bool = False, only_empty: str = "") -> None:
        """Schová řádky, které neobsahují `text`, nebo (only_missing) jsou úplné,
        nebo (only_empty) mají sloupec `only_empty` vyplněný."""
        self._search = text.strip().casefold()
        self._only_missing = only_missing
        self._only_empty = only_empty
        self._apply_filter()

    def visible_indices(self) -> list[int]:
        return [i for r, i in enumerate(self._view) if i is not None and not self.isRowHidden(r)]

    def _apply_filter(self) -> None:
        missing = set(self.missing_indices()) if self._only_missing else None
        columns = self.columns
        for r, i in enumerate(self._view):
            if i is None:
                continue
            row = self._rows[i]
            if self._frozen is not None:
                show = row.key in self._frozen
            else:
                show = missing is None or i in missing
                if show and self._only_empty and self._only_empty in columns:
                    show = not self._row_merged(row, columns)[self._only_empty]
            if show and self._search:
                merged = self._row_merged(row, columns)
                text = " ".join([*row.leading, *(self.display(c, v) for c, v in merged.items())])
                show = self._search in text.casefold()
            self.setRowHidden(r, not show)
        for r, members in self._groups.items():
            rows = [self.table_row(i) for i in members]
            self.setRowHidden(r, all(self.isRowHidden(x) for x in rows))

    # --- úpravy ---------------------------------------------------------------------------

    def add_column(self, name: str) -> str:
        """Přidá sloupec metadat (kanonický název); vrátí klíč, u nepoužitelného prázdno."""
        column = meta.canonical_column(name.strip()) if name.strip() else ""
        if not column or column == "path" or (column == "task" and column not in self._choices):
            return ""
        if column in self._removed:  # odebraný sloupec základu zpátky
            self._removed.discard(column)
            self.refill()
            self.edited.emit()
        elif column not in self.columns:
            self._extra.append(column)
            self.refill()
            self.edited.emit()
        return column

    def remove_column(self, column: str) -> bool:
        """Odebere sloupec metadat i s hodnotami vyplněnými v tabulce (1. 11. 2026).

        Přidaný sloupec zmizí úplně, sloupec ze základu (manifest) se jen nepoužije;
        vrátí ho Zahodit úpravy nebo `restore_columns`."""
        if column not in self.columns:
            return False
        for edits in self._edits.values():
            edits.pop(column, None)
        self._edits = {k: v for k, v in self._edits.items() if v}
        if column in self._extra:
            self._extra.remove(column)
        if column in self._columns:
            self._removed.add(column)
        if self._group_by == column:
            self._group_by = ""
        self.refill()
        self.edited.emit()
        return True

    def restore_columns(self) -> None:
        """Vrátí odebrané sloupce základu."""
        if self._removed:
            self._removed.clear()
            self.refill()
            self.edited.emit()

    def request_remove(self, column: str) -> bool:
        """Odebrat sloupec z nabídky: napřed dotaz (`confirm_remove`), pak `remove_column`."""
        if self.confirm_remove is not None and not self.confirm_remove(column):
            return False
        return self.remove_column(column)

    # --- sloupce přímo v záhlaví (2. 11. 2026) ----------------------------------------------
    # Mockup https://claude.ai/artifact/3CyBAsnqad5YxoysRdG95K (`docs/mockups/sloupce-v-tabulce/`
    # mimo git): „+“ za posledním sloupcem otevře `add_menu` (seznam přidání sloupce, který
    # skládá stránka), pravé tlačítko nebo ▾ při najetí na záhlaví otevře `header_menu`:
    # u metadat Vyplnit, Seskupit (jen když stránka dá `group_column`) a Odebrat, u pevných
    # sloupců Přidat sloupec › a Odebrat šedě s důvodem.

    def set_add_menu(self, menu: Menu | None) -> None:
        """Seznam pro přidání sloupce (položky s `COLUMN_PROP` = klíč sloupce zešednou,
        když sloupec v tabulce už je); bez něj není „+“ ani Přidat sloupec ›."""
        self.add_menu = menu
        if menu is not None:
            menu.aboutToShow.connect(lambda: self._mark_used(menu))
        self._place_header_buttons()

    def _mark_used(self, menu: Menu) -> None:
        present = set(self.columns)
        for action in menu.actions():
            column = action.property(COLUMN_PROP)
            if column:
                used = column in present
                action.setEnabled(not used)
                action.setToolTip(tr("Sloupec už v tabulce je.") if used else "")

    def fill_targets(self, column: str) -> tuple[list[int], bool]:
        """Komu Vyplnit sloupec: vybraným řádkům (aspoň dvěma), jinak viditelným prázdným;
        vrátí (indexy řádků dat, podle výběru)."""
        selected = self.selected_indices()
        if len(selected) >= 2:
            return selected, True
        empty = [
            i
            for i in self.visible_indices()
            if not self._row_merged(self._rows[i], [column]).get(column, "")
        ]
        return empty, False

    def fill_column(self, column: str, value: str) -> int:
        """Vyplní sloupec podle `fill_targets`; vrátí počet řádků."""
        indices, selected = self.fill_targets(column)
        if not indices:
            return 0
        for i in indices:
            self._store(self._rows[i].key, column, value)
        keys = [self._rows[i].key for i in indices]
        self.refill()
        if selected:
            self._reselect(keys)
        self.edited.emit()
        return len(indices)

    def ask_fill_column(self, column: str) -> int:
        """Vyplnit sloupec…: hodnota z nabídky (úloha) nebo napsaná."""
        indices, selected = self.fill_targets(column)
        if not indices:
            return 0
        name = self._header_text(column)
        prompt = (
            tr("Hodnota pro {n} vybraných řádků, sloupec {column}:")
            if selected
            else tr("Hodnota pro {n} prázdných buněk, sloupec {column}:")
        ).format(n=len(indices), column=name)
        choices = self._choices.get(column)
        if choices:
            labels = [label for _value, label in choices]
            label, ok = QInputDialog.getItem(self, tr("Vyplnit sloupec"), prompt, labels, 0, False)
            value = choices[labels.index(label)][0] if ok else ""
        else:
            value, ok = QInputDialog.getText(self, tr("Vyplnit sloupec"), prompt)
        return self.fill_column(column, value) if ok else 0

    def header_menu(self, logical: int) -> Menu | None:
        """Nabídka sloupce záhlaví `logical` (pravé tlačítko, ▾)."""
        if not 0 <= logical < self.columnCount():
            return None
        menu = Menu(self)
        if logical < self.meta_from:
            name = self._leading[logical] if logical < len(self._leading) else ""
            menu.section(tr("sloupec {name}").format(name=name))
            if self.add_menu is not None:
                self.add_menu.menuAction().setProperty(ICON_PROP, "add")
                menu.addMenu(self.add_menu)
                menu.addSeparator()
            menu.add(
                tr("Odebrat sloupec…"),
                icon="remove",
                danger=True,
                reason=tr("pevné sloupce nahrávky nejdou odebrat"),
            )
            return menu
        column = self.columns[logical - self.meta_from]
        menu.section(tr("sloupec {name}").format(name=self._header_text(column).lower()))
        indices, selected = self.fill_targets(column)
        menu.add(
            (
                tr("Vyplnit vybrané řádky ({n})…")
                if selected
                else tr("Vyplnit prázdné buňky ({n})…")
            ).format(n=len(indices)),
            lambda: self.ask_fill_column(column),
            icon="fill",
            reason="" if indices else tr("sloupec nemá prázdnou buňku, pro přepsání vyberte řádky"),
        )
        if self.group_column is not None:
            grouped = self._group_by == column
            group = self.group_column
            menu.add(
                tr("Zrušit seskupení") if grouped else tr("Seskupit podle sloupce"),
                lambda: group("" if grouped else column),
                icon="group",
            )
        menu.addSeparator()
        menu.add(
            tr("Odebrat sloupec…"),
            lambda: self.request_remove(column),
            icon="remove",
            danger=True,
        )
        return menu

    def _header_menu(self, pos) -> None:  # noqa: ANN001
        menu = self.header_menu(self.horizontalHeader().logicalIndexAt(pos))
        if menu is not None:
            menu.exec(self.horizontalHeader().mapToGlobal(pos))

    def _column_end(self, logical: int) -> int:
        """Pravý okraj sloupce v záhlaví; poslední (roztažený) podle obsahu."""
        header = self.horizontalHeader()
        width = header.sectionSize(logical)
        if logical == self.columnCount() - 1 and self._last_natural:
            width = min(width, self._last_natural)
        return header.sectionViewportPosition(logical) + width

    def _place_header_buttons(self) -> None:
        header = self.horizontalHeader()
        n = self.columnCount()
        top = max(0, (header.height() - HEADER_BUTTON) // 2)
        if self.add_menu is not None and n:
            self.add_btn.move(self._column_end(n - 1) + 4, top)
            self.add_btn.show()
            self.add_btn.raise_()
        else:
            self.add_btn.hide()
        if self._caret_column >= 0 and self._caret_column < n:
            self.caret_btn.move(self._column_end(self._caret_column) - HEADER_BUTTON - 6, top)

    def _show_caret(self, logical: int) -> None:
        if logical < 0 or logical >= self.columnCount():
            self._hide_caret()
            return
        self._caret_column = logical
        self._place_header_buttons()
        self.caret_btn.show()
        self.caret_btn.raise_()

    def _hide_caret(self) -> None:
        self._caret_column = -1
        self.caret_btn.hide()

    def _caret_clicked(self) -> None:
        menu = self.header_menu(self._caret_column)
        if menu is not None:
            menu.exec(self.caret_btn.mapToGlobal(QPoint(0, self.caret_btn.height() + 4)))

    def _add_clicked(self) -> None:
        if self.add_menu is not None:
            self.add_menu.exec(self.add_btn.mapToGlobal(QPoint(0, self.add_btn.height() + 4)))

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802
        if not hasattr(self, "caret_btn"):  # filtr tabulky běží už během konstruktoru
            return super().eventFilter(obj, event)
        header = self.horizontalHeader()
        kind = event.type()
        if obj is header:
            if kind == QEvent.Type.MouseMove:
                self._show_caret(header.logicalIndexAt(event.position().toPoint().x()))
            elif kind == QEvent.Type.Leave:
                inside = self.caret_btn.geometry().contains(header.mapFromGlobal(QCursor.pos()))
                if not inside:
                    self._hide_caret()
            elif kind == QEvent.Type.Resize:
                self._place_header_buttons()
        elif obj is self.caret_btn and kind == QEvent.Type.Leave:
            if not header.rect().contains(header.mapFromGlobal(QCursor.pos())):
                self._hide_caret()
        return super().eventFilter(obj, event)

    def set_value(self, key: Path, column: str, value: str) -> str:
        """Hodnota jednoho řádku (jako dvojklik); vrátí normalizovanou podobu."""
        column = meta.canonical_column(column.strip())
        if column not in self.columns:
            self._extra.append(column)
        stored = self._store(key, column, value)
        self.refill()
        self.edited.emit()
        return stored

    def fill_selected(self, column: str, value: str) -> int:
        """Vyplní hodnotu všem vybraným řádkům; vrátí jejich počet."""
        indices = self.selected_indices()
        if not indices:
            return 0
        column = meta.canonical_column(column.strip())
        if column not in self.columns:
            self._extra.append(column)
        for i in indices:
            self._store(self._rows[i].key, column, value)
        keys = [self._rows[i].key for i in indices]
        self.refill()
        self._reselect(keys)
        self.edited.emit()
        return len(indices)

    def discard(self) -> None:
        self._edits.clear()
        self._extra.clear()
        self._removed.clear()
        self.refill()
        self.edited.emit()

    def reset(self) -> None:
        """Zapomene úpravy i přidané sloupce bez překreslení (jiná složka, nový základ)."""
        self._edits.clear()
        self._extra.clear()
        self._removed.clear()

    def _select_table_rows(self, rows: list[int]) -> None:
        model = self.selectionModel()
        self.clearSelection()
        for r in rows:
            model.select(
                self.model().index(r, 0),
                model.SelectionFlag.Select | model.SelectionFlag.Rows,
            )

    def _reselect(self, keys: list[Path]) -> None:
        """Po překreslení znovu označí řádky (seskupení je mohlo přeházet)."""
        wanted = set(keys)
        rows = [self.table_row(i) for i, row in enumerate(self._rows) if row.key in wanted]
        self._select_table_rows([r for r in rows if r >= 0])

    def _store(self, key: Path, column: str, value: str) -> str:
        normalized = meta.normalize_value(column, value)
        row = self._index.get(key)
        original = (row.base if row else {}).get(column, "")
        edits = self._edits.setdefault(key, {})
        if normalized == original:
            edits.pop(column, None)
            if not edits:
                del self._edits[key]
        else:
            edits[column] = normalized
        return normalized

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() < self.meta_from:
            return
        columns = self.columns
        i, c = self.data_index(item.row()), item.column() - self.meta_from
        if i is None or not (0 <= c < len(columns)):
            return
        row, column = self._rows[i], columns[c]
        stored = self._store(row.key, column, self._value_of(column, item.text()))
        if self._group_by and (column == self._group_by or self._groups):
            # změna mění skupinu nebo její nadpis (shodné údaje): překreslit celé
            self.refill()
            self._reselect([row.key])
        else:
            # jen tahle buňka: popisek místo hodnoty (úloha), podbarvení, nápověda; celá
            # přestavba tabulky kvůli jedné buňce sekala (31. 10. 2026)
            self._filling = True
            try:
                item.setText(self.display(column, stored))
                self._mark(item, row, column)
            finally:
                self._filling = False
            if item.column() > 0:
                self.resizeColumnToContents(item.column())
        self.edited.emit()

    def _cell_clicked(self, table_row: int, _column: int) -> None:
        members = self._groups.get(table_row)
        if not members:
            return
        rows = [self.table_row(i) for i in members]
        self._select_table_rows([r for r in rows if r >= 0 and not self.isRowHidden(r)])

    # --- kreslení -------------------------------------------------------------------------

    def refill(self) -> None:
        self._filling = True
        self.setUpdatesEnabled(False)
        try:
            self._refill_now()
        finally:
            self.setUpdatesEnabled(True)
            self._filling = False

    # --- výběr a pohyb (1. 11. 2026) ----------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        """Esc zruší výběr (mimo úpravu buňky, tu zavře editor sám)."""
        if event.key() == Qt.Key.Key_Escape and self.state() != QTableWidget.State.EditingState:
            self.clearSelection()
            self.setCurrentCell(-1, -1)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        """Klik do prázdného místa pod řádky zruší výběr."""
        if not self.indexAt(event.position().toPoint()).isValid():
            self.clearSelection()
            self.setCurrentCell(-1, -1)
            return
        super().mousePressEvent(event)

    def _editor_closed(self, _editor, hint) -> None:  # noqa: ANN001
        """Enter po úpravě: kurzor na buňku pod ní ve stejném sloupci (vyplňování shora dolů).

        Napojené na signál delegáta: přepsání slotu `closeEditor` Qt z Pythonu nevolá."""
        if hint != QAbstractItemDelegate.EndEditHint.SubmitModelCache:
            return
        row, column = self.currentRow(), self.currentColumn()
        if row >= 0:
            QTimer.singleShot(0, lambda: self.move_down(row, column))

    def move_down(self, row: int, column: int) -> int:
        """Další viditelný řádek dat pod `row` (nadpisy skupin a skryté přeskočí); -1 = konec."""
        for r in range(row + 1, self.rowCount()):
            if self.isRowHidden(r) or self.is_group_header(r):
                continue
            self.setCurrentCell(r, column)
            return r
        return -1

    def display_header(self, column: str) -> str:
        """Popisek sloupce jako v záhlaví (pro nabídky)."""
        return self._header_text(column)

    def _header_text(self, column: str) -> str:
        if column in self._choice_headers:
            return self._choice_headers[column]
        return meta.label(column) if self._labels and column in meta.META_COLUMNS else column

    def _build_view(self, columns: list[str]) -> list[tuple[str, list[int]] | int]:
        """Pořadí řádků: bez seskupení indexy, jinak (hodnota, indexy) po skupinách."""
        if not self._group_by or (self._group_by != GROUP_FOLDER and self._group_by not in columns):
            return list(range(len(self._rows)))
        groups: dict[str, list[int]] = {}
        for i, row in enumerate(self._rows):
            if self._group_by == GROUP_FOLDER:
                value = row.folder
            else:
                value = self._row_merged(row, columns)[self._group_by]
            groups.setdefault(value, []).append(i)
        order = sorted(groups, key=lambda v: (v == "", v.casefold()))
        return [(value, groups[value]) for value in order]

    def _group_title(self, value: str, members: list[int], columns: list[str]) -> str:
        if self._group_by == GROUP_FOLDER:
            name = value or tr("(hlavní složka)")
        else:
            name = self.display(self._group_by, value) or tr("bez hodnoty")
        parts = [name]
        shared: list[str] = []
        merged = [self._row_merged(self._rows[i], columns) for i in members]
        for column in columns:
            if column == self._group_by:
                continue
            values = {m[column] for m in merged}
            if len(values) == 1 and (only := values.pop()):
                if column == meta.SEX:
                    only = {"f": tr("žena"), "m": tr("muž")}.get(only, only)
                only = self.display(column, only)
                shared.append(only)
        if shared:
            parts.append(" · ".join(shared))
        count = tr("{n} nahrávek").format(n=len(members))
        seconds = [self._rows[i].seconds for i in members]
        if seconds and all(s is not None for s in seconds):
            count += " · " + format_duration(sum(s for s in seconds if s is not None))
        parts.append(count)
        return "     ".join(parts)

    def _refill_now(self) -> None:
        columns = self.columns
        headers = [h.upper() for h in self._leading] + [
            self._header_text(c).upper() for c in columns
        ]
        # Šířky se během plnění nepřepočítávají (31. 10. 2026): s ResizeToContents Qt po každé
        # vložené buňce přeměřil celý sloupec, u 260 nahrávek 1,35 milionu měření a 10 s na
        # jednu změnu úlohy. Měří se jednou na konci (`_fit_columns`).
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.clearSpans()
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        for c, column in enumerate(columns):
            head = self.horizontalHeaderItem(self.meta_from + c)
            if head is not None:
                head.setToolTip(
                    f"{meta.label(column)} ({column})" if column in meta.META_COLUMNS else column
                )
        view: list[int | None] = []
        groups: dict[int, list[int]] = {}
        titles: dict[int, str] = {}
        for entry in self._build_view(columns):
            if isinstance(entry, int):
                view.append(entry)
                continue
            value, members = entry
            groups[len(view)] = members
            titles[len(view)] = self._group_title(value, members, columns)
            view.append(None)
            view.extend(members)
        self._view, self._groups = view, groups
        self.setRowCount(len(view))
        bold = QFont(self.font())
        bold.setBold(True)
        bold_metrics = QFontMetrics(bold)
        name_width = 0
        for r, i in enumerate(view):
            if i is None:
                item = QTableWidgetItem(titles[r])
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setFont(bold)
                item.setBackground(QColor(theme.NEUTRAL_SOFT))
                item.setToolTip(tr("Klik označí nahrávky skupiny (pak třeba Vyplnit vybrané…)."))
                self.setItem(r, 0, item)
                for c in range(1, len(headers)):
                    filler = QTableWidgetItem("")
                    filler.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self.setItem(r, c, filler)
                if len(headers) > 1:
                    self.setSpan(r, 0, 1, len(headers))
                continue
            row = self._rows[i]
            merged = self._row_merged(row, columns)
            values = list(row.leading) + [self.display(c, merged[c]) for c in columns]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if row.tooltip:
                    item.setToolTip(row.tooltip)
                if c >= self.meta_from:
                    if row.muted and not self._edits.get(row.key):
                        item.setForeground(QColor(theme.MUTED))
                    self._mark(item, row, columns[c - self.meta_from])
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if c == 0:
                        item.setFont(bold)
                        name_width = max(name_width, bold_metrics.horizontalAdvance(value))
                    elif c in self._right:
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                        item.setForeground(QColor(theme.MUTED))
                self.setItem(r, c, item)
        # sloupce podle obsahu, volné místo až za posledním (31. 10. 2026): roztažený název
        # odsouval délku a metadata daleko doprava; dlouhý název se zkrátí výpustkou.
        # Šířku názvu měří tučné písmo, nadpisy skupin (přes celou šířku) se nepočítají.
        header = self.horizontalHeader()
        # vedle textu záhlaví místo pro ▾ (při najetí), aby šipka text nepřekryla
        room = HEADER_BUTTON + 10
        for c in range(1, len(headers)):
            self.resizeColumnToContents(c)  # jedno měření sloupce (hlavička i buňky)
            needed = bold_metrics.horizontalAdvance(headers[c]) + 18 + room
            if self.columnWidth(c) < needed:
                self.setColumnWidth(c, needed)
        if headers:
            head_width = bold_metrics.horizontalAdvance(headers[0]) + 24 + room
            self.setColumnWidth(0, min(NAME_MAX, max(head_width, name_width + 28)))
            self._last_natural = self.columnWidth(len(headers) - 1)
        header.setStretchLastSection(True)
        self._place_header_buttons()
        self.refresh_done()
        self._apply_filter()

    def _mark(self, item: QTableWidgetItem, row: MetaRow, column: str) -> None:
        edits = self._edits.get(row.key, {})
        item.setData(EDITED_ROLE, None)
        item.setData(PLACEHOLDER_ROLE, None)
        item.setData(HINT_ROLE, self._hints.get(column) or None)
        if column in edits:
            item.setBackground(QColor(theme.ACCENT_SOFT))
            item.setData(EDITED_ROLE, True)
            item.setToolTip(
                tr("Upraveno pro tento běh; v manifestu: {value}").format(
                    value=row.base.get(column, "") or "–"
                )
            )
        elif column in self._required and not item.text():
            item.setBackground(QColor(theme.WARN_SOFT))
            item.setData(PLACEHOLDER_ROLE, tr("doplnit"))
            item.setToolTip(tr("chybí v manifestu, dopište dvojklikem"))
        else:
            item.setBackground(QColor(Qt.GlobalColor.transparent))
            if column in meta.META_COLUMNS or column in self._choices:
                item.setToolTip(tr("Dvojklik upraví hodnotu, jen pro tento běh."))


__all__ = ["GROUP_FOLDER", "MetaRow", "MetaTable"]
