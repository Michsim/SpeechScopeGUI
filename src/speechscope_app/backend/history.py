"""Historie běhů: složky `<datum>_<protokol>` v Dokumentech.

Každý běh má složku s `protocol.yaml`, `speechscope.log`, případně
`features.csv` a od této verze `run.json` se stavem (hotovo, zrušeno,
chyba, jen segmentace či přepis). Starší složky bez `run.json` se
poznají podle toho, co v nich leží.
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
from .protocol import Protocol

RUN_FILE = "run.json"
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})_(.+)$")

STATUS_LABELS = {
    "ok": N_("hotovo"),
    "cancelled": N_("zrušeno"),
    "error": N_("chyba"),
    "prepare": N_("mezivýsledky"),
    "running": N_("běží"),
    "unknown": N_("bez záznamu"),
}


@dataclass(slots=True)
class RunInfo:
    dir: Path
    started: datetime | None
    slug: str
    protocol_name: str
    status: str  # ok | cancelled | error | prepare | running | unknown
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
    csv_path = run_dir / "features.csv"
    rows = _count_rows(csv_path) if csv_path.is_file() else None
    data: dict[str, Any] = {}
    run_file = run_dir / RUN_FILE
    if run_file.is_file():
        try:
            data = json.loads(run_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    status = str(data.get("status") or ("ok" if rows is not None else "unknown"))
    return RunInfo(
        dir=run_dir,
        started=started,
        slug=slug,
        protocol_name=str(data.get("protocol") or protocol_name),
        status=status,
        processed=data.get("processed"),
        total=data.get("total"),
        seconds=data.get("seconds"),
        rows=rows,
    )


def list_runs(work_root: Path) -> list[RunInfo]:
    """Všechny běhy pod pracovní složkou, nejnovější první."""
    if not work_root.is_dir():
        return []
    runs = [info for d in work_root.iterdir() if (info := read_run(d)) is not None]
    runs.sort(key=lambda r: (r.started or datetime.min, r.dir.name), reverse=True)
    return runs
