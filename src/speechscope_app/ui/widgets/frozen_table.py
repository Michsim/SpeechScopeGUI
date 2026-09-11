"""Tabulka se zmrazeným prvním sloupcem (podle příkladu Qt „Frozen Column“).

Nad levým okrajem hlavní tabulky leží druhá `QTableView` se stejným
modelem a výběrem, která ukazuje jen první sloupec a jede jen svisle.
Při vodorovném posunu tak název nahrávky zůstane vidět.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget


class FrozenTableView(QTableView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.frozen = QTableView(self)
        self.frozen.setObjectName("frozen")
        self.frozen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.frozen.verticalHeader().hide()
        self.frozen.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.frozen.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.frozen.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.frozen.hide()
        self.viewport().stackUnder(self.frozen)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.frozen.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        self.horizontalHeader().sectionResized.connect(self._section_resized)
        self.verticalHeader().sectionResized.connect(self._row_resized)
        self.verticalScrollBar().valueChanged.connect(self.frozen.verticalScrollBar().setValue)
        self.frozen.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        self.frozen.doubleClicked.connect(self.doubleClicked)

    def setModel(self, model: QAbstractItemModel | None) -> None:  # noqa: N802
        super().setModel(model)
        self.frozen.setModel(model)
        if model is None or model.columnCount() == 0:
            self.frozen.hide()
            return
        self.frozen.setSelectionModel(self.selectionModel())
        for column in range(1, model.columnCount()):
            self.frozen.setColumnHidden(column, True)
        self.frozen.setColumnWidth(0, self.columnWidth(0))
        self.frozen.setAlternatingRowColors(self.alternatingRowColors())
        self.frozen.show()
        self._update_geometry()

    def setAlternatingRowColors(self, enable: bool) -> None:  # noqa: N802
        super().setAlternatingRowColors(enable)
        self.frozen.setAlternatingRowColors(enable)

    def _section_resized(self, index: int, _old: int, new: int) -> None:
        if index == 0:
            self.frozen.setColumnWidth(0, new)
            self._update_geometry()

    def _row_resized(self, index: int, _old: int, new: int) -> None:
        self.frozen.setRowHeight(index, new)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._update_geometry()

    def scrollTo(self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # noqa: ANN001, N802
        if index.column() > 0:  # první sloupec je vidět vždy, neposouvat kvůli němu
            super().scrollTo(index, hint)

    def _update_geometry(self) -> None:
        if self.model() is None:
            return
        self.frozen.setGeometry(
            self.verticalHeader().width() + self.frameWidth(),
            self.frameWidth(),
            self.columnWidth(0),
            self.viewport().height() + self.horizontalHeader().height(),
        )
