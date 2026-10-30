"""Hodnocení z nahrávek: plán dávek po úlohách, po poslední dávce hotové hodnocení.

Klinik vybere složky s nahrávkami pacienta (nebo jednu složku s `manifest.csv`
se sloupcem `task`), GUI zařadí jednu běžnou dávku na úlohu s přibaleným
protokolem a po poslední dávce samo založí `Assessment` s odkazy na řádky
běhů. Plán je `assessments/<id>.plan.json`; dokud existuje, je hodnocení
rozpracované (stránka Hodnocení ho ukáže jako „počítá se“). Stav dávek sdílí
`norm_build.PlanState` s plánem normy, hlavní okno oba plány obsluhuje
stejně (`start_plan`, `build_plan`, `resume_plan`).

Do složek s nahrávkami se nic nezapisuje; manifest dávky vzniká v paměti
z údajů pacienta a knihovna ho dostane jako normalizovanou kopii ve složce
běhu, takže `features.csv` má `subject`, `age`, `sex` a `visit`.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .. import contract
from ..i18n import tr
from . import meta, tasks
from .assessments import Assessment, AssessmentStore, RecordingRef, new_id
from .manifest import Manifest
from .norm_build import PENDING, FolderScan, PlanState, TaskPlan, scan_folder
from .protocol import Protocol
from .visits import Recording, Visit

PLAN_SUFFIX = ".plan.json"
PLAN_VERSION = 2  # 2: dávky s datem (`visit`) a `people` pro víc pacientů; 1 se čte dál


normalize_visit = meta.normalize_visit  # dřív tady; sdílí ho tabulka metadat i Analýza


def patients_text(n: int) -> str:
    """„1 pacient“, „2 pacienti“, „5 pacientů“."""
    if n == 1:
        return tr("1 pacient")
    if 2 <= n <= 4:
        return tr("{n} pacienti").format(n=n)
    return tr("{n} pacientů").format(n=n)


@dataclass(slots=True)
class PersonRow:
    """Údaje jedné nahrávky z manifestu, když je v plánu víc pacientů nebo dat."""

    subject: str = ""
    visit: str = ""  # ISO; prázdné = nahrávka se spočítá, ale do vyšetření se nezaloží
    age: float | None = None
    sex: str = ""


@dataclass(slots=True)
class Patient:
    subject: str = ""
    age: float | None = None
    sex: str = ""
    date: str = ""  # ISO


@dataclass(slots=True)
class AssessmentPlan(PlanState):
    path: Path  # assessments/<id>.plan.json
    id: str
    patient: Patient
    language: str
    tasks: list[TaskPlan]
    created: str = ""
    # víc pacientů nebo dat z manifestu: nahrávka → pacient, datum, věk, pohlaví;
    # prázdné = jeden pacient a jedno datum (`patient`)
    people: dict[Path, PersonRow] = field(default_factory=dict)

    @property
    def key(self) -> Path:
        return self.path

    @property
    def multi(self) -> bool:
        return bool(self.people)

    def subjects(self) -> list[str]:
        return sorted({p.subject for p in self.people.values() if p.subject})

    def visits(self) -> int:
        """Kolik vyšetření (pacient a datum) z plánu vznikne."""
        if not self.multi:
            return 1
        return len({(p.subject, p.visit) for p in self.people.values() if p.subject and p.visit})

    @property
    def done(self) -> int:
        return sum(1 for t in self.tasks if t.finished)

    @property
    def all_finished(self) -> bool:
        return all(t.finished for t in self.tasks)

    @property
    def all_ok(self) -> bool:
        return all(t.status == "ok" for t in self.tasks)

    @property
    def buildable(self) -> bool:
        return any(t.table is not None for t in self.tasks)

    def title(self, task: TaskPlan) -> str:
        if self.multi:
            subjects = sorted(
                {self.people[_key(r)].subject for r in task.inputs if _key(r) in self.people}
            )
            who = ", ".join(subjects) if 0 < len(subjects) <= 3 else patients_text(len(subjects))
            text = tr("Hodnocení {who} · {task}").format(who=who, task=tasks.label(task.task))
            if task.visit:
                text += f" · {task.visit}"
            return text
        return tr("Hodnocení {subject} · {task}").format(
            subject=self.patient.subject, task=tasks.label(task.task)
        )

    def manifest_for(self, task: TaskPlan) -> Manifest:
        """Manifest dávky: pacient, věk, pohlaví a datum pro každou nahrávku."""
        manifest = Manifest(path=self.path.with_name(f"{self.id}-{task.key}.csv"))
        manifest.columns = [meta.SUBJECT, meta.AGE, meta.SEX, meta.VISIT]
        p = self.patient
        for rec in task.inputs:
            person = self.people.get(_key(rec))
            subject = person.subject if person else p.subject
            age = person.age if person else p.age
            manifest.rows[_key(rec)] = {
                meta.SUBJECT: subject,
                meta.AGE: "" if age is None else f"{age:g}",
                meta.SEX: person.sex if person else p.sex,
                meta.VISIT: person.visit if person else p.date,
            }
        return manifest

    # --- soubor -------------------------------------------------------------

    def to_dict(self) -> dict:
        p = self.patient
        return {
            "format": PLAN_VERSION,
            "id": self.id,
            "patient": {"subject": p.subject, "age": p.age, "sex": p.sex, "date": p.date},
            "language": self.language,
            "created": self.created,
            "tasks": [
                {
                    "task": t.task,
                    "visit": t.visit,
                    "protocol": t.protocol,
                    "inputs": [str(x) for x in t.inputs],
                    "status": t.status,
                    "run_dir": str(t.run_dir) if t.run_dir else "",
                }
                for t in self.tasks
            ],
            "people": [
                {
                    "path": str(path),
                    "subject": person.subject,
                    "visit": person.visit,
                    "age": person.age,
                    "sex": person.sex,
                }
                for path, person in self.people.items()
            ],
        }

    def save(self) -> Path:
        if not self.created:
            self.created = dt.datetime.now().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.path

    @classmethod
    def load(cls, path: Path) -> AssessmentPlan:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("format", 1)) > PLAN_VERSION:
            raise ValueError("plán hodnocení je z novější verze aplikace")
        p = data.get("patient", {})
        patient = Patient(
            subject=str(p.get("subject") or ""),
            age=meta.parse_age(p.get("age")),
            sex=meta.parse_sex(p.get("sex")) if p.get("sex") else "",
            date=str(p.get("date") or ""),
        )
        tasks = [
            TaskPlan(
                task=str(t["task"]),
                protocol=dict(t["protocol"]),
                inputs=[Path(x) for x in t.get("inputs", [])],
                status=str(t.get("status", PENDING)),
                run_dir=Path(t["run_dir"]) if t.get("run_dir") else None,
                visit=str(t.get("visit") or ""),
            )
            for t in data.get("tasks", [])
        ]
        people = {
            Path(str(row["path"])): PersonRow(
                subject=str(row.get("subject") or ""),
                visit=str(row.get("visit") or ""),
                age=meta.parse_age(row.get("age")),
                sex=meta.parse_sex(row.get("sex")) if row.get("sex") else "",
            )
            for row in data.get("people", [])
            if row.get("path")
        }
        return cls(
            path=path,
            id=str(data.get("id") or path.name.removesuffix(PLAN_SUFFIX)),
            patient=patient,
            language=str(data.get("language") or ""),
            tasks=tasks,
            created=str(data.get("created") or ""),
            people=people,
        )

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


def _key(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def new_plan(
    folder: Path,
    patient: Patient,
    language: str,
    tasks: list[TaskPlan],
    people: dict[Path, PersonRow] | None = None,
) -> AssessmentPlan:
    plan_id = new_id()
    return AssessmentPlan(
        folder / f"{plan_id}{PLAN_SUFFIX}", plan_id, patient, language, tasks, people=people or {}
    )


def people_from_scans(scans: dict[str, FolderScan]) -> dict[Path, PersonRow]:
    """Pacient, datum, věk a pohlaví každé nahrávky z manifestů složek.

    Nahrávka bez řádku v manifestu nebo bez pacienta dostane prázdného pacienta;
    průvodce ji pak nechá doplnit, nebo vynechá."""
    people: dict[Path, PersonRow] = {}
    for scan in scans.values():
        for rec in scan.recordings:
            key = _key(rec)
            if key in people:
                continue
            row = scan.manifest.meta_for(rec) if scan.manifest is not None else None
            row = row or {}
            people[key] = PersonRow(
                subject=str(row.get(meta.SUBJECT, "")).strip(),
                visit=normalize_visit(row.get(meta.VISIT, "")),
                age=meta.parse_age(row.get(meta.AGE, "")),
                sex=meta.parse_sex(row.get(meta.SEX, "")) if row.get(meta.SEX) else "",
            )
    return people


def is_multi(people: dict[Path, PersonRow]) -> bool:
    """Víc pacientů nebo víc dat vyšetření: průvodce přepne na tabulku nahrávek."""
    subjects = {p.subject for p in people.values() if p.subject}
    visits = {p.visit for p in people.values() if p.visit}
    return len(subjects) > 1 or len(visits) > 1


def split_by_visit(recordings: list[Path], people: dict[Path, PersonRow]) -> dict[str, list[Path]]:
    """Nahrávky úlohy po datech vyšetření (dávka na úlohu a datum); bez data pod „“."""
    groups: dict[str, list[Path]] = {}
    for rec in recordings:
        person = people.get(_key(rec))
        groups.setdefault(person.visit if person else "", []).append(rec)
    return dict(sorted(groups.items()))


def write_manifest_template(target: Path, recordings: dict[Path, str], base: Path) -> Path:
    """CSV se seznamem nahrávek a prázdnými sloupci k vyplnění v Excelu.

    `recordings` je nahrávka → úloha (z řádku průvodce); cesty jsou relativní
    k `base`, aby manifest šel uložit vedle nahrávek. Do složky se zapíše jen
    tam, kam uživatel ukáže."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["path", meta.SUBJECT, meta.VISIT, "task", meta.AGE, meta.SEX])
        for rec, task in recordings.items():
            try:
                shown = rec.relative_to(base)
            except ValueError:
                shown = rec
            writer.writerow([str(shown), "", "", task, "", ""])
    return target


