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
from ...backend.library import Library, LibraryError
from ...backend.protocol import Protocol, card_infos
from .. import theme
from ..protocol_dialog import SaveProtocolDialog
from ..widgets.protocol_editor import ProtocolEditorDialog
from ..widgets.protocol_list import ProtocolCardInfo, ProtocolList


class BatchPage(QWidget):
    run_requested = Signal(object, list)  # Protocol, list[Path]
    save_requested = Signal(object)  # Protocol upravený v rozšířeném režimu

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self._protocols: list[Protocol] = []
        self._recordings: list[Recording] = []
        self._seconds_per_file: Callable[[str], float | None] = lambda _slug: None

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

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.splitter, 1)

        self.protocols = ProtocolList()
        self.protocols.current_changed.connect(self._protocol_changed)
        self.splitter.addWidget(self.protocols)

        self.files = QTableWidget()
        self.files.setColumnCount(3)
        self.files.setHorizontalHeaderLabels(["nahrávka", "ruční labely", "ruční přepis"])
        self.files.horizontalHeader().setStretchLastSection(True)
        self.files.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.files.verticalHeader().setVisible(False)
        self.splitter.addWidget(self.files)

        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

        # rozšířený režim: souhrn výběru a tlačítko do okna editoru
        self.editor_dialog = ProtocolEditorDialog(self)
        self.editor = self.editor_dialog.editor
        self.editor.changed.connect(self._editor_changed)
        self.advanced_box = QGroupBox("Feature a parametry")
        adv = QHBoxLayout(self.advanced_box)
        self.editor_summary = QLabel("")
        self.editor_summary.setWordWrap(True)
        adv.addWidget(self.editor_summary, 1)
        self.edit_btn = QPushButton("Upravit…")
        self.edit_btn.setToolTip("Výběr feature a parametry jen pro tento běh")
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self.open_editor)
        adv.addWidget(self.edit_btn)
        self.save_btn = QPushButton("Uložit jako protokol…")
        self.save_btn.setToolTip("Uloží aktuální výběr feature a parametry jako nový protokol")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        adv.addWidget(self.save_btn)
        layout.addWidget(self.advanced_box)

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
        self._seconds_per_file = lookup
        self.editor.set_stats_lookup(lookup)

    def refresh_summary(self) -> None:
        """Po běhu: nová doba na nahrávku do karet i do souhrnu editoru."""
        self.editor.refresh_summary()
        self.protocols.set_protocols(
            self._protocols, self._card_infos(), current=self.protocols.current_name()
        )

    def set_protocols(self, protocols: list[Protocol], *, current: str = "") -> None:
        self._protocols = protocols
        self.protocols.set_protocols(protocols, self._card_infos(), current=current)
        if self.protocols.current() is None:
            self._protocol_changed(None)

    def _card_infos(self) -> dict[str, ProtocolCardInfo]:
        if self._library is None:
            return {}
        try:
            catalog = self._library.features()
            providers = self._library.providers()
        except LibraryError:
            return {}
        raw = card_infos(self._protocols, catalog, providers, self._seconds_per_file)
        return {name: ProtocolCardInfo(list(p), hint) for name, (p, hint) in raw.items()}

    def select_protocol(self, name: str) -> bool:
        return self.protocols.select(name)

    def protocol_names(self) -> set[str]:
        return {p.name for p in self._protocols}

    def set_advanced(self, advanced: bool) -> None:
        self.advanced_box.setVisible(advanced)

    def set_folder(self, folder: Path) -> None:
        self.folder.setText(str(folder))
        self.rescan()

    def current_protocol(self) -> Protocol | None:
        return self.protocols.current()

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

    def _protocol_changed(self, proto: Protocol | None) -> None:
        self.editor.set_protocol(proto)
        self._update_run_state()

    def _editor_changed(self) -> None:
        proto = self.current_protocol()
        if proto is not None:
            edited = self.editor.result()
            self.protocols.set_modified(
                proto.name, edited is not None and edited.to_dict() != proto.to_dict()
            )
        self._update_run_state()

    def open_editor(self) -> None:
        proto = self.current_protocol()
        if proto is None:
            return
        self.editor_dialog.open_for(f"{proto.name} · úprava jen pro tento běh")
        self._editor_changed()

    def _update_editor_summary(self) -> None:
        """Krátký souhrn výběru do rámečku na Datech (celý je v okně editoru)."""
        if not self.editor.has_catalog():
            self.editor_summary.setText("Seznam feature není k dispozici.")
            return
        text = self.editor.picker.summary.text()
        self.editor_summary.setText(text)

    # --- spuštění -------------------------------------------------------------

    def _update_run_state(self) -> None:
        proto = self.current_protocol()
        ok = bool(self._recordings) and proto is not None and self._library is not None
        self.run_btn.setEnabled(ok)
        self.save_btn.setEnabled(proto is not None and self.editor.has_catalog())
        self.edit_btn.setEnabled(proto is not None and self.editor.has_catalog())
        self._update_editor_summary()
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
