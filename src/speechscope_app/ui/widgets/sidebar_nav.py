"""Boční nabídka ve dvou skupinách: nahoře každodenní stránky, dole ostatní.

Navenek se chová jako jeden `QListWidget` (`setCurrentRow`, `currentRow`,
`item`, `currentRowChanged`), uvnitř jsou dva seznamy a mezi nimi
roztažitelná mezera, takže spodní skupina sedí u dolního okraje.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class SidebarNav(QWidget):
    currentRowChanged = Signal(int)  # noqa: N815  jako u QListWidget

    def __init__(
        self, labels: tuple[str, ...], *, bottom_from: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._labels = tuple(labels)
        self._split = bottom_from
        self._current = -1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)
        self.top = self._make_list(labels[:bottom_from])
        self.bottom = self._make_list(labels[bottom_from:])
        layout.addWidget(self.top)
        layout.addStretch(1)
        layout.addWidget(self.bottom)
        self.top.currentRowChanged.connect(lambda row: self._changed(row, 0))
        self.bottom.currentRowChanged.connect(lambda row: self._changed(row, self._split))

    def _make_list(self, labels: tuple[str, ...]) -> QListWidget:
        widget = QListWidget()
        widget.setObjectName("nav")
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        for label in labels:
            widget.addItem(label)
        return widget

    def _changed(self, row: int, offset: int) -> None:
        if row < 0:
            return
        index = row + offset
        if index == self._current:
            return
        self._current = index
        other = self.bottom if offset == 0 else self.top
        other.blockSignals(True)
        other.setCurrentRow(-1)
        other.clearSelection()
        other.blockSignals(False)
        self.currentRowChanged.emit(index)

    # --- rozhraní jako QListWidget ---------------------------------------------

    def count(self) -> int:
        return len(self._labels)

    def currentRow(self) -> int:  # noqa: N802
        return self._current

    def setCurrentRow(self, index: int) -> None:  # noqa: N802
        if index < self._split:
            self.top.setCurrentRow(index)
        else:
            self.bottom.setCurrentRow(index - self._split)

    def item(self, index: int) -> QListWidgetItem:
        if index < self._split:
            return self.top.item(index)
        return self.bottom.item(index - self._split)