def pending_plans(folder: Path) -> list[AssessmentPlan]:
    """Rozpracovaná hodnocení (plán ještě existuje), nejnovější první."""
    if not folder.is_dir():
        return []
    out: list[AssessmentPlan] = []
    for path in sorted(folder.glob(f"*{PLAN_SUFFIX}")):
        try:
            out.append(AssessmentPlan.load(path))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue  # rozbitý plán nesmí shodit stránku
    out.sort(key=lambda p: p.created, reverse=True)
    return out


def plan_from_assessment(
    assessment: Assessment, visit: Visit, missing: list[RecordingRef], folder: Path
) -> tuple[AssessmentPlan | None, list[str]]:
    """Plán přepočtu hotového hodnocení: tytéž nahrávky a protokoly, současná knihovna.

    Nahrávky jsou cesty z tabulek běhů, protokol úlohy je `protocol.yaml` jejího
    běhu (přesně to, čím se počítalo, včetně jazyka). Plán má id hodnocení, takže
    `finish_plan` hodnocení přepíše na místě. Když něco chybí (nahrávka na disku,
    běh, protokol), vrátí None a důvody.
    """
    problems: list[str] = []
    if missing:
        problems.append(
            tr("v bězích chybí: {files}").format(files=", ".join(r.file for r in missing))
        )
    by_task: dict[str, list[Recording]] = {}
    for rec in visit.recordings:
        by_task.setdefault(rec.task, []).append(rec)
    plan_tasks: list[TaskPlan] = []
    language = ""
    for task in sorted(by_task, key=tasks.task_order):
        recs = by_task[task]
        inputs: list[Path] = []
        for rec in recs:
            if rec.path is None or not rec.path.is_file():
                problems.append(tr("nahrávka {file} už není na disku").format(file=rec.file))
            else:
                inputs.append(rec.path)
        proto = None
        for rec in recs:
            try:
                proto = Protocol.load(rec.run.dir / "protocol.yaml")
                break
            except Exception:  # noqa: BLE001  chybějící nebo rozbitý soubor
                continue
        if proto is None:
            problems.append(
                tr("běh úlohy {task} nemá uložený protokol").format(task=tasks.label(task))
            )
            continue
        language = language or str(proto.config.get("transcript", {}).get("language") or "")
        plan_tasks.append(TaskPlan(task, proto.to_dict(), inputs))
    if not plan_tasks and not problems:
        problems.append(tr("hodnocení nemá žádnou nahrávku"))
    if problems:
        return None, problems
    patient = Patient(assessment.subject, assessment.age, assessment.sex, assessment.date)
    path = folder / f"{assessment.id}{PLAN_SUFFIX}"
    return AssessmentPlan(path, assessment.id, patient, language, plan_tasks), []


