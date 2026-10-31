"""Historie běhů jako karty, sdílená stránkami Výpočet a Výsledky.

Karta: název protokolu, štítek stavu, řádek s datem, počtem nahrávek
a dobou. Proužek vlevo má barvu stavu, vybraná karta modrý rámeček (jako
u protokolů). Seznam čte složky běhů z `backend/history.py`; při obnově
zůstane vybraný týž běh. Dvojklik otevře složku, pravé tlačítko nabídku,
Delete a tlačítko Smazat… pošlou `delete_requested` (mazání řeší hlavní okno).

Přepínač Analýzy · Hodnocení · Normy (`set_filter`, `filter_changed`) ukazuje
jen jednu skupinu běhů (`RunInfo.kind`), s počty v tlačítkách; výchozí jsou
Analýzy a volbu si pamatuje hlavní okno v nastavení. Běhy jednoho hodnocení
z nahrávek jsou jedna karta (`RunGroup`: pacient, úlohy, souhrnný stav);
`selected` pak nese skupinu a stránka si z ní vybere běh. Výběr složky, která
je v jiné skupině než zobrazené (`select_dir`, `refresh(select=…)`), přepne
filtr sám.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...backend import tasks
from ...backend.history import (
    KIND_ANALYSIS,
    KIND_ASSESSMENT,
    KIND_NORM,
    KINDS,
    RunGroup,
    RunInfo,
    group_runs,
    list_runs,
)
from ...i18n import tr
from .. import theme
from .elided_label import ElidedLabel

Entry = RunInfo | RunGroup

# role karty (proužek) a štítku podle stavu běhu
STATUS_ROLES: dict[str, str] = {
    "ok": "ok",
    "cancelled": "warn",
    "error": "missing",
    "prepare": "neutral",
    "running": "accent",
    "interrupted": "warn",
    "unknown": "neutral",
}

KIND_LABELS = {
    KIND_ANALYSIS: tr("Analýzy"),
    KIND_ASSESSMENT: tr("Hodnocení"),
    KIND_NORM: tr("Normy"),
}


def _pill(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("pill")
    label.setProperty("role", role)
    return label


def _format_seconds(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def entry_dir(entry: Entry) -> Path | None:
    """Složka karty: běh, nebo složka vyšetření (u nesloučeného None)."""
    return entry.dir


def entry_contains(entry: Entry, run_dir: Path) -> bool:
    if isinstance(entry, RunGroup):
        return entry.contains(run_dir)
    return entry.dir == run_dir


class RunCard(QFrame):
    def __init__(self, entry: Entry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = entry
        self.setObjectName("card")
        self.setProperty("role", STATUS_ROLES.get(entry.status, "neutral"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = ElidedLabel(entry.display_name)  # dlouhý název se zkrátí, celý v tooltipu
        self.title.setObjectName("card_title")
        head.addWidget(self.title, 1)
        if isinstance(entry, RunGroup):
            # vyšetření pacienta z Hodnocení z nahrávek: běhy po úlohách v jedné kartě
            self.kind_pill = _pill(tr("hodnocení"), "accent")
            self.kind_pill.setToolTip(
                tr("Běhy hodnocení z nahrávek, jeden na úlohu. Hodnocení je na stránce Hodnocení.")
            )
            head.addWidget(self.kind_pill, 0, Qt.AlignmentFlag.AlignTop)
        elif entry.norm:
            # dávka nové normy: zdravé kontroly, ne pacienti (Hodnocení ji nenabízí)
            self.norm_pill = _pill(tr("norma"), "accent")
            self.norm_pill.setToolTip(tr("Kontroly pro normu, v Hodnocení se nenabízejí."))
            head.addWidget(self.norm_pill, 0, Qt.AlignmentFlag.AlignTop)
        self.pill = _pill(entry.status_label, STATUS_ROLES.get(entry.status, "neutral"))
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        self.detail = QLabel(
            self._group_text(entry) if isinstance(entry, RunGroup) else self._detail_text(entry)
        )
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)  # u vyšetření je řádek delší (úlohy, co běží a čekají)
        layout.addWidget(self.detail)
        folder = entry.dir if entry.dir is not None else entry.runs[0].dir.parent
        self.setToolTip(str(folder))

    @staticmethod
    def _detail_text(info: RunInfo) -> str:
        parts = [info.started.strftime("%d.%m. %H:%M") if info.started else info.dir.name]
        if info.norm:
            parts.append(tr("kontroly normy"))
        if info.label:
            parts.append(info.protocol_name)
        if info.total:
            if info.processed is not None and info.processed != info.total:
                parts.append(
                    tr("{n} z {total} nahrávek").format(n=info.processed, total=info.total)
                )
            else:
                parts.append(tr("{n} nahrávek").format(n=info.total))
        elif info.rows is not None:
            parts.append(tr("{n} nahrávek").format(n=info.rows))
        if info.seconds:
            parts.append(_format_seconds(info.seconds))
        return " · ".join(parts)

    @staticmethod
    def _group_text(group: RunGroup) -> str:
        parts = [group.started.strftime("%d.%m. %H:%M") if group.started else ""]
        if group.status == "ok":
            parts.append(tr("{n} úlohy").format(n=group.task_count))
            parts.append(tr("{n} nahrávek").format(n=group.total or group.processed))
        else:
            parts.append(tr("{done} z {n} úloh hotové").format(done=group.done, n=group.task_count))
            for status, what in (
                ("running", tr("{task} běží")),
                ("error", tr("{task} s chybou")),
                ("cancelled", tr("{task} zrušeno")),
                ("interrupted", tr("{task} přerušeno")),
            ):
                names = [tasks.label(r.task) for r in group.runs if r.status == status]
                if names:
                    parts.append(what.format(task=", ".join(names)))
            waiting = [tasks.label(t) for t in group.waiting_tasks()]
            if waiting:
                parts.append(tr("čeká: {tasks}").format(tasks=", ".join(waiting)))
        if group.seconds:
            parts.append(_format_seconds(group.seconds))
        return " · ".join(p for p in parts if p)

    def set_current(self, current: bool) -> None:
        theme.set_role(
            self, "selected" if current else STATUS_ROLES.get(self.info.status, "neutral")
        )


class RunHistory(QWidget):
    selected = Signal(object)  # RunInfo | RunGroup
    delete_requested = Signal(object)  # RunInfo | RunGroup: Smazat…, Delete, pravé tlačítko
    rename_requested = Signal(object)  # RunInfo: Přejmenovat…, F2
    filter_changed = Signal(str)  # skupina z KINDS; i při samočinném přepnutí výběrem
    rebuilt = Signal()  # seznam se znovu sestavil (přehled na stránce Výsledky ho opisuje)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._work_root: Path | None = None
        self._runs: list[RunInfo] = []  # všechny běhy, ploše
        self._entries: list[Entry] = []  # zobrazené karty (po filtru)
        self._cards: list[RunCard] = []
        self._filter = KIND_ANALYSIS
        # rozpracovaná hodnocení (id → úlohy se stavem), dodává hlavní okno
        self._plans_lookup: Callable[[], dict[str, list[tuple[str, str]]]] = dict
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        head = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setObjectName("section")
        self.refresh_btn = QPushButton(tr("Obnovit"))
        self.refresh_btn.clicked.connect(lambda: self.refresh())  # bez `checked` jako select
        self.delete_btn = QPushButton(tr("Smazat…"))
        self.delete_btn.setToolTip(
            tr("Přesune složku vybraného běhu do Koše. Nahrávek se to netýká.")
        )
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_current)
        head.addWidget(self.title, 1)
        head.addWidget(self.delete_btn)
        head.addWidget(self.refresh_btn)
        layout.addLayout(head)
        # přepínač skupin: jedna vybraná, počty v tlačítkách
        bar = QHBoxLayout()
        bar.setSpacing(0)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self.filter_buttons: dict[str, QPushButton] = {}
        for kind in KINDS:
            btn = QPushButton(KIND_LABELS[kind])
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setChecked(kind == self._filter)
            btn.clicked.connect(lambda _=False, k=kind: self._filter_clicked(k))
            self.filter_group.addButton(btn)
            self.filter_buttons[kind] = btn
            bar.addWidget(btn, 1)
        self.filter_buttons[KIND_ASSESSMENT].setToolTip(
            tr("Běhy z Hodnocení z nahrávek, jedna karta na vyšetření pacienta.")
        )
        self.filter_buttons[KIND_NORM].setToolTip(tr("Dávky kontrol z průvodce Nová norma."))
        layout.addLayout(bar)
        self.runs = QListWidget()
        self.runs.setObjectName("cards")
        self.runs.setSpacing(3)
        self.runs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.runs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.runs.currentItemChanged.connect(self._item_changed)
        self.runs.itemDoubleClicked.connect(lambda _item: self.open_current_folder())
        self.runs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.runs.customContextMenuRequested.connect(self._menu)
        self.runs.viewport().installEventFilter(self)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.runs, self._delete_current)
        QShortcut(QKeySequence(Qt.Key.Key_F2), self.runs, self._rename_current)
        layout.addWidget(self.runs, 1)
        self.empty = QLabel(tr("Zatím žádný běh."))
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)
        self.empty.hide()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.runs.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_cards()
        return super().eventFilter(obj, event)

    def _fit_cards(self) -> None:
        width = self.runs.viewport().width() - 2 * self.runs.spacing() - 4
        if width <= 0:
            return
        for row in range(self.runs.count()):
            item = self.runs.item(row)
            card = self.runs.itemWidget(item)
            if card is None:
                continue
            card.setFixedWidth(width)
            item.setSizeHint(QSize(width, card.sizeHint().height()))

    # --- filtr -----------------------------------------------------------------------

    def filter(self) -> str:
        return self._filter

    def set_filter(self, kind: str, *, select: Path | None = None) -> None:
        """Přepne skupinu (bez signálu, když se nemění); `select` jako u `refresh`."""
        if kind not in KINDS:
            return
        changed = kind != self._filter
        self._filter = kind
        self.filter_buttons[kind].setChecked(True)
        self._rebuild(select)
        if changed:
            self.filter_changed.emit(kind)

    def _filter_clicked(self, kind: str) -> None:
        if kind != self._filter:
            self.set_filter(kind)

    def _kind_of(self, run_dir: Path) -> str | None:
        for entry in self._grouped():
            if entry_contains(entry, run_dir):
                return entry.kind
        return None

    def _grouped(self) -> list[Entry]:
        return group_runs(self._runs, self._plans_lookup())

    # --- data ----------------------------------------------------------------------

    def set_work_root(self, root: Path) -> None:
        self._work_root = root
        self.refresh()

    def set_plans_lookup(self, lookup: Callable[[], dict[str, list[tuple[str, str]]]]) -> None:
        """Odkud brát rozpracovaná hodnocení (úlohy, které ještě nemají běh)."""
        self._plans_lookup = lookup

    def all_runs(self) -> list[RunInfo]:
        """Všechny běhy bez ohledu na filtr, ploše (i vnořené ve vyšetření)."""
        return list(self._runs)

    def entries(self) -> list[Entry]:
        return list(self._entries)

    def count(self) -> int:
        return len(self._entries)

    def card(self, row: int) -> RunCard | None:
        return self._cards[row] if 0 <= row < len(self._cards) else None

    def current_row(self) -> int:
        return self.runs.currentRow()

    def current(self) -> Entry | None:
        row = self.runs.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def select_row(self, row: int) -> None:
        if 0 <= row < self.runs.count():
            self.runs.setCurrentRow(row)

    def select_dir(self, run_dir: Path) -> bool:
        """Vybere kartu běhu (nebo vyšetření, které ho obsahuje); přepne filtr, je-li třeba."""
        kind = self._kind_of(run_dir)
        if kind is not None and kind != self._filter:
            self.set_filter(kind)
        for row, entry in enumerate(self._entries):
            if entry_contains(entry, run_dir):
                self.runs.setCurrentRow(row)
                return True
        return False

    def refresh(self, select: Path | None = None) -> None:
        """Znovu načte seznam; `select` je složka běhu, která má zůstat vybraná.

        Když je ta složka v jiné skupině, než filtr ukazuje, filtr se přepne
        (po skončené dávce hodnocení nebo z odkazu „otevřít běh“)."""
        current = self.current()
        keep = select or (current.dir if current is not None and current.dir else None)
        if keep is None and isinstance(current, RunGroup):
            keep = current.runs[0].dir
        self._runs = list_runs(self._work_root) if self._work_root else []
        if keep is not None:
            kind = self._kind_of(keep)
            if kind is not None and kind != self._filter:
                self.set_filter(kind, select=keep)
                return
        self._rebuild(keep)

    def _rebuild(self, keep: Path | None) -> None:
        all_entries = self._grouped()
        counts = {kind: 0 for kind in KINDS}
        for entry in all_entries:
            counts[entry.kind] += 1
        for kind, btn in self.filter_buttons.items():
            btn.setText(
                tr("{label} ({n})").format(label=KIND_LABELS[kind], n=counts[kind])
                if counts[kind]
                else KIND_LABELS[kind]
            )
        self._entries = [e for e in all_entries if e.kind == self._filter]
        self.runs.blockSignals(True)
        self.runs.clear()
        self._cards = []
        select_row: int | None = None
        for row, entry in enumerate(self._entries):
            item = QListWidgetItem()
            card = RunCard(entry)
            item.setSizeHint(card.sizeHint())
            self.runs.addItem(item)
            self.runs.setItemWidget(item, card)
            self._cards.append(card)
            if keep is not None and entry_contains(entry, keep):
                select_row = row
        self._fit_cards()
        self.runs.blockSignals(False)
        self.title.setText(
            tr("{title} ({n})").format(title=self._title, n=len(self._entries))
            if self._entries
            else self._title
        )
        self.empty.setVisible(not self._entries)
        if select_row is not None:
            # výběr zůstává, kde byl; stránka už ten běh ukazuje, znovu ho nenačítat
            self.runs.blockSignals(True)
            self.runs.setCurrentRow(select_row)
            self.runs.blockSignals(False)
            self._mark_current()
        self.delete_btn.setEnabled(self.can_delete(self.current()))
        self.rebuilt.emit()

    # --- výběr a akce --------------------------------------------------------------

    def _mark_current(self) -> None:
        row = self.runs.currentRow()
        for i, card in enumerate(self._cards):
            card.set_current(i == row)

    def _item_changed(self, _item: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        self._mark_current()
        entry = self.current()
        self.delete_btn.setEnabled(self.can_delete(entry))
        if entry is not None:
            self.selected.emit(entry)

    def can_delete(self, entry: Entry | None) -> bool:
        return entry is not None and entry.status != "running"

    @staticmethod
    def can_rename(entry: Entry | None) -> bool:
        """Vyšetření se nepřejmenovává (název je pacient), jednotlivý běh ano."""
        return isinstance(entry, RunInfo)

    def _delete_current(self) -> None:
        entry = self.current()
        if self.can_delete(entry):
            self.delete_requested.emit(entry)

    def _rename_current(self) -> None:
        entry = self.current()
        if self.can_rename(entry):
            self.rename_requested.emit(entry)

    def open_current_folder(self) -> None:
        entry = self.current()
        if entry is None:
            return
        folder = entry.dir if entry.dir is not None else entry.runs[0].dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _menu(self, pos) -> None:  # noqa: ANN001
        item = self.runs.itemAt(pos)
        if item is None:
            return
        self.runs.setCurrentItem(item)
        entry = self.current()
        if entry is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction(tr("Otevřít složku"))
        open_action.triggered.connect(self.open_current_folder)
        if self.can_rename(entry):
            rename_action = menu.addAction(tr("Přejmenovat…"))
            rename_action.triggered.connect(lambda: self.rename_requested.emit(entry))
        delete_action = menu.addAction(
            tr("Smazat vyšetření…") if isinstance(entry, RunGroup) else tr("Smazat běh…")
        )
        delete_action.setEnabled(self.can_delete(entry))
        delete_action.triggered.connect(lambda: self.delete_requested.emit(entry))
        menu.exec(self.runs.viewport().mapToGlobal(pos))
