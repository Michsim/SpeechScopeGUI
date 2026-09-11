"""Stránka Protokoly: přehled, kopie, import, export, smazání a úprava.

Přibalené protokoly jsou jen ke čtení, „Vytvořit kopii“ z nich udělá
vlastní. Vlastní protokol jde v rozšířeném režimu upravit stejným
editorem jako na Datech (výběr feature, parametry) a přejmenovat; v obou
režimech jde importovat, exportovat a smazat, aby klinik mohl přinést
protokol od výzkumníka bez zapínání rozšířeného režimu.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.library import Library, LibraryError
from ...backend.protocol import (
    Protocol,
    describe,
    import_protocol,
    unique_name,
)
from ...i18n import tr
from .. import theme
from ..widgets.protocol_editor import ProtocolEditorDialog

ROLE_PROTO = Qt.ItemDataRole.UserRole
YAML_FILTER = "Protokol SpeechScope (*.yaml)"


class ProtocolsPage(QWidget):
    protocols_changed = Signal()  # něco se uložilo, smazalo nebo importovalo

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self._protocols: list[Protocol] = []
        self._folder: Path | None = None
        self._advanced = False
        self._dirty = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel(tr("Protokoly"))
        title.setObjectName("page_title")
        head.addWidget(title, 1)
        self.copy_btn = QPushButton(tr("Vytvořit kopii"))
        self.copy_btn.setToolTip(tr("Nový vlastní protokol podle vybraného"))
        self.copy_btn.clicked.connect(self.copy_current)
        self.import_btn = QPushButton(tr("Import…"))
        self.import_btn.setToolTip(tr("Přidat protokol ze souboru YAML, třeba od kolegy"))
        self.import_btn.clicked.connect(self.import_file)
        self.folder_btn = QPushButton(tr("Otevřít složku"))
        self.folder_btn.setToolTip(tr("Složka s vlastními protokoly v Průzkumníku"))
        self.folder_btn.clicked.connect(self.open_folder)
        for btn in (self.copy_btn, self.import_btn, self.folder_btn):
            head.addWidget(btn)
        layout.addLayout(head)
        subtitle = QLabel(
            tr(
                "Protokol je pojmenované nastavení dávky: úloha, výběr feature a parametry. "
                "Přibalené protokoly se nemění, vlastní jdou upravit, poslat kolegovi nebo smazat."
            )
        )
        subtitle.setObjectName("page_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)

        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.currentItemChanged.connect(self._current_changed)
        self.splitter.addWidget(self.list)

        right = QWidget()
        detail = QVBoxLayout(right)
        detail.setContentsMargins(12, 0, 0, 0)
        detail.setSpacing(8)
        name_row = QHBoxLayout()
        self.name_label = QLabel("")
        self.name_label.setObjectName("headline")
        self.origin = QLabel("")
        self.origin.setObjectName("pill")
        name_row.addWidget(self.name_label, 1)
        name_row.addWidget(self.origin)
        detail.addLayout(name_row)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        detail.addWidget(self.summary)

        self.edit_box = QWidget()
        form = QFormLayout(self.edit_box)
        form.setContentsMargins(0, 0, 0, 0)
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._mark_dirty)
        form.addRow(tr("Jméno:"), self.name_edit)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setFixedHeight(56)
        self.desc_edit.textChanged.connect(self._mark_dirty)
        form.addRow(tr("Popis:"), self.desc_edit)
        detail.addWidget(self.edit_box)
        self.editor_dialog = ProtocolEditorDialog(self)
        self.editor = self.editor_dialog.editor
        self.editor.changed.connect(self._mark_dirty)
        self.edit_btn = QPushButton(tr("Upravit feature a parametry…"))
        self.edit_btn.clicked.connect(self.open_editor)
        detail.addWidget(self.edit_btn, 0, Qt.AlignmentFlag.AlignLeft)
        detail.addStretch(1)

        actions = QHBoxLayout()
        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        actions.addWidget(self.note, 1)
        self.export_btn = QPushButton(tr("Export…"))
        self.export_btn.clicked.connect(self.export_current)
        self.delete_btn = QPushButton(tr("Smazat"))
        self.delete_btn.clicked.connect(self.delete_current)
        self.save_btn = QPushButton(tr("Uložit"))
        theme.set_role(self.save_btn, "primary")
        self.save_btn.clicked.connect(self.save_current)
        for btn in (self.export_btn, self.delete_btn, self.save_btn):
            actions.addWidget(btn)
        detail.addLayout(actions)
        self.splitter.addWidget(right)
        self.splitter.setSizes([230, 770])
        self._show(None)

    # --- nastavení zvenku -----------------------------------------------------

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self.editor.set_library(library)
        self._show(self.current())

    def set_doctor(self, report: dict[str, Any] | None) -> None:
        self.editor.set_doctor(report)

    def set_stats_lookup(self, lookup: Callable[[str], float | None]) -> None:
        self.editor.set_stats_lookup(lookup)

    def set_advanced(self, advanced: bool) -> None:
        self._advanced = advanced
        self._show(self.current())

    def set_protocols(self, protocols: list[Protocol], folder: Path, *, current: str = "") -> None:
        self._protocols = list(protocols)
        self._folder = folder
        self.list.blockSignals(True)
        self.list.clear()
        chosen: QListWidgetItem | None = None
        order = {t: i for i, t in enumerate(contract.TASKS)}
        for builtin, header in ((True, tr("Přibalené")), (False, tr("Vlastní"))):
            group = sorted(
                (p for p in protocols if p.builtin == builtin),
                key=lambda p: (order.get(p.task, len(order)), p.name),
            )
            if not group and builtin:
                continue
            head = QListWidgetItem(header)
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            self.list.addItem(head)
            if not group:
                hint = QListWidgetItem(tr("  zatím žádné"))
                hint.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list.addItem(hint)
            for proto in group:
                item = QListWidgetItem("  " + proto.display_name)
                item.setData(ROLE_PROTO, proto)
                item.setToolTip(str(proto.path) if proto.path else tr("přibalený k aplikaci"))
                self.list.addItem(item)
                if proto.name == current or chosen is None:
                    chosen = item
        self.list.blockSignals(False)
        self.list.setCurrentItem(chosen)
        if chosen is None:
            self._show(None)

    def current(self) -> Protocol | None:
        item = self.list.currentItem()
        return item.data(ROLE_PROTO) if item is not None else None

    def current_name(self) -> str:
        proto = self.current()
        return proto.name if proto else ""

    def select(self, name: str) -> bool:
        for row in range(self.list.count()):
            item = self.list.item(row)
            proto = item.data(ROLE_PROTO)
            if proto is not None and proto.name == name:
                self.list.setCurrentItem(item)
                return True
        return False

    # --- detail ---------------------------------------------------------------

    def _current_changed(self, item: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        self._show(item.data(ROLE_PROTO) if item is not None else None)

    def _show(self, proto: Protocol | None) -> None:
        self._dirty = False
        editable = proto is not None and not proto.builtin and self._advanced
        self.edit_box.setVisible(editable)
        self.edit_btn.setVisible(editable)
        self.summary.setVisible(proto is not None)
        self.copy_btn.setEnabled(proto is not None)
        self.export_btn.setEnabled(proto is not None)
        self.delete_btn.setEnabled(proto is not None and not proto.builtin)
        self.save_btn.setVisible(editable)
        self.save_btn.setEnabled(False)
        if proto is None:
            self.name_label.setText(tr("Žádný protokol"))
            self.origin.setVisible(False)
            self.summary.setText("")
            self.note.setText("")
            self.editor.set_protocol(None)
            return
        self.name_label.setText(proto.display_name)
        self.origin.setVisible(True)
        self.origin.setText(tr("přibalený") if proto.builtin else tr("vlastní"))
        theme.set_role(self.origin, "neutral" if proto.builtin else "accent")
        self.summary.setText(self._summary_html(proto))
        if editable:
            self.name_edit.blockSignals(True)
            self.desc_edit.blockSignals(True)
            self.name_edit.setText(proto.name)
            self.desc_edit.setPlainText(proto.description)
            self.name_edit.blockSignals(False)
            self.desc_edit.blockSignals(False)
            self.editor.set_protocol(proto)
            self.note.setText("")
        else:
            self.editor.set_protocol(None)
            if proto.builtin:
                self.note.setText(tr("Přibalený protokol se nemění. Uprav si jeho kopii."))
            elif not self._advanced:
                self.note.setText(
                    tr("Úprava feature a parametrů je v rozšířeném režimu (Nastavení).")
                )
            else:
                self.note.setText("")

    def _summary_html(self, proto: Protocol) -> str:
        catalog: list = []
        providers = None
        if self._library is not None:
            try:
                catalog = self._library.features()
                providers = self._library.providers()
            except LibraryError:
                catalog = []
        info = describe(proto, catalog, providers)
        rows = [
            (tr("Úloha"), contract.TASK_LABELS.get(proto.task, proto.task)),
            (tr("Popis"), proto.display_description or "–"),
            (
                tr("Spustí se"),
                ", ".join(contract.PROVIDER_LABELS.get(p, p) for p in info.providers)
                or tr("nic, jen akustika bez modelů"),
            ),
            (tr("Feature"), info.features_text),
            (tr("Změněné parametry"), info.params_text or tr("žádné, výchozí z knihovny")),
        ]
        if proto.path:
            rows.append((tr("Soubor"), str(proto.path)))
        return (
            "<table cellspacing='0' cellpadding='3'>"
            + "".join(
                f"<tr><td style='color:{theme.MUTED}'>{k}:&nbsp;</td><td>{v}</td></tr>"
                for k, v in rows
            )
            + "</table>"
        )

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.save_btn.setEnabled(True)

    def open_editor(self) -> None:
        base = self.current()
        if base is None or base.builtin:
            return
        if self.editor_dialog.open_for(f"{self.name_edit.text().strip() or base.name}"):
            edited = self._edited()
            if edited is not None:
                self.summary.setText(self._summary_html(edited))
            self._mark_dirty()

    # --- akce ------------------------------------------------------------------

    def _edited(self) -> Protocol | None:
        base = self.current()
        if base is None or base.builtin:
            return None
        proto = self.editor.result() or Protocol(
            name=base.name,
            task=base.task,
            description=base.description,
            features=list(base.features),
            domain=base.domain,
            vad=base.vad,
            config={k: dict(v) for k, v in base.config.items()},
        )
        proto.name = self.name_edit.text().strip() or base.name
        proto.description = self.desc_edit.toPlainText().strip()
        proto.path = base.path
        return proto

    def save_current(self) -> None:
        base = self.current()
        proto = self._edited()
        if base is None or proto is None or self._folder is None:
            return
        taken = {p.name for p in self._protocols if p.name != base.name}
        if proto.name in taken:
            QMessageBox.warning(
                self,
                tr("SpeechScope"),
                tr("Protokol „{name}“ už existuje.").format(name=proto.name),
            )
            return
        path = base.path or self._folder / f"{proto.slug()}.yaml"
        proto.save(path)
        self.note.setText(tr("Uloženo do {path}").format(path=path))
        self._dirty = False
        self._reload(proto.name)

    def copy_current(self) -> None:
        base = self.current()
        if base is None or self._folder is None:
            return
        name = unique_name(
            tr("{name} (kopie)").format(name=base.name), {p.name for p in self._protocols}
        )
        proto = Protocol(
            name=name,
            task=base.task,
            description=base.description,
            features=list(base.features),
            domain=base.domain,
            vad=base.vad,
            config={k: dict(v) for k, v in base.config.items()},
        )
        proto.save(self._folder / f"{proto.slug()}.yaml")
        self._reload(name)

    def delete_current(self) -> None:
        proto = self.current()
        if proto is None or proto.builtin or proto.path is None:
            return
        answer = QMessageBox.question(
            self,
            tr("Smazat protokol"),
            tr("Opravdu smazat vlastní protokol „{name}“?\n{path}").format(
                name=proto.display_name, path=proto.path
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        proto.path.unlink(missing_ok=True)
        self._reload("")

    def export_current(self) -> None:
        proto = self.current()
        if proto is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, tr("Exportovat protokol"), f"{proto.slug()}.yaml", YAML_FILTER
        )
        if target:
            self.export_to(Path(target))

    def export_to(self, target: Path) -> None:
        proto = self.current()
        if proto is None:
            return
        copy = Protocol.from_dict(proto.to_dict())
        copy.save(target)
        self.note.setText(tr("Exportováno do {path}").format(path=target))

    def import_file(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, tr("Importovat protokol"), "", YAML_FILTER)
        if source:
            self.import_from(Path(source))

    def import_from(self, source: Path) -> None:
        if self._folder is None:
            return
        try:
            proto = Protocol.load(source)
        except Exception as exc:  # rozbitý YAML, chybějící pole, cizí úloha
            QMessageBox.warning(
                self,
                tr("SpeechScope"),
                tr("Soubor není platný protokol: {error}").format(error=exc),
            )
            return
        taken = {p.name for p in self._protocols}
        overwrite = False
        if proto.name in taken:
            existing = next(p for p in self._protocols if p.name == proto.name)
            if existing.builtin:
                proto.name = unique_name(proto.name, taken)
            else:
                answer = QMessageBox.question(
                    self,
                    tr("Protokol už existuje"),
                    tr(
                        "Vlastní protokol „{name}“ už existuje. Přepsat ho?\n"
                        "Ne = uložit vedle pod jiným jménem."
                    ).format(name=proto.name),
                )
                overwrite = answer == QMessageBox.StandardButton.Yes
                if not overwrite:
                    proto.name = unique_name(proto.name, taken)
        path = import_protocol(proto, self._folder, self._protocols, overwrite=overwrite)
        self.note.setText(tr("Importováno do {path}").format(path=path))
        self._reload(proto.name)

    def open_folder(self) -> None:
        if self._folder is None:
            return
        self._folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder)))

    def _reload(self, select: str) -> None:
        """Hlavní okno v obsluze signálu znovu načte protokoly do všech stránek."""
        self.protocols_changed.emit()
        if select:
            self.select(select)
