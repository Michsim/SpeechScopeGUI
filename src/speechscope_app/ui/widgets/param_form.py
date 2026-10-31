"""Formulář parametrů generovaný z deklarace `Param` knihovny.

Podoba B (31. 10. 2026, mockup https://claude.ai/artifact/14g3AuV9rwngo5QNx1zC23):
parametr je jeden řádek seznamu, vlevo popis z knihovny (bez jednotky ze závorky)
a pod ním šedě technické jméno a výchozí hodnota, vpravo pole a ↺. Typ určí
vstup (`widgets/inputs.py`): číslo = `NumberField` s jednotkou a mezemi
`gt/ge/lt/le`, 2–3 možnosti = `Segmented`, víc možností = rozbalovací seznam,
ano/ne = `ToggleSwitch`, text = pole. Změněný parametr má tučný popis s modrou
tečkou a podbarvené pole. Vrací jen hodnoty, které se liší od výchozích, takže
YAML protokolu zůstává krátký a čitelný.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...backend.library import ParamInfo
from ...i18n import tr
from .. import theme
from ..feature_detail import split_unit
from .elided_label import ElidedLabel
from .inputs import NumberField, Segmented, ToggleSwitch, format_value

SEGMENT_MAX = 3  # do tolika možností přepínač, nad to rozbalovací seznam

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
    if isinstance(value, float):
        return format_value(value)
    if value == "":
        return tr("prázdné")
    return str(value)


def split_title(description: str) -> tuple[str, str]:
    """Popis z knihovny → (titulek, zbytek): titulek do první dvojtečky nebo středníku.

    „Reference půltónů: fmin (…), fixed (…)“ → („Reference půltónů“, „fmin (…), …“);
    krátký popis bez dvojtečky je celý titulek.
    """
    text = description.strip().rstrip(".")
    for sep in (":", ";"):
        head, found, tail = text.partition(sep)
        if found and len(head.strip()) >= 8:
            return head.strip(), tail.strip()
    return text, ""


class _Row(QWidget):
    """Jeden parametr jako řádek seznamu: popis vlevo, pole a ↺ vpravo."""

    def __init__(self, info: ParamInfo, widget: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.widget = widget
        text, _unit = split_unit(info.description)
        title, rest = split_title(text)
        layout = QGridLayout(self)
        layout.setContentsMargins(4, 9, 0, 9)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(2)
        # titulek i druhý řádek na jeden řádek se zkrácením, ať řádek neroste dolů;
        # celý popis z knihovny je v tooltipu řádku
        self.name = ElidedLabel(title or info.name)
        layout.addWidget(self.name, 0, 0)
        self.desc = ElidedLabel(
            f"{info.name} · " + tr("výchozí {value}").format(value=format_default(info.default))
        )
        self.desc.setObjectName("muted")
        self.desc.setStyleSheet("font-size: 8.5pt;")
        layout.addWidget(self.desc, 1, 0)
        default = tr("výchozí {value}").format(value=format_default(info.default))
        # celý popis (i zbytek za dvojtečkou) je jen v tooltipu řádku
        description = info.description.strip()
        self.setToolTip(f"{description}\n{info.name} · {default}" if description else "")
        field = QHBoxLayout()
        field.setSpacing(4)
        field.addWidget(widget)
        self.reset_btn = QToolButton()
        self.reset_btn.setText("↺")
        self.reset_btn.setAutoRaise(True)
        self.reset_btn.setToolTip(
            tr("Vrátit na výchozí: {value}").format(value=format_default(info.default))
        )
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setFixedWidth(22)
        field.addWidget(self.reset_btn)
        layout.addLayout(
            field, 0, 1, 2, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.setColumnStretch(0, 1)
        self.set_changed(False)

    def set_changed(self, changed: bool) -> None:
        # ↺ jen u změněného; místo drží, ať pole neposkakují
        policy = self.reset_btn.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.reset_btn.setSizePolicy(policy)
        self.reset_btn.setVisible(changed)
        font = self.name.font()
        font.setBold(changed)
        self.name.setFont(font)
        self.name.setStyleSheet(f"color: {theme.ACCENT_HOVER};" if changed else "")
        if isinstance(self.widget, NumberField):
            self.widget.set_changed(changed)


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
        if p.choices and len(p.choices) <= SEGMENT_MAX:
            seg = Segmented(list(p.choices), p.default)
            seg.currentChanged.connect(lambda _v: self.changed.emit())
            return seg
        if p.choices:
            combo = QComboBox()
            for choice in p.choices:
                combo.addItem(str(choice), choice)
            combo.setCurrentIndex(max(0, combo.findData(p.default)))
            combo.currentIndexChanged.connect(self.changed)
            return combo
        if p.type == "bool":
            switch = ToggleSwitch(bool(p.default))
            switch.toggled.connect(self.changed)
            return switch
        if p.type in ("int", "float"):
            integer = p.type == "int"
            lo, hi = _bounds(p, integer=integer)
            _text, unit = split_unit(p.description)
            field = NumberField(
                float(p.default or 0),
                unit=unit,
                integer=integer,
                minimum=lo,
                maximum=hi,
                width=80,
            )
            field.valueChanged.connect(lambda _v: self.changed.emit())
            return field
        edit = QLineEdit(str(p.default if p.default is not None else ""))
        edit.setPlaceholderText(tr("prázdné") if p.default in ("", None) else "")
        edit.setFixedWidth(220)
        edit.textChanged.connect(self.changed)
        return edit

    # --- hodnoty ------------------------------------------------------------------

    def value(self, name: str) -> Any:
        w = self._widgets[name]
        if isinstance(w, Segmented):
            return w.current()
        if isinstance(w, QComboBox):
            return w.currentData()
        if isinstance(w, ToggleSwitch):
            return w.isChecked()
        if isinstance(w, NumberField):
            return w.value()
        assert isinstance(w, QLineEdit)
        return w.text()

    def set_value(self, name: str, value: Any) -> None:
        w = self._widgets[name]
        if isinstance(w, Segmented):
            w.set_current(value)
        elif isinstance(w, QComboBox):
            w.setCurrentIndex(max(0, w.findData(value)))
        elif isinstance(w, ToggleSwitch):
            w.setChecked(bool(value))
        elif isinstance(w, NumberField):
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
