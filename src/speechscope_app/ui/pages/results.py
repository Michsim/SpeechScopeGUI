"""Stránka Výsledky: historie běhů vlevo, tabulka vybraného běhu vpravo.

Historie se čte ze složek `<datum>_<protokol>` v Dokumentech
(`backend/history.py`). Vybraný běh ukáže `features.csv`; běhy bez
tabulky (jen segmentace, chyba před první nahrávkou) ukážou aspoň stav
a odkaz na složku.

Tabulka má zmrazený první sloupec (název nahrávky), hledání sloupce,
popis sloupce v tooltipu hlavičky (z `list --json`), detail jedné
nahrávky po dvojkliku a „Spočítat znovu chybné“ pro řádky s chybou.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...backend.history import RunInfo
from ...backend.library import FeatureInfo, Library, LibraryError
from ...backend.results import failed_paths
from ...i18n import tr
from ..file_actions import file_menu
from ..recording_detail import RecordingDetailDialog, column_descriptions
from ..widgets.frozen_table import FrozenTableView
from ..widgets.run_history import RunHistory

PROBLEM_COLUMNS = ("notes", "error")


class FrameModel(QAbstractTableModel):
    def __init__(self, frame: pd.DataFrame, descriptions: dict[str, str] | None = None) -> None:
        super().__init__()
        self.frame = frame
        self.descriptions = descriptions or {}

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
        if orientation != Qt.Orientation.Horizontal:
            return str(section + 1) if role == Qt.ItemDataRole.DisplayRole else None
        column = str(self.frame.columns[section])
        if role == Qt.ItemDataRole.DisplayRole:
            return column
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.descriptions.get(column, column)
        return None


class ResultsPage(QWidget):
    rerun_requested = Signal(object, list)  # složka běhu, cesty nahrávek s chybou
    notice = Signal(str)  # krátká hláška do stavového řádku hlavního okna

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: Path | None = None
        self._frame: pd.DataFrame | None = None
        self._note = ""
        self._library: Library | None = None
        self._catalog: list[FeatureInfo] | None = None

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
        self.open_btn = QPushButton(tr("Otevřít složku"))
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_folder)
        head.addWidget(self.summary, 1)
        head.addWidget(self.open_btn)
        right_layout.addLayout(head)

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat sloupec"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(260)
        self.search.textChanged.connect(self._refresh)
        self.problems_only = QCheckBox(tr("jen řádky s poznámkou nebo chybou"))
        self.problems_only.toggled.connect(self._refresh)
        self.detail_btn = QPushButton(tr("Detail nahrávky…"))
        self.detail_btn.setToolTip(tr("Hodnoty jedné nahrávky po skupinách s popisem sloupců."))
        self.detail_btn.setEnabled(False)
        self.detail_btn.clicked.connect(self._detail_current)
        self.rerun_btn = QPushButton(tr("Spočítat znovu chybné"))
        self.rerun_btn.setToolTip(
            tr(
                "Pustí stejný protokol jen nad nahrávkami s chybou a jejich řádky "
                "v této tabulce nahradí."
            )
        )
        self.rerun_btn.hide()
        self.rerun_btn.clicked.connect(self._rerun_failed)
        tools.addWidget(self.search)
        tools.addWidget(self.problems_only)
        tools.addStretch(1)
        tools.addWidget(self.rerun_btn)
        tools.addWidget(self.detail_btn)
        right_layout.addLayout(tools)

        self.table = FrozenTableView()
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(lambda index: self.show_detail(index.row()))
        for view in (self.table, self.table.frozen):
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda pos, view=view: self._row_menu(view, pos)
            )
        right_layout.addWidget(self.table, 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([400, 700])

    # --- knihovna (popisy sloupců) ------------------------------------------------------

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self._catalog = None

    def catalog(self) -> list[FeatureInfo]:
        """Katalog feature z `list --json`, líně a jen jednou; bez knihovny prázdný."""
        if self._catalog is None:
            self._catalog = []
            if self._library is not None:
                try:
                    self._catalog = self._library.features()
                except LibraryError:
                    self._catalog = []
        return self._catalog

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
        elif info.status == "running":
            note = tr("Výpočet ještě běží, tabulka je zatím částečná.")
        elif info.status == "interrupted":
            note = tr("Běh byl přerušen (aplikace skončila během výpočtu), tabulka je částečná.")
        if info.csv_path is not None and info.status != "running":
            self.load(info.csv_path, note=note, refresh_history=False)
            return
        # Běžící běh se neotvírá: Windows by knihovně zablokovaly přejmenování `.part`.
        self._path = info.dir / "features.csv"
        self._frame = None
        self.table.setModel(None)
        self.detail_btn.setEnabled(False)
        self.rerun_btn.hide()
        self.open_btn.setEnabled(True)
        if info.status == "running":
            self.summary.setText(
                note
                + " "
                + tr("Hotovo {n} z {total}, tabulka se ukáže po skončení.").format(
                    n=info.processed if info.processed is not None else 0, total=info.total or "?"
                )
            )
        elif info.status == "prepare":
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
            self.detail_btn.setEnabled(False)
            self.rerun_btn.hide()
            return
        self.open_btn.setEnabled(True)
        self._refresh()
        if refresh_history:
            self.refresh()

    def visible_frame(self) -> pd.DataFrame | None:
        model = self.table.model()
        return model.frame if isinstance(model, FrameModel) else None

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
        needle = self.search.text().strip().lower()
        if needle:
            keep = [
                c
                for c in frame.columns
                if c == "file" or needle in str(c).lower() or c in PROBLEM_COLUMNS
            ]
            frame = frame[keep]
        self.table.setModel(
            FrameModel(frame.reset_index(drop=True), column_descriptions(self.catalog()))
        )
        self.table.resizeColumnsToContents()
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.detail_btn.setEnabled(False)
        can_rerun = bool(failed_paths(self._frame)) and (
            self._path is not None and (self._path.parent / "protocol.yaml").is_file()
        )
        self.rerun_btn.setVisible(can_rerun)
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

    def _selection_changed(self) -> None:
        self.detail_btn.setEnabled(bool(self.table.selectionModel().selectedRows()))

    # --- detail a znovu chybné ---------------------------------------------------------

    def detail_row(self, row: int) -> dict[str, Any] | None:
        """Celý řádek (všechny sloupce, i skryté hledáním) podle řádku v tabulce."""
        shown = self.visible_frame()
        if shown is None or self._frame is None or not (0 <= row < len(shown)):
            return None
        if "file" in shown.columns and "file" in self._frame.columns:
            name = shown.iat[row, list(shown.columns).index("file")]
            full = self._frame[self._frame["file"] == name]
            if len(full) == 1:
                return full.iloc[0].to_dict()
        return shown.iloc[row].to_dict()

    def make_detail(self, row: int) -> RecordingDetailDialog | None:
        data = self.detail_row(row)
        if data is None:
            return None
        return RecordingDetailDialog(data, self.catalog(), self)

    def show_detail(self, row: int) -> None:
        dialog = self.make_detail(row)
        if dialog is not None:
            dialog.exec()

    def path_for_row(self, row: int) -> Path | None:
        data = self.detail_row(row)
        if not data or not data.get("path"):
            return None
        return Path(str(data["path"]))

    def _row_menu(self, view, pos) -> None:  # noqa: ANN001
        index = view.indexAt(pos)
        if not index.isValid():
            return
        path = self.path_for_row(index.row())
        if path is None:
            return
        menu = file_menu(self, path, self.notice.emit)
        detail = menu.addAction(tr("Detail nahrávky…"))
        detail.triggered.connect(lambda: self.show_detail(index.row()))
        menu.exec(view.viewport().mapToGlobal(pos))

    def _detail_current(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.model() else []
        if rows:
            self.show_detail(rows[0].row())

    def _rerun_failed(self) -> None:
        if self._frame is None or self._path is None:
            return
        paths = failed_paths(self._frame)
        if paths:
            self.rerun_requested.emit(self._path.parent, paths)

    def _open_folder(self) -> None:
        if self._path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path.parent)))
