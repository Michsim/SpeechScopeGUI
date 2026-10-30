"""Historie běhů: složky `<datum>_<čas>_<protokol>` v Dokumentech.

Má-li běh vlastní název, je místo slugu protokolu v názvu složky slug názvu
(`rename_run_dir`), aby se běh dal najít i v Průzkumníku; protokol je vždy
v `protocol.yaml`.

Každý běh má složku s `protocol.yaml`, `speechscope.log`, případně
`features.csv` a od této verze `run.json` se stavem (hotovo, zrušeno,
chyba, jen segmentace či přepis). Starší složky bez `run.json` se
poznají podle toho, co v nich leží.

Hlavní okno zapíše `run.json` se stavem „běží“ hned při spuštění, protože
knihovna zapisuje `features.csv` průběžně a bez záznamu by rozpracovaná
dávka vypadala jako hotová. Po každé nahrávce do něj doplní `processed`.
Běh, který zůstal ve stavu „běží“ po pádu aplikace, se při dalším startu
označí jako přerušený (`mark_orphans`).

Tabulka běžícího běhu se tu nikdy neotvírá: knihovna ji píše přes `.part`
a přejmenování, a Windows přejmenování odmítnou, dokud má soubor někdo
otevřený. Počet řádků se počítá až u skončených běhů.

Běhy z Hodnocení z nahrávek mají v `run.json` `assessment` (id hodnocení)
a `patient`; historie je skládá do jedné skupiny na vyšetření (`RunGroup`,
`group_runs`). Po poslední dávce se jejich složky přesunou do jedné složky
vyšetření `<datum>_<čas>_hodnoceni-<pacient>\<úloha>\` (`merge_assessment_runs`);
nahoře leží `run.json` skupiny (`group`), podsložky jsou dál úplné běhy
a `list_runs` je vrací s `group_dir`. Odkazy na běh (hodnocení, index
nahrávek) používají `RunInfo.key`, u plochého běhu název složky, u vnořeného
`<skupina>/<úloha>`.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..i18n import N_, tr
from . import tasks
from .protocol import Protocol, slugify
from .trash import send_to_trash

if TYPE_CHECKING:
    from .catalog import Catalog

# Katalog (`catalog.Catalog`) drží RunInfo podle otisku souborů běhu: `run.json`,
# `protocol.yaml` a `features.csv` se pak čtou jen u nového nebo změněného běhu.
# Hlavní okno ho nastaví při startu (`use_catalog`); bez něj se čte vždy ze souborů.
_catalog: Catalog | None = None


def use_catalog(catalog: Catalog | None) -> None:
    global _catalog
    _catalog = catalog


RUN_FILE = "run.json"
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})_(.+)$")
GROUP_ASSESSMENT = "assessment"  # hodnota `group` v run.json složky vyšetření

# Skupiny v historii (přepínač Analýzy · Hodnocení · Normy)
KIND_ANALYSIS = "analysis"
KIND_ASSESSMENT = "assessment"
KIND_NORM = "norm"
KINDS = (KIND_ANALYSIS, KIND_ASSESSMENT, KIND_NORM)

STATUS_LABELS = {
    "ok": N_("hotovo"),
    "cancelled": N_("zrušeno"),
    "error": N_("chyba"),
    "prepare": N_("mezivýsledky"),
    "running": N_("běží"),
    "interrupted": N_("přerušeno"),
    "unknown": N_("bez záznamu"),
}


@dataclass(slots=True)
class RunInfo:
    dir: Path
    started: datetime | None
    slug: str
    protocol_name: str
    label: str  # vlastní název běhu z run.json; prázdné = podle protokolu
    status: str  # ok | cancelled | error | prepare | running | interrupted | unknown
    processed: int | None
    total: int | None
    seconds: float | None
    rows: int | None  # řádků ve features.csv, None = tabulka není
    work_dir: Path | None = None  # pracovní složka knihovny (work\<úloha>), z run.json
    prepare: str = ""  # jen segmentace ("segments") nebo přepis ("transcript"), jinak ""
    norm: str = ""  # dávka nové normy: složka jejího plánu; takový běh není pacient
    assessment: str = ""  # dávka hodnocení z nahrávek: id hodnocení
    patient: str = ""  # pacient dávky hodnocení (subject)
    task: str = ""  # úloha z protocol.yaml; u dávky vlastní úlohy její klíč z run.json
    group_dir: Path | None = None  # složka vyšetření, když je běh v ní vnořený
    library_version: str = ""  # verze knihovny, která běh počítala (z run.json)
    # verze feature z katalogu knihovny při spuštění (feature -> verze); starší
    # běhy je nemají. Hodnocení podle nich pozná, že se feature od normy liší.
    feature_versions: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Odkaz na běh v hodnocení a indexu: název složky, u vnořeného `<skupina>/<úloha>`."""
        if self.group_dir is not None:
            return f"{self.group_dir.name}/{self.dir.name}"
        return self.dir.name

    @property
    def kind(self) -> str:
        """Skupina v historii: analýza, hodnocení, nebo norma."""
        if self.norm:
            return KIND_NORM
        if self.assessment:
            return KIND_ASSESSMENT
        return KIND_ANALYSIS

    @property
    def csv_path(self) -> Path | None:
        path = self.dir / "features.csv"
        return path if path.is_file() else None

    @property
    def status_label(self) -> str:
        return tr(STATUS_LABELS.get(self.status, self.status))

    @property
    def display_name(self) -> str:
        """Název běhu pro karty a nadpisy: vlastní, jinak jméno protokolu."""
        return self.label or self.protocol_name


