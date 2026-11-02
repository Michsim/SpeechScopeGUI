"""Nabídky na pravé tlačítko a z tlačítek v podobě karty (2. 11. 2026).

Mockup https://claude.ai/artifact/7i9ZQ6rtGDjTmM1QaivQpH (`docs/mockups/nabidky/` mimo git).
Dřív kreslil nabídky styl Fusion: šedý panel, malé řádky a nedostupná položka byla jen
o odstín světlejší s důvodem schovaným v tooltipu. `Menu` je bílá karta se zaoblením
a stínem, řádky 32 px s čárovou ikonou (jako v levé liště), zkratkou šedě vpravo,
nebezpečná položka (Smazat…) červeně a nedostupná položka šedě s důvodem pod sebou
(`add(..., reason=…)`, `set_reason`), takže je hned vidět, proč nejde.

Chování (myš, klávesy, podnabídky) zůstává z QMenu, třída jen kreslí. Výšku řádků určuje
stylesheet (`QMenu#ssMenu::item` v `theme.py`); ostatní QMenu (třeba kontextová nabídka
textového pole) mají ze stylesheetu aspoň bílou kartu s řádky stejné výšky.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QLabel,
    QMenu,
    QMenuBar,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .. import theme
from .sidebar_nav import ICONS as NAV_ICONS
from .sidebar_nav import _line, _poly

OBJECT_NAME = "ssMenu"  # stylesheet: výška řádků a místo na ikonu
SHADOW = 8  # okraj okna pro stín kolem karty
PAD = 6  # vnitřní okraj karty
RADIUS = 10
TEXT_LEFT = 38  # začátek textu v řádku (ikona 16 px a mezery); sedí se stylesheetem
ICON_LEFT = 11
REASON_CHARS = 44  # šířka důvodu nedostupné položky ve znacích (zalomí se)

HOVER = "#eff5ff"
FAINT = "#9ca3af"
ICON_COLOR = "#4b5563"
ICON_OFF = "#c4c8cf"
REASON = "#b45309"

ICON_PROP = "ss_icon"
DANGER_PROP = "ss_danger"
REASON_PROP = "ss_reason"


# --- ikony (souřadnice 24 × 24 jako v levé liště) -------------------------------------


def _open(p: QPainterPath) -> None:
    _line(p, 4, 12, 18, 12)
    _poly(p, (13, 6), (19, 12), (13, 18))


def _compare(p: QPainterPath) -> None:
    _line(p, 5, 20, 5, 10)
    _line(p, 12, 20, 12, 4)
    _line(p, 19, 20, 19, 13)


def _folder(p: QPainterPath) -> None:
    _poly(p, (3.5, 6), (9, 6), (11, 8), (20.5, 8), (20.5, 19), (3.5, 19), (3.5, 6))


def _rename(p: QPainterPath) -> None:
    _poly(p, (4, 20), (8, 20), (19, 9), (15, 5), (4, 16), (4, 20))
    _line(p, 13, 7, 17, 11)


def _delete(p: QPainterPath) -> None:
    _line(p, 4, 7, 20, 7)
    _poly(p, (9, 7), (9, 4), (15, 4), (15, 7))
    _poly(p, (6, 7), (7, 20), (17, 20), (18, 7))


def _play(p: QPainterPath) -> None:
    _poly(p, (8, 5), (19, 12), (8, 19), (8, 5))


def _copy(p: QPainterPath) -> None:
    _poly(p, (9, 9), (20, 9), (20, 20), (9, 20), (9, 9))
    _poly(p, (15, 9), (15, 4), (4, 4), (4, 15), (9, 15))


def _detail(p: QPainterPath) -> None:
    _poly(p, (5, 3), (19, 3), (19, 21), (5, 21), (5, 3))
    _line(p, 8, 8, 16, 8)
    _line(p, 8, 12, 16, 12)
    _line(p, 8, 16, 13, 16)


def _settings(p: QPainterPath) -> None:
    p.addEllipse(QPointF(12, 12), 3, 3)
    for x1, y1, x2, y2 in (
        (12, 3, 12, 6),
        (12, 18, 12, 21),
        (3, 12, 6, 12),
        (18, 12, 21, 12),
        (5.6, 5.6, 7.7, 7.7),
        (16.3, 16.3, 18.4, 18.4),
        (5.6, 18.4, 7.7, 16.3),
        (16.3, 7.7, 18.4, 5.6),
    ):
        _line(p, x1, y1, x2, y2)


def _download(p: QPainterPath) -> None:
    _line(p, 12, 4, 12, 15)
    _poly(p, (7, 10), (12, 15), (17, 10))
    _line(p, 5, 20, 19, 20)


def _package(p: QPainterPath) -> None:
    _poly(p, (4, 8), (12, 4), (20, 8), (12, 12), (4, 8))
    _poly(p, (4, 8), (4, 16), (12, 20), (20, 16), (20, 8))
    _line(p, 12, 12, 12, 20)


def _quality(p: QPainterPath) -> None:
    _line(p, 4, 19, 20, 19)
    _poly(p, (5, 15), (9, 10), (13, 13), (19, 6))


def _exit(p: QPainterPath) -> None:
    _poly(p, (10, 4), (5, 4), (5, 20), (10, 20))
    _poly(p, (14, 8), (18, 12), (14, 16))
    _line(p, 18, 12, 9, 12)


def _fill(p: QPainterPath) -> None:
    _line(p, 4, 6, 20, 6)
    _line(p, 4, 11, 14, 11)
    _line(p, 4, 16, 10, 16)
    _poly(p, (13, 20), (13.5, 17.5), (19, 12), (21, 14), (15.5, 19.5), (13, 20))


def _remove(p: QPainterPath) -> None:
    p.addEllipse(QPointF(12, 12), 8, 8)
    _line(p, 8, 12, 16, 12)


def _add(p: QPainterPath) -> None:
    _line(p, 12, 5, 12, 19)
    _line(p, 5, 12, 19, 12)


def _save(p: QPainterPath) -> None:
    _poly(p, (4, 4), (16, 4), (20, 8), (20, 20), (4, 20), (4, 4))
    _poly(p, (8, 20), (8, 14), (16, 14), (16, 20))
    _line(p, 8, 4, 8, 8)


def _undo(p: QPainterPath) -> None:
    _poly(p, (9, 5), (5, 9), (9, 13))
    p.moveTo(5, 9)
    p.lineTo(15, 9)
    p.quadTo(20, 9, 20, 14)
    p.quadTo(20, 19, 15, 19)
    p.lineTo(10, 19)


def _close(p: QPainterPath) -> None:
    _line(p, 6, 6, 18, 18)
    _line(p, 18, 6, 6, 18)


def _group(p: QPainterPath) -> None:
    _line(p, 4, 5, 20, 5)
    _poly(p, (5, 9), (5, 19))
    _line(p, 9, 10, 20, 10)
    _line(p, 9, 14, 20, 14)
    _line(p, 9, 18, 20, 18)


ICONS: dict[str, Callable[[QPainterPath], None]] = {
    "group": _group,
    "open": _open,
    "compare": _compare,
    "folder": _folder,
    "rename": _rename,
    "delete": _delete,
    "play": _play,
    "copy": _copy,
    "detail": _detail,
    "settings": _settings,
    "download": _download,
    "package": _package,
    "quality": _quality,
    "exit": _exit,
    "fill": _fill,
    "remove": _remove,
    "add": _add,
    "save": _save,
    "undo": _undo,
    "close": _close,
}


def draw_menu_icon(painter: QPainter, name: str, rect: QRectF, color: QColor | str) -> None:
    """Čárová ikona nabídky (nebo stránky z levé lišty, např. „users“, „table“)."""
    build = ICONS.get(name) or NAV_ICONS.get(name)
    if build is None:
        return
    path = QPainterPath()
    build(path)
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 24, rect.height() / 24)
    pen = QPen(QColor(color), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.restore()


def _sentence(text: str) -> str:
    """Důvod jako věta: velké první písmeno a tečka (kontroly vracejí „vyberte …“)."""
    text = text.strip()
    if not text:
        return ""
    text = text[:1].upper() + text[1:]
    return text if text[-1] in ".!?…" else text + "."


def _plain(text: str) -> str:
    """Text položky bez zkratky za tabulátorem a bez & (mnemonika)."""
    text = text.partition("\t")[0]
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


class _Note(QWidget):
    """Řádek bez akce: nadpis výběru nahoře, nebo důvod pod nedostupnou položkou."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QVBoxLayout(self)
        self.label = QLabel()
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * (0.82 if kind == "section" else 0.9)))
        if kind == "section":
            font.setWeight(QFont.Weight.DemiBold)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.7)
            layout.setContentsMargins(10, 6, 10, 2)
            color = FAINT
        else:
            self.label.setWordWrap(True)
            layout.setContentsMargins(TEXT_LEFT, 0, 12, 7)
            color = REASON
        self.label.setFont(font)
        self.label.setStyleSheet(f"color: {color}; background: transparent;")
        layout.addWidget(self.label)

    def set_text(self, text: str) -> None:
        if self.kind == "section":
            self.label.setText(text.upper())
            return
        self.label.setText(text)
        width = self.label.fontMetrics().averageCharWidth() * REASON_CHARS
        self.label.setFixedWidth(width)
        self.label.setFixedHeight(self.label.heightForWidth(width))


