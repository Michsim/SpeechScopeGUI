"""Index vyšetření: řádky všech `features.csv` v Dokumentech podle pacienta a návštěvy.

Běh má jednu úlohu, vyšetření pacienta jich má víc (fonace, DDK, čtení),
takže pavouk na stránce Hodnocení skládá řádky z více běhů. Pacient je
sloupec `subject` z manifestu (po aliasech `pacient`, `id`…), návštěva
sloupec `visit`; bez nich je pacientem název souboru a návštěvou datum
běhu, a vyšetření je „neurčené“ (`explicit=False`).

Běhy z průvodce Nová norma (`RunInfo.norm`) se přeskočí, jsou to kontroly.
Běžící běh se neotvírá: Windows by knihovně zablokovaly přejmenování
`.part` (viz CLAUDE.md). Tabulky se drží v paměti podle cesty, času změny
a velikosti souboru; `refresh` po každém skončeném, smazaném nebo
přejmenovaném běhu je tak levné. S katalogem (`catalog.Catalog`) se řádky
drží i na disku: po restartu se CSV, které se nezměnilo, nečte znovu.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from .. import contract
from . import meta, tasks
from .history import RunInfo, list_runs
from .norms import is_feature_column

if TYPE_CHECKING:
    from .catalog import Catalog

_CacheKey = tuple[str, int, int, int]  # cesta, mtime_ns, velikost, verze registru úloh
ASSIGNMENTS_FILE = "visits.json"  # ruční přiřazení nahrávek k vyšetření (AppData)


@dataclass(slots=True)
class Recording:
    """Jeden řádek tabulky feature: nahrávka v jednom běhu."""

    run: RunInfo
    row: int  # index řádku ve features.csv
    task: str
    file: str
    path: Path | None
    data: dict[str, Any]  # celý řádek (metadata i feature)

    @property
    def ref(self) -> str:
        """Klíč nahrávky pro ruční přiřazení: název složky běhu a řádek."""
        return f"{self.run.key}#{self.row}"

    @property
    def values(self) -> dict[str, float]:
        """Jen číselné hodnoty feature."""
        out: dict[str, float] = {}
        for key, value in self.data.items():
            if not is_feature_column(key):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                out[key] = number
        return out


@dataclass(slots=True)
class Visit:
    """Vyšetření: nahrávky jednoho pacienta z jedné návštěvy, napříč běhy."""

    subject: str
    visit: str  # hodnota sloupce visit, nebo datum běhu (ISO)
    explicit: bool  # pacient z manifestu (True), nebo jen název souboru (False)
    recordings: list[Recording] = field(default_factory=list)
    age: float | None = None
    sex: str = ""
    group: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.subject, self.visit)

    @property
    def tasks(self) -> list[str]:
        return sorted({r.task for r in self.recordings}, key=tasks.task_order)

    @property
    def started(self):  # noqa: ANN201
        """Nejnovější začátek běhu ve vyšetření; řadí seznam."""
        starts = [r.run.started for r in self.recordings if r.run.started]
        return max(starts) if starts else None

    def rows_by_task(self) -> dict[str, pd.DataFrame]:
        """Vstup pro `dimensions.evaluate`: tabulka řádků po úlohách."""
        groups: dict[str, list[dict[str, Any]]] = {}
        for rec in self.recordings:
            groups.setdefault(rec.task, []).append(rec.data)
        return {task: pd.DataFrame(rows) for task, rows in groups.items()}

    def versions_by_task(self) -> dict[str, dict[str, str]]:
        """Verze feature z `run.json` běhů po úlohách (vstup `dimensions.evaluate`)."""
        out: dict[str, dict[str, str]] = {}
        for rec in self.recordings:
            if rec.run.feature_versions:
                out.setdefault(contract.canonical_task(rec.task), {}).update(
                    rec.run.feature_versions
                )
        return out

    def count(self, task: str) -> int:
        return sum(1 for r in self.recordings if r.task == task)


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _recordings(run: RunInfo, frame: pd.DataFrame) -> list[Recording]:
    frame = frame.rename(columns={c: meta.canonical_column(str(c)) for c in frame.columns})
    out: list[Recording] = []
    for i, row in enumerate(frame.to_dict(orient="records")):
        data = {str(k): v for k, v in row.items()}
        task = contract.canonical_task(_text(data.get("task")))
        if tasks.is_custom(run.task):
            task = run.task  # dávka vlastní úlohy: knihovna zapsala jen základní úlohu
        if not task:
            continue
        path_text = _text(data.get("path"))
        file = _text(data.get("file")) or (Path(path_text).name if path_text else f"#{i}")
        out.append(Recording(run, i, task, file, Path(path_text) if path_text else None, data))
    return out


class VisitIndex:
    """Vyšetření ze všech skončených běhů pod pracovní složkou."""

    def __init__(
        self, work_root: Path, assignments: Path | None = None, catalog: Catalog | None = None
    ) -> None:
        self.work_root = work_root
        self.catalog = catalog
        self._cache: dict[Path, tuple[_CacheKey, list[Recording]]] = {}
        self.visits: list[Visit] = []
        self._assignments_path = assignments
        self.assignments: dict[str, tuple[str, str]] = {}  # ref nahrávky → (pacient, návštěva)
        self._load_assignments()

    def _load_assignments(self) -> None:
        path = self._assignments_path
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            self.assignments = {
                str(k): (str(v[0]), str(v[1]))
                for k, v in data.items()
                if isinstance(v, list) and len(v) == 2
            }

    def _save_assignments(self) -> None:
        path = self._assignments_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self.assignments, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # přiřazení zůstane aspoň v paměti

    def assign(self, recording: Recording, visit: Visit) -> None:
        """Ručně zařadí nahrávku do vyšetření (data bez manifestu); platí přes restart."""
        self.assignments[recording.ref] = visit.key
        self._save_assignments()
        self.visits = group_visits(self.all_recordings(), self.assignments)

    def unassign(self, recording: Recording) -> None:
        if self.assignments.pop(recording.ref, None) is not None:
            self._save_assignments()
            self.visits = group_visits(self.all_recordings(), self.assignments)

    def all_recordings(self) -> list[Recording]:
        return [r for _key, recs in self._cache.values() for r in recs]

    def signature(self, run: RunInfo) -> tuple:
        """Otisk tabulky běhu (cesta, čas změny, velikost), jak ho zná poslední `refresh`."""
        csv_path = run.csv_path
        cached = self._cache.get(csv_path) if csv_path is not None else None
        return cached[0] if cached is not None else ()

    def set_work_root(self, root: Path) -> None:
        if root != self.work_root:
            self.work_root = root
            self._cache.clear()
            self.visits = []

    def refresh(self) -> list[Visit]:
        """Znovu projde běhy; tabulky, které se nezměnily, se nečtou znovu."""
        recordings: list[Recording] = []
        seen: set[Path] = set()
        seen_keys: set[str] = set()
        for run in list_runs(self.work_root):
            csv_path = run.csv_path
            if csv_path is None or run.status == "running" or run.norm:
                continue  # běh normy jsou zdravé kontroly, ne pacienti
            seen.add(csv_path)
            seen_keys.add(run.key)
            try:
                stat = csv_path.stat()
            except OSError:
                continue
            key: _CacheKey = (
                str(csv_path),
                stat.st_mtime_ns,
                stat.st_size,
                tasks.registry().version,  # nová vlastní úloha mění, čí je běh
            )
            cached = self._cache.get(csv_path)
            if cached is not None and cached[0] == key:
                recordings.extend(cached[1])
                continue
            recs = self._from_catalog(run, key)
            if recs is None:
                try:
                    frame = pd.read_csv(csv_path)
                except (OSError, ValueError):
                    continue
                recs = _recordings(run, frame)
                self._to_catalog(run, key, recs)
            self._cache[csv_path] = (key, recs)
            recordings.extend(recs)
        for stale in set(self._cache) - seen:
            del self._cache[stale]
        if self.catalog is not None:
            self.catalog.drop_runs(self.work_root, seen_keys)
        self.visits = group_visits(recordings, self.assignments)
        return self.visits

    def _from_catalog(self, run: RunInfo, key: _CacheKey) -> list[Recording] | None:
        """Řádky běhu z katalogu, když tam jsou pro touž podobu CSV; jinak None."""
        if self.catalog is None:
            return None
        if self.catalog.run_signature(self.work_root, run.key) != key[1:]:
            return None
        return [
            Recording(run, r.row, r.task, r.file, Path(r.path) if r.path else None, r.data)
            for r in self.catalog.load_run(self.work_root, run.key)
        ]

    def _to_catalog(self, run: RunInfo, key: _CacheKey, recs: list[Recording]) -> None:
        if self.catalog is None:
            return
        from .catalog import RecordingRow

        self.catalog.store_run(
            self.work_root,
            run.key,
            key[1:],
            [RecordingRow(r.row, r.task, r.file, str(r.path or ""), r.data) for r in recs],
        )

    def find(self, subject: str, visit: str) -> Visit | None:
        return next((v for v in self.visits if v.key == (subject, visit)), None)

    def visit_of(self, run_dir: Path, row: int) -> Visit | None:
        """Vyšetření, do kterého patří řádek běhu (Výsledky → Hodnocení)."""
        for v in self.visits:
            if any(r.run.dir == run_dir and r.row == row for r in v.recordings):
                return v
        return None


def group_visits(
    recordings: list[Recording], assignments: dict[str, tuple[str, str]] | None = None
) -> list[Visit]:
    """Seskupí nahrávky podle pacienta a návštěvy, nejnovější vyšetření první.

    `assignments` (ref nahrávky → klíč vyšetření) mají přednost před manifestem.
    """
    visits: dict[tuple[str, str], Visit] = {}
    for rec in recordings:
        assigned = (assignments or {}).get(rec.ref)
        subject = _text(rec.data.get(meta.SUBJECT))
        explicit = bool(subject) or assigned is not None
        if not subject:
            subject = Path(rec.file).stem
        visit = _text(rec.data.get(meta.VISIT))
        if not visit:
            visit = rec.run.started.date().isoformat() if rec.run.started else ""
        if assigned is not None:
            subject, visit = assigned
        entry = visits.get((subject, visit))
        if entry is None:
            entry = Visit(subject, visit, explicit)
            visits[(subject, visit)] = entry
        entry.recordings.append(rec)
        if entry.age is None:
            entry.age = meta.parse_age(rec.data.get(meta.AGE))
        if not entry.sex:
            entry.sex = meta.parse_sex(rec.data.get(meta.SEX))
        if not entry.group:
            entry.group = _text(rec.data.get(meta.GROUP))
    out = list(visits.values())
    out.sort(key=lambda v: (v.started is not None, v.started, v.subject), reverse=True)
    return out
