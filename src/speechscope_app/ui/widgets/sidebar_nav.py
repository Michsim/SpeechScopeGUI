"""Levá lišta: logo, stránky s ikonami ve dvou skupinách, stav prostředí a sbalení.

Podoba B se sbalením a světle modrou barvou (31. 10. 2026, mockupy
https://claude.ai/artifact/WrwYsgM4VjgRZ39WXDFfYP a
https://claude.ai/artifact/8Hf6JPLaZd8XZ83FQx3itR, `docs/mockups/lista/` mimo git).
Nahoře každodenní stránky, dole pod nadpisem Nastavení ostatní. Vybraná položka
je bílá karta s modrým proužkem vlevo. Položka může nést štítek (počet hotových
nahrávek běžící dávky) a tenký průběh pod sebou. Ctrl+B lištu sbalí na samé
ikony, Ctrl+1 … Ctrl+7 přepnou stránku.

Navenek se chová jako dřív jeden `QListWidget` (`setCurrentRow`, `currentRow`,
`item`, `currentRowChanged`), ostatní kód se o vzhled nestará. Ikony se kreslí
čarami v QPainter (souřadnice 24 × 24), bez souborů a bez QtSvg.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from importlib import resources

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...i18n import tr
from .. import theme
from .elided_label import ElidedLabel

EXPANDED_WIDTH = 212  # nejmenší šířka rozbalené lišty, při větším písmu roste
COLLAPSED_WIDTH = 56
SIDE_PAD = 10  # okraj položek od kraje lišty; proužek vybrané položky je v něm

# barvy lišty (varianta 3, světle modrá)
BG = theme.SIDEBAR
EDGE = theme.SIDEBAR_EDGE
TEXT = "#2c3e5c"
ICON = "#5b6f90"
MUTED = theme.SIDEBAR_MUTED
BRAND = theme.SIDEBAR_BRAND
HOVER = "#e2e9f5"
SELECTED_BORDER = "#d6e0f0"
TRACK = "#d6e0f0"

ENV_COLORS = {"ok": theme.OK, "warn": theme.WARN, "missing": theme.MISSING, "": MUTED}


# --- ikony -------------------------------------------------------------------


def _poly(path: QPainterPath, *points: tuple[float, float]) -> None:
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)


def _line(path: QPainterPath, x1: float, y1: float, x2: float, y2: float) -> None:
    path.moveTo(x1, y1)
    path.lineTo(x2, y2)


def _home(p: QPainterPath) -> None:
    _poly(p, (3.5, 10.5), (12, 3.5), (20.5, 10.5))
    _poly(p, (5.5, 9), (5.5, 20), (18.5, 20), (18.5, 9))
    _poly(p, (10, 20), (10, 14.5), (14, 14.5), (14, 20))


def _wave(p: QPainterPath) -> None:
    _line(p, 3, 12, 4.5, 12)
    _line(p, 7, 9, 7, 15)
    _line(p, 10.5, 5, 10.5, 19)
    _line(p, 14, 8, 14, 16)
    _line(p, 17.5, 10.5, 17.5, 13.5)
    _line(p, 20, 12, 21, 12)


def _table(p: QPainterPath) -> None:
    p.addRoundedRect(QRectF(3.5, 4.5, 17, 15), 2, 2)
    _line(p, 3.5, 9.5, 20.5, 9.5)
    _line(p, 3.5, 14.5, 20.5, 14.5)
    _line(p, 9, 9.5, 9, 19.5)


def _users(p: QPainterPath) -> None:
    p.addEllipse(QPointF(9, 8.5), 3.3, 3.3)
    p.moveTo(3, 19.5)
    p.cubicTo(3.7, 16.2, 6.1, 14.3, 9, 14.3)
    p.cubicTo(11.9, 14.3, 14.3, 16.2, 15, 19.5)
    p.arcMoveTo(QRectF(11.4, 5.2, 6.6, 6.6), 76)
    p.arcTo(QRectF(11.4, 5.2, 6.6, 6.6), 76, -152)
    p.moveTo(17.5, 14.6)
    p.cubicTo(19.3, 15.2, 20.5, 16.9, 20.9, 19.5)


def _protocol(p: QPainterPath) -> None:
    p.addRoundedRect(QRectF(5, 4.5, 14, 16), 2, 2)
    _poly(p, (9, 4.5), (9, 3.5), (15, 3.5), (15, 4.5))
    _line(p, 8.5, 10, 15.5, 10)
    _line(p, 8.5, 13.5, 15.5, 13.5)
    _line(p, 8.5, 17, 12.5, 17)


def _norm(p: QPainterPath) -> None:
    _line(p, 3, 19.5, 21, 19.5)
    p.moveTo(4, 18.5)
    p.cubicTo(7.2, 18.5, 8.3, 7.5, 12, 7.5)
    p.cubicTo(15.7, 7.5, 16.8, 18.5, 20, 18.5)
    for y in (9.5, 12.5, 15.5):  # čárkovaná osa uprostřed
        _line(p, 12, y, 12, y + 1.4)


def _environment(p: QPainterPath) -> None:
    p.addRoundedRect(QRectF(6.5, 6.5, 11, 11), 2, 2)
    for x in (9.5, 14.5):
        _line(p, x, 3.5, x, 6.5)
        _line(p, x, 17.5, x, 20.5)
    for y in (9.5, 14.5):
        _line(p, 3.5, y, 6.5, y)
        _line(p, 17.5, y, 20.5, y)


def _panel(p: QPainterPath) -> None:
    p.addRoundedRect(QRectF(3.5, 4.5, 17, 15), 2, 2)
    _line(p, 9, 4.5, 9, 19.5)


def _collapse(p: QPainterPath) -> None:
    _panel(p)
    _poly(p, (15.5, 10), (13.5, 12), (15.5, 14))


def _expand(p: QPainterPath) -> None:
    _panel(p)
    _poly(p, (13.5, 10), (15.5, 12), (13.5, 14))


ICONS: dict[str, Callable[[QPainterPath], None]] = {
    "home": _home,
    "wave": _wave,
    "table": _table,
    "users": _users,
    "protocol": _protocol,
    "norm": _norm,
    "environment": _environment,
    "collapse": _collapse,
    "expand": _expand,
}

# ikona podle pořadí stránky (Domů · Analýza · Výsledky · Pacienti · Protokoly · Normy · Prostředí)
PAGE_ICONS = ("home", "wave", "table", "users", "protocol", "norm", "environment")


def draw_icon(painter: QPainter, name: str, rect: QRectF, color: QColor | str) -> None:
    """Čárová ikona `name` vepsaná do čtverce `rect`."""
    path = QPainterPath()
    ICONS[name](path)
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 24, rect.height() / 24)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.restore()


# --- položka -----------------------------------------------------------------


class NavItem(QAbstractButton):
    """Jedna stránka v liště: ikona, název, štítek a průběh; sbalená jen ikona."""

    def __init__(self, label: str, icon: str, shortcut: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._label = label
        self._icon = icon
        self._shortcut = shortcut
        self._badge = ""
        self._progress: float | None = None
        self._collapsed = False
        self._update_tooltip()

    # rozhraní jako QListWidgetItem
    def text(self) -> str:  # noqa: D102  název stránky bez štítku
        return self._label

    def setText(self, text: str) -> None:  # noqa: N802
        self._label = text
        self._update_tooltip()
        self.updateGeometry()
        self.update()

    def badge(self) -> str:
        return self._badge

    def progress(self) -> float | None:
        return self._progress

    def set_badge(self, text: str, progress: float | None = None) -> None:
        """Štítek vpravo (např. „12/40 +1“) a průběh 0–1 pod položkou; prázdné = nic."""
        changed = (progress is None) != (self._progress is None)
        self._badge = text
        self._progress = None if progress is None else max(0.0, min(1.0, progress))
        self._update_tooltip()
        if changed:
            self.updateGeometry()
        self.update()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._update_tooltip()
        self.updateGeometry()
        self.update()

    def _update_tooltip(self) -> None:
        if self._collapsed:
            tip = f"{self._label} · {self._badge}" if self._badge else self._label
        else:
            tip = ""
        if self._shortcut:
            tip = f"{tip} ({self._shortcut})" if tip else self._shortcut
        self.setToolTip(tip)

    def _row_height(self) -> int:
        return max(32, QFontMetrics(self.font()).height() + 14)

    def sizeHint(self) -> QSize:  # noqa: N802
        extra = 5 if self._progress is not None and not self._collapsed else 0
        height = self._row_height() + extra
        if self._collapsed:
            return QSize(COLLAPSED_WIDTH, max(height, 36))
        return QSize(self.preferred_width(), height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def preferred_width(self) -> int:
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        text = QFontMetrics(bold).horizontalAdvance(self._label)
        return SIDE_PAD * 2 + 10 + 18 + 10 + text + 12 + self._badge_width() + 10

    def _badge_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.82))
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def _badge_width(self) -> int:
        if not self._badge:
            return 0
        return QFontMetrics(self._badge_font()).horizontalAdvance(self._badge) + 14

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.isChecked()
        hover = self.underMouse()
        row_h = self._row_height()
        if self._collapsed:
            box = QRectF((self.width() - 38) / 2, (self.height() - 34) / 2, 38, 34)
        else:
            box = QRectF(SIDE_PAD, 1, self.width() - 2 * SIDE_PAD, row_h - 2)
        if selected:
            p.setPen(QPen(QColor(SELECTED_BORDER), 1))
            p.setBrush(QColor(theme.CARD))
            p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            bar_x = box.left() - (8 if self._collapsed else SIDE_PAD)
            p.drawRoundedRect(QRectF(bar_x, box.top() + 7, 3, box.height() - 14), 1.5, 1.5)
        elif hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(HOVER))
            p.drawRoundedRect(box, 6, 6)
        icon_color = theme.ACCENT if selected else ICON
        if self._collapsed:
            icon = QRectF(box.center().x() - 9, box.center().y() - 9, 18, 18)
            draw_icon(p, self._icon, icon, icon_color)
            if self._badge:
                self._paint_dot_badge(p, box)
            return
        icon = QRectF(box.left() + 10, box.center().y() - 9, 18, 18)
        draw_icon(p, self._icon, icon, icon_color)
        text_x = icon.right() + 10
        right = box.right() - 8
        if self._badge:
            width = self._badge_width()
            chip = QRectF(right - width, box.center().y() - 9, width, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT_SOFT))
            p.drawRoundedRect(chip, 9, 9)
            p.setFont(self._badge_font())
            p.setPen(QColor(theme.ACCENT_HOVER))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, self._badge)
            right = chip.left() - 6
        font = QFont(self.font())
        if selected:
            font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor(BRAND if selected else TEXT))
        text_rect = QRectF(text_x, box.top(), max(0.0, right - text_x), box.height())
        label = QFontMetrics(font).elidedText(
            self._label, Qt.TextElideMode.ElideRight, int(text_rect.width())
        )
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
        if self._progress is not None:
            track = QRectF(text_x, row_h + 1, box.right() - 8 - text_x, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(TRACK))
            p.drawRoundedRect(track, 1.5, 1.5)
            if self._progress > 0:
                done = QRectF(track.left(), track.top(), track.width() * self._progress, 3)
                p.setBrush(QColor(theme.ACCENT))
                p.drawRoundedRect(done, 1.5, 1.5)

    def _paint_dot_badge(self, p: QPainter, box: QRectF) -> None:
        """Sbalená lišta: první číslo štítku v modrém kolečku vpravo nahoře."""
        digits = ""
        for ch in self._badge:
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        text = digits or "•"
        font = QFont(self.font())
        font.setPointSizeF(max(6.5, font.pointSizeF() * 0.68))
        font.setWeight(QFont.Weight.Bold)
        width = max(14.0, QFontMetrics(font).horizontalAdvance(text) + 6.0)
        chip = QRectF(box.right() - width + 3, box.top() - 1, width, 14)
        p.setPen(QPen(QColor(BG), 1.5))
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(chip, 7, 7)
        p.setFont(font)
        p.setPen(QColor("white"))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)


class _FooterButton(QAbstractButton):
    """Řádek dole: stav prostředí (tečka) nebo Sbalit lištu; sbalený jen značka."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind  # "env" nebo "toggle"
        self._state = ""
        self._text = ""
        self._hint = ""
        self._collapsed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_content(self, text: str, hint: str = "", state: str = "") -> None:
        self._text = text
        self._hint = hint
        self._state = state
        self.setToolTip(text if self._collapsed or self._kind == "env" else "")
        if self._kind == "toggle":
            self.setToolTip(f"{text} ({hint})")
        self.update()

    def label(self) -> str:
        return self._text

    def state(self) -> str:
        return self._state

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.set_content(self._text, self._hint, self._state)
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(COLLAPSED_WIDTH, max(28, QFontMetrics(self._small()).height() + 12))

    def _small(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.9)
        return font

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._collapsed:
            box = QRectF((self.width() - 38) / 2, 1, 38, self.height() - 2)
        else:
            box = QRectF(SIDE_PAD, 1, self.width() - 2 * SIDE_PAD, self.height() - 2)
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(HOVER))
            p.drawRoundedRect(box, 6, 6)
        if self._kind == "env":
            mark_x = box.center().x() if self._collapsed else box.left() + 10 + 9
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ENV_COLORS.get(self._state, MUTED)))
            p.drawEllipse(QPointF(mark_x, box.center().y()), 4, 4)
        else:
            name = "expand" if self._collapsed else "collapse"
            x = box.center().x() - 9 if self._collapsed else box.left() + 10
            draw_icon(p, name, QRectF(x, box.center().y() - 9, 18, 18), MUTED)
        if self._collapsed:
            return
        font = self._small()
        p.setFont(font)
        p.setPen(QColor(MUTED))
        left = box.left() + 10 + 18 + 10
        right = box.right() - 8
        if self._hint and self._kind == "toggle":
            width = QFontMetrics(font).horizontalAdvance(self._hint)
            p.drawText(
                QRectF(right - width, box.top(), width, box.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                self._hint,
            )
            right -= width + 8
        text = QFontMetrics(font).elidedText(
            self._text, Qt.TextElideMode.ElideRight, int(max(0.0, right - left))
        )
        p.drawText(
            QRectF(left, box.top(), right - left, box.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )


class _Rule(QWidget):
    """Tenká čára přes šířku lišty (nad stavem prostředí, ve sbalené místo nadpisu)."""

    def __init__(self, inset: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._inset = inset
        self.setFixedHeight(9)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setPen(QPen(QColor(EDGE), 1))
        y = self.height() // 2
        inset = self._inset if self.width() > COLLAPSED_WIDTH else 16
        p.drawLine(inset, y, self.width() - inset, y)


# --- lišta -------------------------------------------------------------------


class SidebarNav(QWidget):
    currentRowChanged = Signal(int)  # noqa: N815  jako u QListWidget
    collapsed_changed = Signal(bool)
    environment_clicked = Signal()

    def __init__(
        self, labels: tuple[str, ...], *, bottom_from: int, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._labels = tuple(labels)
        self._split = bottom_from
        self._current = -1
        self._collapsed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 8)
        layout.setSpacing(2)

        # logo: značka a název; sbalená jen značka
        self.brand_panel = QWidget()
        brand = QHBoxLayout(self.brand_panel)
        brand.setContentsMargins(SIDE_PAD + 6, 0, SIDE_PAD, 12)
        brand.setSpacing(9)
        self.mark = QLabel()
        self.mark.setObjectName("nav_mark")
        self.mark.setFixedSize(30, 28)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.addWidget(self.mark)
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        self.brand_name = QLabel(tr("SpeechScope"))
        self.brand_name.setObjectName("brand")
        names.addWidget(self.brand_name)
        self.subtitle = ElidedLabel("")  # při větším písmu se zkrátí, nerozšíří lištu
        self.subtitle.setObjectName("brand_sub")
        names.addWidget(self.subtitle)
        self.brand_names = QWidget()
        self.brand_names.setLayout(names)
        brand.addWidget(self.brand_names, 1)
        layout.addWidget(self.brand_panel)
        self._load_mark()

        self._items: list[NavItem] = []
        for index, label in enumerate(labels):
            if index == bottom_from:
                layout.addStretch(1)
                self.section = QLabel(tr("Nastavení").upper())
                self.section.setObjectName("nav_section")
                self.section.setContentsMargins(SIDE_PAD + 10, 8, SIDE_PAD, 4)
                layout.addWidget(self.section)
                self.section_rule = _Rule(SIDE_PAD + 10)
                self.section_rule.hide()
                layout.addWidget(self.section_rule)
            icon = PAGE_ICONS[index] if index < len(PAGE_ICONS) else "table"
            shortcut = f"Ctrl+{index + 1}" if index < 9 else ""
            item = NavItem(label, icon, shortcut)
            item.clicked.connect(lambda _=False, i=index: self.setCurrentRow(i))
            layout.addWidget(item)
            self._items.append(item)
            if shortcut:
                QShortcut(QKeySequence(shortcut), self, lambda i=index: self.setCurrentRow(i))
        if bottom_from >= len(labels):
            layout.addStretch(1)

        layout.addWidget(_Rule(SIDE_PAD))
        self.env = _FooterButton("env")
        self.env.clicked.connect(self.environment_clicked)
        layout.addWidget(self.env)
        self.toggle = _FooterButton("toggle")
        self.toggle.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        layout.addWidget(self.toggle)
        QShortcut(QKeySequence("Ctrl+B"), self, lambda: self.set_collapsed(not self._collapsed))
        self.set_environment("", tr("Kontroluji prostředí…"))
        self._apply_collapsed()

    def _load_mark(self) -> None:
        pixmap = QPixmap(str(resources.files("speechscope_app") / "assets" / "mark.png"))
        if pixmap.isNull():
            return
        dpr = self.devicePixelRatioF()
        scaled = pixmap.scaled(
            round(28 * dpr),
            round(26 * dpr),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self.mark.setPixmap(scaled)

    # --- sbalení ---------------------------------------------------------------

    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._apply_collapsed()
        self.collapsed_changed.emit(collapsed)

    def _apply_collapsed(self) -> None:
        collapsed = self._collapsed
        self.brand_names.setVisible(not collapsed)
        margins = (0, 0, 0, 12) if collapsed else (SIDE_PAD + 6, 0, SIDE_PAD, 12)
        self.brand_panel.layout().setContentsMargins(*margins)
        self.brand_panel.layout().setAlignment(
            self.mark, Qt.AlignmentFlag.AlignHCenter if collapsed else Qt.AlignmentFlag.AlignLeft
        )
        if hasattr(self, "section"):
            self.section.setVisible(not collapsed)
            self.section_rule.setVisible(collapsed)
        for item in self._items:
            item.set_collapsed(collapsed)
        self.env.set_collapsed(collapsed)
        self.toggle.set_content(tr("Rozbalit lištu") if collapsed else tr("Sbalit lištu"), "Ctrl+B")
        self.toggle.set_collapsed(collapsed)
        self._update_width()

    def _update_width(self) -> None:
        if self._collapsed:
            width = COLLAPSED_WIDTH
        else:
            width = max([EXPANDED_WIDTH] + [item.preferred_width() for item in self._items])
        self.setFixedWidth(width)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._update_width()
        super().changeEvent(event)

    # --- stav ------------------------------------------------------------------

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)

    def set_environment(self, state: str, text: str) -> None:
        """Tečka a věta dole: `ok`, `warn`, `missing` nebo prázdné (zatím nevíme)."""
        self.env.set_content(text, state=state)

    def set_badge(self, index: int, text: str, progress: float | None = None) -> None:
        self._items[index].set_badge(text, progress)
        self._update_width()

    # --- rozhraní jako QListWidget ---------------------------------------------

    def count(self) -> int:
        return len(self._labels)

    def currentRow(self) -> int:  # noqa: N802
        return self._current

    def setCurrentRow(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < len(self._items):
            return
        for i, item in enumerate(self._items):
            item.setChecked(i == index)
        if index == self._current:
            return
        self._current = index
        self.currentRowChanged.emit(index)

    def item(self, index: int) -> NavItem:
        return self._items[index]


def parse_progress(text: str) -> float | None:
    """„12/40“ → 0,3; jiný text (např. „segmentace“) → None."""
    head = text.split()[0] if text.split() else ""
    if "/" not in head:
        return None
    done, _, total = head.partition("/")
    try:
        d, t = float(done), float(total)
    except ValueError:
        return None
    if t <= 0 or math.isnan(d):
        return None
    return d / t
