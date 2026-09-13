"""Historie běhů jako karty, sdílená stránkami Výpočet a Výsledky.

Karta: název protokolu, štítek stavu, řádek s datem, počtem nahrávek
a dobou. Proužek vlevo má barvu stavu, vybraná karta modrý rámeček (jako
u protokolů). Seznam čte složky běhů z `backend/history.py`; při obnově
zůstane vybraný týž běh. Dvojklik otevře složku, pravé tlačítko nabídku,
Delete a tlačítko Smazat… pošlou `delete_requested` (mazání řeší hlavní okno).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
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

from ...backend.history import RunInfo, list_runs
from ...i18n import tr
from .. import theme

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


class RunCard(QFrame):
    def __init__(self, info: RunInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("card")
        self.setProperty("role", STATUS_ROLES.get(info.status, "neutral"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel(info.protocol_name)
        self.title.setObjectName("card_title")
        head.addWidget(self.title, 1)
        self.pill = _pill(info.status_label, STATUS_ROLES.get(info.status, "neutral"))
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        self.detail = QLabel(self._detail_text(info))
        self.detail.setObjectName("muted")
        layout.addWidget(self.detail)
        self.setToolTip(str(info.dir))

    @staticmethod
    def _detail_text(info: RunInfo) -> str:
        parts = [info.started.strftime("%d.%m. %H:%M") if info.started else info.dir.name]
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

    def set_current(self, current: bool) -> None:
        theme.set_role(
            self, "selected" if current else STATUS_ROLES.get(self.info.status, "neutral")
        )


class RunHistory(QWidget):
    selected = Signal(object)  # RunInfo
    delete_requested = Signal(object)  # RunInfo: Smazat…, Delete, pravé tlačítko

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._work_root: Path | None = None
        self._runs: list[RunInfo] = []
        self._cards: list[RunCard] = []
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        head = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setObjectName("section")
        self.refresh_btn = QPushButton(tr("Obnovit"))
        self.refresh_btn.clicked.connect(self.refresh)
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

    # --- data ----------------------------------------------------------------------

    def set_work_root(self, root: Path) -> None:
        self._work_root = root
        self.refresh()

    def all_runs(self) -> list[RunInfo]:
        return list(self._runs)

    def count(self) -> int:
        return len(self._runs)

    def card(self, row: int) -> RunCard | None:
        return self._cards[row] if 0 <= row < len(self._cards) else None

    def current_row(self) -> int:
        return self.runs.currentRow()

    def current(self) -> RunInfo | None:
        row = self.runs.currentRow()
        return self._runs[row] if 0 <= row < len(self._runs) else None

    def select_row(self, row: int) -> None:
        if 0 <= row < self.runs.count():
            self.runs.setCurrentRow(row)

    def select_dir(self, run_dir: Path) -> bool:
        for row, info in enumerate(self._runs):
            if info.dir == run_dir:
                self.runs.setCurrentRow(row)
                return True
        return False

    def refresh(self, select: Path | None = None) -> None:
        """Znovu načte seznam; `select` je složka běhu, která má zůstat vybraná."""
        keep = select or (self.current().dir if self.current() else None)
        self._runs = list_runs(self._work_root) if self._work_root else []
        self.runs.blockSignals(True)
        self.runs.clear()
        self._cards = []
        select_row: int | None = None
        for row, info in enumerate(self._runs):
            item = QListWidgetItem()
            card = RunCard(info)
            item.setSizeHint(card.sizeHint())
            self.runs.addItem(item)
            self.runs.setItemWidget(item, card)
            self._cards.append(card)
            if keep is not None and info.dir == keep:
                select_row = row
        self._fit_cards()
        self.runs.blockSignals(False)
        self.title.setText(
            tr("{title} ({n})").format(title=self._title, n=len(self._runs))
            if self._runs
            else self._title
        )
        self.empty.setVisible(not self._runs)
        if select_row is not None:
            # výběr zůstává, kde byl; stránka už ten běh ukazuje, znovu ho nenačítat
            self.runs.blockSignals(True)
            self.runs.setCurrentRow(select_row)
            self.runs.blockSignals(False)
            self._mark_current()
        self.delete_btn.setEnabled(self.can_delete(self.current()))

    # --- výběr a akce --------------------------------------------------------------

    def _mark_current(self) -> None:
        row = self.runs.currentRow()
        for i, card in enumerate(self._cards):
            card.set_current(i == row)

    def _item_changed(self, _item: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        self._mark_current()
        info = self.current()
        self.delete_btn.setEnabled(self.can_delete(info))
        if info is not None:
            self.selected.emit(info)

    def can_delete(self, info: RunInfo | None) -> bool:
        return info is not None and info.status != "running"

    def _delete_current(self) -> None:
        info = self.current()
        if self.can_delete(info):
            self.delete_requested.emit(info)

    def open_current_folder(self) -> None:
        info = self.current()
        if info is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(info.dir)))

    def _menu(self, pos) -> None:  # noqa: ANN001
        item = self.runs.itemAt(pos)
        if item is None:
            return
        self.runs.setCurrentItem(item)
        info = self.current()
        if info is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction(tr("Otevřít složku"))
        open_action.triggered.connect(self.open_current_folder)
        delete_action = menu.addAction(tr("Smazat běh…"))
        delete_action.setEnabled(self.can_delete(info))
        delete_action.triggered.connect(lambda: self.delete_requested.emit(info))
        menu.exec(self.runs.viewport().mapToGlobal(pos))
