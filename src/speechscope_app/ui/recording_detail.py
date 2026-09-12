"""Detail jedné nahrávky: hodnoty po skupinách feature s popisem sloupců.

Tabulka se 117 sloupci se nedá číst; tady je jeden řádek jako strom
skupina → feature → hodnota, s popisem z `list --json` (`outputs`).
Sloupce, které katalog nezná (metadata z manifestu), jdou do „Ostatní“.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend.library import FeatureInfo
from ..i18n import tr
from . import theme

HEADER_COLUMNS = ("file", "path", "task", "speechscope_version", "notes", "error")


@dataclass(slots=True)
class ColumnInfo:
    feature: FeatureInfo
    short: str
    description: str


def column_index(catalog: list[FeatureInfo]) -> dict[str, ColumnInfo]:
    """Sloupec tabulky → feature, krátké jméno a popis z katalogu."""
    index: dict[str, ColumnInfo] = {}
    for feature in catalog:
        for column in feature.columns:
            short = column.removeprefix(feature.name + ".")
            index[column] = ColumnInfo(feature, short, feature.outputs.get(short, ""))
    return index


def column_descriptions(catalog: list[FeatureInfo]) -> dict[str, str]:
    """Tooltip hlavičky: „feature: popis“ pro každý známý sloupec."""
    out: dict[str, str] = {}
    for column, info in column_index(catalog).items():
        out[column] = f"{info.feature.name}: {info.description}" if info.description else column
    return out


def filter_tree(tree: QTreeWidget, text: str) -> None:
    """Skryje větve stromu skupina → feature → sloupec, které neodpovídají hledání."""
    needle = text.strip().lower()
    for g in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(g)
        group_hit = needle in group.text(0).lower()
        any_child = False
        for f in range(group.childCount()):
            feature = group.child(f)
            feature_hit = group_hit or any(
                needle in feature.text(i).lower() for i in range(feature.columnCount())
            )
            shown = 0
            for c in range(feature.childCount()):
                col = feature.child(c)
                hit = (
                    not needle
                    or feature_hit
                    or any(needle in col.text(i).lower() for i in range(col.columnCount()))
                )
                col.setHidden(not hit)
                shown += hit
            feature.setHidden(not (shown or feature_hit))
            any_child |= bool(shown or feature_hit)
        group.setHidden(not (any_child or group_hit))


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return "—"
        return f"{value:.4g}"
    text = str(value)
    return "—" if text in ("nan", "") else text


class RecordingDetailDialog(QDialog):
    def __init__(
        self, row: dict[str, Any], catalog: list[FeatureInfo], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        name = str(row.get("file") or "")
        self.setWindowTitle(tr("Nahrávka {name}").format(name=name))
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel(name)
        title.setObjectName("headline")
        layout.addWidget(title)
        head_lines = []
        if row.get("path"):
            head_lines.append(str(row["path"]))
        if row.get("task"):
            task = str(row["task"])
            head_lines.append(tr("Úloha: {task}").format(task=contract.TASK_LABELS.get(task, task)))
        head = QLabel("\n".join(head_lines))
        head.setObjectName("muted")
        head.setWordWrap(True)
        layout.addWidget(head)
        for key, role in (("error", "missing"), ("notes", "warn")):
            text = row.get(key)
            if text is None or (isinstance(text, float) and math.isnan(text)) or text == "":
                continue
            pill = QLabel((tr("Chyba: ") if key == "error" else tr("Poznámka: ")) + str(text))
            pill.setObjectName("pill")
            pill.setWordWrap(True)
            theme.set_role(pill, role)
            layout.addWidget(pill)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat feature nebo sloupec"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([tr("feature / sloupec"), tr("hodnota"), tr("popis")])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        layout.addWidget(self.tree, 1)
        self._fill(row, catalog)
        self.tree.expandAll()
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # --- strom -----------------------------------------------------------------------

    def _fill(self, row: dict[str, Any], catalog: list[FeatureInfo]) -> None:
        index = column_index(catalog)
        groups: dict[str, QTreeWidgetItem] = {}
        features: dict[str, QTreeWidgetItem] = {}
        other: QTreeWidgetItem | None = None
        for column, raw in row.items():
            if column in HEADER_COLUMNS:
                continue
            info = index.get(column)
            if info is None:
                if other is None:
                    other = QTreeWidgetItem([tr("Ostatní (metadata, neznámé sloupce)"), "", ""])
                    other.setFirstColumnSpanned(False)
                self._add_value(other, column, raw, "")
                continue
            group = info.feature.group
            if group not in groups:
                label = contract.GROUP_LABELS.get(group, group)
                groups[group] = QTreeWidgetItem([label, "", group])
                groups[group].setForeground(2, QColor(theme.MUTED))
                self.tree.addTopLevelItem(groups[group])
            name = info.feature.name
            if info.feature.columns == [name]:
                # jediný sloupec = feature sama (lingvistika): hodnota rovnou u feature
                self._add_value(
                    groups[group],
                    name.removeprefix(group + "."),
                    raw,
                    info.feature.description or "",
                )
                continue
            if name not in features:
                features[name] = QTreeWidgetItem(
                    [name.removeprefix(group + "."), "", info.feature.description or ""]
                )
                features[name].setToolTip(0, name)
                groups[group].addChild(features[name])
            self._add_value(features[name], info.short, raw, info.description)
        if other is not None:
            self.tree.addTopLevelItem(other)

    def _add_value(self, parent: QTreeWidgetItem, name: str, raw: Any, desc: str) -> None:
        text = format_value(raw)
        item = QTreeWidgetItem([name, text, desc])
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if text == "—":
            item.setForeground(1, QColor(theme.MUTED))
        item.setToolTip(2, desc)
        parent.addChild(item)

    def _filter(self, text: str) -> None:
        filter_tree(self.tree, text)
