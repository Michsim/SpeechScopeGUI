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

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
)

from ...backend import meta
from ...backend.audio import format_duration
from ...i18n import tr
from .. import theme


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
WARN_TEXT = "#92400e"  # tmavě oranžový text na žlutém podkladu (kontrast nad 4.5:1)
PAD = 10  # odsazení textu v buňce (padding-left ze stylu + okraj)
SELECTED_BG = "#eff6ff"  # výběr světlejší než upravená buňka (ACCENT_SOFT)


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

    def sizeHint(self, option, index):  # noqa: ANN001, ANN201, N802
        hint = super().sizeHint(option, index)
        extra = ""
        if index.data(PLACEHOLDER_ROLE) and not index.data(Qt.ItemDataRole.DisplayRole):
            extra = str(index.data(PLACEHOLDER_ROLE))
        elif index.data(EDITED_ROLE):
            extra = str(index.data(Qt.ItemDataRole.DisplayRole)) + "   "
        if extra:
            width = option.fontMetrics.horizontalAdvance(extra) + PAD + 16
            hint.setWidth(max(hint.width(), width))
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
        self._columns: list[str] = []  # sloupce metadat ze základu
        self._extra: list[str] = []  # sloupce přidané uživatelem
        self._edits: dict[Path, dict[str, str]] = {}
        self._required: set[str] = set()
        self._group_by = ""
        self._view: list[int | None] = []  # řádek tabulky -> index řádku dat, None = nadpis
        self._groups: dict[int, list[int]] = {}  # řádek nadpisu -> indexy řádků dat
        self._search = ""
        self._only_missing = False
        self._filling = False
        self.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed
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
        self.itemChanged.connect(self._item_changed)
        self.cellClicked.connect(self._cell_clicked)

    # --- stav -----------------------------------------------------------------------------

    @property
    def meta_from(self) -> int:
        return len(self._leading)

    @property
    def columns(self) -> list[str]:
        """Sloupce metadat, jak jsou v tabulce (základ, pak přidané)."""
        out = list(self._columns)
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

    def set_rows(self, rows: list[MetaRow], columns: list[str]) -> None:
        """Nový seznam řádků a sloupce základu; úpravy řádků, které zůstaly, platí dál."""
        self._rows = list(rows)
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
        return bool(self._edits or self._extra)

    def merged(self, key: Path) -> dict[str, str]:
        """Metadata řádku: základ přepsaný úpravami, pro všechny sloupce tabulky."""
        row = next((r for r in self._rows if r.key == key), None)
        base = dict(row.base) if row else {}
        base.update(self._edits.get(key, {}))
        return {c: base.get(c, "") for c in self.columns}

    def rows_merged(self) -> dict[Path, dict[str, str]]:
        return {row.key: self.merged(row.key) for row in self._rows}

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

    def set_filter(self, text: str = "", only_missing: bool = False) -> None:
        """Schová řádky, které neobsahují `text`, nebo (only_missing) jsou úplné."""
        self._search = text.strip().casefold()
        self._only_missing = only_missing
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
            show = missing is None or i in missing
            if show and self._search:
                merged = self._row_merged(row, columns)
                text = " ".join([*row.leading, *merged.values()])
                show = self._search in text.casefold()
            self.setRowHidden(r, not show)
        for r, members in self._groups.items():
            rows = [self.table_row(i) for i in members]
            self.setRowHidden(r, all(self.isRowHidden(x) for x in rows))

    # --- úpravy ---------------------------------------------------------------------------

    def add_column(self, name: str) -> str:
        """Přidá sloupec metadat (kanonický název); vrátí klíč, u nepoužitelného prázdno."""
        column = meta.canonical_column(name.strip()) if name.strip() else ""
        if not column or column in ("path", "task"):
            return ""
        if column not in self.columns:
            self._extra.append(column)
            self.refill()
            self.edited.emit()
        return column

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
        self.refill()
        self.edited.emit()

    def reset(self) -> None:
        """Zapomene úpravy i přidané sloupce bez překreslení (jiná složka, nový základ)."""
        self._edits.clear()
        self._extra.clear()

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
        row = next((r for r in self._rows if r.key == key), None)
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
        stored = self._store(row.key, column, item.text())
        if self._group_by and (column == self._group_by or self._groups):
            # změna mění skupinu nebo její nadpis (shodné údaje): překreslit celé
            self.refill()
            self._reselect([row.key])
        else:
            self._filling = True
            try:
                item.setText(stored)
                self._mark(item, row, column)
            finally:
                self._filling = False
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
        try:
            self._refill_now()
        finally:
            self._filling = False

    def _header_text(self, column: str) -> str:
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
            name = value or tr("bez hodnoty")
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
            values = list(row.leading) + [merged[c] for c in columns]
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
        for c in range(len(headers)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        if headers:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            head_width = bold_metrics.horizontalAdvance(headers[0]) + 24
            self.setColumnWidth(0, min(NAME_MAX, max(head_width, name_width + 28)))
        header.setStretchLastSection(True)
        self._apply_filter()

    def _mark(self, item: QTableWidgetItem, row: MetaRow, column: str) -> None:
        edits = self._edits.get(row.key, {})
        item.setData(EDITED_ROLE, None)
        item.setData(PLACEHOLDER_ROLE, None)
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
            if column in meta.META_COLUMNS:
                item.setToolTip(tr("Dvojklik upraví hodnotu, jen pro tento běh."))


__all__ = ["GROUP_FOLDER", "MetaRow", "MetaTable"]
