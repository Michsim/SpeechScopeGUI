"""Dialog Prahy kontroly kvality: žlutý a červený práh a vypínač pro každou kontrolu.

Prahy jsou společné pro celou aplikaci (rozhodnuto 31. 10. 2026). Uloží se jen
odchylky od přibalených (`quality.save_checks` do `settings.quality_file()`),
Obnovit výchozí soubor smaže. Nic se nepřepočítává: prahy se použijí až při
zobrazení Výsledků, Pacientů a Norem. Mockup
https://claude.ai/artifact/ALTCFxkegtMF7zTpGrxb15 (artboard 3).
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..backend import quality, tasks
from ..i18n import tr
from . import theme
from .widgets.clean_table import card, cell, fit_widget_column, style_table
from .widgets.inputs import NumberField, ToggleSwitch, format_value

FIELD_WIDTH = 64
ALL_TASKS = ("phonation", "ddk", "reading", "spontaneous")


def _group_label(task_set: frozenset[str]) -> str:
    if set(ALL_TASKS) <= task_set:
        return tr("Všechny úlohy")
    return ", ".join(tasks.long_label(t) for t in sorted(task_set, key=tasks.task_order))


def _unit(check: quality.Check) -> str:
    return check.unit or {"ddk_triplets": tr("trojic"), "spontaneous_words": tr("slov")}.get(
        check.key, ""
    )


class QualityThresholdsDialog(QDialog):
    COL_ON, COL_NAME, COL_WARN, COL_BAD, COL_NOTE = range(5)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Prahy kontroly kvality"))
        self.resize(860, 800)
        self._defaults = {c.key: c for c in quality.default_checks()}
        self._checks = list(quality.load_checks())
        self._rows: dict[str, int] = {}  # klíč kontroly -> řádek tabulky
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        hint = QLabel(
            tr(
                "Nahrávka pod žlutým prahem je podezřelá, pod červeným nepoužitelná; u přebuzení "
                "naopak nad prahem. Vypnutá kontrola se nehlásí. Prahy platí pro celou "
                "aplikaci a nic se nepřepočítává."
            )
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        # uhlazená tabulka (podoba A) s poli podoby B: bez šipek, jednotka uvnitř;
        # „pod / nad“ je v záhlaví a u přebuzení u názvu, ne v každém poli
        self.table = QTableWidget(0, 5)
        style_table(
            self.table,
            [tr("zapnuto"), tr("kontrola"), tr("podezřelá pod"), tr("nepoužitelná pod"), ""],
            row_height=NumberField().height() + 10,
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(True)  # „výchozí 5 ↺“ dostane zbytek šířky
        layout.addWidget(card(self.table), 1)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        self.reset_btn = QPushButton(tr("Obnovit výchozí"))
        self.reset_btn.clicked.connect(lambda: self.reset_defaults())
        bottom.addWidget(self.reset_btn)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("Uložit"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Storno"))
        theme.set_role(buttons.button(QDialogButtonBox.StandardButton.Save), "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)
        self._fill()

    # --- tabulka --------------------------------------------------------------

    def _fill(self) -> None:
        self.table.setRowCount(0)
        self._rows.clear()
        last_group = None
        for check in self._checks:
            group = _group_label(check.tasks)
            if group != last_group:
                row = self.table.rowCount()
                self.table.insertRow(row)
                item = QTableWidgetItem(group.upper())
                font = item.font()
                font.setBold(True)
                font.setPointSizeF(font.pointSizeF() * 0.9)
                item.setFont(font)
                item.setForeground(QColor(theme.MUTED))
                item.setBackground(QColor(theme.NEUTRAL_SOFT))
                self.table.setItem(row, 0, item)
                self.table.setSpan(row, 0, 1, 5)
                self.table.setRowHeight(row, self.table.fontMetrics().height() + 14)
                last_group = group
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._rows[check.key] = row
            toggle = ToggleSwitch(check.enabled)
            toggle.setToolTip(tr("Kontrolu hlásit"))
            toggle.toggled.connect(lambda _=False, k=check.key: self._changed(k))
            self.table.setCellWidget(row, self.COL_ON, cell(toggle, transparent=False))
            self.table.setItem(row, self.COL_NAME, QTableWidgetItem(""))
            name = QLabel(check.display_name)
            name.setObjectName("check_name")
            font = QFont(name.font())
            font.setBold(True)
            name.setFont(font)
            parts: list[QWidget] = [name]
            if check.kind == "max":  # u přebuzení vadí hodnota nad prahem
                over = QLabel(tr("nad prahem"))
                over.setObjectName("muted")
                parts.append(over)
            self.table.setCellWidget(row, self.COL_NAME, cell(*parts))
            unit = _unit(check)
            for col, value in ((self.COL_WARN, check.warn), (self.COL_BAD, check.bad)):
                field = NumberField(
                    value,
                    unit=unit,
                    minimum=0,
                    maximum=1e6,
                    step=0.1 if check.kind == "max" else 1,
                    width=FIELD_WIDTH,
                )
                field.valueChanged.connect(lambda _v=0.0, k=check.key: self._changed(k))
                self.table.setCellWidget(row, col, cell(field, transparent=False))
            default = QLabel("")
            default.setObjectName("muted")
            reset = QToolButton()
            reset.setText("↺")
            reset.setAutoRaise(True)
            reset.setCursor(Qt.CursorShape.PointingHandCursor)
            reset.setToolTip(tr("Vrátit výchozí"))
            reset.clicked.connect(lambda _=False, k=check.key: self.reset_check(k))
            self.table.setCellWidget(row, self.COL_NOTE, cell(default, reset, transparent=False))
            self._changed(check.key)
        for col in (self.COL_ON, self.COL_NAME, self.COL_WARN, self.COL_BAD):
            fit_widget_column(self.table, col, extra=16)
        self._update_status()

    def _spin(self, key: str, col: int) -> NumberField:
        return self.table.cellWidget(self._rows[key], col).findChild(NumberField)

    def _box(self, key: str) -> ToggleSwitch:
        return self.table.cellWidget(self._rows[key], self.COL_ON).findChild(ToggleSwitch)

    def _note(self, key: str) -> tuple[QLabel, QToolButton]:
        holder = self.table.cellWidget(self._rows[key], self.COL_NOTE)
        return holder.findChild(QLabel), holder.findChild(QToolButton)

    def note_text(self, key: str) -> str:
        """Pro testy: text vedle polí („výchozí 5“)."""
        return self._note(key)[0].text()

    def reset_check(self, key: str) -> None:
        """↺ u řádku: přibalené prahy a zapnutí jedné kontroly."""
        base = self._defaults.get(key)
        if base is None:
            return
        self.set_values(key, warn=base.warn, bad=base.bad, enabled=base.enabled)

    def set_values(self, key: str, *, warn: float | None = None, bad: float | None = None,
                   enabled: bool | None = None) -> None:  # fmt: skip
        """Pro testy: nastaví práh nebo vypínač kontroly `key` jako uživatel."""
        if warn is not None:
            self._spin(key, self.COL_WARN).setValue(warn)
        if bad is not None:
            self._spin(key, self.COL_BAD).setValue(bad)
        if enabled is not None:
            self._box(key).setChecked(enabled)

    def current_checks(self) -> list[quality.Check]:
        out = []
        for check in self._checks:
            out.append(
                replace(
                    check,
                    warn=self._spin(check.key, self.COL_WARN).value(),
                    bad=self._spin(check.key, self.COL_BAD).value(),
                    enabled=self._box(check.key).isChecked(),
                )
            )
        return out

    def _changed(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None or self.table.cellWidget(row, self.COL_BAD) is None:
            return
        if self.table.cellWidget(row, self.COL_NOTE) is None:
            return  # řádek se teprve plní
        base = self._defaults.get(key)
        enabled = self._box(key).isChecked()
        defaults = []
        any_change = base is not None and enabled != base.enabled
        for col, attr in ((self.COL_WARN, "warn"), (self.COL_BAD, "bad")):
            field = self._spin(key, col)
            field.setEnabled(enabled)
            differs = base is not None and field.value() != getattr(base, attr)
            field.set_changed(differs)
            any_change = any_change or differs
            if differs:
                defaults.append(format_value(getattr(base, attr)))
        name = self.table.cellWidget(row, self.COL_NAME).findChild(QLabel, "check_name")
        if name is not None:
            name.setStyleSheet(f"color: {theme.TEXT if enabled else theme.MUTED};")
        label, reset = self._note(key)
        label.setText(tr("výchozí {value}").format(value=" / ".join(defaults)) if defaults else "")
        label.setMinimumWidth(label.sizeHint().width())  # jinak zůstane šířka prázdného textu
        reset.setVisible(any_change)
        self._update_status()

    def changes(self) -> int:
        """Kolik kontrol se liší od přibalených."""
        n = 0
        for check in self.current_checks():
            base = self._defaults.get(check.key)
            if base is not None and (check.warn, check.bad, check.enabled) != (
                base.warn,
                base.bad,
                base.enabled,
            ):
                n += 1
        return n

    def _update_status(self) -> None:
        if len(self._rows) < len(self._checks):
            return  # tabulka se teprve plní
        n = self.changes()
        self.status.setText(tr("{n} změn proti výchozím").format(n=n) if n else tr("výchozí prahy"))
        self.reset_btn.setEnabled(bool(n))

    # --- akce -----------------------------------------------------------------

    def reset_defaults(self) -> None:
        self._checks = list(quality.default_checks())
        self._fill()

    def problems(self) -> list[str]:
        return [p for p in (quality.validate(c) for c in self.current_checks()) if p]

    def accept(self) -> None:
        problems = self.problems()
        if problems:
            QMessageBox.warning(self, tr("Prahy kontroly kvality"), "\n".join(problems))
            return
        quality.save_checks(self.current_checks())
        super().accept()
