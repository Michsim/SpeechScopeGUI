"""Pavoukový graf odchylek od normy po dimenzích a pruh skóre do tabulky.

Osy jsou dimenze se skóre (z-skóre otočené k patologii), kružnice na 1 a 2
směrodatných odchylkách, pásma pozadí v barvách stavů (ok, warn, missing),
bod na ose barvou pásma a popisek s názvem a „+2,3 SD“. Skóre pod nulou
(lepší než norma) se kreslí na středu, číslo zůstává se znaménkem. Jedna
dimenze = jeden vodorovný pruh, pavouk s jednou osou nedává smysl.
Kreslí se přes QPainter, aby `grab()` dal obrázek do zprávy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme

MAX_SD = 3.0  # okraj grafu

BAND_COLORS: dict[str, str] = {"ok": theme.OK, "warn": theme.WARN, "bad": theme.MISSING}
BAND_SOFT: dict[str, str] = {
    "ok": theme.OK_SOFT,
    "warn": theme.WARN_SOFT,
    "bad": theme.MISSING_SOFT,
}


def format_sd(score: float) -> str:
    """„+2,3 SD“ s českou čárkou; nula bez znaménka."""
    text = f"{score:+.1f}".replace(".", ",")
    if text in ("+0,0", "-0,0"):
        text = "0,0"
    return f"{text} SD"


@dataclass(slots=True)
class Axis:
    label: str
    score: float
    band: str  # ok | warn | bad


class SpiderChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._axes: list[Axis] = []
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_axes(self, axes: list[Axis]) -> None:
        self._axes = list(axes)
        self.update()

    def axes(self) -> list[Axis]:
        return list(self._axes)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.CARD))
        if not self._axes:
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "")
            return
        if len(self._axes) == 1:
            self._paint_bar(painter)
        else:
            self._paint_spider(painter)
        painter.end()

    # --- pavouk ---------------------------------------------------------------------

    def _paint_spider(self, painter: QPainter) -> None:
        rect = self.rect()
        label_font = QFont(self.font())
        label_font.setBold(True)
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        painter.setFont(label_font)
        metrics = painter.fontMetrics()
        metrics_h = metrics.height()
        # místo na popisky: vodorovně nejdelší název, svisle dva řádky (název a SD)
        label_w = max(metrics.horizontalAdvance(a.label) for a in self._axes)
        width = float(label_w + 8)
        radius = max(
            20.0,
            min(rect.width() / 2 - label_w - 22, rect.height() / 2 - 2 * metrics_h - 18),
        )
        center = QPointF(rect.center())
        n = len(self._axes)
        step = 2 * math.pi / n

        def point(i: int, value: float) -> QPointF:
            angle = -math.pi / 2 + i * step
            r = radius * min(max(value, 0.0), MAX_SD) / MAX_SD
            return QPointF(center.x() + r * math.cos(angle), center.y() + r * math.sin(angle))

        # pásma pozadí: od okraje do středu, aby vnitřní překryla vnější
        for limit, band in ((MAX_SD, "bad"), (2.0, "warn"), (1.0, "ok")):
            r = radius * limit / MAX_SD
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(BAND_SOFT[band]))
            painter.drawEllipse(center, r, r)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for limit, dashed in ((1.0, True), (2.0, True), (MAX_SD, False)):
            pen = QPen(QColor(theme.NEUTRAL if dashed else theme.BORDER), 1)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            r = radius * limit / MAX_SD
            painter.drawEllipse(center, r, r)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        for i in range(n):
            painter.drawLine(center, point(i, MAX_SD))
        polygon = QPolygonF([point(i, a.score) for i, a in enumerate(self._axes)])
        fill = QColor(theme.ACCENT)
        fill.setAlphaF(0.18)
        painter.setBrush(fill)
        painter.setPen(
            QPen(
                QColor(theme.ACCENT),
                2.5,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPolygon(polygon)
        for i, axis in enumerate(self._axes):
            color = QColor(BAND_COLORS.get(axis.band, theme.NEUTRAL))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(point(i, axis.score), 5, 5)

        # popisky kružnic mezi první a druhou osou, na podkladu, aby je čára nepřekryla
        painter.setFont(small)
        small_metrics = painter.fontMetrics()
        half = -math.pi / 2 + step / 2
        for limit, text in ((1.0, "1 SD"), (2.0, "2 SD")):
            r = radius * limit / MAX_SD
            at = QPointF(center.x() + r * math.cos(half), center.y() + r * math.sin(half))
            box = QRectF(small_metrics.boundingRect(text)).adjusted(-3, -1, 3, 1)
            box.moveCenter(at)
            back = QColor(theme.CARD)
            back.setAlphaF(0.85)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(back)
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

        # popisky za koncem osy, zarovnané podle strany
        for i, axis in enumerate(self._axes):
            angle = -math.pi / 2 + i * step
            tip = QPointF(
                center.x() + (radius + 10) * math.cos(angle),
                center.y() + (radius + 10) * math.sin(angle),
            )
            cos, sin = math.cos(angle), math.sin(angle)
            if cos > 0.3:
                x, align = tip.x(), Qt.AlignmentFlag.AlignLeft
            elif cos < -0.3:
                x, align = tip.x() - width, Qt.AlignmentFlag.AlignRight
            else:
                x, align = tip.x() - width / 2, Qt.AlignmentFlag.AlignHCenter
            y = (
                tip.y() - metrics_h
                if sin < -0.3
                else tip.y()
                if sin > 0.3
                else tip.y() - metrics_h / 2
            )
            box = QRectF(x, y - metrics_h / 2, width, metrics_h)
            painter.setFont(label_font)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(box, int(align | Qt.AlignmentFlag.AlignVCenter), axis.label)
            painter.setFont(small)
            painter.setPen(QColor(BAND_COLORS.get(axis.band, theme.NEUTRAL)))
            painter.drawText(
                box.translated(0, metrics_h),
                int(align | Qt.AlignmentFlag.AlignVCenter),
                format_sd(axis.score),
            )

    # --- jedna dimenze: pruh ---------------------------------------------------------

    def _paint_bar(self, painter: QPainter) -> None:
        axis = self._axes[0]
        rect = self.rect().adjusted(24, 0, -24, 0)
        h = 22.0
        y = rect.center().y() - h / 2
        width = float(rect.width())
        metrics_h = painter.fontMetrics().height()
        for limit, band, start in ((1.0, "ok", 0.0), (2.0, "warn", 1.0), (MAX_SD, "bad", 2.0)):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(BAND_SOFT[band]))
            x0 = rect.left() + width * start / MAX_SD
            x1 = rect.left() + width * limit / MAX_SD
            painter.drawRect(QRectF(x0, y, x1 - x0, h))
        painter.setPen(QPen(QColor(theme.NEUTRAL), 1, Qt.PenStyle.DashLine))
        painter.setFont(self.font())
        for limit in (1.0, 2.0):
            x = rect.left() + width * limit / MAX_SD
            painter.drawLine(QPointF(x, y - 6), QPointF(x, y + h + 6))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(QPointF(x + 4, y + h + metrics_h + 4), f"{int(limit)} SD")
            painter.setPen(QPen(QColor(theme.NEUTRAL), 1, Qt.PenStyle.DashLine))
        value = min(max(axis.score, 0.0), MAX_SD)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BAND_COLORS.get(axis.band, theme.NEUTRAL)))
        painter.drawRect(QRectF(rect.left(), y + 6, width * value / MAX_SD, h - 12))
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRectF(rect.left(), y - metrics_h - 8, width, metrics_h),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"{axis.label}  {format_sd(axis.score)}",
        )


class ScoreBar(QWidget):
    """Pruh 0 až 3 SD barvou pásma do stromu položek."""

    def __init__(self, score: float | None, band: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.score = score
        self.band = band
        self.setMinimumWidth(60)
        self.setFixedHeight(18)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(4, 6, self.width() - 8, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(track, 3, 3)
        if self.score is None:
            return
        value = min(max(self.score, 0.0), MAX_SD) / MAX_SD
        painter.setBrush(QColor(BAND_COLORS.get(self.band, theme.NEUTRAL)))
        painter.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * value, 6), 3, 3)
        painter.end()
