"""Uhlazená tabulka (podoba A): bílá karta, záhlaví velkými písmeny, bez mřížky, štítky.

Od 31. 10. 2026 jednotný vzhled všech tabulek a stromů aplikace (mockup
https://claude.ai/artifact/JEpsb6wKch8WBXTYT7yXC8, `docs/mockups/tabulky/` mimo git):
přehled běhů a Průběh na Výsledcích, Pacienti, Normy, Prostředí, průvodci a dialogy.
Řádek 40 px, kde nejsou pole v buňkách; hlavní sloupec tučně (`set_text(bold=True)`),
vedlejší šedě (`muted=True`), stavy štítkem (`set_pill`). Vlastní styl mají jen
Hodnoty (`widgets/values_table.py`), tabulka Analýzy (`meta_table.py`), strom dimenzí
a přehled Vývoje, vypadají ale stejně.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme

ROW_HEIGHT = 40


def style_table(
    table: QTableWidget,
    headers: list[str],
    *,
    row_height: int = ROW_HEIGHT,
    selectable: bool = False,
) -> None:
    """Záhlaví velkými písmeny, řádky bez mřížky s čárou dole.

    `selectable` = výběr jednoho celého řádku (přehled běhů, dvojklik a Enter otevřou
    detail); jinak se nevybírá nic a tabulka nebere fokus (dialogy s poli v buňkách).
    """
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels([h.upper() for h in headers])
    table.setFrameShape(QFrame.Shape.NoFrame)
    table.setStyleSheet(
        f"QTableWidget {{ background: {theme.CARD}; border: none; }}"
        f"QTableWidget::item {{ border-bottom: 1px solid {theme.BORDER}; padding-left: 8px; }}"
        f"QTableWidget::item:selected {{ background: {theme.ACCENT_SOFT}; }}"
        f"QHeaderView::section {{ background: {theme.NEUTRAL_SOFT}; color: {theme.MUTED};"
        f" border: none; border-bottom: 1px solid {theme.BORDER}; padding: 8px;"
        " font-weight: 600; }"
    )
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    if selectable:
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    else:
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if not selectable:
        header.setHighlightSections(False)
    for col in range(len(headers)):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)


def card(table: QTableWidget) -> QFrame:
    """Tabulka v bílé kartě s rámečkem, bez vnitřních okrajů."""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(table)
    return frame


def pill(text: str, role: str) -> QLabel:
    """Štítek (role ok, warn, missing, neutral, accent) se šířkou spočítanou napřed."""
    label = QLabel(text)
    label.setObjectName("pill")
    theme.set_role(label, role)
    bold = QFont(label.font())
    bold.setBold(True)
    label.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(text) + 24)
    return label


def cell(*widgets: QWidget, transparent: bool = True) -> QWidget:
    """Obsah buňky vlevo a svisle uprostřed; průhledný pro myš, když nic neovládá."""
    holder = QWidget()
    if transparent:
        holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(6)
    for widget in widgets:
        layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
    layout.addStretch(1)
    return holder


def fit_widget_column(table: QTableWidget, col: int, *, extra: int = 20) -> None:
    """Šířka sloupce podle widgetů v buňkách (štítky, pole): ResizeToContents je nevidí."""
    widths = [
        w.sizeHint().width()
        for r in range(table.rowCount())
        if (w := table.cellWidget(r, col)) is not None
    ]
    label = table.horizontalHeaderItem(col)
    head = (
        table.horizontalHeader().fontMetrics().horizontalAdvance(label.text()) + 24
        if label is not None
        else 0
    )
    header = table.horizontalHeader()
    header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
    header.resizeSection(col, max([head, *(w + extra for w in widths)]))


def set_text(
    table: QTableWidget,
    row: int,
    col: int,
    text: str,
    *,
    bold: bool = False,
    muted: bool = False,
    right: bool = False,
    tooltip: str = "",
) -> QTableWidgetItem:
    """Textová buňka: hlavní sloupec tučně, vedlejší šedě, čísla vpravo."""
    item = QTableWidgetItem(text)
    if bold:
        font = QFont(item.font())
        font.setBold(True)
        item.setFont(font)
    if muted:
        item.setForeground(QColor(theme.MUTED))
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if tooltip:
        item.setToolTip(tooltip)
    table.setItem(row, col, item)
    return item


def set_pill(
    table: QTableWidget, row: int, col: int, text: str, role: str, *, tooltip: str = ""
) -> QTableWidgetItem:
    """Štítek přes buňku; text položky zůstává (logika, řazení, testy), jen není vidět."""
    item = QTableWidgetItem(text)
    item.setForeground(QColor(0, 0, 0, 0))
    if tooltip:
        item.setToolTip(tooltip)
    table.setItem(row, col, item)
    old = table.cellWidget(row, col)
    if old is not None:
        old.hide()  # Qt ho maže až z fronty událostí; do té doby by zůstal vidět
    holder = cell(pill(text, role))
    if tooltip:
        holder.setToolTip(tooltip)
    table.setCellWidget(row, col, holder)
    return item


def fit_height(table: QTableWidget, *, max_rows: int | None = None) -> None:
    """Pevná výška podle řádků (tabulka v kartě bez vlastního posuvníku)."""
    rows = table.rowCount() if max_rows is None else min(table.rowCount(), max_rows)
    height = table.horizontalHeader().sizeHint().height() + 2
    height += sum(table.rowHeight(r) for r in range(rows))
    table.setFixedHeight(height)


def style_tree(tree: QTreeWidget, headers: list[str]) -> None:
    """Strom ve stylu tabulek: záhlaví velkými písmeny, bez střídání, čára mezi řádky."""
    tree.setColumnCount(len(headers))
    tree.setHeaderLabels([h.upper() for h in headers])
    tree.setFrameShape(QFrame.Shape.NoFrame)
    tree.setAlternatingRowColors(False)
    tree.setStyleSheet(
        f"QTreeWidget {{ background: {theme.CARD}; border: none; }}"
        f"QTreeWidget::item {{ border-bottom: 1px solid {theme.BORDER}; padding: 7px 4px; }}"
        f"QTreeWidget::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}"
        f"QHeaderView::section {{ background: {theme.NEUTRAL_SOFT}; color: {theme.MUTED};"
        f" border: none; border-bottom: 1px solid {theme.BORDER}; padding: 8px;"
        " font-weight: 600; }"
    )
    header = tree.header()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)


def style_group_item(item: QTreeWidgetItem, columns: int) -> None:
    """Skupina ve stromu jako šedý řádek přes celou šířku (jako skupiny v prazích kvality)."""
    font = QFont(item.font(0))
    font.setBold(True)
    for c in range(columns):
        item.setBackground(c, QColor(theme.NEUTRAL_SOFT))
        item.setFont(c, font)
        item.setForeground(c, QColor("#4b5563"))
    item.setFirstColumnSpanned(True)


def right_header(table: QTableWidget, *columns: int) -> None:
    """Záhlaví číselných sloupců vpravo, nad čísly zarovnanými vpravo."""
    for col in columns:
        item = table.horizontalHeaderItem(col)
        if item is not None:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


PILL_ROLE = Qt.ItemDataRole.UserRole + 41  # role štítku (ok, warn, missing, accent, neutral)
PILL_COLORS = {
    "ok": (theme.OK_SOFT, "#166534"),
    "warn": (theme.WARN_SOFT, "#92400e"),
    "missing": (theme.MISSING_SOFT, "#991b1b"),
    "accent": (theme.ACCENT_SOFT, "#1d4ed8"),
    "neutral": (theme.NEUTRAL_SOFT, "#4b5563"),
}


class PillDelegate(QStyledItemDelegate):
    """Štítek kreslený z textu buňky a role v `PILL_ROLE`, bez widgetu v buňce.

    Pro sloupce se stavem u tabulek s mnoha řádky (Průběh): widget na buňku stál při
    přehrání 500 nahrávek desetiny sekundy a zpomaloval posun (31. 10. 2026).
    """

    def _font(self, option) -> QFont:  # noqa: ANN001
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 0.85)
        return font

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: ANN001
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(theme.ACCENT_SOFT))
        # čára pod řádkem jako u ostatních buněk (styl ::item se tu nekreslí)
        painter.save()
        painter.setPen(QColor(theme.BORDER))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            return
        soft, strong = PILL_COLORS.get(index.data(PILL_ROLE) or "", PILL_COLORS["neutral"])
        font = self._font(option)
        metrics = QFontMetrics(font)
        height = metrics.height() + 4
        rect = QRectF(
            option.rect.left() + 8,
            option.rect.center().y() - height / 2 + 1,
            metrics.horizontalAdvance(text) + 18,
            height,
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(soft))
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setPen(QColor(strong))
        painter.setFont(font)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: ANN001, N802
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        return QSize(QFontMetrics(self._font(option)).horizontalAdvance(text) + 34, 28)
