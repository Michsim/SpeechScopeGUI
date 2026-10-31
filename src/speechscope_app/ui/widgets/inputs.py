"""Vstupy podoby B (mockup https://claude.ai/artifact/14g3AuV9rwngo5QNx1zC23).

`NumberField` = úzké číselné pole bez šipek, číslo vpravo, jednotka šedě uvnitř,
desetinná tečka (rozhodnuto 31. 10. 2026) a bez zbytečných nul; krokuje se
šipkami na klávesnici a kolečkem, změněná hodnota je podbarvená
(`set_changed`), hodnota mimo meze nebo nečitelná červeně a nepřijme se.
`ToggleSwitch` = vypínač ano/ne, `Segmented` = přepínač mezi 2–3 možnostmi.
Používají je Prahy kontroly kvality a parametry feature v editoru protokolu.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from .. import theme

CHANGED_BG = "#eff6ff"
CHANGED_BORDER = "#bfdbfe"
ERROR_BG = "#fff5f5"


def format_value(value: float, *, integer: bool = False) -> str:
    """Číslo bez zbytečných nul a s desetinnou tečkou: 5, 0.1, 0.0005."""
    if integer:
        return str(int(round(value)))
    if math.isfinite(value) and float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


def parse_value(text: str) -> float | None:
    """Číslo z pole; čárka se bere jako tečka, prázdné nebo nesmysl = None."""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def auto_step(value: float, *, integer: bool) -> float:
    """Krok šipkou: u celých čísel 1, u desetinných desetina řádu hodnoty."""
    if integer:
        return 1.0
    if value == 0:
        return 0.1
    return max(1e-6, 10.0 ** (math.floor(math.log10(abs(value))) - 1))


class NumberField(QFrame):
    """Číselné pole s jednotkou; `valueChanged` jen po platné změně."""

    valueChanged = Signal(float)  # noqa: N815  jako u QSpinBox

    def __init__(
        self,
        value: float = 0.0,
        *,
        unit: str = "",
        integer: bool = False,
        minimum: float = -1e12,
        maximum: float = 1e12,
        step: float | None = None,
        width: int = 104,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("number_field")
        self._integer = integer
        self._min = minimum
        self._max = maximum
        self._step = step
        self._value = self._clamp(float(value))
        self._changed = False
        self._error = False
        self._focus = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(5)
        self.edit = QLineEdit(format_value(self._value, integer=integer))
        self.edit.setObjectName("number_edit")
        self.edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.edit.setFrame(False)
        self.edit.textEdited.connect(self._edited)
        self.edit.editingFinished.connect(self._finished)
        self.edit.installEventFilter(self)
        layout.addWidget(self.edit, 1)
        self.unit = QLabel(unit)
        self.unit.setObjectName("number_unit")
        self.unit.setVisible(bool(unit))
        layout.addWidget(self.unit)
        self.setFixedWidth(width + (self.unit.sizeHint().width() + 5 if unit else 0))
        self.setFixedHeight(max(30, self.edit.sizeHint().height() + 6))
        self.setFocusProxy(self.edit)
        self._restyle()

    # --- hodnota --------------------------------------------------------------

    def value(self) -> float:
        return int(self._value) if self._integer else self._value  # type: ignore[return-value]

    def setValue(self, value: float) -> None:  # noqa: N802
        value = self._clamp(float(value))
        self.edit.setText(format_value(value, integer=self._integer))
        self._set_error(False)
        if value != self._value:
            self._value = value
            self.valueChanged.emit(value)

    def text(self) -> str:
        return self.edit.text()

    def set_text(self, text: str) -> None:
        """Pro testy: jako by uživatel napsal `text` a odešel z pole."""
        self.edit.setText(text)
        self._edited(text)
        self._finished()

    def set_changed(self, changed: bool) -> None:
        """Hodnota se liší od výchozí: modré podbarvení a tučné číslo."""
        if changed != self._changed:
            self._changed = changed
            self._restyle()

    def has_error(self) -> bool:
        return self._error

    def _clamp(self, value: float) -> float:
        value = min(self._max, max(self._min, value))
        return float(round(value)) if self._integer else value

    def _edited(self, text: str) -> None:
        value = parse_value(text)
        ok = value is not None and self._min <= value <= self._max
        if ok and self._integer and not float(value).is_integer():
            ok = False
        self._set_error(not ok)
        if ok and value != self._value:
            self._value = float(value)
            self.valueChanged.emit(self._value)

    def _finished(self) -> None:
        """Po opuštění pole: chybný text se vrátí na poslední platnou hodnotu."""
        self.edit.setText(format_value(self._value, integer=self._integer))
        self._set_error(False)

    def _set_error(self, error: bool) -> None:
        if error != self._error:
            self._error = error
            self._restyle()

    def step_by(self, steps: int) -> None:
        step = self._step or auto_step(self._value, integer=self._integer)
        value = self._value + steps * step
        if not self._integer:
            value = round(value, 10)
        self.setValue(value)

    # --- ovládání -------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802
        if obj is self.edit and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._focus = event.type() == QEvent.Type.FocusIn  # :focus-within Qt neumí
            self._restyle()
        if (
            obj is self.edit
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
        ):
            key = event.key()
            if key == Qt.Key.Key_Up:
                self.step_by(1)
                return True
            if key == Qt.Key.Key_Down:
                self.step_by(-1)
                return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self.edit.hasFocus():  # kolečko při rolování dialogu hodnotu nemění
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta:
            self.step_by(1 if delta > 0 else -1)
        event.accept()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange:
            self._restyle()
        super().changeEvent(event)

    def _restyle(self) -> None:
        if not self.isEnabled():
            bg, border = theme.NEUTRAL_SOFT, theme.BORDER
        elif self._error:
            bg, border = ERROR_BG, theme.MISSING
        elif self._changed:
            bg, border = CHANGED_BG, CHANGED_BORDER
        else:
            bg, border = theme.CARD, "#d1d5db"
        if self._focus and self.isEnabled() and not self._error:
            border = theme.ACCENT
        color = (
            theme.MUTED if not self.isEnabled() else ("#1d4ed8" if self._changed else theme.TEXT)
        )
        weight = 600 if self._changed and self.isEnabled() else 400
        self.setStyleSheet(
            f"QFrame#number_field {{ background: {bg}; border: 1px solid {border};"
            " border-radius: 7px; }"
            "QLineEdit#number_edit { background: transparent; border: none; padding: 0;"
            f" color: {color}; font-weight: {weight}; }}"
            f"QLabel#number_unit {{ color: {theme.MUTED}; background: transparent; }}"
        )


class ToggleSwitch(QAbstractButton):
    """Vypínač ano/ne (místo zaškrtávátka „zapnuto“)."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 20)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(36, 20)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        track = QColor(theme.ACCENT if on else "#d1d5db")
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 36, 20), 10, 10)
        p.setBrush(QColor("white"))
        x = 18 if on else 2
        p.drawEllipse(QRectF(x, 2, 16, 16))


