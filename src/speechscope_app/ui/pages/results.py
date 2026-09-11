"""Stránka Výsledky: historie běhů vlevo, tabulka vybraného běhu vpravo.

Historie se čte ze složek `<datum>_<protokol>` v Dokumentech
(`backend/history.py`). Vybraný běh ukáže `features.csv`; běhy bez
tabulky (jen segmentace, chyba před první nahrávkou) ukážou aspoň stav
a odkaz na složku.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...backend.history import RunInfo
from ...i18n import tr
from ..widgets.run_history import RunHistory

PROBLEM_COLUMNS = ("notes", "error")


class FrameModel(QAbstractTableModel):
    def __init__(self, frame: pd.DataFrame) -> None:
        super().__init__()
        self.frame = frame

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.frame)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.frame.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            value = self.frame.iat[index.row(), index.column()]
            if isinstance(value, float):
                return "" if pd.isna(value) else f"{value:.4g}"
            return "" if pd.isna(value) else str(value)
        if role == Qt.ItemDataRole.BackgroundRole:
            row = self.frame.iloc[index.row()]
            if "error" in self.frame.columns and pd.notna(row.get("error")) and row.get("error"):
                return QColor(255, 205, 205)
            if "notes" in self.frame.columns and pd.notna(row.get("notes")) and row.get("notes"):
                return QColor(255, 240, 200)
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return str(self.frame.columns[section])
        return str(section + 1)


class ResultsPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: Path | None = None
        self._frame: pd.DataFrame | None = None
        self._note = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        title = QLabel(tr("Výsledky"))
        title.setObjectName("page_title")
        layout.addWidget(title)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)

        # --- historie (sdílený seznam) --------------------------------------------------
        self.history = RunHistory(tr("Historie běhů"))
        self.history.selected.connect(self.show_run)
        self.runs = self.history.runs
        self.splitter.addWidget(self.history)

        # --- tabulka -------------------------------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        head = QHBoxLayout()
        self.summary = QLabel(tr("Zatím žádný výstup."))
        self.summary.setWordWrap(True)
        self.summary.setToolTip("")
        self.problems_only = QCheckBox(tr("jen řádky s poznámkou nebo chybou"))
        self.problems_only.toggled.connect(self._refresh)
        self.open_btn = QPushButton(tr("Otevřít složku"))
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_folder)
        head.addWidget(self.summary, 1)
        head.addWidget(self.problems_only)
        head.addWidget(self.open_btn)
        right_layout.addLayout(head)
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        right_layout.addWidget(self.table, 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([400, 700])

    # --- historie ----------------------------------------------------------------------

    def set_work_root(self, root: Path) -> None:
        self.history.set_work_root(root)

    def refresh(self) -> None:
        """Znovu načte seznam běhů; výběr zůstane na složce otevřené tabulky."""
        self.history.refresh(select=self._path.parent if self._path else None)

    def show_run(self, info: RunInfo) -> None:
        note = ""
        if info.status == "cancelled" and info.total:
            note = tr("Částečný výsledek po zrušení: {n} z {total} nahrávek.").format(
                n=info.processed if info.processed is not None else "?", total=info.total
            )
        elif info.status == "error":
            note = tr("Běh skončil chybou, viz log ve složce.")
        if info.csv_path is not None:
            self.load(info.csv_path, note=note, refresh_history=False)
            return
        self._path = info.dir / "features.csv"
        self._frame = None
        self.table.setModel(None)
        self.open_btn.setEnabled(True)
        if info.status == "prepare":
            self.summary.setText(
                tr(
                    "{name}: jen mezivýsledky (segmentace nebo přepis), tabulka se nepočítala. "
                    "Leží ve složce work."
                ).format(name=info.protocol_name)
            )
        else:
            self.summary.setText(
                (note + " " if note else "")
                + tr("{name}: bez tabulky výsledků.").format(name=info.protocol_name)
            )

    # --- tabulka --------------------------------------------------------------------------

    def load(self, path: Path, note: str = "", *, refresh_history: bool = True) -> None:
        """Načte CSV; `note` je věta před souhrn (třeba že jde o částečný výsledek)."""
        self._path = path
        self._note = note
        try:
            self._frame = pd.read_csv(path)
        except (OSError, pd.errors.ParserError) as exc:
            self.summary.setText(tr("CSV nejde načíst: {error}").format(error=exc))
            self._frame = None
            self.table.setModel(None)
            return
        self.open_btn.setEnabled(True)
        self._refresh()
        if refresh_history:
            self.refresh()

    def _refresh(self) -> None:
        if self._frame is None:
            return
        frame = self._frame
        n_notes = int(frame["notes"].notna().sum()) if "notes" in frame.columns else 0
        n_err = int(frame["error"].notna().sum()) if "error" in frame.columns else 0
        if self.problems_only.isChecked():
            mask = pd.Series(False, index=frame.index)
            for col in PROBLEM_COLUMNS:
                if col in frame.columns:
                    mask |= frame[col].notna()
            frame = frame[mask]
        self.table.setModel(FrameModel(frame.reset_index(drop=True)))
        self.table.resizeColumnsToContents()
        self.summary.setToolTip(str(self._path))
        note = f"{self._note} " if self._note else ""
        self.summary.setText(
            note
            + tr(
                "{path}: {rows} řádků, {cols} sloupců, {notes} s poznámkou, {errors} s chybou."
            ).format(
                path=f"{self._path.parent.name}/{self._path.name}" if self._path else "",
                rows=len(self._frame),
                cols=len(self._frame.columns),
                notes=n_notes,
                errors=n_err,
            )
        )

    def _open_folder(self) -> None:
        if self._path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path.parent)))
