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

Plán zůstává ve složce normy i po sestavení (do exportu nejde), takže normu
jde po změně knihovny spočítat znovu: `reset_for_recompute` vrátí dávky do
stavu „čeká“ a nastaví `recomputing`; dokud platí, je norma na stránce Normy
zase rozpracovaná (`pending_plans`), stará `norm.yaml` ale zůstává použitelná
a `finish_plan` ji po poslední dávce přepíše. `cancel_recompute` přepočet
zruší a starou normu nechá.

Norma z hotových běhů (`plan_from_runs`) je týž plán: každý zdrojový běh je
dávka ve stavu „hotovo“ se svou složkou běhu (víc běhů jedné úlohy = víc dávek,
`TaskPlan.part`), takže sestavení, přepočet i Přidat kontroly fungují stejně.
Do normy jdou jen nahrávky z `NormPlan.files` (filtr skupiny, nahrazené
kontroly) a věk a pohlaví z tabulky kontrol přebijí metadata v tabulkách běhů.
Přidané kontroly (`extend_plan`) jsou dávky s `added`; nahrávky, které
nahrazují (`replaces`), vypadnou z normy, až má jejich dávka tabulku.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..i18n import N_, Labels, tr
from . import meta, tasks
from .discover import find_recordings
from .history import RunInfo, read_run
from .manifest import Manifest, find_manifest, read_manifest
from .norms import NORM_FILE, Norm, NormInfo, build_norm
from .protocol import Protocol

logger = logging.getLogger(__name__)

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
    visit: str = ""  # datum vyšetření (ISO) u hodnocení víc pacientů: dávka na úlohu a datum
    part: int = 0  # další dávka téže úlohy v normě (norma z víc běhů, přidané kontroly)
    added: bool = False  # přidané kontroly, které ještě nejsou v normě
    replaces: list[Path] = field(default_factory=list)  # nahrávky normy, které přidané nahradí

    @property
    def key(self) -> str:
        """Klíč dávky v plánu a ve frontě: úloha, u dávky s datem `úloha@datum`,
        u další dávky téže úlohy v normě `úloha#n`."""
        key = f"{self.task}@{self.visit}" if self.visit else self.task
        return f"{key}#{self.part}" if self.part else key

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


def task_of_key(key: str) -> str:
    """Úloha z klíče dávky (`úloha@datum#n` → `úloha`)."""
    return key.split("#", 1)[0].split("@", 1)[0]


class PlanState:
    """Stav dávek plánu (norma i hodnocení): společné pro NormPlan a AssessmentPlan.

    Potomek má `tasks: list[TaskPlan]`, `save()` a `key` (cesta, podle které
    hlavní okno plán pozná ve frontě), `title(task)` je název dávky.
    """

    tasks: list[TaskPlan]

    def save(self) -> Path:  # pragma: no cover - přepisuje potomek
        raise NotImplementedError

    def task(self, key: str) -> TaskPlan | None:
        """Dávka podle klíče (`TaskPlan.key`); samotná úloha najde dávku bez data."""
        return next((t for t in self.tasks if t.key == key), None)

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
    def complete(self) -> bool:
        """Po poslední dávce se plán sestaví sám (norma, hodnocení)."""
        return self.all_finished and self.all_ok

    @property
    def buildable(self) -> bool:
        """Aspoň jedna dávka má tabulku: jde sestavit z hotových."""
        return any(t.table is not None for t in self.tasks)

    def remaining(self) -> list[TaskPlan]:
        """Úlohy k dopočítání: nespuštěné, spadlé nebo zrušené (ne právě běžící)."""
        return [t for t in self.tasks if t.status in (PENDING, ERROR, CANCELLED)]

    def set_status(self, key: str, status: str, run_dir: Path | None = None) -> None:
        item = self.task(key)
        if item is None:
            return
        item.status = status
        if run_dir is not None:
            item.run_dir = run_dir
        self.save()


