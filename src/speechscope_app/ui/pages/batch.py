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
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend import meta, quality, tasks
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
from ..widgets.task_rows import SHARE_COLORS, SummaryBar, TaskRowWidget

TASK = "task"  # sloupec úlohy v tabulce nahrávek (z manifestu nebo doplněný)


def runs_text(n: int) -> str:
    """„1 běh“, „3 běhy“, „5 běhů“ (texty zvlášť kvůli překladu)."""
    if n == 1:
        return tr("1 běh")
    if 2 <= n <= 4:
        return tr("{n} běhy").format(n=n)
    return tr("{n} běhů").format(n=n)


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
    # "segments" | "transcript", Protocol, cesty, volby z dialogu (model, cut_audio, language)
    prepare_requested = Signal(str, object, list, dict)
    # víc úloh najednou (manifest se sloupcem task): druh běhu, [(Protocol, cesty)], volby
    runs_requested = Signal(str, list, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self._protocols: list[Protocol] = []
        self._recordings: list[Recording] = []  # nalezené minus odebrané ze seznamu
        self._run_after_doctor = False  # Spustit čeká na kontrolu prostředí
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
        self._shown_task = ""  # úloha bez manifestu, kterou ukazuje sloupec úloha šedě
        # seznam k doplnění: "" | "missing" (bez údajů) | "task" (bez úlohy)
        self._fix_mode = ""
        self._task_cache: dict[Path, str] | None = None  # klíč nahrávky -> úloha z tabulky
        # řádky úloh v kroku 2 (31. 10. 2026): bez úloh v manifestu jeden řádek s výběrem
        # úlohy (`_single_task`), jinak řádek na úlohu manifestu; nahrávky bez úlohy jdou
        # do `_unassigned_task` („počítat jako“). Vybraný protokol si pamatuje úloha.
        # bez manifestu žádná předvolená úloha (1. 11. 2026): řádek je červený a Spustit
        # nejde, dokud se úloha nevybere; jiná složka volbu vynuluje
        self._single_task = ""
        self._unassigned_task = ""
        self._unassigned_auto = True
        self._row_choice: dict[str, str] = {}  # základní úloha -> jméno protokolu
        self._task_rows: dict[str, TaskRowWidget] = {}
        self._rows_key: tuple = ()
        self._current_task = ""
        self._syncing = False
        self._seconds_per_file: Callable[[str], float | None] = lambda _slug: None
        self._seconds_per_audio_minute: Callable[[str], float | None] = lambda _slug: None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel(tr("Analýza"))
        title.setObjectName("page_title")
        head.addWidget(title, 1)
        # kroky jako v průvodcích (mockup analyza-zjednoduseni, 31. 10. 2026)
        self.step_label = QLabel("")
        self.step_label.setTextFormat(Qt.TextFormat.RichText)
        head.addWidget(self.step_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)
        # Dlouhý podtitulek (kam jdou výsledky) jen v tooltipu nadpisu; kde jsou výsledky,
        # říká i Prostředí. Objekt zůstává kvůli textu pro tooltip.
        self._title = title
        self.subtitle = QLabel("")
        self.subtitle.hide()
        self.set_work_root(None)

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

        left_layout.setSpacing(10)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.folder = QLineEdit()
        self.folder.setPlaceholderText(tr("Složka s nahrávkami"))
        # Jen při změně textu: editingFinished přijde i při ztrátě zaměření (Pokračovat)
        # a znovu prohledat celou složku stálo u 260 nahrávek většinu přechodu na krok 2.
        self.folder.editingFinished.connect(self._folder_edited)
        self.browse_btn = QPushButton(tr("Vybrat…"))
        self.browse_btn.clicked.connect(self._browse)
        self.recursive = QCheckBox(tr("i podsložky"))
        self.recursive.setChecked(True)
        self.recursive.toggled.connect(self.rescan)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(self.recursive)
        folder_row.addWidget(self.browse_btn)
        left_layout.addLayout(folder_row)

        # Souhrn jedním řádkem místo dlaždic: počet, zvuk, pacienti, štítek manifestu
        # a žlutý odkaz na doplnění, když něco chybí.
        summary_row = QHBoxLayout()
        summary_row.setSpacing(10)
        self.step1_hint = QLabel(tr("Vyber složku."))
        summary_row.addWidget(self.step1_hint)
        self.manifest_label = QLabel("")
        self.manifest_label.setObjectName("pill")
        self.manifest_label.hide()
        summary_row.addWidget(self.manifest_label)
        self.missing_tile = QPushButton("")
        self.missing_tile.setCursor(Qt.CursorShape.PointingHandCursor)
        self.missing_tile.setToolTip(tr("Ukáže jen nahrávky s chybějícími údaji k doplnění."))
        self.missing_tile.setStyleSheet(
            f"QPushButton {{ background: {theme.WARN_SOFT}; color: #92400e; border: none;"
            " border-radius: 11px; padding: 3px 12px; font-weight: 600; }"
            "QPushButton:hover { background: #fde68a; }"
        )
        self.missing_tile.clicked.connect(self._toggle_missing)
        self.missing_tile.hide()
        summary_row.addWidget(self.missing_tile)
        summary_row.addStretch(1)
        left_layout.addLayout(summary_row)

        # Lišta nad tabulkou: hledání, Seskupit, Vrátit odebrané a Metadata ▾; s výběrem
        # se místo ní ukáže „vybráno n · Vyplnit… · Odebrat · ✕“.
        self.bars = QStackedWidget()
        self.bars.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        bar = QWidget()
        bar.setObjectName("metabar")
        bar.setStyleSheet("QWidget#metabar { background: transparent; }")
        meta_row = QHBoxLayout(bar)
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat nahrávku"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        meta_row.addWidget(self.search)
        # Seskupit: jen podle toho, co v datech je (sloupec metadat, podsložka); výchozí nic.
        self.group_label = QLabel(tr("Seskupit"))
        self.group_label.setObjectName("muted")
        self.group_combo = QComboBox()
        self.group_combo.setToolTip(
            tr("Nadpis skupiny ukáže shodné údaje; klik na něj označí nahrávky skupiny.")
        )
        self.group_combo.currentIndexChanged.connect(lambda _i: self._group_changed())
        meta_row.addWidget(self.group_label)
        meta_row.addWidget(self.group_combo)
        meta_row.addStretch(1)
        # Odebrání ze seznamu: nahrávka zůstane na disku, jen se pro ni nepočítá.
        self.restore_btn = QPushButton("")
        self.restore_btn.setFlat(True)
        self.restore_btn.setToolTip(tr("Vrátí do seznamu všechny odebrané nahrávky."))
        self.restore_btn.clicked.connect(self.restore_excluded)
        self.restore_btn.hide()
        meta_row.addWidget(self.restore_btn)
        self.meta_btn = QPushButton(tr("Metadata"))
        self.meta_btn.setToolTip(
            tr(
                "Sloupce metadat, manifest (CSV se sloupcem „path“ a údaji nahrávek; "
                "manifest.csv ve složce se načte sám) a úpravy v tabulce."
            )
        )
        self.meta_menu = QMenu(self.meta_btn)
        self.column_menu = self.meta_menu.addMenu(tr("+ Sloupec"))
        task_action = self.column_menu.addAction(tr("úloha (task)"))
        task_action.setToolTip(tr("Úloha každé nahrávky; víc úloh = víc samostatných běhů."))
        task_action.triggered.connect(lambda: self.add_meta_column(TASK))
        for column in meta.META_COLUMNS:
            action = self.column_menu.addAction(f"{meta.label(column)} ({column})")
            action.triggered.connect(lambda _c=False, col=column: self.add_meta_column(col))
        self.column_menu.addSeparator()
        custom = self.column_menu.addAction(tr("Vlastní název…"))
        custom.triggered.connect(lambda: self._ask_meta_column())
        self.save_action = self.meta_menu.addAction(tr("Uložit manifest…"))
        self.save_action.setToolTip(
            tr(
                "Zapíše metadata z tabulky (soubor i úpravy) jako CSV, kam ukážete; "
                "vedle nahrávek se načte příště samo. Jinak se do složky nic nezapisuje."
            )
        )
        self.save_action.setEnabled(False)
        self.save_action.triggered.connect(lambda: self.save_manifest())
        self.manifest_action = self.meta_menu.addAction(tr("Načíst jiný manifest…"))
        self.manifest_action.triggered.connect(lambda: self._browse_manifest())
        self.manifest_clear_action = self.meta_menu.addAction(tr("Nepoužít manifest"))
        self.manifest_clear_action.triggered.connect(lambda: self.clear_manifest())
        self.manifest_clear_action.setVisible(False)
        self.meta_menu.addSeparator()
        # odebrat jeden sloupec (i pravým tlačítkem na záhlaví), 1. 11. 2026
        self.remove_menu = self.meta_menu.addMenu(tr("Odebrat sloupec"))
        self.remove_menu.aboutToShow.connect(self._fill_remove_menu)
        self.restore_columns_action = self.meta_menu.addAction("")
        self.restore_columns_action.triggered.connect(lambda: self.files.restore_columns())
        self.restore_columns_action.setVisible(False)
        self.edits_action = self.meta_menu.addAction("")
        self.edits_action.setToolTip(
            tr("Zahodí úpravy metadat i přidané sloupce; manifest zůstane.")
        )
        self.edits_action.triggered.connect(lambda: self.discard_meta_edits())
        self.edits_action.setVisible(False)
        self.meta_menu.setToolTipsVisible(True)
        self.meta_btn.setMenu(self.meta_menu)
        meta_row.addWidget(self.meta_btn)
        self.bars.addWidget(bar)

        sel_bar = QFrame()
        sel_bar.setObjectName("card")
        sel_bar.setStyleSheet(
            "QFrame#card { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px; }"
        )
        sel_row = QHBoxLayout(sel_bar)
        sel_row.setContentsMargins(10, 3, 10, 3)
        sel_row.setSpacing(8)
        self.selection_label = QLabel("")
        self.selection_label.setStyleSheet("color: #1d4ed8; font-weight: 600;")
        sel_row.addWidget(self.selection_label)
        self.fill_btn = QPushButton(tr("Vyplnit…"))
        self.fill_btn.setToolTip(
            tr("Jedna hodnota do sloupce pro všechny vybrané řádky (třeba úloha, skupina, věk).")
        )
        self.fill_btn.setEnabled(False)
        self.fill_btn.clicked.connect(lambda: self._ask_fill())
        sel_row.addWidget(self.fill_btn)
        self.remove_btn = QPushButton(tr("Odebrat"))
        self.remove_btn.setToolTip(
            tr("Odebere vybrané nahrávky ze seznamu (Delete). Na disku zůstanou.")
        )
        self.remove_btn.setEnabled(False)
        self.remove_btn.clicked.connect(self.remove_selected)
        sel_row.addWidget(self.remove_btn)
        sel_row.addStretch(1)
        clear_sel = QLabel(f"<a href='#' style='color:{theme.ACCENT}'>{tr('✕ zrušit výběr')}</a>")
        clear_sel.linkActivated.connect(lambda _h: self.files.clearSelection())
        sel_row.addWidget(clear_sel)
        self.bars.addWidget(sel_bar)
        left_layout.addWidget(self.bars)

        # Tabulka nahrávek: název, délka (a ruční vstupy) jen ke čtení, metadata
        # upravitelná (`widgets/meta_table.py`); úpravy jdou do manifestu běhu, do
        # složky s nahrávkami nic (rozhodnuto 30. 10. 2026). Pacient, věk a pohlaví
        # jsou „povinné“ jen vzhledem: prázdné žlutě, když sloupec v tabulce je.
        self.files = MetaTable([tr("nahrávka"), tr("délka")], labels=True, right_columns=(1,))
        self.files.set_required({meta.SUBJECT, meta.AGE, meta.SEX})
        self.files.confirm_remove = self._confirm_remove_column
        # Úloha je sloupec s nabídkou (31. 10. 2026): dvojklik = rozbalovací seznam úloh,
        # prázdná buňka šedě „jako …“ podle „počítat jako“ z kroku 2; nic neblokuje.
        self.files.set_choices(
            TASK, [(t, tasks.label(t)) for t in contract.TASKS], header=tr("úloha")
        )
        self.files.edited.connect(self._meta_edited)
        self.files.cellDoubleClicked.connect(self.open_recording)
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._files_menu)
        self.files.itemSelectionChanged.connect(self._selection_changed)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.files, self.remove_selected)
        # Pruh seznamu k doplnění nad tabulkou: kolik hotovo, Hotovo, po doplnění všeho
        # nabídka zapsat manifest (údaje platí hned pro běh, proto „Hotovo“, ne „Uložit“).
        self.fix_banner = QFrame()
        self.fix_banner.setObjectName("card")
        fix_layout = QHBoxLayout(self.fix_banner)
        fix_layout.setContentsMargins(14, 8, 14, 8)
        fix_layout.setSpacing(10)
        self.fix_label = QLabel("")
        fix_layout.addWidget(self.fix_label, 1)
        self.fix_save_btn = QPushButton(tr("Uložit do manifestu…"))
        self.fix_save_btn.setToolTip(
            tr("Zapíše údaje do manifest.csv, příště se ke složce načtou samy.")
        )
        self.fix_save_btn.clicked.connect(lambda: self.save_manifest())
        fix_layout.addWidget(self.fix_save_btn)
        self.fix_done_btn = QPushButton(tr("Hotovo"))
        theme.set_role(self.fix_done_btn, "primary")
        self.fix_done_btn.setToolTip(tr("Zpět na celou tabulku; doplněné údaje zůstávají."))
        self.fix_done_btn.clicked.connect(lambda: self.end_fixing())
        fix_layout.addWidget(self.fix_done_btn)
        self.fix_banner.hide()
        left_layout.addWidget(self.fix_banner)
        left_layout.addWidget(self.files, 1)

        bottom1 = QHBoxLayout()
        self.continue_btn = QPushButton(tr("Pokračovat →"))
        theme.set_role(self.continue_btn, "primary")
        self.continue_btn.setEnabled(False)
        self.continue_btn.clicked.connect(lambda: self.go_to_step(2))
        bottom1.addStretch(1)
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

        task_card = QFrame()
        task_card.setObjectName("card")
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(16, 14, 16, 14)
        task_layout.setSpacing(10)
        # Název běhu nahoře nad úlohami (rozhodnuto 31. 10. 2026), vysvětlení v tooltipu.
        label_row = QHBoxLayout()
        label_row.setSpacing(10)
        label_title = QLabel(tr("Název běhu"))
        label_title.setObjectName("card_title")
        self.run_label_edit = QLineEdit()
        self.run_label_edit.setPlaceholderText(
            tr("nepovinné, třeba pacienti září, kontrola po 3 měsících")
        )
        self.run_label_edit.setToolTip(
            tr("Ukáže se v historii a nad výsledky; bez názvu se běh jmenuje podle protokolu.")
        )
        self.run_label_edit.setClearButtonEnabled(True)
        self._label_touched = False
        self.run_label_edit.textEdited.connect(self._label_edited)
        self.run_label_edit.returnPressed.connect(self._run)
        label_row.addWidget(label_title)
        label_row.addWidget(self.run_label_edit, 1)
        task_layout.addLayout(label_row)

        # Hlavička úloh: nadpis s nápovědou, vpravo co spustit (rozšířený režim) a jazyk
        # (jen když ho některý protokol potřebuje: přepis nebo jazykový rozbor).
        head = QHBoxLayout()
        head.setSpacing(10)
        self.step2_title = QLabel(tr("Úlohy"))
        self.step2_title.setObjectName("card_title")
        head.addWidget(self.step2_title)
        self.step2_hint = ElidedLabel("")
        self.step2_hint.setObjectName("muted")
        head.addWidget(self.step2_hint, 1)
        # Rozšířený režim: co spustit, jako malá přepínací tlačítka v hlavičce.
        self.mode_row = QWidget()
        mode_layout = QHBoxLayout(self.mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(4)
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
        self.mode_row.hide()
        head.addWidget(self.mode_row)
        self.language_label = QLabel(tr("Jazyk"))
        self.language_label.setObjectName("muted")
        self.language = QComboBox()
        self.language.setToolTip(
            tr(
                "Jazyk přepisu (Whisper) a jazykového rozboru (Stanza) pro tento běh. "
                "Přebije nastavení protokolu."
            )
        )
        self.language.currentIndexChanged.connect(self._language_changed)
        self.set_languages(list(contract.DEFAULT_LANGUAGES))
        self.language.setMinimumWidth(150)
        head.addWidget(self.language_label)
        head.addWidget(self.language)
        task_layout.addLayout(head)

        self.rows_box = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_box)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)

        # Nahrávky bez úlohy (mockup varianta B, 31. 10. 2026): žlutý pruh nad úlohami,
        # „počítat jako“ v normální velikosti a cesta zpět do kroku 1 je doplnit.
        self.unassigned_row = QFrame()
        self.unassigned_row.setObjectName("card")
        self.unassigned_row.setStyleSheet(
            f"QFrame#card {{ background: {theme.WARN_SOFT}; border: 1px solid #fcd34d; }}"
        )
        un_layout = QHBoxLayout(self.unassigned_row)
        un_layout.setContentsMargins(14, 10, 14, 10)
        un_layout.setSpacing(10)
        icon = QLabel("⚠")
        icon.setStyleSheet(f"color: {theme.WARN}; font-weight: 600;")
        un_layout.addWidget(icon)
        self.unassigned_label = QLabel("")
        self.unassigned_label.setStyleSheet("color: #92400e; font-weight: 600;")
        un_layout.addWidget(self.unassigned_label)
        un_layout.addSpacing(8)
        as_label = QLabel(tr("Počítat jako"))
        as_label.setStyleSheet("color: #92400e;")
        un_layout.addWidget(as_label)
        self.unassigned_combo = QComboBox()
        self.unassigned_combo.setMinimumWidth(170)
        for task in contract.TASKS:
            self.unassigned_combo.addItem(tasks.label(task), task)
        self.unassigned_combo.currentIndexChanged.connect(self._unassigned_changed)
        un_layout.addWidget(self.unassigned_combo)
        un_layout.addStretch(1)
        self.fill_tasks_link = QLabel(
            f"<a href='#' style='color:{theme.ACCENT}'>{tr('nebo doplnit v kroku 1 ›')}</a>"
        )
        self.fill_tasks_link.setToolTip(
            tr("Zpět na nahrávky, ukážou se jen ty bez úlohy; úloha se vybere dvojklikem.")
        )
        self.fill_tasks_link.linkActivated.connect(lambda _h: self.show_without_task(True))
        un_layout.addWidget(self.fill_tasks_link)
        self.unassigned_row.hide()
        task_layout.addWidget(self.unassigned_row)
        task_layout.addWidget(self.rows_box)

        # Kontrola kvality z hlavičky souboru: nahrávky kratší než práh své úlohy
        # (`assets/quality.yaml`), ještě před výpočtem. Zbytek až z výsledků.
        self.short_warning = QLabel("")
        self.short_warning.setWordWrap(True)
        self.short_warning.setStyleSheet(f"color: {theme.WARN};")
        self.short_warning.hide()
        task_layout.addWidget(self.short_warning)

        # Jen segmentace / Jen přepis: pod úlohami volby mezivýsledku.
        self.prepare_box = QWidget()
        prepare_layout = QVBoxLayout(self.prepare_box)
        prepare_layout.setContentsMargins(0, 4, 0, 0)
        prepare_layout.setSpacing(8)
        step3, self.step3_hint = _step(3, tr("Segmentace"), "")
        self.step3_title: QLabel = step3.itemAt(1).widget()
        prepare_layout.addLayout(step3)
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
        self.prepare_box.hide()
        task_layout.addWidget(self.prepare_box)

        # karta v posuvné ploše: volby Jen segmentace / Jen přepis nesmí zvětšovat
        # minimální výšku okna (1. 11. 2026, okno při přepnutí režimu měnilo velikost)
        body = QWidget()
        body.setObjectName("step2_body")
        body.setStyleSheet("#step2_body { background: transparent; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(task_card)
        body_layout.addStretch(1)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea { background: transparent; }")
        area.setWidget(body)
        page2_layout.addWidget(area, 1)

        # Patička: zpět, souhrn (běhy, nahrávky, zvuk, odhad) a Spustit. Dřív souhrnný pruh
        # v kartě a stavový řádek dole říkaly totéž dvakrát. U Jen segmentace / Jen přepis
        # místo čísel stav (model, jazyk).
        foot = QFrame()
        foot.setObjectName("card")
        bottom = QHBoxLayout(foot)
        bottom.setContentsMargins(10, 8, 10, 8)
        bottom.setSpacing(12)
        self.back_btn = QPushButton(tr("← Nahrávky"))
        self.back_btn.clicked.connect(lambda: self.go_to_step(1))
        bottom.addWidget(self.back_btn)
        self.summary = SummaryBar()
        self.summary.setStyleSheet("QFrame#summarybar { background: transparent; border: none; }")
        bottom.addWidget(self.summary, 1)
        # stav se zkracuje (celý v tooltipu): dlouhý text ve Výpočtu feature roztahoval
        # minimální šířku okna a okno při přepnutí režimu měnilo velikost
        self.status = ElidedLabel("")
        self.status.setObjectName("muted")
        bottom.addWidget(self.status, 1)
        self.run_btn = QPushButton(tr("Spustit"))
        theme.set_role(self.run_btn, "primary")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._run)
        bottom.addWidget(self.run_btn)
        page2_layout.addWidget(foot)
        self.steps.addWidget(page2)
        self.go_to_step(1)

    # --- kroky ------------------------------------------------------------------------

    def current_step(self) -> int:
        return self.steps.currentIndex() + 1

    def go_to_step(self, step: int) -> None:
        """Krok 1 (nahrávky) nebo 2 (úloha a protokol); stránka si ho pamatuje."""
        self.steps.setCurrentIndex(0 if step == 1 else 1)
        self.step_label.setText(self._steps_html(step))

    @staticmethod
    def _steps_html(step: int) -> str:
        """„1 Nahrávky › 2 Úlohy“, aktuální krok modře (jako v průvodcích)."""
        on = f"background:{theme.ACCENT_SOFT}; color:#1d4ed8;"
        off = "background:#f3f4f6; color:#6b7280;"
        parts = [
            f"<span style='{on if step == n else off} font-weight:600'>&nbsp;{text}&nbsp;</span>"
            for n, text in ((1, tr("1 Nahrávky")), (2, tr("2 Úlohy")))
        ]
        return " › ".join(parts)

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
        if self._run_after_doctor:
            self._run_after_doctor = False
            self._close_doctor_wait()
            self._run_checked()
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

    def set_rate_lookup(self, lookup: Callable[[str], float | None]) -> None:
        """Sekundy výpočtu na minutu zvuku podle slugu protokolu (z nastavení)."""
        self._seconds_per_audio_minute = lookup

    def durations_for(self, paths: list[Path]) -> list[float | None]:
        """Délky zvuku k cestám ve stejném pořadí; None, kde ji nejde přečíst."""
        known = {rec.path: rec.duration for rec in self._recordings}
        return [known.get(p) for p in paths]

    def expected_seconds(
        self, proto: Protocol | None = None, recordings: list[Recording] | None = None
    ) -> float | None:
        """Odhad dávky: podle minut zvuku, kde jsou známé, jinak na nahrávku.

        Bez `recordings` celá dávka (všechny nahrávky kroku 1)."""
        proto = proto or self.current_protocol()
        recs = self._recordings if recordings is None else recordings
        if proto is None or not recs:
            return None
        slug = proto.slug()
        rate = self._seconds_per_audio_minute(slug)
        per_file = self._seconds_per_file(slug)
        known = [rec.duration for rec in recs if rec.duration]
        unknown = len(recs) - len(known)
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
        """Souhrn kroku 1 jedním řádkem: nahrávky, zvuk, pacienti; odkaz na doplnění."""
        self._update_tiles()
        text = self.folder.text().strip()
        self.browse_btn.setText(tr("Změnit…") if text else tr("Vybrat…"))
        if not text:
            self.step1_hint.setText(tr("Vyber složku s nahrávkami."))
        elif not self._recordings and not self._excluded:
            self.step1_hint.setText(tr("žádná nahrávka nenalezena"))
        else:
            parts = [self._count_text()]
            known = [rec.duration for rec in self._recordings if rec.duration]
            if known:
                parts.append(tr("{t} zvuku").format(t=format_seconds(sum(known))))
            if meta.SUBJECT in self.files.columns:
                merged = self.files.rows_merged()
                subjects = {m.get(meta.SUBJECT, "") for m in merged.values()} - {""}
                parts.append(tr("{n} pacientů").format(n=len(subjects)))
            self.step1_hint.setText("  ·  ".join(parts))

    def refresh_summary(self) -> None:
        """Po běhu: nová doba na nahrávku do řádků úloh i do souhrnu editoru."""
        self._sync_rows()

    def set_protocols(self, protocols: list[Protocol], *, current: str = "") -> None:
        self._protocols = protocols
        chosen = next((p for p in protocols if p.name == current), None)
        self._rows_key = ()  # protokoly se změnily: řádky znovu
        # vybraný protokol (naposledy použitý, nově uložený) i s jeho úlohou
        if chosen is not None and self.select_protocol(chosen.name):
            return
        self._sync_rows()
        self._protocol_changed(self.current_protocol())

    def _card_infos(self) -> dict[str, tuple[list[str], str]]:
        """Protokol → (providery, nápověda ceny) pro druhý řádek výběru a stav."""
        if self._library is None:
            return {}
        try:
            catalog = self._library.features()
            providers = self._library.providers()
        except LibraryError:
            return {}
        raw = card_infos(self._protocols, catalog, providers, self._seconds_per_file)
        return {name: (list(p), hint) for name, (p, hint) in raw.items()}

    def select_protocol(self, name: str) -> bool:
        """Vybere protokol v řádku jeho úlohy; bez úloh v manifestu přepne i úlohu."""
        proto = next((p for p in self._protocols if p.name == name), None)
        if proto is None:
            return False
        self._row_choice[proto.task] = proto.name
        if not self._multi():
            if self._single_task != proto.task:
                self._single_task = proto.task
                self._rows_key = ()
        self._sync_rows()
        row_task = next((t for t in self._task_rows if tasks.base_task(t) == proto.task), None)
        if row_task is None:
            return False
        row = self._task_rows[row_task]
        if not row.protocol.select_name(proto.name):
            return False
        self._current_task = row_task
        self._protocol_changed(proto)
        return True

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
        self._title.setToolTip(self.subtitle.text())

    def set_advanced(self, advanced: bool) -> None:
        self._advanced = advanced
        self.mode_row.setVisible(advanced)
        if not advanced:
            self.set_mode("extract")  # základní režim umí jen výpočet feature
        self._sync_rows()
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
        self.prepare_box.setVisible(mode != "extract")
        self.segment_opts.setVisible(mode == "segments")
        self.transcribe_opts.setVisible(mode == "transcript")
        self.step3_title.setText(
            {
                "extract": tr("Protokol"),
                "segments": tr("Segmentace"),
                "transcript": tr("Přepis (Whisper)"),
            }[mode]
        )
        self._rows_key = ()  # stav řádků podle režimu (DDK nemá mezivýsledek)
        self._update_run_state()

    @staticmethod
    def _task_allows(task: str, mode: str) -> bool:
        """Přepis jen u souvislé řeči, segmentace ne u DDK."""
        base = tasks.base_task(task)
        if mode == "transcript":
            return base in contract.TRANSCRIPT_TASKS
        if mode == "segments":
            return base in contract.SEGMENT_TASKS
        return True

    def _mode_allowed(self, mode: str) -> bool:
        """Režim jde, když ho umí aspoň jedna úloha; bez úloh vše."""
        if mode == "extract":
            return True
        groups = [t for t, recs in self._task_groups().items() if recs] or [self._single_task]
        return any(self._task_allows(t, mode) for t in groups)

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

    # --- složka ---------------------------------------------------------------

    def _browse(self) -> None:
        start = self.folder.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, tr("Složka s nahrávkami"), start)
        if chosen:
            self.set_folder(Path(chosen))

    def _folder_edited(self) -> None:
        if self.folder.text().strip() != self._scanned_folder:
            self.rescan()

    def rescan(self) -> None:
        text = self.folder.text().strip()
        if text != self._scanned_folder:
            self._excluded.clear()
            self.files.reset()  # úpravy metadat patří složce; jiná složka = od začátku
            self._scanned_folder = text
            self._fix_mode = ""  # seznam k doplnění patří složce
            self.files.freeze(None)
            self._single_task = ""  # úloha bez manifestu patří složce, nic se nepředvybírá
            self._rows_key = ()
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
        self.bars.setCurrentIndex(1 if n else 0)  # s výběrem lišta akcí s výběrem

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
        columns = [c for c in self.files.columns if c != TASK]
        folder = self.folder.text().strip()
        merged = Manifest(
            path=base.path if base else Path(folder or ".") / "manifest.csv",
            columns=columns,
        )
        for key, row in self.files.rows_merged().items():
            if row.get(TASK):
                merged.tasks[key] = row[TASK]  # úloha z manifestu nebo doplněná v tabulce
            if any(row.get(c) for c in columns):
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

    def remove_meta_column(self, column: str) -> bool:
        """Odebere sloupec metadat z tabulky (manifest ve složce se nemění)."""
        return self.files.request_remove(column)

    def _confirm_remove_column(self, column: str) -> bool:
        """Úloha mění počet běhů: před odebráním se zeptat; ostatní bez dotazu."""
        if column != TASK:
            return True
        answer = QMessageBox.question(
            self,
            tr("Odebrat sloupec úloha"),
            tr(
                "Bez sloupce úloha se všechny nahrávky spočítají jako jedna úloha, kterou "
                "vyberete v kroku 2 (místo běhu na každou úlohu). Odebrat?"
            ),
        )
        return answer == QMessageBox.StandardButton.Yes

    def _fill_remove_menu(self) -> None:
        self.remove_menu.clear()
        columns = self.files.columns
        if not columns:
            self.remove_menu.addAction(tr("žádný sloupec")).setEnabled(False)
        for column in columns:
            label = tr("úloha (task)") if column == TASK else self.files.display_header(column)
            action = self.remove_menu.addAction(label)
            action.triggered.connect(lambda _c=False, col=column: self.remove_meta_column(col))

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
        for column in (TASK, *meta.META_COLUMNS):
            if column not in choices:
                choices.append(column)

        def column_label(c: str) -> str:
            if c == TASK:
                return tr("úloha (task)")
            return f"{meta.label(c)} ({c})" if c in meta.META_COLUMNS else c

        labels = [column_label(c) for c in choices]
        label, ok = QInputDialog.getItem(
            self, tr("Vyplnit vybrané"), tr("Sloupec:"), labels, 0, False
        )
        if not ok:
            return
        column = choices[labels.index(label)]
        if column == TASK:
            names = [tasks.label(t) for t in contract.TASKS]
            name, ok = QInputDialog.getItem(
                self,
                tr("Vyplnit vybrané"),
                tr("Úloha pro {n} vybraných nahrávek:").format(n=len(rows)),
                names,
                0,
                False,
            )
            if ok:
                self.fill_selected(TASK, contract.TASKS[names.index(name)])
            return
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
        self._task_cache = None  # úloha mohla přibýt nebo se změnit
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
        """Dlaždice „bez údajů“: seznam k doplnění, druhý klik = Hotovo."""
        if self._fix_mode == "missing":
            self.end_fixing()
        else:
            self.start_fixing("missing")

    def _apply_filter(self) -> None:
        self.files.set_filter(self.search.text())
        self.files.refresh_done()
        # dlaždice „bez údajů“ je teď odkaz v souhrnu; stav říká její text (doplňuješ)
        self._update_fix_banner()
        self._update_tiles()

    def _update_tiles(self) -> None:
        """Žlutý odkaz „n bez údajů · doplnit ›“ v souhrnu kroku 1."""
        n = len(self._recordings)
        missing = self.files.missing_indices() if n else []
        if missing:
            labels = ", ".join(meta.label(c) for c in self.files.missing_columns())
            text = (
                tr("{n} bez údajů · doplňuješ").format(n=len(missing))
                if self._fix_mode == "missing"
                else tr("{n} bez údajů · doplnit ›").format(n=len(missing))
            )
            self.missing_tile.setText(text)
            self.missing_tile.setToolTip(tr("chybí: {columns}").format(columns=labels))
        self.missing_tile.setVisible(bool(missing))
        if hasattr(self, "short_warning"):  # seznam nahrávek se změnil, varování kroku 2 taky
            self._update_short_warning()

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
        if manifest is not None and manifest.tasks:
            columns.insert(0, TASK)  # úloha z manifestu jako první sloupec metadat
        # Ruční vstupy (labely, přepis vedle nahrávky) zajímají výzkumníka,
        # klinik je nikdy nemá; v základním režimu se sloupec neukazuje.
        headers = [tr("nahrávka"), tr("délka")]
        if self._advanced:
            headers.append(tr("ruční vstupy"))
        self.files.set_leading_headers(headers)
        self._task_cache = None
        root = Path(self.folder.text().strip()) if self.folder.text().strip() else None
        rows: list[MetaRow] = []
        for rec in self._recordings:
            row_meta = manifest.meta_for(rec.path) if manifest else None
            base = dict(row_meta or {})
            own = manifest.tasks.get(key_for(rec.path), "") if manifest else ""
            if own:
                base[TASK] = own
            leading = [rec.name, format_duration(rec.duration)]
            if self._advanced:
                manual = [tr("labely")] * rec.has_labels + [tr("přepis")] * rec.has_transcript
                leading.append(", ".join(manual))
            rows.append(
                MetaRow(
                    key_for(rec.path),
                    leading,
                    base,
                    tooltip=tr("{path}\nDvojklik přehraje.").format(path=rec.path),
                    muted=row_meta is None,
                    seconds=rec.duration,
                    folder=_subfolder(rec.path, root),
                )
            )
        self._update_task_hint(refill=False)
        self.files.set_rows(rows, columns)
        self._update_group_choices()
        self._update_meta_label()
        self._apply_filter()

    def _update_meta_label(self) -> None:
        """Štítek manifestu v souhrnu (soubor, shoda, úpravy) a položky nabídky Metadata."""
        base = self._manifest
        edits = self.meta_edits()
        columns = self.files.columns
        self.manifest_clear_action.setVisible(base is not None)
        self.edits_action.setVisible(self.files.has_changes())
        removed = self.files.removed_columns()
        self.restore_columns_action.setVisible(bool(removed))
        self.restore_columns_action.setText(
            tr("Vrátit odebrané sloupce ({n})").format(n=len(removed))
        )
        self.edits_action.setText(tr("Zahodit úpravy ({n})").format(n=edits))
        self.save_action.setEnabled(self.manifest() is not None)
        role, tip = "neutral", ""
        if base is None:
            if self.files.has_changes():
                text = tr("Vlastní metadata · upraveno: {n}").format(n=edits)
                tip = ", ".join(columns)
            else:
                text = ""
        elif base.problems:
            text = tr("{name} · chyba").format(name=base.path.name)
            tip = base.problems[0]
            role = "missing"
        else:
            paths = [rec.path for rec in self._recordings]
            matched = base.matched(paths)
            if matched == len(paths):
                text = tr("{name} ✓").format(name=base.path.name)
                role = "ok"
            else:
                text = tr("{name} · {matched} z {total}").format(
                    name=base.path.name, matched=matched, total=len(paths)
                )
                role = "warn"
            if edits:
                text += tr(" · upraveno: {n}").format(n=edits)
            tip = tr("Sloupce: {columns}").format(columns=", ".join(columns) or "–")
        self.manifest_label.setText(text)
        self.manifest_label.setToolTip(tip)
        self.manifest_label.setVisible(bool(text))
        theme.set_role(self.manifest_label, role)

    # --- protokol -------------------------------------------------------------------

    def _protocol_changed(self, proto: Protocol | None) -> None:
        self._sync_mode_to_protocol()
        self._update_run_state()

    # --- spuštění -------------------------------------------------------------

    # --- úloha nahrávek ---------------------------------------------------------------

    def default_task(self) -> str:
        """Úloha nahrávek bez úlohy v manifestu: „počítat jako“, bez manifestu zvolená úloha."""
        return self._unassigned_task if self._multi() else self._single_task

    def manifest_task(self, path: Path) -> str:
        """Úloha nahrávky z tabulky (manifest nebo doplněná); prázdné = „počítat jako“."""
        if self._task_cache is None:
            self._task_cache = (
                {k: row.get(TASK, "") for k, row in self.files.rows_merged().items()}
                if TASK in self.files.columns
                else {}
            )
        return self._task_cache.get(key_for(path), "")

    def task_of(self, path: Path) -> str:
        """Úloha, jako kterou se nahrávka spočítá (manifest má přednost)."""
        return self.manifest_task(path) or self.default_task()

    def _task_column(self) -> bool:
        """Sloupec úloha v tabulce (z manifestu nebo přidaný); jinak všude zvolená úloha."""
        return TASK in self.files.columns

    def _multi(self) -> bool:
        """Úlohy z manifestu: řádek na úlohu, jinak jeden řádek s výběrem úlohy."""
        return self._task_column()

    def _task_groups(self) -> dict[str, list[Recording]]:
        """Úloha → nahrávky dávky, v pořadí úloh; bez manifestu jedna skupina."""
        if not self._multi():
            return {self._single_task: list(self._recordings)}
        groups: dict[str, list[Recording]] = {}
        for rec in self._recordings:
            groups.setdefault(self.task_of(rec.path), []).append(rec)
        return dict(sorted(groups.items(), key=lambda kv: tasks.task_order(kv[0])))

    def task_rows(self) -> dict[str, TaskRowWidget]:
        """Řádky úloh kroku 2 podle úlohy (pro testy a hlavní okno)."""
        return dict(self._task_rows)

    def _auto_unassigned(self) -> str:
        """Výchozí „počítat jako“: první úloha, kterou tabulka nemá, jinak fonace."""
        present = {self.manifest_task(rec.path) for rec in self._recordings}
        return next((t for t in contract.TASKS if t not in present), contract.TASKS[0])

    def _update_task_hint(self, *, refill: bool = True) -> None:
        """Šedé „jako …“ v prázdných buňkách úlohy podle „počítat jako“."""
        task = self.default_task()
        self._shown_task = task
        self.files.set_hint(TASK, tr("jako {task}").format(task=tasks.label(task)) if task else "")
        if refill and self._task_column():
            self.files.refill()
            self._apply_filter()

    def show_without_task(self, only: bool = True) -> None:
        """Krok 1 se seznamem nahrávek bez úlohy (z kroku 2 „Doplnit v kroku 1“), nebo konec."""
        if not only:
            self.end_fixing()
            return
        if self._task_column():
            self.go_to_step(1)
            self.start_fixing("task")

    # --- seznam k doplnění ----------------------------------------------------------------

    def _row_done(self, key: Path) -> bool:
        """Řádek seznamu k doplnění je hotový: úloha vyplněná, nebo povinné údaje."""
        merged = self.files.merged(key)
        if self._fix_mode == "task":
            return bool(merged.get(TASK))
        required = [c for c in (meta.SUBJECT, meta.AGE, meta.SEX) if c in merged]
        return all(merged.get(c) for c in required)

    def start_fixing(self, mode: str) -> None:
        """Zamrazí neúplné řádky („missing“) nebo řádky bez úlohy („task“) do seznamu."""
        if mode == "missing":
            keys = {self.files.keys()[i] for i in self.files.missing_indices()}
        else:
            keys = {
                key_for(rec.path) for rec in self._recordings if not self.manifest_task(rec.path)
            }
        if not keys:
            self.end_fixing()
            return
        self._fix_mode = mode
        self.files.set_done_check(self._row_done)
        self.files.freeze(keys)
        self._apply_filter()

    def end_fixing(self) -> None:
        """Hotovo: zpět na celou tabulku, doplněné údaje zůstávají."""
        self._fix_mode = ""
        self.files.freeze(None)
        self._apply_filter()

    def fixing(self) -> str:
        return self._fix_mode

    def _update_fix_banner(self) -> None:
        keys = self.files.frozen_keys()
        if not self._fix_mode or keys is None:
            self.fix_banner.hide()
            return
        done = sum(1 for k in keys if self._row_done(k))
        total = len(keys)
        what = tr("úlohy") if self._fix_mode == "task" else tr("údaje")
        if done < total:
            self.fix_label.setText(
                tr("Doplňuješ {what} · hotovo {done} z {total} · zbývá {left}").format(
                    what=what, done=done, total=total, left=total - done
                )
            )
            self.fix_banner.setStyleSheet(
                "QFrame#card { background: #eff6ff; border: 1px solid #bfdbfe; }"
            )
            self.fix_label.setStyleSheet("color: #1d4ed8; font-weight: 600;")
        else:
            self.fix_label.setText(tr("✓ Vše doplněno ({n} nahrávek)").format(n=total))
            self.fix_banner.setStyleSheet(
                f"QFrame#card {{ background: {theme.OK_SOFT}; border: 1px solid #86efac; }}"
            )
            self.fix_label.setStyleSheet("color: #166534; font-weight: 600;")
        self.fix_save_btn.setVisible(done == total)
        self.fix_banner.show()

    def set_unassigned_task(self, task: str) -> None:
        """Jako jakou úlohu počítat nahrávky bez úlohy v manifestu."""
        idx = self.unassigned_combo.findData(task)
        if idx >= 0:
            self._unassigned_auto = False
            self.unassigned_combo.setCurrentIndex(idx)
            self._unassigned_changed()

    def _unassigned_changed(self, _idx: int = 0) -> None:
        if self._syncing:
            return
        task = str(self.unassigned_combo.currentData() or "")
        if task and task != self._unassigned_task:
            self._unassigned_auto = False
            self._unassigned_task = task
            self._update_task_hint()
            self._update_run_state()

    def _protocols_for(self, task: str) -> list[Protocol]:
        base = tasks.base_task(task)
        return [p for p in self._protocols if p.task == base]

    def _choice_for(self, task: str) -> str:
        """Protokol řádku: naposledy vybraný pro úlohu, jinak přibalený výchozí."""
        if not task:
            return ""  # úloha ještě není vybraná
        base = tasks.base_task(task)
        names = [p.name for p in self._protocols_for(task)]
        chosen = self._row_choice.get(base, "")
        if chosen in names:
            return chosen
        builtin = [p for p in self._protocols_for(task) if p.builtin]
        return (builtin or self._protocols_for(task) or [Protocol(name="", task=base)])[0].name

    def _sync_rows(self) -> None:
        """Řádky úloh podle nahrávek a manifestu; stavba jen při změně sady úloh."""
        if self._syncing:
            return
        self._syncing = True
        try:
            if self._multi():
                if self._unassigned_auto or not self._unassigned_task:
                    self._unassigned_task = self._auto_unassigned()
                idx = self.unassigned_combo.findData(self._unassigned_task)
                self.unassigned_combo.setCurrentIndex(max(idx, 0))
            groups = self._task_groups()
            key = (self._multi(), tuple(groups), tuple(p.name for p in self._protocols))
            if key != self._rows_key:
                self._build_rows(list(groups))
                self._rows_key = key
            if self._current_task not in self._task_rows:
                self._current_task = next(iter(self._task_rows), "")
            for task, row in self._task_rows.items():
                self._refresh_row(task, row, groups.get(task, []))
            unassigned = [
                rec
                for rec in self._recordings
                if self._multi() and not self.manifest_task(rec.path)
            ]
            self.unassigned_label.setText(tr("{n} nahrávek nemá úlohu.").format(n=len(unassigned)))
            self.unassigned_row.setVisible(bool(unassigned))
            if self._task_column() and self.default_task() != self._shown_task:
                self._update_task_hint()  # šedé „jako …“ v tabulce podle „počítat jako“
        finally:
            self._syncing = False

    def _build_rows(self, task_keys: list[str]) -> None:
        for row in self._task_rows.values():
            self.rows_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._task_rows = {}
        multi = self._multi()
        for task in task_keys:
            row = TaskRowWidget(
                task,
                switch=multi,
                task_choices=None if multi else list(contract.TASKS),
            )
            row.protocol.set_protocols(self._protocols_for(task), current=self._choice_for(task))
            row.protocol.currentIndexChanged.connect(
                lambda _i, t=task: self._row_protocol_changed(t)
            )
            row.toggled.connect(lambda _on: self._update_run_state())
            row.clicked.connect(lambda t=task: self._row_clicked(t))
            row.details_requested.connect(
                lambda t=task: self.show_protocol_detail(self._row_protocol(t))
            )
            if row.task_combo is not None:
                if not task:  # bez předvolby: nahoře výzva, dokud se úloha nevybere
                    row.task_combo.insertItem(0, tr("vyberte úlohu…"), "")
                    row.task_combo.setCurrentIndex(0)
                row.task_combo.currentIndexChanged.connect(
                    lambda _i, r=row: self._single_task_changed(r)
                )
            self.rows_layout.addWidget(row)
            self._task_rows[task] = row

    def _row_protocol(self, task: str) -> Protocol | None:
        row = self._task_rows.get(task)
        return row.protocol.current_protocol() if row is not None else None

    def _row_clicked(self, task: str) -> None:
        if task != self._current_task:
            self._current_task = task
            self._protocol_changed(self.current_protocol())

    def _row_protocol_changed(self, task: str) -> None:
        if self._syncing:
            return
        proto = self._row_protocol(task)
        if proto is not None:
            self._row_choice[proto.task] = proto.name
        self._current_task = task
        self._protocol_changed(proto)

    def _single_task_changed(self, row: TaskRowWidget) -> None:
        if self._syncing or row.task_combo is None:
            return
        task = str(row.task_combo.currentData() or "")
        if not task or task == self._single_task:
            return
        self._single_task = task
        self._rows_key = ()
        self._sync_rows()
        self._protocol_changed(self.current_protocol())

    def _row_sub(self, proto: Protocol, infos: dict[str, tuple[list[str], str]]) -> str:
        """Druhý řádek výběru protokolu: počet sloupců a cena."""
        hint = infos.get(proto.name, ([], ""))[1]
        columns = 0
        if self._library is not None:
            try:
                catalog = self._library.features(proto.task)
                providers = self._library.providers()
                columns = summarize(
                    proto.select([f.name for f in catalog]), catalog, providers
                ).columns
            except LibraryError:
                columns = 0
        parts = [tr("{n} sloupců").format(n=columns)] if columns else []
        if hint:
            parts.append(hint)
        return " · ".join(parts)

    def _refresh_row(self, task: str, row: TaskRowWidget, recs: list[Recording]) -> None:
        infos = self._card_infos()
        protocols = self._protocols_for(task)
        row.protocol.blockSignals(True)
        current = row.protocol.current_protocol()
        row.protocol.set_protocols(
            protocols,
            current=current.name if current is not None else self._choice_for(task),
            subs={p.name: self._row_sub(p, infos) for p in protocols},
        )
        row.protocol.blockSignals(False)
        if row.task_combo is not None:
            idx = row.task_combo.findData(task)
            row.task_combo.blockSignals(True)
            row.task_combo.setCurrentIndex(max(idx, 0))
            row.task_combo.blockSignals(False)
        audio = sum(r.duration or 0 for r in recs)
        sub = tr("{n} nahrávek · {audio}").format(n=len(recs), audio=format_duration(audio))
        if self._multi():
            loose = sum(1 for r in recs if not self.manifest_task(r.path))
            if loose:
                sub += (
                    f" · <span style='color:#92400e; background:{theme.WARN_SOFT};"
                    f" font-weight:600'>&nbsp;"
                    + tr("+{n} bez úlohy").format(n=loose)
                    + "&nbsp;</span>"
                )
        row.sub.setText(sub)
        row.sub.setTextFormat(Qt.TextFormat.RichText)
        if not task:  # úloha ještě není vybraná: červený řádek, bez protokolu
            row.protocol.set_protocols([])
            row.protocol.setPlaceholderText(tr("protokol podle úlohy"))
            row.protocol.setEnabled(False)
            row.details.hide()
            row.set_status("none", "")
            row.set_count("–")
            row.sub.setText(sub)
            if row.task_combo is not None:
                row.task_combo.setToolTip(tr("Vyberte, jako jaká úloha se nahrávky počítají."))
                row.task_combo.setStyleSheet("QComboBox { border: 1px solid #f87171; }")
            row.set_off_look(False)
            row.setProperty("state", "missing")
            row.style().unpolish(row)
            row.style().polish(row)
            return
        allowed = self._task_allows(task, self._mode)
        row.set_off_look(not row.is_on() or not recs or not allowed)
        if row.switch is not None:
            row.switch.setEnabled(allowed and bool(recs))
        proto = row.protocol.current_protocol()
        extract = self._mode == "extract"
        row.details.setVisible(extract)
        row.protocol.setEnabled(extract or self._mode == "transcript")
        if not allowed:
            row.set_status(
                "none",
                tr("bez mezivýsledku") if self._mode == "segments" else tr("bez přepisu"),
            )
            row.set_count("–")
            return
        if proto is None:
            row.set_status("warn", tr("žádný protokol"))
            row.set_count("–")
            return
        if not extract:
            row.set_status("info", contract.RUN_MODE_LABELS[self._mode].lower())
        else:
            missing = self.missing_providers(proto)
            providers = self._card_infos().get(proto.name, ([], ""))[0]
            if missing:
                names = ", ".join(contract.PROVIDER_SHORT.get(m, m) for m in missing)
                row.set_status(
                    "warn",
                    tr("chybí: {names}").format(names=names),
                    tr("Feature, které to potřebují, zůstanou prázdné. Řeší se na Prostředí."),
                )
            elif providers:
                row.set_status(
                    "info", ", ".join(contract.PROVIDER_SHORT.get(p, p) for p in providers)
                )
            else:
                row.set_status("ok", tr("bez modelů"))
        seconds = self.expected_seconds(proto, recs) if extract else None
        if seconds is None or not recs:
            row.set_count("–" if recs else "")
        else:
            row.set_count(
                "~" + format_seconds(seconds),
                tr("{s} / nahr.").format(s=format_seconds(seconds / len(recs))),
            )

    def planned_runs(self) -> list[tuple[str, Protocol, list[Path]]]:
        """Běhy, které Spustit zařadí: zapnuté úlohy s nahrávkami, protokol s jazykem."""
        runs: list[tuple[str, Protocol, list[Path]]] = []
        for task, recs in self._task_groups().items():
            row = self._task_rows.get(task)
            if row is None or not recs or not row.is_on():
                continue
            if not self._task_allows(task, self._mode):
                continue
            proto = row.protocol.current_protocol()
            if proto is None:
                continue
            runs.append((task, self._with_language(proto), [r.path for r in recs]))
        return runs

    def _update_language_visibility(self, runs: list[tuple[str, Protocol, list[Path]]]) -> None:
        """Jazyk jen když ho některý běh potřebuje (přepis, jazykový rozbor); bez katalogu vždy."""
        if self._mode == "transcript":
            needed = True
        else:
            infos = self._card_infos()
            needed = not infos or any(
                {"transcript", "nlp"} & set(infos.get(proto.name, ([], ""))[0])
                for _task, proto, _paths in runs
            )
        self.language_label.setVisible(needed)
        self.language.setVisible(needed)

    def _update_summary(self, runs: list[tuple[str, Protocol, list[Path]]]) -> None:
        """Souhrn v patičce: běhy a nahrávky, zvuk, odhad a podíl úloh."""
        durations = {rec.path: rec.duration for rec in self._recordings}
        n_files = sum(len(paths) for _t, _p, paths in runs)
        audio = sum(durations.get(path) or 0 for _t, _p, paths in runs for path in paths)
        estimates: list[tuple[str, float]] = []
        unknown = False
        for task, proto, paths in runs:
            recs = [r for r in self._recordings if r.path in set(paths)]
            seconds = self.expected_seconds(proto, recs) if self._mode == "extract" else None
            if seconds is None:
                unknown = True
            else:
                estimates.append((task, seconds))
        first = (
            runs_text(len(runs)),
            tr("{n} nahrávek").format(n=n_files),
        )
        figures = [first, (format_duration(audio), tr("zvuku"))] if runs else []
        if estimates and not unknown:
            total = sum(s for _t, s in estimates)
            figures.append(("~" + format_seconds(total), tr("odhad výpočtu")))
        self.summary.set_figures(figures)
        self.summary.share.set_parts(
            [(s, SHARE_COLORS[i % len(SHARE_COLORS)]) for i, (_t, s) in enumerate(estimates)]
            if len(estimates) > 1
            else []
        )
        if len(estimates) > 1:
            longest = max(estimates, key=lambda e: e[1])[0]
            self.summary.set_hint(tr("nejdéle {task}").format(task=tasks.label(longest)))
        else:
            self.summary.set_hint("")
        extract = self._mode == "extract"
        self.summary.setVisible(bool(runs) and extract)
        self.status.setVisible(not (bool(runs) and extract))

    def short_recordings(self, task: str | None = None) -> list[Recording]:
        """Nahrávky kratší než práh délky své úlohy (podle hlavičky souboru).

        Úloha z manifestu má přednost, jinak `task`, jinak úloha z kroku 2."""
        default = task if task is not None else self.default_task()
        out: list[Recording] = []
        for rec in self._recordings:
            own = self.manifest_task(rec.path) or default
            limit = quality.min_duration(own) if own else None
            if limit is not None and rec.duration and rec.duration < limit:
                out.append(rec)
        return out

    def _update_short_warning(self) -> None:
        """Krátké nahrávky jedním řádkem (celá věta v tooltipu); práh podle úlohy nahrávky."""
        proto = self.current_protocol()
        short = self.short_recordings()
        if not short or proto is None:
            self.short_warning.hide()
            return
        names = ", ".join(r.name for r in short[:3]) + (" …" if len(short) > 3 else "")
        if len(self._task_groups()) > 1:  # víc úloh: každá má vlastní práh
            limit_text = tr("pod prahem své úlohy")
        else:
            limit = quality.min_duration(proto.task) or 0
            limit_text = tr("{task} pod {s} s").format(task=tasks.label(proto.task), s=f"{limit:g}")
        self.short_warning.setText(
            tr("⚠ Krátké nahrávky ({n}, {limit}): {names} · budou označené").format(
                n=len(short), limit=limit_text, names=names
            )
        )
        self.short_warning.setToolTip(
            tr(
                "Nahrávky kratší, než úloha potřebuje (prahy v Nastavení › Kontrola kvality). "
                "Spočítají se, ale ve výsledcích budou označené jako podezřelé."
            )
        )
        self.short_warning.show()

    def _update_run_state(self) -> None:
        self._sync_rows()
        proto = self.current_protocol()
        runs = self.planned_runs()
        ok = bool(runs) and self._library is not None
        self.run_btn.setEnabled(ok)
        self.run_btn.setText(
            tr("Spustit · {runs}").format(runs=runs_text(len(runs)))
            if len(runs) > 1
            else tr("Spustit")
        )
        self._update_summary(runs)
        self._update_short_warning()
        self._update_language_visibility(runs)
        self._update_step1_hint()
        multi = self._multi()
        self.step2_title.setText(tr("Úlohy") if multi else tr("Úloha"))
        if multi:
            self.step2_hint.setText(
                tr("{n} · každá úloha = samostatný běh").format(n=len(self._task_rows))
            )
        else:
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
        self.status.setStyleSheet("")
        if not self._recordings:
            self.status.setText(tr("Vyber složku s nahrávkami."))
        else:
            if not multi and not self._single_task:
                self.status.setText(tr("Vyberte úlohu."))
                self.status.setStyleSheet("color: #b91c1c; font-weight: 600;")
            elif proto is None:
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
            elif len(runs) > 1:
                self.status.setText(
                    self._count_text()
                    + " · "
                    + runs_text(len(runs))
                    + f" · {contract.language_label(self.language_code())}"
                )
            else:
                self.status.setText(
                    self._count_text() + f" · {contract.TASK_LABELS[proto.task]} · "
                    f"{contract.language_label(self.language_code())} · {proto.display_name}"
                )

    def current_protocol(self) -> Protocol | None:
        """Protokol řádku, se kterým se naposledy pracovalo (bez manifestu jediný řádek)."""
        proto = self._row_protocol(self._current_task)
        if proto is None and self._task_rows:
            proto = self._row_protocol(next(iter(self._task_rows)))
        return proto

    def effective_protocol(self) -> Protocol | None:
        """Protokol pro běh: vybraný protokol tak, jak je uložený, plus jazyk z lišty."""
        base = self.current_protocol()
        return self._with_language(base) if base is not None else None

    def _with_language(self, base: Protocol) -> Protocol:
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
        if not self.planned_runs():
            return
        if self._mode != "extract":
            self._prepare(self._mode)
            return
        if self._library is not None and not self._doctor_known:
            # Zpráva doctor ještě nedorazila (klik hned po startu): počkat na pozadí,
            # ať klinik nespustí hodinový běh s prázdnými sloupci. Storno = nespouštět.
            self._run_after_doctor = True
            self._check_requested()
            dialog = QProgressDialog(tr("Kontroluji prostředí…"), tr("Storno"), 0, 0, self)
            dialog.setWindowTitle(tr("SpeechScope"))
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setMinimumDuration(300)  # rychlá odpověď se bez dialogu obejde
            dialog.canceled.connect(self._cancel_doctor_wait)
            self._doctor_wait = dialog
            dialog.setValue(0)  # od teď běží odpočet do zobrazení
            return
        self._run_checked()

    def _cancel_doctor_wait(self) -> None:
        self._run_after_doctor = False
        self._close_doctor_wait()

    def _close_doctor_wait(self) -> None:
        if self._doctor_wait is not None:
            dialog, self._doctor_wait = self._doctor_wait, None
            dialog.blockSignals(True)
            dialog.close()
            dialog.deleteLater()

    def _run_checked(self) -> None:
        runs = self.planned_runs()
        if not runs:
            return
        proto = runs[0][1]
        missing: list[str] = []
        for _task, run_proto, _paths in runs:
            missing += [m for m in self.missing_providers(run_proto) if m not in missing]
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
        short = self.short_recordings()
        if short:
            limit = quality.min_duration(self.task_of(short[0].path)) or 0
            answer = QMessageBox.question(
                self,
                tr("Krátké nahrávky"),
                tr(
                    "{n} nahrávek je kratších než {s} s, pro úlohu {task} je to málo "
                    "(např. {name}). Spočítají se, ale ve výsledcích budou označené jako "
                    "podezřelé.\n\nSpustit přesto?"
                ).format(
                    n=len(short),
                    s=f"{limit:g}",
                    task=tasks.label(self.task_of(short[0].path)),
                    name=short[0].name,
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if len(runs) == 1:
            self.run_requested.emit(proto, list(runs[0][2]))
        else:
            self.runs_requested.emit("extract", [(p, list(paths)) for _t, p, paths in runs], {})

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
        runs = self.planned_runs()
        if not runs:
            return
        if len(runs) == 1:
            _task, proto, paths = runs[0]
            self.prepare_requested.emit(kind, proto, list(paths), self.prepare_options())
            return
        self.runs_requested.emit(
            kind, [(p, list(paths)) for _t, p, paths in runs], self.prepare_options()
        )
