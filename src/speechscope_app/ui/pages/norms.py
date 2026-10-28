"""Stránka Normy: přehled norem zdravých kontrol, import, export a smazání.

Vlevo karty norem (vlastní, pak přibalené), vpravo detail vybrané: původ,
podmínky nahrávání, kontroly po úlohách (osoby, ženy a muži, věk, sloupce),
rozložení věku a varování z `norms.norm_problems`. Přibalené normy jsou
jen ke čtení. Export je jeden soubor (`.ssnorm`, zip s norm.yaml
a controls.csv), import ho uloží jako vlastní normu vedle, nikdy nepřepíše.
Normu ze složek s nahrávkami vyrobí průvodce Nová norma (zvlášť).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.norms import (
    NORM_SUFFIX,
    Norm,
    TaskSummary,
    age_histogram,
    all_norms,
    export_norm,
    import_norm,
    norm_problems,
    norm_slug,
    task_summaries,
    trash_norm,
)
from ...i18n import tr
from .. import theme
from ..widgets.elided_label import ElidedLabel
from ..widgets.status_header import StatusHeader

NORM_FILTER = "Norma SpeechScope (*.ssnorm)"


def format_age_range(age_range: tuple[float, float] | None) -> str:
    if age_range is None:
        return "–"
    lo, hi = age_range
    return tr("{lo} až {hi}").format(lo=f"{lo:.0f}", hi=f"{hi:.0f}")


def format_date(text: str) -> str:
    try:
        day = dt.date.fromisoformat(text)
    except ValueError:
        return text
    return f"{day.day}. {day.month}. {day.year}"


def norm_overview(norm: Norm) -> str:
    """Jedna řádka na kartu: osoby, věk, počet úloh."""
    parts = [tr("{n} osob").format(n=norm.subjects)]
    people = norm.controls.drop_duplicates("subject") if "subject" in norm.controls else None
    if people is not None and "age" in people:
        ages = people["age"].dropna()
        if not ages.empty:
            parts.append(
                tr("{lo} až {hi} let").format(lo=f"{ages.min():.0f}", hi=f"{ages.max():.0f}")
            )
    parts.append(tr("úlohy: {n}").format(n=len(norm.tasks)))
    return " · ".join(parts)


class NormCard(QFrame):
    def __init__(self, norm: Norm, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.norm = norm
        self.setObjectName("card")
        problems = norm_problems(norm)
        self._role = "warn" if problems else "neutral"
        theme.set_role(self, self._role)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = ElidedLabel(norm.info.display_name)
        self.title.setObjectName("card_title")
        head.addWidget(self.title, 1)
        if problems:
            text = tr("varování: {n}").format(n=len(problems))
            role = "warn"
        else:
            text = tr("přibalená") if norm.builtin else tr("připravená")
            role = "neutral" if norm.builtin else "ok"
        self.pill = QLabel(text)
        self.pill.setObjectName("pill")
        theme.set_role(self.pill, role)
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        lines = [norm_overview(norm)]
        if norm.info.created:
            lines.append(tr("vznikla {date}").format(date=format_date(norm.info.created)))
        self.detail = QLabel("\n".join(lines))
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

    def set_current(self, current: bool) -> None:
        theme.set_role(self, "selected" if current else self._role)


class AgeHistogram(QWidget):
    """Sloupce osob po desetiletích věku; kreslí se přes QPainter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bins: list[tuple[int, int]] = []
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_bins(self, bins: list[tuple[int, int]]) -> None:
        self._bins = list(bins)
        self.update()

    def bins(self) -> list[tuple[int, int]]:
        return list(self._bins)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.CARD))
        if not self._bins:
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("kontroly nemají věk"))
            painter.end()
            return
        metrics_h = painter.fontMetrics().height()
        top, bottom = metrics_h + 4, self.height() - metrics_h - 6
        width = self.width() / len(self._bins)
        peak = max(count for _start, count in self._bins) or 1
        for i, (start, count) in enumerate(self._bins):
            h = (bottom - top) * count / peak
            bar = QRectF(i * width + 4, bottom - h, width - 8, h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(bar, 3, 3)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(
                QRectF(i * width, bar.top() - metrics_h - 2, width, metrics_h),
                int(Qt.AlignmentFlag.AlignCenter),
                str(count) if count else "",
            )
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(
                QRectF(i * width, bottom + 4, width, metrics_h),
                int(Qt.AlignmentFlag.AlignCenter),
                f"{start}–{start + 9}",
            )
        painter.end()


class NormsPage(QWidget):
    norms_changed = Signal()  # import nebo smazání: Hodnocení si normy načte znovu
    notice = Signal(str)  # krátká hláška do stavového řádku hlavního okna

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._folder: Path | None = None
        self._norms: list[Norm] = []
        self._cards: list[NormCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel(tr("Normy"))
        title.setObjectName("page_title")
        head.addWidget(title, 1)
        self.import_btn = QPushButton(tr("Import…"))
        self.import_btn.setToolTip(
            tr("Přidat normu ze souboru .ssnorm, třeba od jiného pracoviště")
        )
        self.import_btn.clicked.connect(self.import_file)
        self.folder_btn = QPushButton(tr("Otevřít složku"))
        self.folder_btn.setToolTip(tr("Složka s vlastními normami v Průzkumníku"))
        self.folder_btn.clicked.connect(self.open_folder)
        head.addWidget(self.import_btn)
        head.addWidget(self.folder_btn)
        layout.addLayout(head)
        subtitle = QLabel(
            tr(
                "Norma je tabulka zdravých kontrol, se kterou Hodnocení srovnává pacienta. "
                "Pacienta počítejte stejnými protokoly a stejnou verzí knihovny jako kontroly."
            )
        )
        subtitle.setObjectName("page_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)

        left = QWidget()
        left.setMinimumWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self.list = QListWidget()
        self.list.setObjectName("cards")
        self.list.setSpacing(3)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._row_changed)
        self.list.viewport().installEventFilter(self)
        left_layout.addWidget(self.list, 1)
        self.empty = QLabel(
            tr(
                "Zatím žádná norma. Normu dodá pracoviště jako soubor (Import…), "
                "nebo ji vyrobíte z nahrávek zdravých kontrol."
            )
        )
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.empty)
        self.splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(10)
        self.header = StatusHeader()
        self.export_btn = QPushButton(tr("Export…"))
        self.export_btn.setToolTip(tr("Norma jako jeden soubor pro kolegu nebo jiné pracoviště"))
        self.export_btn.clicked.connect(self.export_current)
        self.delete_btn = QPushButton(tr("Smazat…"))
        theme.set_role(self.delete_btn, "danger")
        self.delete_btn.clicked.connect(self.delete_current)
        self.header.add_button(self.export_btn)
        self.header.add_button(self.delete_btn)
        right_layout.addWidget(self.header)

        self.problems = QLabel("")
        self.problems.setObjectName("card")
        theme.set_role(self.problems, "warn")
        self.problems.setWordWrap(True)
        self.problems.setContentsMargins(12, 8, 12, 8)
        right_layout.addWidget(self.problems)

        table_card = QFrame()
        table_card.setObjectName("card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(12, 10, 12, 10)
        table_layout.setSpacing(6)
        table_title = QLabel(tr("Kontroly po úlohách"))
        table_title.setObjectName("card_title")
        table_layout.addWidget(table_title)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            [tr("úloha"), tr("kontrol"), tr("ženy / muži"), tr("věk"), tr("sloupců")]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        table_layout.addWidget(self.table)
        right_layout.addWidget(table_card)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        age_card = QFrame()
        age_card.setObjectName("card")
        age_layout = QVBoxLayout(age_card)
        age_layout.setContentsMargins(12, 10, 12, 10)
        age_title = QLabel(tr("Věk kontrol"))
        age_title.setObjectName("card_title")
        age_layout.addWidget(age_title)
        self.histogram = AgeHistogram()
        age_layout.addWidget(self.histogram)
        bottom.addWidget(age_card, 1)
        about_card = QFrame()
        about_card.setObjectName("card")
        about_layout = QVBoxLayout(about_card)
        about_layout.setContentsMargins(12, 10, 12, 10)
        about_title = QLabel(tr("O normě"))
        about_title.setObjectName("card_title")
        about_layout.addWidget(about_title)
        self.about = QLabel("")
        self.about.setObjectName("muted")
        self.about.setWordWrap(True)
        self.about.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.about.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        about_layout.addWidget(self.about, 1)
        bottom.addWidget(about_card, 1)
        right_layout.addLayout(bottom)
        right_layout.addStretch(1)
        self.detail = right
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([320, 800])
        self._show(None)

    # --- napojení -------------------------------------------------------------

    def set_norms_dir(self, folder: Path) -> None:
        self._folder = folder
        self.reload()

    def reload(self, select: Path | None = None) -> None:
        """Znovu načte normy; výběr zůstane na téže složce (nebo na `select`)."""
        current = self.current()
        keep = select or (current.path if current is not None else None)
        self._norms = all_norms(self._folder) if self._folder is not None else []
        # vlastní napřed: s nimi se pracuje, přibalené jsou jen ke čtení
        self._norms.sort(key=lambda n: (n.builtin, n.info.display_name.lower()))
        self.list.blockSignals(True)
        self.list.clear()
        self._cards = []
        row_to_select = 0
        for row, norm in enumerate(self._norms):
            item = QListWidgetItem()
            card = NormCard(norm)
            item.setSizeHint(card.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, card)
            self._cards.append(card)
            if keep is not None and norm.path == keep:
                row_to_select = row
        self._fit_cards()
        self.list.blockSignals(False)
        self.empty.setVisible(not self._norms)
        if self._norms:
            self.list.setCurrentRow(row_to_select)
            self._row_changed(row_to_select)
        else:
            self._show(None)

    def count(self) -> int:
        return self.list.count()

    def card(self, row: int) -> NormCard | None:
        return self._cards[row] if 0 <= row < len(self._cards) else None

    def current(self) -> Norm | None:
        row = self.list.currentRow()
        return self._norms[row] if 0 <= row < len(self._norms) else None

    def select(self, path: Path) -> bool:
        for row, norm in enumerate(self._norms):
            if norm.path == path:
                self.list.setCurrentRow(row)
                return True
        return False

    # --- seznam ---------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.list.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_cards()
        return super().eventFilter(obj, event)

    def _fit_cards(self) -> None:
        width = self.list.viewport().width() - 2 * self.list.spacing() - 4
        if width <= 0:
            return
        for row in range(self.list.count()):
            item = self.list.item(row)
            card = self.list.itemWidget(item)
            if card is not None:
                card.setFixedWidth(width)
                item.setSizeHint(QSize(width, card.sizeHint().height()))

    def _row_changed(self, row: int) -> None:
        for i, card in enumerate(self._cards):
            card.set_current(i == row)
        self._show(self.current())

    # --- detail ---------------------------------------------------------------

    def _show(self, norm: Norm | None) -> None:
        self.detail.setVisible(norm is not None)
        self.export_btn.setEnabled(norm is not None)
        self.delete_btn.setEnabled(norm is not None and not norm.builtin)
        if norm is None:
            return
        info = norm.info
        self.header.title.setText(info.display_name)
        sub = []
        if info.source:
            sub.append(tr("Zdroj: {text}").format(text=info.source))
        if info.conditions:
            sub.append(tr("Podmínky: {text}").format(text=info.conditions))
        if info.language:
            sub.append(tr("jazyk {lang}").format(lang=contract.language_label(info.language)))
        self.header.subtitle.setText(" · ".join(sub) or info.display_description)
        pills: list = [
            (
                tr("přibalená") if norm.builtin else tr("vlastní"),
                "neutral" if norm.builtin else "accent",
                "",
                None,
            )
        ]
        for summary in task_summaries(norm):
            pills.append((self._task_label(summary), "neutral", "", None))
        self.header.set_pills(pills)
        problems = norm_problems(norm)
        self.problems.setVisible(bool(problems))
        self.problems.setText("\n".join(problems))
        self.header.set_role("warn" if problems else "neutral")
        self._fill_table(task_summaries(norm))
        self.histogram.set_bins(age_histogram(norm))
        self.about.setText(self._about_text(norm))

    @staticmethod
    def _task_label(summary: TaskSummary) -> str:
        return tr("{task}: {n}").format(
            task=contract.TASK_SHORT.get(summary.task, summary.task), n=summary.subjects
        )

    def _fill_table(self, summaries: list[TaskSummary]) -> None:
        self.table.setRowCount(len(summaries))
        for row, s in enumerate(summaries):
            cells = [
                contract.TASK_LABELS.get(s.task, s.task),
                str(s.subjects),
                f"{s.female} / {s.male}",
                format_age_range(s.age_range),
                str(s.columns),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, col, item)
        height = self.table.horizontalHeader().height() + 4
        height += sum(self.table.rowHeight(r) for r in range(self.table.rowCount()))
        self.table.setFixedHeight(height)

    @staticmethod
    def _about_text(norm: Norm) -> str:
        info = norm.info
        lines = []
        if info.display_description:
            lines.append(info.display_description)
        if info.created:
            lines.append(tr("Vznikla {date}.").format(date=format_date(info.created)))
        if info.library_version:
            lines.append(tr("Knihovna {version}.").format(version=info.library_version))
        lines.append(
            str(norm.path)
            if norm.path is not None and not norm.builtin
            else tr("přibalená k aplikaci")
        )
        return "\n".join(lines)

    # --- akce -----------------------------------------------------------------

    def export_current(self) -> None:
        norm = self.current()
        if norm is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            tr("Exportovat normu"),
            norm_slug(norm.info.name) + NORM_SUFFIX,
            NORM_FILTER,
        )
        if target:
            self.export_to(Path(target))

    def export_to(self, target: Path) -> Path | None:
        norm = self.current()
        if norm is None:
            return None
        try:
            export_norm(norm, target)
        except OSError as exc:
            QMessageBox.warning(
                self, tr("SpeechScope"), tr("Normu se nepodařilo uložit: {error}").format(error=exc)
            )
            return None
        self.notice.emit(tr("Norma exportována: {path}").format(path=target))
        return target

    def import_file(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, tr("Importovat normu"), "", NORM_FILTER)
        if source:
            self.import_from(Path(source))

    def import_from(self, source: Path) -> Norm | None:
        if self._folder is None:
            return None
        try:
            norm = import_norm(source, self._folder)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(
                self,
                tr("SpeechScope"),
                tr("Soubor není platná norma: {error}").format(error=exc),
            )
            return None
        self.notice.emit(tr("Norma importována: {name}").format(name=norm.info.display_name))
        self.reload(select=norm.path)
        self.norms_changed.emit()
        return norm

    def delete_current(self, *, confirm: bool = True) -> bool:
        norm = self.current()
        if norm is None or norm.builtin or norm.path is None:
            return False
        if confirm:
            answer = QMessageBox.question(
                self,
                tr("Smazat normu"),
                tr("Přesunout normu „{name}“ do Koše?\n{path}").format(
                    name=norm.info.display_name, path=norm.path
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            trash_norm(norm)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(
                self, tr("SpeechScope"), tr("Normu se nepodařilo smazat: {error}").format(error=exc)
            )
            return False
        self.notice.emit(tr("Norma je v Koši: {name}").format(name=norm.info.display_name))
        self.reload()
        self.norms_changed.emit()
        return True

    def open_folder(self) -> None:
        if self._folder is None:
            return
        self._folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder)))


__all__ = ["NormsPage", "NormCard", "AgeHistogram", "format_age_range", "norm_overview"]