@dataclass(slots=True)
class NormPlan(PlanState):
    info: NormInfo
    folder: Path
    tasks: list[TaskPlan]
    files: dict[Path, str] = field(default_factory=dict)  # nahrávka -> kontrola
    controls: dict[str, Control] = field(default_factory=dict)
    recomputing: bool = False  # norma už existuje a počítá se znovu (norm.yaml se přepíše)

    @property
    def key(self) -> Path:
        return self.folder

    def missing_inputs(self) -> list[Path]:
        """Nahrávky plánu, které už na disku nejsou (bez nich přepočet nejde)."""
        return [p for t in self.tasks for p in t.inputs if not p.is_file()]

    def reset_for_recompute(self) -> None:
        """Všechny dávky znovu od začátku; staré běhy se do normy už nepočítají."""
        for task in self.tasks:
            task.status, task.run_dir = PENDING, None
        self.recomputing = True
        self.save()

    def cancel_recompute(self) -> None:
        """Přepočet zrušit: nespuštěné dávky jako zrušené, stará norma zůstává.

        Přidané kontroly, které se ještě nesloučily do normy, z plánu zmizí."""
        dropped = [t for t in self.tasks if t.added]
        self.tasks = [t for t in self.tasks if not t.added]
        kept = {p for t in self.tasks for p in t.inputs}
        for task in dropped:
            for path in task.inputs:
                if path not in kept:
                    self.files.pop(path, None)
        for task in self.tasks:
            if task.status in (PENDING, RUNNING):
                task.status = CANCELLED
        self.recomputing = False
        self.save()

    @property
    def extending(self) -> bool:
        """Přidávají se kontroly (dávky s `added`), ne přepočet celé normy."""
        return any(t.added for t in self.tasks)

    @property
    def complete(self) -> bool:
        """Sestavit sám: vše hotové bez chyby; u přidaných kontrol stačí, že bez chyby
        doběhly přidané dávky (starší spadlá dávka normy přidání neblokuje)."""
        if not self.all_finished:
            return False
        if self.extending:
            return all(t.status == OK for t in self.tasks if t.added)
        return self.all_ok

    def existing_pairs(self) -> dict[tuple[str, str], list[Path]]:
        """(kontrola, úloha) → nahrávky, které už v normě jsou (bez přidaných)."""
        out: dict[tuple[str, str], list[Path]] = {}
        for task in self.tasks:
            if task.added:
                continue
            for path in task.inputs:
                subject = self.files.get(path)
                if subject:
                    out.setdefault((subject, task.task), []).append(path)
        return out

    def protocol_for(self, task: str) -> dict | None:
        """Protokol úlohy z poslední dávky normy (Přidat kontroly počítá týmž)."""
        found = [t.protocol for t in self.tasks if t.task == task]
        return found[-1] if found else None

    @property
    def language(self) -> str:
        return self.info.language

    def title(self, task: TaskPlan) -> str:
        return tr("Norma {name} · {task}").format(
            name=self.info.display_name, task=tasks.label(task.task)
        )

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
                    "part": t.part,
                    "added": t.added,
                    "replaces": [str(p) for p in t.replaces],
                }
                for t in self.tasks
            ],
            "files": {str(p): s for p, s in self.files.items()},
            "controls": [
                {"subject": c.subject, "age": c.age, "sex": c.sex} for c in self.controls.values()
            ],
            "recomputing": self.recomputing,
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
                part=int(t.get("part") or 0),
                added=bool(t.get("added")),
                replaces=[Path(p) for p in t.get("replaces", [])],
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
        return cls(
            NormInfo.from_dict(data["info"]),
            folder,
            tasks,
            files,
            controls,
            recomputing=bool(data.get("recomputing")),
        )


