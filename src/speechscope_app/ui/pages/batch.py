"""Stránka Data: složka, protokol, nalezené nahrávky, spuštění.

Základní režim ukazuje jen složku, protokol a nahrávky. Rozšířený režim
přidává pod nahrávky editor protokolu (výběr feature a parametry),
kterým výzkumník upraví protokol pro tento běh nebo ho uloží jako nový.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.discover import Recording, find_recordings
from ...backend.library import Library
from ...backend.protocol import Protocol
from .. import theme
from ..protocol_dialog import SaveProtocolDialog
from ..widgets.protocol_editor import ProtocolEditor


class BatchPage(QWidget):
    run_requested = Signal(object, list)  # Protocol, list[Path]
    save_requested = Signal(object)  # Protocol upravený v rozšířeném režimu

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self._protocols: list[Protocol] = []
        self._recordings: list[Recording] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        title = QLabel("Data")
        title.setObjectName("page_title")
        layout.addWidget(title)
        subtitle = QLabel(
            "Vyber složku s nahrávkami a protokol. Do složky s nahrávkami se nic nezapisuje, "
            "výsledky jdou do Dokumentů."
        )
        subtitle.setObjectName("page_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        folder_row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Složka s nahrávkami")
        self.folder.editingFinished.connect(self.rescan)
        browse = QPushButton("Vybrat…")
        browse.clicked.connect(self._browse)
        self.recursive = QCheckBox("včetně podsložek")
        self.recursive.setChecked(True)
        self.recursive.toggled.connect(self.rescan)
        folder_row.addWidget(QLabel("Nahrávky:"))
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        folder_row.addWidget(self.recursive)
        layout.addLayout(folder_row)

        proto_row = QHBoxLayout()
        self.protocol = QComboBox()
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self.protocol_desc = QLabel("")
        self.protocol_desc.setWordWrap(True)
        proto_row.addWidget(QLabel("Protokol:"))
        proto_row.addWidget(self.protocol, 1)
        layout.addLayout(proto_row)
        layout.addWidget(self.protocol_desc)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.splitter, 1)

        self.files = QTableWidget()
        self.files.setColumnCount(3)
        self.files.setHorizontalHeaderLabels(["nahrávka", "ruční labely", "ruční přepis"])
        self.files.horizontalHeader().setStretchLastSection(True)
        self.files.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.files.verticalHeader().setVisible(False)
        self.splitter.addWidget(self.files)

        self.advanced_box = QGroupBox("Feature a parametry")
        adv = QVBoxLayout(self.advanced_box)
        self.editor = ProtocolEditor()
        self.editor.changed.connect(self._update_run_state)
        adv.addWidget(self.editor, 1)
        self.save_btn = QPushButton("Uložit jako protokol…")
        self.save_btn.setToolTip("Uloží aktuální výběr feature a parametry jako nový protokol")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        adv.addWidget(self.save_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.splitter.addWidget(self.advanced_box)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([150, 450])

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.run_btn = QPushButton("Spustit")
        theme.set_role(self.run_btn, "primary")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._run)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.run_btn)
        layout.addLayout(bottom)

    # --- nastavení zvenku -----------------------------------------------------

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self.editor.set_library(library)
        self._update_run_state()

    def set_doctor(self, report: dict[str, Any] | None) -> None:
        self.editor.set_doctor(report)

    def set_stats_lookup(self, lookup: Callable[[str], float | None]) -> None:
        self.editor.set_stats_lookup(lookup)

    def refresh_summary(self) -> None:
        self.editor.refresh_summary()

    def set_protocols(self, protocols: list[Protocol], *, current: str = "") -> None:
        self._protocols = protocols
        self.protocol.blockSignals(True)
        self.protocol.clear()
        for p in protocols:
            self.protocol.addItem(p.name if p.builtin else f"{p.name} (vlastní)", p)
        self.protocol.blockSignals(False)
        idx = next((i for i, p in enumerate(protocols) if p.name == current), 0)
        self.protocol.setCurrentIndex(idx)
        self._protocol_changed()

    def protocol_names(self) -> set[str]:
        return {p.name for p in self._protocols}

    def set_advanced(self, advanced: bool) -> None:
        self.advanced_box.setVisible(advanced)

    def set_folder(self, folder: Path) -> None:
        self.folder.setText(str(folder))
        self.rescan()

    def current_protocol(self) -> Protocol | None:
        return self.protocol.currentData()

    # --- složka ---------------------------------------------------------------

    def _browse(self) -> None:
        start = self.folder.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Složka s nahrávkami", start)
        if chosen:
            self.set_folder(Path(chosen))

    def rescan(self) -> None:
        text = self.folder.text().strip()
        self._recordings = (
            find_recordings(Path(text), recursive=self.recursive.isChecked()) if text else []
        )
        self.files.setRowCount(len(self._recordings))
        for r, rec in enumerate(self._recordings):
            for c, value in enumerate(
                [rec.name, "ano" if rec.has_labels else "", "ano" if rec.has_transcript else ""]
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(str(rec.path))
                self.files.setItem(r, c, item)
        self.files.resizeColumnsToContents()
        self._update_run_state()

    # --- protokol -------------------------------------------------------------------

    def _protocol_changed(self) -> None:
        proto = self.current_protocol()
        self.protocol_desc.setText(proto.description if proto else "")
        self.editor.set_protocol(proto)
        self._update_run_state()

    # --- spuštění -------------------------------------------------------------

    def _update_run_state(self) -> None:
        proto = self.current_protocol()
        ok = bool(self._recordings) and proto is not None and self._library is not None
        self.run_btn.setEnabled(ok)
        self.save_btn.setEnabled(proto is not None and self.editor.has_catalog())
        if not self._recordings:
            self.status.setText("Vyber složku s nahrávkami.")
        elif proto is None:
            self.status.setText("Vyber protokol.")
        else:
            self.status.setText(
                f"{len(self._recordings)} nahrávek, úloha {contract.TASK_LABELS[proto.task]}."
            )

    def effective_protocol(self) -> Protocol | None:
        """Protokol tak, jak ho uživatel upravil v rozšířeném režimu."""
        if not self.advanced_box.isHidden():
            return self.editor.result()
        base = self.current_protocol()
        if base is None:
            return None
        return Protocol(
            name=base.name,
            task=base.task,
            description=base.description,
            features=list(base.features),
            domain=base.domain,
            vad=base.vad,
            config={k: dict(v) for k, v in base.config.items()},
        )

    def _run(self) -> None:
        proto = self.effective_protocol()
        if proto is None:
            return
        self.run_requested.emit(proto, [r.path for r in self._recordings])

    def _save(self) -> None:
        proto = self.effective_protocol()
        if proto is None:
            return
        builtin = {p.name for p in self._protocols if p.builtin}
        dialog = SaveProtocolDialog(proto, self.protocol_names() - builtin, self)
        if dialog.exec():
            self.save_requested.emit(dialog.result_protocol())
