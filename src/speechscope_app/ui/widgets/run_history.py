"""Seznam běhů ze složek v Dokumentech: kdy, protokol, nahrávek, stav.

Sdílí ho stránka Výsledky (otevře tabulku) a Výpočet (ukáže průběh
a log). Widget jen vypisuje a hlásí výběr, data bere z `backend/history`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...backend.history import RunInfo, list_runs
from ...i18n import tr
from .. import theme

STATUS_COLORS = {
    "ok": theme.OK,
    "cancelled": theme.WARN,
    "error": theme.MISSING,
    "prepare": theme.NEUTRAL,
    "running": theme.ACCENT,
    "interrupted": theme.WARN,
    "unknown": theme.MUTED,
}


class RunHistory(QWidget):
    selected = Signal(object)  # RunInfo
    delete_requested = Signal(object)  # RunInfo: Smazat…, Delete, pravé tlačítko

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._work_root: Path | None = None
        self._runs: list[RunInfo] = []
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setObjectName("section")
        self.refresh_btn = QPushButton(tr("Obnovit"))
        self.refresh_btn.clicked.connect(self.refresh)
        self.delete_btn = QPushButton(tr("Smazat…"))
        self.delete_btn.setToolTip(
            tr("Přesune složku vybraného běhu do Koše. Nahrávek se to netýká.")
        )
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_current)
        head.addWidget(self.title, 1)
        head.addWidget(self.delete_btn)
        head.addWidget(self.refresh_btn)
        layout.addLayout(head)
        self.runs = QTableWidget()
        self.runs.setColumnCount(4)
        self.runs.setHorizontalHeaderLabels([tr("kdy"), tr("protokol"), tr("nahrávek"), tr("stav")])
        self.runs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.runs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.runs.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.runs.verticalHeader().setVisible(False)
        self.runs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.runs.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.runs.itemSelectionChanged.connect(self._selection_changed)
        self.runs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.runs.customContextMenuRequested.connect(self._menu)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.runs, self._delete_current)
        layout.addWidget(self.runs, 1)

    # --- mazání --------------------------------------------------------------------

    def can_delete(self, info: RunInfo | None) -> bool:
        return info is not None and info.status != "running"

    def _delete_current(self) -> None:
        info = self.current()
        if self.can_delete(info):
            self.delete_requested.emit(info)

    def _menu(self, pos) -> None:  # noqa: ANN001
        item = self.runs.itemAt(pos)
        if item is None:
            return
        self.runs.selectRow(item.row())
        info = self.current()
        if info is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction(tr("Otevřít složku"))
        open_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(info.dir)))
        )
        delete_action = menu.addAction(tr("Smazat běh…"))
        delete_action.setEnabled(self.can_delete(info))
        delete_action.triggered.connect(lambda: self.delete_requested.emit(info))
        menu.exec(self.runs.viewport().mapToGlobal(pos))

    def set_work_root(self, root: Path) -> None:
        self._work_root = root
        self.refresh()

    def all_runs(self) -> list[RunInfo]:
        return list(self._runs)

    def current(self) -> RunInfo | None:
        model = self.runs.selectionModel()
        rows = model.selectedRows() if model else []
        return self._runs[rows[0].row()] if rows else None

    def refresh(self, select: Path | None = None) -> None:
        """Znovu načte seznam; `select` je složka běhu, která má zůstat vybraná."""
        keep = select or (self.current().dir if self.current() else None)
        self._runs = list_runs(self._work_root) if self._work_root else []
        self.runs.blockSignals(True)
        self.runs.setRowCount(len(self._runs))
        select_row: int | None = None
        for row, info in enumerate(self._runs):
            when = info.started.strftime("%d.%m. %H:%M") if info.started else info.dir.name
            count = ""
            if info.total:
                count = (
                    f"{info.processed}/{info.total}"
                    if info.processed is not None and info.processed != info.total
                    else str(info.total)
                )
            elif info.rows is not None:
                count = str(info.rows)
            cells = [when, info.protocol_name, count, info.status_label]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(str(info.dir))
                if col == 3:
                    item.setForeground(QColor(STATUS_COLORS.get(info.status, theme.MUTED)))
                if col == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.runs.setItem(row, col, item)
            if keep is not None and info.dir == keep:
                select_row = row
        self.runs.blockSignals(False)
        self.title.setText(
            tr("{title} ({n})").format(title=self._title, n=len(self._runs))
            if self._runs
            else self._title
        )
        if select_row is not None:
            # výběr zůstává, kde byl; stránka už ten běh ukazuje, znovu ho nenačítat
            self.runs.blockSignals(True)
            self.runs.selectRow(select_row)
            self.runs.blockSignals(False)
        self.delete_btn.setEnabled(self.can_delete(self.current()))

    def select_dir(self, run_dir: Path) -> bool:
        for row, info in enumerate(self._runs):
            if info.dir == run_dir:
                self.runs.selectRow(row)
                return True
        return False

    def _selection_changed(self) -> None:
        info = self.current()
        self.delete_btn.setEnabled(self.can_delete(info))
        if info is not None:
            self.selected.emit(info)
