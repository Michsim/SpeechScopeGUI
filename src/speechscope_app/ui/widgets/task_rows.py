"""Řádek úlohy a souhrnný pruh (podoba 1 z mockupu, 31. 10. 2026).

Mockup https://claude.ai/artifact/Nuh6L6PrsVcY5xAizg113o (`docs/mockups/analyza-ulohy/`
mimo git). Jeden řádek na úlohu: vypínač, ikona úlohy, název s počtem a délkou
zvuku, případně složka s nahrávkami, protokol jako dvouřádkový výběr (název a pod
ním, co stojí), stav tečkou a slovem a odhad vpravo. Používá ho krok 2 Analýzy
i průvodci Hodnocení z nahrávek a Nová norma, aby úlohy vypadaly všude stejně.
Widget nezná knihovnu; texty mu dodá stránka.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from ...backend import tasks
from ...backend.protocol import Protocol
from ...i18n import tr
from .. import theme
from .inputs import ToggleSwitch

SUB_ROLE = Qt.ItemDataRole.UserRole + 21  # druhý řádek položky výběru protokolu
ROW_ROLE_OFF = "off"  # vypnutý řádek (bez složky, vypnutý vypínač, nehodí se k režimu)
NAME_WIDTH = 235
STATUS_WIDTH = 190
STATUS_MIN = 100
PROTOCOL_MIN = 240
COUNT_WIDTH = 92

# Úroveň stavu → barva tečky
LEVEL_COLORS = {"ok": theme.OK, "warn": theme.WARN, "info": theme.ACCENT, "none": "#d1d5db"}


# --- ikony úloh (čárové jako v levé liště, kreslené v QPainter) --------------------------


def _phonation(p: QPainterPath) -> None:
    for x, h in ((3, 0), (7, 4), (11, 7), (15, 4), (19, 2)):
        p.moveTo(x, 12 - h)
        p.lineTo(x, 12 + h)
    p.moveTo(2, 12)
    p.lineTo(4, 12)


def _ddk(p: QPainterPath) -> None:
    for x in (5, 12, 19):
        p.addEllipse(QRectF(x - 2, 12, 4, 4))
        p.moveTo(x, 7)
        p.lineTo(x, 9)


def _reading(p: QPainterPath) -> None:
    p.moveTo(3, 5)
    p.lineTo(9, 5)
    p.quadTo(12, 5, 12, 8)
    p.lineTo(12, 19)
    p.quadTo(12, 17, 10, 17)
    p.lineTo(3, 17)
    p.closeSubpath()
    p.moveTo(21, 5)
    p.lineTo(15, 5)
    p.quadTo(12, 5, 12, 8)
    p.moveTo(12, 19)
    p.quadTo(12, 17, 14, 17)
    p.lineTo(21, 17)
    p.lineTo(21, 5)


def _spontaneous(p: QPainterPath) -> None:
    p.moveTo(4, 5)
    p.lineTo(20, 5)
    p.lineTo(20, 15)
    p.lineTo(9, 15)
    p.lineTo(4, 19)
    p.closeSubpath()
    p.moveTo(8, 9)
    p.lineTo(16, 9)
    p.moveTo(8, 12)
    p.lineTo(13, 12)


TASK_ICONS: dict[str, Callable[[QPainterPath], None]] = {
    "phonation": _phonation,
    "ddk": _ddk,
    "reading": _reading,
    "spontaneous": _spontaneous,
}


class TaskIcon(QWidget):
    """Čtvereček s ikonou úlohy; vlastní úloha má ikonu své základní úlohy."""

    SIZE = 34

    def __init__(self, task: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._task = task
        self._off = False
        self.setFixedSize(self.SIZE, self.SIZE)

    def set_task(self, task: str) -> None:
        self._task = task
        self.update()

    def set_off(self, off: bool) -> None:
        self._off = off
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.NEUTRAL_SOFT if self._off else theme.ACCENT_SOFT))
        p.drawRoundedRect(QRectF(0, 0, self.SIZE, self.SIZE), 9, 9)
        draw = TASK_ICONS.get(tasks.base_task(self._task)) if self._task else None
        if draw is None:
            return
        path = QPainterPath()
        draw(path)
        p.translate(8, 8)
        p.scale(18 / 24, 18 / 24)
        pen = QPen(QColor("#9ca3af" if self._off else "#1d4ed8"), 1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)


# --- výběr protokolu na dva řádky ------------------------------------------------------


class ProtocolCombo(QComboBox):
    """Rozbalovací výběr protokolu: název tučně, pod ním šedě, co stojí.

    Chová se jako QComboBox (data položky = `Protocol`), jen kreslí dva řádky.
    Druhý řádek je v `SUB_ROLE` položky; v rozbaleném seznamu je v tooltipu.
    """

    def set_protocols(
        self,
        protocols: list[Protocol],
        *,
        current: str = "",
        subs: dict[str, str] | None = None,
        tips: dict[str, str] | None = None,
    ) -> None:
        subs = subs or {}
        tips = tips or {}
        self.blockSignals(True)
        self.clear()
        ordered = sorted(protocols, key=lambda p: (not p.builtin, p.display_name.lower()))
        for proto in ordered:
            text = (
                proto.display_name
                if proto.builtin
                else tr("{name} · vlastní").format(name=proto.display_name)
            )
            self.addItem(text, proto)
            i = self.count() - 1
            self.setItemData(i, subs.get(proto.name, ""), SUB_ROLE)
            tip = tips.get(proto.name) or proto.display_description
            if tip:
                self.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
        idx = next((i for i, p in enumerate(ordered) if p.name == current), 0 if ordered else -1)
        self.setCurrentIndex(idx)
        self.blockSignals(False)
        self._sync_tooltip()
        self.update()

    def current_protocol(self) -> Protocol | None:
        data = self.currentData()
        return data if isinstance(data, Protocol) else None

    def select_name(self, name: str) -> bool:
        for i in range(self.count()):
            data = self.itemData(i)
            if isinstance(data, Protocol) and data.name == name:
                self.setCurrentIndex(i)
                return True
        return False

    def set_sub(self, name: str, text: str) -> None:
        """Druhý řádek jednoho protokolu (třeba po novém odhadu)."""
        for i in range(self.count()):
            data = self.itemData(i)
            if isinstance(data, Protocol) and data.name == name:
                self.setItemData(i, text, SUB_ROLE)
        self.update()

    def current_sub(self) -> str:
        return str(self.currentData(SUB_ROLE) or "")

    def _sync_tooltip(self) -> None:
        self.setToolTip(str(self.currentData(Qt.ItemDataRole.ToolTipRole) or ""))

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        super().setCurrentIndex(index)
        self._sync_tooltip()

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        lines = QFontMetrics(self.font()).height() * 2 + 12
        return QSize(max(hint.width(), 220), max(hint.height(), lines))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, self.sizeHint().height())

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        painter = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        opt.currentText = ""
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        rect = (
            self.style()
            .subControlRect(
                QStyle.ComplexControl.CC_ComboBox, opt, QStyle.SubControl.SC_ComboBoxEditField, self
            )
            .adjusted(4, 0, -2, 0)
        )
        name = self.currentText()
        sub = self.current_sub()
        bold = QFont(self.font())
        bold.setBold(True)
        bold_metrics = QFontMetrics(bold)
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() * 0.88))
        small_metrics = QFontMetrics(small)
        # zamčený výběr (jediný protokol, základní režim) kreslí text normálně, jinak by
        # řádek vypadal jako vypnutá úloha; zamčení říká rámeček bez šipky
        enabled = True
        if not sub:
            painter.setFont(bold)
            painter.setPen(QColor(theme.TEXT if enabled else "#9ca3af"))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                bold_metrics.elidedText(name, Qt.TextElideMode.ElideRight, rect.width()),
            )
            return
        total = bold_metrics.height() + small_metrics.height()
        top = rect.top() + (rect.height() - total) // 2
        painter.setFont(bold)
        painter.setPen(QColor(theme.TEXT if enabled else "#9ca3af"))
        painter.drawText(
            rect.left(),
            top + bold_metrics.ascent(),
            bold_metrics.elidedText(name, Qt.TextElideMode.ElideRight, rect.width()),
        )
        painter.setFont(small)
        painter.setPen(QColor(theme.MUTED if enabled else "#b6bcc6"))
        painter.drawText(
            rect.left(),
            top + bold_metrics.height() + small_metrics.ascent(),
            small_metrics.elidedText(sub, Qt.TextElideMode.ElideRight, rect.width()),
        )


# --- řádek ---------------------------------------------------------------------------


def status_html(level: str, text: str) -> str:
    """Stav tečkou a slovem: ok zelená, warn oranžová, info modrá, none šedá."""
    color = LEVEL_COLORS.get(level, LEVEL_COLORS["none"])
    return f"<span style='color:{color}'>●</span>&nbsp;{text}"


class TaskRowWidget(QFrame):
    """Jeden řádek úlohy. Volitelné části: vypínač, výběr úlohy, složka, odebrání.

    Atributy pro stránky a testy: `switch`, `icon`, `label` (nebo `task_combo`),
    `sub`, `folder` a `browse`, `protocol` (`ProtocolCombo`), `status`, `count`
    (sloupec vpravo: odhad nebo počet), `details` (odkaz „co se počítá“),
    `edit_btn` (rozšířený režim), `remove`.
    """

    toggled = Signal(bool)
    details_requested = Signal()
    edit_requested = Signal()
    clicked = Signal()

    def __init__(
        self,
        task: str,
        *,
        title: str = "",
        switch: bool = False,
        folder: bool = False,
        task_choices: list[str] | None = None,
        removable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task = task
        self.setObjectName("taskrow")
        self.setProperty("state", "")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        self.switch: ToggleSwitch | None = None
        if switch:
            self.switch = ToggleSwitch(True)
            self.switch.setToolTip(tr("Vypnutá úloha se nespustí."))
            self.switch.toggled.connect(self._switch_toggled)
            layout.addWidget(self.switch)

        self.icon = TaskIcon(task)
        layout.addWidget(self.icon)

        self.name_box = QWidget()
        self.name_box.setFixedWidth(NAME_WIDTH)
        name_layout = QVBoxLayout(self.name_box)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(1)
        self.task_combo: QComboBox | None = None
        self.label = QLabel(title or tasks.label(task))
        bold = QFont(self.label.font())
        bold.setBold(True)
        self.label.setFont(bold)
        if task_choices is not None:
            self.task_combo = QComboBox()
            for key in task_choices:
                self.task_combo.addItem(tasks.label(key), key)
            idx = self.task_combo.findData(task)
            self.task_combo.setCurrentIndex(max(idx, 0))
            self.task_combo.setToolTip(tr("Jako jakou úlohu se nahrávky počítají."))
            self.label.hide()
            name_layout.addWidget(self.task_combo)
        name_layout.addWidget(self.label)
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        name_layout.addWidget(self.sub)
        layout.addWidget(self.name_box)

        self.folder: QLineEdit | None = None
        self.browse: QPushButton | None = None
        if folder:
            self.folder = QLineEdit()
            self.folder.setPlaceholderText(tr("nevybráno, úloha se vynechá"))
            self.folder.setMinimumWidth(160)
            self.browse = QPushButton(tr("Vybrat…"))
            folder_box = QHBoxLayout()
            folder_box.setSpacing(4)
            folder_box.addWidget(self.folder, 1)
            folder_box.addWidget(self.browse)
            layout.addLayout(folder_box, 4)

        self.protocol = ProtocolCombo()
        self.protocol.setMinimumWidth(PROTOCOL_MIN)
        layout.addWidget(self.protocol, 5)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        # v užším okně stav ustoupí výběru protokolu (dřív „Fonace, zákla…“ při 1 100 px)
        self.status.setMinimumWidth(STATUS_MIN)
        self.status.setMaximumWidth(STATUS_WIDTH)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.status, 2)
        if folder:
            self.status.hide()  # v průvodcích stav říká už druhý řádek protokolu

        self.count = QLabel("")
        self.count.setFixedWidth(COUNT_WIDTH)
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.count.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.count)

        self.details = QLabel(f"<a href='#' style='color:{theme.ACCENT}'>{tr('co se počítá')}</a>")
        self.details.setToolTip(tr("Seznam feature a sloupců vybraného protokolu s popisem."))
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.details.linkActivated.connect(lambda _href: self.details_requested.emit())
        layout.addWidget(self.details)
        self.edit_btn = QPushButton(tr("Upravit…"))
        self.edit_btn.setToolTip(tr("Upraví vybraný protokol; přibalený se uloží jako kopie."))
        self.edit_btn.clicked.connect(lambda: self.edit_requested.emit())
        self.edit_btn.hide()
        layout.addWidget(self.edit_btn)

        self.remove = QPushButton("✕")
        self.remove.setFixedSize(30, 30)
        self.remove.setToolTip(tr("Odebrat vlastní úlohu z normy"))
        self.remove.setVisible(removable)
        layout.addWidget(self.remove)
        if folder:
            self.details.hide()

    # --- stav -------------------------------------------------------------------------

    def is_on(self) -> bool:
        return self.switch is None or self.switch.isChecked()

    def set_on(self, on: bool) -> None:
        if self.switch is not None and self.switch.isChecked() != on:
            self.switch.setChecked(on)

    def set_off_look(self, off: bool) -> None:
        """Šedý řádek: vypnutý, bez složky, nebo úloha nejde v daném režimu."""
        state = ROW_ROLE_OFF if off else ""
        if self.property("state") != state:
            self.setProperty("state", state)
            self.style().unpolish(self)
            self.style().polish(self)
        self.icon.set_off(off)
        color = "#9ca3af" if off else theme.TEXT
        self.label.setStyleSheet(f"color: {color};")

    def set_task(self, task: str) -> None:
        self.task = task
        self.icon.set_task(task)
        self.label.setText(tasks.label(task))

    def set_status(self, level: str, text: str, tooltip: str = "") -> None:
        self.status.setText(status_html(level, text) if text else "")
        self.status.setToolTip(tooltip or text)

    def set_count(self, main: str, sub: str = "") -> None:
        """Sloupec vpravo: hlavní číslo tučně, pod ním šedě (odhad a čas na nahrávku)."""
        if sub:
            self.count.setText(f"<b>{main}</b><br><span style='color:{theme.MUTED}'>{sub}</span>")
        else:
            self.count.setText(f"<b>{main}</b>" if main else "")

    def _switch_toggled(self, on: bool) -> None:
        self.set_off_look(not on)
        self.toggled.emit(on)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001, N802
        self.details_requested.emit()
        super().mouseDoubleClickEvent(event)


# --- souhrnný pruh -----------------------------------------------------------------------

SHARE_COLORS = ("#2563eb", "#60a5fa", "#f59e0b", "#93c5fd", "#7c3aed", "#14b8a6")


class ShareBar(QWidget):
    """Tenký pruh s podílem úloh na odhadu výpočtu."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._parts: list[tuple[float, str]] = []
        self.setFixedSize(180, 8)

    def set_parts(self, parts: list[tuple[float, str]]) -> None:
        self._parts = [(v, c) for v, c in parts if v > 0]
        self.setVisible(len(self._parts) > 1)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 1, self.width(), 6)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 3, 3)
        p.setClipPath(clip)
        p.fillRect(rect, QColor(theme.BORDER))
        total = sum(v for v, _c in self._parts) or 1.0
        x = 0.0
        for value, color in self._parts:
            w = rect.width() * value / total
            p.fillRect(QRectF(x, rect.top(), w, rect.height()), QColor(color))
            x += w
            p.fillRect(QRectF(x - 1, rect.top(), 2, rect.height()), QColor("#f8fafc"))


