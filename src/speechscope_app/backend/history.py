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
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..i18n import N_, tr
from .protocol import Protocol, slugify
from .trash import send_to_trash

RUN_FILE = "run.json"
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})_(.+)$")

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


def read_run(run_dir: Path) -> RunInfo | None:
    """Jedna složka běhu; `None`, když to složka běhu není."""
    match = STAMP.match(run_dir.name)
    if not run_dir.is_dir() or match is None:
        return None
    if not (run_dir / "protocol.yaml").is_file() and not (run_dir / "speechscope.log").is_file():
        return None
    date, hh, mm, ss, slug = match.groups()
    try:
        started: datetime | None = datetime.strptime(f"{date} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        started = None
    protocol_name = slug
    proto_path = run_dir / "protocol.yaml"
    if proto_path.is_file():
        try:
            protocol_name = Protocol.load(proto_path).display_name
        except Exception:  # rozbitý soubor nesmí shodit seznam  # noqa: BLE001
            pass
    data: dict[str, Any] = {}
    run_file = run_dir / RUN_FILE
    if run_file.is_file():
        try:
            data = json.loads(run_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
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
    )


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
    """Celá složka běhu do Koše; `OSError`, když to nejde (otevřený soubor)."""
    if read_run(run_dir) is None:
        raise OSError(0, f"není složka běhu: {run_dir}")
    send_to_trash(run_dir)


def trash_all(work_root: Path, *, keep: set[Path] | None = None) -> tuple[int, list[Path]]:
    """Všechny běhy do Koše kromě `keep` (běžící); vrací počet a co se nepovedlo."""
    skipped = {p.resolve() for p in (keep or set())}
    done = 0
    failed: list[Path] = []
    for info in list_runs(work_root):
        if info.dir.resolve() in skipped or info.status == "running":
            continue
        try:
            send_to_trash(info.dir)
            done += 1
        except OSError:
            failed.append(info.dir)
    return done, failed


def list_runs(work_root: Path) -> list[RunInfo]:
    """Všechny běhy pod pracovní složkou, nejnovější první."""
    if not work_root.is_dir():
        return []
    runs = [info for d in work_root.iterdir() if (info := read_run(d)) is not None]
    runs.sort(key=lambda r: (r.started or datetime.min, r.dir.name), reverse=True)
    return runs
