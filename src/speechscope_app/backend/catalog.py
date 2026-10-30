"""Katalog: SQLite v datech aplikace s pacienty, hodnoceními a indexem běhů (30. 10. 2026).

Dvě role, záměrně různé:

- **Pacienti a hodnocení jsou tady pravda.** Dřív byly jedním JSONem na
  hodnocení ve složce `assessments`; ty se při prvním otevření katalogu
  naimportují a přesunou do `assessments/imported/` (`import_assessments`).
  Plány rozpracovaných hodnocení (`*.plan.json`) zůstávají soubory, jsou
  přechodné. Tabulka `patients` drží pacienta jako entitu (zatím jen klíč,
  označení a pohlaví; `birth_year` je připravený pro dopočet věku).
- **Běhy a jejich řádky jsou jen index**, přestavitelný ze složek v Dokumentech
  (`VisitIndex`): tabulka `features.csv` se čte, jen když se změnila
  (čas změny, velikost, verze registru úloh), jinak se řádky vezmou odsud,
  i po restartu. Smazání katalogu nic neztratí, jen se index přestaví.

Katalog je jeden soubor (`settings.catalog_path()`), žádný server; čte
a zapisuje se jen z hlavního vlákna.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import meta
from .assessments import Assessment, RecordingRef, new_id, patient_key

SCHEMA_VERSION = 1
IMPORTED_DIR = "imported"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS patients (
    key TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    sex TEXT NOT NULL DEFAULT '',
    birth_year INTEGER,
    created TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS assessments (
    id TEXT PRIMARY KEY,
    patient_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    age REAL,
    sex TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS assessments_patient ON assessments (patient_key, date);
CREATE TABLE IF NOT EXISTS assessment_recordings (
    assessment_id TEXT NOT NULL,
    pos INTEGER NOT NULL,
    run TEXT NOT NULL,
    row INTEGER NOT NULL,
    file TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL,
    PRIMARY KEY (assessment_id, pos)
);
CREATE INDEX IF NOT EXISTS assessment_recordings_run ON assessment_recordings (run);
CREATE TABLE IF NOT EXISTS runs (
    root TEXT NOT NULL,
    key TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    registry INTEGER NOT NULL,
    PRIMARY KEY (root, key)
);
CREATE TABLE IF NOT EXISTS recordings (
    root TEXT NOT NULL,
    run TEXT NOT NULL,
    row INTEGER NOT NULL,
    task TEXT NOT NULL,
    file TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    visit TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL,
    PRIMARY KEY (root, run, row)
);
CREATE INDEX IF NOT EXISTS recordings_subject ON recordings (root, subject);
CREATE TABLE IF NOT EXISTS run_info (
    dir TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    data TEXT NOT NULL
);
"""

RunSignature = tuple[int, int, int]  # mtime_ns, velikost, verze registru úloh


@dataclass(slots=True)
class PatientRow:
    key: str
    subject: str
    sex: str = ""
    birth_year: int | None = None
    visits: int = 0
    last_date: str = ""


@dataclass(slots=True)
class RecordingRow:
    """Řádek indexu: to, z čeho `VisitIndex` složí `Recording` bez čtení CSV."""

    row: int
    task: str
    file: str
    path: str
    data: dict[str, Any]


