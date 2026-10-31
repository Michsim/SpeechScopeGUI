"""Tabulka hodnot běhu (Výsledky › Hodnoty), podoba „A + karta sloupce“.

Mockup https://claude.ai/artifact/3zTRx5diHQquy8ZjSMU6WW, `docs/mockups/vysledky-hodnoty/`
mimo git (31. 10. 2026). Model `ValuesModel` ukazuje `features.csv` v pořadí
nahrávka · stav · pacient (zmrazené vlevo), metadata, feature, technické sloupce;
`ValuesModel.frame` zůstává tabulka po filtru v pořadí CSV (detail, testy).
Záhlaví `BandHeader` má dvě patra: nahoře pruh feature („PITCH · F0“) přes její
sloupce, dole krátký název z katalogu a jednotka ze závorky v popisu. Čísla jsou
zarovnaná vpravo s desetinnými místy podle velikosti hodnot sloupce, stav je
štítek ok · poznámka · chyba (`StatusDelegate`), chybný řádek ukáže text chyby
přes sloupce feature (span nastavuje stránka). `FooterBar` pod tabulkou kreslí
průměr ± SD zarovnaný se sloupci, `ColumnCard` nad tabulkou statistiku jednoho
sloupce s histogramem (`Histogram`) po kliku na záhlaví. Statistika se počítá
jen z řádků bez chyby v aktuálním filtru (`column_stats`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend import meta, quality
from ...backend.library import FeatureInfo
from ...backend.quality import Issue
from ...i18n import tr
from .. import theme
from ..feature_detail import split_unit
from ..recording_detail import column_index

STATUS = "\x00status"  # sloupec stavu, v CSV není
LEAD_META = (meta.SUBJECT,)  # zmrazené vlevo za stavem, když v tabulce jsou
TECH_COLUMNS = ("notes", "error", "task", "path", "speechscope_version")
BAND_ROLE = Qt.ItemDataRole.UserRole + 21  # klíč pruhu (feature) v headerData
BAND_LABEL_ROLE = Qt.ItemDataRole.UserRole + 22
UNIT_ROLE = Qt.ItemDataRole.UserRole + 23
FOOTER_ROLE = Qt.ItemDataRole.UserRole + 24
STATUS_ROLE = Qt.ItemDataRole.UserRole + 25  # ok | warn | missing
NUMERIC_ROLE = Qt.ItemDataRole.UserRole + 26
ERROR_TEXT_ROLE = Qt.ItemDataRole.UserRole + 27  # text chyby na začátku spanu přes feature
BAND_COLORS = ("#c7d2fe", "#bbf7d0", "#fde68a", "#fbcfe8", "#a5f3fc", "#ddd6fe", "#fed7aa")
SELECTED_COLUMN = "#eff6ff"
SELECTED_ROW = theme.ACCENT_SOFT
# Výběr bez proužku stylu Windows 11 (kreslil modrou čárku u každé buňky řádku);
# pravidlo jen pro :selected, podbarvení řádků (chyba, poznámka) dál kreslí styl.
TABLE_STYLE = (
    f"QTableView::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}"
)
ERROR_ROW = QColor(254, 242, 242)
NOTE_ROW = QColor(255, 251, 235)
BAD_ROW = QColor(255, 237, 213)  # kvalita nahrávky: nepoužitelná
HIST_BAR = "#93c5fd"


def group_short(group: str) -> str:
    """„Akustika · kvalita hlasu“ → „Kvalita hlasu“; neznámá skupina poslední částí klíče."""
    label = contract.GROUP_LABELS.get(group, "")
    text = label.split(" · ")[-1] if label else group.rsplit(".", 1)[-1].replace("_", " ")
    return text[:1].upper() + text[1:]


@dataclass(slots=True)
class ColumnMeta:
    """Co tabulka ví o jednom sloupci pohledu."""

    key: str  # sloupec CSV nebo STATUS
    label: str  # dolní patro záhlaví
    unit: str = ""
    band: str = ""  # klíč pruhu (název feature), prázdno = bez pruhu
    band_label: str = ""
    tooltip: str = ""
    numeric: bool = False
    decimals: int = 2
    feature: FeatureInfo | None = None
    description: str = ""


@dataclass(slots=True)
class ColumnStats:
    mean: float
    sd: float
    median: float
    low: float
    high: float
    n: int


def column_stats(values: pd.Series) -> ColumnStats | None:
    """Průměr, SD (n − 1), medián, rozsah a počet hodnot; bez hodnot None."""
    numbers = pd.to_numeric(values, errors="coerce").dropna()
    if numbers.empty:
        return None
    sd = float(numbers.std(ddof=1)) if len(numbers) > 1 else 0.0
    return ColumnStats(
        mean=float(numbers.mean()),
        sd=sd,
        median=float(numbers.median()),
        low=float(numbers.min()),
        high=float(numbers.max()),
        n=int(len(numbers)),
    )


def decimals_for(values: pd.Series) -> int:
    """Desetinná místa pro asi tři platné číslice podle mediánu velikosti hodnot."""
    numbers = pd.to_numeric(values, errors="coerce").dropna()
    if numbers.empty:
        return 2
    if pd.api.types.is_integer_dtype(values) or bool((numbers == numbers.round()).all()):
        return 0
    magnitude = float(numbers.abs().median())
    if magnitude == 0 or not math.isfinite(magnitude):
        return 2
    return max(0, min(4, 2 - math.floor(math.log10(magnitude))))


def format_number(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def format_mean_sd(stats: ColumnStats, decimals: int, sep: str = " ± ") -> str:
    """Průměr ± SD; u celých čísel (počty) s jedním desetinným místem."""
    d = max(decimals, 1)
    return f"{format_number(stats.mean, d)}{sep}{format_number(stats.sd, d)}"


def problem_mask(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = frame[column]
    return values.notna() & (values.astype(str).str.strip() != "")


class ValuesModel(QAbstractTableModel):
    """`frame` (po filtru, v pořadí CSV) ukázaný ve sloupcích `columns` (pořadí pohledu)."""

    def __init__(
        self,
        frame: pd.DataFrame,
        catalog: list[FeatureInfo] | None = None,
        footer: dict[str, str] | None = None,
        quality: list[list[Issue]] | None = None,
    ) -> None:
        super().__init__()
        self.frame = frame
        self._errors = problem_mask(frame, "error")
        self._notes = problem_mask(frame, "notes")
        # kontrola kvality (`backend/quality.py`) po řádcích `frame`; prázdný seznam = ok
        self.quality: list[list[Issue]] = (
            quality if quality is not None else [[] for _ in range(len(frame))]
        )
        self.highlight = ""  # sloupec s otevřenou kartou
        self.footer = footer or {}
        index = column_index(catalog or [])
        lead = ["file"] if "file" in frame.columns else []
        lead.append(STATUS)
        lead += [c for c in LEAD_META if c in frame.columns]
        meta_cols, feature_cols, tech = [], [], []
        for column in frame.columns:
            column = str(column)
            if column in lead:
                continue
            if column in index:
                feature_cols.append(column)
            elif column in TECH_COLUMNS:
                tech.append(column)
            else:
                meta_cols.append(column)
        tech.sort(key=TECH_COLUMNS.index)
        self.frozen = len(lead)
        self.columns: list[ColumnMeta] = []
        for key in lead + meta_cols + feature_cols + tech:
            self.columns.append(self._meta(key, index))
        self._positions = {c.key: i for i, c in enumerate(self.columns)}

    def _meta(self, key: str, index: dict) -> ColumnMeta:
        if key == STATUS:
            return ColumnMeta(STATUS, tr("stav"), tooltip=tr("ok, poznámka nebo chyba"))
        if key == "file":
            return ColumnMeta(key, tr("nahrávka"), tooltip="file")
        info = index.get(key)
        if info is None:
            label = meta.label(key) if key in meta.META_COLUMNS else key
            numeric = key not in TECH_COLUMNS and pd.api.types.is_numeric_dtype(self.frame[key])
            return ColumnMeta(
                key,
                label,
                tooltip=key,
                numeric=numeric,
                decimals=decimals_for(self.frame[key]) if numeric else 0,
            )
        feature: FeatureInfo = info.feature
        text, unit = split_unit(info.description)
        short = info.short.replace("_", " ")
        band_label = f"{group_short(feature.group)} · {feature.name.rsplit('.', 1)[-1]}"
        band_label = band_label.replace("_", " ")
        tooltip = "\n".join(
            part
            for part in (
                f"{band_label} · {short}" + (f" ({unit})" if unit else ""),
                text,
                tr("{column} · verze {version}").format(column=key, version=feature.version),
            )
            if part
        )
        numeric = pd.api.types.is_numeric_dtype(self.frame[key])
        return ColumnMeta(
            key,
            short,
            unit=unit,
            band=feature.name,
            band_label=band_label,
            tooltip=tooltip,
            numeric=numeric,
            decimals=decimals_for(self.frame[key]) if numeric else 0,
            feature=feature,
            description=text,
        )

    # --- dotazy stránky -------------------------------------------------------------------

    def position(self, key: str) -> int:
        """Sloupec pohledu se sloupcem CSV `key`; -1, když tam není."""
        return self._positions.get(key, -1)

    def key(self, section: int) -> str:
        return self.columns[section].key if 0 <= section < len(self.columns) else ""

    def column(self, key: str) -> ColumnMeta | None:
        i = self.position(key)
        return self.columns[i] if i >= 0 else None

    def feature_span(self) -> tuple[int, int] | None:
        """První sloupec feature a jejich počet (pro text chyby přes řádek)."""
        sections = [i for i, c in enumerate(self.columns) if c.feature is not None]
        return (sections[0], sections[-1] - sections[0] + 1) if sections else None

    def is_error(self, row: int) -> bool:
        return bool(self._errors.iloc[row]) if 0 <= row < len(self.frame) else False

    def set_highlight(self, key: str) -> None:
        self.highlight = key
        if self.columns:
            self.dataChanged.emit(
                self.index(0, 0), self.index(max(0, len(self.frame) - 1), len(self.columns) - 1)
            )
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)

    # --- QAbstractTableModel --------------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self.frame)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self.columns)

    def quality_level(self, row: int) -> str:
        """Nejhorší kontrola kvality řádku bez poznámek knihovny: `bad`, `warn` nebo prázdné."""
        issues = self.quality[row] if 0 <= row < len(self.quality) else []
        return quality.level_of([i for i in issues if i.check.key != quality.NOTES_KEY])

    def _status(self, row: int) -> tuple[str, str, str]:
        if self._errors.iloc[row]:
            return tr("chyba"), "missing", str(self.frame["error"].iloc[row])
        issues = self.quality[row] if 0 <= row < len(self.quality) else []
        level = self.quality_level(row)
        if level == quality.BAD:
            return tr("nepoužitelná"), "missing", quality.summary(issues)
        if level == quality.WARN:
            return tr("kvalita"), "warn", quality.summary(issues)
        if self._notes.iloc[row]:
            return tr("poznámka"), "warn", str(self.frame["notes"].iloc[row])
        return tr("ok"), "ok", ""

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), self.columns[index.column()]
        if col.key == STATUS:
            text, status, tip = self._status(row)
            if role == Qt.ItemDataRole.DisplayRole:
                return text
            if role == STATUS_ROLE:
                return status
            if role == Qt.ItemDataRole.ToolTipRole:
                return tip or None
            return None
        if col.feature is not None and self._errors.iloc[row]:
            span = self.feature_span()
            at_start = span is not None and index.column() == span[0]
            if role in (ERROR_TEXT_ROLE, Qt.ItemDataRole.ToolTipRole) and at_start:
                return str(self.frame["error"].iloc[row])
            if role == Qt.ItemDataRole.DisplayRole:
                return ""  # text kreslí ErrorSpanDelegate, šířku sloupce nesmí ovlivnit
        if role == Qt.ItemDataRole.DisplayRole:
            value = self.frame[col.key].iloc[row]
            if pd.isna(value):
                return ""
            if col.numeric and isinstance(value, (int, float)):
                return format_number(float(value), col.decimals)
            return str(value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col.numeric and not (col.feature is not None and self._errors.iloc[row]):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            if col.feature is not None and self._errors.iloc[row]:
                return QColor("#991b1b")
            if col.key in LEAD_META or col.key in TECH_COLUMNS:
                return QColor(theme.MUTED)
            return None
        if role == Qt.ItemDataRole.FontRole and col.key == "file":
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.BackgroundRole:
            if self._errors.iloc[row]:
                return ERROR_ROW
            level = self.quality_level(row)
            if level == quality.BAD:
                return BAD_ROW
            if self._notes.iloc[row] or level == quality.WARN:
                return NOTE_ROW
            if self.highlight and col.key == self.highlight:
                return QColor(SELECTED_COLUMN)
            return None
        if role == Qt.ItemDataRole.ToolTipRole and col.key == "file" and "path" in self.frame:
            return str(self.frame["path"].iloc[row])
        return None

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return str(section + 1) if role == Qt.ItemDataRole.DisplayRole else None
        if not 0 <= section < len(self.columns):
            return None
        col = self.columns[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return col.label.upper()
        if role == Qt.ItemDataRole.ToolTipRole:
            return col.tooltip
        if role == UNIT_ROLE:
            return col.unit
        if role == BAND_ROLE:
            return col.band
        if role == BAND_LABEL_ROLE:
            return col.band_label.upper()
        if role == FOOTER_ROLE:
            return self.footer.get(col.key, "")
        if role == NUMERIC_ROLE:
            return col.numeric
        if role == Qt.ItemDataRole.UserRole:
            return col.key
        if role == Qt.ItemDataRole.BackgroundRole and self.highlight == col.key:
            return QColor(theme.ACCENT_SOFT)
        return None


class ErrorSpanDelegate(QStyledItemDelegate):
    """Text chyby přes sloupce feature; začíná za zmrazenou částí, i když je začátek
    spanu odrolovaný pod ní."""

    def __init__(self, view: QTableView, frozen_width) -> None:  # noqa: ANN001
        super().__init__(view)
        self._frozen_width = frozen_width

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: ANN001
        super().paint(painter, option, index)
        text = index.data(ERROR_TEXT_ROLE)
        if not text:
            return
        rect = option.rect
        left = max(rect.left(), self._frozen_width()) + 10
        painter.save()
        painter.setPen(QColor("#991b1b"))
        painter.drawText(
            QRect(left, rect.top(), max(0, rect.right() - left - 4), rect.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            str(text),
        )
        painter.restore()


class StatusDelegate(QStyledItemDelegate):
    """Štítek stavu (ok, poznámka, chyba) místo textu."""

    COLORS = {
        "ok": (theme.OK_SOFT, "#166534"),
        "warn": (theme.WARN_SOFT, "#92400e"),
        "missing": (theme.MISSING_SOFT, "#991b1b"),
    }

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: ANN001
        background = index.data(Qt.ItemDataRole.BackgroundRole)
        if isinstance(background, (QColor, QBrush)):
            painter.fillRect(option.rect, background)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(SELECTED_ROW))
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        soft, strong = self.COLORS.get(index.data(STATUS_ROLE) or "", self.COLORS["ok"])
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 0.85)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 18
        height = metrics.height() + 4
        rect = QRectF(
            option.rect.left() + 8,
            option.rect.center().y() - height / 2 + 1,
            width,
            height,
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(soft))
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setPen(QColor(strong))
        painter.setFont(font)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: ANN001, N802
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 0.85)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        return QSize(QFontMetrics(font).horizontalAdvance(text) + 34, 28)


class BandHeader(QHeaderView):
    """Záhlaví ve dvou patrech: pruh feature nahoře, krátký název a jednotka dole."""

    BAND = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setMinimumSectionSize(40)
        # levý okraj textu pruhu (za zmrazenou částí), jinak se popisek odrolované
        # feature schová pod zmrazené sloupce
        self.text_left = lambda: 0

    def _fonts(self) -> tuple[QFont, QFont, QFont]:
        bold = QFont(self.font())
        bold.setBold(True)
        small = QFont(self.font())
        small.setPointSizeF(small.pointSizeF() * 0.85)
        band = QFont(small)
        band.setPointSizeF(small.pointSizeF() * 0.95)
        return bold, small, band

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        bold, small, _band = self._fonts()
        height = self.BAND + QFontMetrics(bold).height() + QFontMetrics(small).height() + 14
        return QSize(hint.width(), height)

    def sectionSizeFromContents(self, logical: int) -> QSize:  # noqa: N802
        model = self.model()
        bold, small, _band = self._fonts()
        if model is None:
            return super().sectionSizeFromContents(logical)
        horizontal = Qt.Orientation.Horizontal
        label = str(model.headerData(logical, horizontal) or "")
        unit = str(model.headerData(logical, horizontal, UNIT_ROLE) or "")
        footer = str(model.headerData(logical, horizontal, FOOTER_ROLE) or "")
        width = max(
            QFontMetrics(bold).horizontalAdvance(label),
            QFontMetrics(small).horizontalAdvance(unit),
            QFontMetrics(self.font()).horizontalAdvance(footer),
        )
        return QSize(width + 24, self.sizeHint().height())

    def paintSection(self, painter: QPainter, rect: QRect, logical: int) -> None:  # noqa: N802
        model = self.model()
        if model is None or not rect.isValid():
            return
        horizontal = Qt.Orientation.Horizontal
        bold, small, _band = self._fonts()
        painter.save()
        painter.fillRect(rect, QColor("#f9fafb"))
        lower = QRect(rect.left(), rect.top() + self.BAND, rect.width(), rect.height() - self.BAND)
        highlighted = model.headerData(logical, horizontal, Qt.ItemDataRole.BackgroundRole)
        if highlighted is not None:
            painter.fillRect(lower.adjusted(1, 0, -1, 0), highlighted)
        band = model.headerData(logical, horizontal, BAND_ROLE) or ""
        if band:
            color = QColor(BAND_COLORS[_band_index(model, band) % len(BAND_COLORS)])
            next_band = (
                model.headerData(logical + 1, horizontal, BAND_ROLE)
                if logical + 1 < model.columnCount()
                else None
            )
            right = rect.right() - (6 if next_band != band else 0)
            painter.fillRect(
                QRect(rect.left(), rect.top() + self.BAND - 3, right - rect.left() + 1, 2), color
            )
        label = str(model.headerData(logical, horizontal) or "")
        unit = str(model.headerData(logical, horizontal, UNIT_ROLE) or "")
        numeric = bool(model.headerData(logical, horizontal, NUMERIC_ROLE))
        align = Qt.AlignmentFlag.AlignRight if numeric else Qt.AlignmentFlag.AlignLeft
        text_rect = lower.adjusted(10, 4, -10, -6)
        painter.setPen(QColor(theme.ACCENT_HOVER if highlighted is not None else theme.MUTED))
        painter.setFont(bold)
        line = QFontMetrics(bold).height()
        painter.drawText(
            QRect(text_rect.left(), text_rect.top(), text_rect.width(), line),
            int(align | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(bold).elidedText(label, Qt.TextElideMode.ElideRight, text_rect.width()),
        )
        if unit:
            painter.setFont(small)
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(
                QRect(
                    text_rect.left(),
                    text_rect.top() + line,
                    text_rect.width(),
                    text_rect.height() - line,
                ),
                int(align | Qt.AlignmentFlag.AlignTop),
                unit,
            )
        painter.setPen(QColor(theme.BORDER))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        super().paintEvent(event)
        model = self.model()
        if model is None:
            return
        # popisky pruhů přes všechny sloupce téže feature (sekce je kreslí jen čarou)
        horizontal = Qt.Orientation.Horizontal
        _bold, _small, band_font = self._fonts()
        metrics = QFontMetrics(band_font)
        painter = QPainter(self.viewport())
        painter.setFont(band_font)
        painter.setPen(QColor(theme.MUTED))
        count = model.columnCount()
        logical = 0
        while logical < count:
            band = model.headerData(logical, horizontal, BAND_ROLE) or ""
            end = logical
            while (
                end + 1 < count and (model.headerData(end + 1, horizontal, BAND_ROLE) or "") == band
            ):
                end += 1
            if band and not all(self.isSectionHidden(i) for i in range(logical, end + 1)):
                left = max(self.sectionViewportPosition(logical), self.text_left())
                right = self.sectionViewportPosition(end) + self.sectionSize(end)
                text = str(model.headerData(logical, horizontal, BAND_LABEL_ROLE) or "")
                width = right - left - 16
                if width > 20:
                    painter.drawText(
                        QRect(left + 10, 2, width, self.BAND - 5),
                        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                        metrics.elidedText(text, Qt.TextElideMode.ElideRight, width),
                    )
            logical = end + 1
        painter.end()


def _band_index(model, band: str) -> int:  # noqa: ANN001
    """Pořadí pruhu v tabulce (střídání barev), cache na modelu."""
    cache = getattr(model, "_band_order", None)
    if cache is None:
        cache = {}
        for i in range(model.columnCount()):
            key = model.headerData(i, Qt.Orientation.Horizontal, BAND_ROLE) or ""
            if key and key not in cache:
                cache[key] = len(cache)
        model._band_order = cache  # noqa: SLF001
    return cache.get(band, 0)


class FooterBar(QWidget):
    """Řádek průměr ± SD pod tabulkou, zarovnaný se sloupci (i při posunu)."""

    def __init__(self, table: QTableView, label: str) -> None:
        super().__init__()
        self.table = table
        self.label = label
        self.frozen = 1
        header = table.horizontalHeader()
        header.sectionResized.connect(lambda *_a: self.update())
        header.geometriesChanged.connect(self.update)
        table.horizontalScrollBar().valueChanged.connect(lambda _v: self.update())
        self.setFixedHeight(QFontMetrics(self.font()).height() + 14)

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        model = self.table.model()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f9fafb"))
        painter.setPen(QColor(theme.BORDER))
        painter.drawLine(0, 0, self.width(), 0)
        if model is None:
            return
        header = self.table.horizontalHeader()
        offset = self.table.verticalHeader().width() + self.table.frameWidth()
        frozen_width = sum(header.sectionSize(i) for i in range(self.frozen))
        bold = QFont(self.font())
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRect(offset + 10, 0, frozen_width - 12, self.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.label,
        )
        painter.setFont(self.font())
        painter.setClipRect(QRect(offset + frozen_width, 0, self.width(), self.height()))
        for section in range(self.frozen, model.columnCount()):
            text = model.headerData(section, Qt.Orientation.Horizontal, FOOTER_ROLE)
            if not text:
                continue
            left = offset + header.sectionViewportPosition(section)
            painter.drawText(
                QRect(left, 0, header.sectionSize(section) - 10, self.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                str(text),
            )
        painter.end()


class Histogram(QWidget):
    """Sloupcový histogram hodnot jednoho sloupce; vybraná nahrávka tmavě, průměr
    čárkovaně."""

    def __init__(self) -> None:
        super().__init__()
        self.values: list[float] = []
        self.selected: float | None = None
        self.mean: float | None = None
        self.setMinimumHeight(64)
        self.setMinimumWidth(160)

    def set_data(
        self,
        values: list[float],
        selected: float | None,
        mean: float | None,
    ) -> None:
        self.values, self.selected, self.mean = values, selected, mean
        self.update()

    def bins(self) -> list[int]:
        if not self.values:
            return []
        k = max(6, min(16, 2 * round(math.sqrt(len(self.values)))))
        low, high = min(self.values), max(self.values)
        if high == low:
            return [len(self.values)]
        counts = [0] * k
        for v in self.values:
            counts[min(k - 1, int((v - low) / (high - low) * k))] += 1
        return counts

    def _bin_of(self, value: float, k: int) -> int:
        low, high = min(self.values), max(self.values)
        if high == low:
            return 0
        return min(k - 1, max(0, int((value - low) / (high - low) * k)))

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        counts = self.bins()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.rect().adjusted(0, 4, 0, -2)
        if not counts:
            painter.end()
            return
        k = len(counts)
        top = max(counts)
        gap = 4
        width = (area.width() - gap * (k - 1)) / k
        chosen = self._bin_of(self.selected, k) if self.selected is not None else -1
        for i, count in enumerate(counts):
            if not count:
                continue
            height = (area.height() - 2) * count / top
            x = area.left() + i * (width + gap)
            color = theme.ACCENT if i == chosen else HIST_BAR
            painter.fillRect(QRectF(x, area.bottom() - height, width, height), QColor(color))
        painter.setPen(QColor(theme.BORDER))
        painter.drawLine(area.left(), area.bottom(), area.right(), area.bottom())
        if self.mean is not None and len(self.values) > 1:
            low, high = min(self.values), max(self.values)
            if high > low:
                x = area.left() + (self.mean - low) / (high - low) * area.width()
                pen = QPen(QColor(theme.TEXT))
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
        painter.end()


class ColumnCard(QFrame):
    """Karta jednoho sloupce nad tabulkou: popis, histogram, průměr ± SD, medián, rozsah."""

    closed = Signal()
    step_requested = Signal(int)  # -1 předchozí, +1 další sloupec

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        theme.set_role(self, "accent")
        self.key = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(20)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.group_label = QLabel("")
        self.group_label.setObjectName("muted")
        self.name_label = QLabel("")
        font = self.name_label.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.2)
        self.name_label.setFont(font)
        self.description = QLabel("")
        self.description.setWordWrap(True)
        self.key_label = QLabel("")
        self.key_label.setObjectName("muted")
        self.key_label.setWordWrap(True)
        for widget in (self.group_label, self.name_label, self.description, self.key_label):
            info.addWidget(widget)
        info.addStretch(1)
        info_box = QWidget()
        info_box.setLayout(info)
        info_box.setFixedWidth(260)
        layout.addWidget(info_box)
        middle = QVBoxLayout()
        middle.setSpacing(2)
        self.histogram = Histogram()
        middle.addWidget(self.histogram, 1)
        scale = QHBoxLayout()
        self.low_label = QLabel("")
        self.low_label.setObjectName("muted")
        self.legend = QLabel(tr("tmavě vybraná nahrávka · čárkovaně průměr"))
        self.legend.setObjectName("muted")
        self.legend.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.high_label = QLabel("")
        self.high_label.setObjectName("muted")
        scale.addWidget(self.low_label)
        scale.addWidget(self.legend, 1)
        scale.addWidget(self.high_label)
        middle.addLayout(scale)
        layout.addLayout(middle, 1)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        self.stat_labels: dict[str, QLabel] = {}
        for row, (key, caption) in enumerate(
            (
                ("mean", tr("průměr ± SD")),
                ("median", tr("medián")),
                ("range", tr("min – max")),
                ("n", tr("hodnot")),
            )
        ):
            name = QLabel(caption)
            name.setObjectName("muted")
            value = QLabel("")
            if key == "mean":
                bold = value.font()
                bold.setBold(True)
                value.setFont(bold)
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self.stat_labels[key] = value
        layout.addLayout(grid)
        buttons = QVBoxLayout()
        buttons.setSpacing(4)
        self.close_btn = QPushButton("×")
        self.close_btn.setToolTip(tr("Zavřít kartu sloupce"))
        self.close_btn.clicked.connect(lambda: self.closed.emit())
        self.prev_btn = QPushButton("‹")
        self.prev_btn.setToolTip(tr("Předchozí sloupec"))
        self.prev_btn.clicked.connect(lambda: self.step_requested.emit(-1))
        self.next_btn = QPushButton("›")
        self.next_btn.setToolTip(tr("Další sloupec"))
        self.next_btn.clicked.connect(lambda: self.step_requested.emit(1))
        for btn in (self.close_btn, self.prev_btn, self.next_btn):
            btn.setFixedWidth(36)
            buttons.addWidget(btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def show_column(
        self,
        column: ColumnMeta,
        values: pd.Series,
        selected: float | None,
        errors: int,
    ) -> None:
        """`values` = hodnoty bez chybných řádků."""
        self.key = column.key
        stats = column_stats(values)
        self.group_label.setText(column.band_label.upper())
        unit = f"  ({column.unit})" if column.unit else ""
        self.name_label.setText(column.label.upper() + unit)
        self.description.setText(column.description)
        self.description.setVisible(bool(column.description))
        version = column.feature.version if column.feature is not None else ""
        self.key_label.setText(
            tr("{column} · verze {version}").format(column=column.key, version=version)
            if version
            else column.key
        )
        d = column.decimals
        numbers = [float(v) for v in pd.to_numeric(values, errors="coerce").dropna()]
        if stats is None:
            for label in self.stat_labels.values():
                label.setText("–")
            self.low_label.setText("")
            self.high_label.setText("")
            self.histogram.set_data([], None, None)
            return
        self.stat_labels["mean"].setText(format_mean_sd(stats, d))
        self.stat_labels["median"].setText(format_number(stats.median, d))
        self.stat_labels["range"].setText(
            f"{format_number(stats.low, d)} – {format_number(stats.high, d)}"
        )
        n_text = str(stats.n)
        if errors:
            n_text += " " + tr("({n} s chybou)").format(n=errors)
        self.stat_labels["n"].setText(n_text)
        self.low_label.setText(format_number(stats.low, d))
        self.high_label.setText(format_number(stats.high, d))
        self.histogram.set_data(numbers, selected, stats.mean)


__all__ = [
    "STATUS",
    "BandHeader",
    "ColumnCard",
    "ColumnMeta",
    "ColumnStats",
    "ErrorSpanDelegate",
    "FooterBar",
    "Histogram",
    "StatusDelegate",
    "ValuesModel",
    "column_stats",
    "decimals_for",
    "group_short",
]
