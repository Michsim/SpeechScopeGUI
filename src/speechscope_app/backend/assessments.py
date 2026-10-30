"""Uložená hodnocení: pacient, věk, pohlaví, datum a vybrané nahrávky po úlohách.

Hodnocení zakládá klinik ručně (stránka Pacienti, Nové hodnocení…), nic
se neskládá samo ze všech běhů. Nahrávka je odkaz na řádek `features.csv`
hotového běhu (název složky běhu, řádek, soubor a úloha); hodnoty se při
zobrazení čtou z indexu běhů (`visits.VisitIndex`). Hodnocení i pacienti
jsou od 30. 10. 2026 v katalogu SQLite (`catalog.Catalog`); starší JSONy
ve složce `assessments` se při otevření úložiště naimportují. Norma se volí
až při prohlížení.

Z hodnocení se pro výpočet složí `visits.Visit`, se kterým pracuje
`dimensions.evaluate` a celá pravá část stránky. Víc nahrávek jedné úlohy
se zprůměruje po feature (`dimensions.visit_values`).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import meta, tasks
from .protocol import Protocol
from .visits import Recording, Visit

if TYPE_CHECKING:
    from .catalog import Catalog

FORMAT_VERSION = 1


def patient_key(subject: str) -> str:
    """Pacient = stejné označení bez ohledu na mezery a velikost písmen (zatím jen řetězec)."""
    return " ".join(subject.split()).casefold()


@dataclass(slots=True)
class RecordingRef:
    """Odkaz na nahrávku: řádek features.csv v jednom běhu."""

    run: str  # klíč běhu (`RunInfo.key`: název složky, u vnořeného `<vyšetření>/<úloha>`)
    row: int
    file: str
    task: str

    @classmethod
    def of(cls, rec: Recording) -> RecordingRef:
        return cls(rec.run.key, rec.row, rec.file, rec.task)

    def matches(self, rec: Recording) -> bool:
        return rec.run.key == self.run and rec.row == self.row


@dataclass(slots=True)
class Assessment:
    id: str
    subject: str
    age: float | None = None
    sex: str = ""  # f | m | ""
    date: str = ""  # datum vyšetření, ISO
    recordings: list[RecordingRef] = field(default_factory=list)
    created: str = ""

    @property
    def tasks(self) -> list[str]:
        return sorted({r.task for r in self.recordings}, key=tasks.task_order)

    def to_dict(self) -> dict:
        return {
            "format": FORMAT_VERSION,
            "id": self.id,
            "subject": self.subject,
            "age": self.age,
            "sex": self.sex,
            "date": self.date,
            "created": self.created,
            "recordings": [
                {"run": r.run, "row": r.row, "file": r.file, "task": r.task}
                for r in self.recordings
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Assessment:
        if int(data.get("format", 1)) > FORMAT_VERSION:
            raise ValueError("hodnocení je z novější verze aplikace")
        return cls(
            id=str(data["id"]),
            subject=str(data.get("subject") or ""),
            age=meta.parse_age(data.get("age")),
            sex=meta.parse_sex(data.get("sex")) if data.get("sex") else "",
            date=str(data.get("date") or ""),
            created=str(data.get("created") or ""),
            recordings=[
                RecordingRef(str(r["run"]), int(r["row"]), str(r.get("file", "")), str(r["task"]))
                for r in data.get("recordings", [])
            ],
        )


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class AssessmentStore:
    """Hodnocení v katalogu; `folder` zůstává pro plány rozpracovaných hodnocení.

    Při otevření naimportuje starší JSONy ze složky (jednou, soubory se
    přesunou do `imported/`)."""

    def __init__(self, folder: Path, catalog: Catalog) -> None:
        self.folder = folder
        self.catalog = catalog
        catalog.import_assessments(folder)

    def all(self) -> list[Assessment]:
        """Od nejnovějšího vyšetření."""
        return self.catalog.assessments()

    def save(self, assessment: Assessment) -> Assessment:
        if not assessment.id:
            assessment.id = new_id()
        if not assessment.created:
            assessment.created = dt.datetime.now().isoformat(timespec="seconds")
        self.catalog.save_assessment(assessment)
        return assessment

    def delete(self, assessment: Assessment) -> None:
        self.catalog.delete_assessment(assessment.id)

    def relocate(self, old_run: str, new_run: str) -> int:
        """Po přejmenování složky běhu přepíše odkazy; vrátí počet změněných hodnocení."""
        return self.catalog.relocate(old_run, new_run)


def resolve(
    assessment: Assessment, recordings: list[Recording]
) -> tuple[Visit, list[RecordingRef]]:
    """Vyšetření pro výpočet a seznam odkazů, které už v bězích nejsou.

    Řádek se hledá podle složky běhu a čísla řádku; když se tabulka mezitím
    změnila (Spočítat znovu chybné), záložně podle souboru a úlohy v témž běhu.
    """
    visit = Visit(
        assessment.subject,
        assessment.date,
        True,
        age=assessment.age,
        sex=assessment.sex,
    )
    missing: list[RecordingRef] = []
    by_run: dict[str, list[Recording]] = {}
    for rec in recordings:
        by_run.setdefault(rec.run.key, []).append(rec)
    for ref in assessment.recordings:
        candidates = by_run.get(ref.run, [])
        found = next((r for r in candidates if ref.matches(r) and r.file == ref.file), None)
        if found is None:
            found = next((r for r in candidates if r.file == ref.file and r.task == ref.task), None)
        if found is None:
            missing.append(ref)
        else:
            visit.recordings.append(found)
    return visit, missing


def prefill(rec: Recording) -> tuple[str, float | None, str, str]:
    """Pacient, věk, pohlaví a datum z řádku nahrávky (manifest), jinak z běhu."""
    subject = str(rec.data.get(meta.SUBJECT) or "").strip()
    if subject.lower() == "nan":
        subject = ""
    age = meta.parse_age(rec.data.get(meta.AGE))
    sex = meta.parse_sex(rec.data.get(meta.SEX))
    visit = str(rec.data.get(meta.VISIT) or "").strip()
    date = ""
    if visit and visit.lower() != "nan":
        try:
            date = dt.date.fromisoformat(visit[:10]).isoformat()
        except ValueError:
            date = ""
    if not date and rec.run.started is not None:
        date = rec.run.started.date().isoformat()
    return subject, age, sex, date


def candidates_for(
    task: str, subject: str, recordings: list[Recording]
) -> tuple[list[Recording], list[Recording], list[Recording]]:
    """Nahrávky úlohy: podle manifestu pacienta, podle názvu souboru, ostatní.

    Každá skupina od nejnovějšího běhu; běhy norem v `recordings` nejsou
    (`VisitIndex` je přeskočí).
    """
    needle = subject.strip().lower()
    by_manifest: list[Recording] = []
    by_name: list[Recording] = []
    others: list[Recording] = []
    for rec in recordings:
        if rec.task != task:
            continue
        rec_subject = str(rec.data.get(meta.SUBJECT) or "").strip().lower()
        if needle and rec_subject == needle:
            by_manifest.append(rec)
        elif needle and needle in rec.file.lower():
            by_name.append(rec)
        else:
            others.append(rec)

    def newest(recs: list[Recording]) -> list[Recording]:
        return sorted(
            recs, key=lambda r: (r.run.started is not None, r.run.started, r.file), reverse=True
        )

    return newest(by_manifest), newest(by_name), newest(others)


_PROTOCOL_CACHE: dict[Path, str] = {}


def run_protocol_name(run_dir: Path) -> str:
    """Klíč protokolu běhu (`protocol.yaml`); prázdné, když chybí."""
    if run_dir not in _PROTOCOL_CACHE:
        try:
            _PROTOCOL_CACHE[run_dir] = Protocol.load(run_dir / "protocol.yaml").name
        except Exception:  # noqa: BLE001  chybějící nebo rozbitý soubor
            _PROTOCOL_CACHE[run_dir] = ""
    return _PROTOCOL_CACHE[run_dir]


def norm_match(rec: Recording, norm_protocols: dict[str, str]) -> bool | None:
    """Sedí protokol nahrávky s normou? None = norma protokoly nezná (starší, import)."""
    expected = norm_protocols.get(rec.task)
    if not expected:
        return None
    return run_protocol_name(rec.run.dir) == expected
