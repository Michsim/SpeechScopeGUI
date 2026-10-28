"""Pavoukový graf odchylek od normy po dimenzích a pruh skóre do tabulky.

Osy jsou dimenze se skóre (z-skóre otočené k patologii). Graf je otočený
„čím větší, tím zdravější“ (rozhodnuto 28. 10. 2026, mockup varianta 2b):
okraj je −2 SD (lepší než kontroly), silná kružnice uprostřed je 0 SD
(průměr kontrol), střed +3 SD. Osa je lineární v SD, takže patologie má
uvnitř stejně místa jako norma vně; pásma pozadí od středu: mimo normu
(nad 2 SD), hraniční (1 až 2 SD), v normě (−1 až +1 SD) a světle modré
„lepší než kontroly“. Bod na ose má barvu pásma, popisek název a „+2,3 SD“,
číslo zůstává se znaménkem. Jedna dimenze = jeden vodorovný pruh s nulou
uprostřed (vlevo lepší, vpravo horší), pavouk s jednou osou nedává smysl.
Orientaci jde přepnout (`set_orientation`): `INWARD` je popsaná podoba
(střed horší), `OUTWARD` je původní směr (střed −2 SD, okraj +3 SD, patologie
trčí ven). Dimenze nad 1 SD jsou zvýrazněné v obou: popisek barvou pásma,
osa od nuly k bodu tlustší a výseč kolem osy vybarvená, aby zářez dovnitř
nezanikl. Kreslí se přes QPainter, aby `grab()` dal obrázek do zprávy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme

MAX_SD = 3.0  # střed pavouka a pravý okraj pruhu (nejhorší)
MIN_SD = -2.0  # okraj pavouka a levý okraj pruhu (lepší než kontroly)
SPAN = MAX_SD - MIN_SD
BETTER_SOFT = "#eff6ff"  # pásmo pod −1 SD: lepší než kontroly
INWARD = "inward"  # střed = horší, větší plocha = zdravější
OUTWARD = "outward"  # střed = lepší než kontroly, patologie ven
ORIENTATIONS = (INWARD, OUTWARD)

BAND_COLORS: dict[str, str] = {"ok": theme.OK, "warn": theme.WARN, "bad": theme.MISSING}
BAND_SOFT: dict[str, str] = {
    "ok": theme.OK_SOFT,
    "warn": theme.WARN_SOFT,
    "bad": theme.MISSING_SOFT,
}


def clamp_sd(score: float) -> float:
    return min(max(score, MIN_SD), MAX_SD)


def fraction(score: float) -> float:
    """Poloha skóre na ose 0 (MIN_SD, lepší) až 1 (MAX_SD, horší)."""
    return (clamp_sd(score) - MIN_SD) / SPAN


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
        self._orientation = INWARD
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_axes(self, axes: list[Axis]) -> None:
        self._axes = list(axes)
        self.update()

    def axes(self) -> list[Axis]:
        return list(self._axes)

    def orientation(self) -> str:
        return self._orientation

    def set_orientation(self, orientation: str) -> None:
        if orientation in ORIENTATIONS and orientation != self._orientation:
            self._orientation = orientation
            self.update()

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

        inward = self._orientation == INWARD

        def ring(value: float) -> float:
            """Poloměr kružnice skóre: dovnitř je okraj MIN_SD (lepší) a střed MAX_SD (horší),
            ven naopak."""
            f = fraction(value)
            return radius * ((1.0 - f) if inward else f)

        def point(i: int, value: float) -> QPointF:
            angle = -math.pi / 2 + i * step
            r = ring(value)
            return QPointF(center.x() + r * math.cos(angle), center.y() + r * math.sin(angle))

        # pásma pozadí: od okraje do středu, aby vnitřní překryla vnější
        bands = [
            (MIN_SD, BETTER_SOFT),
            (-1.0, BAND_SOFT["ok"]),
            (1.0, BAND_SOFT["warn"]),
            (2.0, BAND_SOFT["bad"]),
        ]
        if not inward:
            bands = [(MAX_SD, BAND_SOFT["bad"]), (2.0, BAND_SOFT["warn"])]
            bands += [(1.0, BAND_SOFT["ok"]), (-1.0, BETTER_SOFT)]
        for limit, color in bands:
            r = ring(limit)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(center, r, r)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # kružnice: norma plná a tmavá, ±1 a +2 SD čárkované, okraj jemný
        for limit, color, width_px, dashed in (
            (2.0, theme.NEUTRAL, 1.0, True),
            (1.0, theme.NEUTRAL, 1.0, True),
            (0.0, theme.TEXT, 1.5, False),
            (-1.0, theme.NEUTRAL, 1.0, True),
            (MIN_SD if inward else MAX_SD, theme.BORDER, 1.0, False),
        ):
            pen = QPen(QColor(color), width_px)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            r = ring(limit)
            painter.drawEllipse(center, r, r)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        edge = MIN_SD if inward else MAX_SD
        for i in range(n):
            painter.drawLine(center, point(i, edge))
        # dimenze nad 1 SD: výseč od nuly k bodu barvou pásma, ať zářez dovnitř nezanikne
        for i, axis in enumerate(self._axes):
            if axis.band not in ("warn", "bad"):
                continue
            wedge = QColor(BAND_COLORS[axis.band])
            wedge.setAlphaF(0.28)
            angle = -math.pi / 2 + i * step
            r0, r1 = sorted((ring(0.0), ring(axis.score)))
            spread = step / 2 * 0.8
            path = QPainterPath()
            arc = QRectF(center.x() - r1, center.y() - r1, 2 * r1, 2 * r1)
            inner = QRectF(center.x() - r0, center.y() - r0, 2 * r0, 2 * r0)
            start_deg = -math.degrees(angle - spread)
            sweep_deg = -math.degrees(2 * spread)
            path.arcMoveTo(arc, start_deg)
            path.arcTo(arc, start_deg, sweep_deg)
            path.arcTo(inner, start_deg + sweep_deg, -sweep_deg)
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(wedge)
            painter.drawPath(path)
            painter.setPen(QPen(QColor(BAND_COLORS[axis.band]), 3, Qt.PenStyle.SolidLine))
            painter.drawLine(point(i, 0.0), point(i, axis.score))
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
        for limit, text in ((2.0, "+2 SD"), (1.0, "+1 SD"), (0.0, "norma"), (-1.0, "−1 SD")):
            r = ring(limit)
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
            )  # popisky jsou za okrajem (MIN_SD), nezávisle na skóre
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
            # název dimenze barvou pásma, když je mimo normu nebo hraniční
            painter.setPen(
                QColor(BAND_COLORS[axis.band] if axis.band in ("warn", "bad") else theme.TEXT)
            )
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

        def x_of(value: float) -> float:
            return rect.left() + width * fraction(value)

        # pásma zleva: lepší než kontroly, v normě, hraniční, mimo normu
        for start, limit, color in (
            (MIN_SD, -1.0, BETTER_SOFT),
            (-1.0, 1.0, BAND_SOFT["ok"]),
            (1.0, 2.0, BAND_SOFT["warn"]),
            (2.0, MAX_SD, BAND_SOFT["bad"]),
        ):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRect(QRectF(x_of(start), y, x_of(limit) - x_of(start), h))
        painter.setFont(self.font())
        for limit, text in ((-1.0, "−1 SD"), (0.0, "norma"), (1.0, "+1 SD"), (2.0, "+2 SD")):
            x = x_of(limit)
            solid = limit == 0.0
            painter.setPen(
                QPen(QColor(theme.TEXT if solid else theme.NEUTRAL), 1.5 if solid else 1)
                if solid
                else QPen(QColor(theme.NEUTRAL), 1, Qt.PenStyle.DashLine)
            )
            painter.drawLine(QPointF(x, y - 6), QPointF(x, y + h + 6))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(QPointF(x + 4, y + h + metrics_h + 4), text)
        # pruh od nuly: doprava horší, doleva lepší
        value = clamp_sd(axis.score)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BAND_COLORS.get(axis.band, theme.NEUTRAL)))
        x0, x1 = sorted((x_of(0.0), x_of(value)))
        painter.drawRect(QRectF(x0, y + 6, max(2.0, x1 - x0), h - 12))
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
    """Pruh −2 až +3 SD barvou pásma do stromu položek: od nuly doprava horší, doleva lepší."""

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
        zero = track.left() + track.width() * fraction(0.0)
        painter.setBrush(QColor(theme.NEUTRAL))
        painter.drawRect(QRectF(zero - 0.5, 3, 1, 12))  # značka nuly (průměr kontrol)
        if self.score is None:
            return
        at = track.left() + track.width() * fraction(self.score)
        x0, x1 = sorted((zero, at))
        painter.setBrush(QColor(BAND_COLORS.get(self.band, theme.NEUTRAL)))
        painter.drawRoundedRect(QRectF(x0, track.top(), max(2.0, x1 - x0), 6), 3, 3)
        painter.end()
