"""Porovnání skupin (Výsledky › označit běhy › Porovnat…).

Mockup https://claude.ai/artifact/LaQwfCHYfEx4kptyEDoWk3 (`docs/mockups/porovnani-skupin/`
mimo git). Nahoře hlavička se skupinami, pod ní řada ovládání (skupiny podle běhu nebo
sloupce, metoda, po osobě, po logaritmu, jen odlišné), tabulka po feature seřazená podle
velikosti efektu a vpravo panel vybrané feature (krabice, popis skupin, test, normalita,
u víc skupin párová porovnání). Výpočet je `backend/compare.py`; nic se neukládá, Export
CSV… zapíše tabulku.
"""

from __future__ import annotations

import math
from html import escape
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ... import contract
from ...backend import compare as cmp
from ...backend import tasks
from ...backend.history import RunInfo
from ...backend.library import FeatureInfo
from ...i18n import N_, tr
from .. import theme
from ..feature_detail import split_unit
from ..recording_detail import column_index
from ..widgets.clean_table import (
    ColorHeader,
    card,
    cell,
    fit_height,
    fit_widget_column,
    right_header,
    set_text,
    style_table,
)
from ..widgets.elided_label import ElidedLabel
from ..widgets.flow_layout import FlowLayout
from ..widgets.inputs import Segmented, ToggleSwitch
from ..widgets.status_header import Pill

GROUP_COLORS = ("#2563eb", "#d97706", "#7c3aed", "#0d9488", "#db2777", "#4b5563")
GROUP_SOFT = ("#e8efff", "#fff4e0", "#f3edff", "#e6f7f5", "#fdeef5", "#f1f2f4")
GROUP_ROLES = ("accent", "warn", "neutral", "neutral", "neutral", "neutral")
PANEL_WIDTH = 360
FEATURE_MIN, FEATURE_MAX = 160, 320  # sloupec názvu feature v tabulce porovnání
# výšky řádků podoby A (1. 11. 2026): feature na dva řádky, nadpis domény, pruh feature
FEATURE_ROW, DOMAIN_ROW, BAND_ROW = 48, 30, 36
BAND_BG = "#eff6ff"  # pruh feature: světlejší než výběr řádku (ACCENT_SOFT)


class _Domain(str):
    """Nadpis domény v řazení podle feature (`acoustic.timing`); jako jméno feature je to
    řádek bez výsledku, proto podtřída `str`."""


TEXT_ROLE = Qt.ItemDataRole.UserRole + 1  # text buňky, kterou kreslí popisek
SHORT_MAX = 10  # delší názvy skupin se v tabulce a grafu nahradí písmeny A, B, C
CSV_FILTER = "CSV (*.csv)"
GENERIC_FEATURES = {"stats", "rate", "count", "summary", "values"}  # chce i skupinu
EFFECT_SYMBOLS = {"delta": "δ", "g": "g", "epsilon2": "ε²", "eta2": "η²"}
EFFECT_NAMES = {
    "delta": tr("Cliffovo δ"),
    "g": tr("Hedgesovo g"),
    "epsilon2": tr("ε²"),
    "eta2": tr("η²"),
}
TEST_NAMES = {
    "MW": tr("Mann-Whitney"),
    "t": tr("Welchův t-test"),
    "KW": tr("Kruskal-Wallis"),
    "ANOVA": tr("Welchova ANOVA"),
}


def color_of(index: int) -> str:
    return GROUP_COLORS[index % len(GROUP_COLORS)]


def fmt(value: float | None, digits: int = 3) -> str:
    """Asi tři platné číslice, desetinná tečka jako v Hodnotách, mínus jako znak."""
    if value is None or not math.isfinite(value):
        return "–"
    if value == 0:
        return "0"
    decimals = max(0, min(4, digits - 1 - math.floor(math.log10(abs(value)))))
    text = f"{value:.{decimals}f}"
    return "−" + text[1:] if text.startswith("-") else text


def fmt_effect(value: float | None) -> str:
    """Velikost efektu na dvě desetinná místa (δ, g, ε², η²)."""
    if value is None or not math.isfinite(value):
        return "–"
    text = f"{value:.2f}"
    return "−" + text[1:] if text.startswith("-") else text


def tick(value: float, ticks: list[float]) -> str:
    """Popisek osy s tolika desetinnými místy, kolik krok potřebuje."""
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1.0
    decimals = 0 if step >= 1 else max(0, -math.floor(math.log10(step) + 1e-9))
    text = f"{value:.{decimals}f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]
    return "−" + text[1:] if text.startswith("-") else text


def fmt_p(value: float | None) -> str:
    if value is None:
        return "–"
    if value < 0.001:
        return "<0.001"
    return f"{value:.3f}"


def effect_size_word(name: str, value: float | None) -> str:
    """Slovní velikost efektu podle obvyklých hranic (Cohen, Romano)."""
    if value is None:
        return ""
    v = abs(value)
    cuts = {
        "delta": (0.147, 0.33, 0.474),
        "g": (0.2, 0.5, 0.8),
        "epsilon2": (0.01, 0.08, 0.26),
        "eta2": (0.01, 0.06, 0.14),
    }.get(name, (0.2, 0.5, 0.8))
    if v < cuts[0]:
        return tr("zanedbatelný")
    if v < cuts[1]:
        return tr("malý")
    if v < cuts[2]:
        return tr("střední")
    return tr("velký")


def effect_fraction(name: str, value: float | None) -> float:
    """Délka pruhu efektu 0–1 (δ a ε²/η² jsou už v 0–1, g se ořízne na 2)."""
    if value is None:
        return 0.0
    if name == "g":
        return min(abs(value) / 2.0, 1.0)
    return min(abs(value), 1.0)


