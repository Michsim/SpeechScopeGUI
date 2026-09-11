"""Výběr feature: plochá tabulka se skupinami místo stromu.

Skupina je doména a druhá část jména (`acoustic.timing.*` → „Akustika ·
časování“), řádek skupiny má tristate zaškrtávátko a počet vybraných.
U každé feature je počet sloupců, které dává, a co potřebuje (providery).
Vyhledávání filtruje řádky, rychlé volby vybírají podle `requires`.

Widget nezná knihovnu ani protokol; dostane seznam `FeatureInfo` a výběr,
vrací vybraná jména. Kdo ho používá, mu pod tabulku posílá souhrn
(`set_summary`) a bere z něj `current_changed` pro panel parametrů.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.library import FeatureInfo
from .. import theme

ROLE_NAME = Qt.ItemDataRole.UserRole  # jméno feature; u skupiny prázdné
ROLE_GROUP = Qt.ItemDataRole.UserRole + 1  # klíč skupiny (`acoustic.timing`)

COL_NAME, COL_COLUMNS, COL_REQUIRES = range(3)


def group_label(group: str) -> str:
    """`acoustic.timing` → „Akustika · časování“; neznámé skupiny surově."""
    if group in contract.GROUP_LABELS:
        return contract.GROUP_LABELS[group]
    domain, _, rest = group.partition(".")
    return f"{contract.DOMAIN_LABELS.get(domain, domain)} · {rest or '?'}"


def requires_label(requires: list[str]) -> str:
    return ", ".join(contract.PROVIDER_SHORT.get(r, r) for r in requires)


class FeaturePicker(QWidget):
    selection_changed = Signal()
    current_changed = Signal(str)  # jméno feature nebo providera, "" = nic

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._features: list[FeatureInfo] = []
        self._rows: dict[str, int] = {}  # jméno feature -> řádek
        self._group_rows: dict[str, int] = {}
        self._updating = False
        self._overridden: set[str] = set()  # feature a providery se změněnými parametry
        self._summary_args: tuple[list[str], int, str] = ([], 0, "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Hledat feature nebo sloupec…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)
        self.quick: dict[str, QPushButton] = {}
        for key, label, tip in (
            ("all", "Vše", "Vybrat všechny feature pro úlohu"),
            ("none", "Nic", "Zrušit výběr"),
            ("nomodels", "Jen bez modelů", "Jen feature, které nepotřebují žádný provider"),
            (
                "notranscript",
                "Bez přepisu",
                "Vynechat feature, které potřebují Whisper nebo Stanzu",
            ),
        ):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, k=key: self.quick_pick(k))
            top.addWidget(btn)
            self.quick[key] = btn
        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["feature", "sloupců", "potřebuje"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_COLUMNS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_REQUIRES, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._item_changed)
        self.table.currentCellChanged.connect(self._current_cell_changed)
        layout.addWidget(self.table, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.linkActivated.connect(self._link_activated)
        layout.addWidget(self.summary)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color: {theme.WARN};")
        self.warning.setVisible(False)
        layout.addWidget(self.warning)

    # --- naplnění ---------------------------------------------------------------

    def set_features(self, features: list[FeatureInfo], selected: set[str]) -> None:
        self._features = list(features)
        self._updating = True
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._rows.clear()
        self._group_rows.clear()
        bold = QFont()
        bold.setBold(True)
        groups: dict[str, list[FeatureInfo]] = {}
        for f in sorted(features, key=lambda f: f.name):
            groups.setdefault(f.group, []).append(f)
        for group, members in groups.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            head = QTableWidgetItem(group_label(group))
            head.setFont(bold)
            head.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            head.setData(ROLE_NAME, "")
            head.setData(ROLE_GROUP, group)
            head.setBackground(QColor(theme.NEUTRAL_SOFT))
            self.table.setItem(row, COL_NAME, head)
            for col in (COL_COLUMNS, COL_REQUIRES):
                cell = QTableWidgetItem("")
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
                cell.setBackground(QColor(theme.NEUTRAL_SOFT))
                cell.setForeground(QColor(theme.MUTED))
                self.table.setItem(row, col, cell)
            self._group_rows[group] = row
            for f in members:
                row = self.table.rowCount()
                self.table.insertRow(row)
                short = f.name[len(group) + 1 :] if f.name.startswith(group + ".") else f.name
                item = QTableWidgetItem("    " + short)
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(
                    Qt.CheckState.Checked if f.name in selected else Qt.CheckState.Unchecked
                )
                item.setData(ROLE_NAME, f.name)
                item.setData(ROLE_GROUP, group)
                outputs = "\n".join(f"{k}: {v}" for k, v in f.outputs.items())
                item.setToolTip(f"{f.name}\n{f.description or ''}\n{outputs}".strip())
                self.table.setItem(row, COL_NAME, item)
                count = QTableWidgetItem(str(len(f.columns) or len(f.outputs) or 1))
                count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                count.setToolTip(outputs)
                count.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, COL_COLUMNS, count)
                req = QTableWidgetItem(requires_label(f.requires))
                req.setForeground(QColor(theme.MUTED if f.requires else theme.OK))
                req.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, COL_REQUIRES, req)
                self._rows[f.name] = row
        self.table.blockSignals(False)
        self._updating = False
        self._refresh_groups()
        self._apply_filter(self.search.text())

    def count(self) -> int:
        return len(self._rows)

    def set_overridden(self, names: set[str]) -> None:
        """Feature a providery se změněnými parametry: tučně v tabulce a v souhrnu."""
        if names == self._overridden:
            return
        self._overridden = set(names)
        self._updating = True
        self.table.blockSignals(True)
        for name, row in self._rows.items():
            item = self.table.item(row, COL_NAME)
            font = item.font()
            font.setBold(name in names)
            item.setFont(font)
            item.setToolTip(
                item.toolTip().split("\n\n(změněné parametry)")[0]
                + ("\n\n(změněné parametry)" if name in names else "")
            )
        self.table.blockSignals(False)
        self._updating = False
        self.set_summary(*self._summary_args)

    # --- výběr --------------------------------------------------------------------

    def selected(self) -> list[str]:
        return [
            name
            for name, row in self._rows.items()
            if self.table.item(row, COL_NAME).checkState() == Qt.CheckState.Checked
        ]

    def set_checked(self, name: str, checked: bool) -> None:
        row = self._rows[name]
        self.table.item(row, COL_NAME).setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )

    def set_selected(self, names: set[str]) -> None:
        self._updating = True
        self.table.blockSignals(True)
        for name, row in self._rows.items():
            self.table.item(row, COL_NAME).setCheckState(
                Qt.CheckState.Checked if name in names else Qt.CheckState.Unchecked
            )
        self.table.blockSignals(False)
        self._updating = False
        self._refresh_groups()
        self.selection_changed.emit()

    def quick_pick(self, key: str) -> None:
        match key:
            case "all":
                names = {f.name for f in self._features}
            case "none":
                names = set()
            case "nomodels":
                names = {f.name for f in self._features if not f.requires}
            case "notranscript":
                names = {
                    f.name for f in self._features if not ({"transcript", "nlp"} & set(f.requires))
                }
            case _:
                return
        self.set_selected(names)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != COL_NAME:
            return
        name = item.data(ROLE_NAME)
        if name:
            self._refresh_groups()
            self.selection_changed.emit()
            return
        # řádek skupiny: přepne všechny členy
        group = item.data(ROLE_GROUP)
        state = item.checkState()
        target = state != Qt.CheckState.Unchecked
        self._updating = True
        self.table.blockSignals(True)
        for f in self._features:
            if f.group == group:
                self.table.item(self._rows[f.name], COL_NAME).setCheckState(
                    Qt.CheckState.Checked if target else Qt.CheckState.Unchecked
                )
        self.table.blockSignals(False)
        self._updating = False
        self._refresh_groups()
        self.selection_changed.emit()

    def _refresh_groups(self) -> None:
        """Tristate skupiny a počet vybraných v ní."""
        self._updating = True
        self.table.blockSignals(True)
        for group, row in self._group_rows.items():
            members = [f.name for f in self._features if f.group == group]
            n_checked = sum(
                self.table.item(self._rows[m], COL_NAME).checkState() == Qt.CheckState.Checked
                for m in members
            )
            head = self.table.item(row, COL_NAME)
            if n_checked == 0:
                head.setCheckState(Qt.CheckState.Unchecked)
            elif n_checked == len(members):
                head.setCheckState(Qt.CheckState.Checked)
            else:
                head.setCheckState(Qt.CheckState.PartiallyChecked)
            self.table.item(row, COL_REQUIRES).setText(f"{n_checked} z {len(members)}")
        self.table.blockSignals(False)
        self._updating = False

    # --- filtr a aktuální řádek -----------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        visible_groups: set[str] = set()
        for f in self._features:
            row = self._rows[f.name]
            hay = f.name + " " + " ".join(f"{k} {v}" for k, v in f.outputs.items())
            show = not needle or needle in hay.lower()
            self.table.setRowHidden(row, not show)
            if show:
                visible_groups.add(f.group)
        for group, row in self._group_rows.items():
            self.table.setRowHidden(row, group not in visible_groups)

    def _current_cell_changed(self, row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
        item = self.table.item(row, COL_NAME) if row >= 0 else None
        self.current_changed.emit(item.data(ROLE_NAME) if item is not None else "")

    def _link_activated(self, link: str) -> None:
        if link.startswith("provider:"):
            self.current_changed.emit(link.split(":", 1)[1])

    # --- souhrn ---------------------------------------------------------------------

    def set_summary(self, providers: list[str], columns: int, hint: str = "") -> None:
        """Řádek pod tabulkou: providery jako odkazy na parametry, počty, cena."""
        self._summary_args = (list(providers), columns, hint)
        n = len(self.selected())
        if providers:
            links = ", ".join(
                f'<a href="provider:{p}">{contract.PROVIDER_LABELS.get(p, p)}</a>'
                + (" <b>(upraveno)</b>" if p in self._overridden else "")
                for p in providers
            )
            runs = f"Spustí se: {links}"
        else:
            runs = "Bez modelů"
        parts = [runs, f"{n} feature, {columns} sloupců"]
        if hint:
            parts.append(hint)
        self.summary.setText(" · ".join(parts))

    def set_warning(self, text: str) -> None:
        self.warning.setText(text)
        self.warning.setVisible(bool(text))
