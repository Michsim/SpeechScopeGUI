"""Stránka Analýza: složka, protokol, nalezené nahrávky, spuštění.

Základní režim ukazuje jen složku, protokol a nahrávky. Rozšířený režim
přidává pod nahrávky editor protokolu (výběr feature a parametry),
kterým výzkumník upraví protokol pro tento běh nebo ho uloží jako nový.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.discover import Recording, find_recordings
from ...backend.library import Library, LibraryError
from ...backend.manifest import Manifest, find_manifest, read_manifest
from ...backend.protocol import Protocol, card_infos, summarize
from ...i18n import tr
from .. import theme
from ..prepare_dialog import SegmentDialog, TranscribeDialog
from ..protocol_dialog import SaveProtocolDialog
from ..widgets.protocol_editor import ProtocolEditorDialog
from ..widgets.protocol_list import ProtocolCardInfo, ProtocolList


def _step(number: int, title: str, hint: str = "") -> tuple[QHBoxLayout, QLabel]:
    """Nadpis kroku: číslo v kroužku, název, šedá nápověda vpravo."""
    row = QHBoxLayout()
    row.setSpacing(10)
    no = QLabel(str(number))
    no.setObjectName("step_no")
    label = QLabel(title)
    label.setObjectName("step_title")
    hint_label = QLabel(hint)
    hint_label.setObjectName("step_hint")
    row.addWidget(no)
    row.addWidget(label)
    row.addWidget(hint_label, 1)
    return row, hint_label


class BatchPage(QWidget):
    run_requested = Signal(object, list)  # Protocol, list[Path]
    save_requested = Signal(object)  # Protocol upravený v rozšířeném režimu
    # "segments" | "transcript", Protocol, cesty, volby z dialogu (model, cut_audio, language)
    prepare_requested = Signal(str, object, list, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self._protocols: list[Protocol] = []
        self._recordings: list[Recording] = []
        self._manifest: Manifest | None = None
        self._doctor: dict[str, Any] | None = None
        self._advanced = False
        self._manifest_auto = True  # manifest nalezený ve složce, ne vybraný ručně
        self._seconds_per_file: Callable[[str], float | None] = lambda _slug: None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        title = QLabel(tr("Analýza"))
        title.setObjectName("page_title")
        layout.addWidget(title)
        subtitle = QLabel(
            tr(
                "Vlevo co se analyzuje (nahrávky, úloha, jazyk), vpravo jak (protokol). "
                "Do složky s nahrávkami se nic nezapisuje, výsledky jdou do Dokumentů."
            )
        )
        subtitle.setObjectName("page_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        layout.addLayout(columns, 1)

        # --- levý sloupec: vstup --------------------------------------------------
        left = QFrame()
        left.setObjectName("card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 14, 16, 14)
        left_layout.setSpacing(8)
        columns.addWidget(left, 2)

        step1, self.step1_hint = _step(1, tr("Nahrávky"), tr("Vyber složku."))
        left_layout.addLayout(step1)
        folder_row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText(tr("Složka s nahrávkami"))
        self.folder.editingFinished.connect(self.rescan)
        browse = QPushButton(tr("Vybrat…"))
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        left_layout.addLayout(folder_row)
        self.recursive = QCheckBox(tr("včetně podsložek"))
        self.recursive.setChecked(True)
        self.recursive.toggled.connect(self.rescan)
        left_layout.addWidget(self.recursive)
        self.files = QTableWidget()
        self.files.setColumnCount(3)
        self.files.setHorizontalHeaderLabels([tr("nahrávka")])
        # Název vyplní zbylé místo, ostatní sloupce podle obsahu; šířka pak
        # neposkakuje při přeskenování (podsložky, jiná složka, metadata).
        self.files.horizontalHeader().setStretchLastSection(False)
        self.files.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.files.verticalHeader().setVisible(False)
        self.files.verticalHeader().setDefaultSectionSize(24)
        left_layout.addWidget(self.files, 1)
        meta_row = QHBoxLayout()
        self.manifest_label = QLabel("")
        self.manifest_label.setObjectName("muted")
        self.manifest_label.setWordWrap(True)
        self.manifest_btn = QPushButton(tr("Metadata…"))
        self.manifest_btn.setToolTip(
            tr(
                "CSV s metadaty nahrávek (pacient, skupina, návštěva). Sloupec „path“ "
                "s názvem souboru, ostatní sloupce se propíší do výsledků. "
                "Soubor manifest.csv ve složce se načte sám."
            )
        )
        self.manifest_btn.clicked.connect(self._browse_manifest)
        self.manifest_clear_btn = QPushButton(tr("Nepoužít"))
        self.manifest_clear_btn.setToolTip(tr("Nepoužívat metadata"))
        self.manifest_clear_btn.clicked.connect(self.clear_manifest)
        self.manifest_clear_btn.hide()
        meta_row.addWidget(self.manifest_label, 1)
        meta_row.addWidget(self.manifest_btn)
        meta_row.addWidget(self.manifest_clear_btn)
        left_layout.addLayout(meta_row)

        step2, self.step2_hint = _step(2, tr("Úloha a jazyk"), "")
        left_layout.addLayout(step2)
        self.protocols = ProtocolList()
        self.protocols.current_changed.connect(self._protocol_changed)
        left_layout.addWidget(self.protocols.task_bar)
        lang_row = QHBoxLayout()
        self.language = QComboBox()
        self.language.setToolTip(
            tr(
                "Jazyk přepisu (Whisper) a jazykového rozboru (Stanza) pro tento běh. "
                "Přebije nastavení protokolu."
            )
        )
        self.language.currentIndexChanged.connect(self._language_changed)
        self.set_languages(list(contract.DEFAULT_LANGUAGES))
        lang_row.addWidget(QLabel(tr("Jazyk nahrávek:")))
        lang_row.addWidget(self.language, 1)
        left_layout.addLayout(lang_row)

        # --- pravý sloupec: protokol --------------------------------------------
        right = QFrame()
        right.setObjectName("card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(8)
        columns.addWidget(right, 3)

        step3, self.step3_hint = _step(3, tr("Protokol"), tr("Co se má z nahrávek spočítat."))
        right_layout.addLayout(step3)
        right_layout.addWidget(self.protocols, 1)

        # rozšířený režim: souhrn výběru a tlačítko do okna editoru
        self.editor_dialog = ProtocolEditorDialog(self)
        self.editor = self.editor_dialog.editor
        self.editor.changed.connect(self._editor_changed)
        self.advanced_box = QGroupBox(tr("Feature a parametry"))
        adv = QVBoxLayout(self.advanced_box)
        self.editor_summary = QLabel("")
        self.editor_summary.setWordWrap(True)
        adv.addWidget(self.editor_summary)
        adv_buttons = QHBoxLayout()
        self.segment_btn = QPushButton(tr("Jen segmentace"))
        self.segment_btn.setToolTip(
            tr(
                "Spustí jen segmentaci řeči do pracovní složky (model z protokolu, "
                "výchozí conformer). Výpočet feature ji pak vezme z cache."
            )
        )
        self.segment_btn.setEnabled(False)
        self.segment_btn.clicked.connect(lambda: self._prepare("segments"))
        adv_buttons.addWidget(self.segment_btn)
        self.transcribe_btn = QPushButton(tr("Jen přepis"))
        self.transcribe_btn.setToolTip(
            tr(
                "Spustí jen přepis Whisperem do pracovní složky, v jazyce z lišty. "
                "Přepisy jde před výpočtem ručně zkontrolovat."
            )
        )
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.clicked.connect(lambda: self._prepare("transcript"))
        adv_buttons.addWidget(self.transcribe_btn)
        adv_buttons.addStretch(1)
        self.edit_btn = QPushButton(tr("Upravit…"))
        self.edit_btn.setToolTip(tr("Výběr feature a parametry jen pro tento běh"))
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self.open_editor)
        adv_buttons.addWidget(self.edit_btn)
        self.save_btn = QPushButton(tr("Uložit jako protokol…"))
        self.save_btn.setToolTip(tr("Uloží aktuální výběr feature a parametry jako nový protokol"))
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        adv_buttons.addWidget(self.save_btn)
        adv.addLayout(adv_buttons)
        right_layout.addWidget(self.advanced_box)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.run_btn = QPushButton(tr("Spustit"))
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
        self._doctor = report
        self.editor.set_doctor(report)
        stanza = ((report or {}).get("models") or {}).get("stanza") or {}
        languages = [str(code) for code in stanza.get("languages") or []]
        if languages:
            self.set_languages(languages)

    # --- jazyk ------------------------------------------------------------------------

    def set_languages(self, codes: list[str]) -> None:
        """Nabídka jazyků; zachová vybraný, pokud v ní zůstal."""
        current = self.language_code()
        self.language.blockSignals(True)
        self.language.clear()
        for code in codes:
            self.language.addItem(contract.language_label(code), code)
        idx = self.language.findData(current)
        self.language.setCurrentIndex(idx if idx >= 0 else 0)
        self.language.blockSignals(False)
        if hasattr(self, "run_btn"):  # při stavbě stránky ještě tlačítka nejsou
            self._update_run_state()

    def language_code(self) -> str:
        return str(self.language.currentData() or "")

    def set_language(self, code: str) -> None:
        idx = self.language.findData(code)
        if idx >= 0:
            self.language.setCurrentIndex(idx)

    def _language_changed(self, _idx: int) -> None:
        self._push_language()
        self._update_run_state()

    def _push_language(self) -> None:
        """Jazyk z lišty do editoru, ať ho výzkumník vidí i v parametrech."""
        code = self.language_code()
        if code and self.editor.protocol() is not None:
            for provider in ("transcript", "nlp"):
                self.editor.set_override(provider, "language", code)

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
        self._advanced = advanced
        self.advanced_box.setVisible(advanced)
        self._fill_files()

    def set_folder(self, folder: Path) -> None:
        self.folder.setText(str(folder))
        self.rescan()

    def current_protocol(self) -> Protocol | None:
        return self.protocols.current()

    # --- složka ---------------------------------------------------------------

    def _browse(self) -> None:
        start = self.folder.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, tr("Složka s nahrávkami"), start)
        if chosen:
            self.set_folder(Path(chosen))

    def rescan(self) -> None:
        text = self.folder.text().strip()
        self._recordings = (
            find_recordings(Path(text), recursive=self.recursive.isChecked()) if text else []
        )
        n = len(self._recordings)
        self.step1_hint.setText(
            tr("Vyber složku.")
            if not text
            else (tr("{n} nahrávek").format(n=n) if n else tr("žádná nenalezena"))
        )
        if self._manifest_auto:
            found = find_manifest(Path(text)) if text else None
            self._manifest = read_manifest(found, Path(text)) if found else None
        self._fill_files()
        self._update_run_state()

    # --- metadata (manifest) ------------------------------------------------------------

    def manifest(self) -> Manifest | None:
        """Manifest, který se předá knihovně; `None` = bez metadat."""
        return self._manifest if self._manifest and not self._manifest.problems else None

    def set_manifest(self, path: Path | None) -> None:
        """Ručně vybraný manifest; relativní cesty se berou od složky s nahrávkami."""
        base = Path(self.folder.text().strip()) if self.folder.text().strip() else None
        self._manifest = read_manifest(path, base) if path else None
        self._manifest_auto = path is None
        self._fill_files()
        self._update_run_state()

    def clear_manifest(self) -> None:
        self._manifest = None
        self._manifest_auto = False
        self._fill_files()
        self._update_run_state()

    def _browse_manifest(self) -> None:
        start = self.folder.text() or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(
            self, tr("Metadata nahrávek"), start, tr("Tabulka CSV (*.csv);;Všechny soubory (*)")
        )
        if chosen:
            self.set_manifest(Path(chosen))

    def _fill_files(self) -> None:
        manifest = self._manifest
        columns = list(manifest.columns) if manifest else []
        # Ruční vstupy (labely, přepis vedle nahrávky) zajímají výzkumníka,
        # klinik je nikdy nemá; v základním režimu se sloupec neukazuje.
        headers = [tr("nahrávka")]
        if self._advanced:
            headers.append(tr("ruční vstupy"))
        headers.extend(columns)
        meta_from = len(headers) - len(columns)
        self.files.setColumnCount(len(headers))
        self.files.setHorizontalHeaderLabels(headers)
        self.files.setRowCount(len(self._recordings))
        for r, rec in enumerate(self._recordings):
            meta = manifest.meta_for(rec.path) if manifest else None
            values = [rec.name]
            if self._advanced:
                manual = [tr("labely")] * rec.has_labels + [tr("přepis")] * rec.has_transcript
                values.append(", ".join(manual))
            values.extend((meta or {}).get(c, "") for c in columns)
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(rec.path))
                if self._advanced and c == 1 and value:
                    item.setToolTip(
                        tr(
                            "Ruční vstupy vedle nahrávky mají přednost před modely: "
                            "`.labels.txt` nahradí segmentaci, `.txt` přepis Whisperem."
                        )
                    )
                if c >= meta_from and meta is None:
                    item.setForeground(QColor(theme.MUTED))
                self.files.setItem(r, c, item)
        header = self.files.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(headers)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.manifest_clear_btn.setVisible(manifest is not None)
        if manifest is None:
            self.manifest_label.setText(
                tr("Bez metadat. Do výsledků jde jen název souboru.") if self._recordings else ""
            )
            self.manifest_label.setStyleSheet("")
            return
        if manifest.problems:
            self.manifest_label.setText(
                tr("{name}: {problem}").format(
                    name=manifest.path.name, problem=manifest.problems[0]
                )
            )
            self.manifest_label.setStyleSheet(f"color: {theme.MISSING};")
            return
        paths = [rec.path for rec in self._recordings]
        matched = manifest.matched(paths)
        self.manifest_label.setText(
            tr("{name}: {columns} · {matched} z {total}").format(
                name=manifest.path.name,
                columns=", ".join(columns) or tr("bez sloupců"),
                matched=matched,
                total=len(paths),
            )
        )
        self.manifest_label.setStyleSheet(
            f"color: {theme.WARN};" if matched < len(paths) else f"color: {theme.OK};"
        )

    # --- protokol -------------------------------------------------------------------

    def _protocol_changed(self, proto: Protocol | None) -> None:
        self.editor.set_protocol(proto)
        self._push_language()
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
        self.editor_dialog.open_for(
            tr("{name} · úprava jen pro tento běh").format(name=proto.display_name)
        )
        self._editor_changed()

    def _update_editor_summary(self) -> None:
        """Krátký souhrn výběru do rámečku na Datech (celý je v okně editoru)."""
        if not self.editor.has_catalog():
            self.editor_summary.setText(tr("Seznam feature není k dispozici."))
            return
        text = self.editor.picker.summary.text()
        self.editor_summary.setText(text)

    # --- spuštění -------------------------------------------------------------

    def _update_run_state(self) -> None:
        proto = self.current_protocol()
        ok = bool(self._recordings) and proto is not None and self._library is not None
        self.run_btn.setEnabled(ok)
        self.segment_btn.setEnabled(ok)
        self.transcribe_btn.setEnabled(ok)
        self.save_btn.setEnabled(proto is not None and self.editor.has_catalog())
        self.edit_btn.setEnabled(proto is not None and self.editor.has_catalog())
        self._update_editor_summary()
        self.step2_hint.setText(
            f"{contract.TASK_LABELS.get(proto.task, proto.task)} · "
            f"{contract.language_label(self.language_code())}"
            if proto is not None
            else ""
        )
        self.step3_hint.setText(
            proto.display_name if proto is not None else tr("Co se má spočítat.")
        )
        if not self._recordings:
            self.status.setText(tr("Vyber složku s nahrávkami."))
        elif proto is None:
            self.status.setText(tr("Vyber protokol."))
        else:
            self.status.setText(
                tr("{n} nahrávek").format(n=len(self._recordings))
                + f" · {contract.TASK_LABELS[proto.task]} · "
                f"{contract.language_label(self.language_code())} · {proto.display_name}"
            )

    def effective_protocol(self) -> Protocol | None:
        """Protokol pro běh: úpravy z rozšířeného režimu a jazyk z lišty."""
        if not self.advanced_box.isHidden():
            proto = self.editor.result()
        else:
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
                config={k: dict(v) for k, v in base.config.items()},
            )
        if proto is None:
            return None
        language = self.language_code()
        if language:
            for provider in ("transcript", "nlp"):
                proto.config.setdefault(provider, {})["language"] = language
        return proto

    def missing_providers(self, proto: Protocol) -> list[str]:
        """Providery, které protokol spustí a doctor hlásí jako nepřipravené.

        Bez zprávy doctor se zavolá knihovna přímo (pár sekund), aby klinik
        nespustil hodinový běh s prázdnými sloupci.
        """
        if self._library is None:
            return []
        if self._doctor is None:
            try:
                self._doctor = self._library.doctor()
            except LibraryError:
                return []
        try:
            catalog = self._library.features(proto.task)
            providers = self._library.providers()
        except LibraryError:
            return []
        summary = summarize(proto.select([f.name for f in catalog]), catalog, providers)
        state = self._doctor.get("providers", {})
        return [p for p in summary.providers if p in state and not state[p].get("ready")]

    def _run(self) -> None:
        proto = self.effective_protocol()
        if proto is None:
            return
        missing = self.missing_providers(proto)
        if missing:
            names = ", ".join(contract.PROVIDER_LABELS.get(p, p) for p in missing)
            answer = QMessageBox.question(
                self,
                tr("Něco chybí"),
                tr(
                    "Není připraveno: {names}.\n"
                    "Feature, které to potřebují, zůstanou ve výsledcích prázdné. "
                    "Modely a součásti se řeší na stránce Prostředí.\n\n"
                    "Spustit přesto?"
                ).format(names=names),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.run_requested.emit(proto, [r.path for r in self._recordings])

    def _prepare(self, kind: str) -> None:
        proto = self.effective_protocol()
        if proto is None:
            return
        n = len(self._recordings)
        if kind == "segments":
            choices: list[str] | None = None
            if self._library is not None:
                try:
                    param = next(
                        p for p in self._library.params("segments").params if p.name == "model"
                    )
                    choices = [str(c) for c in (param.choices or [])] or None
                except (LibraryError, StopIteration):
                    choices = None
            dialog: SegmentDialog | TranscribeDialog = SegmentDialog(
                n, model=proto.segments_model(), choices=choices, parent=self
            )
        else:
            transcript = proto.config.get("transcript", {})
            dialog = TranscribeDialog(
                n,
                language=self.language_code(),
                languages=[self.language.itemData(i) for i in range(self.language.count())],
                model=str(transcript.get("model", "large-v3")),
                parent=self,
            )
        if dialog.exec():
            self.prepare_requested.emit(
                kind, proto, [r.path for r in self._recordings], dialog.options()
            )

    def _save(self) -> None:
        proto = self.effective_protocol()
        if proto is None:
            return
        builtin = {p.name for p in self._protocols if p.builtin}
        dialog = SaveProtocolDialog(proto, self.protocol_names() - builtin, self)
        if dialog.exec():
            self.save_requested.emit(dialog.result_protocol())
