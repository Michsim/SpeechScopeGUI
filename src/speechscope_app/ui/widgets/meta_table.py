"""Tabulka nahrávek s upravitelnými sloupci metadat (Analýza krok 1 i průvodce z nahrávek).

Vlevo pevné sloupce jen ke čtení (název, délka, úloha…), vpravo sloupce
metadat, které jdou upravit dvojklikem nebo F2. Úpravy se drží podle cesty
nahrávky (`edits`), platí pro aktuální seznam a přežijí přeskenování téže
složky; hodnota shodná se základem (manifestem) úpravu ruší. Hodnoty se
normalizují jako v manifestu (`meta.normalize_value`: věk číslo, pohlaví
f/m, datum ISO). Upravená buňka je podbarvená s původní hodnotou v tooltipu,
prázdná buňka ve sloupci z `required` žlutě.

Sloupce přidává `add_column` (název se kanonizuje, `vek` → `age`),
`fill_selected` vyplní jednu hodnotu všem vybraným řádkům. Stránka si pak
bere `merged(path)` nebo `rows_merged()` a skládá z nich manifest běhu;
do souboru ve složce se odsud nikdy nezapisuje.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem

from ...backend import meta
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


class MetaTable(QTableWidget):
    edited = Signal()  # po každé úpravě, přidání sloupce nebo zahození úprav

    def __init__(self, leading_headers: list[str], *, labels: bool = False) -> None:
        super().__init__()
        self._leading = list(leading_headers)
        self._labels = labels  # hlavičky česky (průvodce), jinak klíče sloupců (Analýza)
        self._rows: list[MetaRow] = []
        self._columns: list[str] = []  # sloupce metadat ze základu
        self._extra: list[str] = []  # sloupce přidané uživatelem
        self._edits: dict[Path, dict[str, str]] = {}
        self._required: set[str] = set()
        self._filling = False
        self.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed
        )
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.itemChanged.connect(self._item_changed)

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
        rows = sorted({index.row() for index in self.selectedIndexes()})
        if not rows:
            return 0
        column = meta.canonical_column(column.strip())
        if column not in self.columns:
            self._extra.append(column)
        for r in rows:
            if 0 <= r < len(self._rows):
                self._store(self._rows[r].key, column, value)
        self.refill()
        self.edited.emit()
        return len(rows)

    def discard(self) -> None:
        self._edits.clear()
        self._extra.clear()
        self.refill()
        self.edited.emit()

    def reset(self) -> None:
        """Zapomene úpravy i přidané sloupce bez překreslení (jiná složka, nový základ)."""
        self._edits.clear()
        self._extra.clear()

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
        r, c = item.row(), item.column() - self.meta_from
        if not (0 <= r < len(self._rows)) or not (0 <= c < len(columns)):
            return
        row, column = self._rows[r], columns[c]
        stored = self._store(row.key, column, item.text())
        self._filling = True
        try:
            item.setText(stored)
            self._mark(item, row, column)
        finally:
            self._filling = False
        self.edited.emit()

    # --- kreslení -------------------------------------------------------------------------

    def refill(self) -> None:
        self._filling = True
        try:
            self._refill_now()
        finally:
            self._filling = False

    def _refill_now(self) -> None:
        columns = self.columns
        headers = list(self._leading) + [
            meta.label(c) if self._labels and c in meta.META_COLUMNS else c for c in columns
        ]
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        for c, column in enumerate(columns):
            head = self.horizontalHeaderItem(self.meta_from + c)
            if head is not None:
                head.setToolTip(
                    f"{meta.label(column)} ({column})" if column in meta.META_COLUMNS else column
                )
        self.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            values = list(row.leading) + [self.merged(row.key).get(c, "") for c in columns]
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
                self.setItem(r, c, item)
        header = self.horizontalHeader()
        if headers:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(headers)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)

    def _mark(self, item: QTableWidgetItem, row: MetaRow, column: str) -> None:
        edits = self._edits.get(row.key, {})
        if column in edits:
            item.setBackground(QColor(theme.ACCENT_SOFT))
            item.setToolTip(
                tr("Upraveno pro tento běh; v manifestu: {value}").format(
                    value=row.base.get(column, "") or "–"
                )
            )
        elif column in self._required and not item.text():
            item.setBackground(QColor(theme.WARN_SOFT))
            item.setToolTip(tr("chybí v manifestu, dopište dvojklikem"))
        else:
            item.setBackground(QColor(Qt.GlobalColor.transparent))
            if column in meta.META_COLUMNS:
                item.setToolTip(tr("Dvojklik upraví hodnotu, jen pro tento běh."))


__all__ = ["MetaRow", "MetaTable"]
