"""Dialog Kontroly a kvalita: nahrávky kontrol normy s kontrolou kvality a vyřazením.

Vyřazuje se nahrávka (kontrola × úloha), ne celá osoba, a nic samo (rozhodnuto
31. 10. 2026). Vyřazení se zapíše do `norm.yaml` (`NormInfo.excluded`), řádky
v `controls.csv` zůstávají, takže jde vrátit a nic se nepřepočítává. U přibalené
a importované normy (bez plánu) jen ke čtení. Mockup
https://claude.ai/artifact/ALTCFxkegtMF7zTpGrxb15 (artboard 2).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..backend import meta, quality, tasks
from ..backend.norms import ControlQuality, Excluded, Norm, control_quality
from ..i18n import tr
from . import theme
from .widgets.clean_table import card, cell, fit_widget_column, pill, style_table

LEVEL_TEXT = {quality.BAD: "nepoužitelná", quality.WARN: "podezřelá", "": "v pořádku"}
LEVEL_ROLES = {quality.BAD: "missing", quality.WARN: "warn", "": "ok"}


def _person(c: ControlQuality) -> str:
    parts = []
    if c.age is not None:
        parts.append(f"{c.age:.0f}")
    if c.sex in (meta.FEMALE, meta.MALE):
        parts.append(tr("žena") if c.sex == meta.FEMALE else tr("muž"))
    return " · ".join(parts)


class NormQualityDialog(QDialog):
    thresholds_requested = Signal()  # Prahy kvality… (otevírá hlavní okno)
    COL_USE, COL_SUBJECT, COL_TASK, COL_PERSON, COL_LEVEL, COL_REASON = range(6)

    def __init__(self, norm: Norm, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.norm = norm
        self.editable = norm.editable
        self.setWindowTitle(tr("Kontroly a kvalita · {name}").format(name=norm.info.display_name))
        self.resize(860, 560)
        self._reasons = {e.key: e.reason for e in norm.info.excluded}
        self._items: list[ControlQuality] = []
        self._use: dict[tuple[str, str], bool] = {}
        self._initial: dict[tuple[str, str], bool] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        hint = QLabel(
            tr(
                "Vyřazená nahrávka se při srovnání s normou nepoužije; řádek v normě zůstává "
                "a jde vrátit. Nic se nevyřazuje samo."
            )
            if self.editable
            else tr("Přibalenou a importovanou normu nejde měnit, kontrolu kvality jen ukazuje.")
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.only_problems = QPushButton()
        self.all_rows = QPushButton()
        group = QButtonGroup(self)
        for btn in (self.only_problems, self.all_rows):
            btn.setCheckable(True)
            group.addButton(btn)
            bar.addWidget(btn)
            btn.clicked.connect(lambda: self._fill())
        self.only_problems.setChecked(True)
        self.task_filter = QComboBox()
        self.task_filter.addItem(tr("všechny úlohy"), "")
        for task in sorted(norm.tasks, key=lambda t: (tasks.task_order(t), t)):
            self.task_filter.addItem(tasks.long_label(task), task)
        self.task_filter.currentIndexChanged.connect(lambda _i: self._fill())
        bar.addWidget(self.task_filter)
        bar.addStretch(1)
        self.exclude_bad_btn = QPushButton(tr("Vyřadit nepoužitelné"))
        self.exclude_bad_btn.setToolTip(tr("Odškrtne jen červené (nepoužitelné) nahrávky."))
        self.exclude_bad_btn.clicked.connect(lambda: self.exclude_bad())
        self.exclude_bad_btn.setVisible(self.editable)
        bar.addWidget(self.exclude_bad_btn)
        layout.addLayout(bar)

        # uhlazená tabulka jako na Výsledcích (podoba A): karta, záhlaví velkými, štítky
        self.table = QTableWidget(0, 6)
        style_table(
            self.table,
            [
                tr("použít"),
                tr("kontrola"),
                tr("úloha"),
                tr("věk · pohlaví"),
                tr("kvalita"),
                tr("důvod"),
            ],
        )
        layout.addWidget(card(self.table), 1)
        self.empty = QLabel("")
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        bottom.addWidget(self.status, 1)
        thresholds = QPushButton(tr("Prahy kvality…"))
        thresholds.clicked.connect(lambda: self._edit_thresholds())
        bottom.addWidget(thresholds)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
            if self.editable
            else QDialogButtonBox.StandardButton.Close
        )
        if self.editable:
            save = buttons.button(QDialogButtonBox.StandardButton.Save)
            save.setText(tr("Uložit"))
            theme.set_role(save, "primary")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Storno"))
        else:
            buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("Zavřít"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)
        self.reload()

    # --- data -----------------------------------------------------------------

    def reload(self) -> None:
        """Znovu posoudí kontroly (po změně prahů); rozdělané zaškrtnutí zůstává."""
        self._items = control_quality(self.norm)
        for c in self._items:
            self._initial.setdefault(c.key, not c.excluded)
            self._use.setdefault(c.key, not c.excluded)
        problems = sum(1 for c in self._items if c.level)
        self.only_problems.setText(tr("jen problémové {n}").format(n=problems))
        self.all_rows.setText(tr("všechny {n}").format(n=len(self._items)))
        self._fill()

    def _visible(self) -> list[ControlQuality]:
        task = self.task_filter.currentData() or ""
        out = []
        for c in self._items:
            if task and c.task != task:
                continue
            if self.only_problems.isChecked() and not c.level and self._use[c.key]:
                continue  # vyřazené zůstávají vidět, i když teď prošly
            out.append(c)
        return out

    def _fill(self) -> None:
        rows = self._visible()
        self.table.setRowCount(len(rows))
        for r, c in enumerate(rows):
            use = self._use[c.key]
            box = QCheckBox()
            box.setChecked(use)
            box.setEnabled(self.editable)
            box.setToolTip(tr("Použít nahrávku ve výběru kontrol"))
            box.toggled.connect(lambda on, k=c.key: self.set_use(k, on))
            self.table.setCellWidget(r, self.COL_USE, cell(box, transparent=False))
            reason = quality.summary(c.issues) or self._reasons.get(c.key, "")
            level_text = tr(LEVEL_TEXT[c.level])
            cells = [
                (self.COL_SUBJECT, c.subject),
                (self.COL_TASK, ""),  # štítek úlohy
                (self.COL_PERSON, _person(c)),
                (self.COL_LEVEL, ""),  # štítek kvality
                (self.COL_REASON, reason),
            ]
            for col, text in cells:
                item = QTableWidgetItem(text)
                if col == self.COL_SUBJECT:
                    font = QFont(item.font())
                    font.setBold(True)
                    font.setStrikeOut(not use)
                    item.setFont(font)
                    item.setForeground(QColor(theme.TEXT if use else theme.MUTED))
                elif col in (self.COL_PERSON, self.COL_REASON):
                    item.setForeground(QColor(theme.MUTED))
                if col == self.COL_LEVEL:
                    item.setData(Qt.ItemDataRole.UserRole, level_text)
                    item.setToolTip(reason)
                self.table.setItem(r, col, item)
            self.table.setCellWidget(r, self.COL_TASK, cell(pill(tasks.label(c.task), "neutral")))
            if use:
                badge = pill(level_text, LEVEL_ROLES[c.level])
            else:
                badge = pill(tr("vyřazená"), "neutral")
            self.table.setCellWidget(r, self.COL_LEVEL, cell(badge))
        for col in (self.COL_TASK, self.COL_LEVEL):
            fit_widget_column(self.table, col, extra=16)
        self.empty.setText(
            tr("Všechny nahrávky kontrol prošly kontrolou kvality.") if not rows else ""
        )
        self.empty.setVisible(not rows)
        self._update_status()

    def _update_status(self) -> None:
        excluded = sum(1 for on in self._use.values() if not on)
        changed = sum(1 for k, on in self._use.items() if self._initial.get(k) != on)
        parts = [tr("vyřazeno {n}").format(n=excluded)]
        if changed:
            parts.append(tr("změněno {n}").format(n=changed))
        self.status.setText(" · ".join(parts))

    # --- akce -----------------------------------------------------------------

    def set_use(self, key: tuple[str, str], use: bool) -> None:
        if not self.editable or self._use.get(key) == use:
            return
        self._use[key] = use
        self._fill()

    def exclude_bad(self) -> int:
        """Odškrtne nepoužitelné nahrávky; vrátí, kolik jich nově vyřadil."""
        n = 0
        for c in self._items:
            if c.level == quality.BAD and self._use[c.key]:
                self._use[c.key] = False
                n += 1
        self._fill()
        return n

    def excluded(self) -> list[Excluded]:
        """Vyřazení podle zaškrtnutí; důvod je kontrola kvality v době vyřazení."""
        by_key = {c.key: c for c in self._items}
        out = []
        for key, use in self._use.items():
            if use:
                continue
            c = by_key.get(key)
            reason = quality.summary(c.issues) if c is not None and c.issues else ""
            out.append(Excluded(key[0], key[1], reason or self._reasons.get(key, "")))
        out.sort(key=lambda e: (tasks.task_order(e.task), e.subject))
        return out

    def _edit_thresholds(self) -> None:
        self.thresholds_requested.emit()
        self.reload()