class Segmented(QWidget):
    """Přepínač mezi několika možnostmi (2–3), vybraná modře."""

    currentChanged = Signal(object)  # noqa: N815  hodnota vybrané možnosti

    def __init__(self, choices: list[Any], current: Any = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._choices = list(choices)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        last = len(self._choices) - 1
        for i, choice in enumerate(self._choices):
            btn = QPushButton(str(choice))
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            left = "7px" if i == 0 else "0"
            right = "7px" if i == last else "0"
            btn.setStyleSheet(
                "QPushButton { border-radius: 0; padding: 3px 9px;"
                f" border-top-left-radius: {left}; border-bottom-left-radius: {left};"
                f" border-top-right-radius: {right}; border-bottom-right-radius: {right};"
                + (" margin-left: -1px; }" if i else " }")
            )
            self.group.addButton(btn, i)
            layout.addWidget(btn)
            self.buttons.append(btn)
        self.group.idClicked.connect(lambda i: self.currentChanged.emit(self._choices[i]))
        self.set_current(current if current in self._choices else self._choices[0])

    def current(self) -> Any:
        i = self.group.checkedId()
        return self._choices[i] if 0 <= i < len(self._choices) else None

    def set_current(self, value: Any) -> None:
        if value not in self._choices:
            return
        btn = self.buttons[self._choices.index(value)]
        if not btn.isChecked():
            btn.setChecked(True)
            self.currentChanged.emit(value)
