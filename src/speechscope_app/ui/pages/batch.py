"""Stránka Analýza: složka, protokol, nalezené nahrávky, spuštění.

Základní režim ukazuje jen složku, protokol a nahrávky. Rozšířený režim
přidává pod nahrávky editor protokolu (výběr feature a parametry),
kterým výzkumník upraví protokol pro tento běh nebo ho uloží jako nový.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend import meta
from ...backend.audio import format_duration
from ...backend.discover import Recording, find_recordings
from ...backend.library import Library, LibraryError
from ...backend.manifest import Manifest, find_manifest, key_for, read_manifest
from ...backend.protocol import Protocol, card_infos, summarize
from ...i18n import tr
from .. import theme
from ..file_actions import file_menu, open_file
from ..pages.run import format_seconds
from ..protocol_detail import ProtocolDetailDialog
from ..widgets.elided_label import ElidedLabel
from ..widgets.meta_table import GROUP_FOLDER, MetaRow, MetaTable
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
    hint_label = ElidedLabel(hint)  # dlouhá nápověda nesmí roztahovat sloupec
    hint_label.setObjectName("step_hint")
    row.addWidget(no)
    row.addWidget(label)
    row.addWidget(hint_label, 1)
    return row, hint_label


class _Tile(QFrame):
    """Dlaždice souhrnu nad tabulkou nahrávek: velké číslo a popisek; jde kliknout."""

    clicked = Signal()

    def __init__(self, warn: bool = False) -> None:
        super().__init__()
        self._warn = warn
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(0)
        self.value = QLabel("")
        font = self.value.font()
        font.setPointSizeF(font.pointSizeF() * 1.35)
        font.setBold(True)
        self.value.setFont(font)
        self.caption = QLabel("")
        small = self.caption.font()
        small.setPointSizeF(small.pointSizeF() * 0.9)
        self.caption.setFont(small)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)
        if warn:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_active(False)

    def set_text(self, value: str, caption: str) -> None:
        self.value.setText(value)
        self.caption.setText(caption)

    def set_active(self, active: bool) -> None:
        if self._warn:
            border = theme.WARN if active else "#fcd34d"
            width = 2 if active else 1
            self.setStyleSheet(
                f"_Tile {{ background: {theme.WARN_SOFT}; border: {width}px solid {border};"
                " border-radius: 10px; }"
                " QLabel { color: #92400e; background: transparent; border: none; }"
            )
        else:
            self.setStyleSheet(
                f"_Tile {{ background: {theme.CARD}; border: 1px solid {theme.BORDER};"
                " border-radius: 10px; }"
                " QLabel { background: transparent; border: none; }"
            )
            self.caption.setStyleSheet(f"color: {theme.MUTED};")

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _subfolder(path: Path, root: Path | None) -> str:
    """Podsložka nahrávky vůči vybrané složce („a/b“), v ní samé prázdno."""
    if root is None:
        return ""
    try:
        rel = path.parent.relative_to(root)
    except ValueError:
        return str(path.parent)
    return "" if str(rel) == "." else rel.as_posix()


class BatchPage(QWidget):
    run_requested = Signal(object, list)  # Protocol, list[Path]
    notice = Signal(str)  # krátká hláška do stavového řádku hlavního okna
    save_requested = Signal(object)  # Protocol k uložení (úprava, kopie, nový)
    # "segments" | "transcript", Protocol, cesty, volby z dialogu (model, cut_audio, language)
    prepare_requested = Signal(str, object, list, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self._protocols: list[Protocol] = []
        self._recordings: list[Recording] = []  # nalezené minus odebrané ze seznamu
        self._run_after_doctor: Protocol | None = None  # Spustit čeká na kontrolu prostředí
        self._doctor_wait: QProgressDialog | None = None
        self._check_requested: Callable[[], None] = lambda: None
        self._mode = "extract"  # extract | segments | transcript; po startu vždy výpočet
        self._found: list[Recording] = []
        self._excluded: set[Path] = set()  # odebrané pro tuto dávku; změna složky je zahodí
        self._scanned_folder: str = ""
        self._manifest: Manifest | None = None
        self._doctor: dict[str, Any] | None = None
        self._doctor_known = False  # True po první zprávě doctor (i neúspěšné)
        self._advanced = False
        self._manifest_auto = True  # manifest nalezený ve složce, ne vybraný ručně
        self._only_missing = False  # dlaždice „bez údajů“: jen neúplné řádky
        self._seconds_per_file: Callable[[str], float | None] = lambda _slug: None
        self._seconds_per_audio_minute: Callable[[str], float | None] = lambda _slug: None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel(tr("Analýza"))
        title.setObjectName("page_title")
        head.addWidget(title, 1)
        self.step_label = QLabel("")
        self.step_label.setObjectName("muted")
        head.addWidget(self.step_label, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(head)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("page_subtitle")
        self.subtitle.setWordWrap(True)
        self.set_work_root(None)
        layout.addWidget(self.subtitle)

        # Dva kroky v zásobníku: 1 nahrávky a metadata, 2 úloha, jazyk a protokol.
        self.steps = QStackedWidget()
        layout.addWidget(self.steps, 1)

        # === krok 1: nahrávky ============================================================
        page1 = QWidget()
        page1_layout = QVBoxLayout(page1)
        page1_layout.setContentsMargins(0, 0, 0, 0)
        page1_layout.setSpacing(10)
        left = QFrame()
        left.setObjectName("card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 14, 16, 14)
        left_layout.setSpacing(8)
        page1_layout.addWidget(left, 1)

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
        self.recursive = QCheckBox(tr("včetně podsložek"))
        self.recursive.setChecked(True)
        self.recursive.toggled.connect(self.rescan)
        folder_row.addWidget(self.recursive)
        left_layout.addLayout(folder_row)
        # Souhrn nad tabulkou (podoba A, mockup https://claude.ai/artifact/RbdMDqP2NUunQRc1RdH1VD):
        # zvuk, odhad, pacienti a klikací dlaždice nahrávek bez údajů (= jen neúplné).
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(10)
        self.audio_tile = _Tile()
        self.estimate_tile = _Tile()
        self.patients_tile = _Tile()
        self.missing_tile = _Tile(warn=True)
        self.missing_tile.setToolTip(tr("Klik ukáže jen nahrávky s chybějícími údaji."))
        self.missing_tile.clicked.connect(self._toggle_missing)
        for tile in (self.audio_tile, self.estimate_tile, self.patients_tile, self.missing_tile):
            tiles_row.addWidget(tile)
            tile.hide()
        tiles_row.addStretch(1)
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
        tiles_row.addWidget(self.manifest_btn, 0, Qt.AlignmentFlag.AlignBottom)
        tiles_row.addWidget(self.manifest_clear_btn, 0, Qt.AlignmentFlag.AlignBottom)
        left_layout.addLayout(tiles_row)

        # Řada nad tabulkou: vlevo práce s výběrem, vpravo hledání, seskupení, sloupce.
        meta_row = QHBoxLayout()
        self.selection_label = QLabel("")
        self.selection_label.setObjectName("muted")
        self.selection_label.hide()
        meta_row.addWidget(self.selection_label)
        # Odebrání ze seznamu: nahrávka zůstane na disku, jen se pro ni nepočítá.
        self.remove_btn = QPushButton(tr("Odebrat"))
        self.remove_btn.setToolTip(
            tr("Odebere vybrané nahrávky ze seznamu (Delete). Na disku zůstanou.")
        )
        self.remove_btn.setEnabled(False)
        self.remove_btn.clicked.connect(self.remove_selected)
        self.restore_btn = QPushButton("")
        self.restore_btn.setFlat(True)
        self.restore_btn.setToolTip(tr("Vrátí do seznamu všechny odebrané nahrávky."))
        self.restore_btn.clicked.connect(self.restore_excluded)
        self.restore_btn.hide()
        self.fill_btn = QPushButton(tr("Vyplnit vybrané…"))
        self.fill_btn.setToolTip(
            tr("Jedna hodnota do sloupce pro všechny vybrané řádky (třeba skupina nebo věk).")
        )
        self.fill_btn.setEnabled(False)
        self.fill_btn.clicked.connect(lambda: self._ask_fill())
        self.edits_btn = QPushButton("")
        self.edits_btn.setFlat(True)
        self.edits_btn.setToolTip(tr("Zahodí úpravy metadat i přidané sloupce; manifest zůstane."))
        self.edits_btn.clicked.connect(lambda: self.discard_meta_edits())
        self.edits_btn.hide()
        meta_row.addWidget(self.fill_btn)
        meta_row.addWidget(self.remove_btn)
        meta_row.addWidget(self.restore_btn)
        meta_row.addWidget(self.edits_btn)
        meta_row.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat nahrávku"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(220)
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        meta_row.addWidget(self.search)
        # Seskupit: jen podle toho, co v datech je (sloupec metadat, podsložka); výchozí nic.
        self.group_label = QLabel(tr("Seskupit:"))
        self.group_combo = QComboBox()
        self.group_combo.setToolTip(
            tr("Nadpis skupiny ukáže shodné údaje; klik na něj označí nahrávky skupiny.")
        )
        self.group_combo.currentIndexChanged.connect(lambda _i: self._group_changed())
        meta_row.addWidget(self.group_label)
        meta_row.addWidget(self.group_combo)
        self.column_btn = QPushButton(tr("+ Sloupec…"))
        self.column_btn.setToolTip(
            tr(
                "Přidá sloupec metadat (pacient, věk, pohlaví, skupina, nebo vlastní). "
                "Hodnoty vyplníte dvojklikem v tabulce; jdou do výsledků tohoto běhu."
            )
        )
        self.column_menu = QMenu(self.column_btn)
        for column in meta.META_COLUMNS:
            action = self.column_menu.addAction(f"{meta.label(column)} ({column})")
            action.triggered.connect(lambda _c=False, col=column: self.add_meta_column(col))
        self.column_menu.addSeparator()
        custom = self.column_menu.addAction(tr("Vlastní název…"))
        custom.triggered.connect(lambda: self._ask_meta_column())
        self.column_btn.setMenu(self.column_menu)
        self.save_btn = QPushButton(tr("Uložit manifest…"))
        self.save_btn.setToolTip(
            tr(
                "Zapíše metadata z tabulky (soubor i úpravy) jako CSV, kam ukážete; "
                "vedle nahrávek se načte příště samo. Jinak se do složky nic nezapisuje."
            )
        )
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(lambda: self.save_manifest())
        meta_row.addWidget(self.column_btn)
        meta_row.addWidget(self.save_btn)
        left_layout.addLayout(meta_row)

        # Tabulka nahrávek: název, délka (a ruční vstupy) jen ke čtení, metadata
        # upravitelná (`widgets/meta_table.py`); úpravy jdou do manifestu běhu, do
        # složky s nahrávkami nic (rozhodnuto 30. 10. 2026). Pacient, věk a pohlaví
        # jsou „povinné“ jen vzhledem: prázdné žlutě, když sloupec v tabulce je.
        self.files = MetaTable([tr("nahrávka"), tr("délka")], labels=True, right_columns=(1,))
        self.files.set_required({meta.SUBJECT, meta.AGE, meta.SEX})
        self.files.edited.connect(self._meta_edited)
        self.files.cellDoubleClicked.connect(self.open_recording)
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._files_menu)
        self.files.itemSelectionChanged.connect(self._selection_changed)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.files, self.remove_selected)
        left_layout.addWidget(self.files, 1)
        self.manifest_label = QLabel("")
        self.manifest_label.setObjectName("muted")
        self.manifest_label.setWordWrap(True)
        # stav manifestu pod tabulkou přes celou šířku; vedle tlačítek se lámal po slovech
        left_layout.addWidget(self.manifest_label)

        bottom1 = QHBoxLayout()
        self.status1 = QLabel("")
        self.continue_btn = QPushButton(tr("Pokračovat →"))
        theme.set_role(self.continue_btn, "primary")
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(lambda: self.go_to_step(2))
        bottom1.addWidget(self.status1, 1)
        bottom1.addWidget(self.continue_btn)
        page1_layout.addLayout(bottom1)
        QShortcut(QKeySequence(Qt.Key.Key_Return), page1, self._continue_if_ready)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), page1, self._continue_if_ready)
        self.steps.addWidget(page1)

        # === krok 2: úloha a jazyk nahoře, protokol pod tím ==============================
        page2 = QWidget()
        page2_layout = QVBoxLayout(page2)
        page2_layout.setContentsMargins(0, 0, 0, 0)
        page2_layout.setSpacing(10)

        label_card = QFrame()
        label_card.setObjectName("card")
        label_layout = QVBoxLayout(label_card)
        label_layout.setContentsMargins(16, 12, 16, 12)
        label_layout.setSpacing(4)
        label_row = QHBoxLayout()
        label_title = QLabel(tr("Název běhu"))
        label_title.setObjectName("card_title")
        self.run_label_edit = QLineEdit()
        self.run_label_edit.setPlaceholderText(tr("např. pacienti září, kontrola po 3 měsících"))
        self.run_label_edit.setClearButtonEnabled(True)
        self._label_touched = False
        self.run_label_edit.textEdited.connect(self._label_edited)
        self.run_label_edit.returnPressed.connect(self._run)
        label_row.addWidget(label_title)
        label_row.addWidget(self.run_label_edit, 1)
        label_layout.addLayout(label_row)
        label_hint = QLabel(
            tr(
                "Nepovinné. Ukáže se v historii a nad výsledky; "
                "bez názvu se běh jmenuje podle protokolu."
            )
        )
        label_hint.setObjectName("muted")
        label_layout.addWidget(label_hint)
        page2_layout.addWidget(label_card)

        task_card = QFrame()
        task_card.setObjectName("card")
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(16, 14, 16, 14)
        task_layout.setSpacing(8)
        step2, self.step2_hint = _step(2, tr("Úloha a jazyk"), "")
        task_layout.addLayout(step2)
        self.protocols = ProtocolList(task_columns=len(contract.TASKS))
        self.protocols.current_changed.connect(self._protocol_changed)
        self.protocols.details_requested.connect(self.show_protocol_detail)
        self.protocols.edit_requested.connect(self.edit_protocol)
        task_row = QHBoxLayout()
        task_row.setSpacing(12)
        task_row.addWidget(self.protocols.task_bar, 1)
        self.language = QComboBox()
        self.language.setToolTip(
            tr(
                "Jazyk přepisu (Whisper) a jazykového rozboru (Stanza) pro tento běh. "
                "Přebije nastavení protokolu."
            )
        )
        self.language.currentIndexChanged.connect(self._language_changed)
        self.set_languages(list(contract.DEFAULT_LANGUAGES))
        self.language.setMinimumWidth(160)
        task_row.addWidget(QLabel(tr("Jazyk nahrávek:")))
        task_row.addWidget(self.language)
        task_layout.addLayout(task_row)
        # Rozšířený režim: co spustit. Přepínače jako úlohy, ať nejsou schované dole.
        self.mode_row = QWidget()
        mode_layout = QHBoxLayout(self.mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(6)
        mode_layout.addWidget(QLabel(tr("Spustit:")))
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for mode in contract.RUN_MODES:
            btn = QPushButton(contract.RUN_MODE_LABELS[mode])
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self.mode_group.addButton(btn)
            mode_layout.addWidget(btn)
            self.mode_buttons[mode] = btn
        self.mode_buttons["extract"].setChecked(True)
        self.mode_buttons["segments"].setToolTip(
            tr(
                "Jen segmentace řeči do pracovní složky, žádné feature. Výpočet feature "
                "ji pak vezme z mezivýsledků. Ne u DDK."
            )
        )
        self.mode_buttons["transcript"].setToolTip(
            tr(
                "Jen přepis Whisperem do pracovní složky, v jazyce z lišty. Přepisy jde "
                "před výpočtem feature ručně zkontrolovat. Jen u souvislé řeči."
            )
        )
        mode_layout.addStretch(1)
        self.mode_row.hide()
        task_layout.addWidget(self.mode_row)
        page2_layout.addWidget(task_card)

        right = QFrame()
        right.setObjectName("card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(8)
        page2_layout.addWidget(right, 1)
        step3, self.step3_hint = _step(3, tr("Protokol"), tr("Co se má z nahrávek spočítat."))
        self.step3_title: QLabel = step3.itemAt(1).widget()
        right_layout.addLayout(step3)
        right_layout.addWidget(self.protocols, 1)

        # Jen segmentace / Jen přepis: místo karet protokolů volby mezivýsledku.
        self.prepare_box = QWidget()
        prepare_layout = QVBoxLayout(self.prepare_box)
        prepare_layout.setContentsMargins(0, 4, 0, 0)
        prepare_layout.setSpacing(8)
        self.segment_opts = QWidget()
        seg_form = QFormLayout(self.segment_opts)
        seg_form.setContentsMargins(0, 0, 0, 0)
        self.segment_model = QComboBox()
        for name in contract.SEGMENT_MODELS:
            self.segment_model.addItem(contract.SEGMENT_MODEL_LABELS.get(name, name), name)
        self.segment_model.currentIndexChanged.connect(lambda _i: self._update_run_state())
        seg_form.addRow(tr("Model:"), self.segment_model)
        self.cut_audio = QCheckBox(tr("uložit i vyříznutou řeč (VAD) jako wav do pracovní složky"))
        self.cut_audio.setToolTip(tr("Do složky s nahrávkami se nikdy nezapisuje."))
        seg_form.addRow("", self.cut_audio)
        seg_note = QLabel(
            tr(
                "Žádné feature se nepočítají. Výpočet feature segmentaci potom vezme "
                "z mezivýsledků; protokol k tomu musí mít stejný model v segments.model "
                "(auto bere jen hotovou cache)."
            )
        )
        seg_note.setObjectName("muted")
        seg_note.setWordWrap(True)
        seg_form.addRow("", seg_note)
        prepare_layout.addWidget(self.segment_opts)
        self.transcribe_opts = QWidget()
        tr_form = QFormLayout(self.transcribe_opts)
        tr_form.setContentsMargins(0, 0, 0, 0)
        self.transcribe_model = QLabel("")
        self.transcribe_model.setObjectName("muted")
        tr_form.addRow(tr("Model:"), self.transcribe_model)
        tr_note = QLabel(
            tr(
                "Nahrávky se jen přepíší Whisperem do pracovní složky, v jazyce z lišty "
                "nahoře; model podle vybraného protokolu. Žádné feature se nepočítají; "
                "přepisy jde před výpočtem ručně zkontrolovat."
            )
        )
        tr_note.setObjectName("muted")
        tr_note.setWordWrap(True)
        tr_form.addRow("", tr_note)
        prepare_layout.addWidget(self.transcribe_opts)
        prepare_layout.addStretch(1)
        self.prepare_box.hide()
        right_layout.addWidget(self.prepare_box, 1)

        # rozšířený režim: souhrn výběru; úpravy jdou přes Upravit… na kartě
        # a Nový protokol…, každá změna se uloží do protokolu
        self.editor_dialog = ProtocolEditorDialog(self)
        self.editor = self.editor_dialog.editor
        self.advanced_box = QGroupBox(tr("Feature a parametry"))
        adv = QVBoxLayout(self.advanced_box)
        self.editor_summary = QLabel("")
        self.editor_summary.setWordWrap(True)
        adv.addWidget(self.editor_summary)
        adv_buttons = QHBoxLayout()
        adv_buttons.addStretch(1)
        self.new_btn = QPushButton(tr("Nový protokol…"))
        self.new_btn.setToolTip(
            tr("Nový vlastní protokol: jméno, popis, úloha, výběr feature a parametry.")
        )
        self.new_btn.setEnabled(False)
        self.new_btn.clicked.connect(self.new_protocol)
        adv_buttons.addWidget(self.new_btn)
        adv.addLayout(adv_buttons)
        page2_layout.addWidget(self.advanced_box)

        bottom = QHBoxLayout()
        self.back_btn = QPushButton(tr("← Nahrávky"))
        self.back_btn.clicked.connect(lambda: self.go_to_step(1))
        self.status = QLabel("")
        self.run_btn = QPushButton(tr("Spustit"))
        theme.set_role(self.run_btn, "primary")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._run)
        bottom.addWidget(self.back_btn)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.run_btn)
        page2_layout.addLayout(bottom)
        self.steps.addWidget(page2)
        self.go_to_step(1)

    # --- kroky ------------------------------------------------------------------------

    def current_step(self) -> int:
        return self.steps.currentIndex() + 1

    def go_to_step(self, step: int) -> None:
        """Krok 1 (nahrávky) nebo 2 (úloha a protokol); stránka si ho pamatuje."""
        self.steps.setCurrentIndex(0 if step == 1 else 1)
        self.step_label.setText(
            tr("krok 1 ze 2 · Nahrávky") if step == 1 else tr("krok 2 ze 2 · Úloha a protokol")
        )

    def run_label(self) -> str:
        return self.run_label_edit.text().strip()

    def clear_run_label(self) -> None:
        """Po spuštění nebo zařazení dávky: název platí pro jeden běh, další začíná prázdný."""
        self.run_label_edit.clear()
        self._label_touched = False

    def _label_edited(self, text: str) -> None:
        self._label_touched = bool(text.strip())

    def _continue_if_ready(self) -> None:
        if self.continue_btn.isEnabled():
            self.go_to_step(2)

    # --- nastavení zvenku -----------------------------------------------------

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self._doctor = None
        self._doctor_known = False
        self.editor.set_library(library)
        self._load_languages()
        self._update_run_state()

    def _load_languages(self) -> None:
        """Jazyky nahrávek z `models list --json` hned při startu (laciné, bez torche);
        `doctor` je zpřesní, ale ten se bez chybějících modelů při startu nevolá."""
        if self._library is None:
            return
        try:
            payload = self._library.models()
        except LibraryError:
            return
        for model in payload.get("models", []):
            if model.get("key") == "stanza" and model.get("languages"):
                self.set_languages([str(code) for code in model["languages"]])
                return

    def set_check_requester(self, fn: Callable[[], None]) -> None:
        """Jak si vyžádat kontrolu prostředí na pozadí (Prostředí → refresh)."""
        self._check_requested = fn

    def set_doctor(self, report: dict[str, Any] | None) -> None:
        self._doctor = report
        self._doctor_known = True
        self.editor.set_doctor(report)
        if self._run_after_doctor is not None:
            proto, self._run_after_doctor = self._run_after_doctor, None
            self._close_doctor_wait()
            self._run_checked(proto)
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
        self._update_run_state()

    def set_stats_lookup(self, lookup: Callable[[str], float | None]) -> None:
        self._seconds_per_file = lookup
        self.editor.set_stats_lookup(lookup)

    def set_rate_lookup(self, lookup: Callable[[str], float | None]) -> None:
        """Sekundy výpočtu na minutu zvuku podle slugu protokolu (z nastavení)."""
        self._seconds_per_audio_minute = lookup

    def durations_for(self, paths: list[Path]) -> list[float | None]:
        """Délky zvuku k cestám ve stejném pořadí; None, kde ji nejde přečíst."""
        known = {rec.path: rec.duration for rec in self._recordings}
        return [known.get(p) for p in paths]

    def expected_seconds(self, proto: Protocol | None = None) -> float | None:
        """Odhad celé dávky: podle minut zvuku, kde jsou známé, jinak na nahrávku."""
        proto = proto or self.current_protocol()
        if proto is None or not self._recordings:
            return None
        slug = proto.slug()
        rate = self._seconds_per_audio_minute(slug)
        per_file = self._seconds_per_file(slug)
        known = [rec.duration for rec in self._recordings if rec.duration]
        unknown = len(self._recordings) - len(known)
        total = 0.0
        if rate and known:
            total += rate / 60 * sum(known)
        elif per_file:
            total += per_file * len(known)
        else:
            return None
        if unknown:
            if per_file is None:
                return None
            total += per_file * unknown
        return total

    def file_for_cell(self, row: int, column: int) -> Path | None:
        """Nahrávka na řádku; ve sloupci ruční vstupy (rozšířený režim) labely nebo přepis."""
        if not (0 <= row < len(self._recordings)):
            return None
        rec = self._recordings[row]
        if self._advanced and column == 2:
            stem = str(rec.path.with_suffix(""))
            for suffix, present in (
                (contract.LABELS_SUFFIX, rec.has_labels),
                (contract.TRANSCRIPT_SUFFIX, rec.has_transcript),
            ):
                if present:
                    return Path(stem + suffix)
        return rec.path

    def open_recording(self, row: int, column: int = 0) -> bool:
        """Dvojklik na řádek tabulky `row` (nadpis skupiny nic nepřehraje)."""
        if self.files.columns and column >= self.files.meta_from:
            return False  # dvojklik na metadata upravuje buňku, nepřehrává
        index = self.files.data_index(row)
        if index is None:
            return False
        path = self.file_for_cell(index, column)
        return open_file(path, self.notice.emit) if path is not None else False

    def _files_menu(self, pos) -> None:  # noqa: ANN001
        item = self.files.itemAt(pos)
        if item is None:
            return
        index = self.files.data_index(item.row())
        path = self.file_for_cell(index, item.column()) if index is not None else None
        if path is None:
            return
        menu = file_menu(self, path, self.notice.emit)
        menu.addSeparator()
        n = len(self.selected_rows()) or 1
        remove = menu.addAction(tr("Odebrat ze seznamu ({n})").format(n=n))
        remove.triggered.connect(self.remove_selected)
        menu.exec(self.files.viewport().mapToGlobal(pos))

    def _update_step1_hint(self) -> None:
        """Nápověda u nadpisu kroku 1: počet nahrávek; zvuk a odhad ukazují dlaždice."""
        self._update_tiles()
        text = self.folder.text().strip()
        if not text:
            self.step1_hint.setText(tr("Vyber složku."))
        elif not self._recordings and not self._excluded:
            self.step1_hint.setText(tr("žádná nenalezena"))
        else:
            self.step1_hint.setText(self._count_text())

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

    def make_protocol_detail(self, proto: Protocol) -> ProtocolDetailDialog:
        """Okno „co protokol počítá“ z katalogu knihovny; bez knihovny prázdné."""
        catalog: list = []
        providers = None
        if self._library is not None:
            try:
                catalog = self._library.features()
                providers = self._library.providers()
            except LibraryError:
                catalog = []
        seconds = self._seconds_per_file(proto.slug())
        hint = tr("naposledy {n:.0f} s na nahrávku").format(n=seconds) if seconds else ""
        return ProtocolDetailDialog(
            proto, catalog, providers, self._doctor, hint, self, language=self.language_code()
        )

    def show_protocol_detail(self, proto: Protocol | None) -> None:
        if proto is not None:
            self.make_protocol_detail(proto).exec()

    def protocol_names(self) -> set[str]:
        return {p.name for p in self._protocols}

    def set_work_root(self, root: Path | None) -> None:
        """Podtitul říká, kam jdou výsledky; složku si uživatel volí sám."""
        where = (
            tr("výsledky jdou do {path}").format(path=root)
            if root is not None
            else tr("výsledky jdou do složky z Nastavení")
        )
        self.subtitle.setText(
            tr(
                "Krok 1 nahrávky a metadata, krok 2 úloha, jazyk a protokol. "
                "Do složky s nahrávkami se nic nezapisuje, {where}."
            ).format(where=where)
        )
        self.subtitle.setToolTip(str(root) if root is not None else "")

    def set_advanced(self, advanced: bool) -> None:
        self._advanced = advanced
        self.mode_row.setVisible(advanced)
        if not advanced:
            self.set_mode("extract")  # základní režim umí jen výpočet feature
        self.advanced_box.setVisible(advanced and self._mode == "extract")
        self.protocols.set_advanced(advanced)
        self._fill_files()

    # --- režim běhu (rozšířený režim) -------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Výpočet feature, jen segmentace, nebo jen přepis; mění obsah kroku 3."""
        if mode not in contract.RUN_MODES:
            return
        if not self._mode_allowed(mode):
            mode = "extract"
        self._mode = mode
        btn = self.mode_buttons[mode]
        if not btn.isChecked():
            btn.setChecked(True)
        self.protocols.setVisible(mode == "extract")
        self.prepare_box.setVisible(mode != "extract")
        self.segment_opts.setVisible(mode == "segments")
        self.transcribe_opts.setVisible(mode == "transcript")
        self.advanced_box.setVisible(self._advanced and mode == "extract")
        self.step3_title.setText(
            {
                "extract": tr("Protokol"),
                "segments": tr("Segmentace"),
                "transcript": tr("Přepis (Whisper)"),
            }[mode]
        )
        self._update_run_state()

    def _mode_allowed(self, mode: str) -> bool:
        """Přepis jen u souvislé řeči, segmentace ne u DDK; bez protokolu vše."""
        proto = self.current_protocol()
        if proto is None or mode == "extract":
            return True
        if mode == "transcript":
            return proto.task in contract.TRANSCRIPT_TASKS
        return proto.task in contract.SEGMENT_TASKS

    def _sync_mode_to_protocol(self) -> None:
        """Po změně protokolu: nepovolené režimy vypnout, výchozí modely z protokolu."""
        proto = self.current_protocol()
        for mode in ("segments", "transcript"):
            self.mode_buttons[mode].setEnabled(self._mode_allowed(mode))
        if not self._mode_allowed(self._mode):
            # přepis u fonace spadne na segmentaci, u DDK oba na výpočet feature
            self.set_mode("segments" if self._mode_allowed("segments") else "extract")
        if proto is not None:
            model = proto.segments_model()
            idx = self.segment_model.findData(model if model != "auto" else "conformer")
            self.segment_model.blockSignals(True)
            self.segment_model.setCurrentIndex(max(0, idx))
            self.segment_model.blockSignals(False)
            transcript = proto.config.get("transcript", {})
            self.transcribe_model.setText(str(transcript.get("model", "large-v3")))

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
        if text != self._scanned_folder:
            self._excluded.clear()
            self.files.reset()  # úpravy metadat patří složce; jiná složka = od začátku
            self._scanned_folder = text
        self._found = (
            find_recordings(Path(text), recursive=self.recursive.isChecked()) if text else []
        )
        self._apply_exclusions()
        if self._manifest_auto:
            found = find_manifest(Path(text)) if text else None
            self._manifest = read_manifest(found, Path(text)) if found else None
        self._fill_files()
        self._update_run_state()

    # --- odebrání ze seznamu -------------------------------------------------------------

    def _apply_exclusions(self) -> None:
        self._recordings = [rec for rec in self._found if rec.path not in self._excluded]
        self._excluded &= {rec.path for rec in self._found}  # po přeskenování jen existující
        k = len(self._excluded)
        self.restore_btn.setText(tr("Vrátit odebrané ({k})").format(k=k))
        self.restore_btn.setVisible(k > 0)

    def _count_text(self) -> str:
        text = tr("{n} nahrávek").format(n=len(self._recordings))
        if self._excluded:
            text += " " + tr("({k} odebráno)").format(k=len(self._excluded))
        return text

    def selected_rows(self) -> list[int]:
        """Indexy vybraných nahrávek (do `_recordings`), bez nadpisů skupin."""
        return self.files.selected_indices()

    def _selection_changed(self) -> None:
        n = len(self.selected_rows())
        self.fill_btn.setEnabled(bool(n))
        self.remove_btn.setEnabled(bool(n))
        self.selection_label.setText(tr("vybráno {n}").format(n=n))
        self.selection_label.setVisible(n > 0)

    def remove_selected(self) -> None:
        """Vybrané nahrávky ven ze seznamu; na disku zůstanou, jen se nepočítají."""
        rows = self.selected_rows()
        if not rows:
            return
        self._excluded.update(self._recordings[r].path for r in rows if r < len(self._recordings))
        self._apply_exclusions()
        self._fill_files()
        self._update_run_state()
        if self.files.row_count():  # výběr na místo první odebrané, ať jde Delete opakovat
            self.files.select_index(min(rows[0], self.files.row_count() - 1))

    def restore_excluded(self) -> None:
        self._excluded.clear()
        self._apply_exclusions()
        self._fill_files()
        self._update_run_state()

    # --- metadata (manifest) ------------------------------------------------------------

    def base_manifest(self) -> Manifest | None:
        """Manifest ze souboru bez chyb; `None` = bez souboru nebo rozbitý."""
        return self._manifest if self._manifest and not self._manifest.problems else None

    def manifest(self) -> Manifest | None:
        """Manifest, který se předá knihovně: soubor plus úpravy a přidané sloupce z tabulky.

        `None` = bez metadat. Složí se při každém volání, takže běh dostane
        vždy aktuální tabulku; do souboru ve složce se nic nezapisuje."""
        base = self.base_manifest()
        if base is None and not self.files.has_changes():
            return None
        columns = list(self.files.columns)
        folder = self.folder.text().strip()
        merged = Manifest(
            path=base.path if base else Path(folder or ".") / "manifest.csv",
            columns=columns,
            tasks=dict(base.tasks) if base else {},
        )
        for key, row in self.files.rows_merged().items():
            if any(row.values()):
                merged.rows[key] = {c: row.get(c, "") for c in columns}
        return merged

    def meta_edits(self) -> int:
        return self.files.edit_count()

    def add_meta_column(self, name: str) -> str:
        """Přidá sloupec metadat (název se kanonizuje: vek → age); vrátí jeho klíč."""
        return self.files.add_column(name)

    def set_meta(self, path: Path, column: str, value: str) -> str:
        """Hodnota metadat jedné nahrávky (jako dvojklik v tabulce); vrátí uloženou podobu."""
        return self.files.set_value(key_for(path), column, value)

    def fill_selected(self, column: str, value: str) -> int:
        """Vyplní hodnotu sloupce všem vybraným řádkům; vrátí jejich počet."""
        return self.files.fill_selected(column, value)

    def discard_meta_edits(self) -> None:
        self.files.discard()

    def save_manifest(self, target: Path | None = None) -> Path | None:
        """Zapíše metadata z tabulky jako CSV, kam uživatel ukáže (výchozí složka nahrávek).

        Cesty jsou relativní ke složce cíle. Když se uloží tam, odkud se manifest
        načítá, načte se znovu a úpravy se stanou základem."""
        manifest = self.manifest()
        folder = Path(self.folder.text().strip() or ".")
        if manifest is None:
            return None
        if target is None:
            text, _filter = QFileDialog.getSaveFileName(
                self, tr("Uložit manifest"), str(folder / "manifest.csv"), "CSV (*.csv)"
            )
            if not text:
                return None
            target = Path(text)
        columns = list(manifest.columns)
        with_task = bool(manifest.tasks)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["path", *(["task"] if with_task else []), *columns])
            for rec in self._recordings:
                key = key_for(rec.path)
                row = manifest.rows.get(key, {})
                try:
                    shown = rec.path.resolve().relative_to(target.parent.resolve())
                except ValueError:
                    shown = rec.path
                task = [manifest.tasks.get(key, "")] if with_task else []
                writer.writerow([str(shown), *task, *(row.get(c, "") for c in columns)])
        self.notice.emit(tr("Manifest uložen: {path}").format(path=target))
        auto = self._manifest_auto and target.parent == folder
        if auto or (self._manifest is not None and target == self._manifest.path):
            self.files.reset()
            self.set_manifest(None if auto else target)
        return target

    def _ask_fill(self) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        choices = list(self.files.columns)
        for column in meta.META_COLUMNS:
            if column not in choices:
                choices.append(column)
        labels = [f"{meta.label(c)} ({c})" if c in meta.META_COLUMNS else c for c in choices]
        label, ok = QInputDialog.getItem(
            self, tr("Vyplnit vybrané"), tr("Sloupec:"), labels, 0, False
        )
        if not ok:
            return
        column = choices[labels.index(label)]
        value, ok = QInputDialog.getText(
            self,
            tr("Vyplnit vybrané"),
            tr("Hodnota pro {n} vybraných řádků, sloupec {column}:").format(
                n=len(rows), column=column
            ),
        )
        if ok:
            self.fill_selected(column, value)

    def _meta_edited(self) -> None:
        self._update_group_choices()
        self._update_meta_label()
        self._update_run_state()
        self._apply_filter()

    # --- seskupení, hledání, souhrn -------------------------------------------------------

    def _update_group_choices(self) -> None:
        """Volby Seskupit: nic, sloupce metadat v tabulce, podsložka (jen když nějaká je)."""
        current = self.group_combo.currentData() or ""
        choices: list[tuple[str, str]] = [(tr("nic"), "")]
        choices += [(meta.label(c), c) for c in self.files.columns]
        if any(rec.path.parent != self._recordings[0].path.parent for rec in self._recordings):
            choices.append((tr("podsložka"), GROUP_FOLDER))
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for label, key in choices:
            self.group_combo.addItem(label, key)
        found = self.group_combo.findData(current)
        self.group_combo.setCurrentIndex(max(0, found))
        self.group_combo.blockSignals(False)
        visible = len(choices) > 1
        self.group_label.setVisible(visible)
        self.group_combo.setVisible(visible)
        self.files.set_group_by(self.group_combo.currentData() or "")

    def set_group_by(self, column: str) -> bool:
        """Seskupit tabulku (klíč sloupce, `GROUP_FOLDER`, prázdno = nic); False = nejde."""
        found = self.group_combo.findData(column)
        if found < 0:
            return False
        self.group_combo.setCurrentIndex(found)
        return True

    def _group_changed(self) -> None:
        self.files.set_group_by(self.group_combo.currentData() or "")
        self._apply_filter()

    def _toggle_missing(self) -> None:
        self._only_missing = not self._only_missing
        self._apply_filter()

    def _apply_filter(self) -> None:
        if not self.files.missing_indices():
            self._only_missing = False
        self.files.set_filter(self.search.text(), self._only_missing)
        self.missing_tile.set_active(self._only_missing)
        self._update_tiles()

    def _update_tiles(self) -> None:
        """Dlaždice nad tabulkou: zvuk, odhad výpočtu, pacienti, nahrávky bez údajů."""
        n = len(self._recordings)
        known = [rec.duration for rec in self._recordings if rec.duration]
        if n and known:
            caption = (
                tr("zvuku celkem")
                if len(known) == n
                else tr("zvuku ({k} z {n} se známou délkou)").format(k=len(known), n=n)
            )
            self.audio_tile.set_text(format_seconds(sum(known)), caption)
        self.audio_tile.setVisible(bool(n and known))
        expected = self.expected_seconds() if n else None
        if expected:
            self.estimate_tile.set_text(format_seconds(expected), tr("odhad výpočtu"))
        self.estimate_tile.setVisible(bool(expected))
        merged = self.files.rows_merged()
        if n and meta.SUBJECT in self.files.columns:
            subjects = {m.get(meta.SUBJECT, "") for m in merged.values()} - {""}
            self.patients_tile.set_text(str(len(subjects)), tr("pacientů"))
            self.patients_tile.show()
        else:
            self.patients_tile.hide()
        missing = self.files.missing_indices() if n else []
        if missing:
            labels = ", ".join(meta.label(c) for c in self.files.missing_columns())
            caption = tr("bez údajů: {columns}").format(columns=labels)
            if self._only_missing:
                caption += " · " + tr("zobrazeny jen tyto")
            self.missing_tile.set_text(str(len(missing)), caption)
        self.missing_tile.setVisible(bool(missing))

    def _ask_meta_column(self) -> None:
        name, ok = QInputDialog.getText(
            self, tr("Nový sloupec"), tr("Název sloupce (třeba skupina, lék, poznámka):")
        )
        if ok and name.strip():
            self.add_meta_column(name)

    def set_manifest(self, path: Path | None) -> None:
        """Ručně vybraný manifest; relativní cesty se berou od složky s nahrávkami."""
        base = Path(self.folder.text().strip()) if self.folder.text().strip() else None
        if path is None and base is not None:
            found = find_manifest(base)  # zpět k souboru ve složce, když tam je
            self._manifest = read_manifest(found, base) if found else None
        else:
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
        manifest = self.base_manifest()
        if manifest is None and self._manifest is not None:
            manifest = self._manifest  # rozbitý soubor: sloupce ukázat, hodnoty šedě
        columns = list(manifest.columns) if manifest else []
        # Ruční vstupy (labely, přepis vedle nahrávky) zajímají výzkumníka,
        # klinik je nikdy nemá; v základním režimu se sloupec neukazuje.
        headers = [tr("nahrávka"), tr("délka")]
        if self._advanced:
            headers.append(tr("ruční vstupy"))
        self.files.set_leading_headers(headers)
        root = Path(self.folder.text().strip()) if self.folder.text().strip() else None
        rows: list[MetaRow] = []
        for rec in self._recordings:
            row_meta = manifest.meta_for(rec.path) if manifest else None
            leading = [rec.name, format_duration(rec.duration)]
            if self._advanced:
                manual = [tr("labely")] * rec.has_labels + [tr("přepis")] * rec.has_transcript
                leading.append(", ".join(manual))
            rows.append(
                MetaRow(
                    key_for(rec.path),
                    leading,
                    dict(row_meta or {}),
                    tooltip=tr("{path}\nDvojklik přehraje.").format(path=rec.path),
                    muted=row_meta is None,
                    seconds=rec.duration,
                    folder=_subfolder(rec.path, root),
                )
            )
        self.files.set_rows(rows, columns)
        self._update_group_choices()
        self._update_meta_label()
        self._apply_filter()

    def _update_meta_label(self) -> None:
        """Řádek pod tabulkou: soubor manifestu, sloupce, shoda, počet úprav."""
        base = self._manifest
        edits = self.meta_edits()
        columns = self.files.columns
        self.manifest_clear_btn.setVisible(base is not None)
        self.edits_btn.setVisible(self.files.has_changes())
        self.edits_btn.setText(tr("Zahodit úpravy ({n})").format(n=edits))
        self.save_btn.setEnabled(self.manifest() is not None)
        if base is None:
            if self.files.has_changes():
                self.manifest_label.setText(
                    tr("Vlastní metadata: {columns} · upraveno: {n}").format(
                        columns=", ".join(columns), n=edits
                    )
                )
            else:
                self.manifest_label.setText(
                    tr("Bez metadat. Do výsledků jde jen název souboru.")
                    if self._recordings
                    else ""
                )
            self.manifest_label.setStyleSheet("")
            return
        if base.problems:
            self.manifest_label.setText(
                tr("{name}: {problem}").format(name=base.path.name, problem=base.problems[0])
            )
            self.manifest_label.setStyleSheet(f"color: {theme.MISSING};")
            return
        paths = [rec.path for rec in self._recordings]
        matched = base.matched(paths)
        text = tr("{name}: {columns} · {matched} z {total}").format(
            name=base.path.name,
            columns=", ".join(columns) or tr("bez sloupců"),
            matched=matched,
            total=len(paths),
        )
        if edits:
            text += tr(" · upraveno: {n}").format(n=edits)
        self.manifest_label.setText(text)
        self.manifest_label.setStyleSheet(
            f"color: {theme.WARN};" if matched < len(paths) else f"color: {theme.OK};"
        )

    # --- protokol -------------------------------------------------------------------

    def _protocol_changed(self, proto: Protocol | None) -> None:
        self.editor.set_protocol(proto)  # kvůli souhrnu v rámečku
        self._sync_mode_to_protocol()
        self._update_run_state()

    def edit_protocol(self, proto: Protocol | None) -> Protocol | None:
        """Upravit… na kartě: vlastní protokol se uloží, přibalený jako kopie."""
        if proto is None or not self.editor.has_catalog():
            return None
        mode = "copy" if proto.builtin else "edit"
        taken = self.protocol_names() - ({proto.name} if mode == "edit" else set())
        result = self.editor_dialog.edit(proto, mode=mode, taken=taken)
        self.editor.set_protocol(self.current_protocol())  # souhrn zpět na vybraný
        if result is not None:
            self.save_requested.emit(result)
        return result

    def new_protocol(self) -> Protocol | None:
        """Nový protokol…: prázdný pro aktuální úlohu, se všemi feature k ní."""
        if not self.editor.has_catalog():
            return None
        task = self.protocols.current_task() or contract.TASKS[0]
        result = self.editor_dialog.edit(
            Protocol(name="", task=task), mode="new", taken=self.protocol_names()
        )
        self.editor.set_protocol(self.current_protocol())
        if result is not None:
            self.save_requested.emit(result)
        return result

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
        self.new_btn.setEnabled(self.editor.has_catalog())
        self._update_editor_summary()
        self._update_step1_hint()
        self.step2_hint.setText(
            f"{contract.TASK_LABELS.get(proto.task, proto.task)} · "
            f"{contract.language_label(self.language_code())}"
            if proto is not None
            else ""
        )
        work_hint = tr("do mezivýsledků (work)")
        if self._mode == "segments":
            self.step3_hint.setText(work_hint)
        elif self._mode == "transcript":
            self.step3_hint.setText(
                f"{contract.language_label(self.language_code())} · {work_hint}"
            )
        else:
            self.step3_hint.setText(
                proto.display_name if proto is not None else tr("Co se má spočítat.")
            )
        self.continue_btn.setEnabled(bool(self._recordings))
        if not self._recordings:
            self.status1.setText(tr("Vyber složku s nahrávkami."))
            self.status.setText(tr("Vyber složku s nahrávkami."))
        else:
            folder = self._recordings[0].path.parent
            self.status1.setText(
                tr("{count} z {folder}").format(count=self._count_text(), folder=folder)
            )
            if proto is None:
                self.status.setText(tr("Vyber protokol."))
            elif self._mode == "segments":
                self.status.setText(
                    self._count_text()
                    + f" · {contract.RUN_MODE_LABELS['segments']} · "
                    + str(self.segment_model.currentData())
                )
            elif self._mode == "transcript":
                self.status.setText(
                    self._count_text() + f" · {contract.RUN_MODE_LABELS['transcript']} · "
                    f"{contract.language_label(self.language_code())} · "
                    + self.transcribe_model.text()
                )
            else:
                self.status.setText(
                    self._count_text() + f" · {contract.TASK_LABELS[proto.task]} · "
                    f"{contract.language_label(self.language_code())} · {proto.display_name}"
                )

    def effective_protocol(self) -> Protocol | None:
        """Protokol pro běh: vybraný protokol tak, jak je uložený, plus jazyk z lišty."""
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
            names=dict(base.names),
            descriptions=dict(base.descriptions),
        )
        language = self.language_code()
        if language:
            for provider in ("transcript", "nlp"):
                proto.config.setdefault(provider, {})["language"] = language
        return proto

    def missing_providers(self, proto: Protocol) -> list[str]:
        """Providery, které protokol spustí a doctor hlásí jako nepřipravené.

        Jen z poslední zprávy doctor; bez ní prázdný seznam. Knihovna se tu
        nikdy nevolá, Spustit si zprávu vyžádá na pozadí (`_run`).
        """
        if self._library is None or self._doctor is None:
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
        if self._mode != "extract":
            self._prepare(self._mode)
            return
        if self._library is not None and not self._doctor_known:
            # Zpráva doctor ještě nedorazila (klik hned po startu): počkat na pozadí,
            # ať klinik nespustí hodinový běh s prázdnými sloupci. Storno = nespouštět.
            self._run_after_doctor = proto
            self._check_requested()
            dialog = QProgressDialog(tr("Kontroluji prostředí…"), tr("Storno"), 0, 0, self)
            dialog.setWindowTitle(tr("SpeechScope"))
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setMinimumDuration(300)  # rychlá odpověď se bez dialogu obejde
            dialog.canceled.connect(self._cancel_doctor_wait)
            self._doctor_wait = dialog
            dialog.setValue(0)  # od teď běží odpočet do zobrazení
            return
        self._run_checked(proto)

    def _cancel_doctor_wait(self) -> None:
        self._run_after_doctor = None
        self._close_doctor_wait()

    def _close_doctor_wait(self) -> None:
        if self._doctor_wait is not None:
            dialog, self._doctor_wait = self._doctor_wait, None
            dialog.blockSignals(True)
            dialog.close()
            dialog.deleteLater()

    def _run_checked(self, proto: Protocol) -> None:
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

    def prepare_options(self) -> dict[str, object]:
        """Volby mezivýsledku z kroku 3 (model segmentace, vyříznutá řeč, jazyk přepisu)."""
        if self._mode == "segments":
            return {
                "model": str(self.segment_model.currentData()),
                "cut_audio": self.cut_audio.isChecked(),
            }
        return {"language": self.language_code()}

    def _prepare(self, kind: str) -> None:
        """Jen segmentace nebo jen přepis; volby jsou přímo na stránce, bez dialogu."""
        proto = self.effective_protocol()
        if proto is None:
            return
        self.prepare_requested.emit(
            kind, proto, [r.path for r in self._recordings], self.prepare_options()
        )