def finish_plan(plan: AssessmentPlan, store: AssessmentStore) -> list[Assessment]:
    """Hodnocení z tabulek hotových dávek; plán se smaže, hodnocení se uloží.

    Řádky se seskupí podle pacienta a data vyšetření ze sloupců tabulky
    (manifest dávky je tam zapsal). Jeden pacient a jedno datum = jedno
    hodnocení s id plánu jako dřív; víc pacientů nebo dat = hodnocení na každou
    dvojici, řádek bez data se vynechá."""
    groups: dict[tuple[str, str], list[RecordingRef]] = {}
    details: dict[tuple[str, str], tuple[float | None, str]] = {}
    for task in plan.tasks:
        table = task.table
        if table is None or task.run_dir is None:
            continue
        frame = pd.read_csv(table)
        for row, record in enumerate(frame.to_dict(orient="records")):
            file = str(record.get("file") or "")
            if not file or str(record.get("error") or "").strip() not in ("", "nan"):
                continue  # nahrávka, která spadla, do hodnocení nepatří
            if tasks.is_custom(task.task):
                task_name = task.task  # knihovna zapsala základní úlohu, hodnocení zná vlastní
            else:
                task_name = contract.canonical_task(str(record.get("task") or task.task))
            if plan.multi:
                subject = str(record.get(meta.SUBJECT) or "").strip()
                visit = normalize_visit(str(record.get(meta.VISIT) or ""))
                if subject in ("", "nan") or not visit:
                    continue  # bez pacienta nebo data: spočítané, ale žádné vyšetření
                group = (subject, visit)
                if group not in details:
                    sex = str(record.get(meta.SEX) or "")
                    details[group] = (
                        meta.parse_age(record.get(meta.AGE)),
                        meta.parse_sex(sex) if sex not in ("", "nan") else "",
                    )
            else:
                group = (plan.patient.subject, plan.patient.date)
            groups.setdefault(group, []).append(
                RecordingRef(task.run_dir.name, row, file, task_name)
            )
    if not groups:
        raise ValueError(tr("Žádná dávka hodnocení nemá tabulku výsledků."))
    out: list[Assessment] = []
    for (subject, visit), refs in groups.items():
        if plan.multi:
            age, sex = details[(subject, visit)]
            assessment = Assessment(new_id(), subject, age, sex, visit, refs)
        else:
            p = plan.patient
            assessment = Assessment(plan.id, p.subject, p.age, p.sex, p.date, refs)
        store.save(assessment)
        out.append(assessment)
    plan.discard()
    return out