def _key(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def load_plan(folder: Path) -> NormPlan | None:
    """Plán ve složce normy; None, když tam není nebo je rozbitý."""
    if not (folder / PLAN_FILE).is_file():
        return None
    try:
        return NormPlan.load(folder)
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
        return None  # rozbitý plán nesmí shodit stránku


def pending_plans(folder: Path) -> list[NormPlan]:
    """Rozpracované normy: plán bez norm.yaml, nebo norma, která se počítá znovu."""
    if not folder.is_dir():
        return []
    out: list[NormPlan] = []
    for path in sorted(folder.iterdir()):
        if not path.is_dir():
            continue
        plan = load_plan(path)
        if plan is not None and (plan.recomputing or not (path / NORM_FILE).is_file()):
            out.append(plan)
    return out


def _select_rows(
    frame: pd.DataFrame,
    files: dict[Path, str],
    known: set[Path],
    controls: dict[str, Control],
) -> pd.DataFrame:
    """Jen řádky nahrávek normy (`files` podle sloupce `path`), pacient z plánu, věk
    a pohlaví z tabulky kontrol. Tabulka bez cest nebo bez jediné nahrávky plánu
    (`known`, i nahrazené; starší běh s jinými cestami) zůstane celá.

    Přidá sloupec `_key` (cesta nahrávky) kvůli nahrávce ve dvou dávkách."""
    frame = frame.rename(columns={c: meta.canonical_column(str(c)) for c in frame.columns})
    raw = frame["path"] if "path" in frame else pd.Series([None] * len(frame))
    keys = [_key(Path(p)) if isinstance(p, str) and p.strip() else None for p in raw]
    frame["_key"] = [str(k) if k is not None else f"#{id(frame)}-{i}" for i, k in enumerate(keys)]
    if not known or not any(k in known for k in keys if k is not None):
        return frame
    mask = [k is not None and k in files for k in keys]
    frame = frame[mask].copy()
    subjects = [files[k] for k, keep in zip(keys, mask, strict=True) if k is not None and keep]
    old_age = list(frame[meta.AGE]) if meta.AGE in frame else [None] * len(frame)
    old_sex = list(frame[meta.SEX]) if meta.SEX in frame else [""] * len(frame)
    ages, sexes = [], []
    for subject, age, sex in zip(subjects, old_age, old_sex, strict=True):
        control = controls.get(subject)
        ages.append(control.age if control is not None and control.age is not None else age)
        sexes.append(control.sex if control is not None and control.sex else sex)
    frame[meta.SUBJECT] = subjects
    frame[meta.AGE] = ages
    frame[meta.SEX] = sexes
    return frame


def finish_plan(plan: NormPlan, library_version: str = "") -> Norm:
    """Norma z tabulek hotových dávek plánu; uloží se do složky plánu."""
    frames = []
    protocols: dict[str, str] = {}
    versions: dict[str, dict[str, str]] = {}
    merged_added = [t for t in plan.tasks if t.added and t.table is not None]
    replaced = {_key(p) for t in merged_added for p in t.replaces}
    known = {_key(p) for p in plan.files}
    files = {k: s for p, s in plan.files.items() if (k := _key(p)) not in replaced}
    for task in plan.tasks:
        table = task.table
        if table is None:
            continue
        frame = _select_rows(pd.read_csv(table), files, known, plan.controls)
        if tasks.is_custom(task.task):
            frame["task"] = task.task  # knihovna zapsala základní úlohu, norma zná vlastní
        frames.append(frame)
        protocols[task.task] = task.proto().name
        run = read_run(task.run_dir) if task.run_dir is not None else None
        if run is not None and run.feature_versions:
            versions[task.task] = dict(run.feature_versions)  # čím přesně se kontroly počítaly
            library_version = library_version or run.library_version
    if not frames:
        raise ValueError(tr("Žádná dávka normy nemá tabulku výsledků."))
    # táž nahrávka ve dvou dávkách (dva běhy, přepočet): platí pozdější
    merged = pd.concat(frames, ignore_index=True, sort=False)
    merged = merged.drop_duplicates(["_key", "task"], keep="last").drop(columns="_key")
    frames = [merged]
    plan.info.protocols = protocols
    plan.info.feature_versions = versions
    plan.info.created = dt.date.today().isoformat()  # kdy se kontroly (na)počítaly
    if library_version:
        plan.info.library_version = library_version
    norm = build_norm(frames, plan.info)
    if (plan.folder / NORM_FILE).is_file():  # přepočet: ručně vyřazené nahrávky zůstávají
        try:
            previous = Norm.load(plan.folder)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("%s: previous norm unreadable (%s)", plan.folder, exc)
        else:
            present = set(
                zip(
                    norm.controls[meta.SUBJECT].astype(str),
                    norm.controls["task"].astype(str),
                    strict=True,
                )
            )
            norm.set_excluded([e for e in previous.info.excluded if e.key in present])
    norm.save(plan.folder)
    changed = _settle_added(plan, merged_added)
    if plan.recomputing:
        plan.recomputing = False  # nová norma je zapsaná, přepočet skončil
        changed = True
    if changed:
        plan.save()
    return norm


def _settle_added(plan: NormPlan, merged: list[TaskPlan]) -> bool:
    """Přidané kontroly jsou v normě: nahrazené nahrávky pryč z plánu, `added` zrušit."""
    if not merged:
        return False
    replaced = {_key(p) for t in merged for p in t.replaces}
    for task in merged:
        task.added, task.replaces = False, []
    for task in plan.tasks:
        task.inputs = [p for p in task.inputs if _key(p) not in replaced]
    plan.files = {p: s for p, s in plan.files.items() if _key(p) not in replaced}
    # dávka, které nahrazení vzalo všechny nahrávky, už do normy nic nedává
    plan.tasks = [t for t in plan.tasks if t.inputs or t.added]
    return True


# --- norma z hotových běhů a přidané kontroly ------------------------------------


@dataclass(slots=True)
class RunRow:
    """Řádek tabulky běhu, který může jít do normy."""

    path: Path
    task: str
    subject: str
    age: float | None
    sex: str
    run_dir: Path
    from_name: bool = False  # běh pacienta v metadatech nemá, kontrola je z názvu souboru


@dataclass(slots=True)
class RunsScan:
    rows: list[RunRow] = field(default_factory=list)
    no_subject: int = 0  # řádky bez cesty k nahrávce (nejde je přiřadit ani přepočítat)

    @property
    def from_name(self) -> int:
        """Kolik nahrávek má kontrolu z názvu souboru (běh bez sloupce pacienta)."""
        return sum(1 for r in self.rows if r.from_name)

    outside: int = 0  # řádky mimo vybranou skupinu
    errors: int = 0  # chybné řádky (knihovna vrátila chybu)

    def by_task(self) -> dict[str, list[RunRow]]:
        out: dict[str, list[RunRow]] = {}
        for row in self.rows:
            out.setdefault(row.task, []).append(row)
        return out


def _read_table(run: RunInfo) -> pd.DataFrame | None:
    try:
        frame = pd.read_csv(run.dir / "features.csv")
    except (OSError, ValueError):
        return None
    return frame.rename(columns={c: meta.canonical_column(str(c)) for c in frame.columns})


def run_columns(runs: list[RunInfo]) -> dict[str, list[str]]:
    """Sloupce metadat v tabulkách běhů a jejich hodnoty (filtr skupiny v průvodci)."""
    skip = {"file", "path", "task", "error", "speechscope_version", meta.SUBJECT, meta.AGE}
    out: dict[str, set[str]] = {}
    for run in runs:
        frame = _read_table(run)
        if frame is None:
            continue
        for column in frame.columns:
            if column in skip or "." in str(column):
                continue  # feature mají v názvu tečku
            values = {str(v).strip() for v in frame[column].dropna() if str(v).strip()}
            if values and len(values) <= 50:
                out.setdefault(str(column), set()).update(values)
    return {k: sorted(v) for k, v in sorted(out.items())}


def scan_runs(
    runs_by_task: dict[str, list[RunInfo]],
    group_column: str = "",
    group_value: str = "",
    rule: str = "prefix",
) -> RunsScan:
    """Řádky vybraných běhů po úlohách; táž nahrávka ve dvou bězích úlohy = novější běh.

    Řádek bez pacienta v metadatech (analýza bez manifestu) dostane kontrolu z názvu
    souboru podle `rule` (`SUBJECT_RULES`), jako u zdroje ze složek."""
    scan = RunsScan()
    column = meta.canonical_column(group_column) if group_column else ""
    for task, runs in runs_by_task.items():
        chosen: dict[Path, RunRow] = {}
        ordered = sorted(runs, key=lambda r: (r.started is None, r.started or dt.datetime.min))
        for run in ordered:
            frame = _read_table(run)
            if frame is None:
                continue
            for row in frame.to_dict(orient="records"):
                error = row.get("error")
                if isinstance(error, str) and error.strip():
                    scan.errors += 1
                    continue
                if column and str(row.get(column, "")).strip() != group_value.strip():
                    scan.outside += 1
                    continue
                path_text = row.get("path")
                if not isinstance(path_text, str) or not path_text.strip():
                    scan.no_subject += 1
                    continue
                path = Path(path_text)
                subject = row.get(meta.SUBJECT)
                subject = "" if subject is None or pd.isna(subject) else str(subject).strip()
                from_name = not subject
                if from_name:
                    subject = subject_from_name(path, rule)
                chosen[_key(path)] = RunRow(
                    path,
                    task,
                    subject,
                    meta.parse_age(row.get(meta.AGE, "")),
                    meta.parse_sex(row.get(meta.SEX, "")),
                    run.dir,
                    from_name,
                )
        scan.rows.extend(chosen.values())
    return scan


def controls_from_scan(
    scan: RunsScan, known: dict[str, Control] | None = None
) -> dict[str, Control]:
    """Tabulka kontrol z metadat běhů; ručně doplněné hodnoty (`known`) mají přednost."""
    known = known or {}
    out: dict[str, Control] = {}
    for row in scan.rows:
        control = out.get(row.subject) or known.get(row.subject) or Control(row.subject)
        if control.age is None:
            control.age = row.age
        control.sex = control.sex or row.sex
        out[row.subject] = control
    return dict(sorted(out.items()))


def runs_problems(runs_by_task: dict[str, list[RunInfo]]) -> list[str]:
    """Běhy jedné úlohy s jiným protokolem nebo jinou verzí feature: varování, ne zákaz."""
    problems: list[str] = []
    for task, runs in runs_by_task.items():
        names = {r.protocol_name for r in runs if r.protocol_name}
        if len(names) > 1:
            problems.append(
                tr(
                    "{task}: běhy mají různé protokoly ({names}), hodnoty nemusí být srovnatelné."
                ).format(task=tasks.label(task), names=", ".join(sorted(names)))
            )
        changed: set[str] = set()
        seen: dict[str, str] = {}
        for run in runs:
            for feature, version in run.feature_versions.items():
                if feature in seen and seen[feature] != version:
                    changed.add(feature)
                seen.setdefault(feature, version)
        if changed:
            problems.append(
                tr("{task}: běhy počítaly jinou verzí feature: {features}.").format(
                    task=tasks.label(task), features=", ".join(sorted(changed))
                )
            )
    return problems


def tasks_from_runs(
    runs_by_task: dict[str, list[RunInfo]], scan: RunsScan
) -> tuple[list[TaskPlan], list[str]]:
    """Hotové dávky z běhů (jedna na běh s řádky ve výběru); běh bez protokolu = problém."""
    rows_by_run: dict[Path, list[RunRow]] = {}
    for row in scan.rows:
        rows_by_run.setdefault(row.run_dir, []).append(row)
    out: list[TaskPlan] = []
    problems: list[str] = []
    for task, runs in runs_by_task.items():
        part = 0
        for run in runs:
            rows = rows_by_run.get(run.dir)
            if not rows:
                continue
            try:
                proto = Protocol.load(run.dir / "protocol.yaml")
            except Exception:  # noqa: BLE001  chybějící nebo rozbitý soubor
                problems.append(
                    tr("Běh {run} nemá uložený protokol, vynechá se.").format(run=run.display_name)
                )
                continue
            out.append(
                TaskPlan(
                    task,
                    proto.to_dict(),
                    [r.path for r in rows],
                    status=OK,
                    run_dir=run.dir,
                    part=part,
                )
            )
            part += 1
    return out, problems


def plan_from_runs(
    info: NormInfo,
    folder: Path,
    runs_by_task: dict[str, list[RunInfo]],
    scan: RunsScan,
    controls: dict[str, Control],
) -> tuple[NormPlan, list[str]]:
    """Plán normy z hotových běhů: všechny dávky hotové, sestaví se hned (`finish_plan`)."""
    plan_tasks, problems = tasks_from_runs(runs_by_task, scan)
    used = {p for t in plan_tasks for p in t.inputs}
    files = {r.path: r.subject for r in scan.rows if r.path in used}
    subjects = set(files.values())
    plan = NormPlan(
        info=info,
        folder=folder,
        tasks=plan_tasks,
        files=files,
        controls={s: c for s, c in controls.items() if s in subjects},
    )
    return plan, problems


@dataclass(slots=True)
class Extension:
    """Co Přidat kontroly přidá: dávky, kontrola každé nahrávky a tabulka kontrol."""

    tasks: list[TaskPlan]
    files: dict[Path, str]
    controls: dict[str, Control]


def extend_plan(plan: NormPlan, ext: Extension, *, replace: bool) -> tuple[int, int]:
    """Přidá kontroly do plánu normy; vrátí (přidané nahrávky, přeskočené).

    Nahrávka, která v normě už je, se přeskočí vždy. Kontrola, která danou úlohu
    v normě už má, se podle `replace` přeskočí, nebo její nahrávky nahradí nové
    (staré z normy vypadnou, až se nová dávka sloučí do normy)."""
    existing = plan.existing_pairs()
    known = {_key(p) for p in plan.files}
    next_part: dict[str, int] = {}
    for task in plan.tasks:
        next_part[task.task] = max(next_part.get(task.task, 0), task.part + 1)
    added = skipped = 0
    for task in ext.tasks:
        keep: list[Path] = []
        replaces: list[Path] = []
        for path in task.inputs:
            subject = ext.files.get(path, "")
            pair = (subject, task.task)
            if not subject or _key(path) in known:
                skipped += 1
                continue
            if pair in existing:
                if not replace:
                    skipped += 1
                    continue
                replaces.extend(existing[pair])
            keep.append(path)
        if not keep:
            continue
        task.inputs, task.replaces, task.added = keep, replaces, True
        task.part = next_part.get(task.task, 0)
        next_part[task.task] = task.part + 1
        plan.tasks.append(task)
        for path in keep:
            plan.files[path] = ext.files[path]
        added += len(keep)
    used = set(plan.files.values())
    for subject, control in ext.controls.items():
        if subject not in used:
            continue
        old = plan.controls.get(subject)
        if old is None:
            plan.controls[subject] = Control(subject, control.age, control.sex)
        else:
            old.age = old.age if old.age is not None else control.age
            old.sex = old.sex or control.sex
    if any(t.added and not t.finished for t in plan.tasks):
        plan.recomputing = True  # norma zůstává použitelná, rozpracovaná je do sloučení
    return added, skipped


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
    return tasks.label(task)
