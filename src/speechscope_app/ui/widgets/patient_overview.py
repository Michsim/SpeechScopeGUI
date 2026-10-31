"""Přehled pacientů přes celou šířku (31. 10. 2026, varianta B, podoba D).

Mockup https://claude.ai/artifact/J8iCqC36qpFSDbyip27jb6 (artboard D, `docs/mockups/prehledy/`
mimo git). Jeden seznam bez skupin, řádek na pacienta (a na rozpracované hodnocení):
barevný pruh stavu vlevo (štítek stavu v řádku není, rozhodnutí uživatele), jméno a
„muž · 64 let · 3 vyšetření“, vpravo datum
posledního vyšetření a úlohy, šipka ›. Výsledky (dimenze) v řádku záměrně nejsou
(rozhodnutí uživatele), ty jsou v detailu. Rozpracované hodnocení má pod jménem úlohy se
stavem. Nahoře hledání, filtr všichni · mimo normu · počítá se a řazení (poslední
vyšetření, jméno, stav). Dvojklik nebo Enter otevře detail (`open_requested` s indexem
karty ve skrytém seznamu stránky Pacienti, nezávisle na řazení).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...i18n import tr
from .. import theme

ALL, ABNORMAL, PENDING = "all", "abnormal", "pending"
SORT_LAST, SORT_NAME, SORT_STATUS = "last", "name", "status"
STRIPE = {
    "ok": theme.OK,
    "warn": theme.WARN,
    "missing": theme.MISSING,
    "accent": theme.ACCENT,
    "neutral": "#9ca3af",
}
STATUS_RANK = {"bad": 0, "warn": 1, "pending": 2, "ok": 3}


@dataclass(slots=True)
class OverviewRow:
    name: str
    sex: str
    age: str
    status: str
    role: str  # barva štítku a pruhu (role z theme)
    visits: str
    last: str  # datum posledního vyšetření pro čtení
    tasks: str
    kind: str = "patient"  # patient | pending
    band: str = ""
    sort_date: str = ""  # ISO, pro řazení
    chips: list[tuple[str, str]] = field(default_factory=list)  # úlohy rozpracovaného

    def person(self) -> str:
        parts = [self.sex, tr("{age} let").format(age=self.age) if self.age else ""]
        parts = [p for p in parts if p]
        if self.kind == "patient" and self.visits.isdigit():
            n = int(self.visits)
            parts.append(tr("1 vyšetření") if n == 1 else tr("{n} vyšetření").format(n=n))
        return " · ".join(parts)


def _pill(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("pill")
    theme.set_role(label, role)
    return label


class PatientRowWidget(QFrame):
    """Jeden řádek seznamu: pruh, jméno se štítkem a údaji, vpravo datum a úlohy."""

    def __init__(self, row: OverviewRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row = row
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)
        stripe = QFrame()
        stripe.setFixedWidth(6)
        stripe.setStyleSheet(
            f"background: {STRIPE.get(row.role, STRIPE['neutral'])}; border-radius: 3px;"
        )
        layout.addWidget(stripe)
        body = QVBoxLayout()
        body.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.name = QLabel(row.name)
        font = QFont(self.name.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 1)
        self.name.setFont(font)
        top.addWidget(self.name)
        # štítek stavu (mimo normu, v normě…) v seznamu záměrně není: stav říká pruh vlevo
        self.person = QLabel(row.person())
        self.person.setObjectName("muted")
        top.addWidget(self.person)
        top.addStretch(1)
        body.addLayout(top)
        if row.chips:
            chips = QHBoxLayout()
            chips.setSpacing(6)
            for text, role in row.chips:
                chips.addWidget(_pill(text, role))
            chips.addStretch(1)
            body.addLayout(chips)
        layout.addLayout(body, 1)
        self.side = QLabel("\n".join(t for t in (row.last, row.tasks) if t))
        self.side.setObjectName("muted")
        self.side.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.side)
        chevron = QLabel("›")
        chevron.setObjectName("muted")
        chevron_font = QFont(chevron.font())
        chevron_font.setPointSizeF(chevron_font.pointSizeF() + 4)
        chevron.setFont(chevron_font)
        layout.addWidget(chevron)


class PatientOverview(QWidget):
    open_requested = Signal(int)  # index karty ve skrytém seznamu stránky
    search_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[OverviewRow] = []
        self._filter = ALL
        self._sort = SORT_LAST
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        bar.addWidget(self.summary)
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat pacienta"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self.search_changed)
        bar.addWidget(self.search)
        self.filter_group = QButtonGroup(self)
        self.filter_buttons: dict[str, QPushButton] = {}
        for key, label, tip in (
            (ALL, tr("všichni"), ""),
            (ABNORMAL, tr("mimo normu"), tr("Poslední vyšetření mimo normu nebo hraniční.")),
            (PENDING, tr("počítá se"), tr("Rozpracovaná hodnocení, dávky se počítají.")),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _c=False, k=key: self.set_filter(k))
            self.filter_group.addButton(btn)
            self.filter_buttons[key] = btn
            bar.addWidget(btn)
        sort_label = QLabel(tr("řadit:"))
        sort_label.setObjectName("muted")
        bar.addSpacing(6)
        bar.addWidget(sort_label)
        self.sort_combo = QComboBox()
        for key, label in (
            (SORT_LAST, tr("poslední vyšetření")),
            (SORT_NAME, tr("jméno")),
            (SORT_STATUS, tr("stav")),
        ):
            self.sort_combo.addItem(label, key)
        self.sort_combo.currentIndexChanged.connect(
            lambda _i: self.set_sort(self.sort_combo.currentData())
        )
        bar.addWidget(self.sort_combo)
        layout.addLayout(bar)

        frame = QFrame()
        frame.setObjectName("card")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setSpacing(0)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet(
            "QListWidget { background: transparent; }"
            f"QListWidget::item {{ border-bottom: 1px solid {theme.BORDER}; }}"
            f"QListWidget::item:selected {{ background: {theme.ACCENT_SOFT}; }}"
            f"QListWidget::item:hover:!selected {{ background: {theme.NEUTRAL_SOFT}; }}"
        )
        self.list.itemDoubleClicked.connect(self._open_item)
        self.list.itemActivated.connect(self._open_item)
        frame_layout.addWidget(self.list)
        layout.addWidget(frame, 1)
        self.list_frame = frame
        self.empty = QLabel("")
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)
        self.empty.hide()
        hint = QLabel(tr("Dvojklik na řádek (nebo Enter) otevře pacienta."))
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.set_filter(ALL)

    # --- data ----------------------------------------------------------------------------

    def filter(self) -> str:
        return self._filter

    def set_filter(self, key: str) -> None:
        self._filter = key
        for k, btn in self.filter_buttons.items():
            btn.setChecked(k == key)
            theme.set_role(btn, "primary" if k == key else "")
        self._apply_filter()

    def sort(self) -> str:
        return self._sort

    def set_sort(self, key: str) -> None:
        if key not in (SORT_LAST, SORT_NAME, SORT_STATUS):
            return
        self._sort = key
        index = self.sort_combo.findData(key)
        if index >= 0 and index != self.sort_combo.currentIndex():
            self.sort_combo.setCurrentIndex(index)
        self._rebuild(self.current_index())

    def set_empty_text(self, text: str) -> None:
        self.empty.setText(text)

    def row(self, index: int) -> OverviewRow | None:
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def count(self) -> int:
        return len(self._rows)

    def set_rows(self, rows: list[OverviewRow], current: int = -1) -> None:
        self._rows = list(rows)
        patients = sum(1 for r in rows if r.kind == "patient")
        pending = sum(1 for r in rows if r.kind == "pending")
        abnormal = sum(1 for r in rows if r.band == "bad")
        parts = [tr("{n} pacientů").format(n=patients)]
        if pending:
            parts.append(tr("{n} se počítá").format(n=pending))
        if abnormal:
            parts.append(tr("{n} mimo normu").format(n=abnormal))
        self.summary.setText(" · ".join(parts) if rows else "")
        self.list_frame.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self._rebuild(current)

    def update_row(self, index: int, row: OverviewRow) -> None:
        if not 0 <= index < len(self._rows):
            return
        self._rows[index] = row
        item = self._item_for(index)
        if item is not None:
            old = self.list.itemWidget(item)
            if old is not None:
                # starý řádek napřed skrýt: Qt ho maže až z fronty a kreslil by se přes nový
                self.list.removeItemWidget(item)
                old.hide()
                old.setParent(None)
                old.deleteLater()
            widget = PatientRowWidget(row)
            item.setSizeHint(QSize(0, widget.sizeHint().height()))
            self.list.setItemWidget(item, widget)
        self._apply_filter()

    def _sort_key(self, index: int) -> tuple:
        row = self._rows[index]
        if self._sort == SORT_NAME:
            return (row.name.lower(),)
        if self._sort == SORT_STATUS:
            rank = STATUS_RANK.get("pending" if row.kind == "pending" else row.band, 4)
            return (rank, row.name.lower())
        # poslední vyšetření: rozpracované nahoře, pak nejnovější vyšetření
        return (0 if row.kind == "pending" else 1, _desc(row.sort_date), row.name.lower())

    def order(self) -> list[int]:
        """Indexy řádků (= karet) v pořadí, jak jsou v seznamu."""
        return [
            int(self.list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.list.count())
        ]

    def _rebuild(self, current: int = -1) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for index in sorted(range(len(self._rows)), key=self._sort_key):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, index)
            widget = PatientRowWidget(self._rows[index])
            item.setSizeHint(QSize(0, widget.sizeHint().height()))
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
        self.list.blockSignals(False)
        self._apply_filter()
        self.select(current)

    def _apply_filter(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            row = self._rows[int(item.data(Qt.ItemDataRole.UserRole))]
            hidden = (self._filter == ABNORMAL and row.band not in ("bad", "warn")) or (
                self._filter == PENDING and row.kind != "pending"
            )
            item.setHidden(hidden)

    def visible_rows(self) -> list[int]:
        """Indexy karet viditelných řádků v pořadí seznamu (šipky v detailu)."""
        return [
            int(self.list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.list.count())
            if not self.list.item(i).isHidden()
        ]

    def _item_for(self, index: int) -> QListWidgetItem | None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if int(item.data(Qt.ItemDataRole.UserRole)) == index:
                return item
        return None

    def current_index(self) -> int:
        item = self.list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else -1

    def select(self, index: int) -> None:
        item = self._item_for(index)
        if item is not None:
            self.list.setCurrentItem(item)

    def _open_item(self, item: QListWidgetItem) -> None:
        self.open_requested.emit(int(item.data(Qt.ItemDataRole.UserRole)))


def _desc(iso: str) -> str:
    """Klíč pro sestupné řazení ISO data v jinak vzestupném `sorted` (prázdné na konec)."""
    return "".join(chr(0x10FFFF - ord(c)) for c in iso) if iso else "\U0010ffff"


__all__ = ["ABNORMAL", "ALL", "PENDING", "OverviewRow", "PatientOverview"]