def write_run_file(run_dir: Path, **data: Any) -> Path:
    """Zapíše stav běhu; volá hlavní okno po skončení knihovny."""
    path = run_dir / RUN_FILE
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _count_rows(path: Path) -> int | None:
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return max(0, sum(1 for _ in csv.reader(fh)) - 1)
    except OSError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _started_from(name: str) -> tuple[datetime | None, str] | None:
    """Datum, čas a slug z názvu složky `<datum>_<čas>_<slug>`; None, když tak nevypadá."""
    match = STAMP.match(name)
    if match is None:
        return None
    date, hh, mm, ss, slug = match.groups()
    try:
        started: datetime | None = datetime.strptime(f"{date} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        started = None
    return started, slug


def read_group(group_dir: Path) -> dict[str, Any] | None:
    """`run.json` složky vyšetření (má `group`); None, když to složka vyšetření není."""
    if not group_dir.is_dir() or _started_from(group_dir.name) is None:
        return None
    data = _read_json(group_dir / RUN_FILE)
    return data if data.get("group") else None


def _run_signature(run_dir: Path) -> str:
    """Otisk souborů, ze kterých RunInfo vzniká (čas změny a velikost)."""
    parts: list[str] = []
    for name in (RUN_FILE, "protocol.yaml", "features.csv", "speechscope.log"):
        try:
            stat = (run_dir / name).stat()
            parts.append(f"{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def _info_to_dict(info: RunInfo) -> dict[str, Any]:
    return {
        "started": info.started.isoformat() if info.started else "",
        "slug": info.slug,
        "protocol_name": info.protocol_name,
        "label": info.label,
        "status": info.status,
        "processed": info.processed,
        "total": info.total,
        "seconds": info.seconds,
        "rows": info.rows,
        "work_dir": str(info.work_dir) if info.work_dir else "",
        "prepare": info.prepare,
        "norm": info.norm,
        "assessment": info.assessment,
        "patient": info.patient,
        "task": info.task,
        "library_version": info.library_version,
        "feature_versions": dict(info.feature_versions),
    }


def _info_from_dict(run_dir: Path, group_dir: Path | None, data: dict[str, Any]) -> RunInfo:
    started = None
    if data.get("started"):
        try:
            started = datetime.fromisoformat(str(data["started"]))
        except ValueError:
            started = None
    return RunInfo(
        dir=run_dir,
        started=started,
        slug=str(data.get("slug") or run_dir.name),
        protocol_name=str(data.get("protocol_name") or ""),
        label=str(data.get("label") or ""),
        status=str(data.get("status") or "unknown"),
        processed=data.get("processed"),
        total=data.get("total"),
        seconds=data.get("seconds"),
        rows=data.get("rows"),
        work_dir=Path(data["work_dir"]) if data.get("work_dir") else None,
        prepare=str(data.get("prepare") or ""),
        norm=str(data.get("norm") or ""),
        assessment=str(data.get("assessment") or ""),
        patient=str(data.get("patient") or ""),
        task=str(data.get("task") or ""),
        group_dir=group_dir,
        library_version=str(data.get("library_version") or ""),
        feature_versions=_versions(data.get("feature_versions")),
    )


def read_run(run_dir: Path, *, group_dir: Path | None = None) -> RunInfo | None:
    """Jedna složka běhu; `None`, když to složka běhu není.

    S katalogem se složka čte jen při změně otisku; běžící běh se čte vždy
    (jeho `run.json` se mění po každé nahrávce a tabulka roste)."""
    if _catalog is None or not run_dir.is_dir():
        return _read_run_from_disk(run_dir, group_dir=group_dir)
    signature = _run_signature(run_dir)
    cached = _catalog.load_run_info(run_dir)
    if cached is not None and cached[0] == signature and cached[1].get("status") != "running":
        if group_dir is None and read_group(run_dir.parent) is not None:
            group_dir = run_dir.parent
        return _info_from_dict(run_dir, group_dir, cached[1])
    info = _read_run_from_disk(run_dir, group_dir=group_dir)
    if info is not None and info.status != "running":
        _catalog.store_run_info(run_dir, signature, _info_to_dict(info))
    return info


def _read_run_from_disk(run_dir: Path, *, group_dir: Path | None = None) -> RunInfo | None:
    """RunInfo ze souborů složky (`run.json`, `protocol.yaml`, počet řádků CSV).

    Vnořený běh ve složce vyšetření (`group_dir`) nemá datum v názvu, bere ho
    ze složky vyšetření; bez `group_dir` se vnoření pozná podle rodiče.
    """
    if not run_dir.is_dir():
        return None
    if group_dir is None and read_group(run_dir.parent) is not None:
        group_dir = run_dir.parent
    stamp = _started_from(group_dir.name if group_dir is not None else run_dir.name)
    if stamp is None:
        return None
    if not (run_dir / "protocol.yaml").is_file() and not (run_dir / "speechscope.log").is_file():
        return None
    started, slug = stamp
    if group_dir is not None:
        slug = run_dir.name
    protocol_name = slug
    task = ""
    proto_path = run_dir / "protocol.yaml"
    if proto_path.is_file():
        try:
            proto = Protocol.load(proto_path)
            protocol_name, task = proto.display_name, proto.task
        except Exception:  # rozbitý soubor nesmí shodit seznam  # noqa: BLE001
            pass
    data = _read_json(run_dir / RUN_FILE)
    csv_path = run_dir / "features.csv"
    running = data.get("status") == "running"
    rows = _count_rows(csv_path) if csv_path.is_file() and not running else None
    status = str(data.get("status") or ("ok" if rows is not None else "unknown"))
    processed = data.get("processed")
    if status == "interrupted" and processed is None:
        processed = rows  # záznam bez počtu, tabulka ale říká, kam běh došel
    return RunInfo(
        dir=run_dir,
        started=started,
        slug=slug,
        protocol_name=str(data.get("protocol") or protocol_name),
        label=str(data.get("label") or "").strip(),
        status=status,
        processed=processed,
        total=data.get("total"),
        seconds=data.get("seconds"),
        rows=rows,
        work_dir=Path(data["work_dir"]) if data.get("work_dir") else None,
        prepare=str(data.get("prepare") or ""),
        norm=str(data.get("norm") or ""),
        assessment=str(data.get("assessment") or ""),
        patient=str(data.get("patient") or ""),
        task=str(data.get("task") or "") or task,
        group_dir=group_dir,
        library_version=str(data.get("library_version") or ""),
        feature_versions=_versions(data.get("feature_versions")),
    )


def _versions(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v}


def unique_run_dir(work_root: Path, name: str) -> Path:
    """Složka `name` ve složce výsledků; při kolizi (týž běh v téže vteřině) `-2`, `-3`…"""
    run_dir = work_root / name
    n = 2
    while run_dir.exists():
        run_dir = work_root / f"{name}-{n}"
        n += 1
    return run_dir


def rename_run_dir(run_dir: Path, label: str) -> Path:
    """Přejmenuje složku běhu podle názvu: `<datum>_<čas>_<slug názvu>`.

    Prázdný název vrátí slug protokolu z `protocol.yaml`. Datum a čas zůstávají,
    aby historie složku dál poznala a řazení sedělo. Vrací novou cestu; když se
    jméno nemění, tu původní. Při otevřeném souboru ve složce Windows přejmenování
    odmítnou (`OSError`), o tom rozhoduje volající.
    """
    match = STAMP.match(run_dir.name)
    if match is None:
        return run_dir
    date, hh, mm, ss, old_slug = match.groups()
    if label.strip():
        slug = slugify(label)
    else:
        slug = old_slug
        proto_path = run_dir / "protocol.yaml"
        if proto_path.is_file():
            try:
                slug = Protocol.load(proto_path).slug()
            except Exception:  # noqa: BLE001
                pass
    if slug == old_slug:
        return run_dir
    target = unique_run_dir(run_dir.parent, f"{date}_{hh}-{mm}-{ss}_{slug}")
    run_dir.rename(target)
    return target


def rename_run(run_dir: Path, label: str) -> None:
    """Vlastní název běhu do run.json (prázdný = zpět na jméno protokolu)."""
    run_file = run_dir / RUN_FILE
    data: dict[str, Any] = {}
    if run_file.is_file():
        try:
            data = json.loads(run_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data["label"] = label.strip()
    write_run_file(run_dir, **data)


def mark_orphans(work_root: Path) -> list[Path]:
    """Běhy, které zůstaly „běží“ (pád aplikace), označí jako přerušené.

    Volá se při startu aplikace, kdy nic běžet nemůže.
    """
    marked: list[Path] = []
    for info in list_runs(work_root):
        if info.status != "running":
            continue
        try:
            data = json.loads((info.dir / RUN_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        data["status"] = "interrupted"
        data.setdefault("processed", info.rows)
        try:
            write_run_file(info.dir, **data)
        except OSError:
            continue
        marked.append(info.dir)
    return marked


def trash_run(run_dir: Path) -> None:
    """Celá složka běhu (nebo vyšetření) do Koše; `OSError`, když to nejde (otevřený soubor)."""
    if read_run(run_dir) is None and read_group(run_dir) is None:
        raise OSError(0, f"není složka běhu: {run_dir}")
    send_to_trash(run_dir)


def trash_all(work_root: Path, *, keep: set[Path] | None = None) -> tuple[int, list[Path]]:
    """Všechny běhy do Koše kromě `keep` (běžící); vrací počet a co se nepovedlo."""
    skipped = {p.resolve() for p in (keep or set())}
    done = 0
    failed: list[Path] = []
    trashed: set[Path] = set()
    for info in list_runs(work_root):
        if info.dir.resolve() in skipped or info.status == "running":
            continue
        target = info.group_dir or info.dir  # vnořené běhy i se složkou vyšetření, jednou
        if target in trashed:
            continue
        try:
            send_to_trash(target)
            trashed.add(target)
            done += 1
        except OSError:
            failed.append(target)
    return done, failed


def list_runs(work_root: Path) -> list[RunInfo]:
    """Všechny běhy pod pracovní složkou, nejnovější první; vnořené běhy vyšetření také."""
    if not work_root.is_dir():
        return []
    runs: list[RunInfo] = []
    for d in work_root.iterdir():
        if read_group(d) is not None:
            runs.extend(
                info
                for sub in sorted(d.iterdir())
                if (info := read_run(sub, group_dir=d)) is not None
            )
        elif (info := read_run(d)) is not None:
            runs.append(info)
    runs.sort(key=lambda r: (r.started or datetime.min, r.dir.name), reverse=True)
    if _catalog is not None:
        _catalog.prune_run_info(work_root, {r.dir for r in runs})
    return runs


def run_key(work_root: Path, run_dir: Path) -> str:
    """Klíč běhu pro odkazy (`RunInfo.key`) z cesty, i když složka už neexistuje."""
    try:
        rel = run_dir.relative_to(work_root)
    except ValueError:
        return run_dir.name
    return rel.as_posix()


# --- vyšetření: skupina běhů jednoho hodnocení ---------------------------------------


@dataclass(slots=True)
class RunGroup:
    """Běhy jednoho hodnocení z nahrávek: jedna karta v historii, úlohy uvnitř."""

    key: str  # id hodnocení
    runs: list[RunInfo]  # pořadí podle úloh ze smlouvy
    dir: Path | None  # složka vyšetření, když už jsou běhy sloučené
    patient: str
    # úlohy rozpracovaného plánu (úloha, stav pending/running/ok/error/cancelled);
    # prázdné = hodnocení už založené, karta zná jen běhy
    planned: list[tuple[str, str]] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return KIND_ASSESSMENT

    @property
    def in_progress(self) -> bool:
        return bool(self.planned)

    @property
    def task_count(self) -> int:
        return len(self.planned) if self.planned else len(self.runs)

    def waiting_tasks(self) -> list[str]:
        """Úlohy plánu, které ještě nemají běh (čekají ve frontě)."""
        have = {r.task for r in self.runs}
        return [t for t, status in self.planned if status == "pending" and t not in have]

    @property
    def started(self) -> datetime | None:
        starts = [r.started for r in self.runs if r.started]
        return min(starts) if starts else None

    @property
    def status(self) -> str:
        """Souhrn: běží, chyba, zrušeno či přerušeno má přednost před hotovo.

        Dokud plán hodnocení existuje a některá úloha čeká nebo běží, je to
        „běží“, i když právě žádná dávka nepíše (další je ve frontě)."""
        statuses = {r.status for r in self.runs}
        if "running" in statuses:
            return "running"
        if self.planned and (self.waiting_tasks() or any(s == "running" for _t, s in self.planned)):
            return "running"
        for status in ("error", "interrupted", "cancelled"):
            if status in statuses:
                return status
        return "ok"

    @property
    def status_label(self) -> str:
        if self.status == "running" and self.planned:
            return tr("počítá se")
        return tr(STATUS_LABELS.get(self.status, self.status))

    @property
    def display_name(self) -> str:
        return self.patient or tr("Hodnocení")

    @property
    def total(self) -> int:
        return sum(r.total or 0 for r in self.runs)

    @property
    def processed(self) -> int:
        done = 0
        for r in self.runs:
            if r.processed is not None:
                done += r.processed
            elif r.status == "ok":
                done += r.total or r.rows or 0
        return done

    @property
    def seconds(self) -> float | None:
        known = [r.seconds for r in self.runs if r.seconds]
        return sum(known) if known else None

    @property
    def done(self) -> int:
        return sum(1 for r in self.runs if r.status == "ok")

    def run_for(self, run_dir: Path) -> RunInfo | None:
        return next((r for r in self.runs if r.dir == run_dir), None)

    def contains(self, run_dir: Path) -> bool:
        return self.dir == run_dir or self.run_for(run_dir) is not None

    def latest(self) -> RunInfo:
        """Běh na stránku Výpočet: běžící, jinak nejnovější."""
        running = next((r for r in self.runs if r.status == "running"), None)
        return running or max(self.runs, key=lambda r: r.started or datetime.min)


def group_runs(
    runs: list[RunInfo], plans: dict[str, list[tuple[str, str]]] | None = None
) -> list[RunInfo | RunGroup]:
    """Běhy hodnocení složí podle `assessment` do skupin, ostatní nechá; nejnovější první.

    `plans` jsou rozpracovaná hodnocení (id → úlohy se stavem): skupina pak ví
    i o úlohách, které ještě běh nemají, a hlásí „počítá se“ místo „hotovo“."""
    groups: dict[str, list[RunInfo]] = {}
    entries: list[RunInfo | RunGroup] = []
    for run in runs:
        if run.assessment:
            groups.setdefault(run.assessment, []).append(run)
        else:
            entries.append(run)
    for key, members in groups.items():
        members.sort(key=lambda r: (tasks.task_order(r.task), r.started or datetime.min))
        group_dir = members[0].group_dir if all(r.group_dir for r in members) else None
        patient = next((r.patient for r in members if r.patient), "")
        planned = list((plans or {}).get(key, []))
        entries.append(RunGroup(key, members, group_dir, patient, planned))
    entries.sort(
        key=lambda e: (e.started or datetime.min, e.dir.name if e.dir else ""), reverse=True
    )
    return entries


def retire_assessment_runs(work_root: Path, assessment_id: str) -> int:
    """Před přepočtem hodnocení: dosavadní běhy přestanou patřit k jeho kartě.

    `assessment` v run.json se vyprázdní (karta vyšetření v historii patří novým
    dávkám), `former_assessment` zůstane pro dohledání; běhy, tabulky i název
    („Hodnocení P-017 · Fonace“) se nemění, v historii jsou dál mezi analýzami.
    Vrací, kolik běhů upravil."""
    changed = 0
    for run in list_runs(work_root):
        if run.assessment != assessment_id:
            continue
        data = _read_json(run.dir / RUN_FILE)
        data["assessment"] = ""
        data["former_assessment"] = assessment_id
        try:
            write_run_file(run.dir, **data)
        except OSError:
            continue
        changed += 1
    return changed


def merge_assessment_runs(work_root: Path, group: RunGroup) -> tuple[Path, list[tuple[Path, Path]]]:
    """Ploché běhy vyšetření přesune do jedné složky `<datum>_<čas>_hodnoceni-<pacient>`.

    Vrací složku vyšetření a dvojice (stará, nová) složka běhu. Když některý
    přesun selže (otevřený soubor), vrátí už přesunuté zpět a vyhodí `OSError`;
    volající pak nechá běhy ploché a zkusí to příště.
    """
    if group.dir is not None or group.status == "running":
        raise OSError(0, "vyšetření už je sloučené, nebo ještě běží")
    started = group.started or datetime.now()
    slug = slugify(group.patient) if group.patient.strip() else group.key
    group_dir = unique_run_dir(work_root, f"{started:%Y-%m-%d_%H-%M-%S}_hodnoceni-{slug}")
    group_dir.mkdir(parents=True)
    write_run_file(
        group_dir,
        group=GROUP_ASSESSMENT,
        assessment=group.key,
        patient=group.patient,
        tasks=[r.task for r in group.runs],
    )
    moved: list[tuple[Path, Path]] = []
    try:
        for run in group.runs:
            name = slugify(tasks.label(run.task) or run.dir.name)
            target = group_dir / name
            n = 2
            while target.exists():
                target = group_dir / f"{name}-{n}"
                n += 1
            run.dir.rename(target)
            moved.append((run.dir, target))
    except OSError:
        for old, new in reversed(moved):
            try:
                new.rename(old)
            except OSError:
                pass
        try:
            (group_dir / RUN_FILE).unlink(missing_ok=True)
            group_dir.rmdir()
        except OSError:
            pass
        raise
    return group_dir, moved
