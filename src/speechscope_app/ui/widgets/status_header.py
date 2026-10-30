"""Stavová karta v hlavičce stránky (Výpočet, Výsledky).

Barevný proužek podle stavu, velký nadpis, šedý podtitul, řádek štítků,
tlačítka vpravo nahoře a místo pro další obsah (průběh) pod tím.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import theme
from .elided_label import ElidedLabel
from .flow_layout import FlowLayout, flow_policy


class Pill(QLabel):
    """Štítek; s `on_click` se chová jako přepínač (filtr na Výsledcích)."""

    def __init__(self, text: str, role: str, on_click: Callable[[], None] | None = None) -> None:
        super().__init__(text)
        self.setObjectName("pill")
        theme.set_role(self, role)
        self._on_click = on_click
        if on_click is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._on_click is not None:
            self._on_click()
        super().mousePressEvent(event)


class StatusHeader(QFrame):
    """`subtitle_below=True` (Pacienti, 31. 10. 2026): podtitul na vlastním řádku pod
    nadpisem i tlačítky, přes celou šířku a na jeden řádek s výpustkou; vedle tlačítek
    se v úzkém okně lámal do tří řádků a hlavička rostla."""

    def __init__(self, parent: QWidget | None = None, *, subtitle_below: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        theme.set_role(self, "neutral")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel("")
        self.title.setObjectName("headline")
        self.title.setWordWrap(True)
        if subtitle_below:
            self.subtitle: QLabel = ElidedLabel("")
        else:
            self.subtitle = QLabel("")
            self.subtitle.setWordWrap(True)
        self.subtitle.setObjectName("muted")
        text.addWidget(self.title)
        if not subtitle_below:
            text.addWidget(self.subtitle)
        top.addLayout(text, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        top.addLayout(self.buttons, 0)
        top.setAlignment(
            self.buttons,
            Qt.AlignmentFlag.AlignVCenter if subtitle_below else Qt.AlignmentFlag.AlignTop,
        )
        outer.addLayout(top)
        if subtitle_below:
            outer.addWidget(self.subtitle)
        # štítky pod titulkem přes celou šířku karty a zalamované: sedm dimenzí
        # v jedné řadě vedle tlačítek tlačilo minimální šířku okna přes obrazovku
        self.pills = FlowLayout(spacing=6)
        outer.addLayout(self.pills)
        self.body = QVBoxLayout()
        self.body.setSpacing(4)
        outer.addLayout(self.body)
        self._pill_widgets: list[Pill] = []

    def set_role(self, role: str) -> None:
        theme.set_role(self, role)

    def add_button(self, widget: QWidget) -> None:
        self.buttons.addWidget(widget)

    def add_body(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

    def set_pills(self, pills: list[tuple[str, str, str, Callable[[], None] | None]]) -> None:
        """Štítky (text, role, tooltip, akce) místo dosavadních."""
        for old in self._pill_widgets:
            self.pills.removeWidget(old)
            old.hide()  # jinak starý štítek zůstane vykreslený, dokud ho Qt nesmaže
            old.setParent(None)
            old.deleteLater()
        self._pill_widgets = []
        for text, role, tooltip, action in pills:
            pill = Pill(text, role, action)
            pill.setToolTip(tooltip)
            flow_policy(pill)
            self.pills.addWidget(pill)
            self._pill_widgets.append(pill)
