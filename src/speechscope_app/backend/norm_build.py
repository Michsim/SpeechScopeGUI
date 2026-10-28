"""Nová norma ze složek s nahrávkami: plán, dávky po úlohách a sestavení.

Průvodce Nová norma vyrobí `NormPlan`: jméno a popis normy, pro každou
úlohu protokol a nahrávky, a jednu tabulku kontrol (pacient, věk, pohlaví).
Plán se uloží jako `plan.json` do budoucí složky normy; dokud v ní není
`norm.yaml`, je norma rozpracovaná (stránka Normy ji ukáže jako „počítá se“).
Každá úloha je jedna běžná dávka ve frontě Výpočtu s manifestem z tabulky
kontrol, takže `features.csv` běhu má sloupce subject, age a sex. Po poslední
dávce `finish_plan` sestaví normu z tabulek běhů (`norms.build_norm`)
a zapíše k ní protokoly a verzi knihovny.

Do složek s nahrávkami se nic nezapisuje; manifest existuje jen v paměti
a jako normalizovaná kopie ve složce běhu.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .. import contract
from ..i18n import N_, Labels, tr
from . import meta
from .discover import find_recordings
from .manifest import Manifest, find_manifest, read_manifest
from .norms import NORM_FILE, Norm, NormInfo, build_norm
from .protocol import Protocol

PLAN_FILE = "plan.json"
PLAN_VERSION = 1

# Výchozí přibalený protokol úlohy pro normu; spontánní řeč i s lingvistikou,
# jinak by dimenze Jazyk neměla s čím srovnávat.
DEFAULT_PROTOCOLS: dict[str, str] = {
    "phonation": "Fonace, základní",
    "ddk": "DDK, základní",
    "reading": "Čtení, akustika",
    "spontaneous": "Spontánní řeč, akustika i lingvistika",
}

# Jak z názvu souboru poznat kontrolu (RBD239_6Ra.wav)
SUBJECT_RULES = Labels(
    {
        "prefix": N_("text před prvním „_“"),
        "stem": N_("celý název bez přípony"),
        "letters": N_("text před první číslicí za písmeny"),
    }
)

# stav úlohy v plánu
PENDING, RUNNING, OK, ERROR, CANCELLED = "pending", "running", "ok", "error", "cancelled"


def subject_from_name(path: Path, rule: str) -> str:
    stem = path.stem
    if rule == "stem":
        return stem
    if rule == "letters":
        match = re.match(r"[^\d]*\d+", stem)  # RBD239_6Ra -> RBD239
        return match.group(0) if match else stem
    return stem.split("_", 1)[0] or stem


def default_protocol(task: str, protocols: list[Protocol]) -> Protocol | None:
    """Přibalený protokol úlohy pro normu (podle DEFAULT_PROTOCOLS), jinak první přibalený."""
    builtin = [p for p in protocols if p.builtin and p.task == task]
    wanted = DEFAULT_PROTOCOLS.get(task)
    for proto in builtin:
        if proto.name == wanted:
            return proto
    return builtin[0] if builtin else None


def with_language(proto: Protocol, language: str) -> Protocol:
    """Kopie protokolu s jazykem nahrávek pro přepis a lingvistiku (jako na Analýze)."""
    copy = Protocol.from_dict(proto.to_dict())
    if language:
        for provider in ("transcript", "nlp"):
            copy.config.setdefault(provider, {})["language"] = language
    return copy


@dataclass(slots=True)
class Control:
    subject: str
    age: float | None = None
    sex: str = ""  # f | m | ""


@dataclass(slots=True)
class TaskPlan:
    task: str
    protocol: dict  # Protocol.to_dict() s jazykem; norma pak ví, čím přesně se počítalo
    inputs: list[Path]
    status: str = PENDING
    run_dir: Path | None = None

    def proto(self) -> Protocol:
        return Protocol.from_dict(self.protocol)

    @property
    def finished(self) -> bool:
        return self.status not in (PENDING, RUNNING)

    @property
    def table(self) -> Path | None:
        if self.run_dir is None:
            return None
        out = self.run_dir / "features.csv"
        return out if out.is_file() else None


@dataclass(slots=True)
class NormPlan:
    info: NormInfo
    folder: Path
    tasks: list[TaskPlan]
    files: dict[Path, str] = field(default_factory=dict)  # nahrávka -> kontrola
    controls: dict[str, Control] = field(default_factory=dict)

    # --- stav ---------------------------------------------------------------

    def task(self, task: str) -> TaskPlan | None:
        return next((t for t in self.tasks if t.task == task), None)

    @property
    def done(self) -> int:
        return sum(1 for t in self.tasks if t.finished)

    @property
    def all_finished(self) -> bool:
        return all(t.finished for t in self.tasks)

    @property
    def all_ok(self) -> bool:
        return all(t.status == OK for t in self.tasks)

    @property
    def buildable(self) -> bool:
        """Aspoň jedna dávka má tabulku: jde sestavit z hotových."""
        return any(t.table is not None for t in self.tasks)

    def remaining(self) -> list[TaskPlan]:
        """Úlohy k dopočítání: nespuštěné, spadlé nebo zrušené (ne právě běžící)."""
        return [t for t in self.tasks if t.status in (PENDING, ERROR, CANCELLED)]

    def manifest_for(self, task: TaskPlan) -> Manifest:
        """Manifest dávky: subject, age, sex z tabulky kontrol pro každou nahrávku."""
        manifest = Manifest(path=self.folder / f"manifest-{task.task}.csv")
        manifest.columns = [meta.SUBJECT, meta.AGE, meta.SEX]
        for rec in task.inputs:
            subject = self.files.get(rec, "")
            control = self.controls.get(subject, Control(subject))
            manifest.rows[_key(rec)] = {
                meta.SUBJECT: subject,
                meta.AGE: "" if control.age is None else f"{control.age:g}",
                meta.SEX: control.sex,
            }
        return manifest

    # --- soubor -------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "format": PLAN_VERSION,
            "info": self.info.to_dict(),
            "tasks": [
                {
                    "task": t.task,
                    "protocol": t.protocol,
                    "inputs": [str(p) for p in t.inputs],
                    "status": t.status,
                    "run_dir": str(t.run_dir) if t.run_dir else "",
                }
                for t in self.tasks
            ],
            "files": {str(p): s for p, s in self.files.items()},
            "controls": [
                {"subject": c.subject, "age": c.age, "sex": c.sex} for c in self.controls.values()
            ],
        }

    def save(self) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / PLAN_FILE
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, folder: Path) -> NormPlan:
        data = json.loads((folder / PLAN_FILE).read_text(encoding="utf-8"))
        if int(data.get("format", 1)) > PLAN_VERSION:
            raise ValueError("plán normy je z novější verze aplikace")
        tasks = [
            TaskPlan(
                task=str(t["task"]),
                protocol=dict(t["protocol"]),
                inputs=[Path(p) for p in t.get("inputs", [])],
                status=str(t.get("status", PENDING)),
                run_dir=Path(t["run_dir"]) if t.get("run_dir") else None,
            )
            for t in data.get("tasks", [])
        ]
        controls = {
            str(c["subject"]): Control(
                str(c["subject"]),
                float(c["age"]) if c.get("age") is not None else None,
                str(c.get("sex") or ""),
            )
            for c in data.get("controls", [])
        }
        files = {Path(p): str(s) for p, s in data.get("files", {}).items()}
        return cls(NormInfo.from_dict(data["info"]), folder, tasks, files, controls)

    def set_status(self, task: str, status: str, run_dir: Path | None = None) -> None:
        item = self.task(task)
        if item is None:
            return
        item.status = status
        if run_dir is not None:
            item.run_dir = run_dir
        self.save()


def _key(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def pending_plans(folder: Path) -> list[NormPlan]:
    """Rozpracované normy: složky s plánem, ale ještě bez norm.yaml."""
    if not folder.is_dir():
        return []
    out: list[NormPlan] = []
    for path in sorted(folder.iterdir()):
        if path.is_dir() and (path / PLAN_FILE).is_file() and not (path / NORM_FILE).is_file():
            try:
                out.append(NormPlan.load(path))
            except (ValueError, KeyError, OSError, json.JSONDecodeError):
                continue  # rozbitý plán nesmí shodit stránku
    return out


def finish_plan(plan: NormPlan, library_version: str = "") -> Norm:
    """Norma z tabulek hotových dávek plánu; uloží se do složky plánu."""
    frames = []
    protocols: dict[str, str] = {}
    for task in plan.tasks:
        table = task.table
        if table is None:
            continue
        frames.append(pd.read_csv(table))
        protocols[task.task] = task.proto().name
    if not frames:
        raise ValueError(tr("Žádná dávka normy nemá tabulku výsledků."))
    plan.info.protocols = protocols
    if library_version:
        plan.info.library_version = library_version
    norm = build_norm(frames, plan.info)
    norm.save(plan.folder)
    return norm


# --- kontroly z nahrávek ---------------------------------------------------------


@dataclass(slots=True)
class FolderScan:
    """Nahrávky jedné složky a metadata z jejího manifest.csv, pokud tam je."""

    recordings: list[Path]
    manifest: Manifest | None = None
    durations: dict[Path, float | None] = field(default_factory=dict)  # sekundy z hlavičky

    @property
    def audio_seconds(self) -> float:
        return sum(d for d in self.durations.values() if d)


def scan_folder(folder: Path, *, recursive: bool = False) -> FolderScan:
    if not folder.is_dir():
        return FolderScan([])
    found_recordings = find_recordings(folder, recursive=recursive)
    found = find_manifest(folder)
    manifest = read_manifest(found) if found is not None else None
    return FolderScan(
        [r.path for r in found_recordings],
        manifest,
        {r.path: r.duration for r in found_recordings},
    )


def guess_controls(
    scans: dict[str, FolderScan], rule: str, known: dict[str, Control] | None = None
) -> tuple[dict[Path, str], dict[str, Control]]:
    """Kontrola každé nahrávky a tabulka kontrol.

    Pacient, věk a pohlaví z manifestu složky mají přednost před pravidlem
    z názvu souboru; ručně vyplněné hodnoty (`known`) se zachovají.
    """
    files: dict[Path, str] = {}
    controls: dict[str, Control] = {}
    known = known or {}
    for scan in scans.values():
        for rec in scan.recordings:
            row = scan.manifest.meta_for(rec) if scan.manifest else None
            subject = (row or {}).get(meta.SUBJECT, "").strip() or subject_from_name(rec, rule)
            files[rec] = subject
            control = controls.get(subject) or known.get(subject) or Control(subject)
            if row:
                control.age = (
                    control.age
                    if control.age is not None
                    else meta.parse_age(row.get(meta.AGE, ""))
                )
                control.sex = control.sex or meta.parse_sex(row.get(meta.SEX, ""))
            controls[subject] = control
    return files, dict(sorted(controls.items()))


def read_controls_csv(path: Path) -> dict[str, Control]:
    """Věk a pohlaví kontrol z CSV (subject, age, sex; i české názvy sloupců)."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1250")
    sample = text[:4096]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    columns = {meta.canonical_column(n.strip()): n for n in (reader.fieldnames or []) if n}
    if meta.SUBJECT not in columns:
        raise ValueError(
            tr("CSV nemá sloupec s pacientem (subject, id, pacient…); sloupce: {columns}").format(
                columns=", ".join(columns.values()) or "–"
            )
        )
    out: dict[str, Control] = {}
    for raw in reader:
        subject = (raw.get(columns[meta.SUBJECT]) or "").strip()
        if not subject:
            continue
        age = meta.parse_age(raw.get(columns[meta.AGE], "")) if meta.AGE in columns else None
        sex = meta.parse_sex(raw.get(columns[meta.SEX], "")) if meta.SEX in columns else ""
        out[subject] = Control(subject, age, sex)
    return out


def task_label(task: str) -> str:
    return contract.TASK_SHORT.get(task, task)
