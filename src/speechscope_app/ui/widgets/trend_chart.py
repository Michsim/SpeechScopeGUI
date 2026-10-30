"""Malé grafy vývoje v čase: jedna dimenze = jeden graf, mřížka po třech (30. 10. 2026).

Stejná řeč jako pavouk, jen rozložená v čase: osa y v SD proti normě, patologie
nahoru, zelený pás ±1 SD, čárkovaně ±2 SD, silná čára norma, body barvou
pásma. Vyšetření s jinou verzí feature než norma má dutý bod. Osa x má
vyšetření rovnoměrně za sebou (ne podle odstupu dnů), aby bylo čitelných
i deset návštěv; data jsou pod body.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from .. import theme
from .spider_chart import BAND_COLORS, MAX_SD, SPAN, clamp_sd

COLUMNS = 3


@dataclass(slots=True)
class TrendSeries:
    title: str
    labels: list[str]  # popisky vyšetření pod body
    scores: list[float | None]
    bands: list[str]
    outdated: list[bool] = field(default_factory=list)
    headline: str = ""  # poslední hodnota a změna vpravo nahoře
    color: str = theme.TEXT  # barva názvu podle posledního pásma


class TrendChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.series: TrendSeries | None = None
        self.setFixedHeight(200)  # pevná výška: karta i mřížka ji pak hlásí nahoru
        self.setMinimumWidth(220)

    def set_series(self, series: TrendSeries) -> None:
        self.series = series
        tips = [
            f"{label}: {score:+.1f} SD".replace(".", ",") if score is not None else f"{label}: –"
            for label, score in zip(series.labels, series.scores, strict=False)
        ]
        self.setToolTip("\n".join(tips))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        series = self.series
        if series is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        base_font = painter.font()
        bold = QFont(base_font)
        bold.setBold(True)
        # titulek vlevo, souhrn vpravo; titulek dostane jen místo, které souhrn nechá
        painter.setFont(base_font)
        head_width = painter.fontMetrics().horizontalAdvance(series.headline) + 8
        painter.setPen(QColor(series.color))
        painter.drawText(
            QRectF(width - 4 - head_width, 2, head_width, 20),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            series.headline,
        )
        painter.setFont(bold)
        title_width = max(40.0, width - 8 - head_width - 6)
        title = painter.fontMetrics().elidedText(
            series.title, Qt.TextElideMode.ElideRight, int(title_width)
        )
        painter.drawText(
            QRectF(4, 2, title_width, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )
        painter.setFont(base_font)
        plot = QRectF(30, 28, width - 38, height - 28 - 26)
        if plot.width() <= 10 or plot.height() <= 10:
            return

        def y_of(score: float) -> float:
            return plot.top() + (MAX_SD - clamp_sd(score)) / SPAN * plot.height()

        # pásma: zelený prstenec ±1 SD, norma, ±2 SD čárkovaně
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.OK_SOFT))
        painter.drawRect(QRectF(plot.left(), y_of(1.0), plot.width(), y_of(-1.0) - y_of(1.0)))
        dashed = QPen(QColor(theme.NEUTRAL), 1, Qt.PenStyle.DashLine)
        painter.setPen(dashed)
        for level in (2.0, -2.0):
            painter.drawLine(QPointF(plot.left(), y_of(level)), QPointF(plot.right(), y_of(level)))
        painter.setPen(QPen(QColor(theme.TEXT), 2))
        painter.drawLine(QPointF(plot.left(), y_of(0.0)), QPointF(plot.right(), y_of(0.0)))
        small = QFont(base_font)
        small.setPointSizeF(max(7.0, base_font.pointSizeF() - 2))
        painter.setFont(small)
        painter.setPen(QColor(theme.MUTED))
        for level, text in ((2.0, "+2"), (0.0, "0"), (-2.0, "−2")):
            painter.drawText(
                QRectF(0, y_of(level) - 8, 26, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        # body a čára
        n = len(series.scores)
        if n == 0:
            painter.end()
            return
        pad = 18.0
        step = (plot.width() - 2 * pad) / (n - 1) if n > 1 else 0.0
        xs = [plot.left() + pad + i * step for i in range(n)]
        if n == 1:
            xs = [plot.center().x()]
        points = [
            (x, y_of(s), band, out)
            for x, s, band, out in zip(
                xs, series.scores, series.bands, series.outdated or [False] * n, strict=False
            )
            if s is not None
        ]
        painter.setPen(QPen(QColor(theme.TEXT), 1.5))
        for (x0, y0, _b0, _o0), (x1, y1, _b1, _o1) in zip(points, points[1:], strict=False):
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        for x, y, band, outdated in points:
            color = QColor(BAND_COLORS.get(band, theme.NEUTRAL))
            painter.setPen(QPen(color, 2))
            painter.setBrush(QColor(theme.CARD) if outdated else color)
            painter.drawEllipse(QPointF(x, y), 5, 5)
        painter.setPen(QColor(theme.MUTED))
        for x, label in zip(xs, series.labels, strict=False):
            painter.drawText(
                QRectF(x - 40, plot.bottom() + 4, 80, 18), Qt.AlignmentFlag.AlignHCenter, label
            )
        painter.end()


class TrendGrid(QWidget):
    """Mřížka karet s grafy, tři vedle sebe."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(12)
        self.charts: list[TrendChart] = []
        self._cards: list[QFrame] = []
        self.empty = QLabel("")
        self.empty.setObjectName("muted")
        self.empty.hide()

    def set_series(self, series: list[TrendSeries]) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.charts, self._cards = [], []
        if not series:
            self._grid.addWidget(self.empty, 0, 0)
            self.empty.show()
            return
        self.empty.hide()
        for i, one in enumerate(series):
            card = QFrame()
            card.setObjectName("card")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(8, 6, 8, 6)
            chart = TrendChart()
            chart.set_series(one)
            layout.addWidget(chart)
            self._grid.addWidget(card, i // COLUMNS, i % COLUMNS)
            self.charts.append(chart)
            self._cards.append(card)
        for col in range(COLUMNS):
            self._grid.setColumnStretch(col, 1)
        # mřížka v rolovací ploše: výšku si musí říct sama, jinak karty přetečou
        rows = (len(series) + COLUMNS - 1) // COLUMNS
        self.setMinimumHeight(rows * (200 + 12 + 12) + max(0, rows - 1) * 12)
        self.updateGeometry()


__all__ = ["TrendChart", "TrendGrid", "TrendSeries"]
