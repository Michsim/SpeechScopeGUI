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

Víc pacientů nebo dat najednou (30. 10. 2026, mockup
docs/mockups/domu-pacienti, artboard 4): jediný zdroj je `manifest.csv`
(sloupce pacient, datum, úloha, věk, pohlaví). Když manifest dá víc než
jednoho pacienta nebo víc dat, pole pacienta nahoře zmizí a místo nich je
krok 2 Pacienti (tabulka vyšetření, prázdné datum a věk jdou dopsat jen pro
tento výpočet), dávka je na úlohu a datum (`TaskPlan.visit`) a po skončení
vznikne vyšetření na každou dvojici pacient a datum. Ze struktury složek se
nic neodvozuje (rozhodnuto 30. 10.); kdo manifest nemá, dostane
„Uložit šablonu manifestu…“ se seznamem nahrávek k vyplnění v Excelu.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QColor
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
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
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
    PersonRow,
    is_multi,
    new_plan,
    patient_from_scan,
    patients_text,
    people_from_scans,
    split_by_task,
    split_by_visit,
    write_manifest_template,
)
from ..backend.audio import format_duration as audio_duration
from ..backend.norm_build import FolderScan, TaskPlan, with_language
from ..backend.norms import RECOMMENDED_CONTROLS  # noqa: F401  (stejná sada textů)
from ..backend.protocol import Protocol
from ..i18n import tr
from . import theme
from .new_norm_dialog import SEX_CHOICES, TaskRow, _scrolled, format_duration
from .widgets.clean_table import card, set_text, style_table
from .widgets.status_header import Pill
from .widgets.task_rows import SummaryBar


