"""Vzhled rozbalovacích seznamů v celé aplikaci (31. 10. 2026).

Mockup https://claude.ai/artifact/VDkmusEWGcnrKAdSKCFkHf (`docs/mockups/rozbalovaci-seznamy/`
mimo git). Dřív stylesheet vypnul šipku (`::drop-down` bez obrázku) a otevřený seznam byl
holý panel s plným modrým řádkem. Teď: šipka kreslená v QPainter do malého PNG pro
stylesheet (bez SVG a bez souborů v balíku), otevřený seznam jako karta se zaoblením,
řádky s najetím a fajfkou u vybrané hodnoty (`ComboDelegate`). Delegáta a průhledné okno
seznamu dostane každý QComboBox sám při prvním vyleštění (`ComboPolisher`, filtr událostí
aplikace), takže se nic nemusí měnit na 26 místech, kde seznam vzniká.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from .. import theme

HOVER_BG = "#eff6ff"
ARROW = "#6b7280"
ARROW_OFF = "#d1d5db"
ROW_PAD = 14  # svislé odsazení řádku seznamu (řádek ~32 px při běžném písmu)


def _arrow_png(name: str, color: str, *, up: bool = False) -> str:
    """Šipka ˅ (nebo ˄) jako PNG 32 × 32 v dočasné složce; cesta pro stylesheet."""
    folder = Path(tempfile.gettempdir()) / "speechscope-ui"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.png"
    if not target.exists():
        image = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 3.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        if up:
            path.moveTo(8, 20)
            path.lineTo(16, 12)
            path.lineTo(24, 20)
        else:
            path.moveTo(8, 12)
            path.lineTo(16, 20)
            path.lineTo(24, 12)
        p.drawPath(path)
        p.end()
        image.save(str(target))
    return target.as_posix()


def arrow_urls() -> dict[str, str]:
    """Cesty k šipkám pro stylesheet (`theme.stylesheet`)."""
    return {
        "ARROW_DOWN": _arrow_png("chevron-down", ARROW),
        "ARROW_UP": _arrow_png("chevron-up", theme.ACCENT, up=True),
        "ARROW_OFF": _arrow_png("chevron-off", ARROW_OFF),
    }


class ComboDelegate(QStyledItemDelegate):
    """Řádek otevřeného seznamu: najetí světle modře, vybraná hodnota modře s fajfkou."""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo)
        self._combo = combo

    def sizeHint(self, option, index) -> QSize:  # noqa: ANN001, N802
        hint = super().sizeHint(option, index)
        if self._is_separator(index):
            return QSize(hint.width(), 9)
        height = option.fontMetrics.height() + ROW_PAD + 4
        return QSize(hint.width() + 40, max(hint.height(), height))

    @staticmethod
    def _is_separator(index) -> bool:  # noqa: ANN001
        return index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: ANN001
        rect = QRectF(option.rect).adjusted(0, 1, 0, -1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._is_separator(index):
            painter.setPen(QPen(QColor(theme.BORDER), 1))
            y = rect.center().y()
            painter.drawLine(QPointF(rect.left() + 6, y), QPointF(rect.right() - 6, y))
            painter.restore()
            return
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        hot = bool(
            option.state & (QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected)
        )
        if hot and enabled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(HOVER_BG))
            painter.drawRoundedRect(rect, 6, 6)
        current = index.row() == self._combo.currentIndex()
        font = QFont(option.font)
        font.setBold(current)
        painter.setFont(font)
        color = theme.ACCENT if current else (theme.TEXT if enabled else "#9ca3af")
        painter.setPen(QColor(color))
        text_rect = rect.adjusted(10, 0, -30, 0)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            painter.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, int(text_rect.width())
            ),
        )
        if current:
            painter.drawText(
                rect.adjusted(0, 0, -10, 0),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                "✓",
            )
        painter.restore()


class ComboPolisher(QObject):
    """Každému QComboBox při prvním vyleštění dá delegáta a průhledné okno seznamu."""

    DONE = "speechscope_combo_styled"

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.Polish
            and isinstance(obj, QComboBox)
            and not obj.property(self.DONE)
        ):
            obj.setProperty(self.DONE, True)
            style_combo(obj)
        return False


def style_combo(combo: QComboBox) -> None:
    """Delegát řádků a okno seznamu se zaoblenými rohy (bez hranatého podkladu)."""
    if combo.isEditable():
        return  # upravitelné seznamy (doplňování) si nechají výchozí chování
    combo.setItemDelegate(ComboDelegate(combo))
    view = combo.view()
    view.setMouseTracking(True)
    container: QWidget | None = view.parentWidget()
    if container is not None:
        container.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        container.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # průhledný jen kontejner (kvůli zaobleným rohům karty), ne seznam v něm
        container.setObjectName("comboPopup")
        container.setStyleSheet("QWidget#comboPopup { background: transparent; border: none; }")


_POLISHER: ComboPolisher | None = None


def install(app: QApplication) -> None:
    """Zapne vzhled seznamů pro celou aplikaci (jednou, z `theme.apply`)."""
    global _POLISHER
    if _POLISHER is None:
        _POLISHER = ComboPolisher(app)
        app.installEventFilter(_POLISHER)
