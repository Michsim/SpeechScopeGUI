"""Formulář parametrů generovaný z deklarace `Param` knihovny.

Typ určí widget, meze `gt/ge/lt/le` jdou do spinboxu, `choices` do
comboboxu. Vrací jen hodnoty, které se liší od výchozích, takže YAML
protokolu zůstává krátký a čitelný.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from ...backend.library import ParamInfo

_BIG = 1e9


def _bounds(info: ParamInfo, *, integer: bool) -> tuple[float, float]:
    lo, hi = -_BIG, _BIG
    b = info.bounds
    step = 1 if integer else 1e-6
    if "ge" in b:
        lo = b["ge"]
    elif "gt" in b:
        lo = b["gt"] + step
    if "le" in b:
        hi = b["le"]
    elif "lt" in b:
        hi = b["lt"] - step
    return lo, hi


class ParamForm(QWidget):
    changed = Signal()

    def __init__(self, params: list[ParamInfo], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._params = {p.name: p for p in params}
        self._widgets: dict[str, QWidget] = {}
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if not params:
            layout.addRow(QLabel("Bez parametrů."))
        for p in params:
            w = self._make_widget(p)
            w.setToolTip(p.description)
            label = QLabel(p.name)
            label.setToolTip(p.description)
            layout.addRow(label, w)
            self._widgets[p.name] = w

    def _make_widget(self, p: ParamInfo) -> QWidget:
        if p.choices:
            combo = QComboBox()
            for choice in p.choices:
                combo.addItem(str(choice), choice)
            combo.setCurrentIndex(max(0, combo.findData(p.default)))
            combo.currentIndexChanged.connect(self.changed)
            return combo
        if p.type == "bool":
            box = QCheckBox()
            box.setChecked(bool(p.default))
            box.toggled.connect(self.changed)
            return box
        if p.type == "int":
            spin = QSpinBox()
            lo, hi = _bounds(p, integer=True)
            spin.setRange(int(max(lo, -2_000_000_000)), int(min(hi, 2_000_000_000)))
            spin.setValue(int(p.default or 0))
            spin.valueChanged.connect(self.changed)
            return spin
        if p.type == "float":
            spin = QDoubleSpinBox()
            lo, hi = _bounds(p, integer=False)
            spin.setDecimals(4)
            spin.setRange(lo, hi)
            spin.setSingleStep(0.01)
            spin.setValue(float(p.default or 0.0))
            spin.valueChanged.connect(self.changed)
            return spin
        edit = QLineEdit(str(p.default if p.default is not None else ""))
        edit.textChanged.connect(self.changed)
        return edit

    def value(self, name: str) -> Any:
        w = self._widgets[name]
        if isinstance(w, QComboBox):
            return w.currentData()
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QSpinBox | QDoubleSpinBox):
            return w.value()
        assert isinstance(w, QLineEdit)
        return w.text()

    def set_value(self, name: str, value: Any) -> None:
        w = self._widgets[name]
        if isinstance(w, QComboBox):
            w.setCurrentIndex(max(0, w.findData(value)))
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, QSpinBox):
            w.setValue(int(value))
        elif isinstance(w, QDoubleSpinBox):
            w.setValue(float(value))
        elif isinstance(w, QLineEdit):
            w.setText(str(value))

    def set_values(self, values: dict[str, Any]) -> None:
        for name, value in values.items():
            if name in self._widgets:
                self.set_value(name, value)

    def reset(self) -> None:
        """Vrátí všechno na výchozí hodnoty z knihovny."""
        for name, p in self._params.items():
            self.set_value(name, p.default if p.default is not None else "")
        self.changed.emit()

    def overrides(self) -> dict[str, Any]:
        """Jen hodnoty odlišné od výchozích."""
        out: dict[str, Any] = {}
        for name, p in self._params.items():
            value = self.value(name)
            if value != p.default:
                out[name] = value
        return out