class BoxPlot(QWidget):
    """Vodorovné krabice skupin na společné ose (medián, kvartily, vousy 1,5 IQR)."""

    ROW = 34

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups: list[tuple[str, str, list[float]]] = []
        self.setMinimumHeight(80)

    def set_groups(self, groups: list[tuple[str, str, list[float]]]) -> None:
        """(název, barva, hodnoty) po skupinách."""
        self._groups = groups
        self.setFixedHeight(self.ROW * max(1, len(groups)) + 18 + self.fontMetrics().height() + 8)
        self.updateGeometry()
        self.update()

    def axis(self) -> tuple[float, float, list[float]]:
        values = [v for _n, _c, vals in self._groups for v in vals]
        if not values:
            return 0.0, 1.0, [0.0, 0.5, 1.0]
        lo, hi = min(values), max(values)
        if lo == hi:
            lo, hi = lo - 1, hi + 1
        raw = (hi - lo) / 4
        magnitude = 10 ** math.floor(math.log10(raw))
        step = next(s * magnitude for s in (1, 2, 2.5, 5, 10) if s * magnitude >= raw)
        start = math.floor(lo / step) * step
        end = math.ceil(hi / step) * step
        ticks = []
        t = start
        while t <= end + step / 2:
            ticks.append(round(t, 10))
            t += step
        return start, end, ticks

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right = (
            40,
            self.width() - 46,
        )  # vpravo místo na „n 21“ (medián je v tabulce pod grafem)
        lo, hi, ticks = self.axis()
        span = (hi - lo) or 1.0

        def x(v: float) -> float:
            return left + (v - lo) / span * (right - left)

        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.9)
        p.setFont(font)
        for i, (name, color, values) in enumerate(self._groups):
            y = 14 + i * self.ROW + self.ROW / 2
            bold = QFont(font)
            bold.setBold(True)
            p.setFont(bold)
            p.setPen(QColor(color))
            p.drawText(QRectF(0, y - 10, left - 4, 20), Qt.AlignmentFlag.AlignVCenter, name)
            p.setFont(font)
            if not values:
                continue
            vals = sorted(values)
            q1, med, q3 = (_quantile(vals, q) for q in (0.25, 0.5, 0.75))
            p.setPen(QColor(theme.TEXT))
            p.drawText(
                QRectF(right + 6, y - 10, self.width() - right - 6, 20),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"n {len(vals)}",
            )
            if len(vals) < 2:
                continue
            iqr = q3 - q1
            lo_w = min(v for v in vals if v >= q1 - 1.5 * iqr)
            hi_w = max(v for v in vals if v <= q3 + 1.5 * iqr)
            pen = QPen(QColor(color), 1.2)
            p.setPen(pen)
            p.drawLine(int(x(lo_w)), int(y), int(x(hi_w)), int(y))
            fill = QColor(color)
            fill.setAlpha(45)
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(x(q1), y - 10, max(2.0, x(q3) - x(q1)), 20), 2, 2)
            p.setPen(QPen(QColor(color), 2.2))
            p.drawLine(int(x(med)), int(y - 10), int(x(med)), int(y + 10))
            p.setBrush(QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            for v in vals:
                if v < lo_w or v > hi_w:
                    p.drawEllipse(QRectF(x(v) - 2.5, y - 2.5, 5, 5))
        base = 14 + len(self._groups) * self.ROW + 4
        p.setPen(QColor(theme.BORDER))
        p.drawLine(left, int(base), right, int(base))
        p.setPen(QColor(theme.MUTED))
        for t in ticks:
            p.drawText(
                QRectF(x(t) - 30, base + 2, 60, 14), Qt.AlignmentFlag.AlignHCenter, tick(t, ticks)
            )
        p.end()


def _narrow(table: QTableWidget) -> None:
    """Tabulka v úzkém panelu: bez vodorovného posuvníku, poslední sloupec roztažený."""
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.horizontalHeader().setStretchLastSection(True)
    # šířku určuje panel, ne obsah (jinak tabulka přetéká a posuvná plocha ji ořízne)
    table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    table.horizontalHeader().setMinimumSectionSize(24)


def _quantile(sorted_values: list[float], q: float) -> float:
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


class ComparePage(QWidget):
    """Porovnání skupin z vybraných běhů."""

    back_requested = Signal()
    sort_changed = Signal(str)  # effect | feature, hlavní okno si volbu pamatuje
    notice = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runs: list[RunInfo] = []
        self._catalog: list[FeatureInfo] = []
        self._index: dict = {}
        self._columns: dict[str, dict[str, int]] = {}
        self._result: cmp.Comparison | None = None
        self._shown: list[cmp.FeatureResult] = []
        self._current = ""  # vybraná feature (sloupec)
        self._value_boxes: list[QCheckBox] = []
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        # Zjednodušeno 1. 11. 2026 (mockup https://claude.ai/artifact/EAcPNALzd8xARofqmr3t3F,
        # `docs/mockups/porovnani-zjednoduseni/` mimo git): nahoře kdo proti komu (karty skupin)
        # a věta výsledku, zřídka měněné volby v Nastavení ▾, nad tabulkou jen filtr a hledání.
        head = QFrame()
        head.setObjectName("card")
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(16, 12, 16, 12)
        head_layout.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.title = QLabel(tr("Porovnání"))
        self.title.setObjectName("card_title")
        top.addWidget(self.title)
        top.addStretch(1)
        self.settings_btn = QPushButton(tr("Nastavení ▾"))
        self.settings_btn.setToolTip(
            tr("Metoda, průměr po osobě, logaritmus, nepoužitelné nahrávky")
        )
        self.settings_btn.clicked.connect(lambda: self.open_settings())
        top.addWidget(self.settings_btn)
        self.export_btn = QPushButton(tr("Export CSV…"))
        self.export_btn.clicked.connect(lambda: self.export_csv())
        top.addWidget(self.export_btn)
        head_layout.addLayout(top)
        # řádek skupin: jen když běhy mají sloupec, podle kterého jde dělit (group…)
        self.groups_row = QWidget()
        self._groups_flow = FlowLayout(self.groups_row, spacing=8)
        self._groups_flow.setContentsMargins(0, 0, 0, 0)
        self.groups_row.hide()
        head_layout.addWidget(self.groups_row)
        self.cards_box = QWidget()
        self.cards_layout = QHBoxLayout(self.cards_box)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)
        self._cards: list[QWidget] = []
        head_layout.addWidget(self.cards_box)
        verdict = QWidget()
        self.notes_flow = FlowLayout(verdict, spacing=8)
        self.notes_flow.setContentsMargins(0, 0, 0, 0)
        self.verdict = QLabel("")
        self.verdict.setTextFormat(Qt.TextFormat.RichText)
        self.verdict.linkActivated.connect(lambda _l: self.open_settings())
        self.notes_flow.addWidget(self.verdict)
        self._note_pills: list[Pill] = []
        head_layout.addWidget(verdict)
        outer.addWidget(head)

        # volby, které se mění zřídka: v rozbalovacím okně Nastavení ▾
        self.settings_panel = QFrame()
        self.settings_panel.setObjectName("settings_panel")
        # vlastní pozadí: okno nabídky je v tématu průhledné, bez něj prosvítala stránka
        self.settings_panel.setAutoFillBackground(True)
        self.settings_panel.setStyleSheet(
            f"QFrame#settings_panel {{ background: {theme.CARD};"
            f" border: 1px solid {theme.BORDER}; border-radius: 10px; }}"
            "QFrame#settings_panel QLabel { background: transparent; border: none; }"
        )
        grid = QGridLayout(self.settings_panel)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.BY_RUN, self.BY_COLUMN = tr("běhu"), tr("sloupce")
        self.by = Segmented([self.BY_RUN, self.BY_COLUMN])
        self.by.currentChanged.connect(lambda _v: self._by_changed())
        self.column = QComboBox()
        self.column.currentIndexChanged.connect(lambda _i: self._column_changed())
        by_box = QWidget()
        by_row = QHBoxLayout(by_box)
        by_row.setContentsMargins(0, 0, 0, 0)
        by_row.setSpacing(8)
        by_row.addWidget(self.by)
        by_row.addWidget(self.column)
        by_row.addStretch(1)
        self.values_box = QWidget()
        self.values_layout = QHBoxLayout(self.values_box)
        self.values_layout.setContentsMargins(0, 0, 0, 0)
        self.values_layout.setSpacing(8)
        self.METHOD_LABELS = {
            cmp.NONPARAMETRIC: tr("Neparametrická"),
            cmp.PARAMETRIC: tr("Parametrická"),
            cmp.AUTO: tr("Podle normality"),
        }
        self.method = Segmented(list(self.METHOD_LABELS.values()))
        self.method.currentChanged.connect(lambda _v: self._method_changed())
        self.by_subject = ToggleSwitch(True)
        self.by_subject.toggled.connect(lambda _c: self._recompute())
        self.log = ToggleSwitch(False)
        self.log.toggled.connect(lambda _c: self._recompute())
        self.include_bad = ToggleSwitch(False)
        self.include_bad.toggled.connect(lambda _c: self._recompute())
        # Skupiny podle běhu / sloupce jsou v hlavičce (1. 11. 2026: v Nastavení byly
        # schované), tady jen volby, které se mění zřídka
        rows = (
            (tr("Metoda"), self.method, "method"),
            (
                tr("Průměr po osobě"),
                self.by_subject,
                tr("víc nahrávek jedné osoby = jeden bod; bez sloupce pacient po nahrávkách"),
            ),
            (
                tr("Po logaritmu"),
                self.log,
                tr("testy z logaritmu hodnot (jen kladné feature, šikmá rozdělení)"),
            ),
            (
                tr("I nepoužitelné"),
                self.include_bad,
                tr("nahrávky, které kontrola kvality označila jako nepoužitelné"),
            ),
        )
        row = 0
        for label, widget, note in rows:
            if label:
                title = QLabel(label)
                title.setStyleSheet("font-weight: 600;")
                grid.addWidget(title, row, 0)
            grid.addWidget(widget, row, 1, Qt.AlignmentFlag.AlignLeft)
            row += 1
            if note:
                hint = QLabel(note)
                hint.setObjectName("muted")
                grid.addWidget(hint, row, 1)
                row += 1
                if note == "method":
                    self.method_hint = hint
        self.method_hint.setText(tr(self.METHOD_HINTS[self.method_key()]))
        groups_label = QLabel(tr("Skupiny:"))
        groups_label.setObjectName("muted")
        self._groups_flow.addWidget(groups_label)
        self._groups_flow.addWidget(by_box)
        self._groups_flow.addWidget(self.values_box)
        self._settings_menu = QMenu(self)
        action = QWidgetAction(self._settings_menu)
        action.setDefaultWidget(self.settings_panel)
        self._settings_menu.addAction(action)

        # nad tabulkou filtr, řazení a hledání; řazení mění i klik na záhlaví (feature /
        # rozdíl), přepínač je vidět, aby šlo poznat, jak je seřazeno, a vrátit to (1. 11. 2026)
        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.BY_EFFECT, self.BY_FEATURE = tr("podle rozdílu"), tr("podle feature")
        self.sort = Segmented([self.BY_EFFECT, self.BY_FEATURE])
        self.sort.currentChanged.connect(lambda _v: self._sort_chosen())
        self.SIGNIFICANT, self.ALL = tr("liší se"), tr("všechny")
        self.only = Segmented([self.SIGNIFICANT, self.ALL])
        self.only.currentChanged.connect(lambda _v: self._only_chosen())
        self._only_auto = True  # bez volby uživatele: Liší se, když se něco liší
        controls.addWidget(self.only)
        controls.addSpacing(8)
        sort_label = QLabel(tr("Řadit:"))
        sort_label.setObjectName("muted")
        controls.addWidget(sort_label)
        controls.addWidget(self.sort)
        controls.addSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat feature"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(lambda _t: self._fill_table())
        controls.addWidget(self.search)
        controls.addStretch(1)
        outer.addLayout(controls)

        # tabulka a panel
        body = QHBoxLayout()
        body.setSpacing(12)
        self.table = QTableWidget()
        # záhlaví skupin barvou skupiny jako v grafu (styl tabulky barvu jinak přebije)
        self.table.setHorizontalHeader(ColorHeader(self.table))
        style_table(self.table, [tr("feature")], selectable=True)
        self.table.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._row_changed(r))
        self.table.viewport().installEventFilter(self)  # šířka názvu podle okna
        # volné místo za posledním sloupcem: záhlaví tam stejně šedé jako sloupce
        self.table.setStyleSheet(
            self.table.styleSheet() + f"QHeaderView {{ background: {theme.NEUTRAL_SOFT}; }}"
        )
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        body.addWidget(card(self.table), 1)
        body.addWidget(self._build_panel())
        outer.addLayout(body, 1)

    @staticmethod
    def _labelled(text: str, widget: QWidget) -> QWidget:
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(text)
        label.setObjectName("muted")
        layout.addWidget(label)
        layout.addWidget(widget)
        return box

    def _build_panel(self) -> QWidget:
        """Panel vybrané feature (mockup část 4): krabice s n (medián je v tabulce
        skupin pod grafem), test, efekt, popis skupin v řádcích, normalita štítky, u víc
        skupin párová porovnání."""
        panel = QFrame()
        panel.setObjectName("card")
        panel.setFixedWidth(PANEL_WIDTH)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)
        content = QWidget()
        self.panel_content = content
        content.setObjectName("compare_panel")
        content.setStyleSheet("#compare_panel { background: transparent; }")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(6)
        self.panel_title = QLabel("")
        self.panel_title.setObjectName("card_title")
        self.panel_title.setWordWrap(True)
        layout.addWidget(self.panel_title)
        self.panel_sub = QLabel("")
        self.panel_sub.setObjectName("muted")
        self.panel_sub.setWordWrap(True)
        layout.addWidget(self.panel_sub)
        self.boxplot = BoxPlot()
        layout.addWidget(self.boxplot)
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setStyleSheet(f"color: {theme.BORDER};")
        layout.addWidget(rule)
        self.test_label = QLabel("")
        self.test_label.setTextFormat(Qt.TextFormat.RichText)
        self.test_label.setWordWrap(True)
        layout.addWidget(self.test_label)
        effect_row = QHBoxLayout()
        effect_row.setSpacing(6)
        self.effect_bar = QProgressBar()
        self.effect_bar.setTextVisible(False)
        self.effect_bar.setFixedSize(64, 6)
        self.effect_bar.setRange(0, 100)
        effect_row.addWidget(self.effect_bar)
        self.effect_label = QLabel("")
        self.effect_label.setTextFormat(Qt.TextFormat.RichText)
        effect_row.addWidget(self.effect_label, 1)
        layout.addLayout(effect_row)
        self.groups_table = QTableWidget()
        # dvojice podle metody (medián a IQR, nebo průměr a SD); druhá je v tooltipu řádku,
        # čtyři čísla se do úzkého panelu nevejdou
        style_table(self.groups_table, ["", tr("medián"), tr("IQR")], row_height=30)
        right_header(self.groups_table, 1, 2)
        groups_header = self.groups_table.horizontalHeader()
        for col in (1, 2):
            groups_header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        _narrow(self.groups_table)
        small = self.groups_table.font()
        small.setPointSizeF(small.pointSizeF() * 0.92)
        self.groups_table.setFont(small)
        layout.addWidget(self.groups_table)
        self.normal_box = QWidget()
        self.normal_flow = FlowLayout(self.normal_box, spacing=5)
        layout.addWidget(self.normal_box)
        self.pairs_table = QTableWidget()
        style_table(self.pairs_table, [tr("dvojice"), "", "q"], row_height=30)
        right_header(self.pairs_table, 1, 2)
        _narrow(self.pairs_table)
        layout.addWidget(self.pairs_table)
        self.panel_note = QLabel("")
        self.panel_note.setObjectName("muted")
        self.panel_note.setWordWrap(True)
        layout.addWidget(self.panel_note)
        self.version_note = QLabel("")
        self.version_note.setWordWrap(True)
        self.version_note.setStyleSheet(f"color: {theme.WARN};")
        layout.addWidget(self.version_note)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        nav = QHBoxLayout()
        self.prev_btn = QPushButton(tr("‹ předchozí"))
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn = QPushButton(tr("další ›"))
        self.next_btn.clicked.connect(lambda: self.step(1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        outer.addLayout(nav)
        return panel

    # --- vstup ---------------------------------------------------------------------

    def set_catalog(self, catalog: list[FeatureInfo]) -> None:
        self._catalog = catalog
        self._index = column_index(catalog)

    def set_runs(self, runs: list[RunInfo]) -> None:
        """Nové porovnání: skupiny podle běhu (u jednoho běhu podle prvního sloupce)."""
        self._runs = list(runs)
        frames = []
        for run in runs:
            try:
                frames.append(cmp.read_table(run))
            except (OSError, ValueError):
                continue
        self._columns = cmp.group_columns(frames)
        self._busy = True
        self.column.clear()
        for name in self._columns:
            self.column.addItem(name, name)
        idx = self.column.findData("group")
        self.column.setCurrentIndex(max(idx, 0))
        self.by.set_current(self.BY_COLUMN if len(runs) == 1 else self.BY_RUN)
        self.by.setEnabled(bool(self._columns) and len(runs) > 1)
        self.groups_row.setVisible(bool(self._columns))
        self._busy = False
        self._current = ""
        self._by_changed()

    def open_settings(self) -> None:
        """Nastavení ▾: skupiny, metoda, po osobě, log, nepoužitelné (pod tlačítkem)."""
        self._settings_menu.popup(
            self.settings_btn.mapToGlobal(self.settings_btn.rect().bottomRight())
            - QPoint(self.settings_panel.sizeHint().width(), 0)
        )

    METHOD_HINTS = {
        cmp.NONPARAMETRIC: N_("Mann-Whitney / Kruskal-Wallis, efekt Cliffovo δ / ε², medián"),
        cmp.PARAMETRIC: N_("Welchův t-test / Welchova ANOVA, efekt Hedgesovo g / η², průměr"),
        cmp.AUTO: N_("podle Shapiro-Wilk u každé feature zvlášť, test je u q"),
    }

    def _method_changed(self) -> None:
        self.method_hint.setText(tr(self.METHOD_HINTS[self.method_key()]))
        self._recompute()

    def _header_clicked(self, section: int) -> None:
        """Klik na záhlaví: feature = řadit podle feature, rozdíl = podle efektu."""
        result = self._result
        if result is None:
            return
        if section == 0:
            self.sort.set_current(self.BY_FEATURE)
        elif section == 1 + len(result.groups):
            self.sort.set_current(self.BY_EFFECT)

    def sort_key(self) -> str:
        return "feature" if self.sort.current() == self.BY_FEATURE else "effect"

    def set_sort(self, key: str, *, emit: bool = True) -> None:
        """Řazení tabulky: effect (podle rozdílu) nebo feature."""
        self.blockSignals(not emit)
        try:
            self.sort.set_current(self.BY_FEATURE if key == "feature" else self.BY_EFFECT)
        finally:
            self.blockSignals(False)

    def _sort_chosen(self) -> None:
        self._fill_table()
        self.sort_changed.emit(self.sort_key())

    def _only_chosen(self) -> None:
        self._only_auto = False
        self._fill_table()

    def set_by(self, by: str, column: str = "", values: list[str] | None = None) -> None:
        """Skupiny podle běhu, nebo podle sloupce (a které hodnoty)."""
        self._busy = True
        self.by.set_current(self.BY_COLUMN if by == cmp.BY_COLUMN else self.BY_RUN)
        if column:
            self.column.setCurrentIndex(max(self.column.findData(column), 0))
        self._busy = False
        self._by_changed(values)

    def set_method(self, method: str) -> None:
        self.method.set_current(self.METHOD_LABELS[method])
        self._recompute()

    def method_key(self) -> str:
        current = self.method.current()
        return next(k for k, v in self.METHOD_LABELS.items() if v == current)

    def by_key(self) -> str:
        return cmp.BY_COLUMN if self.by.current() == self.BY_COLUMN else cmp.BY_RUN

    def comparison(self) -> cmp.Comparison | None:
        return self._result

    # --- skupiny ----------------------------------------------------------------------

    def _by_changed(self, values: list[str] | None = None) -> None:
        by_column = self.by_key() == cmp.BY_COLUMN
        self.column.setVisible(by_column)
        self.values_box.setVisible(by_column)
        if by_column:
            self._fill_values(values)
        if not self._busy:
            self._recompute()

    def _column_changed(self) -> None:
        if self._busy:
            return
        self._fill_values()
        self._recompute()

    def _fill_values(self, values: list[str] | None = None) -> None:
        for box in self._value_boxes:
            self.values_layout.removeWidget(box)
            box.hide()
            box.deleteLater()
        self._value_boxes = []
        counts = self._columns.get(str(self.column.currentData() or ""), {})
        for value, n in counts.items():
            text = f"{value} {n}" if value else tr("bez skupiny {n}").format(n=n)
            box = QCheckBox(text)
            box.setProperty("value", value)
            wanted = values is None and bool(value) and n >= cmp.MIN_N
            box.setChecked(value in values if values is not None else wanted)
            if value and n < cmp.MIN_N:
                box.setToolTip(tr("málo osob"))
            box.toggled.connect(lambda _c: self._recompute())
            self.values_layout.addWidget(box)
            self._value_boxes.append(box)

    def chosen_values(self) -> list[str]:
        return [str(b.property("value")) for b in self._value_boxes if b.isChecked()]

    def set_value_checked(self, value: str, checked: bool) -> None:
        for box in self._value_boxes:
            if box.property("value") == value:
                box.setChecked(checked)

    # --- výpočet ----------------------------------------------------------------------

    def _recompute(self) -> None:
        if self._busy or not self._runs:
            return
        by = self.by_key()
        values = self.chosen_values() if by == cmp.BY_COLUMN else None
        column = str(self.column.currentData() or "")
        try:
            groups, errors, bad = cmp.make_groups(
                self._runs, by, column, values, include_bad=self.include_bad.isChecked()
            )
        except (OSError, ValueError) as exc:
            self.notice.emit(str(exc))
            return
        if len(groups) < 2:
            self._result = None
            self._show_empty(tr("Vyberte aspoň dvě skupiny."))
            return
        self._result = cmp.compare(
            groups,
            self.method_key(),
            by_subject=self.by_subject.isChecked(),
            log=self.log.isChecked(),
            excluded_errors=errors,
            excluded_bad=bad,
        )
        if self._only_auto:  # dokud uživatel nevybral: Liší se, když se aspoň něco liší
            self.only.blockSignals(True)
            self.only.set_current(self.SIGNIFICANT if self._result.significant else self.ALL)
            self.only.blockSignals(False)
        self._fill_header()
        self._fill_table()

    def _show_empty(self, text: str) -> None:
        self.title.setText(tr("Porovnání"))
        self._set_cards([])
        self.verdict.setText(text)
        self._set_notes([])
        self.table.setRowCount(0)
        self._shown = []
        self._show_feature(None)

    def _task_label(self) -> str:
        names = {tasks.label(r.task) for r in self._runs if r.task}
        return ", ".join(sorted(names))

    def _fill_header(self) -> None:
        """Karty skupin (kdo proti komu) a věta výsledku s tím, jak se počítalo."""
        result = self._result
        assert result is not None
        self.title.setText(tr("Porovnání · {task}").format(task=self._task_label()))
        self._set_cards(result.groups)
        first = next((r for r in result.results if r.test), None)
        test = TEST_NAMES.get(first.test, "") if first else ""
        how = [test or tr("bez testu")]
        if result.by_subject:
            how.append(tr("průměr po osobě"))
        elif result.no_subject:
            how.append(tr("po nahrávkách (některý běh nemá sloupec pacient)"))
        else:
            how.append(tr("po nahrávkách"))
        if result.log:
            how.append(tr("po logaritmu"))
        how.append(tr("korekce FDR"))
        self.verdict.setText(
            "<b style='font-size:15px'>"
            + tr("Liší se {k} z {n} feature").format(k=result.significant, n=len(result.results))
            + f"</b> <span style='color:{theme.MUTED}'>· "
            + ", ".join(how)
            + f" · </span><a href='settings' style='color:{theme.ACCENT}'>"
            + tr("změnit")
            + "</a>"
        )
        notes: list[tuple[str, str, str, object]] = []
        for name, columns in result.only_in.items():
            notes.append(
                (
                    tr("jen v {group}: {n} sloupců").format(group=name, n=len(columns)),
                    "neutral",
                    "\n".join(columns[:40]),
                    lambda name=name, columns=columns: self._show_only_in(name, columns),
                )
            )
        skipped = []
        if result.excluded_errors:
            skipped.append(tr("{n} chybné").format(n=result.excluded_errors))
        if result.excluded_bad:
            skipped.append(tr("{n} nepoužitelné").format(n=result.excluded_bad))
        if skipped:
            notes.append(
                (tr("vynecháno: {what}").format(what=", ".join(skipped)), "neutral", "", None)
            )
        if result.shared_subjects:
            notes.append(
                (
                    tr("osoby ve víc skupinách: {n}").format(n=len(result.shared_subjects)),
                    "warn",
                    ", ".join(result.shared_subjects[:30]),
                    None,
                )
            )
        self._set_notes(notes)

    def _set_cards(self, groups: list[cmp.Group]) -> None:
        """Karta na skupinu barvou grafu: zkratka, počet osob (nahrávek), odkud je."""
        for old in self._cards:
            self.cards_layout.removeWidget(old)
            old.hide()  # jinak se do smazání z fronty událostí kreslí přes nové
            old.deleteLater()
        self._cards = []
        result = self._result
        unit_people = result is not None and result.by_subject
        for i, group in enumerate(groups):
            if i == 1 and len(groups) == 2:
                versus = QLabel(tr("proti"))
                versus.setObjectName("muted")
                self.cards_layout.addWidget(versus)
                self._cards.append(versus)
            color = color_of(i)
            soft = GROUP_SOFT[i % len(GROUP_SOFT)]
            box = QFrame()
            box.setObjectName("groupcard")
            box.setStyleSheet(
                f"QFrame#groupcard {{ background: {soft}; border: none;"
                f" border-left: 4px solid {color}; border-radius: 8px; }}"
                "QFrame#groupcard QLabel { background: transparent; border: none; }"
            )
            layout = QVBoxLayout(box)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(1)
            short = QLabel(self.short(i))
            short.setStyleSheet(f"color: {color}; font-weight: 700;")
            layout.addWidget(short)
            n = self._group_n(group)
            count = QLabel(
                tr("{n} osob").format(n=n) if unit_people else tr("{n} nahrávek").format(n=n)
            )
            count.setStyleSheet("font-size: 17px; font-weight: 600;")
            layout.addWidget(count)
            detail = ElidedLabel(group.name if self.short(i) != group.name else "")
            detail.setObjectName("muted")
            layout.addWidget(detail)
            box.setToolTip(group.name)
            self.cards_layout.addWidget(box, 1)
            self._cards.append(box)

    def _set_notes(self, notes: list) -> None:
        """Malé štítky za větou výsledku (jen v jedné skupině, vynechané, sdílené osoby)."""
        for old in self._note_pills:
            self.notes_flow.removeWidget(old)
            old.hide()
            old.deleteLater()
        self._note_pills = []
        for text, role, tooltip, action in notes:
            pill = Pill(text, role, action)
            pill.setToolTip(tooltip)
            self.notes_flow.addWidget(pill)
            self._note_pills.append(pill)

    def short(self, index: int) -> str:
        """Krátký název skupiny do tabulky, grafu a panelu: hodnota sloupce, nebo písmeno,
        když jsou názvy dlouhé (běhy); celý název je v hlavičce a v tooltipu."""
        groups = self._result.groups if self._result is not None else []
        if groups and all(len(g.name) <= SHORT_MAX for g in groups):
            return groups[index].name
        return chr(ord("A") + index) if index < 26 else str(index + 1)

    def _pill_text(self, index: int, group: cmp.Group) -> str:
        short = self.short(index)
        name = group.name if short == group.name else f"{short} · {group.name}"
        return f"● {name} · {self._group_n(group)}"

    def _group_n(self, group: cmp.Group) -> int:
        result = self._result
        return group.subjects if result is not None and result.by_subject else len(group.frame)

    def _show_only_in(self, name: str, columns: list[str]) -> None:
        QMessageBox.information(
            self,
            tr("Jen v jedné skupině"),
            tr("Tyto sloupce má jen {group}, netestují se:\n\n{columns}").format(
                group=name, columns="\n".join(columns)
            ),
        )

    # --- tabulka ----------------------------------------------------------------------

    def feature_parts(self, column: str) -> tuple[str, str, str, str, str]:
        """(feature, sloupec, jednotka, popis, celé jméno feature) jako v Hodnotách:
        `perturbation · hnr dB`; bez katalogu z technického jména."""
        info = self._index.get(column)
        if info is None:
            parts = column.split(".")
            prefix = parts[-2] if len(parts) > 1 else ""
            return prefix, parts[-1], "", "", ".".join(parts[:-1])
        feature = info.feature
        text, unit = split_unit(info.description)
        group_tail = feature.group.split(".")[-1]
        rest = feature.name.removeprefix(feature.group + ".")
        if column == feature.name:  # feature s jedinou hodnotou (acoustic.ddk.rate)
            prefix, short = group_tail, feature.name.rsplit(".", 1)[-1]
        else:
            prefix, short = rest, info.short
            if rest in GENERIC_FEATURES:
                prefix = f"{group_tail}.{rest}"  # „stats“ samo nic neřekne
        return prefix, short, unit, text, feature.name

    def feature_label(self, column: str) -> tuple[str, str]:
        """Krátký název `feature · sloupec` a jednotka (tabulka, panel, hledání)."""
        prefix, short, unit, _text, _name = self.feature_parts(column)
        return (f"{prefix} · {short}" if prefix else short), unit

    def visible_results(self) -> list[cmp.FeatureResult]:
        return list(self._shown)

    def _ordered(self, results: list[cmp.FeatureResult]) -> list[cmp.FeatureResult]:
        """Podle efektu (pořadí z výpočtu), nebo podle feature v pořadí katalogu."""
        if self.sort.current() != self.BY_FEATURE:
            return results
        order: dict[str, tuple[int, int]] = {}
        for fi, feature in enumerate(self._catalog):
            for ci, column in enumerate(feature.columns):
                order[column] = (fi, ci)
        fallback = len(self._catalog)
        rank: dict[str, int] = {}  # feature → pořadí (sloupec mimo katalog ji nerozdělí)
        for r in results:
            name = self.feature_parts(r.column)[4]
            fi = order.get(r.column, (fallback, 0))[0]
            rank[name] = min(rank.get(name, fallback), fi)

        def key(r: cmp.FeatureResult) -> tuple:
            name = self.feature_parts(r.column)[4]
            return rank[name], name, order.get(r.column, (fallback, 0))[1], r.column

        return sorted(results, key=key)

    def _feature_cell(self, row: int, r: cmp.FeatureResult, grouped: bool) -> None:
        """Buňka feature, podoba A (1. 11. 2026, mockup docs/mockups/porovnani-feature/):
        nahoře tučně čitelný název z popisu knihovny a šedě jednotka, pod ním malým písmem
        technické `feature · sloupec` (v řazení podle feature jen sloupec). Bez popisu jen
        technické jméno na jednom řádku. Text položky zůstává kvůli hledání a testům."""
        prefix, short, unit, text, _name = self.feature_parts(r.column)
        label, _u = self.feature_label(r.column)
        plain = label + (f" ({unit})" if unit else "") + ("  ⚠" if r.versions else "")
        tooltip = r.column + (f"\n{text}" if text else "")
        if r.versions:
            tooltip += "\n" + tr("jiná verze feature: {v}").format(
                v=", ".join(f"{k} {v}" for k, v in r.versions.items())
            )
        item = set_text(self.table, row, 0, "", tooltip=tooltip)
        item.setData(Qt.ItemDataRole.UserRole, r.column)
        item.setData(TEXT_ROLE, plain)  # text buňky pod popiskem (hledání, testy)
        technical = short if grouped or not prefix else f"{prefix} · {short}"
        readable = text.strip()
        holder = QWidget()
        holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lines = QVBoxLayout(holder)
        lines.setContentsMargins(24 if grouped else 8, 0, 8, 0)
        lines.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = ElidedLabel(readable or technical)
        bold = name.font()
        bold.setBold(True)
        name.setFont(bold)
        name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        name.setMinimumWidth(0)
        top.addWidget(name)  # jednotka hned za názvem, ne u pravého okraje
        if unit:
            unit_label = QLabel(unit)
            unit_label.setObjectName("muted")
            top.addWidget(unit_label)
        if r.versions:
            warn = QLabel("⚠")
            warn.setStyleSheet(f"color: {theme.WARN};")
            top.addWidget(warn)
        top.addStretch(1)
        lines.addStretch(1)
        lines.addLayout(top)
        if readable and readable != technical:
            tech = ElidedLabel(technical)
            font = QFont("Consolas")
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setPointSizeF(max(7.0, self.font().pointSizeF() * 0.85))
            tech.setFont(font)
            tech.setStyleSheet(f"color: {theme.MUTED};")
            tech.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            tech.setMinimumWidth(0)
            lines.addWidget(tech)
        lines.addStretch(1)
        holder.setToolTip(tooltip)
        self.table.setCellWidget(row, 0, holder)

    def _domain_header(self, row: int, group: str, columns: int) -> None:
        """Nadpis domény velkými písmeny („AKUSTIKA · ČASOVÁNÍ“) nad pruhy jejích feature."""
        item = set_text(self.table, row, 0, "")
        item.setData(TEXT_ROLE, str(group))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setSpan(row, 0, 1, columns)
        label = QLabel(contract.GROUP_LABELS.get(group, group).upper())
        font = label.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 0.85)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
        label.setFont(font)
        label.setStyleSheet(f"color: {theme.MUTED}; padding-left: 8px; padding-top: 6px;")
        label.setToolTip(str(group))
        self.table.setCellWidget(row, 0, label)
        self.table.setRowHeight(row, DOMAIN_ROW)

    def _group_header(self, row: int, name: str, columns: int) -> None:
        """Pruh feature přes celou šířku (řazení podle feature): tučně popis feature z knihovny,
        šedě technické jméno, vpravo počet sloupců."""
        description = ""
        feature = next((f for f in self._catalog if f.name == name), None)
        if feature is not None and feature.description:
            description = split_unit(feature.description)[0]
        item = set_text(self.table, row, 0, "")
        item.setData(TEXT_ROLE, name)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setSpan(row, 0, 1, columns)
        short = name.split(".", 2)[-1] if name.count(".") >= 2 else name
        band = QWidget()
        band.setObjectName("feature_band")
        band.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        band.setStyleSheet(f"QWidget#feature_band {{ background: {BAND_BG}; }}")
        layout = QHBoxLayout(band)
        layout.setContentsMargins(8, 0, 12, 0)
        layout.setSpacing(8)
        title = QLabel(f"<b>{escape(description or short)}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(title)
        if description:
            tech = QLabel(short)
            tech.setObjectName("muted")
            layout.addWidget(tech)
        layout.addStretch(1)
        n = getattr(self, "_band_counts", {}).get(name, 0)
        count = QLabel(tr("1 sloupec") if n == 1 else tr("{n} sloupců").format(n=n))
        count.setObjectName("muted")
        layout.addWidget(count)
        band.setToolTip(name)
        self.table.setCellWidget(row, 0, band)
        self.table.setRowHeight(row, BAND_ROW)

    def _fill_table(self) -> None:
        result = self._result
        if result is None:
            return
        needle = self.search.text().strip().lower()
        only = self.only.current() == self.SIGNIFICANT
        shown = []
        for r in result.results:
            if only and not r.significant:
                continue
            label, _unit = self.feature_label(r.column)
            if needle and needle not in label.lower() and needle not in r.column.lower():
                continue
            shown.append(r)
        shown = self._ordered(shown)
        self._shown = shown
        grouped = self.sort.current() == self.BY_FEATURE
        self.only.buttons[0].setText(tr("Liší se ({n})").format(n=result.significant))
        self.only.buttons[1].setText(tr("Všechny ({n})").format(n=len(result.results)))
        groups = result.groups
        first = next((r for r in result.results if r.effect_name), None)
        effect_name = EFFECT_NAMES.get(first.effect_name, tr("efekt")) if first else tr("efekt")
        headers = [tr("feature")]
        headers += [f"● {self.short(i)}" for i in range(len(groups))]
        headers += [effect_name, "q"]  # normalita je v panelu vpravo
        # řádky tabulky: výsledek, nebo jméno feature = nadpis skupiny
        rows: list[cmp.FeatureResult | str] = []
        last = last_group = None
        self._band_counts: dict[str, int] = {}
        for r in shown:
            name = self.feature_parts(r.column)[4]
            self._band_counts[name] = self._band_counts.get(name, 0) + 1
            if grouped and name != last:
                group = ".".join(name.split(".")[:2])
                if group != last_group:
                    rows.append(_Domain(group))
                    last_group = group
                rows.append(name)
                last = name
            rows.append(r)
        self._row_items = rows
        table = self.table
        table.blockSignals(True)
        table.clearSpans()
        for r in range(table.rowCount()):
            for c in range(table.columnCount()):
                old = table.cellWidget(r, c)
                if old is not None:
                    old.hide()
                    table.removeCellWidget(r, c)
        table.setColumnCount(len(headers))
        # řecká písmena velikost nemění (ε² by bylo Ε², k nerozeznání od E)
        table.setHorizontalHeaderLabels([h if h == effect_name else h.upper() for h in headers])
        for i, _g in enumerate(groups):
            item = table.horizontalHeaderItem(1 + i)
            if item is not None:
                item.setForeground(QColor(color_of(i)))
        n_groups = len(groups)
        right_header(table, *range(1, 1 + n_groups), 2 + n_groups)
        # šipka u sloupce, podle kterého se řadí (feature, nebo rozdíl)
        sort_col = 0 if grouped else 1 + n_groups
        table.horizontalHeader().setSortIndicatorShown(True)
        table.horizontalHeader().setSortIndicator(
            sort_col, Qt.SortOrder.AscendingOrder if grouped else Qt.SortOrder.DescendingOrder
        )
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        for c in range(len(headers)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        table.setRowCount(len(rows))
        select_row = -1
        for row, entry in enumerate(rows):
            if isinstance(entry, _Domain):
                self._domain_header(row, entry, len(headers))
                continue
            if isinstance(entry, str):
                self._group_header(row, entry, len(headers))
                continue
            r = entry
            self._feature_cell(row, r, grouped)
            table.setRowHeight(row, FEATURE_ROW)
            for i in range(n_groups):
                set_text(
                    table, row, 1 + i, fmt(r.center[i]), right=True,
                    tooltip=tr("n = {n}").format(n=r.n[i]),
                )  # fmt: skip
            col_effect = 1 + n_groups
            if r.tested:
                bar = QProgressBar()
                bar.setTextVisible(False)
                bar.setFixedSize(36, 6)
                bar.setRange(0, 100)
                bar.setValue(int(effect_fraction(r.effect_name, r.effect) * 100))
                if r.versions:
                    bar.setStyleSheet(f"QProgressBar::chunk {{ background: {theme.WARN}; }}")
                set_text(table, row, col_effect, "")
                word = QLabel(effect_size_word(r.effect_name, r.effect))
                word.setObjectName("muted")
                table.setCellWidget(row, col_effect, cell(bar, QLabel(self._effect_text(r)), word))
            else:
                note = {
                    "few": tr("málo hodnot"),
                    "log": tr("nejde logaritmovat"),
                    "constant": tr("bez rozptylu"),
                }.get(r.note, "")
                set_text(table, row, col_effect, note, muted=True)
            q_text = fmt_p(r.q)
            if r.tested and self.method_key() == cmp.AUTO:
                q_text = f"{q_text} ({r.test})"
            q_item = set_text(table, row, 2 + n_groups, q_text, right=True, muted=not r.significant)
            q_item.setIcon(_dot_icon(theme.OK if r.significant else "#d1d5db"))
            q_item.setToolTip(
                tr("liší se (q < 0,05)") if r.significant else tr("rozdíl není významný")
            )
            if r.significant:
                font = q_item.font()
                font.setBold(True)
                q_item.setFont(font)
            if select_row < 0 or r.column == self._current:
                select_row = row
        table.blockSignals(False)
        fit_widget_column(table, 1 + n_groups)
        self._fit_feature_column()
        if select_row >= 0:
            table.selectRow(select_row)
            self._row_changed(select_row)
        else:
            self._show_feature(None)

    def _fit_feature_column(self) -> None:
        """Název feature podle obsahu, nejvýš `FEATURE_MAX`, čísla hned za ním a volné místo
        až za posledním sloupcem (1. 11. 2026: dřív bral název všechno volné místo jako strom
        dimenzí). V úzkém okně se napřed zúží název, až na `FEATURE_MIN`."""
        table = self.table
        header = table.horizontalHeader()
        if table.columnCount() == 0:
            return
        widths = [
            w.sizeHint().width()
            for r in range(table.rowCount())
            if table.columnSpan(r, 0) == 1 and (w := table.cellWidget(r, 0)) is not None
        ]
        label = table.horizontalHeaderItem(0)
        head = header.fontMetrics().horizontalAdvance(label.text()) + 24 if label else 0
        others = sum(header.sectionSize(c) for c in range(1, table.columnCount()))
        room = table.viewport().width() - others - 4
        content = max([head, *(w + 16 for w in widths)])
        width = max(FEATURE_MIN, min(content, FEATURE_MAX, room if room > 0 else FEATURE_MAX))
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.resizeSection(0, width)

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802
        if obj is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_feature_column()
        return super().eventFilter(obj, event)

    @staticmethod
    def _effect_text(r: cmp.FeatureResult) -> str:
        effect = fmt_effect(r.effect)
        if r.effect is not None and r.effect > 0 and r.effect_name in ("delta", "g"):
            effect = "+" + effect
        return effect

    def _row_of(self, column: str) -> int:
        for row, entry in enumerate(getattr(self, "_row_items", [])):
            if not isinstance(entry, str) and entry.column == column:
                return row
        return -1

    def _row_changed(self, row: int) -> None:
        items = getattr(self, "_row_items", [])
        if 0 <= row < len(items) and not isinstance(items[row], str):
            self._show_feature(items[row])

    def select_feature(self, column: str) -> bool:
        row = self._row_of(column)
        if row < 0:
            return False
        self.table.selectRow(row)
        self._row_changed(row)
        return True

    def step(self, delta: int) -> None:
        columns = [r.column for r in self._shown]
        if self._current not in columns:
            return
        index = columns.index(self._current) + delta
        if 0 <= index < len(columns):
            self.select_feature(columns[index])

    # --- panel -------------------------------------------------------------------------

    def _show_feature(self, r: cmp.FeatureResult | None) -> None:
        result = self._result
        tables = (self.groups_table, self.pairs_table)
        if r is None or result is None:
            self._current = ""
            for widget in (self.panel_title, self.panel_sub, self.test_label, self.effect_label):
                widget.setText("")
            self.panel_note.setText("")
            self.version_note.setText("")
            self.effect_bar.setValue(0)
            self.boxplot.set_groups([])
            for table in tables:
                table.setRowCount(0)
            self._fill_normality(None)
            return
        self._current = r.column
        label, unit = self.feature_label(r.column)
        self.panel_title.setText(label + (f" · {unit}" if unit else ""))
        description = self.feature_parts(r.column)[3]
        sub = description or r.column
        if result.log and r.note != "log":
            sub += tr(" · po logaritmu")
        self.panel_sub.setText(sub)
        self.panel_sub.setToolTip(r.column)
        groups = result.groups
        self.boxplot.set_groups(
            [
                (self.short(i), color_of(i), [float(v) for v in r.values[i]])
                for i in range(len(groups))
            ]
        )
        self.panel_content.layout().activate()  # graf jiné výšky (počet skupin) hned
        muted = theme.MUTED
        if r.tested:
            test = TEST_NAMES.get(r.test, r.test)
            q_text, p_text = escape(fmt_p(r.q)), escape(fmt_p(r.p))
            q = f"<b>q {q_text}</b>" if r.significant else f"q {q_text}"
            dot = f"<span style='color:{muted}'>·</span>"
            self.test_label.setText(f"<b>{test}</b> {dot} p {p_text} {dot} {q}")
            symbol = EFFECT_SYMBOLS.get(r.effect_name, "")
            word = effect_size_word(r.effect_name, r.effect)
            text = f"{symbol} {self._effect_text(r)} · <b>{word}</b>"
            if r.other_effect is not None:
                other = "g" if r.effect_name == "delta" else "δ"
                extra = f"{other} {fmt_effect(r.other_effect)}"
                text += f" <span style='color:{muted}'>· {extra}</span>"
            self.effect_label.setText(text)
            self.effect_bar.setValue(int(effect_fraction(r.effect_name, r.effect) * 100))
            self.effect_bar.show()
            self.panel_note.setText("")
        else:
            self.test_label.setText("")
            self.effect_label.setText("")
            self.effect_bar.hide()
            self.panel_note.setText(
                tr("Netestováno: méně než 3 hodnoty ve skupině, nebo nejde logaritmovat.")
            )
        self._fill_groups_table(r, groups)
        self._fill_normality(r)
        self._fill_pairs(r)
        if r.versions:
            versions = ", ".join(f"{k} {v}" for k, v in r.versions.items())
            self.version_note.setText(
                tr("Jiná verze feature ve skupinách ({v}); rozdíl může být metodický.").format(
                    v=versions
                )
            )
        else:
            self.version_note.setText("")
        columns = [x.column for x in self._shown]
        pos = columns.index(r.column) if r.column in columns else -1
        self.prev_btn.setEnabled(pos > 0)
        self.next_btn.setEnabled(0 <= pos < len(columns) - 1)

    def _group_name_item(self, table: QTableWidget, row: int, index: int) -> None:
        item = set_text(table, row, 0, f"● {self.short(index)}", bold=True)
        item.setForeground(QColor(color_of(index)))
        groups = self._result.groups if self._result is not None else []
        if index < len(groups):
            item.setToolTip(groups[index].name)

    def _fill_groups_table(self, r: cmp.FeatureResult, groups: list[cmp.Group]) -> None:
        table = self.groups_table
        parametric = r.test in ("t", "ANOVA") or (not r.test and r.center == r.mean)
        heads = ["", "x̄", "SD"] if parametric else ["", tr("medián"), tr("IQR")]
        table.setHorizontalHeaderLabels([h if h in ("x̄", "SD") else h.upper() for h in heads])
        table.setRowCount(len(groups))
        for i in range(len(groups)):
            self._group_name_item(table, i, i)
            median = f"{fmt(r.median[i])} ({fmt(r.q1[i])}–{fmt(r.q3[i])})"
            mean = f"{fmt(r.mean[i])} ± {fmt(r.sd[i])}"
            other = tr("medián {m}").format(m=median) if parametric else f"x̄ ± SD {mean}"
            if parametric:
                center, spread = fmt(r.mean[i]), fmt(r.sd[i])
            else:
                center, spread = fmt(r.median[i]), f"{fmt(r.q1[i])}–{fmt(r.q3[i])}"
            set_text(table, i, 1, center, right=True, tooltip=other)
            set_text(table, i, 2, spread, right=True, muted=True, tooltip=other)
        fit_height(table)

    def _fill_normality(self, r: cmp.FeatureResult | None) -> None:
        while self.normal_flow.count():
            item = self.normal_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        if r is None:
            return
        for i, p in enumerate(r.shapiro):
            g = self.short(i)
            if p is None:
                text, role = tr("{g} málo hodnot").format(g=g), "neutral"
            elif p >= cmp.ALPHA:
                text, role = tr("{g} ≈ normální · p {p}").format(g=g, p=fmt_p(p)), "ok"
            else:
                text, role = tr("{g} šikmé · p {p}").format(g=g, p=fmt_p(p)), "warn"
            pill = Pill(text, role)
            pill.setToolTip(tr("šikmost {s}").format(s=fmt(r.skew[i], 2)))
            self.normal_flow.addWidget(pill)

    def _fill_pairs(self, r: cmp.FeatureResult) -> None:
        table = self.pairs_table
        table.setVisible(bool(r.pairs))
        if not r.pairs:
            table.setRowCount(0)
            return
        symbol = "g" if r.effect_name == "eta2" else "δ"
        header = table.horizontalHeaderItem(1)
        if header is not None:
            header.setText(symbol)
        table.setRowCount(len(r.pairs))
        for row, pair in enumerate(r.pairs):
            set_text(table, row, 0, f"{self.short(pair.a)} – {self.short(pair.b)}")
            effect = fmt_effect(pair.effect)
            if pair.effect is not None and pair.effect > 0:
                effect = "+" + effect
            set_text(table, row, 1, effect, right=True)
            significant = pair.q is not None and pair.q < cmp.ALPHA
            item = set_text(table, row, 2, fmt_p(pair.q), right=True, muted=not significant)
            if significant:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        fit_height(table)

    # --- export ------------------------------------------------------------------------

    def export_csv(self, path: Path | None = None) -> Path | None:
        """Tabulka porovnání do CSV; metoda a nastavení v prvních řádcích s #."""
        result = self._result
        if result is None:
            return None
        if path is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self, tr("Export porovnání"), "porovnani.csv", CSV_FILTER
            )
            if not chosen:
                return None
            path = Path(chosen)
        frame = cmp.to_frame(result)
        lines = [
            f"# method: {result.method}",
            f"# by_subject: {result.by_subject}",
            f"# log: {result.log}",
            "# correction: FDR (Benjamini-Hochberg)",
            "# groups: " + "; ".join(g.name for g in result.groups),
        ]
        try:
            with path.open("w", encoding="utf-8", newline="") as fh:
                fh.write("\n".join(lines) + "\n")
                frame.to_csv(fh, index=False)
        except OSError as exc:
            self.notice.emit(str(exc))
            return None
        self.notice.emit(tr("Porovnání uloženo: {path}").format(path=path))
        return path


_DOTS: dict[str, QIcon] = {}


def _dot_icon(color: str) -> QIcon:
    """Tečka u q: zelená = liší se, šedá = ne."""
    if color not in _DOTS:
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(5, 5, 10, 10)
        painter.end()
        _DOTS[color] = QIcon(pixmap)
    return _DOTS[color]
