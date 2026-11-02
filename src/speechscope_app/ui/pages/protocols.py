"""Stránka Protokoly: přehled, kopie, import, export, smazání a úprava.

Přibalené protokoly jsou jen ke čtení, „Vytvořit kopii“ z nich udělá
vlastní. Vlastní protokol jde v rozšířeném režimu upravit stejným
editorem jako na Analýze (výběr feature, parametry, `ProtocolEditorDialog`)
a přejmenovat; v obou režimech jde importovat, exportovat a smazat, aby
klinik mohl přinést protokol od výzkumníka bez zapínání rozšířeného režimu.

Podoba A z mockupu https://claude.ai/artifact/TFhTurHqvt7kCDJMDR5HYr
(`docs/mockups/protokoly/` mimo git, 2. 11. 2026; část „Kde se používá“ uživatel
nechtěl): vlevo karty po úlohách s hledáním a filtrem původu, vpravo hlavička
s tlačítky a nabídkou ⋯, čtyři čísla, co se počítá po skupinách feature, co je
potřeba (providery se stavem z doctor) a změněné parametry. Úprava jen oknem
Upravit…, název a popis přes Přejmenovat… (uloží se hned, bez rozpracovaného stavu).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend import tasks
from ...backend.library import FeatureInfo, FeatureParams, Library, LibraryError
from ...backend.norm_build import DEFAULT_PROTOCOLS
from ...backend.protocol import Protocol, import_protocol, summarize, unique_name
from ...i18n import tr
from .. import theme
from ..file_actions import reveal_file
from ..protocol_detail import ProtocolDetailDialog
from ..widgets.clean_table import (
    fit_height,
    fit_widget_column,
    right_header,
    set_pill,
    set_text,
    style_table,
)
from ..widgets.elided_label import ElidedLabel
from ..widgets.inputs import Segmented
from ..widgets.menu import Menu
from ..widgets.protocol_editor import ProtocolEditorDialog
from ..widgets.status_header import StatusHeader

ROLE_PROTO = Qt.ItemDataRole.UserRole
YAML_FILTER = "Protokol SpeechScope (*.yaml)"
LANGUAGE_PROVIDERS = ("transcript", "nlp")
NARROW = 860  # px detailu, pod nimiž jdou karty Co se počítá a Co je potřeba pod sebe


class ProtocolCard(QFrame):
    """Karta protokolu v seznamu: název, štítek původu, sloupce · modely · čas."""

    def __init__(self, proto: Protocol, detail: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.proto = proto
        self.setObjectName("card")
        self._role = "neutral" if proto.builtin else "accent"
        theme.set_role(self, self._role)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = ElidedLabel(proto.display_name)
        self.title.setObjectName("card_title")
        head.addWidget(self.title, 1)
        self.pill = QLabel(tr("přibalený") if proto.builtin else tr("vlastní"))
        self.pill.setObjectName("pill")
        theme.set_role(self.pill, "neutral" if proto.builtin else "accent")
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        self.detail = ElidedLabel(detail)
        self.detail.setObjectName("muted")
        layout.addWidget(self.detail)

    def set_current(self, current: bool) -> None:
        theme.set_role(self, "selected" if current else self._role)


class NewProtocolDialog(QDialog):
    """Nový protokol…: úloha a název; začne jako kopie základního protokolu úlohy."""

    def __init__(
        self, base_for: Callable[[str], Protocol | None], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._base_for = base_for
        self.setWindowTitle(tr("Nový protokol"))
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.task_combo = QComboBox()
        for task in contract.TASKS:
            self.task_combo.addItem(tasks.label(task), task)
        self.task_combo.currentIndexChanged.connect(lambda _i: self._update_hint())
        form.addRow(tr("Úloha:"), self.task_combo)
        self.name_edit = QLineEdit()
        form.addRow(tr("Název:"), self.name_edit)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        form.addRow(self.hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Vytvořit a upravit…"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._update_hint()

    def task(self) -> str:
        return str(self.task_combo.currentData())

    def select_task(self, task: str) -> None:
        index = self.task_combo.findData(task)
        if index >= 0:
            self.task_combo.setCurrentIndex(index)

    def _update_hint(self) -> None:
        task = self.task()
        self.name_edit.setPlaceholderText(
            tr("Nový protokol · {task}").format(task=tasks.label(task))
        )
        base = self._base_for(task)
        self.hint.setText(
            tr(
                "Začne jako kopie protokolu „{name}“, výběr feature a parametry pak upravíte."
            ).format(name=base.display_name)
            if base is not None
            else tr("Pro tuto úlohu není přibalený protokol.")
        )


class InfoDialog(QDialog):
    """Přejmenovat…: název a popis vlastního protokolu."""

    def __init__(self, proto: Protocol, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Přejmenovat protokol"))
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.name_edit = QLineEdit(proto.name)
        form.addRow(tr("Název:"), self.name_edit)
        self.desc_edit = QPlainTextEdit(proto.description)
        self.desc_edit.setFixedHeight(80)
        form.addRow(tr("Popis:"), self.desc_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


def _section(title: str) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    """Bílá karta s nadpisem velkými písmeny; vrátí kartu, její obsah a řádek nadpisu."""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)
    head = QHBoxLayout()
    label = QLabel(title.upper())
    label.setObjectName("section_label")
    label.setStyleSheet(f"color: {theme.MUTED}; font-weight: 600; letter-spacing: 1px;")
    head.addWidget(label, 1)
    layout.addLayout(head)
    return frame, layout, head


def _table(headers: list[str], stretch: int = 0) -> QTableWidget:
    """Tabulka v kartě; sloupec `stretch` bere zbylé místo, ostatní podle obsahu."""
    table = QTableWidget()
    style_table(table, headers)
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return table


class ProtocolsPage(QWidget):
    protocols_changed = Signal()  # něco se uložilo, smazalo nebo importovalo

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self._doctor: dict[str, Any] | None = None
        self._seconds_per_file: Callable[[str], float | None] = lambda _slug: None
        self._protocols: list[Protocol] = []
        self._folder: Path | None = None
        self._advanced = False
        self._cards: list[ProtocolCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel(tr("Protokoly"))
        title.setObjectName("page_title")
        head.addWidget(title, 1)
        self.import_btn = QPushButton(tr("Import…"))
        self.import_btn.setToolTip(tr("Přidat protokol ze souboru YAML, třeba od kolegy"))
        self.import_btn.clicked.connect(lambda: self.import_file())
        self.folder_btn = QPushButton(tr("Otevřít složku"))
        self.folder_btn.setToolTip(tr("Složka s vlastními protokoly v Průzkumníku"))
        self.folder_btn.clicked.connect(lambda: self.open_folder())
        # Nový protokol… = kopie základního protokolu zvolené úlohy a hned editor
        # (2. 11. 2026, jen v rozšířeném režimu, kde jde protokol upravit)
        self.new_btn = QPushButton(tr("Nový protokol…"))
        self.new_btn.setToolTip(
            tr("Vlastní protokol pro úlohu, začne jako kopie základního protokolu úlohy")
        )
        theme.set_role(self.new_btn, "primary")
        self.new_btn.clicked.connect(lambda: self._new_clicked())
        self.new_btn.setVisible(False)  # ukáže set_advanced
        for btn in (self.new_btn, self.import_btn, self.folder_btn):
            head.addWidget(btn)
        layout.addLayout(head)
        subtitle = QLabel(
            tr(
                "Protokol je pojmenované nastavení dávky: úloha, výběr feature a parametry. "
                "Přibalené protokoly se nemění, vlastní jdou upravit, poslat kolegovi nebo smazat."
            )
        )
        subtitle.setObjectName("page_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)

        # --- vlevo: hledání, filtr původu, karty po úlohách ---
        left = QWidget()
        left.setMinimumWidth(280)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Hledat protokol…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        left_layout.addWidget(self.search)
        self.ALL, self.BUILTIN, self.CUSTOM = tr("vše"), tr("přibalené"), tr("vlastní")
        self.origin_filter = Segmented([self.ALL, self.BUILTIN, self.CUSTOM])
        self.origin_filter.currentChanged.connect(lambda _v: self._apply_filter())
        left_layout.addWidget(self.origin_filter, 0, Qt.AlignmentFlag.AlignLeft)
        self.list = QListWidget()
        self.list.setObjectName("cards")
        self.list.setSpacing(3)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._current_changed)
        self.list.itemDoubleClicked.connect(
            lambda item: self.show_protocol_detail(item.data(ROLE_PROTO))
        )
        self.list.setToolTip(tr("Dvojklik ukáže, co protokol počítá."))
        self.list.viewport().installEventFilter(self)
        left_layout.addWidget(self.list, 1)
        self.empty = QLabel(tr("Žádný protokol neodpovídá hledání."))
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        left_layout.addWidget(self.empty)
        self.splitter.addWidget(left)

        # --- vpravo: detail v posuvné ploše ---
        detail = QWidget()
        detail.setObjectName("protocol_detail")
        detail.setStyleSheet("#protocol_detail { background: transparent; }")
        right = QVBoxLayout(detail)
        right.setContentsMargins(12, 0, 0, 0)
        right.setSpacing(12)

        self.header = StatusHeader()
        self.edit_btn = QPushButton(tr("Upravit…"))
        self.edit_btn.setToolTip(tr("Výběr feature a parametry protokolu"))
        theme.set_role(self.edit_btn, "primary")
        self.edit_btn.clicked.connect(lambda: self.open_editor())
        self.copy_btn = QPushButton(tr("Vytvořit kopii"))
        self.copy_btn.setToolTip(tr("Nový vlastní protokol podle vybraného"))
        self.copy_btn.clicked.connect(lambda: self.copy_current())
        self.export_btn = QPushButton(tr("Export…"))
        self.export_btn.setToolTip(tr("Protokol jako soubor YAML pro kolegu"))
        self.export_btn.clicked.connect(lambda: self.export_current())
        self.more_btn = QPushButton("⋯")
        self.more_btn.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0px; }")
        self.more_btn.setToolTip(tr("Další akce"))
        self.more_menu = Menu(self.more_btn)
        self.rename_action = self.more_menu.add(
            tr("Přejmenovat…"), lambda: self.rename_current(), icon="rename"
        )
        self.file_action = self.more_menu.add(
            tr("Zobrazit soubor"), lambda: self._reveal_current(), icon="folder"
        )
        self.more_menu.addSeparator()
        self.delete_action = self.more_menu.add(
            tr("Smazat…"), lambda: self.delete_current(), icon="delete", danger=True
        )
        self.more_btn.setMenu(self.more_menu)
        for btn in (self.edit_btn, self.copy_btn, self.export_btn, self.more_btn):
            btn.setMinimumWidth(btn.sizeHint().width())
            self.header.add_button(btn)
        right.addWidget(self.header)

        # čtyři čísla
        self.facts = QFrame()
        self.facts.setObjectName("card")
        facts = self.facts_grid = QGridLayout(self.facts)
        facts.setContentsMargins(14, 10, 14, 10)
        facts.setHorizontalSpacing(24)
        self.fact_values: list[QLabel] = []
        self.fact_captions: list[QLabel] = []
        for col in range(4):
            value = QLabel("")
            value.setStyleSheet("font-size: 15pt; font-weight: 600;")
            caption = QLabel("")
            caption.setObjectName("muted")
            facts.addWidget(value, 0, col)
            facts.addWidget(caption, 1, col)
            facts.setColumnStretch(col, 1)
            self.fact_values.append(value)
            self.fact_captions.append(caption)
        right.addWidget(self.facts)

        # vedle sebe, v úzkém okně pod sebou (`_fit_columns`); dřív se pravá karta ořízla
        columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        columns.setSpacing(12)
        self.columns = columns
        # co se počítá
        computed, computed_layout, computed_head = _section(tr("Co se počítá"))
        self.columns_btn = QPushButton(tr("Všechny sloupce…"))
        self.columns_btn.setFlat(True)
        self.columns_btn.setStyleSheet(f"color: {theme.ACCENT}; border: none;")
        self.columns_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.columns_btn.clicked.connect(lambda: self.show_protocol_detail(self.current()))
        computed_head.addWidget(self.columns_btn)
        self.groups_table = _table([tr("skupina"), tr("feature"), tr("sloupce")], stretch=1)
        right_header(self.groups_table, 2)
        computed_layout.addWidget(self.groups_table)
        self.quality_note = QLabel(
            tr("Kontrola kvality nahrávky (délka, SNR, přebuzení) se počítá vždy navíc.")
        )
        self.quality_note.setObjectName("muted")
        self.quality_note.setWordWrap(True)
        computed_layout.addWidget(self.quality_note)
        computed_layout.addStretch(1)
        columns.addWidget(computed, 5)

        side = QVBoxLayout()
        side.setSpacing(12)
        needs, needs_layout, _ = _section(tr("Co je potřeba"))
        self.providers_table = _table([tr("model"), tr("stav")])
        self.providers_table.horizontalHeader().setVisible(False)
        needs_layout.addWidget(self.providers_table)
        side.addWidget(needs)
        params, params_layout, _ = _section(tr("Změněné parametry"))
        self.params_table = _table([tr("parametr"), tr("hodnota"), tr("výchozí")], stretch=2)
        params_layout.addWidget(self.params_table)
        self.params_empty = QLabel(tr("Žádné, platí výchozí hodnoty knihovny."))
        self.params_empty.setObjectName("muted")
        params_layout.addWidget(self.params_empty)
        side.addWidget(params)
        side.addStretch(1)
        columns.addLayout(side, 4)
        right.addLayout(columns)

        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        right.addWidget(self.note)
        right.addStretch(1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea { background: transparent; }")
        area.setWidget(detail)
        area.viewport().installEventFilter(self)
        self.detail_area = area
        self.splitter.addWidget(area)
        self.splitter.setSizes([300, 760])

        self.editor_dialog = ProtocolEditorDialog(self)
        self.editor = self.editor_dialog.editor
        self._show(None)

    # --- nastavení zvenku -----------------------------------------------------

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self.editor.set_library(library)
        self._refill(self.current_name())

    def set_doctor(self, report: dict[str, Any] | None) -> None:
        self._doctor = report
        self.editor.set_doctor(report)
        self._show(self.current())

    def set_stats_lookup(self, lookup: Callable[[str], float | None]) -> None:
        self._seconds_per_file = lookup
        self.editor.set_stats_lookup(lookup)

    def make_protocol_detail(self, proto: Protocol) -> ProtocolDetailDialog:
        catalog, providers = self._catalog()
        seconds = self._seconds_per_file(proto.slug())
        hint = tr("naposledy {n:.0f} s na nahrávku").format(n=seconds) if seconds else ""
        return ProtocolDetailDialog(proto, catalog, providers, self._doctor, hint, self)

    def show_protocol_detail(self, proto: Protocol | None) -> None:
        if proto is not None:
            self.make_protocol_detail(proto).exec()

    def set_advanced(self, advanced: bool) -> None:
        self._advanced = advanced
        self.new_btn.setVisible(advanced)
        self._show(self.current())

    def _fit_columns(self) -> None:
        """Co se počítá a Co je potřeba vedle sebe, pod `NARROW` px pod sebou."""
        narrow = self.detail_area.viewport().width() < NARROW
        direction = QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
        if self.columns.direction() != direction:
            self.columns.setDirection(direction)
            # čtyři čísla: v řadě, v úzkém okně 2 × 2
            per_row = 2 if narrow else 4
            for i, (value, caption) in enumerate(
                zip(self.fact_values, self.fact_captions, strict=True)
            ):
                row, col = divmod(i, per_row)
                self.facts_grid.addWidget(value, row * 2, col)
                self.facts_grid.addWidget(caption, row * 2 + 1, col)
            for col in range(4):
                self.facts_grid.setColumnStretch(col, 1 if col < per_row else 0)

    # --- nový protokol ----------------------------------------------------------

    def base_protocol(self, task: str) -> Protocol | None:
        """Základní (výchozí přibalený) protokol úlohy, od kterého nový začíná."""
        builtin = [p for p in self._protocols if p.builtin and p.task == task]
        wanted = DEFAULT_PROTOCOLS.get(task, "")
        return next((p for p in builtin if p.name == wanted), builtin[0] if builtin else None)

    def new_protocol(self, task: str, name: str = "") -> Protocol | None:
        """Vlastní protokol úlohy jako kopie základního; vrátí ho vybraný."""
        base = self.base_protocol(task)
        if base is None or self._folder is None:
            return None
        taken = {p.name for p in self._protocols}
        default = tr("Nový protokol · {task}").format(task=tasks.label(task))
        proto = self._copy_of(base, unique_name(name.strip() or default, taken))
        proto.description = ""
        proto.save(self._folder / f"{proto.slug()}.yaml")
        self._reload(proto.name)
        return self.current()

    def _new_clicked(self) -> None:
        dialog = NewProtocolDialog(self.base_protocol, self)
        current = self.current()
        dialog.select_task(current.task if current is not None else contract.TASKS[0])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if self.new_protocol(dialog.task(), dialog.name_edit.text()) is not None:
            self.open_editor()  # rovnou výběr feature a parametry

    def set_protocols(self, protocols: list[Protocol], folder: Path, *, current: str = "") -> None:
        self._protocols = list(protocols)
        self._folder = folder
        self._refill(current)

    def _catalog(self) -> tuple[list[FeatureInfo], list[FeatureParams] | None]:
        if self._library is None:
            return [], None
        try:
            return self._library.features(), self._library.providers()
        except LibraryError:
            return [], None

    def _refill(self, current: str = "") -> None:
        """Karty po úlohách (úlohy knihovny v pořadí, pak vlastní protokoly za přibalenými)."""
        catalog, providers = self._catalog()
        self.list.blockSignals(True)
        self.list.clear()
        self._cards = []
        chosen: QListWidgetItem | None = None
        order = {t: i for i, t in enumerate(contract.TASKS)}
        by_task: dict[str, list[Protocol]] = {}
        for proto in self._protocols:
            by_task.setdefault(proto.task, []).append(proto)
        for task in sorted(by_task, key=lambda t: (order.get(t, len(order)), t)):
            head = QListWidgetItem(tasks.label(task).upper())
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            head.setForeground(QColor(theme.MUTED))
            font = head.font()
            font.setBold(True)
            font.setPointSizeF(font.pointSizeF() * 0.85)
            head.setFont(font)
            self.list.addItem(head)
            group = sorted(by_task[task], key=lambda p: (not p.builtin, p.display_name))
            for proto in group:
                item = QListWidgetItem()
                item.setData(ROLE_PROTO, proto)
                item.setToolTip(str(proto.path) if proto.path else tr("přibalený k aplikaci"))
                card_widget = ProtocolCard(proto, self._card_detail(proto, catalog, providers))
                item.setSizeHint(card_widget.sizeHint())
                self.list.addItem(item)
                self.list.setItemWidget(item, card_widget)
                self._cards.append(card_widget)
                if proto.name == current or chosen is None:
                    chosen = item
        self._fit_cards()
        self.list.blockSignals(False)
        self._apply_filter()
        if chosen is not None and not chosen.isHidden():
            self.list.setCurrentItem(chosen)
        else:
            self._select_first_visible()
        self._show(self.current())

    def _card_detail(
        self, proto: Protocol, catalog: list[FeatureInfo], providers: list[FeatureParams] | None
    ) -> str:
        pool = [f for f in catalog if proto.task in f.tasks]
        parts: list[str] = []
        summary = None
        if pool:
            summary = summarize(proto.select([f.name for f in pool]), pool, providers)
            parts.append(tr("{n} sloupců").format(n=summary.columns))
            parts.append(
                ", ".join(contract.PROVIDER_SHORT.get(p, p) for p in summary.providers)
                or tr("bez modelů")
            )
        seconds = self._seconds_per_file(proto.slug())
        if seconds:
            parts.append(tr("~{n:.0f} s").format(n=seconds))
        return " · ".join(parts)

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802
        if obj is self.list.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_cards()
        elif (
            getattr(self, "detail_area", None) is not None
            and obj is self.detail_area.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._fit_columns()
        return super().eventFilter(obj, event)

    def _fit_cards(self) -> None:
        width = self.list.viewport().width() - 2 * self.list.spacing() - 4
        if width <= 0:
            return
        for row in range(self.list.count()):
            item = self.list.item(row)
            widget = self.list.itemWidget(item)
            if widget is not None:
                item.setSizeHint(QSize(width, widget.sizeHint().height()))

    def _apply_filter(self) -> None:
        """Hledání v názvu a popisu, filtr přibalené · vlastní; prázdné úlohy schová."""
        text = self.search.text().strip().lower()
        origin = self.origin_filter.current()
        head: QListWidgetItem | None = None
        head_visible = False
        any_visible = False
        for row in range(self.list.count()):
            item = self.list.item(row)
            proto = item.data(ROLE_PROTO)
            if proto is None:
                if head is not None:
                    head.setHidden(not head_visible)
                head, head_visible = item, False
                continue
            match = (
                not text
                or text in proto.display_name.lower()
                or text in proto.display_description.lower()
            )
            if origin == self.BUILTIN:
                match = match and proto.builtin
            elif origin == self.CUSTOM:
                match = match and not proto.builtin
            item.setHidden(not match)
            head_visible = head_visible or match
            any_visible = any_visible or match
        if head is not None:
            head.setHidden(not head_visible)
        self.empty.setVisible(not any_visible and self.list.count() > 0)
        current = self.list.currentItem()
        if current is None or current.isHidden():
            self._select_first_visible()

    def _select_first_visible(self) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(ROLE_PROTO) is not None and not item.isHidden():
                self.list.setCurrentItem(item)
                return

    def current(self) -> Protocol | None:
        item = self.list.currentItem()
        return item.data(ROLE_PROTO) if item is not None else None

    def current_name(self) -> str:
        proto = self.current()
        return proto.name if proto else ""

    def select(self, name: str) -> bool:
        for row in range(self.list.count()):
            item = self.list.item(row)
            proto = item.data(ROLE_PROTO)
            if proto is not None and proto.name == name:
                if item.isHidden():  # filtr ho schoval: zrušit filtr
                    self.search.clear()
                    self.origin_filter.set_current(self.ALL)
                self.list.setCurrentItem(item)
                return True
        return False

    # --- detail ---------------------------------------------------------------

    def _current_changed(self, item: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        current = item.data(ROLE_PROTO) if item is not None else None
        for card_widget in self._cards:
            card_widget.set_current(card_widget.proto is current)
        self._show(current)

    def _show(self, proto: Protocol | None) -> None:
        custom = proto is not None and not proto.builtin
        editable = custom and self._advanced
        self.edit_btn.setVisible(editable)
        self.copy_btn.setEnabled(proto is not None)
        self.export_btn.setEnabled(proto is not None)
        self.more_btn.setEnabled(proto is not None)
        self.rename_action.setVisible(editable)
        self.file_action.setVisible(custom and proto is not None and proto.path is not None)
        self.delete_action.setEnabled(custom)
        if not custom:
            self.more_menu.set_reason(self.delete_action, tr("přibalený protokol se nemaže"))
        else:
            self.delete_action.setToolTip("")
        self.facts.setVisible(proto is not None)
        if proto is None:
            self.header.title.setText(tr("Žádný protokol"))
            self.header.subtitle.setText("")
            self.header.set_pills([])
            self.header.set_role("neutral")
            self.note.setText("")
            self.editor.set_protocol(None)
            self._fill_tables(None)
            return
        self.header.title.setText(proto.display_name)
        self.header.set_role("neutral" if proto.builtin else "accent")
        self.header.set_pills(
            [
                (tasks.label(proto.task), "neutral", tr("úloha"), None),
                (
                    tr("přibalený") if proto.builtin else tr("vlastní"),
                    "neutral" if proto.builtin else "accent",
                    str(proto.path) if proto.path else tr("přibalený k aplikaci"),
                    None,
                ),
            ]
        )
        self.header.subtitle.setText(proto.display_description or tr("bez popisu"))
        self.editor.set_protocol(proto if editable else None)
        if proto.builtin:
            self.note.setText(tr("Přibalený protokol se nemění. Uprav si jeho kopii."))
        elif not self._advanced:
            self.note.setText(tr("Úprava feature a parametrů je v rozšířeném režimu (Nastavení)."))
        else:
            self.note.setText("")
        self._fill_tables(proto)

    def _fill_tables(self, proto: Protocol | None) -> None:
        catalog, providers = self._catalog()
        pool = [f for f in catalog if proto is not None and proto.task in f.tasks]
        chosen = proto.select([f.name for f in pool]) if proto is not None and pool else []
        summary = summarize(chosen, pool, providers) if proto is not None else None

        # skupiny feature
        groups: dict[str, list[FeatureInfo]] = {}
        for f in pool:
            if f.name in chosen:
                groups.setdefault(f.group, []).append(f)
        table = self.groups_table
        table.setRowCount(len(groups))
        for row, (group, features) in enumerate(groups.items()):
            names = [f.name[len(group) + 1 :] or f.name for f in features]
            cols = sum(len(f.columns) or len(f.outputs) or 1 for f in features)
            set_text(table, row, 0, contract.GROUP_LABELS.get(group, group), bold=True)
            set_text(table, row, 1, ", ".join(names), muted=True, tooltip="\n".join(names))
            set_text(table, row, 2, str(cols), right=True)
        fit_height(table)

        # providery se stavem z doctor
        state = (self._doctor or {}).get("providers", {})
        needed = summary.providers if summary is not None else []
        table = self.providers_table
        table.setRowCount(max(1, len(needed)) if proto is not None else 0)
        if proto is not None and not needed:
            set_text(table, 0, 0, tr("Bez modelů"), bold=True)
            set_pill(table, 0, 1, tr("jen akustika"), "ok")
        for row, name in enumerate(needed):
            set_text(table, row, 0, contract.PROVIDER_LABELS.get(name, name), bold=True)
            ready = state.get(name, {}).get("ready") if name in state else None
            if ready:
                set_pill(table, row, 1, tr("připraveno"), "ok")
            elif ready is False:
                set_pill(table, row, 1, tr("chybí"), "missing", tooltip=tr("viz Prostředí"))
            else:
                set_pill(table, row, 1, tr("neověřeno"), "neutral")
        fit_widget_column(table, 1, extra=16)
        fit_height(table)

        # změněné parametry proti výchozím (výchozí jen u providerů, u feature by to byl
        # další dotaz na knihovnu)
        defaults = {(p.name, info.name): info.default for p in providers or [] for info in p.params}
        items = [
            (section, key, value)
            for section, values in (proto.config.items() if proto is not None else [])
            for key, value in values.items()
        ]
        table = self.params_table
        table.setRowCount(len(items))
        for row, (section, key, value) in enumerate(items):
            set_text(table, row, 0, f"{section}.{key}")
            set_text(table, row, 1, str(value), bold=True)
            default = defaults.get((section, key))
            set_text(table, row, 2, "–" if default is None else str(default), muted=True)
        fit_height(table)
        table.setVisible(bool(items))
        self.params_empty.setVisible(proto is not None and not items)

        # čtyři čísla
        if proto is None:
            return
        seconds = self._seconds_per_file(proto.slug())
        language = ""
        if any(p in needed for p in LANGUAGE_PROVIDERS):
            language = str(
                proto.config.get("transcript", {}).get("language")
                or proto.config.get("nlp", {}).get("language")
                or tr("podle nahrávek")
            )
        facts = [
            (str(summary.columns) if summary and pool else "–", tr("sloupců v tabulce")),
            (
                str(len(chosen)) if pool else "–",
                tr("feature v {n} skupinách").format(n=len(groups)),
            ),
            (
                tr("~{n:.0f} s").format(n=seconds) if seconds else tr("odhad"),
                tr("na nahrávku, naposledy")
                if seconds
                else (summary.cost_hint() if summary else ""),
            ),
            (language or "–", tr("jazyk nahrávek") if language else tr("bez jazyka")),
        ]
        for (value, caption), value_label, caption_label in zip(
            facts, self.fact_values, self.fact_captions, strict=True
        ):
            value_label.setText(value)
            caption_label.setText(caption)

    def summary_text(self) -> str:
        """Obsah tabulek detailu jako text (testy)."""
        out: list[str] = []
        for table in (self.groups_table, self.providers_table, self.params_table):
            for row in range(table.rowCount()):
                for col in range(table.columnCount()):
                    item = table.item(row, col)
                    if item is not None:
                        out.append(item.text())
        return " ".join(out)

    # --- akce ------------------------------------------------------------------

    def open_editor(self) -> bool:
        """Upravit…: okno s výběrem feature a parametry; po OK se protokol hned uloží."""
        base = self.current()
        if base is None or base.builtin or not self._advanced:
            return False
        if not self.editor_dialog.open_for(base.display_name):
            return False
        return self.apply_editor()

    def apply_editor(self) -> bool:
        """Uloží výběr a parametry z editoru do vybraného vlastního protokolu."""
        base = self.current()
        if base is None or base.builtin:
            return False
        proto = self.editor.result() or self._copy_of(base, base.name)
        proto.name = base.name
        proto.description = base.description
        proto.path = base.path
        return self._save(base, proto)

    def rename_current(self) -> bool:
        base = self.current()
        if base is None or base.builtin:
            return False
        dialog = InfoDialog(base, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        return self.set_info(dialog.name_edit.text(), dialog.desc_edit.toPlainText())

    def set_info(self, name: str, description: str) -> bool:
        """Nový název a popis vlastního protokolu (uloží se hned, soubor zůstává)."""
        base = self.current()
        if base is None or base.builtin:
            return False
        proto = self._copy_of(base, name.strip() or base.name)
        proto.description = description.strip()
        proto.path = base.path
        return self._save(base, proto)

    def _save(self, base: Protocol, proto: Protocol) -> bool:
        if self._folder is None:
            return False
        taken = {p.name for p in self._protocols if p.name != base.name}
        if proto.name in taken:
            QMessageBox.warning(
                self,
                tr("SpeechScope"),
                tr("Protokol „{name}“ už existuje.").format(name=proto.name),
            )
            return False
        path = base.path or self._folder / f"{proto.slug()}.yaml"
        proto.save(path)
        self._reload(proto.name)
        self.note.setText(tr("Uloženo do {path}").format(path=path))
        return True

    @staticmethod
    def _copy_of(base: Protocol, name: str) -> Protocol:
        return Protocol(
            name=name,
            task=base.task,
            description=base.description,
            features=list(base.features),
            domain=base.domain,
            vad=base.vad,
            config={k: dict(v) for k, v in base.config.items()},
        )

    def copy_current(self) -> None:
        base = self.current()
        if base is None or self._folder is None:
            return
        name = unique_name(
            tr("{name} (kopie)").format(name=base.name), {p.name for p in self._protocols}
        )
        proto = self._copy_of(base, name)
        proto.save(self._folder / f"{proto.slug()}.yaml")
        self._reload(name)

    def delete_current(self) -> None:
        proto = self.current()
        if proto is None or proto.builtin or proto.path is None:
            return
        answer = QMessageBox.question(
            self,
            tr("Smazat protokol"),
            tr("Opravdu smazat vlastní protokol „{name}“?\n{path}").format(
                name=proto.display_name, path=proto.path
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        proto.path.unlink(missing_ok=True)
        self._reload("")

    def _reveal_current(self) -> None:
        proto = self.current()
        if proto is not None and proto.path is not None:
            reveal_file(proto.path)

    def export_current(self) -> None:
        proto = self.current()
        if proto is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, tr("Exportovat protokol"), f"{proto.slug()}.yaml", YAML_FILTER
        )
        if target:
            self.export_to(Path(target))

    def export_to(self, target: Path) -> None:
        proto = self.current()
        if proto is None:
            return
        copy = Protocol.from_dict(proto.to_dict())
        copy.save(target)
        self.note.setText(tr("Exportováno do {path}").format(path=target))

    def import_file(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, tr("Importovat protokol"), "", YAML_FILTER)
        if source:
            self.import_from(Path(source))

    def import_from(self, source: Path) -> None:
        if self._folder is None:
            return
        try:
            proto = Protocol.load(source)
        except Exception as exc:  # rozbitý YAML, chybějící pole, cizí úloha
            QMessageBox.warning(
                self,
                tr("SpeechScope"),
                tr("Soubor není platný protokol: {error}").format(error=exc),
            )
            return
        taken = {p.name for p in self._protocols}
        overwrite = False
        if proto.name in taken:
            existing = next(p for p in self._protocols if p.name == proto.name)
            if existing.builtin:
                proto.name = unique_name(proto.name, taken)
            else:
                answer = QMessageBox.question(
                    self,
                    tr("Protokol už existuje"),
                    tr(
                        "Vlastní protokol „{name}“ už existuje. Přepsat ho?\n"
                        "Ne = uložit vedle pod jiným jménem."
                    ).format(name=proto.name),
                )
                overwrite = answer == QMessageBox.StandardButton.Yes
                if not overwrite:
                    proto.name = unique_name(proto.name, taken)
        path = import_protocol(proto, self._folder, self._protocols, overwrite=overwrite)
        self._reload(proto.name)
        self.note.setText(tr("Importováno do {path}").format(path=path))

    def open_folder(self) -> None:
        if self._folder is None:
            return
        self._folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder)))

    def _reload(self, select: str) -> None:
        """Hlavní okno v obsluze signálu znovu načte protokoly do všech stránek."""
        self.protocols_changed.emit()
        if select:
            self.select(select)
