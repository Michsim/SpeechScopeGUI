"""Stránka Data: složka, protokol, nalezené nahrávky, spuštění.

Základní režim ukazuje jen složku, protokol a nahrávky. Rozšířený režim
přidává pod nahrávky výběr feature (`FeaturePicker`) a vpravo od něj
parametry vybrané feature nebo providera.
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
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.discover import Recording, find_recordings
from ...backend.library import Library, LibraryError
from ...backend.protocol import Protocol, summarize
from .. import theme
from ..protocol_dialog import SaveProtocolDialog
from ..widgets.feature_picker import FeaturePicker
from ..widgets.param_form import ParamForm


class ParamsPanel(QWidget):
    """Parametry jedné feature nebo providera, s návratem na výchozí."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = ""
        self._form: ParamForm | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.title = QLabel("Parametry")
        self.title.setObjectName("card_title")
        self.reset_btn = QPushButton("Výchozí")
        self.reset_btn.setToolTip("Vrátit parametry na hodnoty z knihovny")
        self.reset_btn.setEnabled(False)
        self.reset_btn.clicked.connect(self.reset)
        head.addWidget(self.title, 1)
        head.addWidget(self.reset_btn)
        layout.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.hint = QLabel("Klikni na feature nebo provider v tabulce.")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        self.scroll.setWidget(self.hint)
        layout.addWidget(self.scroll, 1)

    def show_params(self, name: str, form: ParamForm | None, error: str = "") -> None:
        self._name = name
        self._form = form
        if form is not None:
            self.title.setText(name)
            self.scroll.takeWidget()
            self.scroll.setWidget(form)
            form.show()
            self.reset_btn.setEnabled(bool(form._params))
        else:
            self.title.setText(name or "Parametry")
            label = QLabel(error or "Klikni na feature nebo provider v tabulce.")
            label.setObjectName("muted")
            label.setWordWrap(True)
            self.scroll.takeWidget()
            self.scroll.setWidget(label)
            self.reset_btn.setEnabled(False)

    def reset(self) -> None:
        if self._form is not None:
            self._form.reset()


class BatchPage(QWidget):
    run_requested = Signal(object, list)  # Protocol, list[Path]
    save_requested = Signal(object)  # Protocol upravený v rozšířeném režimu

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self._protocols: list[Protocol] = []
        self._recordings: list[Recording] = []
        self._param_forms: dict[str, ParamForm] = {}
        self._overrides: dict[str, dict] = {}
        self._doctor: dict[str, Any] | None = None
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
        adv = QHBoxLayout(self.advanced_box)
        self.adv_splitter = QSplitter(Qt.Orientation.Horizontal)
        adv.addWidget(self.adv_splitter)
        self.picker = FeaturePicker()
        self.picker.selection_changed.connect(self._selection_changed)
        self.picker.current_changed.connect(self._show_params)
        self.adv_splitter.addWidget(self.picker)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.params = ParamsPanel()
        right_layout.addWidget(self.params, 1)
        self.save_btn = QPushButton("Uložit jako protokol…")
        self.save_btn.setToolTip("Uloží aktuální výběr feature a parametry jako nový protokol")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        right_layout.addWidget(self.save_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.adv_splitter.addWidget(right)
        self.adv_splitter.setSizes([560, 320])
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
        self._rebuild_picker()
        self._update_run_state()

    def set_doctor(self, report: dict[str, Any] | None) -> None:
        """Stav providerů z `doctor --json`, kvůli varování o chybějících modelech."""
        self._doctor = report
        self._update_summary()

    def set_stats_lookup(self, lookup: Callable[[str], float | None]) -> None:
        """Doba na nahrávku z minulého běhu protokolu podle jeho slugu."""
        self._seconds_per_file = lookup
        self._update_summary()

    def refresh_summary(self) -> None:
        self._update_summary()

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

    # --- protokol a výběr feature ----------------------------------------------------

    def _protocol_changed(self) -> None:
        proto = self.current_protocol()
        self.protocol_desc.setText(proto.description if proto else "")
        self._overrides = {k: dict(v) for k, v in (proto.config if proto else {}).items()}
        self._param_forms.clear()
        self.params.show_params("", None)
        self._rebuild_picker()
        self._update_run_state()

    def _catalog(self) -> list:
        proto = self.current_protocol()
        if self._library is None or proto is None:
            return []
        try:
            return self._library.features(proto.task)
        except LibraryError as exc:
            self.picker.set_warning(f"Seznam feature nejde načíst: {exc}")
            return []

    def _rebuild_picker(self) -> None:
        proto = self.current_protocol()
        catalog = self._catalog()
        if proto is None or not catalog:
            self.picker.set_features([], set())
            return
        self.picker.set_features(catalog, set(proto.select([f.name for f in catalog])))
        self._update_summary()

    def _selection_changed(self) -> None:
        self._update_summary()
        self._update_run_state()

    def _update_summary(self) -> None:
        proto = self.current_protocol()
        catalog = self._catalog()
        if proto is None or not catalog:
            return
        providers = None
        if self._library is not None:
            try:
                providers = self._library.providers()
            except LibraryError:
                providers = None
        summary = summarize(self.picker.selected(), catalog, providers)
        hint = summary.cost_hint()
        seconds = self._seconds_per_file(proto.slug())
        if seconds:
            hint = f"naposledy {seconds:.0f} s na nahrávku"
        self.picker.set_summary(summary.providers, summary.columns, hint)
        self.picker.set_warning(self._missing_models(summary.providers))

    def _missing_models(self, providers: list[str]) -> str:
        if not self._doctor:
            return ""
        state = self._doctor.get("providers", {})
        missing = [
            contract.PROVIDER_LABELS.get(p, p)
            for p in providers
            if p in state and not state[p].get("ready")
        ]
        if not missing:
            return ""
        return (
            "Není připraveno: "
            + ", ".join(missing)
            + " (viz Prostředí). Dotčené sloupce zůstanou prázdné."
        )

    def _show_params(self, name: str) -> None:
        if not name or self._library is None:
            self.params.show_params("", None)
            return
        form = self._param_forms.get(name)
        if form is None:
            try:
                params = self._library.params(name).params
            except LibraryError as exc:
                self.params.show_params(name, None, str(exc))
                return
            form = ParamForm(params)
            form.set_values(self._overrides.get(name, {}))
            form.changed.connect(
                lambda n=name, f=form: self._overrides.__setitem__(n, f.overrides())
            )
            self._param_forms[name] = form
        self.params.show_params(name, form)

    # --- spuštění -------------------------------------------------------------

    def _update_run_state(self) -> None:
        proto = self.current_protocol()
        ok = bool(self._recordings) and proto is not None and self._library is not None
        self.run_btn.setEnabled(ok)
        self.save_btn.setEnabled(proto is not None and self.picker.count() > 0)
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
        base = self.current_protocol()
        if base is None:
            return None
        proto = Protocol(
            name=base.name,
            task=base.task,
            description=base.description,
            features=list(base.features),
            domain=base.domain,
            vad=base.vad,
            config={k: dict(v) for k, v in self._overrides.items() if v},
        )
        if self.advanced_box.isVisible() and self.picker.count():
            proto.features = self.picker.selected()
            proto.domain = None
        return proto

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
