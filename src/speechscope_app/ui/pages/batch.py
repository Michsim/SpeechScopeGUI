"""Stránka Data: složka, protokol, nalezené nahrávky, spuštění.

Základní režim ukazuje jen složku a protokol. Rozšířený režim přidává
strom feature a parametry vybrané feature.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.discover import Recording, find_recordings
from ...backend.library import Library, LibraryError
from ...backend.protocol import Protocol
from .. import theme
from ..protocol_dialog import SaveProtocolDialog
from ..widgets.param_form import ParamForm


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

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)

        self.files = QTableWidget()
        self.files.setColumnCount(3)
        self.files.setHorizontalHeaderLabels(["nahrávka", "ruční labely", "ruční přepis"])
        self.files.horizontalHeader().setStretchLastSection(True)
        self.splitter.addWidget(self.files)

        self.advanced_box = QGroupBox("Rozšířené")
        adv = QVBoxLayout(self.advanced_box)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["feature", "potřebuje"])
        tree_header = self.tree.header()
        tree_header.setStretchLastSection(False)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._tree_changed)
        self.tree.currentItemChanged.connect(self._show_params)
        adv.addWidget(self.tree, 2)
        self.params_host = QVBoxLayout()
        adv.addLayout(self.params_host, 1)
        self.providers_label = QLabel("")
        self.providers_label.setWordWrap(True)
        adv.addWidget(self.providers_label)
        self.save_btn = QPushButton("Uložit jako protokol…")
        self.save_btn.setToolTip("Uloží aktuální výběr feature a parametry jako nový protokol")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        adv.addWidget(self.save_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.splitter.addWidget(self.advanced_box)
        self.splitter.setSizes([500, 400])

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
        self._rebuild_tree()
        self._update_run_state()

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
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(str(rec.path))
                self.files.setItem(r, c, item)
        self.files.resizeColumnsToContents()
        self._update_run_state()

    # --- protokol a strom -------------------------------------------------------

    def _protocol_changed(self) -> None:
        proto = self.current_protocol()
        self.protocol_desc.setText(proto.description if proto else "")
        self._overrides = {k: dict(v) for k, v in (proto.config if proto else {}).items()}
        self._rebuild_tree()
        self._update_run_state()

    def _rebuild_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._param_forms.clear()
        proto = self.current_protocol()
        if self._library is None or proto is None:
            self.tree.blockSignals(False)
            return
        try:
            features = self._library.features(proto.task)
        except LibraryError as exc:
            self.providers_label.setText(f"Seznam feature nejde načíst: {exc}")
            self.tree.blockSignals(False)
            return

        selected = self._selected_names(proto, [f.name for f in features])
        groups: dict[str, QTreeWidgetItem] = {}
        for domain in contract.DOMAINS:
            dom_item = QTreeWidgetItem([contract.DOMAIN_LABELS[domain], ""])
            dom_item.setFlags(dom_item.flags() | Qt.ItemFlag.ItemIsAutoTristate)
            self.tree.addTopLevelItem(dom_item)
            groups[domain] = dom_item
        for f in features:
            parent = groups[f.domain]
            item = QTreeWidgetItem(parent, [f.name, ", ".join(f.requires)])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0, Qt.CheckState.Checked if f.name in selected else Qt.CheckState.Unchecked
            )
            item.setToolTip(
                0, f.description or "\n".join(f"{k}: {v}" for k, v in f.outputs.items())
            )
            item.setData(0, Qt.ItemDataRole.UserRole, f.name)
        for provider in self._library.providers():
            label = contract.PROVIDER_LABELS.get(provider.name, provider.name)
            item = QTreeWidgetItem(self.tree, [f"[{label}]", ""])
            item.setData(0, Qt.ItemDataRole.UserRole, provider.name)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._update_providers()

    @staticmethod
    def _selected_names(proto: Protocol, available: list[str]) -> set[str]:
        import fnmatch

        pool = [n for n in available if not proto.domain or n.startswith(proto.domain + ".")]
        if not proto.features:
            return set(pool)
        out: set[str] = set()
        for pat in proto.features:
            out.update(n for n in pool if fnmatch.fnmatchcase(n, pat))
        return out

    def _checked_features(self) -> list[str]:
        out: list[str] = []
        for d in range(self.tree.topLevelItemCount()):
            dom = self.tree.topLevelItem(d)
            for i in range(dom.childCount()):
                child = dom.child(i)
                if child.checkState(0) == Qt.CheckState.Checked:
                    out.append(child.data(0, Qt.ItemDataRole.UserRole))
        return out

    def _tree_changed(self, _item: QTreeWidgetItem, _col: int) -> None:
        self._update_providers()
        self._update_run_state()

    def _update_providers(self) -> None:
        if self._library is None:
            return
        proto = self.current_protocol()
        if proto is None:
            return
        chosen = set(self._checked_features())
        needed: set[str] = set()
        for f in self._library.features(proto.task):
            if f.name in chosen:
                needed.update(f.requires)
        if proto.vad or (needed and "segments" not in needed and proto.task != "phonation"):
            pass  # VAD se řeší per feature v knihovně; tady jen informujeme
        names = [contract.PROVIDER_LABELS.get(p, p) for p in sorted(needed)]
        self.providers_label.setText(
            "Spustí se: " + (", ".join(names) if names else "jen akustika bez modelů")
        )

    def _show_params(self, item: QTreeWidgetItem | None, _prev: QTreeWidgetItem | None) -> None:
        while self.params_host.count():
            w = self.params_host.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
        if item is None or self._library is None:
            return
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if not name:
            return
        form = self._param_forms.get(name)
        if form is None:
            try:
                params = self._library.params(name).params
            except LibraryError as exc:
                self.params_host.addWidget(QLabel(str(exc)))
                return
            form = ParamForm(params)
            form.set_values(self._overrides.get(name, {}))
            form.changed.connect(
                lambda n=name, f=form: self._overrides.__setitem__(n, f.overrides())
            )
            self._param_forms[name] = form
        self.params_host.addWidget(QLabel(f"<b>{name}</b>"))
        self.params_host.addWidget(form)

    # --- spuštění -------------------------------------------------------------

    def _update_run_state(self) -> None:
        proto = self.current_protocol()
        ok = bool(self._recordings) and proto is not None and self._library is not None
        self.run_btn.setEnabled(ok)
        self.save_btn.setEnabled(proto is not None and self.tree.topLevelItemCount() > 0)
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
        if self.advanced_box.isVisible() and self.tree.topLevelItemCount():
            proto.features = self._checked_features()
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