class Menu(QMenu):
    """Nabídka podoby karty; položky přes `add`, `add_menu`, `section`, `set_reason`."""

    def __init__(self, parent: QWidget | None = None, title: str = "") -> None:
        super().__init__(title, parent)
        self.setObjectName(OBJECT_NAME)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setContentsMargins(SHADOW + PAD, SHADOW + PAD - 2, SHADOW + PAD, SHADOW + PAD + 2)
        self.setToolTipsVisible(True)
        self._reasons: dict[QAction, QWidgetAction] = {}
        self.aboutToShow.connect(self._sync_reasons)

    # --- skládání ---------------------------------------------------------------------

    def add(
        self,
        text: str,
        slot: Callable[[], object] | None = None,
        *,
        icon: str = "",
        key: str = "",
        danger: bool = False,
        enabled: bool = True,
        reason: str = "",
        tip: str = "",
    ) -> QAction:
        """Položka; `key` = zkratka jen k zobrazení, `reason` = proč nejde (pak nedostupná)."""
        action = self.addAction(text + (f"\t{key}" if key else ""))
        action.setProperty(ICON_PROP, icon)
        action.setProperty(DANGER_PROP, danger)
        if slot is not None:
            action.triggered.connect(lambda _checked=False: slot())
        if tip:
            action.setToolTip(tip)
        action.setEnabled(enabled and not reason)
        if reason:
            self.set_reason(action, reason)
        return action

    def add_menu(self, text: str, icon: str = "") -> Menu:
        sub = Menu(self, text)
        action = self.addMenu(sub)
        action.setProperty(ICON_PROP, icon)
        return sub

    def section(self, text: str) -> QWidgetAction:
        """Šedý nadpis velkými písmeny (např. „vybrány 3 běhy“), nedá se vybrat."""
        note = _Note("section")
        note.set_text(text)
        action = QWidgetAction(self)
        action.setDefaultWidget(note)
        action.setEnabled(False)
        self.addAction(action)
        return action

    def set_reason(self, action: QAction, reason: str) -> None:
        """Důvod pod položkou; ukáže se, jen když je položka nedostupná."""
        reason = _sentence(reason)
        action.setProperty(REASON_PROP, reason)
        action.setToolTip(reason)
        row = self._reasons.get(action)
        if row is None:
            row = QWidgetAction(self)
            row.setDefaultWidget(_Note("reason"))
            row.setEnabled(False)
            actions = self.actions()
            index = actions.index(action) if action in actions else -1
            after = actions[index + 1] if 0 <= index < len(actions) - 1 else None
            self.insertAction(after, row) if after is not None else self.addAction(row)
            self._reasons[action] = row
        self._sync_reasons()

    def reason_visible(self, action: QAction) -> bool:
        row = self._reasons.get(action)
        return row is not None and row.isVisible()

    def _sync_reasons(self) -> None:
        for action, row in self._reasons.items():
            reason = str(action.property(REASON_PROP) or "")
            note = row.defaultWidget()
            if isinstance(note, _Note):
                note.set_text(reason)
            row.setVisible(bool(reason) and action.isVisible() and not action.isEnabled())

    # --- poloha a kreslení ------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        # karta (ne okno se stínem) začíná tam, kam Qt nabídku umístil; pod tlačítkem
        # a pod lištou nabídek se posune jen vodorovně, mezera nahoře zůstane
        parent = self.parentWidget()
        attached = isinstance(parent, QAbstractButton | QMenuBar)
        self.move(self.x() - SHADOW, self.y() - (0 if attached else SHADOW))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(SHADOW, SHADOW - 2, -SHADOW, -SHADOW - 2)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(SHADOW, 0, -1):  # měkký stín z průsvitných vrstev, víc dole
            alpha = int(30 * (1 - i / (SHADOW + 1)) ** 2)
            p.setBrush(QColor(15, 23, 42, alpha))
            p.drawRoundedRect(card.adjusted(-i, -i + 3, i, i + 3), RADIUS + i, RADIUS + i)
        p.setBrush(QColor(theme.CARD))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        for action in self.actions():
            if not action.isVisible() or isinstance(action, QWidgetAction):
                continue
            rect = QRectF(self.actionGeometry(action))
            if rect.isEmpty():
                continue
            if action.isSeparator():
                y = rect.center().y()
                p.setPen(QPen(QColor(theme.BORDER), 1))
                p.drawLine(QPointF(card.left() + PAD + 4, y), QPointF(card.right() - PAD - 4, y))
                continue
            self._paint_item(p, action, rect)
        p.end()

    def _paint_item(self, p: QPainter, action: QAction, rect: QRectF) -> None:
        enabled = action.isEnabled()
        danger = bool(action.property(DANGER_PROP))
        hot = enabled and action is self.activeAction()
        row = rect.adjusted(0, 1, 0, -1)
        if hot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.MISSING_SOFT if danger else HOVER))
            p.drawRoundedRect(row, 6, 6)
        if not enabled:
            color, icon_color = FAINT, ICON_OFF
        elif danger:
            color = icon_color = theme.MISSING
        elif hot:
            color = icon_color = theme.ACCENT
        else:
            color, icon_color = theme.TEXT, ICON_COLOR
        icon_box = QRectF(row.left() + ICON_LEFT, row.center().y() - 8, 16, 16)
        if action.isCheckable() and action.isChecked():
            p.setPen(QColor(theme.ACCENT if enabled else FAINT))
            p.drawText(icon_box, int(Qt.AlignmentFlag.AlignCenter), "✓")
        elif action.property(ICON_PROP):
            draw_menu_icon(p, str(action.property(ICON_PROP)), icon_box, icon_color)
        key = action.text().partition("\t")[2]
        right = 28
        p.setFont(self.font())
        fm = p.fontMetrics()
        if key:
            p.setPen(QColor(FAINT))
            p.drawText(
                row.adjusted(0, 0, -12, 0),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                key,
            )
            right = 12 + fm.horizontalAdvance(key) + 12
        text_rect = row.adjusted(TEXT_LEFT, 0, -right, 0)
        p.setPen(QColor(color))
        text = fm.elidedText(
            _plain(action.text()), Qt.TextElideMode.ElideRight, int(text_rect.width())
        )
        p.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        if action.menu() is not None:
            pen = QPen(QColor(icon_color), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            cx, cy = row.right() - 14, row.center().y()
            path = QPainterPath()
            _poly(path, (cx - 2, cy - 4), (cx + 2, cy), (cx - 2, cy + 4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
