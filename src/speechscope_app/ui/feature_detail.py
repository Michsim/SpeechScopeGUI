"""Okno se sloupci jedné feature: krátké jméno, jednotka, popis. Jen ke čtení.

Dlaždice v editoru má výstupy slepené do odstavce; tady je totéž jako
tabulka. Jednotka se bere ze závorky na konci popisu, jak je knihovna píše
(„… (půltóny)“, „… (Hz)“), jinak zůstane prázdná. Texty jdou z `list --json`
v jazyce aplikace.
"""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend.library import FeatureInfo
from ..i18n import tr

_UNIT = re.compile(r"^(?P<text>.*?)\s*\((?P<unit>[^()]{1,24})\)\s*$")


def split_unit(description: str) -> tuple[str, str]:
    """„Medián kontury F0 (půltóny)“ → („Medián kontury F0“, „půltóny“)."""
    m = _UNIT.match(description or "")
    if not m:
        return description or "", ""
    return m.group("text").strip(), m.group("unit").strip()


def requires_text(requires: list[str]) -> str:
    if not requires:
        return tr("bez modelů")
    return tr("potřebuje: ") + ", ".join(contract.PROVIDER_SHORT.get(r, r) for r in requires)


class FeatureDetailDialog(QDialog):
    def __init__(self, info: FeatureInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        short = info.name.rsplit(".", 1)[-1]
        self.setWindowTitle(tr("Sloupce feature {name}").format(name=short))
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel(
            tr("{group} · {name}, verze {version}").format(
                group=contract.GROUP_LABELS.get(info.group, info.group),
                name=short,
                version=info.version,
            )
        )
        title.setObjectName("headline")
        layout.addWidget(title)
        if info.description:
            desc = QLabel(info.description)
            desc.setObjectName("muted")
            desc.setWordWrap(True)
            layout.addWidget(desc)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat sloupec"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([tr("sloupec"), tr("jednotka"), tr("popis")])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self._fill(info)

        self.footer = QLabel(
            tr("{n} sloupců").format(n=self.table.rowCount()) + " · " + requires_text(info.requires)
        )
        self.footer.setObjectName("muted")
        layout.addWidget(self.footer)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _fill(self, info: FeatureInfo) -> None:
        shorts = [c.removeprefix(info.name + ".") for c in info.columns] or list(info.outputs)
        if not shorts:
            shorts = [info.name.rsplit(".", 1)[-1]]
        self.table.setRowCount(len(shorts))
        for row, short in enumerate(shorts):
            text, unit = split_unit(info.outputs.get(short, ""))
            if short == info.name and not text:  # lingvistika: jediný sloupec = feature
                text, unit = split_unit(info.description)
                short = info.name.rsplit(".", 1)[-1]
            for col, value in enumerate((short, unit, text)):
                item = QTableWidgetItem(value)
                item.setToolTip(f"{info.name}.{short}" if col == 0 else value)
                self.table.setItem(row, col, item)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            hay = " ".join(
                self.table.item(row, c).text().lower()
                for c in range(3)
                if self.table.item(row, c) is not None
            )
            self.table.setRowHidden(row, bool(needle) and needle not in hay)
