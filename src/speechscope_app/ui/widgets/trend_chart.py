"""Malé grafy vývoje v čase: jedna dimenze = jeden graf, mřížka po dvou (31. 10. 2026).

Stejná řeč jako pavouk, jen rozložená v čase: osa y v SD proti normě, patologie
nahoru, v pozadí pásma (zelené v normě, oranžové hraniční, červené mimo), norma
jen tenkou čárkovanou čarou (silná černá rušila), body barvou pásma s hodnotou
nad bodem. Vyšetření s jinou verzí feature než norma má dutý bod. Osa x má
vyšetření rovnoměrně za sebou (ne podle odstupu dnů), aby bylo čitelných
i deset návštěv; data jsou pod body.

Měřítko Hodnoty (mockup https://claude.ai/artifact/Nw7jUHXFYWFv7MHQ7eb1Dm): jeden
graf = jedna položka dimenze v jednotkách feature, osa podle hodnot pacienta
(`nice_axis`), bez pásem, čára a body černé; v hlavičce poslední hodnota a změna
se šipkou k patologii. Mřížka pak má nadpisy skupin po dimenzích
(`TrendGrid.set_groups`). Podoby Vývoje: mockup
https://claude.ai/artifact/DgxUjxJmh5SAihwZ4SkQog.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import theme
from .spider_chart import BAND_COLORS, BAND_SOFT, MAX_SD, SPAN, clamp_sd

COLUMNS = 2
CHART_HEIGHT = 230
GROUP_HEADER_HEIGHT = 28


@dataclass(slots=True)
class TrendSeries:
    title: str
    labels: list[str]  # popisky vyšetření pod body
    scores: list[float | None]
    bands: list[str]
    outdated: list[bool] = field(default_factory=list)
    headline: str = ""  # poslední hodnota (a u SD i změna) vpravo nahoře
    color: str = theme.TEXT  # barva názvu a souhrnu (u SD podle posledního pásma)
    scale: str = "sd"  # „sd“ = osa v SD s pásmy, „values“ = jednotky feature bez pásem
    ymin: float = -2.0  # jen pro „values“
    ymax: float = 3.0
    ticks: list[tuple[float, str]] = field(default_factory=list)  # jen pro „values“
    texts: list[str] = field(default_factory=list)  # hodnoty u bodů a do nápovědy (jinak SD)
    change: str = ""  # změna proti předchozímu vyšetření za souhrnem (jen „values“)
    arrow: str = ""  # ▲ k patologii, ▼ od ní, prázdné bez soudu
    arrow_color: str = theme.MUTED
    subtitle: str = ""  # položky dimenze za názvem, šedě

    def point_texts(self) -> list[str]:
        if self.texts:
            return self.texts
        return [
            f"{score:+.1f}".replace(".", ",").replace("+0,0", "0,0") if score is not None else "–"
            for score in self.scores
        ]

    def tooltip(self) -> str:
        unit = "" if self.texts else " SD"
        return "\n".join(
            f"{label}: {text}{unit if text != '–' else ''}"
            for label, text in zip(self.labels, self.point_texts(), strict=False)
        )


def nice_axis(values: list[float]) -> tuple[float, float, list[float]]:
    """Rozsah osy a dvě až čtyři „hezké“ čáry podle hodnot pacienta s rezervou."""
    if not values:
        return 0.0, 1.0, [0.0, 0.5, 1.0]
    lo, hi = min(values), max(values)
    if lo == hi:
        pad = abs(lo) * 0.2 or 1.0
        lo, hi = lo - pad, hi + pad
    pad = (hi - lo) * 0.15
    lo, hi = lo - pad, hi + pad
    raw = (hi - lo) / 2.5
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(s * magnitude for s in (1, 2, 2.5, 5, 10) if s * magnitude >= raw)
    ymin = math.floor(lo / step) * step
    ymax = math.ceil(hi / step) * step
    ticks: list[float] = []
    tick = ymin
    while tick <= ymax + step / 2:
        ticks.append(round(tick, 10))
        tick += step
    return float(ymin), float(ymax), ticks


def format_tick(value: float, ticks: list[float]) -> str:
    """Popisek čáry osy s tolika desetinnými místy, kolik krok potřebuje."""
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1.0
    decimals = 0 if step >= 1 else max(0, -math.floor(math.log10(step)))
    text = f"{value:.{decimals}f}"
    if text.startswith("-"):
        text = "−" + text[1:]
    return text.replace(".", ",")


class TrendChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.series: TrendSeries | None = None
        self.setFixedHeight(CHART_HEIGHT)  # pevná výška: karta i mřížka ji pak hlásí nahoru
        self.setMinimumWidth(260)

    def set_series(self, series: TrendSeries) -> None:
        self.series = series
        self.setToolTip(series.tooltip())
        self.update()

    def _segments(self) -> list[tuple[str, str]]:
        """Souhrn vpravo nahoře po kusech s barvou: hodnota · ▲ změna."""
        series = self.series
        assert series is not None
        out = [(series.headline, series.color)] if series.headline else []
        if series.change:
            if out:
                out.append((" · ", series.color))
            if series.arrow:
                out.append((series.arrow + " ", series.arrow_color))
            out.append((series.change, series.color))
        return out

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
        bold.setPointSizeF(base_font.pointSizeF() + 1)
        small = QFont(base_font)
        small.setPointSizeF(max(7.0, base_font.pointSizeF() - 2))
        # titulek vlevo (s položkami šedě), souhrn vpravo; titulek dostane zbytek
        painter.setFont(base_font)
        metrics = painter.fontMetrics()
        segments = self._segments()
        head_width = sum(metrics.horizontalAdvance(text) for text, _c in segments) + 8
        x = width - 4
        for text, color in reversed(segments):
            w = metrics.horizontalAdvance(text)
            x -= w
            painter.setPen(QColor(color))
            painter.drawText(
                QRectF(x, 2, w + 1, 22),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        painter.setFont(bold)
        title_width = max(40.0, width - 8 - head_width - 6)
        title = painter.fontMetrics().elidedText(
            series.title, Qt.TextElideMode.ElideRight, int(title_width)
        )
        painter.setPen(QColor(series.color))
        painter.drawText(
            QRectF(4, 2, title_width, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )
        used = painter.fontMetrics().horizontalAdvance(title) + 10
        if series.subtitle and title_width - used > 40:
            painter.setFont(small)
            painter.setPen(QColor(theme.MUTED))
            sub = painter.fontMetrics().elidedText(
                series.subtitle, Qt.TextElideMode.ElideRight, int(title_width - used)
            )
            painter.drawText(
                QRectF(4 + used, 2, title_width - used, 22),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                sub,
            )
        painter.setFont(base_font)
        values = series.scale == "values"
        left = 46
        plot = QRectF(left, 34, width - left - 10, height - 34 - 28)
        if plot.width() <= 10 or plot.height() <= 10:
            return
        ymin, ymax = (series.ymin, series.ymax) if values else (MAX_SD - SPAN, MAX_SD)
        if not ymax > ymin:
            ymax = ymin + 1.0

        def y_of(score: float) -> float:
            value = score if values else clamp_sd(score)
            value = min(max(value, ymin), ymax)
            return plot.top() + (ymax - value) / (ymax - ymin) * plot.height()

        painter.setFont(small)
        if values:
            # jen světlé čáry s hodnotami osy, žádná pásma
            for level, text in series.ticks:
                if level < ymin or level > ymax:
                    continue
                painter.setPen(QPen(QColor(theme.BORDER), 1))
                painter.drawLine(
                    QPointF(plot.left(), y_of(level)), QPointF(plot.right(), y_of(level))
                )
                painter.setPen(QColor(theme.MUTED))
                painter.drawText(
                    QRectF(0, y_of(level) - 8, left - 4, 16),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    text,
                )
        else:
            # pásma v pozadí: mimo normu, hraniční, v normě; pod −1 SD bílá
            painter.setPen(Qt.PenStyle.NoPen)
            for lo, hi, band in ((2.0, MAX_SD, "bad"), (1.0, 2.0, "warn"), (-1.0, 1.0, "ok")):
                painter.setBrush(QColor(BAND_SOFT[band]))
                painter.drawRect(QRectF(plot.left(), y_of(hi), plot.width(), y_of(lo) - y_of(hi)))
            norm_pen = QPen(QColor(theme.MUTED), 1, Qt.PenStyle.DashLine)
            painter.setPen(norm_pen)
            painter.drawLine(QPointF(plot.left(), y_of(0.0)), QPointF(plot.right(), y_of(0.0)))
            for level, text, color in (
                (2.0, "+2 SD", theme.MUTED),
                (1.0, "+1 SD", theme.MUTED),
                (0.0, "norma", theme.MUTED),
                (-1.0, "−1 SD", theme.MUTED),
            ):
                painter.setPen(QColor(color))
                painter.drawText(
                    QRectF(0, y_of(level) - 8, left - 4, 16),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    text,
                )
        # body a čára
        n = len(series.scores)
        if n == 0:
            painter.end()
            return
        pad = 24.0
        step = (plot.width() - 2 * pad) / (n - 1) if n > 1 else 0.0
        xs = [plot.left() + pad + i * step for i in range(n)]
        if n == 1:
            xs = [plot.center().x()]
        bands = series.bands or [""] * n
        texts = series.point_texts()
        points = [
            (x, y_of(s), band, out, text)
            for x, s, band, out, text in zip(
                xs, series.scores, bands, series.outdated or [False] * n, texts, strict=False
            )
            if s is not None
        ]
        painter.setPen(QPen(QColor(theme.TEXT), 2))
        for (x0, y0, *_a), (x1, y1, *_b) in zip(points, points[1:], strict=False):
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        last_x = points[-1][0] if points else None
        for x, y, band, outdated, text in points:
            color = QColor(theme.TEXT if values else BAND_COLORS.get(band, theme.NEUTRAL))
            painter.setPen(QPen(color, 2))
            painter.setBrush(QColor(theme.CARD) if outdated else color)
            painter.drawEllipse(QPointF(x, y), 5.5, 5.5)
            # hodnota nad bodem, u posledního tučně barvou pásma
            label_font = QFont(small)
            label_font.setBold(x == last_x)
            painter.setFont(label_font)
            painter.setPen(QColor(color if x == last_x else theme.MUTED))
            painter.drawText(QRectF(x - 40, y - 22, 80, 14), Qt.AlignmentFlag.AlignHCenter, text)
        painter.setFont(small)
        for x, label in zip(xs, series.labels, strict=False):
            painter.setPen(QColor(theme.TEXT if x == last_x else theme.MUTED))
            painter.drawText(
                QRectF(x - 50, plot.bottom() + 6, 100, 18), Qt.AlignmentFlag.AlignHCenter, label
            )
        painter.end()


class TrendGrid(QWidget):
    """Mřížka karet s grafy, dvě vedle sebe; volitelně ve skupinách s nadpisem."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(12)
        self.charts: list[TrendChart] = []
        self._cards: list[QFrame] = []
        self.headers: list[QWidget] = []
        self.empty = QLabel("")
        self.empty.setObjectName("muted")
        self.empty.hide()

    def set_series(self, series: list[TrendSeries]) -> None:
        self.set_groups([("", "", series)])

    def set_groups(self, groups: list[tuple[str, str, list[TrendSeries]]]) -> None:
        """Skupiny (nadpis, podtitulek, grafy); prázdný nadpis = bez řádku s nadpisem."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.charts, self._cards, self.headers = [], [], []
        if not any(series for _t, _s, series in groups):
            self._grid.addWidget(self.empty, 0, 0)
            self.empty.show()
            return
        self.empty.hide()
        row = 0
        chart_rows = 0
        for title, subtitle, series in groups:
            if not series:
                continue
            if title:
                header = QWidget()
                header_layout = QHBoxLayout(header)
                header_layout.setContentsMargins(0, 6 if row else 0, 0, 0)
                header_layout.setSpacing(8)
                name = QLabel(title)
                font = name.font()
                font.setBold(True)
                name.setFont(font)
                header_layout.addWidget(name)
                if subtitle:
                    sub = QLabel(subtitle)
                    sub.setObjectName("muted")
                    header_layout.addWidget(sub)
                header_layout.addStretch(1)
                header.setFixedHeight(GROUP_HEADER_HEIGHT)
                self._grid.addWidget(header, row, 0, 1, COLUMNS)
                self.headers.append(header)
                row += 1
            for i, one in enumerate(series):
                card = QFrame()
                card.setObjectName("card")
                layout = QVBoxLayout(card)
                layout.setContentsMargins(8, 6, 8, 6)
                chart = TrendChart()
                chart.set_series(one)
                layout.addWidget(chart)
                self._grid.addWidget(card, row + i // COLUMNS, i % COLUMNS)
                self.charts.append(chart)
                self._cards.append(card)
            rows = (len(series) + COLUMNS - 1) // COLUMNS
            row += rows
            chart_rows += rows
        for col in range(COLUMNS):
            self._grid.setColumnStretch(col, 1)
        # mřížka v rolovací ploše: výšku si musí říct sama, jinak karty přetečou
        self.setMinimumHeight(
            chart_rows * (CHART_HEIGHT + 12 + 12)
            + len(self.headers) * GROUP_HEADER_HEIGHT
            + max(0, row - 1) * 12
        )
        self.updateGeometry()


__all__ = ["TrendChart", "TrendGrid", "TrendSeries", "format_tick", "nice_axis"]
