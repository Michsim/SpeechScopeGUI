"""Popisek, který se při nedostatku místa zkrátí trojtečkou místo tlačení na layout.

Obyčejný `QLabel` nesmí být užší než svůj text, takže delší nápověda
u kroku na Analýze roztáhne levý sloupec a pravý se posune. Tenhle
popisek má nulovou minimální šířku a kreslí text zkrácený na to, co se
vejde; celý text je v tooltipu.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self._sync_tooltip()

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self._sync_tooltip()

    def _sync_tooltip(self) -> None:
        metrics = self.fontMetrics()
        self.setToolTip(
            self.text() if metrics.horizontalAdvance(self.text()) > self.width() else ""
        )

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._sync_tooltip()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        rect = self.contentsRect()
        elided = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, rect.width()
        )
        painter.drawText(rect, int(self.alignment()) | Qt.TextFlag.TextSingleLine, elided)
