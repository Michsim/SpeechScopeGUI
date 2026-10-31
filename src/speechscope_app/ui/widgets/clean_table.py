"""Uhlazená tabulka (podoba A): bílá karta, záhlaví velkými písmeny, bez mřížky, štítky.

Stejný vzhled jako přehled běhů na Výsledcích (`pages/runs.py`) a tabulka nahrávek
na Analýze. Zatím ho používají dialogy kontroly kvality; přehled běhů má vlastní
kopii stylu (převod na tenhle modul je samostatný refaktoring).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .. import theme

ROW_HEIGHT = 40


def style_table(table: QTableWidget, headers: list[str], *, row_height: int = ROW_HEIGHT) -> None:
    """Záhlaví velkými písmeny, řádky bez mřížky s čárou dole, bez výběru buněk."""
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels([h.upper() for h in headers])
    table.setFrameShape(QFrame.Shape.NoFrame)
    table.setStyleSheet(
        f"QTableWidget {{ background: {theme.CARD}; border: none; }}"
        f"QTableWidget::item {{ border-bottom: 1px solid {theme.BORDER}; padding-left: 8px; }}"
        f"QTableWidget::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}"
        f"QHeaderView::section {{ background: {theme.NEUTRAL_SOFT}; color: {theme.MUTED};"
        f" border: none; border-bottom: 1px solid {theme.BORDER}; padding: 8px;"
        " font-weight: 600; }"
    )
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
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
