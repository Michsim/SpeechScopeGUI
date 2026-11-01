"""Vývoj v čase: jeden pacient, víc vyšetření (mřížka datum × úloha).

Mockup https://claude.ai/artifact/MDbiZZGRgG5Wnyzk4GPVfv (`docs/mockups/hodnoceni-typy/`
mimo git), rozhodnuto 1. 11. 2026: uživatel vyplňuje sám, nic se neodvozuje ze složek
ani z názvů souborů. Pacient se zadá jednou (z katalogu se doplní pohlaví a rok
narození, to je dřív zadaný údaj), každé vyšetření má ručně psané datum a ke každé
úloze vlastní složku. Jediný automat je manifest: ve vybrané složce se sloupcem task
vyplní ostatní úlohy téhož vyšetření a datum s věkem jen do prázdných polí.
Dosavadní vyšetření pacienta z katalogu jsou nahoře šedě.

Protokoly (mockup https://claude.ai/artifact/PiCgr7oP7N8VABhECAQ6gQ,
`docs/mockups/vyvoj-protokoly/` mimo git): první řádek mřížky pod záhlavím má u každé
úlohy výběr protokolu, jeden pro všechna nová vyšetření. Výchozí je protokol posledního
vyšetření pacienta s touto úlohou („jako minule“, z `protocol.yaml` běhu), jinak výchozí
přibalený. Jiný protokol než minule jen varuje (oranžově, ⚠ v šedých buňkách, řádek pod
mřížkou s „vrátit jako minule“).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend import meta, tasks
from ..backend.assessment_build import PersonRow, patient_from_scan, split_by_task
from ..backend.assessments import Assessment, patient_key
from ..backend.manifest import key_for, read_manifest
from ..backend.norm_build import default_protocol
from ..backend.protocol import Protocol
from ..i18n import tr
from . import theme
from .widgets.clean_table import card, set_text, style_table

CSV_FILTER = "CSV (*.csv)"
PROTOCOL_ROW = 1  # řádek s výběrem protokolů nad vyšetřeními
REVERT_LINK = "revert"


@dataclass(slots=True)
class SeriesPatient:
    """Pacient z katalogu: údaje a dosavadní vyšetření (pro šedé řádky)."""

    subject: str
    sex: str = ""
    birth_year: int | None = None
    visits: list[Assessment] = field(default_factory=list)


@dataclass(slots=True)
class Cell:
    """Nahrávky jedné úlohy jednoho vyšetření (ze složky, nebo z manifestu)."""

    recordings: list[Path] = field(default_factory=list)
    durations: dict[Path, float | None] = field(default_factory=dict)
    source: str = ""  # cesta složky, nebo „manifest“


@dataclass(slots=True)
class SeriesVisit:
    date: str = ""  # ISO; prázdné = chybí
    age: str = ""  # ručně; prázdné = z roku narození
    cells: dict[str, Cell] = field(default_factory=dict)
    note: str = ""  # „z manifest.csv: datum, věk“


def _date_text(iso: str) -> str:
    try:
        day = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{day.day}. {day.month}. {day.year}"


class SeriesEditor(QWidget):
    """Pacient nahoře, pod ním mřížka vyšetření; `changed` po každé úpravě."""

    changed = Signal()
    previous_loaded = Signal(str)  # jazyk minulého vyšetření pacienta (prázdné = nevíme)

    def __init__(
        self,
        task_keys: list[str],
        patients: Callable[[], list[SeriesPatient]] = list,
        *,
        recursive: Callable[[], bool] = lambda: False,
        protocols: list[Protocol] | None = None,
        advanced: bool = False,
        run_protocol: Callable[[str], Protocol | None] = lambda _key: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tasks = list(task_keys)
        self._protocols = list(protocols or [])
        self._advanced = advanced
        self._run_protocol = run_protocol  # klíč běhu → jeho protocol.yaml
        self._run_cache: dict[str, Protocol | None] = {}
        self._choice: dict[str, str] = {}  # úloha → protokol zvolený ručně
        self._previous: dict[str, str] = {}  # úloha → protokol posledního vyšetření
        self._old_protocols: dict[tuple[str, str], str] = {}  # (id hodnocení, úloha) → protokol
        self._previous_for: str | None = None  # pro kterého pacienta je `_previous`
        self.previous_language = ""
        self.protocol_combos: dict[str, QComboBox] = {}
        self._patients_source = patients
        self._patients: dict[str, SeriesPatient] = {}
        self._recursive = recursive
        self._visits: list[SeriesVisit] = []
        self._building = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        person = QFrame()
        person.setObjectName("card")
        person_layout = QHBoxLayout(person)
        person_layout.setContentsMargins(14, 10, 14, 10)
        person_layout.setSpacing(10)
        person_layout.addWidget(QLabel(tr("Pacient")))
        self.subject = QComboBox()
        self.subject.setEditable(True)
        self.subject.setMinimumWidth(180)
        self.subject.lineEdit().setPlaceholderText(tr("ID pacienta"))
        self.subject.currentTextChanged.connect(lambda _t: self._subject_changed())
        person_layout.addWidget(self.subject)
        person_layout.addWidget(QLabel(tr("Pohlaví")))
        self.sex = QComboBox()
        for code, label in (("", "–"), (meta.FEMALE, tr("žena")), (meta.MALE, tr("muž"))):
            self.sex.addItem(label, code)
        self.sex.currentIndexChanged.connect(lambda _i: self._changed())
        person_layout.addWidget(self.sex)
        person_layout.addWidget(QLabel(tr("Rok narození")))
        self.birth_year = QLineEdit()
        self.birth_year.setPlaceholderText(tr("rok"))
        self.birth_year.setMaximumWidth(80)
        self.birth_year.setToolTip(tr("Věk se u každého vyšetření dopočítá z roku narození."))
        self.birth_year.textChanged.connect(lambda _t: self._refill())
        person_layout.addWidget(self.birth_year)
        person_layout.addStretch(1)
        self.known = QLabel("")
        self.known.setObjectName("pill")
        theme.set_role(self.known, "neutral")
        self.known.hide()
        person_layout.addWidget(self.known)
        layout.addWidget(person)

        head = QHBoxLayout()
        title = QLabel(tr("Vyšetření"))
        title.setObjectName("card_title")
        head.addWidget(title)
        hint = QLabel(tr("datum a složka s nahrávkami pro každou úlohu"))
        hint.setObjectName("muted")
        head.addWidget(hint)
        head.addStretch(1)
        # místo pro jazyk nahrávek (průvodce sem přesune svůj výběr jazyka)
        self.head_extra = QHBoxLayout()
        self.head_extra.setSpacing(8)
        head.addLayout(self.head_extra)
        self.manifest_btn = QPushButton(tr("Načíst z manifestu…"))
        self.manifest_btn.setFlat(True)
        self.manifest_btn.setToolTip(
            tr("CSV se sloupci path, visit a task (případně subject, age, sex) vyplní mřížku.")
        )
        self.manifest_btn.clicked.connect(lambda: self._load_manifest())
        head.addWidget(self.manifest_btn)
        layout.addLayout(head)

        self.table = QTableWidget()
        layout.addWidget(card(self.table), 1)
        self.protocol_warning = QLabel("")
        self.protocol_warning.setWordWrap(True)
        self.protocol_warning.setTextFormat(Qt.TextFormat.RichText)
        self.protocol_warning.setStyleSheet(
            "QLabel { background: #fff4e5; border: 1px solid #fbd38d; color: #c2410c;"
            " border-radius: 8px; padding: 6px 10px; }"
        )
        self.protocol_warning.linkActivated.connect(lambda _l: self.revert_protocols())
        self.protocol_warning.hide()
        layout.addWidget(self.protocol_warning)
        buttons = QHBoxLayout()
        self.add_btn = QPushButton(tr("+ Přidat vyšetření"))
        self.add_btn.clicked.connect(lambda: self.add_visit())
        buttons.addWidget(self.add_btn)
        buttons.addStretch(1)
        self.note = QLabel("")
        self.note.setObjectName("muted")
        buttons.addWidget(self.note)
        layout.addLayout(buttons)
        self.reload_patients()
        self.add_visit()

    # --- pacient --------------------------------------------------------------------

    def reload_patients(self) -> None:
        self._patients = {patient_key(p.subject): p for p in self._patients_source()}
        current = self.subject.currentText()
        self.subject.blockSignals(True)
        self.subject.clear()
        self.subject.addItems(sorted(p.subject for p in self._patients.values()))
        self.subject.setCurrentText(current)
        self.subject.blockSignals(False)

    def set_patient(self, subject: str, age: float | None = None, sex: str = "") -> None:
        """Předvyplnění pacienta (Nové vyšetření… u pacienta, Další vyšetření)."""
        self.subject.setCurrentText(subject)
        if sex:
            self.sex.setCurrentIndex(max(0, self.sex.findData(sex)))
        known = self._patients.get(patient_key(subject))
        if (known is None or known.birth_year is None) and age is not None:
            last = known.visits[0].date if known and known.visits else ""
            year = int(last[:4]) if last[:4].isdigit() else dt.date.today().year
            self.birth_year.setText(str(year - int(round(age))))
        self._changed()

    def _subject_changed(self) -> None:
        known = self._patients.get(patient_key(self.subject.currentText()))
        if known is not None:
            if known.sex and not self.sex.currentData():
                self.sex.setCurrentIndex(max(0, self.sex.findData(known.sex)))
            if known.birth_year and not self.birth_year.text().strip():
                self.birth_year.setText(str(known.birth_year))
            self.known.setText(tr("z katalogu · {n} vyšetření").format(n=len(known.visits)))
        self.known.setVisible(known is not None)
        self._load_previous()
        self._refill()

    def subject_text(self) -> str:
        return " ".join(self.subject.currentText().split())

    def sex_code(self) -> str:
        return str(self.sex.currentData() or "")

    def birth_year_value(self) -> int | None:
        text = self.birth_year.text().strip()
        return int(text) if text.isdigit() and 1900 <= int(text) <= dt.date.today().year else None

    # --- vyšetření -----------------------------------------------------------------

    def visits(self) -> list[SeriesVisit]:
        return list(self._visits)

    def add_visit(self, date: str = "") -> int:
        self._visits.append(SeriesVisit(date=meta.normalize_visit(date) if date else ""))
        self._refill()
        return len(self._visits) - 1

    def remove_visit(self, index: int) -> None:
        if 0 <= index < len(self._visits):
            del self._visits[index]
            if not self._visits:
                self._visits.append(SeriesVisit())
            self._refill()

    def set_date(self, index: int, text: str) -> None:
        visit = self._visits[index]
        visit.date = meta.normalize_visit(text) if text.strip() else ""
        self._refill()

    def set_age(self, index: int, text: str) -> None:
        self._visits[index].age = meta.normalize_value(meta.AGE, text) if text.strip() else ""
        self._refill()

    def set_folder(self, index: int, task: str, folder: Path | None) -> None:
        """Složka úlohy vyšetření; manifest se sloupcem task vyplní i ostatní (prázdné)
        úlohy téhož vyšetření, datum a věk z manifestu jen do prázdných polí."""
        visit = self._visits[index]
        if folder is None:
            visit.cells.pop(task, None)
            self._refill()
            return
        split = split_by_task(folder, task, recursive=self._recursive())
        filled: list[str] = []
        for other, scan in split.items():
            if other not in self._tasks or not scan.recordings:
                continue
            if other != task and visit.cells.get(other):
                continue
            visit.cells[other] = Cell(list(scan.recordings), dict(scan.durations), str(folder))
        if task not in split:
            visit.cells[task] = Cell([], {}, str(folder))
        scan = split.get(task) or next(iter(split.values()), None)
        if scan is not None and scan.manifest is not None:
            found = patient_from_scan(scan)
            if found.date and not visit.date:
                visit.date = found.date
                filled.append(tr("datum"))
            if found.age is not None and not visit.age and self.birth_year_value() is None:
                visit.age = f"{found.age:g}"
                filled.append(tr("věk"))
            if found.subject and not self.subject_text():
                self.subject.setCurrentText(found.subject)
            if found.sex and not self.sex_code():
                self.sex.setCurrentIndex(max(0, self.sex.findData(found.sex)))
        visit.note = (
            tr("z {file}: {what}").format(file=scan.manifest.path.name, what=", ".join(filled))
            if filled and scan is not None and scan.manifest is not None
            else visit.note
        )
        self._refill()

    def _browse(self, index: int, task: str) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            tr("Složka s nahrávkami: {task} · {date}").format(
                task=tasks.label(task), date=_date_text(self._visits[index].date) or "–"
            ),
        )
        if chosen:
            self.set_folder(index, task, Path(chosen))

    def _load_manifest(self, path: Path | None = None) -> int:
        """CSV s path, visit, task (a subject, age, sex): vyšetření pacienta do mřížky."""
        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(self, tr("Manifest"), "", CSV_FILTER)
            if not chosen:
                return 0
            path = Path(chosen)
        return self.load_manifest(path)

    def load_manifest(self, path: Path) -> int:
        manifest = read_manifest(path)
        subject = patient_key(self.subject_text())
        rows: dict[str, SeriesVisit] = {}
        for key, data in manifest.rows.items():
            who = data.get(meta.SUBJECT, "").strip()
            if subject and who and patient_key(who) != subject:
                continue  # jiný pacient ze společného manifestu
            if who and not self.subject_text():
                self.subject.setCurrentText(who)
                subject = patient_key(who)
            visit_date = meta.normalize_visit(data.get(meta.VISIT, ""))
            task = manifest.tasks.get(key, "")
            if not task:
                continue
            visit = rows.setdefault(visit_date, SeriesVisit(date=visit_date))
            if data.get(meta.AGE) and not visit.age:
                visit.age = meta.normalize_value(meta.AGE, data[meta.AGE])
            if data.get(meta.SEX) and not self.sex_code():
                self.sex.setCurrentIndex(max(0, self.sex.findData(meta.parse_sex(data[meta.SEX]))))
            cell = visit.cells.setdefault(task, Cell(source=tr("manifest")))
            cell.recordings.append(key)
        if not rows:
            return 0
        self._visits = [v for v in self._visits if v.cells] + [
            rows[d] for d in sorted(rows, key=lambda d: (d == "", d))
        ]
        self._refill()
        return len(rows)

    # --- protokoly ------------------------------------------------------------------

    def _run_proto(self, key: str) -> Protocol | None:
        if key not in self._run_cache:
            try:
                self._run_cache[key] = self._run_protocol(key)
            except Exception:  # noqa: BLE001  chybějící nebo rozbitý protocol.yaml
                self._run_cache[key] = None
        return self._run_cache[key]

    def _load_previous(self) -> None:
        """Protokoly a jazyk posledních vyšetření pacienta (od nejnovějšího)."""
        key = patient_key(self.subject_text())
        if key == self._previous_for:
            return
        self._previous_for = key
        self._previous, self._old_protocols, self.previous_language = {}, {}, ""
        known = self._patients.get(key)
        for assessment in sorted(known.visits if known else [], key=lambda a: a.date)[::-1]:
            for ref in assessment.recordings:
                if (assessment.id, ref.task) in self._old_protocols:
                    continue
                proto = self._run_proto(ref.run)
                if proto is None:
                    continue
                self._old_protocols[(assessment.id, ref.task)] = proto.name
                self._previous.setdefault(ref.task, proto.name)
                if not self.previous_language:
                    for provider in ("transcript", "nlp"):
                        lang = proto.config.get(provider, {}).get("language")
                        if lang:
                            self.previous_language = str(lang)
                            break
        self._choice.clear()  # jiný pacient: zase jako minule
        self.previous_loaded.emit(self.previous_language)

    def _find(self, name: str) -> Protocol | None:
        return next((p for p in self._protocols if p.name == name), None)

    def protocol_choices(self, task: str) -> list[Protocol]:
        """Protokoly úlohy (základní úlohy u vlastní); přibalené, v rozšířeném režimu všechny.
        Protokol z minula je v nabídce vždy, i vlastní u klinika."""
        base = tasks.base_task(task)
        out = [p for p in self._protocols if p.task == base and (p.builtin or self._advanced)]
        previous = self._find(self._previous.get(task, ""))
        if previous is not None and previous not in out:
            out.append(previous)
        return out

    def _default(self, task: str) -> Protocol | None:
        custom = tasks.registry().get(task)
        if custom is not None and custom.protocol and self._find(custom.protocol):
            return self._find(custom.protocol)
        return default_protocol(tasks.base_task(task), self._protocols)

    def protocol_for(self, task: str) -> Protocol | None:
        """Ručně zvolený, jinak jako minule (když existuje), jinak výchozí."""
        for name in (self._choice.get(task, ""), self._previous.get(task, "")):
            found = self._find(name) if name else None
            if found is not None:
                return found
        return self._default(task)

    def set_protocol(self, task: str, name: str) -> None:
        current = self.protocol_for(task)
        if self._find(name) is None or (current is not None and current.name == name):
            return
        self._choice[task] = name
        self._refill()

    def previous_protocol(self, task: str) -> str:
        return self._previous.get(task, "")

    def changed_tasks(self) -> list[str]:
        """Úlohy s jiným protokolem než u posledního vyšetření pacienta."""
        out = []
        for task in self._tasks:
            previous = self._previous.get(task)
            current = self.protocol_for(task)
            if previous and current is not None and current.name != previous:
                out.append(task)
        return out

    def revert_protocols(self) -> None:
        """Vrátit protokoly jako minule."""
        for task in self.changed_tasks():
            self._choice.pop(task, None)
        self._refill()

    def protocol_warning_text(self) -> str:
        changed = self.changed_tasks()
        if not changed:
            return ""
        names = ", ".join(tasks.label(t) for t in changed)
        return tr(
            "{tasks}: jiný protokol než u předchozích vyšetření, hodnoty nemusí být srovnatelné."
        ).format(tasks=names)

    def needs_language(self) -> bool:
        """Jazyk jen u úloh s přepisem (spontánní řeč, čtení); bez nahrávek vždy."""
        used = {t for v in self._visits for t, c in v.cells.items() if c.recordings}
        return not used or any(tasks.base_task(t) in contract.TRANSCRIPT_TASKS for t in used)

    def _protocol_cell(self, task: str, changed: bool) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(4, 4, 4, 4)
        column.setSpacing(2)
        choices = self.protocol_choices(task)
        current = self.protocol_for(task)
        main: QWidget
        if len(choices) > 1:
            combo = QComboBox()
            for proto in choices:
                combo.addItem(proto.display_name, proto.name)
            if current is not None:
                combo.setCurrentIndex(max(0, combo.findData(current.name)))
                combo.setToolTip(current.display_name)
            # šířka podle nejdelšího názvu (s šipkou), jinak se název ořízl
            widest = max(combo.fontMetrics().horizontalAdvance(p.display_name) for p in choices)
            combo.setMinimumWidth(min(widest + 56, 280))
            combo.currentIndexChanged.connect(
                lambda _i, t=task, c=combo: self.set_protocol(t, str(c.currentData()))
            )
            self.protocol_combos[task] = combo
            main = combo
        else:
            # jediná možnost (klinik, přibalený): jen název, neaktivní pole se špatně čte
            label = QLabel(current.display_name if current is not None else "–")
            label.setStyleSheet("font-weight: 600;")
            label.setToolTip(tr("Jiný protokol jde vybrat jen v rozšířeném režimu."))
            main = label
        previous = self._previous.get(task, "")
        if changed:
            shown = self._find(previous)
            sub_text = tr("minule: {name}").format(
                name=shown.display_name if shown is not None else previous
            )
            main.setStyleSheet(
                "QComboBox { border-color: #f59e0b; color: #c2410c; background: #fff4e5; }"
                if isinstance(main, QComboBox)
                else "font-weight: 600; color: #c2410c;"
            )
        elif previous:
            sub_text = tr("jako minule")
        elif self._old_protocols:
            sub_text = tr("výchozí · minule nebylo")
        else:
            sub_text = tr("výchozí")
        sub = QLabel(sub_text)
        sub.setStyleSheet(f"color: {'#c2410c' if changed else theme.MUTED}; font-size: 11px;")
        column.addWidget(main)
        column.addWidget(sub)
        return box

    # --- výsledek ------------------------------------------------------------------

    def age_for(self, visit: SeriesVisit) -> float | None:
        if visit.age:
            return meta.parse_age(visit.age)
        year = self.birth_year_value()
        if year is not None and visit.date[:4].isdigit():
            return float(int(visit.date[:4]) - year)
        return None

    def people(self) -> dict[Path, PersonRow]:
        """Nahrávka → pacient, datum, věk a pohlaví (plán s víc vyšetřeními)."""
        subject = self.subject_text()
        out: dict[Path, PersonRow] = {}
        for visit in self._visits:
            for cell in visit.cells.values():
                for rec in cell.recordings:
                    out[key_for(rec)] = PersonRow(
                        subject=subject,
                        visit=visit.date,
                        age=self.age_for(visit),
                        sex=self.sex_code(),
                    )
        return out

    def task_inputs(self) -> dict[str, dict[str, list[Path]]]:
        """Úloha → datum → nahrávky (dávka na úlohu a datum)."""
        out: dict[str, dict[str, list[Path]]] = {}
        for visit in self._visits:
            if not visit.date:
                continue
            for task, cell in visit.cells.items():
                if cell.recordings:
                    out.setdefault(task, {}).setdefault(visit.date, []).extend(cell.recordings)
        return out

    def durations(self) -> dict[Path, float | None]:
        out: dict[Path, float | None] = {}
        for visit in self._visits:
            for cell in visit.cells.values():
                out.update(cell.durations)
        return out

    def problems(self) -> list[str]:
        """Co brání Spustit: pacient, datum, aspoň jedna nahrávka, stejné datum dvakrát."""
        out = []
        if not self.subject_text():
            out.append(tr("Vyplňte pacienta."))
        filled = [v for v in self._visits if any(c.recordings for c in v.cells.values())]
        if not filled:
            out.append(tr("Vyberte složku aspoň pro jednu úlohu."))
        if any(not v.date for v in filled):
            out.append(tr("Doplňte datum vyšetření."))
        dates = [v.date for v in filled if v.date]
        known = self._patients.get(patient_key(self.subject_text()))
        old = {a.date for a in known.visits} if known else set()
        if len(dates) != len(set(dates)) or set(dates) & old:
            out.append(tr("Stejné datum je u dvou vyšetření."))
        return out

    def new_visit_count(self) -> int:
        return sum(
            1 for v in self._visits if v.date and any(c.recordings for c in v.cells.values())
        )

    def recording_count(self) -> int:
        return sum(len(c.recordings) for v in self._visits for c in v.cells.values())

    # --- kreslení ------------------------------------------------------------------

    def _changed(self) -> None:
        if not self._building:
            self.changed.emit()

    def _refill(self) -> None:
        """Mřížka: šedé řádky z katalogu, pak nová vyšetření s poli a tlačítky složek."""
        self._building = True
        known = self._patients.get(patient_key(self.subject_text()))
        old = sorted(known.visits, key=lambda a: a.date) if known else []
        headers = [tr("datum"), tr("věk")] + [tasks.label(t) for t in self._tasks] + [""]
        table = self.table
        # staré prvky v buňkách napřed skrýt a zahodit: Qt je maže až z fronty událostí
        # a do té doby se kreslily přes nové (překrývající se „Změnit…“)
        for r in range(table.rowCount()):
            for c in range(table.columnCount()):
                widget = table.cellWidget(r, c)
                if widget is not None:
                    widget.hide()
        table.setRowCount(0)
        style_table(table, headers, row_height=44)
        table.setRowCount(PROTOCOL_ROW + len(old) + len(self._visits))
        last = len(headers) - 1
        changed = set(self.changed_tasks())
        self.protocol_combos = {}
        for c in range(len(headers)):
            set_text(table, 0, c, tr("protokol") if c == 0 else "", muted=True)
            table.item(0, c).setBackground(QColor("#f7f8fb"))
        for c, task in enumerate(self._tasks, start=2):
            table.setCellWidget(0, c, self._protocol_cell(task, task in changed))
        table.setRowHeight(0, 66)
        for i, assessment in enumerate(old):
            r = PROTOCOL_ROW + i
            set_text(table, r, 0, _date_text(assessment.date), muted=True)
            set_text(
                table, r, 1, "" if assessment.age is None else f"{assessment.age:g}", muted=True
            )
            have = set(assessment.tasks)
            for c, task in enumerate(self._tasks, start=2):
                text = "–"
                if task in have:
                    text = tr("spočítáno") + (" ⚠" if task in changed else "")
                set_text(table, r, c, text, muted=True)
            set_text(table, r, last, "", muted=True)
            for c in range(len(headers)):
                item = table.item(r, c)
                if item is not None:
                    item.setBackground(QColor("#fafafa"))
                    item.setToolTip(tr("Vyšetření je už v katalogu."))
            for c, task in enumerate(self._tasks, start=2):
                name = self._old_protocols.get((assessment.id, task))
                if name and task in have:
                    shown = self._find(name)
                    tip = tr("Spočítáno protokolem {name}.").format(
                        name=shown.display_name if shown is not None else name
                    )
                    if task in changed:
                        tip += " " + tr("Teď je zvolený jiný protokol.")
                    table.item(r, c).setToolTip(tip)
        for i, visit in enumerate(self._visits):
            r = PROTOCOL_ROW + len(old) + i
            date = QLineEdit(_date_text(visit.date))
            date.setPlaceholderText(tr("datum"))
            if not visit.date:
                date.setStyleSheet(f"background: {theme.WARN_SOFT}; border-color: #f59e0b;")
            if visit.note:
                date.setToolTip(visit.note)
            date.editingFinished.connect(
                lambda i=i, w=date: (
                    self.set_date(i, w.text())
                    if meta.normalize_visit(w.text()) != self._visits[i].date
                    else None
                )
            )
            table.setCellWidget(r, 0, date)
            age = QLineEdit(visit.age)
            computed = self.age_for(visit)
            age.setPlaceholderText("" if computed is None else f"{computed:g}")
            age.setMaximumWidth(70)
            age.editingFinished.connect(
                lambda i=i, w=age: (
                    self.set_age(i, w.text()) if w.text().strip() != self._visits[i].age else None
                )
            )
            table.setCellWidget(r, 1, age)
            for c, task in enumerate(self._tasks, start=2):
                cell = visit.cells.get(task)
                box = QWidget()
                row = QHBoxLayout(box)
                row.setContentsMargins(4, 2, 4, 2)
                row.setSpacing(6)
                btn = QPushButton(tr("Vybrat…") if cell is None else tr("Změnit…"))
                # stejná šířka pro obě podoby: sloupec neposkakuje a text se neořízne
                metrics = btn.fontMetrics()
                btn.setMinimumWidth(
                    max(metrics.horizontalAdvance(tr(t)) for t in ("Vybrat…", "Změnit…")) + 36
                )
                btn.clicked.connect(lambda _c=False, i=i, t=task: self._browse(i, t))
                row.addWidget(btn)
                n = len(cell.recordings) if cell else 0
                count = QLabel(f"{n} ✓" if n else "–")
                count.setMinimumWidth(count.fontMetrics().horizontalAdvance("99 ✓") + 4)
                count.setStyleSheet(
                    "color: #1d4ed8; font-weight: 600;" if n else f"color: {theme.MUTED};"
                )
                if cell is not None:
                    count.setToolTip(cell.source)
                row.addWidget(count)
                row.addStretch(1)
                # pevná šířka buňky: sizeHint prvku v buňce nezná styl a štítek se ořezal
                box.setMinimumWidth(btn.minimumWidth() + count.minimumWidth() + 6 + 8)
                table.setCellWidget(r, c, box)
            remove = QPushButton("✕")
            remove.setFlat(True)
            remove.setFixedWidth(40)
            remove.setToolTip(tr("Odebrat vyšetření"))
            remove.clicked.connect(lambda _c=False, i=i: self.remove_visit(i))
            table.setCellWidget(r, last, remove)
        table.resizeColumnsToContents()
        # šířka sloupců i podle prvků v buňkách (resizeColumnsToContents je nevidí)
        header = table.horizontalHeader()
        for c in range(table.columnCount()):
            widest = max(
                (
                    _polished_width(table.cellWidget(r, c))
                    for r in range(table.rowCount())
                    if table.cellWidget(r, c) is not None
                ),
                default=0,
            )
            if widest + 16 > header.sectionSize(c):
                header.resizeSection(c, widest + 16)
        header.setStretchLastSection(False)
        warning = self.protocol_warning_text()
        link = tr("vrátit jako minule")
        self.protocol_warning.setText(
            f"{warning} <a href='{REVERT_LINK}' style='color:#c2410c'>{link}</a>" if warning else ""
        )
        self.protocol_warning.setVisible(bool(warning))
        notes = [v.note for v in self._visits if v.note]
        self.note.setText(" · ".join(notes))
        self._building = False
        self.changed.emit()


__all__ = ["SeriesEditor", "SeriesPatient", "SeriesVisit"]


def _polished_width(widget: QWidget) -> int:
    """Šířka prvku i se stylem aplikace (bez ensurePolished nezná odsazení tlačítek)."""
    widget.ensurePolished()
    for child in widget.findChildren(QWidget):
        child.ensurePolished()
    layout = widget.layout()
    if layout is not None:
        layout.invalidate()
    return max(widget.sizeHint().width(), widget.minimumSizeHint().width(), widget.minimumWidth())