class SummaryBar(QFrame):
    """Pruh pod úlohami: počet běhů a nahrávek, zvuk, odhad, podíl úloh a poznámka."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("summarybar")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(14, 8, 14, 8)
        self._layout.setSpacing(16)
        self._figures: list[tuple[QLabel, QLabel]] = []
        self._dividers: list[QFrame] = []
        for i in range(3):
            if i:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.VLine)
                line.setStyleSheet(f"color: {theme.BORDER};")
                self._layout.addWidget(line)
                self._dividers.append(line)
            box = QVBoxLayout()
            box.setSpacing(0)
            value = QLabel("")
            font = QFont(value.font())
            font.setBold(True)
            font.setPointSizeF(font.pointSizeF() * 1.15)
            value.setFont(font)
            caption = QLabel("")
            caption.setObjectName("muted")
            box.addWidget(value)
            box.addWidget(caption)
            self._layout.addLayout(box)
            self._figures.append((value, caption))
        self.share = ShareBar()
        self._layout.addWidget(self.share, 0, Qt.AlignmentFlag.AlignVCenter)
        self._layout.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self._layout.addWidget(self.hint)

    def set_figures(self, figures: list[tuple[str, str]]) -> None:
        """Až tři dvojice (hodnota, popisek); chybějící se schovají."""
        for i, (value, caption) in enumerate(self._figures):
            text, cap = figures[i] if i < len(figures) else ("", "")
            value.setText(text)
            caption.setText(cap)
            value.setVisible(bool(text))
            caption.setVisible(bool(text))
            if i:
                self._dividers[i - 1].setVisible(bool(text))

    def figures(self) -> list[str]:
        """Hodnoty pruhu (pro testy)."""
        return [v.text() for v, _c in self._figures if v.text()]

    def set_hint(self, text: str) -> None:
        self.hint.setText(text)
