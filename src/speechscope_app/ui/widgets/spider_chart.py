"""Pavoukový graf odchylek od normy po dimenzích a pruh skóre do tabulky.

Osy jsou dimenze se skóre (z-skóre otočené k patologii). Podoba podle
varianty I z 29. 10. 2026 (mockup `docs/mockups/pavouk-2/`, ostatní varianty
tam zůstávají pro diskusi s týmem): patologie trčí ven, střed je −2 SD
(lepší než kontroly), silná černá kružnice 0 SD (průměr kontrol), okraj +3 SD,
osa lineární v SD. Jediné vybarvené pásmo je zelený prstenec „v normě“ od
−1 do +1 SD se zelenými hranami a popisky, +2 SD je čárkovaná kružnice, zbytek
bílý. Čára pacienta je černá s jemnou šedou výplní, bod na ose má barvu pásma
a popisek (název a „+2,3 SD“ se znaménkem) má vždy barvu pásma, tedy i zelenou
v normě. Dimenze nad 1 SD mají navíc průsvitnou výseč barvou pásma od normy
k bodu. Jedna dimenze = jeden vodorovný pruh s nulou uprostřed (vlevo lepší,
vpravo horší), pavouk s jednou osou nedává smysl. Orientaci jde dál přepnout
(`set_orientation`): `OUTWARD` je popsaná podoba, `INWARD` (varianta 2b, střed
horší) zůstává v kódu, přepínač na stránce je skrytý. Kreslí se přes QPainter,
aby `grab()` dal obrázek do zprávy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...i18n import tr
from .. import theme

MAX_SD = 3.0  # střed pavouka a pravý okraj pruhu (nejhorší)
MIN_SD = -2.0  # okraj pavouka a levý okraj pruhu (lepší než kontroly)
SPAN = MAX_SD - MIN_SD
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


@dataclass(slots=True)
class Overlay:
    """Starší vyšetření v překryvu (Vývoj v čase, 31. 10. 2026): čára modře čárkovaně,
    dutý bod; `scores` po osách v pořadí `axes`, None = dimenze v tom vyšetření chybí."""

    label: str
    scores: list[float | None]
    color: str = "#1d4ed8"
    dash: tuple[float, ...] = (8.0, 4.0)
    marker: str = "square"  # square | circle
    fill_alpha: float = 0.0


class SpiderChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._axes: list[Axis] = []
        self._overlays: list[Overlay] = []
        self._orientation = OUTWARD
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_axes(self, axes: list[Axis], overlays: list[Overlay] | None = None) -> None:
        self._axes = list(axes)
        self._overlays = list(overlays or [])
        self.update()

    def overlays(self) -> list[Overlay]:
        return list(self._overlays)

    def axes(self) -> list[Axis]:
        return list(self._axes)

    def orientation(self) -> str:
        return self._orientation

    def content_rect(self) -> QRect:
        """Výřez s pavoukem a popisky pro export: widget bývá širší než kresba."""
        rect = self.rect()
        if len(self._axes) < 2:
            return rect
        font = QFont(self.font())
        font.setBold(True)
        metrics = QFontMetrics(font)
        label_w = max(metrics.horizontalAdvance(a.label) for a in self._axes)
        width = min(rect.width(), rect.height() + 2 * label_w + 24)
        return QRect(rect.center().x() - width // 2, rect.top(), width, rect.height())

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

        # jediné vybarvené pásmo je „v normě“ (−1 až +1 SD): zelený prstenec, zbytek bílý
        painter.setPen(Qt.PenStyle.NoPen)
        outer, inner = max(ring(1.0), ring(-1.0)), min(ring(1.0), ring(-1.0))
        painter.setBrush(QColor(BAND_SOFT["ok"]))
        painter.drawEllipse(center, outer, outer)
        painter.setBrush(QColor(theme.CARD))
        painter.drawEllipse(center, inner, inner)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # kružnice: ±1 SD zelené hrany prstence, norma i +2 SD jen tenké čárkované (silná
        # černá norma rušila, 31. 10. 2026), okraj jemný
        for limit, color, width_px, dashed in (
            (2.0, theme.NEUTRAL, 1.0, True),
            (1.0, theme.OK, 1.4, False),
            (0.0, theme.MUTED, 1.2, True),
            (-1.0, theme.OK, 1.4, False),
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
        # dimenze nad 1 SD: průsvitná výseč od normy k bodu barvou pásma
        for i, axis in enumerate(self._axes):
            if axis.band not in ("warn", "bad"):
                continue
            wedge = QColor(BAND_COLORS[axis.band])
            wedge.setAlphaF(0.28)
            angle = -math.pi / 2 + i * step
            r0, r1 = sorted((ring(0.0), ring(axis.score)))
            spread = min(step / 2 * 0.8, 0.35)  # nejvýš ±20°, u mála os by byl koláč
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
        # překryv starších vyšetření: modře čárkovaně s dutými body, pod poslední čarou
        for overlay in self._overlays:
            pts = [
                (i, point(i, s))
                for i, s in enumerate(overlay.scores[: len(self._axes)])
                if s is not None
            ]
            if len(pts) < 2:
                continue
            color = QColor(overlay.color)
            pen = QPen(color, 2.0, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern(list(overlay.dash))
            painter.setPen(pen)
            if overlay.fill_alpha > 0 and len(pts) == len(self._axes):
                fill = QColor(color)
                fill.setAlphaF(overlay.fill_alpha)
                painter.setBrush(fill)
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            if len(pts) == len(self._axes):
                painter.drawPolygon(QPolygonF([p for _i, p in pts]))
            else:
                painter.drawPolyline(QPolygonF([p for _i, p in pts]))
            painter.setPen(QPen(color, 2.0))
            painter.setBrush(QColor(theme.CARD))
            for _i, p in pts:
                if overlay.marker == "square":
                    painter.drawRect(QRectF(p.x() - 4, p.y() - 4, 8, 8))
                else:
                    painter.drawEllipse(p, 4, 4)
        polygon = QPolygonF([point(i, a.score) for i, a in enumerate(self._axes)])
        fill = QColor(theme.TEXT)
        fill.setAlphaF(0.08)
        painter.setBrush(fill)
        painter.setPen(
            QPen(
                QColor(theme.TEXT),
                2.2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPolygon(polygon)
        for i, axis in enumerate(self._axes):
            color = QColor(BAND_COLORS.get(axis.band, theme.NEUTRAL))
            painter.setPen(QPen(QColor(theme.CARD), 1.5))
            painter.setBrush(color)
            painter.drawEllipse(point(i, axis.score), 5.5, 5.5)

        # vnější hrana zeleného prstence má popisek mezi první a druhou osou; −1 SD
        # a ostatní kružnice vysvětluje stupnice vedle grafu (ScaleLegend)
        painter.setFont(small)
        painter.setPen(QColor(theme.OK))
        tick_angle = -math.pi / 2 + min(step, math.pi / 2) * 0.45
        r = ring(1.0) + 4
        at = QPointF(center.x() + r * math.cos(tick_angle), center.y() + r * math.sin(tick_angle))
        painter.drawText(
            QRectF(at.x(), at.y() - metrics_h, 60, metrics_h),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
            "+1 SD",
        )

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
            # název dimenze vždy barvou pásma, v normě tedy zeleně
            painter.setPen(QColor(BAND_COLORS.get(axis.band, theme.TEXT)))
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
            (MIN_SD, -1.0, theme.CARD),
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
        # překryv starších vyšetření: dutá značka na pruhu
        for overlay in self._overlays:
            score = overlay.scores[0] if overlay.scores else None
            if score is None:
                continue
            painter.setPen(QPen(QColor(overlay.color), 2.0))
            painter.setBrush(QColor(theme.CARD))
            px = x_of(clamp_sd(score))
            if overlay.marker == "square":
                painter.drawRect(QRectF(px - 4, y + h / 2 - 4, 8, 8))
            else:
                painter.drawEllipse(QPointF(px, y + h / 2), 4, 4)
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRectF(rect.left(), y - metrics_h - 8, width, metrics_h),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"{axis.label}  {format_sd(axis.score)}",
        )


class ScaleLegend(QWidget):
    """Svislá stupnice k pavouku: −2 až +3 SD s popisky, zelený prstenec ±1 SD, norma zvýrazněná.

    Říká, co znamenají kružnice, aby pavouk sám nemusel mít popisky uvnitř;
    podle orientace pavouka píše, který konec je střed a který okraj.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orientation = OUTWARD
        self.setFixedSize(150, 190)

    def set_orientation(self, orientation: str) -> None:
        if orientation in ORIENTATIONS and orientation != self._orientation:
            self._orientation = orientation
            self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.CARD))
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        painter.setFont(small)
        metrics_h = painter.fontMetrics().height()
        top, bottom = float(metrics_h + 6), float(self.height() - metrics_h - 6)
        bar = QRectF(10, top, 16, bottom - top)

        def y_of(value: float) -> float:
            return bottom - (bottom - top) * fraction(value)  # nahoře horší (MAX_SD)

        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(QColor(theme.CARD))
        painter.drawRect(bar)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BAND_SOFT["ok"]))  # jediné pásmo: v normě −1 až +1 SD
        painter.drawRect(QRectF(bar.left(), y_of(1.0), bar.width(), y_of(-1.0) - y_of(1.0)))
        labels = (
            (2.0, "+2 SD", theme.NEUTRAL, False),
            (1.0, "+1 SD", theme.OK, True),
            (0.0, tr("norma"), theme.MUTED, False),
            (-1.0, "−1 SD", theme.OK, True),
        )
        for value, text, color, solid in labels:
            y = y_of(value)
            pen = QPen(QColor(color), 1.5 if solid else 1)
            if not solid:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(bar.left() - 3, y), QPointF(bar.right() + 3, y))
            painter.setPen(QColor(color if solid else theme.MUTED))
            painter.drawText(
                QRectF(
                    bar.right() + 8, y - metrics_h / 2, self.width() - bar.right() - 8, metrics_h
                ),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                text,
            )
        inward = self._orientation == INWARD
        worse = tr("horší · střed") if inward else tr("horší · okraj")
        better = tr("lepší · okraj") if inward else tr("lepší · střed")
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(
            QRectF(0, 0, self.width(), metrics_h),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            worse,
        )
        painter.drawText(
            QRectF(0, self.height() - metrics_h, self.width(), metrics_h),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            better,
        )
        painter.end()


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
