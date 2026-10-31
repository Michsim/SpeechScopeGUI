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
tabulka nahrávek (`people_table`, prázdné datum a věk jdou dopsat jen pro
tento výpočet), dávka je na úlohu a datum (`TaskPlan.visit`) a po skončení
vznikne vyšetření na každou dvojici pacient a datum. Ze struktury složek se
nic neodvozuje (rozhodnuto 30. 10.); kdo manifest nemá, dostane
„Uložit šablonu manifestu…“ se seznamem nahrávek k vyplnění v Excelu.
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
from ..backend.norm_build import FolderScan, TaskPlan, with_language
from ..backend.norms import RECOMMENDED_CONTROLS  # noqa: F401  (stejná sada textů)
from ..backend.protocol import Protocol
from ..i18n import tr
from . import theme
from .new_norm_dialog import SEX_CHOICES, TaskRow, format_duration
from .widgets.clean_table import card, set_text, style_table
from .widgets.meta_table import MetaRow, MetaTable
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
        self._people: dict[Path, PersonRow] = {}  # nahrávka → pacient, datum… z manifestu
        self._multi = False

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
        self.person_box = QWidget()
        person_layout = QVBoxLayout(self.person_box)
        person_layout.setContentsMargins(0, 0, 0, 0)
        person_layout.setSpacing(6)
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
        person_layout.addLayout(person)
        self.prefill_note = QLabel("")
        self.prefill_note.setObjectName("muted")
        self.prefill_note.setWordWrap(True)
        self.prefill_note.hide()
        person_layout.addWidget(self.prefill_note)
        layout.addWidget(self.person_box)

        # víc pacientů nebo dat z manifestu: místo polí pacienta tabulka nahrávek
        self.people_box = QFrame()
        self.people_box.setObjectName("card")
        people_layout = QVBoxLayout(self.people_box)
        people_layout.setContentsMargins(12, 10, 12, 10)
        people_layout.setSpacing(6)
        self.people_summary = QLabel("")
        self.people_summary.setObjectName("card_title")
        people_layout.addWidget(self.people_summary)
        people_note = QLabel(
            tr(
                "Pacient, datum vyšetření, úloha, věk a pohlaví jsou z manifest.csv ve složce. "
                "Co chybí, dopište v tabulce (platí jen pro tento výpočet). Nahrávka bez pacienta "
                "nebo data se spočítá, ale do žádného vyšetření se nezaloží."
            )
        )
        people_note.setObjectName("muted")
        people_note.setWordWrap(True)
        people_layout.addWidget(people_note)
        self.people_table = MetaTable([tr("soubor"), tr("úloha")], labels=True)
        self.people_table.set_required({meta.SUBJECT, meta.VISIT})
        self.people_table.setMinimumHeight(180)
        self.people_table.edited.connect(self._people_changed)
        people_layout.addWidget(self.people_table, 1)
        layout.addWidget(self.people_box, 1)
        self.people_box.hide()

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
        options = QHBoxLayout()
        self.recursive = QCheckBox(tr("včetně podsložek"))
        self.recursive.toggled.connect(self._rescan_all)
        options.addWidget(self.recursive)
        options.addStretch(1)
        self.template_btn = QPushButton(tr("Uložit šablonu manifestu…"))
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
        note = QLabel(
            tr(
                "Když vybraná složka má manifest.csv se sloupcem task, nahrávky se rozdělí "
                "podle něj a vyplní se i ostatní úlohy; s pacientem a datem v manifestu jde "
                "spočítat víc pacientů nebo vyšetření najednou. Protokol je přibalený pro "
                "danou úlohu, jiný jde vybrat jen v rozšířeném režimu. Do složek "
                "s nahrávkami se nic nezapisuje."
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
        self._refresh_people()
        if not self._multi:
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
        self._multi = is_multi(self._merged_people())
        self.person_box.setVisible(not self._multi)
        self.people_box.setVisible(self._multi)
        self.template_btn.setEnabled(bool(scans))
        if self._multi:
            self._fill_people_table()

    def _merged_people(self) -> dict[Path, PersonRow]:
        """Údaje z manifestu přepsané úpravami v tabulce (ty přežijí přeskenování)."""
        edits = self.people_table.edits()
        out: dict[Path, PersonRow] = {}
        for path, person in self._people.items():
            row = edits.get(path, {})
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

    def _fill_people_table(self) -> None:
        rec_tasks = self._recording_tasks()
        order = sorted(
            self._people.items(), key=lambda kv: (kv[1].subject, kv[1].visit, kv[0].name)
        )
        rows = [
            MetaRow(
                path,
                [path.name, tasks.label(rec_tasks.get(path, ""))],
                {
                    meta.SUBJECT: person.subject,
                    meta.VISIT: person.visit,
                    meta.AGE: "" if person.age is None else f"{person.age:g}",
                    meta.SEX: person.sex,
                },
                tooltip=str(path),
            )
            for path, person in order
        ]
        self.people_table.set_rows(rows, [meta.SUBJECT, meta.VISIT, meta.AGE, meta.SEX])
        self._update_people_summary()

    def _update_people_summary(self) -> None:
        patients, visits, n, missing = self.people_counts()
        text = tr("{p} · vyšetření: {v} · nahrávek: {n}").format(
            p=patients_text(patients), v=visits, n=n
        )
        if missing:
            text += tr(" · bez pacienta nebo data: {k}").format(k=missing)
        self.people_summary.setText(text)

    def _people_changed(self) -> None:
        self._update_people_summary()
        self._update_buttons()

    def set_person(self, path: Path, **values: object) -> None:
        """Ruční doplnění údajů nahrávky (jako dvojklik v tabulce); pro testy a skripty."""
        for name, value in values.items():
            text = "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)
            self.people_table.set_value(path, name, text)

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
        style_table(self.batches, [tr("úloha"), tr("dávka"), tr("odhad")])
        header = self.batches.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        layout.addWidget(card(self.batches))
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
        plans = self._task_plans()
        if self._multi:
            patients, visits, _n, _missing = self.people_counts()
            self.summary_title.setText(
                tr("{p} · {v} vyšetření").format(p=patients_text(patients), v=visits)
            )
        else:
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
            ).format(k=len(rows), n=n, b=len(plans))
        )
        self.batches.setRowCount(len(plans))
        total = 0.0
        known = True
        durations = {rec: seconds for row in rows for rec, seconds in row.scan.durations.items()}
        for r, plan in enumerate(plans):
            proto = plan.proto()
            label = tasks.label(plan.task)
            if plan.visit:
                label += f" · {plan.visit}"
            elif self._multi:
                label += tr(" · bez data")
            set_text(self.batches, r, 0, label, bold=True)
            self.batches.setItem(
                r,
                1,
                QTableWidgetItem(
                    tr("{n} nahrávek · {proto}").format(
                        n=len(plan.inputs), proto=proto.display_name
                    )
                ),
            )
            rate = self._rate(proto.slug())
            audio = sum(durations.get(rec) or 0.0 for rec in plan.inputs)
            if rate and audio:
                seconds = rate / 60 * audio
                total += seconds
                estimate = format_duration(seconds)
            else:
                known = False
                estimate = tr("po prvním běhu")
            set_text(self.batches, r, 2, estimate, muted=True)
        height = self.batches.horizontalHeader().height() + 4
        height += sum(self.batches.rowHeight(i) for i in range(len(plans)))
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
            if self._multi:
                patients, visits, n, missing = self.people_counts()
                if not visits:
                    ok, hint = False, tr("Žádná nahrávka nemá pacienta i datum vyšetření.")
                else:
                    hint = tr("{p} · vyšetření: {v} · nahrávek: {n}").format(
                        p=patients_text(patients), v=visits, n=n
                    )
                    if missing:
                        hint += tr(" · bez data: {k}").format(k=missing)
            elif not self.subject.text().strip():
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
