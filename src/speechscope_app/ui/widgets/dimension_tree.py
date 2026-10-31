"""Strom dimenzí a položek na Pacientech: osa odchylky v řádku, stupnice v záhlaví, štítky.

Podoba A z mockupu `docs/mockups/strom-dimenzi/` (31. 10. 2026): každý řádek má
na společné ose −2 až +3 SD bod (dimenze kosočtverec, položka kroužek) s „nožičkou“
od nuly, zelený pás ±1 SD je norma, nula a +2 SD čárkovaně. Barvu nese jen štítek
pásma u dimenze, bod a číslo SD; text řádků zůstává černý.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QWidget,
)

from .. import theme
from .spider_chart import BAND_COLORS, MAX_SD, MIN_SD, fraction

AXIS_PAD = 14  # vodorovný okraj osy v buňce i v záhlaví, aby čísla seděla nad body
AXIS_MIN, AXIS_MAX = 200, 360  # šířka sloupce osy podle místa (`DimensionTree.fit_columns`)
BAND_ROLE = Qt.ItemDataRole.UserRole + 40  # pásmo dimenze → štítek za názvem
PILL_ROLE = Qt.ItemDataRole.UserRole + 41  # text štítku za názvem dimenze
TASK_PILL_ROLE = Qt.ItemDataRole.UserRole + 42  # úloha položky jako šedý štítek

BAND_STRONG: dict[str, str] = {"ok": "#166534", "warn": "#92400e", "bad": "#991b1b"}
BAND_PILL: dict[str, str] = {
    "ok": theme.OK_SOFT,
    "warn": theme.WARN_SOFT,
    "bad": theme.MISSING_SOFT,
}
DIMENSION_ROW_BG = "#f9fafb"
DOT_SIZE = 10
_dots: dict[str, QIcon] = {}


def band_dot(band: str) -> QIcon:
    """Tečka před názvem položky barvou pásma; bez pásma (chybí hodnota) průhledná,
    aby názvy položek zůstaly zarovnané."""
    key = band if band in BAND_COLORS else ""
    if key not in _dots:
        pixmap = QPixmap(DOT_SIZE, DOT_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)
        if key:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(BAND_COLORS[key]))
            painter.drawEllipse(QRectF(1, 1, DOT_SIZE - 2, DOT_SIZE - 2))
            painter.end()
        _dots[key] = QIcon(pixmap)
    return _dots[key]


def _axis_x(rect: QRectF, score: float) -> float:
    return rect.left() + rect.width() * fraction(score)


class DeviationAxis(QWidget):
    """Bod na ose −2 až +3 SD s pásem normy; `dimension=True` kreslí větší kosočtverec."""

    def __init__(
        self, score: float, band: str, dimension: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.score = score
        self.band = band
        self.dimension = dimension
        self.setMinimumWidth(120)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(AXIS_PAD, 0, max(1.0, self.width() - 2 * AXIS_PAD), self.height())
        mid = area.center().y()
        half = 7 if self.dimension else 5
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.OK_SOFT if self.dimension else "#f0fdf4"))
        lo, hi = _axis_x(area, -1.0), _axis_x(area, 1.0)
        painter.drawRect(QRectF(lo, mid - half, hi - lo, 2 * half))
        zero = _axis_x(area, 0.0)
        for score, color in ((0.0, theme.MUTED), (2.0, "#d1d5db")):
            pen = QPen(QColor(color))
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            x = _axis_x(area, score)
            painter.drawLine(QPointF(x, mid - half - 4), QPointF(x, mid + half + 4))
        color = QColor(BAND_COLORS.get(self.band, theme.NEUTRAL))
        at = _axis_x(area, self.score)
        stem = QColor(color)
        stem.setAlphaF(0.35)
        thick = 6 if self.dimension else 3
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(stem)
        x0, x1 = sorted((zero, at))
        painter.drawRect(QRectF(x0, mid - thick / 2, x1 - x0, thick))
        painter.setBrush(color)
        if self.dimension:
            painter.setPen(QPen(QColor(theme.CARD), 2))
            r = 8.0
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(at, mid - r),
                        QPointF(at + r, mid),
                        QPointF(at, mid + r),
                        QPointF(at - r, mid),
                    ]
                )
            )
        else:
            painter.drawEllipse(QPointF(at, mid), 5, 5)
        painter.end()


class AxisHeader(QHeaderView):
    """Záhlaví, které nad sloupcem osy napíše stupnici −2 … +3."""

    def __init__(self, axis_column: int, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.axis_column = axis_column

    def paintSection(self, painter: QPainter, rect: QRect, logical: int) -> None:  # noqa: N802
        super().paintSection(painter, rect, logical)
        if logical != self.axis_column:
            return
        painter.save()
        area = QRectF(rect).adjusted(AXIS_PAD, 0, -AXIS_PAD, 0)
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        for value in range(int(MIN_SD), int(MAX_SD) + 1):
            text = ("+" if value > 0 else "−" if value < 0 else "") + str(abs(value))
            width = metrics.horizontalAdvance(text)
            x = _axis_x(area, float(value)) - width / 2
            painter.setPen(QColor(theme.TEXT if value == 0 else theme.MUTED))
            painter.drawText(
                QRectF(x, rect.top(), width, rect.height()),
                int(Qt.AlignmentFlag.AlignCenter),
                text,
            )
        painter.restore()


class DimensionTree(QTreeWidget):
    """Strom se záhlavím stupnice; šířky sloupců názvu a osy dopočítává sám.

    Název dostane aspoň svou šířku podle obsahu, osa zbytek mezi `AXIS_MIN`
    a `AXIS_MAX`, co zbude, jde zase názvu. Režim Stretch by v užším okně
    smrskl název na pár písmen. Ostatní sloupce jsou podle obsahu. Se
    skrytou osou (bez normy) se nic nepočítá a název se roztahuje. Výška je
    podle viditelných řádků (`fit_height`), vlastní svislý posuvník strom nemá.
    """

    def __init__(self, axis_column: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.axis_column = axis_column
        self.setHeader(AxisHeader(axis_column, self))
        # vysoký podle obsahu, roluje stránka kolem (31. 10. 2026: blok s vlastním
        # posuvníkem uživatele obtěžoval)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.expanded.connect(lambda _index: self.fit_height())
        self.collapsed.connect(lambda _index: self.fit_height())

    def refit(self) -> None:
        """Po naplnění: šířky sloupců a výška podle obsahu."""
        self.fit_columns()
        self.fit_height()

    def _name_width(self) -> int:
        """Šířka názvů včetně sbalených položek, aby rozbalení neposouvalo sloupce."""
        expanded = [self.topLevelItem(i).isExpanded() for i in range(self.topLevelItemCount())]
        self.blockSignals(True)
        self.expandAll()
        self.doItemsLayout()
        width = self.sizeHintForColumn(0)
        for i, state in enumerate(expanded):
            self.topLevelItem(i).setExpanded(state)
        self.blockSignals(False)
        return max(width, self.header().sectionSizeFromContents(0).width())

    def fit_columns(self) -> None:
        header = self.header()
        if self.isColumnHidden(self.axis_column):
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            return
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.axis_column, QHeaderView.ResizeMode.Interactive)
        name = self._name_width()  # i rozvržení, po něm mají sloupce podle obsahu šířku
        others = sum(
            header.sectionSize(c)
            for c in range(1, self.columnCount())
            if c != self.axis_column and not self.isColumnHidden(c)
        )
        free = self.viewport().width() - others
        axis = max(AXIS_MIN, min(AXIS_MAX, free - name))
        header.resizeSection(self.axis_column, axis)
        header.resizeSection(0, max(name, free - axis))

    def fit_height(self) -> None:
        self.doItemsLayout()
        rows = 0
        index = self.model().index(0, 0)
        while index.isValid():
            rows += self.rowHeight(index)
            index = self.indexBelow(index)
        header = self.header()
        height = header.sizeHint().height() + rows + 2 * self.frameWidth() + 2
        if header.length() > self.viewport().width():  # v úzkém okně posuvník do strany
            height += self.horizontalScrollBar().sizeHint().height()
        self.setFixedHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.refit()


class DimensionTreeDelegate(QStyledItemDelegate):
    """Štítek pásma za názvem dimenze (sloupec 0) a úloha položky jako šedý štítek."""

    def _pill_font(self, option) -> QFont:  # noqa: ANN001
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 0.85)
        return font

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: ANN001
        task = index.data(TASK_PILL_ROLE)
        if task:
            # pozadí a výběr bez textu (super().paint by text znovu doplnil)
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            style = opt.widget.style() if opt.widget is not None else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
            self._draw_pill(
                painter, option, option.rect.left() + 4, task, theme.NEUTRAL_SOFT, "#374151"
            )
            return
        super().paint(painter, option, index)
        pill = index.data(PILL_ROLE)
        if not pill:
            return
        band = str(index.data(BAND_ROLE) or "")
        text_font = index.data(Qt.ItemDataRole.FontRole) or option.font
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        left = option.rect.left() + 6 + QFontMetrics(text_font).horizontalAdvance(text) + 10
        self._draw_pill(
            painter,
            option,
            left,
            pill,
            BAND_PILL.get(band, theme.NEUTRAL_SOFT),
            BAND_STRONG.get(band, theme.TEXT),
        )

    def _draw_pill(
        self,
        painter: QPainter,
        option,
        left: float,
        text: str,
        soft: str,
        strong: str,  # noqa: ANN001
    ) -> None:
        font = self._pill_font(option)
        metrics = QFontMetrics(font)
        height = metrics.height() + 2
        rect = QRectF(
            left,
            option.rect.center().y() - height / 2 + 1,
            metrics.horizontalAdvance(text) + 16,
            height,
        )
        painter.save()
        painter.setClipRect(option.rect)  # v úzkém sloupci nepřeteče do sousedního
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(soft))
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setPen(QColor(strong))
        painter.setFont(font)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: ANN001, N802
        size = super().sizeHint(option, index)
        top_level = not index.parent().isValid()
        size.setHeight(max(size.height(), 36 if top_level else 30))
        pill = index.data(PILL_ROLE) or index.data(TASK_PILL_ROLE)
        if pill:
            extra = QFontMetrics(self._pill_font(option)).horizontalAdvance(str(pill)) + 30
            size.setWidth(size.width() + extra if index.data(PILL_ROLE) else extra)
        return size


__all__ = [
    "DOT_SIZE",
    "band_dot",
    "AXIS_PAD",
    "DimensionTree",
    "AxisHeader",
    "BAND_ROLE",
    "BAND_STRONG",
    "DIMENSION_ROW_BG",
    "DeviationAxis",
    "DimensionTreeDelegate",
    "PILL_ROLE",
    "TASK_PILL_ROLE",
]
