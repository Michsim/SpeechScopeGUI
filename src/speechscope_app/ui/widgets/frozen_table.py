"""Tabulka se zmrazenými prvními sloupci (podle příkladu Qt „Frozen Column“).

Nad levým okrajem hlavní tabulky leží druhá `QTableView` se stejným
modelem a výběrem, která ukazuje jen prvních `frozen_count` sloupců (výchozí
jeden, Výsledky tři: nahrávka, stav, pacient) a jede jen svisle. Při
vodorovném posunu tak název nahrávky zůstane vidět.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

SAMPLE_ROWS = 25  # kolik řádků se měří při hledání šířky sloupce


class FrozenTableView(QTableView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frozen_count = 1
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
        # Šířky sloupců z hlavičky a prvních řádků: u tabulky 500 × 135 trvalo měření
        # všech buněk přes 10 s. První sloupec (název souboru) se měří celý.
        self.horizontalHeader().setResizeContentsPrecision(SAMPLE_ROWS)

    def resizeColumnsToContents(self) -> None:  # noqa: N802
        """Šířky z hlavičky a prvních `SAMPLE_ROWS` řádků, první sloupec (název) celý.

        Vlastní měření místo Qt: `sizeHintForColumn` bere omezení řádků jen pod
        viditelnými řádky, a dokud tabulka nebyla vidět (detail běhu se plní ještě
        skrytý), změřil Qt všechny buňky. U normy 500 × 150 trvalo první otevření
        4,3 s místo 0,3 s (31. 10. 2026).
        """
        model = self.model()
        if model is None:
            return
        header = self.horizontalHeader()
        rows = model.rowCount()
        sample = range(min(rows, SAMPLE_ROWS))
        for column in range(model.columnCount()):
            if self.isColumnHidden(column):
                continue
            width = header.sectionSizeHint(column)
            measured = range(rows) if column == 0 else sample
            for row in measured:
                if self.columnSpan(row, column) > 1:
                    continue  # text chyby přes sloupce feature šířku neurčuje
                width = max(width, self.sizeHintForIndex(model.index(row, column)).width())
            self.setColumnWidth(column, width)

    @property
    def frozen_count(self) -> int:
        return self._frozen_count

    def set_frozen_count(self, count: int) -> None:
        """Kolik prvních sloupců zůstává vlevo; platí od příštího `setModel`."""
        self._frozen_count = max(1, count)

    def _frozen_width(self) -> int:
        model = self.model()
        n = min(self._frozen_count, model.columnCount()) if model is not None else 0
        return sum(self.columnWidth(c) for c in range(n))

    def setModel(self, model: QAbstractItemModel | None) -> None:  # noqa: N802
        super().setModel(model)
        self.frozen.setModel(model)
        if model is None or model.columnCount() == 0:
            self.frozen.hide()
            return
        self.frozen.setSelectionModel(self.selectionModel())
        for column in range(model.columnCount()):
            self.frozen.setColumnHidden(column, column >= self._frozen_count)
        for column in range(min(self._frozen_count, model.columnCount())):
            self.frozen.setColumnWidth(column, self.columnWidth(column))
        self.frozen.setAlternatingRowColors(self.alternatingRowColors())
        self.frozen.show()
        self._update_geometry()

    def setAlternatingRowColors(self, enable: bool) -> None:  # noqa: N802
        super().setAlternatingRowColors(enable)
        self.frozen.setAlternatingRowColors(enable)

    def _section_resized(self, index: int, _old: int, new: int) -> None:
        if index < self._frozen_count:
            self.frozen.setColumnWidth(index, new)
            self._update_geometry()

    def _row_resized(self, index: int, _old: int, new: int) -> None:
        self.frozen.setRowHeight(index, new)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._update_geometry()

    def scrollTo(self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # noqa: ANN001, N802
        if index.column() >= self._frozen_count:  # zmrazené jsou vidět vždy
            super().scrollTo(index, hint)

    def frozen_width(self) -> int:
        """Šířka zmrazené části v souřadnicích viewportu (text přes ni nekreslit)."""
        return self._frozen_width()

    def _update_geometry(self) -> None:
        if self.model() is None:
            return
        self.frozen.raise_()  # i nad záhlavím hlavní tabulky (to se dá vyměnit)
        self.frozen.setGeometry(
            self.verticalHeader().width() + self.frameWidth(),
            self.frameWidth(),
            self._frozen_width(),
            self.viewport().height() + self.horizontalHeader().height(),
        )
