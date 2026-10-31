"""Přehled vývoje: řádek = dimenze (nebo položka), sloupec = vyšetření (31. 10. 2026).

Horní část podoby Grafy (mockup https://claude.ai/artifact/DgxUjxJmh5SAihwZ4SkQog,
artboardy B a C): buňka podbarvená pásmem s číslem v SD, poslední vyšetření
tučně, před daty malý průběh (`Sparkline`), vzadu změna za období, v prvním
sloupci políčko „graf“: zaškrtnuté řádky mají pod přehledem graf
(`selection_changed`). V měřítku Hodnoty jsou buňky šedé bez pásem a řádky
jsou položky ve skupinách po dimenzích.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ...i18n import tr
from .. import theme
from .spider_chart import BAND_COLORS, BAND_SOFT, MAX_SD, SPAN, clamp_sd

ROW_HEIGHT = 46
GROUP_HEIGHT = 30
SPARK_WIDTH = 150
NAME_MAX = 320  # nejširší sloupec názvu dimenze / položky (delší se zkrátí s tooltipem)


@dataclass(slots=True)
class MapRow:
    label: str
    values: list[float | None]  # SD, nebo hodnota v jednotkách
    texts: list[str]  # text buněk
    bands: list[str]  # pásmo buňky; "" = bez pásma (šedá)
    outdated: list[bool] = field(default_factory=list)
    change: str = ""  # text změny za období
    change_side: int = 0  # 1 k patologii, -1 od ní, 0 bez soudu
    unit: str = ""  # jen v Hodnotách
    group: str = ""  # nadpis skupiny před řádkem (Hodnoty: dimenze)
    group_note: str = ""
    key: str = ""  # klíč řádku pro výběr grafů (dimenze, nebo úloha:sloupec)
    tooltip: str = ""


class Sparkline(QFrame):
    """Malý průběh v řádku přehledu: v SD zelený pás ±1 a body barvou pásma, v Hodnotách
    světlé čáry a černé body. Dutý bod = jiná verze feature."""

    def __init__(self, row: MapRow, scale: str, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.row = row
        self.scale = scale
        self.setMinimumWidth(SPARK_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(8, 6, -8, -6)
        values = self.row.values
        known = [v for v in values if v is not None]
        if not known or rect.width() < 10:
            painter.end()
            return
        sd = self.scale == "sd"
        if sd:
            lo, hi = MAX_SD - SPAN, MAX_SD
        else:
            lo, hi = min(known), max(known)
            if lo == hi:
                lo, hi = lo - 1, hi + 1
            pad = (hi - lo) * 0.2
            lo, hi = lo - pad, hi + pad

        def y_of(v: float) -> float:
            value = clamp_sd(v) if sd else v
            return rect.top() + (hi - value) / (hi - lo) * rect.height()

        painter.setPen(Qt.PenStyle.NoPen)
        if sd:
            painter.setBrush(QColor(BAND_SOFT["ok"]))
            painter.drawRect(QRectF(rect.left(), y_of(1.0), rect.width(), y_of(-1.0) - y_of(1.0)))
            painter.setPen(QPen(QColor(theme.MUTED), 1, Qt.PenStyle.DashLine))
            painter.drawLine(rect.left(), y_of(0.0), rect.right(), y_of(0.0))
        else:
            painter.setPen(QPen(QColor(theme.BORDER), 1))
            for level in (lo, (lo + hi) / 2, hi):
                painter.drawLine(rect.left(), y_of(level), rect.right(), y_of(level))
        n = len(values)
        step = rect.width() / (n - 1) if n > 1 else 0.0
        xs = [rect.left() + i * step for i in range(n)] if n > 1 else [rect.center().x()]
        bands = self.row.bands or [""] * n
        outdated = self.row.outdated or [False] * n
        points = [
            (x, y_of(v), b, o)
            for x, v, b, o in zip(xs, values, bands, outdated, strict=False)
            if v is not None
        ]
        painter.setPen(QPen(QColor(theme.TEXT), 1.4))
        for (x0, y0, _b, _o), (x1, y1, _b1, _o1) in zip(points, points[1:], strict=False):
            painter.drawLine(x0, y0, x1, y1)
        for x, y, band, out in points:
            color = QColor(BAND_COLORS.get(band, theme.TEXT) if sd else theme.TEXT)
            painter.setPen(QPen(color, 1.6))
            painter.setBrush(QColor(theme.CARD) if out else color)
            painter.drawEllipse(x - 3.2, y - 3.2, 6.4, 6.4)
        painter.end()


class TrendMap(QTableWidget):
    """Tabulka dimenze (položka) × vyšetření s políčkem „graf“, průběhem a změnou; klik na
    datum (`visit_clicked(index)`) přepíná na to vyšetření, políčka hlásí
    `selection_changed(list klíčů)`."""

    visit_clicked = Signal(int)
    selection_changed = Signal(list)

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setWordWrap(False)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(False)
        self._first_date = 0
        self._dates = 0
        self._filling = False
        self._boxes: list[tuple[str, QCheckBox]] = []
        self.cellClicked.connect(self._clicked)

    def selected_keys(self) -> list[str]:
        return [key for key, box in self._boxes if box.isChecked()]

    def set_all(self, checked: bool) -> None:
        """Zaškrtne nebo odškrtne všechny řádky (hlásí změnu jednou)."""
        self._filling = True
        for _key, box in self._boxes:
            box.setChecked(checked)
        self._filling = False
        self.selection_changed.emit(self.selected_keys())

    def _box_cell(self, key: str, checked: bool) -> QWidget:
        """Skutečné zaškrtávátko na střed buňky (stav položky kreslil jen fajfku bez rámečku)."""
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box = QCheckBox()
        box.setChecked(checked)
        box.setToolTip(tr("Ukázat graf"))
        box.toggled.connect(lambda _c: self._box_toggled())
        layout.addWidget(box)
        self._boxes.append((key, box))
        return holder

    def _box_toggled(self) -> None:
        if not self._filling:
            self.selection_changed.emit(self.selected_keys())

    def set_rows(
        self,
        dates: list[str],
        rows: list[MapRow],
        *,
        scale: str = "sd",
        date_tips: list[str] | None = None,
        selected: set[str] | None = None,
    ) -> None:
        self._filling = True
        self.clear()
        self.clearSpans()
        self.setRowCount(0)
        self._boxes = []
        with_unit = scale != "sd"
        headers = [tr("graf"), tr("dimenze") if not with_unit else tr("položka")]
        if with_unit:
            headers.append(tr("jedn."))
        headers.append(tr("průběh"))
        headers += dates
        headers.append(tr("změna za období"))
        self._first_date = 3 if not with_unit else 4
        self._dates = len(dates)
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        for i, tip in enumerate(date_tips or []):
            item = self.horizontalHeaderItem(self._first_date + i)
            if item is not None:
                item.setToolTip(tip)
        last_col = self._first_date + self._dates - 1
        total = len(rows) + sum(
            1 for i, r in enumerate(rows) if r.group and (i == 0 or rows[i - 1].group != r.group)
        )
        self.setRowCount(total)
        r = 0
        previous_group = None
        for row in rows:
            if row.group and row.group != previous_group:
                head = QTableWidgetItem(
                    row.group + (f"   {row.group_note}" if row.group_note else "")
                )
                font = head.font()
                font.setBold(True)
                head.setFont(font)
                head.setBackground(QColor(theme.NEUTRAL_SOFT))
                self.setItem(r, 0, head)
                for c in range(1, len(headers)):
                    filler = QTableWidgetItem("")
                    filler.setBackground(QColor(theme.NEUTRAL_SOFT))
                    self.setItem(r, c, filler)
                self.setSpan(r, 0, 1, len(headers))
                self.setRowHeight(r, GROUP_HEIGHT)
                r += 1
            previous_group = row.group or previous_group
            self.setItem(r, 0, QTableWidgetItem(""))
            self.setCellWidget(
                r, 0, self._box_cell(row.key, selected is not None and row.key in selected)
            )
            name = QTableWidgetItem(("    " if row.group else "") + row.label)
            if not row.group:
                font = name.font()
                font.setBold(True)
                name.setFont(font)
            name.setData(Qt.ItemDataRole.UserRole, row.key)
            name.setToolTip(row.tooltip or row.label)  # zkrácený název celý v tooltipu
            self.setItem(r, 1, name)
            col = 2
            if with_unit:
                unit = QTableWidgetItem(row.unit or "–")
                unit.setForeground(QColor(theme.MUTED))
                self.setItem(r, col, unit)
                col += 1
            self.setItem(r, col, QTableWidgetItem(""))
            self.setCellWidget(r, col, Sparkline(row, scale))
            col += 1
            for i, (value, text, band) in enumerate(
                zip(row.values, row.texts, row.bands or [""] * len(row.values), strict=False)
            ):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setData(Qt.ItemDataRole.UserRole, row.key)
                if value is not None:
                    soft = BAND_SOFT.get(band) if scale == "sd" else None
                    cell.setBackground(QColor(soft or theme.NEUTRAL_SOFT))
                    cell.setForeground(
                        QColor(BAND_COLORS.get(band, theme.TEXT) if scale == "sd" else theme.TEXT)
                    )
                    if row.outdated and i < len(row.outdated) and row.outdated[i]:
                        cell.setToolTip(tr("jiná verze feature než u ostatních vyšetření"))
                    if col + i == last_col:
                        font = QFont(cell.font())
                        font.setBold(True)
                        cell.setFont(font)
                else:
                    cell.setForeground(QColor(theme.MUTED))
                self.setItem(r, col + i, cell)
            change = QTableWidgetItem(row.change or "–")
            if row.change_side:
                change.setForeground(QColor(theme.MISSING if row.change_side > 0 else theme.OK))
                font = QFont(change.font())
                font.setBold(True)
                change.setFont(font)
            elif not row.change or row.change == "–":
                change.setForeground(QColor(theme.MUTED))
            self.setItem(r, len(headers) - 1, change)
            self.setRowHeight(r, ROW_HEIGHT)
            r += 1
        header = self.horizontalHeader()
        # název podle obsahu (nejvýš NAME_MAX), průběh a data hned za ním, volné místo
        # až za posledním sloupcem; Stretch dával mezeru mezi dimenzi a průběh
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.resizeColumnToContents(1)
        header.resizeSection(1, min(header.sectionSize(1) + 12, NAME_MAX))
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 48)
        for c in range(2, len(headers)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        spark_col = self._first_date - 1
        header.setSectionResizeMode(spark_col, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(spark_col, SPARK_WIDTH + 8)
        height = header.height() + 4 + sum(self.rowHeight(i) for i in range(self.rowCount()))
        self.setFixedHeight(max(height, 60))
        self._filling = False

    def _clicked(self, row: int, column: int) -> None:
        index = column - self._first_date
        if 0 <= index < self._dates:
            self.visit_clicked.emit(index)


__all__ = ["MapRow", "Sparkline", "TrendMap"]