# --- nahrávky ze složek -------------------------------------------------------------


def split_by_task(
    folder: Path, default_task: str, *, recursive: bool = False
) -> dict[str, FolderScan]:
    """Nahrávky složky po úlohách: podle `manifest.csv` (sloupec task), jinak všechny
    do `default_task`. Každá úloha dostane vlastní FolderScan se stejným manifestem."""
    scan = scan_folder(folder, recursive=recursive)
    groups: dict[str, list[Path]] = {}
    for rec in scan.recordings:
        task = default_task
        if scan.manifest is not None:
            task = scan.manifest.tasks.get(_key(rec), "") or default_task
        groups.setdefault(task, []).append(rec)
    return {
        task: FolderScan(recs, scan.manifest, {r: scan.durations.get(r) for r in recs})
        for task, recs in groups.items()
    }


def patient_from_scan(scan: FolderScan) -> Patient:
    """Pacient, věk, pohlaví a datum z manifestu složky (první řádek, který je má)."""
    patient = Patient()
    if scan.manifest is None:
        return patient
    for rec in scan.recordings:
        row = scan.manifest.meta_for(rec) or {}
        if not patient.subject:
            patient.subject = row.get(meta.SUBJECT, "").strip()
        if patient.age is None:
            patient.age = meta.parse_age(row.get(meta.AGE, ""))
        if not patient.sex:
            patient.sex = meta.parse_sex(row.get(meta.SEX, ""))
        if not patient.date:
            visit = row.get(meta.VISIT, "").strip()
            try:
                patient.date = dt.date.fromisoformat(visit[:10]).isoformat() if visit else ""
            except ValueError:
                patient.date = ""
    return patient