class ChoiceCard(QFrame):
    """Karta s přepínačem a popisem; klik kamkoli do karty ji vybere, dvojklik potvrdí.

    Bez kolečka přepínače, výběr ukazuje jen modré zvýraznění karty (`indicator=True` ho vrátí)."""

    activated = Signal()  # dvojklik

    def __init__(
        self, title: str, hint: str, parent: QWidget | None = None, *, indicator: bool = False
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setProperty("role", "option")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(2)
        self.radio = QRadioButton(title)
        self.radio.setObjectName("card_title")
        if not indicator:
            self.radio.setStyleSheet(
                "QRadioButton::indicator { width: 0; height: 0; } QRadioButton { spacing: 0; }"
            )
        self.radio.toggled.connect(self._toggled)
        layout.addWidget(self.radio)
        desc = QLabel(hint)
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        desc.setContentsMargins(24 if indicator else 0, 0, 0, 0)
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
        for choice in (card1, card2):
            choice.activated.connect(self.accept)
            layout.addWidget(choice)
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


def _visit_text(visit: str) -> str:
    """ISO datum jako „14. 10. 2026“; jiný text beze změny."""
    try:
        day = dt.date.fromisoformat(visit)
    except ValueError:
        return visit
    return f"{day.day}. {day.month}. {day.year}"


def _sex_text(sex: str) -> str:
    return {"f": tr("žena"), "m": tr("muž")}.get(sex, sex)


class AssessmentBuildWidget(QWidget):
    """Průvodce ve dvou krocích: 1 Nahrávky (složky, protokoly), 2 Pacienti (kdo a kdy).

    Mockup https://claude.ai/artifact/LqdUbs3QgkT4YPuHdsKN6z (31. 10. 2026): krok 2 je vždy,
    u víc pacientů tabulka po vyšetřeních (pacient × datum) s úlohami ve sloupcích, věk
    a pohlaví se píšou jednou za vyšetření, nepřiřazené nahrávky se přiřadí přímo tam.
    Úpravy platí jen pro tento výpočet (`_overrides`), do složky se nic nezapisuje."""

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
        self._people: dict[Path, PersonRow] = {}  # nahrávka → pacient, datum… z manifestu
        self._overrides: dict[Path, dict[str, str]] = {}  # ruční opravy v kroku 2
        self._multi = False
        self._visit_filter = "all"  # all | missing | incomplete
        self._frozen_visits: set[tuple[str, str]] | None = None  # seznam k doplnění
        self._visit_rows: list[tuple[str, str, list[Path]]] = []
        self._filling = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        steps = QHBoxLayout()
        steps.setSpacing(8)
        self.step_pills = [Pill(tr("1 Nahrávky"), "neutral"), Pill(tr("2 Pacienti"), "neutral")]
        steps.addWidget(self.step_pills[0])
        arrow = QLabel("›")
        arrow.setObjectName("muted")
        steps.addWidget(arrow)
        steps.addWidget(self.step_pills[1])
        steps.addStretch(1)
        outer.addLayout(steps)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        # Kroky v posuvné ploše (31. 10. 2026): obsah kroku nesmí zvětšovat minimální výšku
        # okna (260 nahrávek bez data dalo přes 10 000 px a Windows hlásily setGeometry).
        self.stack.addWidget(
            _scrolled(self._build_sources(protocols, advanced, languages or [], language))
        )
        self.stack.addWidget(_scrolled(self._build_patients()))

        # Patička: Storno, souhrn (úlohy, nahrávky, zvuk, odhad), stav a Spustit hodnocení.
        foot = QFrame()
        foot.setObjectName("card")
        footer = QHBoxLayout(foot)
        footer.setContentsMargins(10, 8, 10, 8)
        footer.setSpacing(12)
        self.back_btn = QPushButton(tr("Storno"))
        self.back_btn.clicked.connect(self.go_back)
        footer.addWidget(self.back_btn)
        self.summary = SummaryBar()
        self.summary.setStyleSheet("QFrame#summarybar { background: transparent; border: none; }")
        footer.addWidget(self.summary, 1)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        footer.addWidget(self.hint)
        self.next_btn = QPushButton(tr("Spustit hodnocení"))
        theme.set_role(self.next_btn, "primary")
        self.next_btn.clicked.connect(self.go_next)
        footer.addWidget(self.next_btn)
        outer.addWidget(foot)
        self._show_step(0)

    # --- krok 1 -------------------------------------------------------------------

    def _build_sources(
        self, protocols: list[Protocol], advanced: bool, languages: list[str], language: str
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.language = QComboBox()
        for code in languages or [language]:
            self.language.addItem(contract.language_label(code), code)
        self.language.setCurrentIndex(max(self.language.findData(language), 0))

        tasks_head = QHBoxLayout()
        tasks_head.setSpacing(10)
        tasks_title = QLabel(tr("Úlohy"))
        tasks_title.setObjectName("card_title")
        tasks_head.addWidget(tasks_title)
        tasks_hint = QLabel(tr("složka s nahrávkami pro každou úlohu"))
        tasks_hint.setObjectName("muted")
        tasks_head.addWidget(tasks_hint)
        info = QLabel("ⓘ")
        info.setObjectName("muted")
        info.setToolTip(
            tr(
                "Když vybraná složka má manifest.csv se sloupcem task, nahrávky se rozdělí "
                "podle něj a vyplní se i ostatní úlohy; s pacientem a datem v manifestu jde "
                "spočítat víc pacientů nebo vyšetření najednou. Protokol je přibalený pro "
                "danou úlohu, jiný jde vybrat jen v rozšířeném režimu. Do složek "
                "s nahrávkami se nic nezapisuje. Po výpočtu se hodnocení založí samo."
            )
        )
        tasks_head.addWidget(info)
        tasks_head.addStretch(1)
        language_label = QLabel(tr("Jazyk"))
        language_label.setObjectName("muted")
        tasks_head.addWidget(language_label)
        tasks_head.addWidget(self.language)
        layout.addLayout(tasks_head)
        # řádky úloh jako na Analýze (widgets/task_rows.py), pod sebou
        rows_box = QWidget()
        rows_layout = QVBoxLayout(rows_box)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(6)
        self.rows: dict[str, TaskRow] = {}
        # úlohy knihovny a za nimi vlastní úlohy z registru (z norem a nastavení)
        customs = {t.key: t for t in tasks.registry().custom}
        for task in tasks.registry().keys():
            row = TaskRow(task, protocols, advanced, custom=customs.get(task))
            row.remove.hide()
            row.folder.editingFinished.connect(lambda row=row: self._rescan(row))
            row.browse.clicked.connect(lambda _=False, row=row: self._browse(row))
            rows_layout.addWidget(row.widget)
            self.rows[task] = row
        layout.addWidget(rows_box)
        options = QHBoxLayout()
        self.recursive = QCheckBox(tr("i podsložky"))
        self.recursive.toggled.connect(self._rescan_all)
        options.addWidget(self.recursive)
        options.addStretch(1)
        self.template_btn = QPushButton(tr("Uložit šablonu manifestu…"))
        self.template_btn.setFlat(True)
        self.template_btn.setToolTip(
            tr(
                "CSV se seznamem nalezených nahrávek a prázdnými sloupci pacient, datum, "
                "věk, pohlaví k vyplnění v Excelu; pro víc pacientů nebo dat najednou"
            )
        )
        self.template_btn.setEnabled(False)
        self.template_btn.clicked.connect(lambda: self.save_template())
        options.addWidget(self.template_btn)
        layout.addLayout(options)
        layout.addStretch(1)
        return page

    def _build_patients(self) -> QWidget:
        """Krok 2: jeden pacient formulářem, víc pacientů tabulkou vyšetření."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # jeden pacient
        self.person_box = QFrame()
        self.person_box.setObjectName("card")
        person_layout = QVBoxLayout(self.person_box)
        person_layout.setContentsMargins(14, 12, 14, 12)
        person_layout.setSpacing(8)
        person = QGridLayout()
        person.setHorizontalSpacing(10)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText(tr("ID pacienta"))
        self.subject.textChanged.connect(self._update_buttons)
        self.age = QLineEdit()
        self.age.setPlaceholderText(tr("roky"))
        self.age.setMaximumWidth(80)
        self.age.textChanged.connect(self._update_person_marks)
        self.sex = QComboBox()
        for code, label in SEX_CHOICES:
            self.sex.addItem(tr(label), code)
        self.sex.currentIndexChanged.connect(lambda _i: self._update_person_marks())
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("d. M. yyyy")
        self.date.setDate(QDate.currentDate())
        for col, (text, widget) in enumerate(
            (
                (tr("Pacient"), self.subject),
                (tr("Věk"), self.age),
                (tr("Pohlaví"), self.sex),
                (tr("Datum vyšetření"), self.date),
            )
        ):
            person.addWidget(QLabel(text), 0, 2 * col)
            person.addWidget(widget, 0, 2 * col + 1)
        person.setColumnStretch(1, 1)
        person_layout.addLayout(person)
        self.prefill_note = QLabel("")
        self.prefill_note.setObjectName("muted")
        self.prefill_note.setWordWrap(True)
        self.prefill_note.hide()
        person_layout.addWidget(self.prefill_note)
        layout.addWidget(self.person_box)
        self.task_checks = QLabel("")
        self.task_checks.setTextFormat(Qt.TextFormat.RichText)
        self.task_checks.setWordWrap(True)
        layout.addWidget(self.task_checks)

        # víc pacientů: vyšetření (pacient × datum), úlohy ve sloupcích
        self.people_box = QWidget()
        people_layout = QVBoxLayout(self.people_box)
        people_layout.setContentsMargins(0, 0, 0, 0)
        people_layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel(tr("Vyšetření"))
        title.setObjectName("card_title")
        head.addWidget(title)
        self.people_summary = QLabel("")
        self.people_summary.setObjectName("muted")
        self.people_summary.setToolTip(
            tr(
                "Pacient, datum vyšetření, úloha, věk a pohlaví jsou z manifest.csv ve složce. "
                "Co chybí, dopište tady (platí jen pro tento výpočet). Nahrávka bez pacienta "
                "nebo data se spočítá, ale do žádného vyšetření se nezaloží."
            )
        )
        head.addWidget(self.people_summary)
        head.addStretch(1)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self.filter_buttons: dict[str, QPushButton] = {}
        for key in ("all", "missing", "incomplete"):
            btn = QPushButton("")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, k=key: self.set_visit_filter(k))
            self.filter_group.addButton(btn)
            head.addWidget(btn)
            self.filter_buttons[key] = btn
        self.filter_buttons["all"].setChecked(True)
        self.filter_buttons["missing"].setToolTip(
            tr("Vyšetření bez věku nebo pohlaví; po doplnění v seznamu zůstanou s fajfkou.")
        )
        self.filter_buttons["incomplete"].setToolTip(
            tr("Vyšetření, kterým chybí některá úloha; dimenze, které ji potřebují, nebudou.")
        )
        people_layout.addLayout(head)
        self.visits_table = QTableWidget()
        self.visits_table.itemChanged.connect(self._visit_edited)
        people_layout.addWidget(card(self.visits_table), 1)
        self.totals = QLabel("")
        self.totals.setObjectName("muted")
        people_layout.addWidget(self.totals)
        # nepřiřazené nahrávky: pacient z nabídky (nebo nový) a datum, po přiřazení se
        # nahrávka přesune do řádku vyšetření
        self.unassigned_box = QFrame()
        self.unassigned_box.setObjectName("unassigned")
        self.unassigned_box.setStyleSheet(
            "QFrame#unassigned { background: #fffbeb; border: 1px solid #fcd34d;"
            " border-radius: 10px; }"
            "QFrame#unassigned QLabel { background: transparent; border: none; }"
        )
        box_layout = QVBoxLayout(self.unassigned_box)
        box_layout.setContentsMargins(12, 10, 12, 10)
        box_layout.setSpacing(8)
        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        self.unassigned_head = QLabel("")
        self.unassigned_head.setStyleSheet("color: #92400e; font-weight: 600;")
        head_row.addWidget(self.unassigned_head)
        head_row.addStretch(1)
        # u stovek nahrávek bez data se po jedné vyplňovat nedá: datum pro všechny naráz
        self.bulk_date = QLineEdit()
        self.bulk_date.setPlaceholderText(tr("datum pro všechny bez data"))
        self.bulk_date.setMaximumWidth(220)
        self.bulk_date.returnPressed.connect(lambda: self.apply_bulk_date())
        head_row.addWidget(self.bulk_date)
        self.bulk_btn = QPushButton(tr("Použít"))
        self.bulk_btn.clicked.connect(lambda: self.apply_bulk_date())
        head_row.addWidget(self.bulk_btn)
        box_layout.addLayout(head_row)
        self.unassigned_scroll = QScrollArea()
        self.unassigned_scroll.setWidgetResizable(True)
        self.unassigned_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.unassigned_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        inner = QWidget()
        inner.setObjectName("unassigned_inner")
        inner.setStyleSheet("#unassigned_inner { background: transparent; }")
        self.unassigned_layout = QGridLayout(inner)
        self.unassigned_layout.setContentsMargins(0, 0, 0, 0)
        self.unassigned_layout.setHorizontalSpacing(10)
        self.unassigned_layout.setVerticalSpacing(6)
        self.unassigned_scroll.setWidget(inner)
        box_layout.addWidget(self.unassigned_scroll)
        people_layout.addWidget(self.unassigned_box)
        layout.addWidget(self.people_box, 1)
        layout.addStretch(0)
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
            row.refresh_look()
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
        self._refresh_people()
        if not self._multi:
            self._prefill(row.scan)
        self._update_buttons()

    def _show_count(self, row: TaskRow) -> None:
        n = len(row.scan.recordings)
        row.count.setText(f"{n} ✓" if n else tr("žádné"))
        row.refresh_look()

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

    def prefill(self, subject: str, age: float | None, sex: str) -> None:
        """Další vyšetření téhož pacienta: pacient, věk a pohlaví; datum a složky prázdné."""
        self.subject.setText(subject)
        self.age.setText("" if age is None else f"{age:g}")
        self.sex.setCurrentIndex(max(0, self.sex.findData(sex)))
        self._update_buttons()

    def active_rows(self) -> list[TaskRow]:
        return [
            r for r in self.rows.values() if r.scan.recordings and r.current_protocol() is not None
        ]

    # --- víc pacientů a dat --------------------------------------------------------

    def _recording_tasks(self) -> dict[Path, str]:
        """Nahrávka → úloha podle řádku, ve kterém se našla."""
        out: dict[Path, str] = {}
        for row in self.rows.values():
            for rec in row.scan.recordings:
                try:
                    key = rec.resolve()  # stejný klíč jako `people_from_scans`
                except OSError:
                    key = rec
                out.setdefault(key, row.task)
        return out

    def _refresh_people(self) -> None:
        scans = {task: row.scan for task, row in self.rows.items() if row.scan.recordings}
        self._people = people_from_scans(scans)
        self._overrides = {k: v for k, v in self._overrides.items() if k in self._people}
        self._multi = is_multi(self._merged_people())
        self.person_box.setVisible(not self._multi)
        self.task_checks.setVisible(not self._multi)
        self.people_box.setVisible(self._multi)
        self.template_btn.setEnabled(bool(scans))
        if self._multi:
            self._fill_patients()

    def _merged_people(self) -> dict[Path, PersonRow]:
        """Údaje z manifestu přepsané ručními opravami z kroku 2."""
        out: dict[Path, PersonRow] = {}
        for path, person in self._people.items():
            row = self._overrides.get(path, {})
            out[path] = PersonRow(
                subject=row.get(meta.SUBJECT, person.subject),
                visit=row.get(meta.VISIT, person.visit),
                age=meta.parse_age(row[meta.AGE]) if meta.AGE in row else person.age,
                sex=meta.parse_sex(row[meta.SEX]) if meta.SEX in row else person.sex,
            )
        return out

    def people(self) -> dict[Path, PersonRow]:
        """Údaje nahrávek z manifestu a ručních oprav (jen u víc pacientů nebo dat)."""
        return self._merged_people() if self._multi else {}

    def is_multi(self) -> bool:
        return self._multi

    def people_counts(self) -> tuple[int, int, int, int]:
        """(pacientů, vyšetření, nahrávek, bez pacienta nebo data)."""
        people = self._merged_people()
        complete = {(p.subject, p.visit) for p in people.values() if p.subject and p.visit}
        missing = sum(1 for p in people.values() if not (p.subject and p.visit))
        return len({s for s, _v in complete}), len(complete), len(people), missing

    def set_person(self, path: Path, **values: object) -> None:
        """Ruční oprava údajů nahrávky (pacient, visit, věk, pohlaví) jen pro tento výpočet."""
        row = self._overrides.setdefault(path, {})
        for name, value in values.items():
            text = "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)
            if name == meta.VISIT:
                text = meta.normalize_visit(text) or text
            row[name] = text.strip()
        self._after_people_edit()

    def _after_people_edit(self) -> None:
        self._multi = is_multi(self._merged_people()) or self._multi
        if self._multi:
            self._fill_patients()
        self._update_buttons()

    # --- krok 2: vyšetření ---------------------------------------------------------

    def _visits(self) -> dict[tuple[str, str], list[Path]]:
        """(pacient, datum) → nahrávky; jen s pacientem i datem, v pořadí pacienta a data."""
        people = self._merged_people()
        out: dict[tuple[str, str], list[Path]] = {}
        for path, person in sorted(people.items(), key=lambda kv: (kv[1].subject, kv[1].visit)):
            if person.subject and person.visit:
                out.setdefault((person.subject, person.visit), []).append(path)
        return out

    def _active_tasks(self) -> list[str]:
        return [row.task for row in self.active_rows()]

    def _visit_info(self, paths: list[Path]) -> tuple[float | None, str]:
        people = self._merged_people()
        ages = [people[p].age for p in paths if people[p].age is not None]
        sexes = [people[p].sex for p in paths if people[p].sex]
        return (ages[0] if ages else None), (sexes[0] if sexes else "")

    def _visit_missing(self, paths: list[Path]) -> bool:
        age, sex = self._visit_info(paths)
        return age is None or not sex

    def _visit_incomplete(self, paths: list[Path]) -> bool:
        rec_tasks = self._recording_tasks()
        have = {rec_tasks.get(p, "") for p in paths}
        return any(t not in have for t in self._active_tasks())

    def set_visit_filter(self, key: str) -> None:
        """Všechna · chybí údaje (zamrazený seznam k doplnění) · neúplná sada úloh."""
        self._visit_filter = key
        visits = self._visits()
        self._frozen_visits = (
            {k for k, paths in visits.items() if self._visit_missing(paths)}
            if key == "missing"
            else None
        )
        self.filter_buttons[key].setChecked(True)
        self._fill_patients()

    def _fill_patients(self) -> None:
        """Tabulka vyšetření, souhrn, filtr, celkem po úlohách a nepřiřazené nahrávky."""
        visits = self._visits()
        task_keys = self._active_tasks()
        rec_tasks = self._recording_tasks()
        missing = [k for k, paths in visits.items() if self._visit_missing(paths)]
        incomplete = [k for k, paths in visits.items() if self._visit_incomplete(paths)]
        self.filter_buttons["all"].setText(tr("všechna {n}").format(n=len(visits)))
        self.filter_buttons["missing"].setText(tr("chybí údaje {n}").format(n=len(missing)))
        self.filter_buttons["incomplete"].setText(
            tr("neúplná sada úloh {n}").format(n=len(incomplete))
        )
        if self._visit_filter == "missing" and self._frozen_visits is not None:
            shown = [k for k in visits if k in self._frozen_visits]
        elif self._visit_filter == "incomplete":
            shown = incomplete
        else:
            shown = list(visits)
        headers = [tr("pacient"), tr("datum vyšetření"), tr("věk"), tr("pohlaví")]
        headers += [tasks.label(t) for t in task_keys]
        table = self.visits_table
        self._filling = True
        table.blockSignals(True)
        style_table(table, headers)
        table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        table.setRowCount(len(shown))
        self._visit_rows = []
        for r, key in enumerate(shown):
            subject, visit = key
            paths = visits[key]
            self._visit_rows.append((subject, visit, paths))
            age, sex = self._visit_info(paths)
            done = self._frozen_visits is not None and not self._visit_missing(paths)
            set_text(table, r, 0, subject + ("  ✓" if done else ""), bold=True)
            set_text(table, r, 1, _visit_text(visit))
            for c, text in ((2, "" if age is None else f"{age:g}"), (3, _sex_text(sex))):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                if not text:
                    item.setBackground(QColor(theme.WARN_SOFT))
                    item.setToolTip(tr("chybí, dopište (platí pro celé vyšetření)"))
                table.setItem(r, c, item)
            for c, task in enumerate(task_keys, start=4):
                names = [p.name for p in paths if rec_tasks.get(p) == task]
                item = QTableWidgetItem(str(len(names)) if names else "–")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if names:
                    item.setForeground(QColor("#1d4ed8"))
                    item.setToolTip("\n".join(names))
                else:
                    item.setForeground(QColor("#b45309"))
                    item.setToolTip(
                        tr("Úloha chybí; dimenze, které ji potřebují, v pavouku nebudou.")
                    )
                table.setItem(r, c, item)
        table.resizeColumnsToContents()
        # sloupce podle obsahu, volné místo až za posledním (roztažený poslední sloupec
        # odsouval čísla úlohy daleko od nadpisu)
        table.horizontalHeader().setStretchLastSection(False)
        table.blockSignals(False)
        self._filling = False
        patients, n_visits, n, unassigned = self.people_counts()
        self.people_summary.setText(
            tr("{p} · {v} vyšetření").format(p=patients_text(patients), v=n_visits)
        )
        counts = {t: 0 for t in task_keys}
        for paths in visits.values():
            for path in paths:
                if rec_tasks.get(path) in counts:
                    counts[rec_tasks[path]] += 1
        self.totals.setText(
            tr("celkem: {parts}").format(
                parts=" · ".join(f"{tasks.label(t)} {k}" for t, k in counts.items())
            )
        )
        self._fill_unassigned()

    def _fill_unassigned(self) -> None:
        """Žlutý oddíl s nahrávkami bez pacienta nebo data, seskupený podle úlohy."""
        grid = self.unassigned_layout
        while grid.count():
            item = grid.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()  # Qt maže až z fronty událostí; jinak zůstane vidět
                item.widget().deleteLater()
        people = self._merged_people()
        rec_tasks = self._recording_tasks()
        loose = sorted(
            (p for p, person in people.items() if not (person.subject and person.visit)),
            key=lambda p: (tasks.task_order(rec_tasks.get(p, "")), p.name),
        )
        self.unassigned_box.setVisible(bool(loose))
        if not loose:
            return
        self.unassigned_head.setText(
            tr(
                "⚠ {n} nahrávek není přiřazených k vyšetření · bez pacienta nebo data se jen "
                "spočítají"
            ).format(n=len(loose))
        )
        no_date = sum(1 for p in loose if not people[p].visit)
        self.bulk_date.setVisible(no_date > 1)
        self.bulk_btn.setVisible(no_date > 1)
        subjects = sorted({p.subject for p in people.values() if p.subject})
        row_height = 0
        for r, path in enumerate(loose):
            person = people[path]
            tag = QLabel(tasks.label(rec_tasks.get(path, "")))
            tag.setStyleSheet(
                f"background: {theme.ACCENT_SOFT}; color: #1d4ed8; border-radius: 8px;"
                " padding: 1px 8px; font-weight: 600;"
            )
            grid.addWidget(tag, r, 0)
            name = QLabel(path.name)
            name.setToolTip(str(path))
            grid.addWidget(name, r, 1)
            who = QComboBox()
            who.setEditable(True)
            who.setMinimumWidth(170)
            who.addItems(subjects)
            who.setCurrentText(person.subject)
            who.lineEdit().setPlaceholderText(tr("pacient"))
            who.lineEdit().editingFinished.connect(
                lambda p=path, w=who: self._assign(p, meta.SUBJECT, w.currentText())
            )
            who.activated.connect(
                lambda _i, p=path, w=who: self._assign(p, meta.SUBJECT, w.currentText())
            )
            grid.addWidget(who, r, 2)
            when = QLineEdit(_visit_text(person.visit))
            when.setPlaceholderText(tr("datum, třeba 14. 10. 2026"))
            when.editingFinished.connect(
                lambda p=path, w=when: self._assign(p, meta.VISIT, w.text())
            )
            grid.addWidget(when, r, 3)
            row_height = max(row_height, who.sizeHint().height())
        grid.setColumnStretch(4, 1)
        # nejvýš asi pět řádků, zbytek posuvníkem (seznam nesmí roztahovat stránku)
        shown = min(len(loose), 5)
        self.unassigned_scroll.setFixedHeight(shown * (row_height + 6))

    def apply_bulk_date(self, text: str | None = None) -> int:
        """Datum pro všechny nepřiřazené nahrávky bez data; vrátí jejich počet."""
        raw = (self.bulk_date.text() if text is None else text).strip()
        visit = meta.normalize_visit(raw)
        if not visit:
            self.bulk_date.setStyleSheet(
                f"background: {theme.WARN_SOFT}; border-color: #f59e0b;" if raw else ""
            )
            return 0
        self.bulk_date.setStyleSheet("")
        people = self._merged_people()
        changed = 0
        for path, person in people.items():
            if not person.visit:
                self._overrides.setdefault(path, {})[meta.VISIT] = visit
                changed += 1
        self.bulk_date.clear()
        if changed:
            QTimer.singleShot(0, self._after_people_edit)
        return changed

    def _assign(self, path: Path, column: str, value: str) -> None:
        """Přiřazení nepřiřazené nahrávky; přestavba až v dalším kroku smyčky (signál
        přichází z widgetu, který se při přestavbě maže)."""
        current = self._merged_people().get(path)
        old = getattr(current, "subject" if column == meta.SUBJECT else "visit", "")
        new = value.strip()
        if column == meta.VISIT:
            new = meta.normalize_visit(new) or new
        if new == old:
            return
        self._overrides.setdefault(path, {})[column] = new
        QTimer.singleShot(0, self._after_people_edit)

    def _visit_edited(self, item: QTableWidgetItem) -> None:
        """Věk nebo pohlaví v řádku vyšetření platí pro všechny jeho nahrávky."""
        if self._filling or item.column() not in (2, 3):
            return
        r = item.row()
        if not (0 <= r < len(self._visit_rows)):
            return
        _subject, _visit, paths = self._visit_rows[r]
        column = meta.AGE if item.column() == 2 else meta.SEX
        text = item.text().strip()
        value = meta.normalize_value(column, text) if text else ""
        for path in paths:
            self._overrides.setdefault(path, {})[column] = value
        QTimer.singleShot(0, self._after_people_edit)

    def set_visit_value(self, subject: str, visit: str, column: str, value: str) -> None:
        """Věk nebo pohlaví jednoho vyšetření (jako úprava v tabulce); pro testy a skripty."""
        paths = self._visits().get((subject, visit), [])
        for path in paths:
            self._overrides.setdefault(path, {})[column] = meta.normalize_value(column, value)
        self._after_people_edit()

    def _update_person_marks(self) -> None:
        """Jeden pacient: prázdný věk a pohlaví žlutě (Spustit jde i bez nich)."""
        for widget, empty in (
            (self.age, not self.age.text().strip()),
            (self.sex, not self.sex.currentData()),
        ):
            widget.setStyleSheet(
                f"background: {theme.WARN_SOFT}; border-color: #f59e0b;" if empty else ""
            )

    def _fill_task_checks(self) -> None:
        """Jeden pacient: úlohy vyšetření jedním řádkem, chybějící oranžově."""
        parts = []
        for task, row in self.rows.items():
            n = len(row.scan.recordings)
            if n:
                parts.append(f"{tasks.label(task)} <b style='color:#1d4ed8'>{n}</b>")
            elif not tasks.is_custom(task):
                parts.append(f"<span style='color:#b45309'>{tasks.label(task)} –</span>")
        self.task_checks.setText(
            f"<b>{tr('Úlohy vyšetření')}</b>&nbsp;&nbsp;" + " &nbsp;·&nbsp; ".join(parts)
        )
        self.task_checks.setToolTip(
            tr("Chybějící úloha: dimenze, které ji potřebují, v pavouku nebudou.")
        )

    def save_template(self, target: Path | None = None) -> Path | None:
        """Šablona manifestu se seznamem nalezených nahrávek, kam uživatel ukáže."""
        rec_tasks = self._recording_tasks()
        if not rec_tasks:
            return None
        folders = [
            Path(r.folder.text().strip()) for r in self.rows.values() if r.folder.text().strip()
        ]
        base = folders[0] if folders else next(iter(rec_tasks)).parent
        if target is None:
            text, _filter = QFileDialog.getSaveFileName(
                self, tr("Uložit šablonu manifestu"), str(base / "manifest.csv"), "CSV (*.csv)"
            )
            if not text:
                return None
            target = Path(text)
        return write_manifest_template(target, rec_tasks, base)

    def _task_plans(self) -> list[TaskPlan]:
        language = str(self.language.currentData() or "")
        plans: list[TaskPlan] = []
        for row in self.active_rows():
            proto = row.current_protocol()
            assert proto is not None
            protocol = with_language(proto, language).to_dict()
            if self._multi:
                people = self._merged_people()
                for visit, recs in split_by_visit(list(row.scan.recordings), people).items():
                    plans.append(TaskPlan(row.task, protocol, recs, visit=visit))
            else:
                plans.append(TaskPlan(row.task, protocol, list(row.scan.recordings)))
        return plans

    # --- krok 2 -------------------------------------------------------------------

    # --- souhrn a Spustit ---------------------------------------------------------

    def step(self) -> int:
        return self.stack.currentIndex()

    def _show_step(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, pill in enumerate(self.step_pills):
            theme.set_role(pill, "accent" if i == index else ("ok" if i < index else "neutral"))
        self.back_btn.setText(tr("Storno") if index == 0 else tr("‹ Zpět"))
        if index == 1:
            if self._multi:
                self._fill_patients()
            else:
                self._fill_task_checks()
                self._update_person_marks()
        self._update_buttons()

    def estimate(self) -> tuple[float, bool]:
        """Odhad všech dávek v sekundách a zda je známý pro všechny (podle minut zvuku)."""
        rows = self.active_rows()
        durations = {rec: sec for row in rows for rec, sec in row.scan.durations.items()}
        total, known = 0.0, True
        for plan in self._task_plans():
            rate = self._rate(plan.proto().slug())
            audio = sum(durations.get(rec) or 0.0 for rec in plan.inputs)
            if rate and audio:
                total += rate / 60 * audio
            else:
                known = False
        return total, known

    def _update_summary(self) -> None:
        rows = self.active_rows()
        n = sum(len(r.scan.recordings) for r in rows)
        audio = sum(r.scan.audio_seconds for r in rows)
        total, known = self.estimate()
        if self._multi:
            patients, visits, _n, _missing = self.people_counts()
            first = (patients_text(patients), tr("{v} vyšetření").format(v=visits))
            second = (
                tr("{b} dávek").format(b=len(self._task_plans())),
                tr("{n} nahrávek").format(n=n),
            )
        else:
            first = (tr("{k} úlohy").format(k=len(rows)), tr("{n} nahrávek").format(n=n))
            second = (audio_duration(audio) if audio else "–", tr("zvuku"))
        figures = [first, second]
        if total:
            figures.append(
                ("~" + format_duration(total), tr("odhad") if known else tr("odhad (část)"))
            )
        self.summary.set_figures(figures if rows else [])
        self.summary.setVisible(bool(rows))

    def _update_buttons(self) -> None:
        if not hasattr(self, "next_btn"):  # při stavbě stránky ještě patička není
            return
        hint, ok = "", True
        rows = self.active_rows()
        if self.step() == 0:
            # Krok 1: jen složky. Patička napoví, co čeká v kroku 2.
            self.next_btn.setText(tr("Další › Pacienti"))
            if not rows:
                ok, hint = False, tr("Vyberte složku aspoň pro jednu úlohu.")
            elif self._multi:
                patients, _v, _n, unassigned = self.people_counts()
                missing = sum(1 for paths in self._visits().values() if self._visit_missing(paths))
                hint = tr("manifest: {p}").format(p=patients_text(patients))
                if missing or unassigned:
                    hint += " · " + tr("{k} údajů chybí").format(k=missing + unassigned)
        elif self._multi:
            _patients, visits, _n, unassigned = self.people_counts()
            missing = sum(1 for paths in self._visits().values() if self._visit_missing(paths))
            if not visits:
                ok, hint = False, tr("Žádná nahrávka nemá pacienta i datum vyšetření.")
            else:
                parts = []
                if missing:
                    parts.append(tr("{k} vyšetření bez věku nebo pohlaví").format(k=missing))
                if unassigned:
                    parts.append(tr("{k} nahrávek se jen spočítá").format(k=unassigned))
                hint = " · ".join(parts)
            self.next_btn.setText(
                tr("Spustit · {v} hodnocení").format(v=visits) if visits else tr("Spustit")
            )
        else:
            if not self.subject.text().strip():
                ok, hint = False, tr("Vyplňte pacienta.")
            elif not self.age.text().strip() or not self.sex.currentData():
                hint = tr("bez věku nebo pohlaví se nevybere věkové okno a pohlaví")
            self.next_btn.setText(tr("Spustit hodnocení"))
        self.next_btn.setEnabled(ok)
        self.hint.setText(hint)
        self._update_summary()

    def go_next(self) -> None:
        """Krok 1 → Pacienti, krok 2 → Spustit."""
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
        return new_plan(self._dir, self.patient(), language, self._task_plans(), self.people())


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