def plain(value: Any) -> Any:
    """Hodnota z pandas do JSON: numpy čísla na čísla, NaN na None."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


class Catalog:
    """Jedno spojení na SQLite; `None` místo cesty = databáze v paměti (testy)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:")
        self._db.row_factory = sqlite3.Row
        if path is not None:
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),)
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # --- hodnocení ---------------------------------------------------------------------

    def assessments(self) -> list[Assessment]:
        """Od nejnovějšího vyšetření (datum, založení, pacient), jako dřív ze souborů."""
        rows = self._db.execute(
            "SELECT id, subject, age, sex, date, created FROM assessments "
            "ORDER BY date DESC, created DESC, subject DESC"
        ).fetchall()
        refs: dict[str, list[RecordingRef]] = {}
        for r in self._db.execute(
            "SELECT assessment_id, run, row, file, task FROM assessment_recordings "
            "ORDER BY assessment_id, pos"
        ):
            refs.setdefault(r["assessment_id"], []).append(
                RecordingRef(r["run"], int(r["row"]), r["file"], r["task"])
            )
        return [
            Assessment(
                id=r["id"],
                subject=r["subject"],
                age=r["age"],
                sex=r["sex"],
                date=r["date"],
                created=r["created"],
                recordings=refs.get(r["id"], []),
            )
            for r in rows
        ]

    def save_assessment(self, assessment: Assessment) -> None:
        if not assessment.id:
            assessment.id = new_id()
        key = patient_key(assessment.subject)
        display = " ".join(assessment.subject.split())  # označení bez okrajových mezer
        with self._db:
            self._db.execute(
                "INSERT INTO patients (key, subject, sex, created) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET subject = excluded.subject, "
                "sex = CASE WHEN excluded.sex != '' THEN excluded.sex ELSE patients.sex END",
                (key, display, assessment.sex, assessment.created),
            )
            self._db.execute(
                "INSERT INTO assessments (id, patient_key, subject, age, sex, date, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "patient_key = excluded.patient_key, subject = excluded.subject, "
                "age = excluded.age, sex = excluded.sex, date = excluded.date, "
                "created = excluded.created",
                (
                    assessment.id,
                    key,
                    assessment.subject,
                    assessment.age,
                    assessment.sex,
                    assessment.date,
                    assessment.created,
                ),
            )
            self._db.execute(
                "DELETE FROM assessment_recordings WHERE assessment_id = ?", (assessment.id,)
            )
            self._db.executemany(
                "INSERT INTO assessment_recordings (assessment_id, pos, run, row, file, task) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (assessment.id, pos, ref.run, ref.row, ref.file, ref.task)
                    for pos, ref in enumerate(assessment.recordings)
                ],
            )

    def delete_assessment(self, assessment_id: str) -> None:
        """Smaže hodnocení; pacient bez vyšetření zmizí také (zatím nenese nic navíc)."""
        with self._db:
            row = self._db.execute(
                "SELECT patient_key FROM assessments WHERE id = ?", (assessment_id,)
            ).fetchone()
            self._db.execute(
                "DELETE FROM assessment_recordings WHERE assessment_id = ?", (assessment_id,)
            )
            self._db.execute("DELETE FROM assessments WHERE id = ?", (assessment_id,))
            if row is not None:
                self._db.execute(
                    "DELETE FROM patients WHERE key = ? AND birth_year IS NULL AND NOT EXISTS "
                    "(SELECT 1 FROM assessments WHERE patient_key = patients.key)",
                    (row["patient_key"],),
                )

    def relocate(self, old_run: str, new_run: str) -> int:
        """Po přejmenování složky běhu přepíše odkazy; vrátí počet změněných hodnocení."""
        with self._db:
            ids = [
                r["assessment_id"]
                for r in self._db.execute(
                    "SELECT DISTINCT assessment_id FROM assessment_recordings WHERE run = ?",
                    (old_run,),
                )
            ]
            self._db.execute(
                "UPDATE assessment_recordings SET run = ? WHERE run = ?", (new_run, old_run)
            )
        return len(ids)

    def patients(self) -> list[PatientRow]:
        """Pacienti s počtem vyšetření a posledním datem, nejnovější první."""
        rows = self._db.execute(
            "SELECT p.key, p.subject, p.sex, p.birth_year, COUNT(a.id) AS visits, "
            "COALESCE(MAX(a.date), '') AS last_date FROM patients p "
            "LEFT JOIN assessments a ON a.patient_key = p.key "
            "GROUP BY p.key ORDER BY last_date DESC, p.subject"
        ).fetchall()
        return [
            PatientRow(
                r["key"], r["subject"], r["sex"], r["birth_year"], int(r["visits"]), r["last_date"]
            )
            for r in rows
        ]

    def import_assessments(self, folder: Path) -> int:
        """Starší hodnocení ze souborů `<id>.json`: do katalogu a soubor do `imported/`.

        Plány (`*.plan.json`) se nechají, rozbitý soubor se přeskočí a zůstane.
        Vrací počet naimportovaných hodnocení."""
        if not folder.is_dir():
            return 0
        known = {r["id"] for r in self._db.execute("SELECT id FROM assessments")}
        imported = 0
        for path in sorted(folder.glob("*.json")):
            if path.name.endswith(".plan.json"):
                continue
            try:
                assessment = Assessment.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if assessment.id not in known:
                self.save_assessment(assessment)
                known.add(assessment.id)
                imported += 1
            target = folder / IMPORTED_DIR
            try:
                target.mkdir(exist_ok=True)
                path.rename(target / path.name)
            except OSError:
                pass  # soubor zůstane, příště se jen pozná podle id
        return imported

    # --- index běhů ----------------------------------------------------------------------

    def run_signature(self, root: Path, key: str) -> RunSignature | None:
        row = self._db.execute(
            "SELECT mtime_ns, size, registry FROM runs WHERE root = ? AND key = ?",
            (str(root), key),
        ).fetchone()
        if row is None:
            return None
        return int(row["mtime_ns"]), int(row["size"]), int(row["registry"])

    def store_run(
        self, root: Path, key: str, signature: RunSignature, rows: list[RecordingRow]
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO runs (root, key, mtime_ns, size, registry) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(root, key) DO UPDATE SET mtime_ns = excluded.mtime_ns, "
                "size = excluded.size, registry = excluded.registry",
                (str(root), key, *signature),
            )
            self._db.execute("DELETE FROM recordings WHERE root = ? AND run = ?", (str(root), key))
            self._db.executemany(
                "INSERT INTO recordings (root, run, row, task, file, path, subject, visit, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(root),
                        key,
                        r.row,
                        r.task,
                        r.file,
                        r.path,
                        str(r.data.get(meta.SUBJECT) or "").strip(),
                        str(r.data.get(meta.VISIT) or "").strip(),
                        json.dumps({k: plain(v) for k, v in r.data.items()}, ensure_ascii=False),
                    )
                    for r in rows
                ],
            )

    def load_run(self, root: Path, key: str) -> list[RecordingRow]:
        return [
            RecordingRow(int(r["row"]), r["task"], r["file"], r["path"], json.loads(r["data"]))
            for r in self._db.execute(
                "SELECT row, task, file, path, data FROM recordings "
                "WHERE root = ? AND run = ? ORDER BY row",
                (str(root), key),
            )
        ]

    def drop_runs(self, root: Path, keep: set[str]) -> int:
        """Zapomene běhy, které už pod složkou nejsou; vrátí, kolik jich bylo."""
        gone = [
            r["key"]
            for r in self._db.execute("SELECT key FROM runs WHERE root = ?", (str(root),))
            if r["key"] not in keep
        ]
        with self._db:
            for key in gone:
                self._db.execute(
                    "DELETE FROM recordings WHERE root = ? AND run = ?", (str(root), key)
                )
                self._db.execute("DELETE FROM runs WHERE root = ? AND key = ?", (str(root), key))
        return len(gone)

    # --- RunInfo (historie) ---------------------------------------------------------------

    def load_run_info(self, run_dir: Path) -> tuple[str, dict[str, Any]] | None:
        """(otisk, pole RunInfo) z katalogu; None, když běh v něm není."""
        row = self._db.execute(
            "SELECT signature, data FROM run_info WHERE dir = ?", (str(run_dir),)
        ).fetchone()
        if row is None:
            return None
        return row["signature"], json.loads(row["data"])

    def store_run_info(self, run_dir: Path, signature: str, data: dict[str, Any]) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO run_info (dir, signature, data) VALUES (?, ?, ?) "
                "ON CONFLICT(dir) DO UPDATE SET signature = excluded.signature, "
                "data = excluded.data",
                (str(run_dir), signature, json.dumps(data, ensure_ascii=False)),
            )

    def prune_run_info(self, root: Path, keep: set[Path]) -> int:
        """Zapomene běhy pod složkou, které už neexistují; vrátí jejich počet."""
        prefix = str(root).rstrip("\\/") + ("\\" if "\\" in str(root) else "/")
        keep_text = {str(p) for p in keep}
        gone = [
            r["dir"]
            for r in self._db.execute("SELECT dir FROM run_info WHERE dir LIKE ?", (prefix + "%",))
            if r["dir"] not in keep_text
        ]
        with self._db:
            self._db.executemany("DELETE FROM run_info WHERE dir = ?", [(d,) for d in gone])
        return len(gone)

    def indexed_runs(self, root: Path) -> list[str]:
        return [
            r["key"]
            for r in self._db.execute(
                "SELECT key FROM runs WHERE root = ? ORDER BY key", (str(root),)
            )
        ]
