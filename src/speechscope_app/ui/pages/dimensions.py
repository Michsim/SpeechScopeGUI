"""Stránka Dimenze: které feature patří do které dimenze pavouka (1. 11. 2026).

Mockup https://claude.ai/artifact/XZFXixJuPiT6wmPctQTLV9 (`docs/mockups/dimenze/` mimo git).
Vlevo dimenze v pořadí os pavouka (tažením), vpravo položky vybrané: zapnuto, feature,
úlohy, směr patologie, logaritmus a ve kolika normách feature je. Úpravy jsou rozpracované,
dokud se neuloží; uložení zapíše `settings.dimensions_file()` a pošle dimenze stránce
Pacienti (`saved`), nic se nepřepočítává. Bez rozšířeného režimu jen ke čtení.
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend import tasks
from ...backend.dimensions import (
    BOTH,
    HIGHER,
    LOWER,
    Dimension,
    Item,
    copy_dimensions,
    dump_dimensions,
    dumps_dimensions,
    load_dimensions,
    new_key,
    problems,
    save_dimensions,
)
from ...backend.library import FeatureInfo, Library, LibraryError
from ...backend.norms import Norm
from ...i18n import tr
from .. import theme
from ..feature_detail import split_unit
from ..recording_detail import column_index
from ..widgets.clean_table import cell, fit_widget_column, set_text, style_table
from ..widgets.inputs import Segmented, ToggleSwitch

KEY_ROLE = Qt.ItemDataRole.UserRole + 1
LIST_ROW = 54
CHIP_STYLE = (
    "QLabel { background: #eef2f7; color: #374151; border: 1px solid #e5e7eb;"
    " border-radius: 4px; padding: 0px 6px; }"
)
TASK_STYLE = (
    "QPushButton { border: 1px solid #e5e7eb; border-radius: 10px; padding: 1px 8px;"
    f" color: {theme.MUTED}; background: white; min-height: 0px; }}"
    f"QPushButton:checked {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};"
    " border-color: transparent; font-weight: 600; }"
    "QPushButton:disabled { color: #9ca3af; }"
)
COL_USE, COL_FEATURE, COL_TASKS, COL_DIRECTION, COL_LOG, COL_NORMS, COL_REMOVE = range(7)


def _task_chips() -> dict[str, str]:
    """Krátké popisky úloh ve štítcích tabulky (celý název v tooltipu)."""
    return {"spontaneous": tr("Spont.")}


def _direction_names() -> list[str]:
    return [tr("vyšší"), tr("nižší"), tr("obojí")]


DIRECTIONS = (HIGHER, LOWER, BOTH)


class _DimensionDelegate(QStyledItemDelegate):
    """Řádek seznamu dimenzí: ⋮⋮, tučně název (vypnutá přeškrtnutá), pod ním souhrn,
    vpravo štítek (upraveno, chybí v normě, vypnuto)."""

    def __init__(self, page: DimensionsPage) -> None:
        super().__init__(page)
        self.page = page

    def sizeHint(self, option, index) -> QSize:  # noqa: ANN001, N802
        return QSize(option.rect.width(), LIST_ROW)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        dim = self.page.dimension(index.data(KEY_ROLE))
        if dim is None:
            return
        painter.save()
        rect = option.rect.adjusted(4, 2, -4, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT_SOFT))
            painter.drawRoundedRect(QRectF(rect), 6, 6)
        x = rect.left() + 8
        if self.page.editable():
            painter.setPen(QColor("#9ca3af"))
            painter.drawText(
                QRect(x, rect.top(), 14, rect.height()), Qt.AlignmentFlag.AlignVCenter, "⋮⋮"
            )
            x += 18
        badge, role = self.page.badge(dim)
        badge_w = 0
        font = QFont(option.font)
        if badge:
            small = QFont(option.font)
            small.setPointSizeF(option.font.pointSizeF() * 0.85)
            painter.setFont(small)
            badge_w = painter.fontMetrics().horizontalAdvance(badge) + 14
            box = QRectF(rect.right() - badge_w - 6, rect.center().y() - 10, badge_w, 20)
            colors = {
                "accent": (theme.ACCENT_SOFT, theme.ACCENT),
                "warn": ("#fef3c7", "#b45309"),
                "neutral": ("#f3f4f6", theme.MUTED),
            }[role]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors[0]))
            painter.drawRoundedRect(box, 10, 10)
            painter.setPen(QColor(colors[1]))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, badge)
        width = rect.right() - x - badge_w - 12
        font.setBold(True)
        font.setStrikeOut(not dim.enabled)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT if dim.enabled else "#9ca3af"))
        top = QRect(x, rect.top() + 6, width, rect.height() // 2 - 4)
        name = painter.fontMetrics().elidedText(
            dim.display_name or dim.key, Qt.TextElideMode.ElideRight, width
        )
        painter.drawText(top, Qt.AlignmentFlag.AlignVCenter, name)
        small = QFont(option.font)
        small.setPointSizeF(option.font.pointSizeF() * 0.9)
        painter.setFont(small)
        painter.setPen(QColor(theme.MUTED))
        bottom = QRect(x, rect.center().y() + 1, width, rect.height() // 2 - 6)
        summary = painter.fontMetrics().elidedText(
            self.page.summary(dim), Qt.TextElideMode.ElideRight, width
        )
        painter.drawText(bottom, Qt.AlignmentFlag.AlignVCenter, summary)
        painter.restore()


class AxisPreview(QWidget):
    """Náhled os pavouka: zapnuté dimenze v pořadí, vybraná modře."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._names: list[str] = []
        self._current = -1
        self.setFixedSize(320, 176)

    def set_axes(self, names: list[str], current: int) -> None:
        self._names, self._current = names, current
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2 + 2)
        radius = 52.0
        p.setPen(QPen(QColor("#e5e7eb"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(center, radius, radius)
        p.drawEllipse(center, radius / 2, radius / 2)
        n = len(self._names)
        small = QFont(self.font())
        small.setPointSizeF(self.font().pointSizeF() * 0.8)
        for i, name in enumerate(self._names):
            angle = -math.pi / 2 + 2 * math.pi * i / max(n, 1)
            end = QPointF(
                center.x() + radius * math.cos(angle), center.y() + radius * math.sin(angle)
            )
            on = i == self._current
            p.setPen(QPen(QColor(theme.ACCENT if on else "#d1d5db"), 2.5 if on else 1))
            p.drawLine(center, end)
            label_at = QPointF(
                center.x() + (radius + 12) * math.cos(angle),
                center.y() + (radius + 10) * math.sin(angle),
            )
            font = QFont(small)
            font.setBold(on)
            p.setFont(font)
            p.setPen(QColor(theme.ACCENT if on else theme.MUTED))
            text = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 105)
            w = p.fontMetrics().horizontalAdvance(text)
            cos = math.cos(angle)
            x = label_at.x() - (w / 2 if abs(cos) < 0.3 else (0 if cos > 0 else w))
            p.drawText(QPointF(x, label_at.y() + 4), text)


class FeaturePickerDialog(QDialog):
    """Přidat feature: hledání v katalogu knihovny po skupinách, filtr úlohy, u sloupce
    v jaké dimenzi už je a ve kolika normách."""

    def __init__(
        self,
        catalog: list[FeatureInfo],
        used: dict[str, list[str]],
        coverage,  # noqa: ANN001  (sloupec, úlohy) → (n, m, tooltip)
        dimension_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Přidat feature do dimenze {name}").format(name=dimension_name))
        self.resize(640, 560)
        self._catalog = catalog
        self._used = used
        self._coverage = coverage
        self.chosen: tuple[str, tuple[str, ...]] | None = None
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat feature nebo popis"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._fill())
        layout.addWidget(self.search)
        self.ALL = tr("všechny úlohy")
        self.task_filter = Segmented([self.ALL, *[tasks.label(t) for t in contract.TASKS]])
        self.task_filter.currentChanged.connect(lambda _v: self._fill())
        layout.addWidget(self.task_filter)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([tr("feature"), tr("už v dimenzi"), tr("v normách")])
        self.tree.setRootIsDecorated(False)
        self.tree.itemDoubleClicked.connect(lambda item, _c: self._accept_item(item))
        self.tree.currentItemChanged.connect(lambda *_a: self._update_ok())
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        layout.addWidget(self.tree, 1)
        self.note = QLabel(
            tr(
                "Feature, kterou norma nemá, bude v pavouku bez hodnoty; "
                "normu je pak třeba přepočítat."
            )
        )
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(tr("Přidat"))
        buttons.accepted.connect(lambda: self._accept_item(self.tree.currentItem()))
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Zrušit"))
        layout.addWidget(buttons)
        self._fill()

    def task(self) -> str | None:
        current = self.task_filter.current()
        if current == self.ALL:
            return None
        return contract.TASKS[[tasks.label(t) for t in contract.TASKS].index(current)]

    def _fill(self) -> None:
        needle = self.search.text().strip().lower()
        task = self.task()
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for feature in self._catalog:
            if task is not None and task not in feature.tasks:
                continue
            info = column_index([feature])
            for column in feature.columns:
                ci = info.get(column)
                description = split_unit(ci.description)[0] if ci else ""
                unit = split_unit(ci.description)[1] if ci else ""
                short = ci.short if ci else column
                rest = feature.name.removeprefix(feature.group + ".")
                label = f"{rest} · {short}" if column != feature.name else rest
                if needle and needle not in column.lower() and needle not in description.lower():
                    continue
                group = groups.get(feature.group)
                if group is None:
                    group = QTreeWidgetItem(
                        [contract.GROUP_LABELS.get(feature.group, feature.group)]
                    )
                    font = group.font(0)
                    font.setBold(True)
                    group.setFont(0, font)
                    group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    group.setForeground(0, QColor(theme.MUTED))
                    self.tree.addTopLevelItem(group)
                    groups[feature.group] = group
                feature_tasks = tuple(
                    t for t in contract.TASKS if t in feature.tasks and (task is None or t == task)
                )
                n, m, tip = self._coverage(column, feature_tasks)
                used = ", ".join(self._used.get(column, []))
                text = label + (f"  {unit}" if unit else "")
                item = QTreeWidgetItem(
                    [text, used, (f"✓ {n} z {m}" if n == m else f"⚠ {n} z {m}") if m else "–"]
                )
                item.setToolTip(0, column + (f"\n{description}" if description else ""))
                item.setToolTip(2, tip)
                if m and n < m:
                    item.setForeground(2, QColor(theme.WARN))
                elif m:
                    item.setForeground(2, QColor(theme.OK))
                item.setForeground(1, QColor(theme.MUTED))
                item.setData(0, Qt.ItemDataRole.UserRole, (column, feature_tasks))
                group.addChild(item)
        self.tree.expandAll()
        self.tree.setRootIsDecorated(False)
        self.tree.resizeColumnToContents(1)
        self.tree.resizeColumnToContents(2)
        self._update_ok()

    def _update_ok(self) -> None:
        item = self.tree.currentItem()
        self.ok.setEnabled(item is not None and item.data(0, Qt.ItemDataRole.UserRole) is not None)

    def select(self, column: str) -> bool:
        """Pro testy: vybere sloupec v seznamu."""
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                data = child.data(0, Qt.ItemDataRole.UserRole)
                if data and data[0] == column:
                    self.tree.setCurrentItem(child)
                    return True
        return False

    def _accept_item(self, item: QTreeWidgetItem | None) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if not data:
            return
        self.chosen = (data[0], tuple(data[1]) or tuple(contract.TASKS))
        self.accept()


class DimensionsPage(QWidget):
    saved = Signal(list)  # uložené dimenze (i vypnuté) → Pacienti

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._saved: list[Dimension] = []
        self._work: list[Dimension] = []
        self._path: Path | None = None
        self._library: Library | None = None
        self._catalog: list[FeatureInfo] | None = None
        self._index: dict = {}
        self._norms: list[Norm] = []
        self._advanced = False
        self._current_key = ""
        self._filling = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel(tr("Dimenze"))
        title.setObjectName("page_title")
        titles.addWidget(title)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("page_subtitle")
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.import_btn = QPushButton(tr("Import…"))
        self.import_btn.setToolTip(tr("Načíst dimenze ze souboru YAML (třeba od kolegy)"))
        self.import_btn.clicked.connect(lambda: self.import_file())
        self.export_btn = QPushButton(tr("Export…"))
        self.export_btn.setToolTip(tr("Uložit dimenze do souboru YAML"))
        self.export_btn.clicked.connect(lambda: self.export_file())
        self.reset_btn = QPushButton(tr("Obnovit výchozí…"))
        self.reset_btn.setToolTip(tr("Zahodit vlastní úpravy a vrátit dimenze aplikace"))
        self.reset_btn.clicked.connect(lambda: self.reset_defaults())
        for btn in (self.import_btn, self.export_btn, self.reset_btn):
            head.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        # pruhy: chyba čtení, neuloženo, jen ke čtení
        self.error_bar = self._bar("warn")
        self.error_text = QLabel()
        self.error_text.setWordWrap(True)
        self.error_bar.layout().addWidget(self.error_text, 1)
        layout.addWidget(self.error_bar)
        self.dirty_bar = self._bar("warn")
        self.dirty_title = QLabel()
        self.dirty_title.setStyleSheet("font-weight: 600; color: #b45309;")
        self.dirty_text = QLabel()
        self.dirty_text.setWordWrap(True)
        self.discard_btn = QPushButton(tr("Zahodit"))
        self.discard_btn.clicked.connect(lambda: self.discard())
        self.save_btn = QPushButton(tr("Uložit"))
        self.save_btn.setProperty("role", "primary")
        self.save_btn.clicked.connect(lambda: self.save())
        for w, stretch in (
            (self.dirty_title, 0),
            (self.dirty_text, 1),
            (self.discard_btn, 0),
            (self.save_btn, 0),
        ):
            self.dirty_bar.layout().addWidget(w, stretch)
        layout.addWidget(self.dirty_bar)
        self.readonly_bar = self._bar("info")
        readonly = QLabel(
            tr("<b>Jen ke čtení.</b> Upravovat dimenze jde v rozšířeném režimu (Nastavení).")
        )
        self.readonly_bar.layout().addWidget(readonly, 1)
        layout.addWidget(self.readonly_bar)

        body = QHBoxLayout()
        body.setSpacing(14)
        # vlevo seznam dimenzí
        left = QFrame()
        left.setObjectName("card")
        left.setFixedWidth(290)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 6, 4, 6)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setItemDelegate(_DimensionDelegate(self))
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; }")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.model().rowsMoved.connect(lambda *_a: self._order_from_list())
        self.list.currentItemChanged.connect(lambda cur, _prev: self._select_item(cur))
        left_layout.addWidget(self.list, 1)
        foot = QVBoxLayout()
        foot.setContentsMargins(6, 0, 6, 0)
        self.new_btn = QPushButton(tr("+ Nová dimenze"))
        self.new_btn.clicked.connect(lambda: self.new_dimension())
        foot.addWidget(self.new_btn, 0, Qt.AlignmentFlag.AlignLeft)
        order = QLabel(tr("pořadí = osy pavouka, mění se tažením"))
        order.setObjectName("muted")
        foot.addWidget(order)
        left_layout.addLayout(foot)
        body.addWidget(left)

        # vpravo detail dimenze
        right = QFrame()
        right.setObjectName("card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(10)
        names = QHBoxLayout()
        names.setSpacing(12)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr("název dimenze"))
        self.name_edit.textEdited.connect(lambda text: self._name_edited(text))
        self.name_en_edit = QLineEdit()
        self.name_en_edit.setPlaceholderText(tr("anglicky"))
        self.name_en_edit.textEdited.connect(lambda text: self._name_en_edited(text))
        self.enabled_switch = ToggleSwitch(True)
        self.enabled_switch.toggled.connect(lambda on: self._dimension_enabled(on))
        names.addWidget(self._field(tr("Název"), self.name_edit), 2)
        names.addWidget(self._field(tr("Anglicky"), self.name_en_edit), 1)
        names.addWidget(self.enabled_switch, 0, Qt.AlignmentFlag.AlignBottom)
        self.enabled_label = QLabel(tr("v pavouku"))
        self.enabled_label.setObjectName("muted")
        names.addWidget(self.enabled_label, 0, Qt.AlignmentFlag.AlignBottom)
        names.addStretch(1)
        self.delete_btn = QPushButton(tr("Smazat dimenzi…"))
        self.delete_btn.clicked.connect(lambda: self.delete_dimension())
        names.addWidget(self.delete_btn, 0, Qt.AlignmentFlag.AlignBottom)
        right_layout.addLayout(names)

        self.table = QTableWidget()
        style_table(
            self.table,
            [
                tr("použít"),
                tr("feature"),
                tr("z úloh"),
                tr("horší je"),
                tr("log"),
                tr("v normách"),
                "",
            ],
            row_height=46,
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        right_layout.addWidget(self.table, 1)
        add_row = QHBoxLayout()
        self.add_btn = QPushButton(tr("+ Přidat feature…"))
        self.add_btn.clicked.connect(lambda: self.add_item())
        add_row.addWidget(self.add_btn)
        hint = QLabel(
            tr(
                "Položky dimenze se zprůměrují (z-skóre) a dimenze se "
                "standardizuje proti kontrolám."
            )
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        add_row.addWidget(hint, 1)
        right_layout.addLayout(add_row)
        preview_row = QHBoxLayout()
        self.preview = AxisPreview()
        preview_row.addWidget(self.preview)
        preview_text = QLabel(
            tr(
                "<b>Osy pavouka</b><br>Zapnuté dimenze v tomto pořadí; vybraná je modře. "
                "Uložení se projeví u všech pacientů hned, z-skóre se počítá při zobrazení."
            )
        )
        preview_text.setObjectName("muted")
        preview_text.setWordWrap(True)
        preview_row.addWidget(preview_text, 1)
        right_layout.addLayout(preview_row)
        body.addWidget(right, 1)
        layout.addLayout(body, 1)
        self._update_bars()

    # --- stavba -----------------------------------------------------------------------

    @staticmethod
    def _bar(role: str) -> QFrame:
        frame = QFrame()
        bg = "#fef3c7" if role == "warn" else theme.ACCENT_SOFT
        frame.setObjectName("dims_bar")
        frame.setStyleSheet(f"QFrame#dims_bar {{ background: {bg}; border-radius: 8px; }}")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)
        return frame

    @staticmethod
    def _field(label: str, widget: QWidget) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        caption = QLabel(label.upper())
        caption.setObjectName("muted")
        col.addWidget(caption)
        col.addWidget(widget)
        return box

    # --- vstupy z hlavního okna ---------------------------------------------------------

    def set_source(self, dimensions: list[Dimension], path: Path | None, error: str = "") -> None:
        """Platné dimenze a soubor, kam se ukládají; `error` = vlastní soubor nešel přečíst."""
        self._path = path
        self._saved = copy_dimensions(dimensions)
        self._work = copy_dimensions(dimensions)
        self.error_text.setText(
            tr("Vlastní dimenze nejdou přečíst, platí výchozí: {e}").format(e=error)
            if error
            else ""
        )
        self.error_bar.setVisible(bool(error))
        self._fill_list(self._current_key)

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self._catalog = None

    def set_norms(self, norms: list[Norm]) -> None:
        self._norms = list(norms)
        self._fill_list(self._current_key)

    def set_advanced(self, advanced: bool) -> None:
        self._advanced = advanced
        self.list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
            if advanced
            else QAbstractItemView.DragDropMode.NoDragDrop
        )
        for w in (self.import_btn, self.reset_btn, self.new_btn, self.delete_btn, self.add_btn):
            w.setVisible(advanced)
        for w in (self.name_edit, self.name_en_edit, self.enabled_switch):
            w.setEnabled(advanced)
        self._fill_list(self._current_key)

    def editable(self) -> bool:
        return self._advanced

    # --- dotazy (delegát, testy) --------------------------------------------------------

    def dimensions(self) -> list[Dimension]:
        return self._work

    def dimension(self, key: str | None) -> Dimension | None:
        return next((d for d in self._work if d.key == key), None)

    def current(self) -> Dimension | None:
        return self.dimension(self._current_key)

    def catalog(self) -> list[FeatureInfo]:
        if self._catalog is None:
            try:
                self._catalog = self._library.features() if self._library is not None else []
            except LibraryError:
                self._catalog = []
            self._index = column_index(self._catalog)
        return self._catalog

    def summary(self, dim: Dimension) -> str:
        items = [i for i in dim.items if i.enabled]
        n = len(items)
        text = (
            tr("1 položka")
            if n == 1
            else (tr("{n} položky").format(n=n) if 1 < n < 5 else tr("{n} položek").format(n=n))
        )
        used = sorted({t for i in items for t in i.tasks}, key=tasks.task_order)
        if used:
            text += " · " + ", ".join(tasks.label(t) for t in used)
        return text

    def badge(self, dim: Dimension) -> tuple[str, str]:
        if not dim.enabled:
            return tr("vypnuto"), "neutral"
        if dim.key in self.changed_keys():
            return tr("upraveno"), "accent"
        missing = self.norms_missing(dim)
        if missing:
            return tr("normy: {n} bez").format(n=missing), "warn"
        return "", ""

    def coverage(self, column: str, item_tasks: tuple[str, ...]) -> tuple[int, int, str]:
        """Ve kolika normách je sloupec u některé z úloh (vlastní úloha podle základní)."""
        lines, n = [], 0
        for norm in self._norms:
            ok = any(
                norm.has_column(t, column) for t in norm.tasks if tasks.base_task(t) in item_tasks
            )
            n += ok
            lines.append(f"{norm.info.display_name}: " + (tr("ano") if ok else tr("chybí")))
        return n, len(self._norms), "\n".join(lines)

    def norms_missing(self, dim: Dimension) -> int:
        """Kolik norem nemá aspoň jednu zapnutou položku dimenze."""
        missing = 0
        for norm in self._norms:
            for item in dim.items:
                if not item.enabled:
                    continue
                if not any(
                    norm.has_column(t, item.column)
                    for t in norm.tasks
                    if tasks.base_task(t) in item.tasks
                ):
                    missing += 1
                    break
        return missing

    def changed_keys(self) -> set[str]:
        saved = {d["key"]: d for d in dump_dimensions(self._saved)["dimensions"]}
        work = {d["key"]: d for d in dump_dimensions(self._work)["dimensions"]}
        return {k for k in set(saved) | set(work) if saved.get(k) != work.get(k)}

    def change_count(self) -> int:
        """Počet změn: změněné, přidané a smazané dimenze, plus pořadí os."""
        count = len(self.changed_keys())
        same = [d.key for d in self._saved if self.dimension(d.key) is not None]
        work = [d.key for d in self._work if d.key in same]
        if same != work:
            count += 1
        return count

    def dirty(self) -> bool:
        return dump_dimensions(self._saved) != dump_dimensions(self._work)

    # --- seznam a detail ---------------------------------------------------------------

    def _fill_list(self, select: str = "") -> None:
        self._filling = True
        self.list.clear()
        for dim in self._work:
            item = QListWidgetItem()
            item.setData(KEY_ROLE, dim.key)
            item.setSizeHint(QSize(260, LIST_ROW))
            self.list.addItem(item)
        self._filling = False
        keys = [d.key for d in self._work]
        row = keys.index(select) if select in keys else (0 if keys else -1)
        self.list.setCurrentRow(row)
        if row < 0:
            self._current_key = ""
            self._fill_detail()
        self._update_bars()

    def select_dimension(self, key: str) -> bool:
        keys = [d.key for d in self._work]
        if key not in keys:
            return False
        self.list.setCurrentRow(keys.index(key))
        return True

    def _select_item(self, item: QListWidgetItem | None) -> None:
        if self._filling:
            return
        self._current_key = item.data(KEY_ROLE) if item is not None else ""
        self._fill_detail()

    def _order_from_list(self) -> None:
        order = [self.list.item(i).data(KEY_ROLE) for i in range(self.list.count())]
        by_key = {d.key: d for d in self._work}
        self._work = [by_key[k] for k in order if k in by_key]
        self._changed(rebuild_table=False)

    def move_dimension(self, key: str, to: int) -> None:
        """Pro testy a klávesnici: přesune dimenzi na pozici `to`."""
        dim = self.dimension(key)
        if dim is None:
            return
        self._work.remove(dim)
        self._work.insert(max(0, min(to, len(self._work))), dim)
        self._fill_list(key)

    def _fill_detail(self) -> None:
        dim = self.current()
        for w in (self.name_edit, self.name_en_edit):
            w.blockSignals(True)
        self.enabled_switch.blockSignals(True)
        self.name_edit.setText(dim.name if dim else "")
        self.name_en_edit.setText(dim.names.get("en", "") if dim else "")
        self.enabled_switch.setChecked(dim.enabled if dim else False)
        for w in (self.name_edit, self.name_en_edit):
            w.blockSignals(False)
        self.enabled_switch.blockSignals(False)
        self.delete_btn.setEnabled(dim is not None)
        self.add_btn.setEnabled(dim is not None)
        self._fill_table()
        self._update_preview()

    def _fill_table(self) -> None:
        dim = self.current()
        table = self.table
        for r in range(table.rowCount()):
            for c in range(table.columnCount()):
                old = table.cellWidget(r, c)
                if old is not None:
                    old.hide()
                    table.removeCellWidget(r, c)
        items = dim.items if dim else []
        table.setRowCount(len(items))
        self.catalog()
        editable = self._advanced
        for row, item in enumerate(items):
            for c in range(table.columnCount()):
                set_text(table, row, c, "")
            use = ToggleSwitch(item.enabled)
            use.setEnabled(editable)
            use.toggled.connect(lambda on, i=item: self._set(i, enabled=on))
            table.setCellWidget(row, COL_USE, cell(use, transparent=False))
            table.setCellWidget(row, COL_FEATURE, self._feature_cell(item))
            table.setCellWidget(row, COL_TASKS, self._tasks_cell(item, editable))
            if editable:
                seg = Segmented(
                    _direction_names(), _direction_names()[DIRECTIONS.index(item.direction)]
                )
                seg.currentChanged.connect(
                    lambda v, i=item: self._set(
                        i, direction=DIRECTIONS[_direction_names().index(v)]
                    )
                )
                table.setCellWidget(row, COL_DIRECTION, cell(seg, transparent=False))
            else:
                set_text(
                    table, row, COL_DIRECTION, _direction_names()[DIRECTIONS.index(item.direction)]
                )
            log = ToggleSwitch(item.log)
            log.setEnabled(editable)
            log.toggled.connect(lambda on, i=item: self._set(i, log=on))
            table.setCellWidget(row, COL_LOG, cell(log, transparent=False))
            n, m, tip = self.coverage(item.column, item.tasks)
            if m:
                text = (tr("✓ ve {n} z {m}") if n == m else tr("⚠ v {n} z {m}")).format(n=n, m=m)
            else:
                text = tr("žádná norma")
            norms = set_text(table, row, COL_NORMS, text, tooltip=tip)
            norms.setForeground(
                QColor(theme.OK if m and n == m else theme.WARN if m else theme.MUTED)
            )
            remove = QToolButton()
            remove.setText("✕")
            remove.setAutoRaise(True)
            remove.setToolTip(tr("Odebrat položku"))
            remove.clicked.connect(lambda _=False, i=item: self.remove_item(i))
            remove.setVisible(editable)
            table.setCellWidget(row, COL_REMOVE, cell(remove, transparent=False))
            if not item.enabled:
                for c in (COL_FEATURE, COL_TASKS):
                    w = table.cellWidget(row, c)
                    if w is not None:
                        w.setEnabled(False)
        header = table.horizontalHeader()
        # sloupce s widgety: ResizeToContents widgety nevidí a štítky by se usekly;
        # feature podle obsahu, volné místo dostane sloupec norem
        for c in (COL_USE, COL_FEATURE, COL_TASKS, COL_DIRECTION, COL_LOG, COL_REMOVE):
            fit_widget_column(table, c, extra=12)
        header.setSectionResizeMode(COL_NORMS, QHeaderView.ResizeMode.Stretch)
        table.setColumnHidden(COL_USE, not editable)
        table.setColumnHidden(COL_REMOVE, not editable)

    def _feature_cell(self, item: Item) -> QWidget:
        info = self._index.get(item.column)
        if info is None:
            label = QLabel(f"<b>{item.column}</b>")
            missing = QLabel(tr("není v katalogu knihovny"))
            missing.setStyleSheet(f"color: {theme.WARN};")
            holder = cell(label, missing)
            holder.setToolTip(item.column)
            return holder
        feature = info.feature
        rest = feature.name.removeprefix(feature.group + ".")
        text, unit = split_unit(info.description)
        if unit in ("-", "–"):
            unit = ""  # bezrozměrné
        widgets: list[QWidget] = []
        if item.column != feature.name:
            chip = QLabel(rest)
            chip.setStyleSheet(CHIP_STYLE)
            widgets.append(chip)
        short = info.short if item.column != feature.name else rest
        name = QLabel(
            f"<b>{short}</b>"
            + (f" <span style='color:{theme.MUTED}'>{unit}</span>" if unit else "")
        )
        name.setTextFormat(Qt.TextFormat.RichText)
        widgets.append(name)
        holder = cell(*widgets)
        holder.setToolTip(item.column + (f"\n{text}" if text else ""))
        return holder

    def _tasks_cell(self, item: Item, editable: bool) -> QWidget:
        if not editable:
            label = QLabel(", ".join(tasks.label(t) for t in item.tasks))
            return cell(label)
        buttons: list[QWidget] = []
        for task in contract.TASKS:
            btn = QPushButton(_task_chips().get(task) or tasks.label(task))
            btn.setToolTip(tasks.label(task))
            btn.setCheckable(True)
            btn.setChecked(task in item.tasks)
            btn.setStyleSheet(TASK_STYLE)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.toggled.connect(lambda on, i=item, t=task: self._toggle_task(i, t, on))
            buttons.append(btn)
        holder = cell(*buttons, transparent=False)
        holder.layout().setSpacing(4)
        return holder

    def _update_preview(self) -> None:
        enabled = [d for d in self._work if d.enabled]
        names = [d.display_name or d.key for d in enabled]
        keys = [d.key for d in enabled]
        self.preview.set_axes(
            names, keys.index(self._current_key) if self._current_key in keys else -1
        )

    def _update_bars(self) -> None:
        dims = [d for d in self._work if d.enabled]
        n_items = sum(1 for d in dims for i in d.items if i.enabled)
        custom = self._path is not None and self._path.is_file()
        self.subtitle.setText(
            tr("{d} dimenzí · {i} položek · platí pro všechny pacienty").format(
                d=len(dims), i=n_items
            )
            + (" · " + tr("vlastní úpravy") if custom else " · " + tr("výchozí sada aplikace"))
        )
        dirty = self.dirty()
        self.dirty_bar.setVisible(dirty and self._advanced)
        if dirty:
            changed = [
                (self.dimension(k).display_name if self.dimension(k) else k)
                for k in sorted(self.changed_keys())
            ]
            n = self.change_count()
            changes = (
                tr("1 změna")
                if n == 1
                else tr("{n} změny").format(n=n)
                if 1 < n < 5
                else tr("{n} změn").format(n=n)
            )
            self.dirty_title.setText(tr("Neuloženo · {changes}").format(changes=changes))
            detail = ", ".join(changed[:4]) + ("…" if len(changed) > 4 else "")
            self.dirty_text.setText(
                (detail + ". " if detail else "")
                + tr("Uložení se projeví u všech hodnocení hned, nic se nepřepočítává.")
            )
        self.readonly_bar.setVisible(not self._advanced)

    # --- úpravy ------------------------------------------------------------------------

    def _changed(self, *, rebuild_table: bool = False) -> None:
        self.list.viewport().update()
        if rebuild_table:
            self._fill_table()
        self._update_preview()
        self._update_bars()

    def _set(self, item: Item, **values) -> None:  # noqa: ANN003
        for name, value in values.items():
            setattr(item, name, value)
        self._changed(rebuild_table="enabled" in values)

    def _toggle_task(self, item: Item, task: str, on: bool) -> None:
        current = [t for t in item.tasks if t != task]
        if on:
            current.append(task)
        item.tasks = tuple(t for t in contract.TASKS if t in current) + tuple(
            t for t in current if t not in contract.TASKS
        )
        self._changed(rebuild_table=False)
        row = self.current().items.index(item) if self.current() else -1
        if row >= 0:
            n, m, tip = self.coverage(item.column, item.tasks)
            text = (
                (tr("✓ ve {n} z {m}") if n == m else tr("⚠ v {n} z {m}")).format(n=n, m=m)
                if m
                else tr("žádná norma")
            )
            norms = set_text(self.table, row, COL_NORMS, text, tooltip=tip)
            norms.setForeground(
                QColor(theme.OK if m and n == m else theme.WARN if m else theme.MUTED)
            )

    def _name_edited(self, text: str) -> None:
        dim = self.current()
        if dim is not None:
            dim.name = text
            self._changed()

    def _name_en_edited(self, text: str) -> None:
        dim = self.current()
        if dim is not None:
            if text.strip():
                dim.names["en"] = text
            else:
                dim.names.pop("en", None)
            self._changed()

    def _dimension_enabled(self, on: bool) -> None:
        dim = self.current()
        if dim is not None:
            dim.enabled = on
            self._changed()

    def add_item(
        self, column: str | None = None, item_tasks: tuple[str, ...] | None = None
    ) -> bool:
        """Přidá položku; bez `column` se zeptá výběrem z katalogu."""
        dim = self.current()
        if dim is None:
            return False
        if column is None:
            used: dict[str, list[str]] = {}
            for d in self._work:
                for i in d.items:
                    used.setdefault(i.column, []).append(d.display_name)
            dialog = FeaturePickerDialog(
                self.catalog(), used, self.coverage, dim.display_name, self
            )
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.chosen is None:
                return False
            column, item_tasks = dialog.chosen
        dim.items.append(Item(column, tuple(item_tasks or contract.TASKS)))
        self._changed(rebuild_table=True)
        return True

    def remove_item(self, item: Item) -> None:
        dim = self.current()
        if dim is not None and item in dim.items:
            dim.items.remove(item)
            self._changed(rebuild_table=True)

    def new_dimension(self, name: str | None = None) -> str:
        if name is None:
            name, ok = QInputDialog.getText(self, tr("Nová dimenze"), tr("Název dimenze:"))
            if not ok or not name.strip():
                return ""
        key = new_key(name.strip(), {d.key for d in self._work})
        self._work.append(Dimension(key, name.strip(), []))
        self._fill_list(key)
        return key

    def delete_dimension(self, *, confirm: bool = True) -> None:
        dim = self.current()
        if dim is None:
            return
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("Smazat dimenzi"),
                tr("Smazat dimenzi {name}? Dokud neuložíte, jde to zahodit.").format(
                    name=dim.display_name
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        index = self._work.index(dim)
        self._work.remove(dim)
        keys = [d.key for d in self._work]
        self._fill_list(keys[min(index, len(keys) - 1)] if keys else "")

    # --- uložení, import, export ----------------------------------------------------------

    def save(self, *, notify: bool = True) -> bool:
        """Uloží úpravy; s chybou (`problems`) neuloží a s `notify` řekne proč."""
        issues = problems(self._work)
        self.last_problems = issues
        if issues and not notify:
            return False
        if issues:
            QMessageBox.warning(
                self,
                tr("Dimenze nejdou uložit"),
                tr("Nejdřív opravte:") + "\n\n" + "\n".join(f"• {p}" for p in issues),
            )
            return False
        if self._path is None:
            return False
        try:
            save_dimensions(self._path, self._work)
        except OSError as exc:
            QMessageBox.warning(self, tr("Dimenze nejdou uložit"), str(exc))
            return False
        self._saved = copy_dimensions(self._work)
        self._changed()
        self.saved.emit(copy_dimensions(self._saved))
        return True

    def discard(self) -> None:
        self._work = copy_dimensions(self._saved)
        self._fill_list(self._current_key)

    def reset_defaults(self, *, confirm: bool = True) -> None:
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("Obnovit výchozí dimenze"),
                tr("Vlastní úpravy dimenzí se smažou a platí dimenze aplikace. Pokračovat?"),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self._path is not None and self._path.is_file():
            try:
                self._path.unlink()
            except OSError as exc:
                QMessageBox.warning(self, tr("Obnovit výchozí dimenze"), str(exc))
                return
        defaults = load_dimensions()
        self._saved = copy_dimensions(defaults)
        self._work = copy_dimensions(defaults)
        self.error_bar.hide()
        self._fill_list(self._current_key)
        self.saved.emit(copy_dimensions(self._saved))

    def import_file(self, path: Path | None = None) -> bool:
        """Načte dimenze ze souboru jako neuložené úpravy."""
        if path is None:
            name, _ = QFileDialog.getOpenFileName(
                self, tr("Import dimenzí"), "", tr("Dimenze (*.yaml *.yml)")
            )
            if not name:
                return False
            path = Path(name)
        try:
            loaded = load_dimensions(path)
        except Exception as exc:  # noqa: BLE001  YAML, chybějící klíče, špatný směr…
            QMessageBox.warning(
                self, tr("Import dimenzí"), tr("Soubor nejde načíst: {e}").format(e=exc)
            )
            return False
        self._work = copy_dimensions(loaded)
        self._fill_list(self._current_key)
        return True

    def export_file(self, path: Path | None = None) -> Path | None:
        if path is None:
            name, _ = QFileDialog.getSaveFileName(
                self, tr("Export dimenzí"), "dimenze.yaml", tr("Dimenze (*.yaml)")
            )
            if not name:
                return None
            path = Path(name)
        path.write_text(dumps_dimensions(self._work), encoding="utf-8")
        return path
