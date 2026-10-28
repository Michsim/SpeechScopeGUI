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

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .. import contract
from ..i18n import tr
from . import meta
from .assessments import Assessment, AssessmentStore, RecordingRef, new_id
from .manifest import Manifest
from .norm_build import PENDING, FolderScan, PlanState, TaskPlan, scan_folder

PLAN_SUFFIX = ".plan.json"
PLAN_VERSION = 1


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

    @property
    def key(self) -> Path:
        return self.path

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
        return tr("Hodnocení {subject} · {task}").format(
            subject=self.patient.subject, task=contract.TASK_SHORT.get(task.task, task.task)
        )

    def manifest_for(self, task: TaskPlan) -> Manifest:
        """Manifest dávky: pacient, věk, pohlaví a datum pro každou nahrávku."""
        manifest = Manifest(path=self.path.with_name(f"{self.id}-{task.task}.csv"))
        manifest.columns = [meta.SUBJECT, meta.AGE, meta.SEX, meta.VISIT]
        p = self.patient
        for rec in task.inputs:
            manifest.rows[_key(rec)] = {
                meta.SUBJECT: p.subject,
                meta.AGE: "" if p.age is None else f"{p.age:g}",
                meta.SEX: p.sex,
                meta.VISIT: p.date,
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
                    "protocol": t.protocol,
                    "inputs": [str(x) for x in t.inputs],
                    "status": t.status,
                    "run_dir": str(t.run_dir) if t.run_dir else "",
                }
                for t in self.tasks
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
            )
            for t in data.get("tasks", [])
        ]
        return cls(
            path=path,
            id=str(data.get("id") or path.name.removesuffix(PLAN_SUFFIX)),
            patient=patient,
            language=str(data.get("language") or ""),
            tasks=tasks,
            created=str(data.get("created") or ""),
        )

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


def _key(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def new_plan(
    folder: Path, patient: Patient, language: str, tasks: list[TaskPlan]
) -> AssessmentPlan:
    plan_id = new_id()
    return AssessmentPlan(folder / f"{plan_id}{PLAN_SUFFIX}", plan_id, patient, language, tasks)


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


def finish_plan(plan: AssessmentPlan, store: AssessmentStore) -> Assessment:
    """Hodnocení z tabulek hotových dávek; plán se smaže, hodnocení uloží."""
    refs: list[RecordingRef] = []
    for task in plan.tasks:
        table = task.table
        if table is None or task.run_dir is None:
            continue
        frame = pd.read_csv(table)
        for row, record in enumerate(frame.to_dict(orient="records")):
            file = str(record.get("file") or "")
            if not file or str(record.get("error") or "").strip() not in ("", "nan"):
                continue  # nahrávka, která spadla, do hodnocení nepatří
            task_name = contract.canonical_task(str(record.get("task") or task.task))
            refs.append(RecordingRef(task.run_dir.name, row, file, task_name))
    if not refs:
        raise ValueError(tr("Žádná dávka hodnocení nemá tabulku výsledků."))
    p = plan.patient
    assessment = Assessment(plan.id, p.subject, p.age, p.sex, p.date, refs)
    store.save(assessment)
    plan.discard()
    return assessment


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
