"""Stránka Výsledky: tabulka z výstupního CSV."""

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
    QTableView,
    QVBoxLayout,
    QWidget,
)

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

        layout = QVBoxLayout(self)
        head = QHBoxLayout()
        self.summary = QLabel("Zatím žádný výstup.")
        self.summary.setWordWrap(True)
        self.problems_only = QCheckBox("jen řádky s poznámkou nebo chybou")
        self.problems_only.toggled.connect(self._refresh)
        self.open_btn = QPushButton("Otevřít složku")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_folder)
        head.addWidget(self.summary, 1)
        head.addWidget(self.problems_only)
        head.addWidget(self.open_btn)
        layout.addLayout(head)

        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

    def load(self, path: Path) -> None:
        self._path = path
        try:
            self._frame = pd.read_csv(path)
        except (OSError, pd.errors.ParserError) as exc:
            self.summary.setText(f"CSV nejde načíst: {exc}")
            self._frame = None
            return
        self.open_btn.setEnabled(True)
        self._refresh()

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
        self.summary.setText(
            f"{self._path}: {len(self._frame)} řádků, {len(self._frame.columns)} sloupců, "
            f"{n_notes} s poznámkou, {n_err} s chybou."
        )

    def _open_folder(self) -> None:
        if self._path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path.parent)))
