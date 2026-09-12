"""Výběr protokolu: přepínač úlohy a karty protokolů k ní.

Klinik nejdřív vidí svou úlohu (fonace, DDK, pohádka…), pak dva tři
protokoly k ní. Karta říká jméno, popis, které providery protokol spustí
a kolik to stojí (naposledy změřená doba na nahrávku, jinak odhad).
Widget nezná knihovnu; souhrny mu dodá stránka (`ProtocolCardInfo`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.protocol import Protocol
from ...i18n import tr
from .. import theme


@dataclass(slots=True)
class ProtocolCardInfo:
    """Co karta ukáže navíc k protokolu samotnému."""

    providers: list[str] = field(default_factory=list)
    hint: str = ""  # „naposledy 92 s na nahrávku“ nebo orientační cena


def _pill(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("pill")
    label.setProperty("role", role)
    return label


class ProtocolCard(QFrame):
    details_requested = Signal()  # odkaz „co se počítá“

    def __init__(self, proto: Protocol, info: ProtocolCardInfo, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setProperty("role", "neutral")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(6)
        name = QLabel(proto.display_name)
        name.setObjectName("card_title")
        head.addWidget(name)
        if not proto.builtin:
            head.addWidget(_pill(tr("vlastní"), "accent"))
        self.modified = _pill(tr("upraveno"), "warn")
        self.modified.setVisible(False)
        head.addWidget(self.modified)
        head.addStretch(1)
        hint = QLabel(info.hint)
        hint.setObjectName("muted")
        head.addWidget(hint)
        layout.addLayout(head)
        desc = QLabel(proto.display_description)
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        foot = QHBoxLayout()
        foot.setSpacing(6)
        for provider in info.providers:
            foot.addWidget(_pill(contract.PROVIDER_SHORT.get(provider, provider), "neutral"))
        if not info.providers:
            foot.addWidget(_pill(tr("bez modelů"), "ok"))
        foot.addStretch(1)
        self.details = QLabel(f"<a href='#' style='color:{theme.ACCENT}'>{tr('co se počítá')}</a>")
        self.details.setObjectName("muted")
        self.details.setToolTip(tr("Seznam feature a sloupců s popisem (i dvojklik na kartu)."))
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.details.linkActivated.connect(lambda _href: self.details_requested.emit())
        foot.addWidget(self.details)
        layout.addLayout(foot)


class ProtocolList(QWidget):
    current_changed = Signal(object)  # Protocol | None
    details_requested = Signal(object)  # Protocol: dvojklik na kartu nebo odkaz

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._protocols: list[Protocol] = []
        self._infos: dict[str, ProtocolCardInfo] = {}
        self._cards: dict[str, ProtocolCard] = {}
        self._task = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        # Lišta úloh je vlastní widget, aby si ji stránka mohla dát jinam;
        # tlačítka v mřížce po třech, ať se vejdou i do užšího sloupce.
        self.task_bar = QWidget()
        self.tasks_grid = QGridLayout(self.task_bar)
        self.tasks_grid.setContentsMargins(0, 0, 0, 0)
        self.tasks_grid.setSpacing(6)
        self.task_group = QButtonGroup(self)
        self.task_group.setExclusive(True)
        self.task_buttons: dict[str, QPushButton] = {}
        for i, task in enumerate(contract.TASKS):
            btn = QPushButton(contract.TASK_SHORT.get(task, task))
            btn.setToolTip(contract.TASK_LABELS[task])
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, t=task: self._task_clicked(t))
            self.task_group.addButton(btn)
            self.tasks_grid.addWidget(btn, i // 3, i % 3)
            self.task_buttons[task] = btn
        layout.addWidget(self.task_bar)

        self.list = QListWidget()
        self.list.setSpacing(3)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._item_changed)
        self.list.itemDoubleClicked.connect(
            lambda item: self.details_requested.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        self.list.viewport().installEventFilter(self)
        layout.addWidget(self.list, 1)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.list.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_cards()
        return super().eventFilter(obj, event)

    def _fit_cards(self) -> None:
        """Karty na šířku seznamu; výška podle zalomeného popisu."""
        width = self.list.viewport().width() - 2 * self.list.spacing() - 4
        if width <= 0:
            return
        for row in range(self.list.count()):
            item = self.list.item(row)
            card = self.list.itemWidget(item)
            if card is None:
                continue
            card.setFixedWidth(width)
            layout = card.layout()
            height = layout.heightForWidth(width) if layout.hasHeightForWidth() else 0
            height = max(height, card.sizeHint().height())
            item.setSizeHint(card.sizeHint().expandedTo(card.size()).__class__(width, height))

    # --- naplnění ---------------------------------------------------------------

    def set_protocols(
        self,
        protocols: list[Protocol],
        infos: dict[str, ProtocolCardInfo] | None = None,
        *,
        current: str = "",
    ) -> None:
        self._protocols = list(protocols)
        self._infos = dict(infos or {})
        present = {p.task for p in protocols}
        for task, btn in self.task_buttons.items():
            btn.setVisible(task in present)
        wanted = next((p for p in protocols if p.name == current), None)
        task = wanted.task if wanted else self._task
        if task not in present:
            task = next((t for t in contract.TASKS if t in present), "")
        self._show_task(task, select=wanted.name if wanted else "")

    def set_info(self, name: str, info: ProtocolCardInfo) -> None:
        self._infos[name] = info
        if name in self._cards:
            self._show_task(self._task, select=self.current_name())

    def set_modified(self, name: str, modified: bool) -> None:
        card = self._cards.get(name)
        if card is not None:
            card.modified.setVisible(modified)

    def names(self) -> list[str]:
        return [p.name for p in self._protocols]

    # --- výběr --------------------------------------------------------------------

    def current(self) -> Protocol | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def current_name(self) -> str:
        proto = self.current()
        return proto.name if proto else ""

    def select(self, name: str) -> bool:
        proto = next((p for p in self._protocols if p.name == name), None)
        if proto is None:
            return False
        if proto.task != self._task:
            self._show_task(proto.task, select=name)
            return True
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole).name == name:
                self.list.setCurrentItem(item)
                return True
        return False

    def _task_clicked(self, task: str) -> None:
        if task != self._task:
            self._show_task(task)

    def _show_task(self, task: str, *, select: str = "") -> None:
        self._task = task
        btn = self.task_buttons.get(task)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self.list.blockSignals(True)
        self.list.clear()
        self._cards.clear()
        first: QListWidgetItem | None = None
        chosen: QListWidgetItem | None = None
        for proto in self._protocols:
            if proto.task != task:
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, proto)
            card = ProtocolCard(proto, self._infos.get(proto.name, ProtocolCardInfo()))
            card.details_requested.connect(lambda p=proto: self.details_requested.emit(p))
            item.setSizeHint(card.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, card)
            self._cards[proto.name] = card
            first = first or item
            if proto.name == select:
                chosen = item
        self._fit_cards()
        self.list.blockSignals(False)
        target = chosen or first
        self.list.setCurrentItem(target)  # vydá currentItemChanged, tedy i current_changed
        if target is None:
            self.current_changed.emit(None)

    def _item_changed(self, item: QListWidgetItem | None, prev: QListWidgetItem | None) -> None:
        for it, role in ((prev, "neutral"), (item, "selected")):
            if it is not None:
                card = self._cards.get(it.data(Qt.ItemDataRole.UserRole).name)
                if card is not None:
                    theme.set_role(card, role)
        self.current_changed.emit(item.data(Qt.ItemDataRole.UserRole) if item else None)
