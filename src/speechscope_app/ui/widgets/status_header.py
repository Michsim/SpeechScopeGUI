"""Stavová karta v hlavičce stránky (Výpočet, Výsledky).

Barevný proužek podle stavu, velký nadpis, šedý podtitul, řádek štítků,
tlačítka vpravo nahoře a místo pro další obsah (průběh) pod tím.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import theme


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
    def __init__(self, parent: QWidget | None = None) -> None:
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
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        self.pills = QHBoxLayout()
        self.pills.setSpacing(6)
        self.pills.addStretch(1)
        text.addLayout(self.pills)
        top.addLayout(text, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        top.addLayout(self.buttons, 0)
        top.setAlignment(self.buttons, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(top)
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
            self.pills.insertWidget(self.pills.count() - 1, pill)
            self._pill_widgets.append(pill)
