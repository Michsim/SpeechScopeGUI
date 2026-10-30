"""Nové hodnocení z nahrávek: volba zdroje a průvodce (nahrávky po úlohách, Spustit).

`NewAssessmentChoiceDialog` se ptá, odkud nahrávky vzít: z nahrávek (tento
průvodce, spočítá se) nebo z hotových běhů (`AssessmentDialog`).
`AssessmentBuildWidget` má dva kroky podle mockupu
(docs/mockups/hodnoceni-z-nahravek): 1 pacient, věk, pohlaví, datum, jazyk
a pro každou úlohu složka s nahrávkami a protokol (přibalený, v rozšířeném
režimu jiný); složka s `manifest.csv` se sloupcem `task` vyplní i ostatní
úlohy a údaje pacienta; 2 souhrn dávek s odhadem. Výsledek je
`AssessmentPlan`, dávky spouští hlavní okno. Widget žije na dvou místech
(30. 10. 2026): v kroku 2 stránky Domů (Nové hodnocení… z karty, bez
přeskakování na Pacienty) a v tenkém `AssessmentFromRecordingsDialog`
(Nové hodnocení… na Pacientech); hlásí `accepted` (Spustit) a `rejected`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend import meta, tasks
from ..backend.assessment_build import (
    AssessmentPlan,
    Patient,
    new_plan,
    patient_from_scan,
    split_by_task,
)
from ..backend.norm_build import FolderScan, TaskPlan, with_language
from ..backend.norms import RECOMMENDED_CONTROLS  # noqa: F401  (stejná sada textů)
from ..backend.protocol import Protocol
from ..i18n import tr
from . import theme
from .new_norm_dialog import SEX_CHOICES, TaskRow, format_duration
from .widgets.status_header import Pill


class ChoiceCard(QFrame):
    """Karta s přepínačem a popisem; klik kamkoli do karty ji vybere, dvojklik potvrdí."""

    activated = Signal()  # dvojklik

    def __init__(self, title: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setProperty("role", "option")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(2)
        self.radio = QRadioButton(title)
        self.radio.setObjectName("card_title")
        self.radio.toggled.connect(self._toggled)
        layout.addWidget(self.radio)
        desc = QLabel(hint)
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        desc.setContentsMargins(24, 0, 0, 0)
        layout.addWidget(desc)

    def _toggled(self, checked: bool) -> None:
        theme.set_role(self, "chosen" if checked else "option")

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        self.radio.setChecked(True)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        self.radio.setChecked(True)
        self.activated.emit()
        super().mouseDoubleClickEvent(event)


class NewAssessmentChoiceDialog(QDialog):
    """Z nahrávek (spočítá se), nebo z hotových běhů."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Nové hodnocení"))
        self.resize(560, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(12)
        layout.addWidget(QLabel(tr("Odkud vzít nahrávky pacienta?")))
        card1 = ChoiceCard(
            tr("Z nahrávek"),
            tr("Vyberu složky s nahrávkami, spočítá se a hodnocení vznikne samo."),
        )
        card2 = ChoiceCard(tr("Z hotových běhů"), tr("Vyberu nahrávky, které už jsou spočítané."))
        self.recordings = card1.radio
        self.runs = card2.radio
        group = QButtonGroup(self)
        group.addButton(self.recordings)
        group.addButton(self.runs)
        for card in (card1, card2):
            card.activated.connect(self.accept)
            layout.addWidget(card)
        self.recordings.setChecked(True)
        layout.addStretch(1)
        footer = QHBoxLayout()
        cancel = QPushButton(tr("Storno"))
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        footer.addStretch(1)
        ok = QPushButton(tr("Pokračovat ›"))
        theme.set_role(ok, "primary")
        ok.clicked.connect(self.accept)
        footer.addWidget(ok)
        layout.addLayout(footer)

    def choice(self) -> str:
        return "recordings" if self.recordings.isChecked() else "runs"


class AssessmentBuildWidget(QWidget):
    """Dva kroky průvodce: nahrávky po úlohách, souhrn dávek a Spustit."""

    accepted = Signal()  # Spustit: `plan()` je hotový
    rejected = Signal()  # Storno v prvním kroku

    def __init__(
        self,
        protocols: list[Protocol],
        assessments_dir: Path,
        *,
        advanced: bool = False,
        languages: list[str] | None = None,
        language: str = "cs",
        rate_lookup: Callable[[str], float | None] = lambda _slug: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dir = assessments_dir
        self._rate = rate_lookup

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        steps = QHBoxLayout()
        steps.setSpacing(8)
        self.step_pills = [Pill(tr("1 Nahrávky"), "neutral"), Pill(tr("2 Spustit"), "neutral")]
        steps.addWidget(self.step_pills[0])
        arrow = QLabel("›")
        arrow.setObjectName("muted")
        steps.addWidget(arrow)
        steps.addWidget(self.step_pills[1])
        steps.addStretch(1)
        outer.addLayout(steps)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_sources(protocols, advanced, languages or [], language))
        self.stack.addWidget(self._build_summary())

        footer = QHBoxLayout()
        self.back_btn = QPushButton("")
        self.back_btn.clicked.connect(self.go_back)
        footer.addWidget(self.back_btn)
        footer.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        footer.addWidget(self.hint)
        self.next_btn = QPushButton("")
        theme.set_role(self.next_btn, "primary")
        self.next_btn.clicked.connect(self.go_next)
        footer.addWidget(self.next_btn)
        outer.addLayout(footer)
        self._show_step(0)

    # --- krok 1 -------------------------------------------------------------------

    def _build_sources(
        self, protocols: list[Protocol], advanced: bool, languages: list[str], language: str
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        person = QGridLayout()
        person.setHorizontalSpacing(10)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText(tr("ID pacienta"))
        self.subject.textChanged.connect(self._update_buttons)
        self.age = QLineEdit()
        self.age.setPlaceholderText(tr("roky"))
        self.age.setMaximumWidth(80)
        self.sex = QComboBox()
        for code, label in SEX_CHOICES:
            self.sex.addItem(tr(label), code)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("d. M. yyyy")
        self.date.setDate(QDate.currentDate())
        self.language = QComboBox()
        for code in languages or [language]:
            self.language.addItem(contract.language_label(code), code)
        self.language.setCurrentIndex(max(self.language.findData(language), 0))
        for col, (text, widget) in enumerate(
            (
                (tr("Pacient"), self.subject),
                (tr("Věk"), self.age),
                (tr("Pohlaví"), self.sex),
                (tr("Datum vyšetření"), self.date),
                (tr("Jazyk nahrávek"), self.language),
            )
        ):
            person.addWidget(QLabel(text), 0, 2 * col)
            person.addWidget(widget, 0, 2 * col + 1)
        person.setColumnStretch(1, 1)
        layout.addLayout(person)
        self.prefill_note = QLabel("")
        self.prefill_note.setObjectName("muted")
        self.prefill_note.setWordWrap(True)
        self.prefill_note.hide()
        layout.addWidget(self.prefill_note)

        grid_box = QFrame()
        grid_box.setObjectName("card")
        grid = QGridLayout(grid_box)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        for col, text in enumerate(
            [tr("úloha"), tr("složka s nahrávkami"), "", tr("protokol"), tr("nahrávek")]
        ):
            head = QLabel(text)
            head.setObjectName("muted")
            grid.addWidget(head, 0, col)
        self.rows: dict[str, TaskRow] = {}
        # úlohy knihovny a za nimi vlastní úlohy z registru (z norem a nastavení)
        customs = {t.key: t for t in tasks.registry().custom}
        for r, task in enumerate(tasks.registry().keys(), start=1):
            row = TaskRow(task, protocols, advanced, custom=customs.get(task))
            row.remove.hide()
            row.folder.editingFinished.connect(lambda row=row: self._rescan(row))
            row.browse.clicked.connect(lambda _=False, row=row: self._browse(row))
            grid.addWidget(row.name_box, r, 0)
            grid.addWidget(row.folder, r, 1)
            grid.addWidget(row.browse, r, 2)
            grid.addWidget(row.protocol, r, 3)
            grid.addWidget(row.count, r, 4)
            self.rows[task] = row
        grid.setColumnStretch(1, 1)
        layout.addWidget(grid_box)
        self.recursive = QCheckBox(tr("včetně podsložek"))
        self.recursive.toggled.connect(self._rescan_all)
        layout.addWidget(self.recursive)
        note = QLabel(
            tr(
                "Když vybraná složka má manifest.csv se sloupcem task, nahrávky se rozdělí "
                "podle něj a vyplní se i ostatní úlohy. Protokol je přibalený pro danou úlohu, "
                "jiný jde vybrat jen v rozšířeném režimu. Do složek s nahrávkami se nic "
                "nezapisuje."
            )
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _browse(self, row: TaskRow) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, tr("Složka s nahrávkami: {task}").format(task=row.label.text()), row.folder.text()
        )
        if folder:
            self.set_folder(row.task, Path(folder))

    def set_folder(self, task: str, folder: Path | None) -> None:
        row = self.rows[task]
        row.folder.setText(str(folder) if folder else "")
        self._rescan(row)

    def set_protocol(self, task: str, name: str) -> bool:
        row = self.rows[task]
        for i in range(row.protocol.count()):
            proto = row.protocol.itemData(i)
            if isinstance(proto, Protocol) and proto.name == name:
                row.protocol.setCurrentIndex(i)
                return True
        return False

    def _rescan(self, row: TaskRow) -> None:
        text = row.folder.text().strip()
        if not text:
            row.scan = FolderScan([])
            row.count.setText("–")
            self._update_buttons()
            return
        split = split_by_task(Path(text), row.task, recursive=self.recursive.isChecked())
        row.scan = split.get(row.task, FolderScan([]))
        # manifest se sloupcem task: tatáž složka vyplní i ostatní úlohy
        for task, scan in split.items():
            other = self.rows.get(task)
            if other is None or other is row or not scan.recordings:
                continue
            if not other.folder.text().strip() or other.folder.text().strip() == text:
                other.folder.setText(text)
                other.scan = scan
                self._show_count(other)
        self._show_count(row)
        self._prefill(row.scan)
        self._update_buttons()

    def _show_count(self, row: TaskRow) -> None:
        n = len(row.scan.recordings)
        row.count.setText(f"{n} ✓" if n else tr("žádné"))

    def _rescan_all(self) -> None:
        for row in self.rows.values():
            if row.folder.text().strip():
                self._rescan(row)

    def _prefill(self, scan: FolderScan) -> None:
        """Prázdné údaje pacienta z manifestu složky; vyplněné se nepřepisují."""
        patient = patient_from_scan(scan)
        filled = []
        if patient.subject and not self.subject.text().strip():
            self.subject.setText(patient.subject)
            filled.append(tr("pacient"))
        if patient.age is not None and not self.age.text().strip():
            self.age.setText(f"{patient.age:g}")
            filled.append(tr("věk"))
        if patient.sex and not self.sex.currentData():
            self.sex.setCurrentIndex(max(0, self.sex.findData(patient.sex)))
            filled.append(tr("pohlaví"))
        if patient.date and not getattr(self, "_date_set", False):
            try:
                day = dt.date.fromisoformat(patient.date)
                self.date.setDate(QDate(day.year, day.month, day.day))
                self._date_set = True
                filled.append(tr("datum"))
            except ValueError:
                pass
        if filled and scan.manifest is not None:
            self.prefill_note.setText(
                tr("Z {file}: {what}. Jde přepsat.").format(
                    file=scan.manifest.path.name, what=", ".join(filled)
                )
            )
            self.prefill_note.show()

    def active_rows(self) -> list[TaskRow]:
        return [
            r for r in self.rows.values() if r.scan.recordings and r.current_protocol() is not None
        ]

    # --- krok 2 -------------------------------------------------------------------

    def _build_summary(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.summary_title = QLabel("")
        self.summary_title.setObjectName("headline")
        layout.addWidget(self.summary_title)
        self.summary_text = QLabel("")
        self.summary_text.setWordWrap(True)
        layout.addWidget(self.summary_text)
        self.batches = QTableWidget()
        self.batches.setColumnCount(3)
        self.batches.setHorizontalHeaderLabels([tr("úloha"), tr("dávka"), tr("odhad")])
        self.batches.verticalHeader().setVisible(False)
        self.batches.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.batches.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.batches.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.batches)
        self.total = QLabel("")
        layout.addWidget(self.total)
        self.warnings = QLabel("")
        self.warnings.setObjectName("card")
        theme.set_role(self.warnings, "warn")
        self.warnings.setWordWrap(True)
        self.warnings.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(self.warnings)
        info = QLabel(
            tr(
                "Po poslední dávce se hodnocení založí samo a otevře se. Zatím bude v seznamu "
                "jako „počítá se“. Když některá dávka spadne, půjde hodnocení založit "
                "z hotových, nebo chybné dopočítat."
            )
        )
        info.setObjectName("muted")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch(1)
        return page

    def _fill_summary(self) -> None:
        rows = self.active_rows()
        p = self.patient()
        person = [p.subject]
        if p.sex:
            person.append(tr("žena") if p.sex == "f" else tr("muž"))
        if p.age is not None:
            person.append(tr("{age} let").format(age=int(round(p.age))))
        self.summary_title.setText(" · ".join(person))
        n = sum(len(r.scan.recordings) for r in rows)
        self.summary_text.setText(
            tr(
                "{k} úlohy, {n} nahrávek. Spočítá se dávek: {b}, každá na stránce Výpočet "
                "jako běžná analýza."
            ).format(k=len(rows), n=n, b=len(rows))
        )
        self.batches.setRowCount(len(rows))
        total = 0.0
        known = True
        for r, row in enumerate(rows):
            proto = row.current_protocol()
            assert proto is not None
            self.batches.setItem(r, 0, QTableWidgetItem(row.label.text()))
            self.batches.setItem(
                r,
                1,
                QTableWidgetItem(
                    tr("{n} nahrávek · {proto}").format(
                        n=len(row.scan.recordings), proto=proto.display_name
                    )
                ),
            )
            rate = self._rate(proto.slug())
            if rate and row.scan.audio_seconds:
                seconds = rate / 60 * row.scan.audio_seconds
                total += seconds
                estimate = format_duration(seconds)
            else:
                known = False
                estimate = tr("po prvním běhu")
            self.batches.setItem(r, 2, QTableWidgetItem(estimate))
        height = self.batches.horizontalHeader().height() + 4
        height += sum(self.batches.rowHeight(i) for i in range(len(rows)))
        self.batches.setFixedHeight(height)
        if total:
            text = tr("Celkem odhad asi {t}").format(t=format_duration(total))
            self.total.setText(
                text if known else text + tr(" (bez úloh, které se ještě nepočítaly)")
            )
        else:
            self.total.setText(tr("Odhad času bude po prvním běhu protokolu."))
        skipped = [row.label.text() for row in self.rows.values() if not row.scan.recordings]
        self.warnings.setText(
            tr(
                "{tasks}: bez nahrávek, vynechá se. Dimenze, které tyto úlohy potřebují, "
                "v pavouku nebudou."
            ).format(tasks=", ".join(skipped))
            if skipped
            else ""
        )
        self.warnings.setVisible(bool(skipped))

    # --- kroky ---------------------------------------------------------------------

    def step(self) -> int:
        return self.stack.currentIndex()

    def _show_step(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, pill in enumerate(self.step_pills):
            theme.set_role(pill, "accent" if i == index else ("ok" if i < index else "neutral"))
        self.back_btn.setText(tr("Storno") if index == 0 else tr("‹ Zpět"))
        self.next_btn.setText(tr("Spustit") if index == 1 else tr("Další ›"))
        if index == 1:
            self._fill_summary()
        self._update_buttons()

    def _update_buttons(self) -> None:
        hint, ok = "", True
        if self.step() == 0:
            rows = self.active_rows()
            if not self.subject.text().strip():
                ok, hint = False, tr("Vyplňte pacienta.")
            elif not rows:
                ok, hint = False, tr("Vyberte složku aspoň pro jednu úlohu.")
            else:
                n = sum(len(r.scan.recordings) for r in rows)
                hint = tr("úloh: {k} · nahrávek: {n}").format(k=len(rows), n=n)
        self.next_btn.setEnabled(ok)
        self.hint.setText(hint)

    def go_next(self) -> None:
        if not self.next_btn.isEnabled():
            return
        if self.step() == 0:
            self._show_step(1)
        else:
            self.accepted.emit()

    def go_back(self) -> None:
        if self.step() == 0:
            self.rejected.emit()
        else:
            self._show_step(0)

    # --- výsledek ----------------------------------------------------------------

    def patient(self) -> Patient:
        day = self.date.date()
        return Patient(
            subject=self.subject.text().strip(),
            age=meta.parse_age(self.age.text()),
            sex=str(self.sex.currentData() or ""),
            date=dt.date(day.year(), day.month(), day.day()).isoformat(),
        )

    def plan(self) -> AssessmentPlan:
        language = str(self.language.currentData() or "")
        tasks = []
        for row in self.active_rows():
            proto = row.current_protocol()
            assert proto is not None
            tasks.append(
                TaskPlan(
                    task=row.task,
                    protocol=with_language(proto, language).to_dict(),
                    inputs=list(row.scan.recordings),
                )
            )
        return new_plan(self._dir, self.patient(), language, tasks)


class AssessmentFromRecordingsDialog(QDialog):
    """Průvodce v okně (Nové hodnocení… na Pacientech); vše ostatní dělá widget."""

    def __init__(
        self,
        protocols: list[Protocol],
        assessments_dir: Path,
        *,
        advanced: bool = False,
        languages: list[str] | None = None,
        language: str = "cs",
        rate_lookup: Callable[[str], float | None] = lambda _slug: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Nové hodnocení z nahrávek"))
        self.resize(980, 700)
        self.widget = AssessmentBuildWidget(
            protocols,
            assessments_dir,
            advanced=advanced,
            languages=languages,
            language=language,
            rate_lookup=rate_lookup,
            parent=self,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.addWidget(self.widget)
        self.widget.accepted.connect(self.accept)
        self.widget.rejected.connect(self.reject)

    def __getattr__(self, name: str):  # noqa: ANN204
        """Pole a metody průvodce (`plan`, `rows`, `set_folder`…) jako dřív přímo na dialogu."""
        if name == "widget":
            raise AttributeError(name)
        return getattr(self.widget, name)


__all__ = ["NewAssessmentChoiceDialog", "AssessmentBuildWidget", "AssessmentFromRecordingsDialog"]
