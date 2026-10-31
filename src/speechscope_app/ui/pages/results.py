"""Stránka Výsledky: historie běhů vlevo, tabulka vybraného běhu vpravo.

Historie se čte ze složek `<datum>_<protokol>` v Dokumentech
(`backend/history.py`). Vybraný běh ukáže `features.csv`; běhy bez
tabulky (jen segmentace, chyba před první nahrávkou) ukážou aspoň stav
a odkaz na složku.

Tabulka má zmrazený první sloupec (název nahrávky), hledání sloupce,
popis sloupce v tooltipu hlavičky (z `list --json`), detail jedné
nahrávky po dvojkliku a „Spočítat znovu chybné“ pro řádky s chybou.

Vyšetření z Hodnocení z nahrávek (`RunGroup`) je jedna karta: nad tabulkou
je řada úloh (`task_bar`), která přepíná mezi běhy vyšetření, v hlavičce
odkaz Otevřít v Hodnocení (`open_assessment_requested`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend import quality, tasks
from ...backend.history import RunGroup, RunInfo, read_run
from ...backend.library import FeatureInfo, Library, LibraryError
from ...backend.results import failed_paths
from ...backend.work import CUT_DIR, PREPARE_OUTPUTS
from ...i18n import tr
from ..file_actions import file_menu
from ..recording_detail import RecordingDetailDialog, column_index
from ..widgets.frozen_table import FrozenTableView
from ..widgets.run_history import STATUS_ROLES, RunHistory
from ..widgets.status_header import StatusHeader
from ..widgets.values_table import (
    STATUS,
    TABLE_STYLE,
    BandHeader,
    ColumnCard,
    ErrorSpanDelegate,
    FooterBar,
    StatusDelegate,
    ValuesModel,
    column_stats,
    decimals_for,
    format_mean_sd,
    group_short,
    problem_mask,
)

PROBLEM_COLUMNS = ("notes", "error")
NAME_MAX = 360  # nejširší sloupec s názvem nahrávky v px


FrameModel = ValuesModel  # dřívější jméno modelu tabulky


class ResultsPage(QWidget):
    rerun_requested = Signal(object, list)  # složka běhu, cesty nahrávek s chybou
    notice = Signal(str)  # krátká hláška do stavového řádku hlavního okna
    assess_requested = Signal(object, int)  # složka běhu, řádek ve features.csv → Hodnocení
    open_assessment_requested = Signal(str)  # id hodnocení: Otevřít v Hodnocení
    task_shown = Signal(object)  # RunInfo: zvolená úloha vyšetření (Průběh ji ukáže taky)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: Path | None = None
        self._frame: pd.DataFrame | None = None
        self._note = ""
        self._library: Library | None = None
        self._catalog: list[FeatureInfo] | None = None
        self._group: RunGroup | None = None  # vyšetření, jehož běh je otevřený
        self.prefer_running = False  # vyšetření: výchozí úloha ta běžící (vložená stránka)
        self._feature_group = ""  # filtr skupiny sloupců (FeatureInfo.group), prázdno = vše
        self._card_key = ""  # sloupec s otevřenou kartou

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        self.page_title = QLabel(tr("Výsledky"))
        self.page_title.setObjectName("page_title")
        layout.addWidget(self.page_title)
        self._outer = layout

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
        right_layout.setSpacing(8)
        # stavová karta: název běhu, štítky s počty a tlačítka v jednom řádku, souhrn pod
        # nimi na jeden řádek (jako hlavička pacienta; dřív tři řádky nad tabulkou)
        self.header = StatusHeader(subtitle_below=True, pills_inline=True)
        self.title_label = self.header.title
        self.title_label.setText(tr("Výsledky"))
        self.summary = self.header.subtitle
        self.summary.setText(tr("Zatím žádný výstup."))
        self.summary.setToolTip("")
        self.open_btn = QPushButton(tr("Otevřít složku"))
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_folder)
        self.header.add_button(self.open_btn)
        # Jen segmentace / přepis: živé mezivýsledky ve work\<úloha>, tam se opravují.
        self.work_btn = QPushButton(tr("Otevřít mezivýsledky"))
        self.work_btn.setToolTip(
            tr(
                "Složka work s živými přepisy a segmentací, kterou bere výpočet feature. "
                "Opravy přepisů se dělají tam; ve složce běhu je jen kopie."
            )
        )
        self.work_btn.clicked.connect(self._open_work)
        self.work_btn.hide()
        self.header.add_button(self.work_btn)
        self._work_dir: Path | None = None
        self.assess_btn = QPushButton(tr("Otevřít v Hodnocení"))
        self.assess_btn.setToolTip(tr("Hodnocení pacienta, ke kterému tyto běhy patří."))
        self.assess_btn.clicked.connect(self._open_assessment)
        self.assess_btn.hide()
        self.header.add_button(self.assess_btn)
        right_layout.addWidget(self.header)

        # řada úloh vyšetření: jeden přepínač na běh, vpravo protokol a datum vybraného
        self.task_bar = QWidget()
        task_layout = QHBoxLayout(self.task_bar)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.setSpacing(6)
        self.task_group = QButtonGroup(self)
        self.task_group.setExclusive(True)
        self.task_buttons: list[QPushButton] = []
        self.task_layout = task_layout
        self.task_note = QLabel("")
        self.task_note.setObjectName("muted")
        task_layout.addStretch(1)
        task_layout.addWidget(self.task_note)
        self.task_bar.hide()
        right_layout.addWidget(self.task_bar)

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat sloupec"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(260)
        self.search.textChanged.connect(self._refresh)
        self.problems_only = QCheckBox(tr("jen problémové"))
        self.problems_only.setToolTip(tr("Jen řádky s poznámkou, chybou nebo podezřelou kvalitou."))
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
        # skupiny sloupců (vše + skupiny feature v tabulce), naplní `_fill_group_bar`
        self.group_bar = QWidget()
        self.group_layout = QHBoxLayout(self.group_bar)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        self.group_layout.setSpacing(4)
        self.group_buttons: dict[str, QPushButton] = {}
        self.group_choice = QButtonGroup(self)
        self.group_choice.setExclusive(True)
        tools.addWidget(self.group_bar)
        tools.addWidget(self.problems_only)
        tools.addStretch(1)
        right_layout.addLayout(tools)
        # akce k běhu patří do stavové karty vedle Otevřít složku, lišta je pak jen filtr
        self.header.add_button(self.rerun_btn)
        self.header.add_button(self.detail_btn)

        self.table_card = QFrame()
        self.table_card.setObjectName("card")
        card_layout = QVBoxLayout(self.table_card)
        card_layout.setContentsMargins(8, 6, 8, 6)
        # Podoba „A + karta sloupce“ (mockup https://claude.ai/artifact/3zTRx5diHQquy8ZjSMU6WW):
        # záhlaví ve dvou patrech, nahrávka · stav · pacient vlevo, průměr ± SD dole,
        # klik na záhlaví číselného sloupce = karta se statistikou nad tabulkou.
        self.table = FrozenTableView()
        self.table.setHorizontalHeader(BandHeader(self.table))
        self.table.frozen.setHorizontalHeader(BandHeader(self.table.frozen))
        self.table.frozen.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.table.horizontalHeader().text_left = self.table.frozen_width
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.frozen.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.frozen.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.frozen.verticalHeader().setDefaultSectionSize(34)
        self._status_delegate = StatusDelegate(self.table)
        self.table.setItemDelegate(ErrorSpanDelegate(self.table, self.table.frozen_width))
        for view in (self.table, self.table.frozen):
            view.setStyleSheet(TABLE_STYLE)
        self.table.doubleClicked.connect(lambda index: self.show_detail(index.row()))
        for view in (self.table, self.table.frozen):
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda pos, view=view: self._row_menu(view, pos)
            )
        card_layout.addWidget(self.table)
        self.footer = FooterBar(self.table, tr("průměr ± SD"))
        self.footer.setToolTip(tr("Z nahrávek bez chyby v aktuálním filtru."))
        self.footer.hide()
        card_layout.addWidget(self.footer)
        self._empty_text = tr("Vyber běh v historii vlevo.")
        self.empty = QLabel(self._empty_text)
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.empty)
        self.table.hide()
        self.column_card = ColumnCard()
        self.column_card.closed.connect(self.close_card)
        self.column_card.step_requested.connect(self.step_card)
        self.column_card.hide()
        # statistika sloupce vpravo vedle tabulky (podoba B): tabulka nepřijde o řádky
        table_row = QHBoxLayout()
        table_row.setSpacing(8)
        table_row.addWidget(self.table_card, 1)
        table_row.addWidget(self.column_card)
        right_layout.addLayout(table_row, 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([360, 760])

    def set_embedded(self) -> None:
        """Jen pravý panel jako záložka Hodnoty v detailu běhu na Výsledcích (31. 10. 2026);
        historie zůstává skrytá a slouží přehledu jako zdroj seznamu a výběru."""
        self.page_title.hide()
        self.history.hide()
        self._outer.setContentsMargins(0, 0, 0, 0)
        self.prefer_running = True  # u vyšetření začít úlohou, která běží

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
        """Znovu načte seznam běhů; výběr zůstane na složce otevřené tabulky.

        Když otevřený běh patří do vyšetření, které stránka ještě neukazuje jako
        skupinu (běh se načetl přímo po dávce), doplní se řada úloh."""
        current_dir = self._path.parent if self._path else None
        self.history.refresh(select=current_dir)
        entry = self.history.current()
        if isinstance(entry, RunGroup) and current_dir is not None and entry.contains(current_dir):
            if self._group is None or self._group.key != entry.key:
                self._show_group(entry, prefer=current_dir)
            else:
                self._group = entry
                self._fill_task_bar(entry, current_dir)
        elif isinstance(entry, RunInfo) and entry.dir == current_dir and self._group is not None:
            self._leave_group()

    def open_run(self, run_dir: Path) -> bool:
        """Z Hodnocení: vybere kartu běhu (u vyšetření jeho úlohu) a ukáže tabulku."""
        if not self.history.select_dir(run_dir):
            return False
        entry = self.history.current()
        if isinstance(entry, RunGroup):
            self._show_group(entry, prefer=run_dir)
        return True

    def show_loading(self, title: str) -> None:
        """Okamžitá odezva po kliknutí v přehledu: název běhu a „Načítám…“ místo tabulky.

        Tabulka se načte až v dalším kroku smyčky událostí (`RunsPage.open_row`), na
        pomalém počítači tak klik hned přepne stránku a okno nevisí na staré.
        """
        self.title_label.setText(title)
        self.header.set_role("neutral")
        self.header.set_pills([])
        self.summary.setText(tr("Načítám…"))
        self.summary.setToolTip("")
        self.open_btn.setEnabled(False)
        self.detail_btn.setEnabled(False)
        self.rerun_btn.hide()
        self.close_card()
        self.empty.setText(tr("Načítám…"))
        self._show_table(False)

    def show_run(self, entry: RunInfo | RunGroup) -> None:
        self.empty.setText(self._empty_text)
        if isinstance(entry, RunGroup):
            self._show_group(entry)
            return
        if self._group is not None:
            self._leave_group()
        self._show_single(entry)

    # --- vyšetření (skupina běhů) -------------------------------------------------------

    def _show_group(self, group: RunGroup, prefer: Path | None = None) -> None:
        self._group = group
        run = group.run_for(prefer) if prefer is not None else None
        if run is None and self.prefer_running:
            run = next((r for r in group.runs if r.status == "running"), None)
        if run is None:
            run = next((r for r in group.runs if r.status == "ok"), group.runs[0])
        self._show_single(run)
        self._fill_task_bar(group, run.dir)
        self.assess_btn.show()
        self.task_bar.show()
        self.task_shown.emit(run)

    def _leave_group(self) -> None:
        self._group = None
        self.assess_btn.hide()
        self.task_bar.hide()

    def _fill_task_bar(self, group: RunGroup, current: Path) -> None:
        for btn in self.task_buttons:
            self.task_group.removeButton(btn)
            self.task_layout.removeWidget(btn)
            btn.hide()
            btn.setParent(None)
            btn.deleteLater()
        self.task_buttons = []
        for i, run in enumerate(group.runs):
            count = run.rows if run.rows is not None else run.total
            label = tasks.label(run.task) or run.protocol_name
            btn = QPushButton(f"{label} ({count})" if count else label)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.setCheckable(True)
            btn.setChecked(run.dir == current)
            btn.setToolTip(f"{run.protocol_name} · {run.status_label}")
            btn.clicked.connect(lambda _=False, r=run: self._show_task(r))
            self.task_group.addButton(btn)
            self.task_layout.insertWidget(i, btn)
            self.task_buttons.append(btn)
        for task in group.waiting_tasks():
            # úloha plánu, která ještě nemá běh: jen informace, nejde na ni kliknout
            btn = QPushButton(tr("{task} · čeká").format(task=tasks.label(task)))
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.setEnabled(False)
            self.task_layout.insertWidget(len(self.task_buttons), btn)
            self.task_buttons.append(btn)
        self._set_task_note(group.run_for(current))

    def _set_task_note(self, run: RunInfo | None) -> None:
        if run is None:
            self.task_note.setText("")
            return
        parts = [run.protocol_name, run.started.strftime("%d.%m. %H:%M") if run.started else ""]
        if run.status != "ok":
            parts.append(run.status_label)
        self.task_note.setText(" · ".join(p for p in parts if p))

    def _show_task(self, run: RunInfo) -> None:
        if self._group is None:
            return
        self._show_single(run)
        self._set_task_note(run)
        self.task_shown.emit(run)

    def _group_title(self, group: RunGroup) -> str:
        when = group.started.strftime("%d. %m. %Y") if group.started else ""
        return tr("Hodnocení {subject}, {date}").format(subject=group.display_name, date=when)

    def _title_for(self, info: RunInfo | None, fallback: str) -> str:
        if self._group is not None and info is not None and self._group.contains(info.dir):
            return self._group_title(self._group)
        return info.display_name if info else fallback

    def _open_assessment(self) -> None:
        if self._group is not None:
            self.open_assessment_requested.emit(self._group.key)

    # --- jeden běh ------------------------------------------------------------------------

    def _show_single(self, info: RunInfo) -> None:
        self.title_label.setText(self._title_for(info, info.display_name))
        self.header.set_role(STATUS_ROLES.get(info.status, "neutral"))
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
        self._set_work(info)
        self._frame = None
        self.table.setModel(None)
        self._show_table(False)
        self.header.set_pills([])
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
            self.summary.setText(self._prepare_summary(info))
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
            self._show_table(False)
            self.header.set_role("missing")
            self.detail_btn.setEnabled(False)
            self.rerun_btn.hide()
            return
        info = read_run(path.parent)
        self._set_work(info)
        self.title_label.setText(self._title_for(info, path.parent.name))
        self.header.set_role(STATUS_ROLES.get(info.status, "neutral") if info else "neutral")
        self.open_btn.setEnabled(True)
        self._refresh()
        if refresh_history:
            self.refresh()

    def current_dir(self) -> Path | None:
        return self._path.parent if self._path is not None else None

    def relocate(self, old: Path, new: Path) -> None:
        """Složka otevřeného běhu byla přejmenována; tabulka zůstává, mění se jen cesta."""
        if self._path is not None and self._path.parent == old:
            self._path = new / self._path.name

    def clear(self) -> None:
        """Po smazání běhu, který byl otevřený: bez tabulky, výběr na nejnovějším."""
        self._leave_group()
        self._path = None
        self._frame = None
        self._note = ""
        self.table.setModel(None)
        self._show_table(False)
        self.header.set_pills([])
        self.header.set_role("neutral")
        self.title_label.setText(tr("Výsledky"))
        self.detail_btn.setEnabled(False)
        self.rerun_btn.hide()
        self.open_btn.setEnabled(False)
        self.summary.setToolTip("")
        self.summary.setText(tr("Zatím žádný výstup."))
        self.history.refresh()
        if self.history.count():
            self.history.select_row(0)

    def visible_frame(self) -> pd.DataFrame | None:
        model = self.table.model()
        return model.frame if isinstance(model, FrameModel) else None

    def _refresh(self) -> None:
        if self._frame is None:
            return
        frame = self._frame
        n_notes = int(frame["notes"].notna().sum()) if "notes" in frame.columns else 0
        n_err = int(frame["error"].notna().sum()) if "error" in frame.columns else 0
        issues = self._quality_issues(frame)
        errors = problem_mask(frame, "error")
        levels = pd.Series(
            [
                quality.level_of([i for i in row if i.check.key != quality.NOTES_KEY])
                for row in issues
            ],
            index=frame.index,
        )
        n_bad = int(((levels == quality.BAD) & ~errors).sum())
        n_warn = int(((levels == quality.WARN) & ~errors).sum())
        if self.problems_only.isChecked():
            mask = levels != ""
            for col in PROBLEM_COLUMNS:
                if col in frame.columns:
                    mask |= frame[col].notna()
            frame = frame[mask]
        index = column_index(self.catalog())
        self._fill_group_bar(index)
        needle = self.search.text().strip().lower()
        keep = [
            c
            for c in frame.columns
            if c in ("file", "subject")
            or c in PROBLEM_COLUMNS
            or (
                (not needle or needle in str(c).lower())
                and (
                    not self._feature_group
                    or (c in index and index[c].feature.group == self._feature_group)
                    or (c not in index and not needle)
                )
            )
        ]
        if needle or self._feature_group:
            frame = frame[keep]
        by_index = dict(zip(self._frame.index, issues, strict=True))
        frame_quality = [by_index[i] for i in frame.index]
        frame = frame.reset_index(drop=True)
        model = ValuesModel(
            frame, self.catalog(), self._footer_texts(frame, index), quality=frame_quality
        )
        self.table.set_frozen_count(model.frozen)
        self.table.setModel(model)
        for view in (self.table, self.table.frozen):
            for c in range(2):
                view.setItemDelegateForColumn(c, None)
            if model.position(STATUS) >= 0:
                view.setItemDelegateForColumn(model.position(STATUS), self._status_delegate)
        self._show_table(True)
        self.table.clearSpans()
        self.table.resizeColumnsToContents()
        if self.table.columnWidth(0) > NAME_MAX:
            self.table.setColumnWidth(0, NAME_MAX)
        span = model.feature_span()
        if span is not None and span[1] > 1:  # chybný řádek: text chyby přes sloupce feature
            for row in range(model.rowCount()):
                if model.is_error(row):
                    self.table.setSpan(row, span[0], 1, span[1])
        self.footer.frozen = model.frozen
        self.footer.update()
        if self._card_key and (model.column(self._card_key) or None) is not None:
            model.set_highlight(self._card_key)
            self._update_card()
        elif self._card_key:
            self.close_card()
        pills: list = []
        if n_bad:
            pills.append(
                (
                    tr("{n} nepoužitelných").format(n=n_bad),
                    "missing",
                    tr("Nahrávky pod červeným prahem kontroly kvality (délka, šum, přebuzení). ")
                    + tr("Klik přepne filtr jen na problémové řádky."),
                    self.problems_only.toggle,
                )
            )
        if n_warn:
            pills.append(
                (
                    tr("{n} podezřelých").format(n=n_warn),
                    "warn",
                    tr("Nahrávky pod žlutým prahem kontroly kvality (délka, šum, přebuzení). ")
                    + tr("Klik přepne filtr jen na problémové řádky."),
                    self.problems_only.toggle,
                )
            )
        if n_notes:
            pills.append(
                (
                    tr("{n} s poznámkou").format(n=n_notes),
                    "warn",
                    tr("Klik přepne filtr jen na řádky s poznámkou nebo chybou."),
                    self.problems_only.toggle,
                )
            )
        if n_err:
            pills.append(
                (
                    tr("{n} s chybou").format(n=n_err),
                    "missing",
                    tr("Klik přepne filtr jen na řádky s poznámkou nebo chybou."),
                    self.problems_only.toggle,
                )
            )
        self.header.set_pills(pills)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.detail_btn.setEnabled(False)
        can_rerun = bool(failed_paths(self._frame)) and (
            self._path is not None and (self._path.parent / "protocol.yaml").is_file()
        )
        self.rerun_btn.setVisible(can_rerun)
        self.summary.setToolTip(str(self._path))
        note = f"{self._note} " if self._note else ""
        info = read_run(self._path.parent) if self._path else None
        protocol = f"{info.protocol_name} · " if info is not None and info.label else ""
        self.summary.setText(
            note
            + protocol
            + tr("{rows} nahrávek · {cols} sloupců · {folder}").format(
                rows=len(self._frame),
                cols=len(self._frame.columns),
                folder=self._path.parent.name if self._path else "",
            )
        )

    @staticmethod
    def _quality_issues(frame: pd.DataFrame) -> list[list[quality.Issue]]:
        """Kontrola kvality (`backend/quality.py`) pro každý řádek tabulky."""
        if "task" not in frame.columns:
            return [[] for _ in range(len(frame))]
        return [quality.assess(row, str(row.get("task") or "")) for row in frame.to_dict("records")]

    def _show_table(self, shown: bool) -> None:
        self.table.setVisible(shown)
        self.footer.setVisible(shown)
        self.empty.setVisible(not shown)
        if not shown:
            self.close_card()

    def _selection_changed(self) -> None:
        self.detail_btn.setEnabled(bool(self.table.selectionModel().selectedRows()))
        if self._card_key:
            self._update_card()

    # --- skupiny sloupců, souhrn a karta sloupce ------------------------------------------

    def _fill_group_bar(self, index: dict) -> None:
        """Přepínač skupin sloupců: vše + skupiny feature, které tabulka má (beze změny
        se tlačítka nepřestavují, ať nepoblikávají)."""
        frame = self._frame
        groups: list[str] = []
        for column in frame.columns if frame is not None else []:
            info = index.get(column)
            if info is not None and info.feature.group not in groups:
                groups.append(info.feature.group)
        if self._feature_group not in groups:
            self._feature_group = ""
        wanted = [""] + groups
        if list(self.group_buttons) != wanted:
            for btn in self.group_buttons.values():
                self.group_choice.removeButton(btn)
                self.group_layout.removeWidget(btn)
                btn.hide()
                btn.setParent(None)
                btn.deleteLater()
            self.group_buttons = {}
            for group in wanted:
                btn = QPushButton(group_short(group) if group else tr("vše"))
                btn.setCheckable(True)
                btn.setToolTip(contract.GROUP_LABELS.get(group, group) if group else "")
                btn.clicked.connect(lambda _c=False, g=group: self.set_feature_group(g))
                self.group_choice.addButton(btn)
                self.group_layout.addWidget(btn)
                self.group_buttons[group] = btn
        for group, btn in self.group_buttons.items():
            btn.setChecked(group == self._feature_group)
        self.group_bar.setVisible(len(groups) > 1)

    def set_feature_group(self, group: str) -> None:
        """Jen sloupce jedné skupiny feature (prázdno = všechny)."""
        self._feature_group = group
        self._refresh()

    def feature_group(self) -> str:
        return self._feature_group

    @staticmethod
    def _footer_texts(frame, index: dict) -> dict[str, str]:  # noqa: ANN001
        """Průměr ± SD číselných sloupců z řádků bez chyby (souhrnný řádek pod tabulkou)."""
        ok = frame[~problem_mask(frame, "error")]
        out: dict[str, str] = {}
        for column in frame.columns:
            if column in PROBLEM_COLUMNS or column in ("file", "path", "task"):
                continue
            if not pd.api.types.is_numeric_dtype(frame[column]):
                continue
            stats = column_stats(ok[column])
            if stats is None:
                continue
            d = decimals_for(frame[column])
            out[column] = format_mean_sd(stats, d, " ±")
        return out

    def _header_clicked(self, section: int) -> None:
        model = self.table.model()
        if not isinstance(model, ValuesModel):
            return
        column = model.column(model.key(section))
        if column is None or not column.numeric:
            return
        if self._card_key == column.key and not self.column_card.isHidden():
            self.close_card()
        else:
            self.open_card(column.key)

    def open_card(self, key: str) -> bool:
        """Karta sloupce `key` nad tabulkou (jen číselný sloupec v tabulce)."""
        model = self.table.model()
        column = model.column(key) if isinstance(model, ValuesModel) else None
        if column is None or not column.numeric:
            return False
        self._card_key = key
        model.set_highlight(key)
        self._update_card()
        self.column_card.show()
        self.table.scrollTo(model.index(0, model.position(key)))
        return True

    def close_card(self) -> None:
        self._card_key = ""
        self.column_card.hide()
        model = self.table.model()
        if isinstance(model, ValuesModel):
            model.set_highlight("")

    def card_key(self) -> str:
        return self._card_key if not self.column_card.isHidden() else ""

    def step_card(self, delta: int) -> None:
        """Karta na sousední číselný sloupec v pořadí tabulky."""
        model = self.table.model()
        if not isinstance(model, ValuesModel):
            return
        keys = [c.key for c in model.columns[model.frozen :] if c.numeric]
        if not keys:
            return
        i = keys.index(self._card_key) if self._card_key in keys else -1
        self.open_card(keys[max(0, min(len(keys) - 1, i + delta))])

    def _update_card(self) -> None:
        model = self.table.model()
        column = model.column(self._card_key) if isinstance(model, ValuesModel) else None
        if column is None:
            return
        frame = model.frame
        errors = problem_mask(frame, "error")
        values = frame.loc[~errors, column.key]
        selected = None
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if rows and not errors.iloc[rows[0].row()]:
            value = pd.to_numeric(frame[column.key].iloc[rows[0].row()], errors="coerce")
            selected = None if pd.isna(value) else float(value)
        self.column_card.show_column(column, values, selected, int(errors.sum()))
        position = model.position(column.key)
        numeric = [i for i, c in enumerate(model.columns) if c.numeric and i >= model.frozen]
        self.column_card.prev_btn.setEnabled(bool(numeric) and position > numeric[0])
        self.column_card.next_btn.setEnabled(bool(numeric) and position < numeric[-1])

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
        assess = menu.addAction(tr("Otevřít v Hodnocení"))
        assess.triggered.connect(lambda: self.assess_row(index.row()))
        menu.exec(view.viewport().mapToGlobal(pos))

    def assess_row(self, row: int) -> None:
        """Řádek tabulky → vyšetření na stránce Hodnocení (řádek podle features.csv)."""
        data = self.detail_row(row)
        if data is None or self._path is None or self._frame is None:
            return
        frame_row = row
        if "file" in self._frame.columns:
            hits = self._frame.index[self._frame["file"] == data.get("file")]
            if len(hits) == 1:
                frame_row = int(hits[0])
        self.assess_requested.emit(self._path.parent, frame_row)

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

    def _set_work(self, info: RunInfo | None) -> None:
        """Tlačítko Otevřít mezivýsledky jen u běhu jen segmentace / přepis."""
        self._work_dir = None
        if info is not None and info.prepare and info.work_dir is not None:
            sub = PREPARE_OUTPUTS.get(info.prepare, (info.prepare, ()))[0]
            self._work_dir = info.work_dir / sub
        self.work_btn.setVisible(self._work_dir is not None)

    def _open_work(self) -> None:
        if self._work_dir is not None:
            target = self._work_dir if self._work_dir.is_dir() else self._work_dir.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @staticmethod
    def _prepare_summary(info: RunInfo) -> str:
        """Co po běhu jen segmentace / přepis leží ve složce běhu a kde jsou živé soubory."""
        what = {
            "segments": tr("segmentace"),
            "transcript": tr("přepisy"),
        }.get(info.prepare, tr("mezivýsledky"))
        sub = PREPARE_OUTPUTS.get(info.prepare, (info.prepare, ()))[0]
        copies = sorted(p.name for p in (info.dir / sub).glob("*.txt")) if sub else []
        cut = sorted(p.name for p in (info.dir / CUT_DIR).glob("*.wav"))
        parts = [
            tr("{name}: jen {what}, tabulka feature se nepočítala.").format(
                name=info.protocol_name, what=what
            )
        ]
        if copies:
            shown = ", ".join(copies[:8]) + (", …" if len(copies) > 8 else "")
            parts.append(
                tr("Kopie ve složce běhu ({n}): {files}.").format(n=len(copies), files=shown)
            )
        if cut:
            parts.append(tr("Vyříznutý zvuk ({n}) ve složce cut.").format(n=len(cut)))
        if info.work_dir is not None:
            parts.append(
                tr("Výpočet feature bere živé soubory z {path}; opravy přepisů tam.").format(
                    path=info.work_dir / sub if sub else info.work_dir
                )
            )
        return " ".join(parts)
