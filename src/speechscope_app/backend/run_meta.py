"""Údaje nahrávek (pacient, datum, věk, pohlaví, skupina) doplněné k hotovému běhu.

Rozhodnuto 1. 11. 2026 (varianta A): spočítané hodnoty na manifestu nezávisí, údaje
se jen připíšou do tabulky výsledků běhu (`features.csv`). Původní tabulka zůstane
vedle jako záloha (`features.puvodni.csv`) a jde vrátit. Soubor se čte a zapisuje
jako text (modul csv), takže hodnoty feature zůstanou přesně, jak je knihovna
zapsala; mění se jen sloupce údajů. Ostatní části aplikace (Porovnání, Hodnocení
a Norma z hotových běhů, index nahrávek) čtou právě tuhle tabulku, takže údaje
uvidí bez další úpravy.
"""

from __future__ import annotations

import csv
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import meta
from .manifest import Manifest, key_for

FEATURES = "features.csv"
BACKUP = "features.puvodni.csv"
# sloupce, které nejsou údaje nahrávky a tudy se nemění (úlohu dává výpočet)
PROTECTED = {"file", "path", "task", "error", "notes", "speechscope_version", "frame"}


@dataclass(slots=True)
class RunTable:
    """Tabulka výsledků běhu jako text: záhlaví, řádky a oddělovač."""

    path: Path
    header: list[str]
    rows: list[list[str]]
    delimiter: str = ","

    def column(self, name: str) -> list[str]:
        if name not in self.header:
            return [""] * len(self.rows)
        i = self.header.index(name)
        return [r[i] if i < len(r) else "" for r in self.rows]

    def meta_columns(self) -> list[str]:
        """Sloupce údajů: bez tečky (feature je `acoustic.x.y`) a mimo technické."""
        return [c for c in self.header if "." not in c and c not in PROTECTED]

    def recording_keys(self) -> list[Path]:
        """Klíč řádku: absolutní cesta nahrávky, jinak název souboru (unikátní pořadím)."""
        paths = self.column("path")
        files = self.column("file")
        out: list[Path] = []
        for i, (p, f) in enumerate(zip(paths, files, strict=True)):
            if p.strip():
                out.append(key_for(Path(p)))
            else:
                out.append(Path(f"#{i}") / (f or "?"))
        return out


@dataclass(slots=True)
class Match:
    """Spárování manifestu s řádky běhu."""

    values: dict[int, dict[str, str]] = field(default_factory=dict)  # řádek -> sloupec -> hodnota
    columns: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)  # nahrávky běhu bez řádku v manifestu

    @property
    def matched(self) -> int:
        return len(self.values)


def read_table(run_dir: Path) -> RunTable:
    path = run_dir / FEATURES
    text = path.read_text(encoding="utf-8-sig")
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return RunTable(path, [], [], delimiter)
    return RunTable(path, rows[0], rows[1:], delimiter)


def has_backup(run_dir: Path) -> bool:
    return (run_dir / BACKUP).is_file()


def match_manifest(table: RunTable, manifest: Manifest) -> Match:
    """Řádky běhu podle cesty nahrávky, a když nesedí, podle názvu souboru (jen jedinečného)."""
    by_name: dict[str, list[Path]] = {}
    for key in manifest.rows:
        by_name.setdefault(key.name.lower(), []).append(key)
    files = table.column("file")
    match = Match(columns=[c for c in manifest.columns if c not in PROTECTED])
    for i, key in enumerate(table.recording_keys()):
        row = manifest.rows.get(key)
        if row is None:
            name = (files[i] or key.name).lower()
            candidates = by_name.get(name, [])
            row = manifest.rows[candidates[0]] if len(candidates) == 1 else None
        if row is None:
            match.unmatched.append(files[i] or key.name)
            continue
        values = {c: v for c, v in row.items() if c in match.columns}
        if values:
            match.values[i] = values
    return match


def apply_metadata(
    run_dir: Path, values: dict[int, dict[str, str]], remove: list[str] | None = None
) -> int:
    """Zapíše údaje do `features.csv` (záloha původní tabulky jednou); vrátí počet buněk.

    Nové sloupce přijdou před první feature, vyhrazené (`subject`, `age`…) se
    pojmenují kanonicky a hodnoty normalizují jako v Analýze. Soubor se nahradí
    naráz (zápis do dočasného a `os.replace`); otevřený v Excelu = OSError.
    `remove` = sloupce údajů ke smazání (feature a chráněné sloupce se nesmažou)."""
    table = read_table(run_dir)
    header = list(table.header)
    changed = 0
    for column in remove or []:
        if column in header and column in table.meta_columns():
            col = header.index(column)
            del header[col]
            for row in table.rows:
                if col < len(row):
                    del row[col]
            changed += 1
    first_feature = next((i for i, c in enumerate(header) if "." in c), len(header))
    for row_index, cells in values.items():
        if not 0 <= row_index < len(table.rows):
            continue
        for raw_column, raw_value in cells.items():
            column = meta.canonical_column(raw_column)
            if column in PROTECTED or "." in column:
                continue
            if column not in header:
                header.insert(first_feature, column)
                for row in table.rows:
                    row.insert(first_feature, "")
                first_feature += 1
            col = header.index(column)
            value = meta.normalize_value(column, raw_value) if raw_value.strip() else ""
            row = table.rows[row_index]
            while len(row) < len(header):
                row.append("")
            if row[col] != value:
                row[col] = value
                changed += 1
    if not changed:
        return 0
    backup = run_dir / BACKUP
    if not backup.exists():
        shutil.copy2(table.path, backup)
    tmp = table.path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=table.delimiter)
        writer.writerow(header)
        writer.writerows(table.rows)
    try:
        os.replace(tmp, table.path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return changed


def restore_original(run_dir: Path) -> bool:
    """Vrátí tabulku před doplněním údajů (záloha zmizí)."""
    backup = run_dir / BACKUP
    if not backup.is_file():
        return False
    os.replace(backup, run_dir / FEATURES)
    return True


def write_manifest(table: RunTable, columns: list[str], target: Path) -> Path:
    """Manifest běhu (path, task a údaje) jako CSV se středníkem, jako Uložit manifest…"""
    paths, files, task = table.column("path"), table.column("file"), table.column("task")
    values = [table.column(c) for c in columns]
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["path", "task", *columns])
        for i in range(len(table.rows)):
            writer.writerow([paths[i] or files[i], task[i], *(v[i] for v in values)])
    return target
