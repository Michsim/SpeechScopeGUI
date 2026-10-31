"""Stránka Výsledky = dřívější Výsledky + Výpočet (31. 10. 2026).

Mockup https://claude.ai/artifact/9oecmvHjjNY99LeW3vvxZb (`docs/mockups/vysledky-vypocet/`
mimo git). Dvě obrazovky v `QStackedWidget`:

- **přehled**: nahoře „Běží a ve frontě“ (běžící dávka s průběhem, Průběh, Zrušit…,
  fronta s Vyndat), pod tím přepínač Analýzy · Hodnocení · Normy, hledání a tabulka
  běhů přes celou šířku; dvojklik (Enter) na řádek otevře detail;
- **detail**: „‹ Výsledky“, šipky na sousední běh a záložky Hodnoty · Průběh. Hodnoty
  jsou dnešní `ResultsPage`, Průběh dnešní `RunPage`, obě vložené bez nadpisu
  a bez vlastní historie (`set_embedded`).

Zdroj seznamu je skrytá historie `ResultsPage.history` (filtr, seskupení vyšetření,
mazání a přejmenování zůstávají tam a hlavní okno je má napojené); přehled ji jen
opisuje po každém `rebuilt`. Hodnoty běžícího běhu jsou neaktivní: knihovna během
běhu přepisuje `features.csv` a otevřený soubor by jí přejmenování zablokoval.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...backend import tasks
from ...backend.history import KINDS, RunGroup, RunInfo
from ...i18n import tr
from .. import theme
from ..widgets.clean_table import card, cell, pill, style_table
from ..widgets.run_history import STATUS_ROLES, Entry, _format_seconds, entry_contains
from .results import ResultsPage
from .run import RunPage

VALUES, PROGRESS = "values", "progress"
NAME_MAX = 360  # nejširší sloupec s názvem běhu v px
COLUMNS = (
    tr("běh"),
    tr("úloha"),
    tr("protokol"),
    tr("nahrávky"),
    tr("stav"),
    tr("doba"),
    tr("spuštěno"),
    "",  # šipka ›
)
COL_TASK, COL_FILES, COL_STATUS, COL_WHEN, COL_CHEVRON = 1, 3, 4, 6, 7
TEXT_ROLE = Qt.ItemDataRole.UserRole + 1  # text buňky, kterou kreslí štítek
WEEKDAYS = (tr("po."), tr("út."), tr("st."), tr("čt."), tr("pá."), tr("so."), tr("ne."))
STATUS_COLORS = {
    "ok": theme.OK,
    "accent": theme.ACCENT,
    "warn": theme.WARN,
    "missing": theme.MISSING,
    "neutral": theme.MUTED,
}


def _entry_dir(entry: Entry) -> Path:
    return entry.dir if entry.dir is not None else entry.runs[0].dir


def _when(started: datetime | None, today: date | None = None) -> str:
    """Relativní datum do přehledu: „dnes 14:02“, „včera 9:40“, „čt 30. 10.“, „27. 10.“."""
    if started is None:
        return ""
    today = today or date.today()
    day = started.date()
    clock = f"{started.hour}:{started.minute:02d}"
    if day == today:
        return tr("dnes {time}").format(time=clock)
    if day == today - timedelta(days=1):
        return tr("včera {time}").format(time=clock)
    if 0 < (today - day).days < 7:
        return f"{WEEKDAYS[day.weekday()].rstrip('.')} {day.day}. {day.month}."
    if day.year != today.year:
        return f"{day.day}. {day.month}. {day.year}"
    return f"{day.day}. {day.month}."


def _cells(entry: Entry) -> list[str]:
    """Texty řádku přehledu pro běh nebo vyšetření pacienta."""
    when = _when(entry.started)
    seconds = _format_seconds(entry.seconds) if entry.seconds else ""
    if isinstance(entry, RunGroup):
        task_text = ", ".join(tasks.label(r.task) for r in entry.runs if r.task)
        files = entry.total or entry.processed
        return [
            entry.display_name,
            task_text,
            tr("hodnocení · {done} z {n} úloh").format(done=entry.done, n=entry.task_count),
            str(files) if files else "",
            entry.status_label,
            seconds,
            when,
            "›",
        ]
    files = ""
    if entry.total:
        files = (
            f"{entry.processed}/{entry.total}"
            if entry.processed is not None and entry.processed != entry.total
            else str(entry.total)
        )
    elif entry.rows is not None:
        files = str(entry.rows)
    protocol = entry.protocol_name
    if entry.norm:
        protocol = tr("{protocol} · kontroly normy").format(protocol=protocol)
    return [
        entry.display_name,
        tasks.label(entry.task) if entry.task else "",
        protocol,
        files,
        entry.status_label,
        seconds,
        when,
        "›",
    ]


class RunsPage(QWidget):
    """Přehled běhů a detail běhu se záložkami Hodnoty · Průběh."""

    detail_shown = Signal(str)  # VALUES | PROGRESS po otevření detailu (testy, hlavní okno)

    def __init__(
        self, run_page: RunPage, results_page: ResultsPage, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.run_page = run_page
        self.results_page = results_page
        self.history = results_page.history
        run_page.set_embedded()
        results_page.set_embedded()
        self._rows: list[Entry] = []  # řádky tabulky = položky historie v pořadí
        self._detail: Entry | None = None
        self._live_dir: Path | None = None
        self._task_run: RunInfo | None = None  # zvolená úloha vyšetření (řada úloh)
        self._tab = VALUES
        self._recorded_pending: RunInfo | None = None  # Průběh k přehrání při přepnutí
        self._open_generation = 0  # odložené otevření z přehledu; novější klik ruší starší

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(10)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        # --- přehled -----------------------------------------------------------------------
        overview = QWidget()
        ov = QVBoxLayout(overview)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(12)
        head = QHBoxLayout()
        title = QLabel(tr("Výsledky"))
        title.setObjectName("page_title")
        head.addWidget(title)
        self.counts = QLabel("")
        self.counts.setObjectName("muted")
        head.addWidget(self.counts)
        head.addStretch(1)
        self.refresh_btn = QPushButton(tr("Obnovit"))
        self.refresh_btn.clicked.connect(lambda: self.history.refresh())
        head.addWidget(self.refresh_btn)
        ov.addLayout(head)

        # běží a ve frontě
        self.running_card = QFrame()
        self.running_card.setObjectName("card")
        theme.set_role(self.running_card, "accent")
        rc = QVBoxLayout(self.running_card)
        rc.setContentsMargins(14, 10, 14, 10)
        rc.setSpacing(6)
        running_title = QLabel(tr("Běží a ve frontě"))
        running_title.setObjectName("section")
        rc.addWidget(running_title)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.run_title = QLabel("")
        font = self.run_title.font()
        font.setBold(True)
        self.run_title.setFont(font)
        row.addWidget(self.run_title)
        self.run_detail = QLabel("")
        self.run_detail.setObjectName("muted")
        row.addWidget(self.run_detail, 1)
        self.run_open_btn = QPushButton(tr("Průběh"))
        self.run_open_btn.clicked.connect(lambda: self.show_running())
        row.addWidget(self.run_open_btn)
        self.run_cancel_btn = QPushButton(tr("Zrušit…"))
        theme.set_role(self.run_cancel_btn, "danger")
        self.run_cancel_btn.clicked.connect(lambda: self.run_page.confirm_cancel())
        row.addWidget(self.run_cancel_btn)
        rc.addLayout(row)
        self.run_bar = QProgressBar()
        self.run_bar.setTextVisible(False)
        self.run_bar.setFixedHeight(8)
        rc.addWidget(self.run_bar)
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(84)
        rc.addWidget(self.queue_list)
        queue_row = QHBoxLayout()
        queue_row.addStretch(1)
        self.queue_remove_btn = QPushButton(tr("Vyndat z fronty"))
        self.queue_remove_btn.clicked.connect(self._remove_queued)
        self.queue_clear_btn = QPushButton(tr("Vyprázdnit frontu"))
        self.queue_clear_btn.clicked.connect(lambda: self.run_page.queue_clear.emit())
        queue_row.addWidget(self.queue_remove_btn)
        queue_row.addWidget(self.queue_clear_btn)
        rc.addLayout(queue_row)
        self._queue_widgets = (self.queue_list, self.queue_remove_btn, self.queue_clear_btn)
        for widget in self._queue_widgets:
            widget.hide()  # fronta jen, když něco čeká (`set_queue`)
        ov.addWidget(self.running_card)
        self.running_card.hide()

        # filtr, hledání, mazání
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.filter_group = QButtonGroup(self)
        self.filter_buttons: dict[str, QPushButton] = {}
        for kind in KINDS:
            btn = QPushButton(self.history.filter_buttons[kind].text())
            btn.setCheckable(True)
            btn.setToolTip(self.history.filter_buttons[kind].toolTip())
            btn.clicked.connect(lambda _c=False, k=kind: self.history.set_filter(k))
            self.filter_group.addButton(btn)
            self.filter_buttons[kind] = btn
            bar.addWidget(btn)
        bar.addSpacing(12)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat běh"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(280)
        self.search.textChanged.connect(lambda _t: self._apply_search())
        bar.addWidget(self.search)
        bar.addStretch(1)
        self.delete_btn = QPushButton(tr("Smazat…"))
        self.delete_btn.setToolTip(
            tr("Přesune složku vybraného běhu do Koše. Nahrávek se to netýká.")
        )
        self.delete_btn.clicked.connect(self._delete_selected)
        bar.addWidget(self.delete_btn)
        ov.addLayout(bar)

        # uhlazená tabulka (podoba A, mockup https://claude.ai/artifact/J8iCqC36qpFSDbyip27jb6):
        # bílá karta, záhlaví malými písmeny, štítky stavu a úlohy, průběh v řádku, šipka ›
        self.table = QTableWidget()
        style_table(self.table, list(COLUMNS), selectable=True)
        header = self.table.horizontalHeader()
        # sloupce podle obsahu, volné místo až za posledním (název běhu nesmí odsunout
        # ostatní sloupce doprava); název se při délce nad NAME_MAX zkrátí
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for col in range(1, len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_WHEN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_CHEVRON, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_CHEVRON, 32)
        header.setStretchLastSection(False)
        self.table.cellDoubleClicked.connect(lambda r, _c: self.open_row(r))
        self.table.itemActivated.connect(lambda item: self.open_row(item.row()))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.table, self._delete_selected)
        QShortcut(QKeySequence(Qt.Key.Key_F2), self.table, self._rename_selected)
        table_card = card(self.table)
        ov.addWidget(table_card, 1)
        self.table_card = table_card
        self.empty = QLabel(tr("Zatím žádný běh. Spusťte analýzu na stránce Analýza nahrávek."))
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov.addWidget(self.empty)
        self.empty.hide()
        hint = QLabel(
            tr(
                "Dvojklik na řádek (nebo Enter) otevře běh. Pravé tlačítko: Otevřít složku, "
                "Přejmenovat…, Smazat…."
            )
        )
        hint.setObjectName("muted")
        ov.addWidget(hint)
        self.stack.addWidget(overview)

        # --- detail ------------------------------------------------------------------------
        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(8)
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.back_btn = QPushButton(tr("‹ Výsledky"))
        self.back_btn.setFlat(True)
        self.back_btn.setToolTip(tr("Zpět na přehled běhů (Esc)"))
        self.back_btn.clicked.connect(lambda: self.show_overview())
        nav.addWidget(self.back_btn)
        self.crumb = QLabel("")
        crumb_font = self.crumb.font()
        crumb_font.setBold(True)
        self.crumb.setFont(crumb_font)
        nav.addWidget(self.crumb, 1)
        self.prev_btn = QPushButton(tr("‹ předchozí"))
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn = QPushButton(tr("další ›"))
        self.next_btn.clicked.connect(lambda: self.step(1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        dl.addLayout(nav)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), detail, lambda: self.show_overview())
        # řada úloh vyšetření (z ResultsPage) nad záložkami: platí pro Hodnoty i Průběh
        self.task_bar = results_page.task_bar
        old_layout = self.task_bar.parentWidget().layout() if self.task_bar.parentWidget() else None
        if old_layout is not None:
            old_layout.removeWidget(self.task_bar)
        dl.addWidget(self.task_bar)
        results_page.task_shown.connect(self._task_shown)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.values_tab = QPushButton(tr("Hodnoty"))
        self.progress_tab = QPushButton(tr("Průběh"))
        self.tab_group = QButtonGroup(self)
        for button, tab in ((self.values_tab, VALUES), (self.progress_tab, PROGRESS)):
            button.setCheckable(True)
            self.tab_group.addButton(button)
            button.clicked.connect(lambda _c=False, t=tab: self.set_tab(t))
            tabs.addWidget(button)
        self.tab_note = QLabel("")
        self.tab_note.setObjectName("muted")
        tabs.addSpacing(8)
        tabs.addWidget(self.tab_note, 1)
        dl.addLayout(tabs)
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(results_page)
        self.detail_stack.addWidget(run_page)
        dl.addWidget(self.detail_stack, 1)
        self.stack.addWidget(detail)

        self.history.rebuilt.connect(self.refresh_list)
        self.history.filter_changed.connect(lambda _k: self._sync_filter())
        self._sync_filter()
        self.refresh_list()

    # --- přehled -----------------------------------------------------------------------

    def in_detail(self) -> bool:
        return self.stack.currentIndex() == 1

    def tab(self) -> str:
        return self._tab

    def _sync_filter(self) -> None:
        for kind, btn in self.filter_buttons.items():
            btn.setText(self.history.filter_buttons[kind].text())
            btn.setChecked(kind == self.history.filter())
            theme.set_role(btn, "primary" if kind == self.history.filter() else "")

    def refresh_list(self) -> None:
        """Tabulka z položek skryté historie; detail zmizelého běhu vrátí přehled."""
        self._sync_filter()
        self._rows = list(self.history.entries())
        self.table.setRowCount(len(self._rows))
        for r, entry in enumerate(self._rows):
            role = STATUS_ROLES.get(entry.status, "neutral")
            for c, text in enumerate(_cells(entry)):
                item = QTableWidgetItem(text)
                item.setData(TEXT_ROLE, text)  # text buňky i pod štítkem (hledání, testy)
                self.table.removeCellWidget(r, c)
                if c == 0:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setToolTip(str(_entry_dir(entry)))
                elif c == COL_TASK and text:
                    item.setText("")  # pod štítkem nic viditelného, ani ve vybraném řádku
                    self.table.setCellWidget(
                        r, c, cell(*(pill(t, "neutral") for t in text.split(", ")))
                    )
                elif c == COL_STATUS:
                    item.setText("")  # pod štítkem nic viditelného, ani ve vybraném řádku
                    self.table.setCellWidget(r, c, cell(pill(text, role)))
                elif c == COL_FILES and entry.status == "running" and entry.total:
                    item.setText("")  # pod štítkem nic viditelného, ani ve vybraném řádku
                    bar = QProgressBar()
                    bar.setTextVisible(False)
                    bar.setFixedSize(56, 6)
                    bar.setRange(0, max(1, entry.total))
                    bar.setValue(entry.processed or 0)
                    count = QLabel(text)
                    self.table.setCellWidget(r, c, cell(bar, count))
                elif c in (2, 5, COL_WHEN, COL_CHEVRON):
                    item.setForeground(QColor(theme.MUTED))
                self.table.setItem(r, c, item)
        self.empty.setVisible(not self._rows)
        self.table_card.setVisible(bool(self._rows))
        self.table.resizeColumnToContents(0)
        self.table.setColumnWidth(0, min(NAME_MAX, self.table.columnWidth(0) + 16))
        # sloupce se štítky: šířka podle štítku, ne podle schovaného textu pod ním
        header = self.table.horizontalHeader()
        for c in (COL_TASK, COL_FILES, COL_STATUS):
            widths = [header.sectionSizeFromContents(c).width(), self.table.sizeHintForColumn(c)]
            for r in range(self.table.rowCount()):
                widget = self.table.cellWidget(r, c)
                if widget is not None:
                    widths.append(widget.sizeHint().width())
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, max(widths) + 8)
        total = len(self.history.all_runs())
        self.counts.setText(tr("{n} běhů").format(n=total) if total else "")
        self._apply_search()
        if self._detail is not None:
            fresh = self._find(_entry_dir(self._detail))
            if fresh is not None:
                self._detail = fresh
                self._update_detail_chrome()
            elif not _entry_dir(self._detail).exists():
                self._detail = None
                self.show_overview()

    def _apply_search(self) -> None:
        needle = self.search.text().strip().lower()
        for r in range(self.table.rowCount()):
            text = " ".join(self.cell_text(r, c) for c in range(3)).lower()
            self.table.setRowHidden(r, bool(needle) and needle not in text)

    def cell_text(self, row: int, column: int) -> str:
        """Text buňky přehledu, i když ji kreslí štítek (uložený v `TEXT_ROLE`)."""
        item = self.table.item(row, column)
        return str(item.data(TEXT_ROLE) or "") if item is not None else ""

    def visible_rows(self) -> list[int]:
        return [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]

    def _find(self, run_dir: Path) -> Entry | None:
        return next((e for e in self._rows if entry_contains(e, run_dir)), None)

    def _row_of(self, entry: Entry | None) -> int:
        if entry is None:
            return -1
        target = _entry_dir(entry)
        return next((r for r, e in enumerate(self._rows) if entry_contains(e, target)), -1)

    def _selected_entry(self) -> Entry | None:
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _delete_selected(self) -> None:
        entry = self._selected_entry()
        if entry is not None and self.history.can_delete(entry):
            self.history.delete_requested.emit(entry)

    def _rename_selected(self) -> None:
        entry = self._selected_entry()
        if self.history.can_rename(entry):
            self.history.rename_requested.emit(entry)

    def _menu(self, pos) -> None:  # noqa: ANN001
        row = self.table.rowAt(pos.y())
        if not 0 <= row < len(self._rows):
            return
        self.table.selectRow(row)
        entry = self._rows[row]
        menu = QMenu(self)
        menu.addAction(tr("Otevřít")).triggered.connect(lambda: self.open_row(row))
        folder = menu.addAction(tr("Otevřít složku"))
        folder.triggered.connect(lambda: self._open_folder(entry))
        if self.history.can_rename(entry):
            menu.addAction(tr("Přejmenovat…")).triggered.connect(
                lambda: self.history.rename_requested.emit(entry)
            )
        delete = menu.addAction(
            tr("Smazat vyšetření…") if isinstance(entry, RunGroup) else tr("Smazat běh…")
        )
        delete.setEnabled(self.history.can_delete(entry))
        delete.triggered.connect(lambda: self.history.delete_requested.emit(entry))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    @staticmethod
    def _open_folder(entry: Entry) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(_entry_dir(entry))))

    # --- běží a ve frontě --------------------------------------------------------------

    def set_running(self, title: str, detail: str, run_dir: Path | None) -> None:
        self._live_dir = run_dir
        self.run_title.setText(title)
        self.run_detail.setText(detail)
        self.run_cancel_btn.setEnabled(True)
        self.run_open_btn.setEnabled(True)
        self.running_card.show()
        self._update_detail_chrome()

    def set_progress(self, done: int, total: int) -> None:
        self.run_bar.setRange(0, max(1, total))
        self.run_bar.setValue(done)

    def clear_running(self) -> None:
        self._live_dir = None
        self.run_title.setText("")
        self.run_detail.setText(tr("nic neběží"))
        self.run_bar.setValue(0)
        self.run_cancel_btn.setEnabled(False)
        self.run_open_btn.setEnabled(False)
        self.running_card.setVisible(self.queue_list.count() > 0)
        self._update_detail_chrome()

    def set_queue(self, titles: list[str]) -> None:
        self.queue_list.clear()
        for i, text in enumerate(titles, start=1):
            self.queue_list.addItem(tr("{i}. {title} · čeká").format(i=i, title=text))
        for widget in self._queue_widgets:
            widget.setVisible(bool(titles))
        # výška podle počtu čekajících (nejvýš tři řádky), prázdné místo nepřidává
        self.queue_list.setFixedHeight(min(3, max(1, len(titles))) * 26 + 6)
        self.running_card.setVisible(bool(titles) or self._live_dir is not None)

    def _remove_queued(self) -> None:
        row = self.queue_list.currentRow()
        if row < 0 and self.queue_list.count():
            row = 0
        if row >= 0:
            self.run_page.queue_remove.emit(row)

    def live_dir(self) -> Path | None:
        return self._live_dir if self.run_page.runner.running else None

    # --- detail ------------------------------------------------------------------------

    def _is_live(self, entry: Entry | None) -> bool:
        live = self.live_dir()
        if entry is None or live is None:
            return False
        if isinstance(entry, RunGroup) and self._task_run is not None:
            return self._task_run.dir == live  # u vyšetření rozhoduje zvolená úloha
        return entry_contains(entry, live)

    def task_run(self) -> RunInfo | None:
        return self._task_run

    def _task_shown(self, run: RunInfo) -> None:
        """Klik na úlohu vyšetření: Hodnoty i Průběh ukazují tutéž úlohu."""
        if not isinstance(self._detail, RunGroup) or not self._detail.contains(run.dir):
            return
        self._task_run = run
        self._defer_recorded(run)
        if self._tab == VALUES and self._is_live(self._detail):
            self.set_tab(PROGRESS)
        else:
            self._update_detail_chrome()

    def open_row(self, row: int, tab: str | None = None) -> None:
        """Klik nebo Enter v přehledu (i ‹ › v detailu): hned přepnout, načíst až potom.

        Na pomalém počítači klik okamžitě ukáže detail s „Načítám…“ a tabulka se načte
        v dalším kroku smyčky událostí (31. 10. 2026, varianta A, bez vláken). Ostatní
        cesty (z Pacientů, po dávce) otevírají synchronně přes `open_entry`.
        """
        if not 0 <= row < len(self._rows):
            return
        entry = self._rows[row]
        self._open_generation += 1
        generation = self._open_generation
        self._detail = entry
        self._task_run = None
        self._recorded_pending = None  # Průběh předchozího běhu už nepatří
        self.table.selectRow(row)
        if not self._is_live(entry):
            self.results_page.show_loading(entry.display_name)
        self.stack.setCurrentIndex(1)
        self.set_tab(tab or (PROGRESS if self._is_live(entry) else VALUES))
        QTimer.singleShot(0, lambda: self._finish_open(generation, entry, tab))

    def _finish_open(self, generation: int, entry: Entry, tab: str | None) -> None:
        if generation != self._open_generation or not self.in_detail():
            return  # mezitím jiný klik, nebo zpět na přehled
        self.open_entry(entry, tab or self._tab)

    def loading(self) -> bool:
        """Pro testy: čeká odložené otevření z přehledu?"""
        return self.results_page.summary.text() == tr("Načítám…")

    def open_entry(self, entry: Entry, tab: str | None = None, prefer: Path | None = None) -> None:
        """Detail běhu: Hodnoty z `ResultsPage`, Průběh z `RunPage` (přehrání z events)."""
        self._open_generation += 1  # synchronní otevření ruší odložené z přehledu
        row = self._row_of(entry)
        self._detail = entry
        self._task_run = None
        if row >= 0 and self.history.current_row() != row:
            self.history.select_row(row)  # ResultsPage.show_run (u vyšetření task_shown)
        else:
            self.results_page.show_run(entry)
        if isinstance(entry, RunGroup) and prefer is not None and entry.contains(prefer):
            self.results_page._show_group(entry, prefer)  # konkrétní úloha vyšetření
        if not isinstance(entry, RunGroup):
            self._defer_recorded(entry)
        if row >= 0:
            self.table.selectRow(row)
        self.stack.setCurrentIndex(1)
        self.set_tab(tab or (PROGRESS if self._is_live(entry) else VALUES))

    def open_run(self, run_dir: Path, tab: str | None = None) -> bool:
        """Detail běhu podle složky (z Hodnocení, po dávce); přepne filtr, je-li třeba."""
        if not self.history.select_dir(run_dir):
            self.history.refresh(select=run_dir)
            if not self.history.select_dir(run_dir):
                return False
        entry = self._find(run_dir) or self.history.current()
        if entry is None:
            return False
        self.open_entry(entry, tab, prefer=run_dir)
        return True

    def show_running(self, run_dir: Path | None = None) -> None:
        """Detail běžícího běhu na Průběhu (Spustit, pruh „právě běží“ na Domů)."""
        target = run_dir or self._live_dir
        if target is not None:
            entry = self._find(target)
            if entry is None:
                self.history.refresh(select=target)
                entry = self._find(target)
            if entry is not None:
                self.open_entry(entry, PROGRESS)
                return
        self.stack.setCurrentIndex(1)
        self.set_tab(PROGRESS)

    def show_values(self) -> None:
        """Po dávce: detail běhu, jehož tabulku Výsledky právě načetly, na Hodnotách."""
        current = self.results_page.current_dir()
        entry = self._find(current) if current is not None else None
        if entry is not None:
            self._detail = entry
            row = self._row_of(entry)
            if row >= 0:
                self.table.selectRow(row)
        self.stack.setCurrentIndex(1)
        self.set_tab(VALUES)

    def show_overview(self) -> None:
        self._open_generation += 1  # rozdělané otevření se už nedokončí
        self.stack.setCurrentIndex(0)

    def _defer_recorded(self, run: RunInfo) -> None:
        """Průběh běhu se přehraje až na záložce Průběh, ne při každém otevření Hodnot.

        Přehrání events.jsonl u 500 nahrávek stálo ~0,2 s z každého kliknutí, i když
        uživatel Průběh neotevřel (31. 10. 2026).
        """
        self._recorded_pending = run
        if self._tab == PROGRESS and self.stack.currentIndex() == 1:
            self._show_pending_recorded()

    def _show_pending_recorded(self) -> None:
        run, self._recorded_pending = self._recorded_pending, None
        if run is not None and not self.run_page.runner.running:
            self.run_page.show_recorded(run)

    def set_tab(self, tab: str) -> None:
        if tab == VALUES and self._is_live(self._detail):
            tab = PROGRESS  # tabulka běžícího běhu se neotvírá
        if tab == PROGRESS:
            self._show_pending_recorded()
        self._tab = tab
        self.detail_stack.setCurrentWidget(self.results_page if tab == VALUES else self.run_page)
        self._update_detail_chrome()
        self.detail_shown.emit(tab)

    def step(self, delta: int) -> None:
        rows = self.visible_rows()
        row = self._row_of(self._detail)
        if row not in rows:
            return
        index = rows.index(row) + delta
        if 0 <= index < len(rows):
            self.open_row(rows[index])

    def relocate(self, old: Path, new: Path) -> None:
        if self._live_dir == old:
            self._live_dir = new

    def _update_detail_chrome(self) -> None:
        entry = self._detail
        live = self._is_live(entry)
        running_other = self.run_page.runner.running and not live
        self.values_tab.setEnabled(not live)
        self.values_tab.setToolTip(
            tr("Tabulka bude po doběhnutí (soubor se během běhu nesmí otevírat).") if live else ""
        )
        self.progress_tab.setEnabled(not running_other or entry is None)
        self.progress_tab.setToolTip(
            tr("Průběh jiného běhu se ukáže, až doběhne aktuální výpočet.")
            if running_other and entry is not None
            else ""
        )
        (self.values_tab if self._tab == VALUES else self.progress_tab).setChecked(True)
        for button, tab in ((self.values_tab, VALUES), (self.progress_tab, PROGRESS)):
            theme.set_role(button, "primary" if tab == self._tab else "")
        if live:
            self.tab_note.setText(tr("běží, tabulka hodnot bude po doběhnutí"))
        elif running_other and self._tab == PROGRESS and entry is not None:
            self.tab_note.setText(tr("Průběh ukazuje aktuální výpočet, ne tento běh."))
        else:
            self.tab_note.setText("")
        self.crumb.setText(entry.display_name if entry is not None else self.run_title.text())
        rows = self.visible_rows()
        row = self._row_of(entry)
        position = rows.index(row) if row in rows else -1
        self.prev_btn.setEnabled(position > 0)
        self.next_btn.setEnabled(0 <= position < len(rows) - 1)


__all__ = ["PROGRESS", "VALUES", "RunsPage"]
