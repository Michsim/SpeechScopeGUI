"""Formulář parametrů generovaný z deklarace `Param` knihovny.

Typ určí widget, meze `gt/ge/lt/le` jdou do spinboxu, `choices` do
comboboxu. Každý parametr má tři řádky: jméno (tučně a s tečkou, když je
změněný, vpravo ↺ na návrat k výchozí), samotné pole přes celou šířku
a popis z knihovny s výchozí hodnotou. Vrací jen hodnoty, které se liší
od výchozích, takže YAML protokolu zůstává krátký a čitelný.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...backend.library import ParamInfo
from ...i18n import tr
from .. import theme

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


def format_default(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, bool):
        return tr("ano") if value else tr("ne")
    if value == "":
        return tr("prázdné")
    return str(value)


class _Row(QWidget):
    """Jeden parametr: jméno, pole, popis."""

    def __init__(self, info: ParamInfo, widget: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.widget = widget
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.mark = QLabel("●")
        self.mark.setStyleSheet(f"color: {theme.ACCENT};")
        self.mark.setFixedWidth(12)
        self.name = QLabel(info.name)
        self.name.setToolTip(info.description)
        self.reset_btn = QToolButton()
        self.reset_btn.setText("↺")
        self.reset_btn.setAutoRaise(True)
        self.reset_btn.setToolTip(
            tr("Vrátit na výchozí: {value}").format(value=format_default(info.default))
        )
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        head.addWidget(self.mark)
        head.addWidget(self.name, 1)
        head.addWidget(self.reset_btn)
        layout.addLayout(head)
        layout.addWidget(widget)
        desc = info.description.strip().rstrip(".")
        text = f"{desc} · " if desc else ""
        text += (
            f"<span style='color:{theme.NEUTRAL}'>"
            + tr("výchozí: {value}").format(value=format_default(info.default))
            + "</span>"
        )
        self.desc = QLabel(text)
        self.desc.setObjectName("muted")
        self.desc.setWordWrap(True)
        self.desc.setTextFormat(Qt.TextFormat.RichText)
        self.desc.setStyleSheet("font-size: 8.5pt;")
        layout.addWidget(self.desc)
        self.set_changed(False)

    def set_changed(self, changed: bool) -> None:
        self.mark.setVisible(changed)
        self.reset_btn.setVisible(changed)
        font = self.name.font()
        font.setBold(changed)
        self.name.setFont(font)


class ParamForm(QWidget):
    changed = Signal()

    def __init__(self, params: list[ParamInfo], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._params = {p.name: p for p in params}
        self._widgets: dict[str, QWidget] = {}
        self._rows: dict[str, _Row] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(0)
        if not params:
            empty = QLabel(tr("Bez parametrů."))
            empty.setObjectName("muted")
            layout.addWidget(empty)
        for i, p in enumerate(params):
            w = self._make_widget(p)
            w.setToolTip(p.description)
            row = _Row(p, w)
            row.reset_btn.clicked.connect(lambda _=False, n=p.name: self.reset_param(n))
            if i:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setStyleSheet(f"color: {theme.BORDER};")
                layout.addWidget(line)
            layout.addWidget(row)
            self._widgets[p.name] = w
            self._rows[p.name] = row
        layout.addStretch(1)
        self.changed.connect(self._refresh_marks)

    def _make_widget(self, p: ParamInfo) -> QWidget:
        if p.choices:
            combo = QComboBox()
            for choice in p.choices:
                combo.addItem(str(choice), choice)
            combo.setCurrentIndex(max(0, combo.findData(p.default)))
            combo.currentIndexChanged.connect(self.changed)
            return combo
        if p.type == "bool":
            box = QCheckBox(tr("zapnuto"))
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
        edit.setPlaceholderText(tr("prázdné") if p.default in ("", None) else "")
        edit.textChanged.connect(self.changed)
        return edit

    # --- hodnoty ------------------------------------------------------------------

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
        self._refresh_marks()

    def reset_param(self, name: str) -> None:
        p = self._params[name]
        self.set_value(name, p.default if p.default is not None else "")

    def reset(self) -> None:
        """Vrátí všechno na výchozí hodnoty z knihovny."""
        for name in self._params:
            self.reset_param(name)
        self.changed.emit()

    def is_changed(self, name: str) -> bool:
        return self.value(name) != self._params[name].default

    def overrides(self) -> dict[str, Any]:
        """Jen hodnoty odlišné od výchozích."""
        return {name: self.value(name) for name in self._params if self.is_changed(name)}

    def _refresh_marks(self) -> None:
        for name, row in self._rows.items():
            row.set_changed(self.is_changed(name))
