"""Manifest s metadaty nahrávek (pacient, skupina, návštěva…).

Knihovna bere `--manifest` jako CSV se sloupci `path`, `task` a libovolnými
dalšími, které propíše do výsledné tabulky. Klinik ho ale píše v Excelu:
oddělovač `;`, BOM, relativní cesty, chybějící sloupec `task`. GUI proto
manifest načte tolerantně, ukáže, co v něm je, a do složky běhu zapíše
normalizovanou kopii se všemi nalezenými nahrávkami, aby se žádná
nepřeskočila a knihovna dostala přesně to, co čeká.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .. import contract
from ..i18n import tr

RESERVED = ("path", "task")
DEFAULT_NAMES = ("manifest.csv", "metadata.csv")


@dataclass(slots=True)
class Manifest:
    path: Path
    columns: list[str] = field(default_factory=list)  # metadata bez path a task
    rows: dict[Path, dict[str, str]] = field(default_factory=dict)  # absolutní cesta -> meta
    tasks: dict[Path, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def meta_for(self, recording: Path) -> dict[str, str] | None:
        return self.rows.get(_key(recording))

    def matched(self, recordings: list[Path]) -> int:
        return sum(1 for r in recordings if _key(r) in self.rows)


def _key(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def find_manifest(folder: Path) -> Path | None:
    """`manifest.csv` nebo `metadata.csv` přímo ve složce s nahrávkami."""
    if not folder.is_dir():
        return None
    for name in DEFAULT_NAMES:
        for candidate in folder.iterdir():
            if candidate.is_file() and candidate.name.lower() == name:
                return candidate
    return None


def read_manifest(path: Path, base: Path | None = None) -> Manifest:
    """Načte CSV z Excelu i z knihovny; chyby jdou do `problems`, ne výjimkou."""
    manifest = Manifest(path=path)
    base = base or path.parent
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1250")
    except OSError as exc:
        manifest.problems.append(tr("Soubor nejde přečíst: {error}").format(error=exc))
        return manifest
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    names = [n.strip() for n in (reader.fieldnames or []) if n]
    if "path" not in names:
        manifest.problems.append(
            tr("Chybí sloupec „path“ s názvem souboru; sloupce: {columns}").format(
                columns=", ".join(names) or "–"
            )
        )
        return manifest
    manifest.columns = [n for n in names if n not in RESERVED]
    for lineno, raw in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k is not None}
        rel = row.get("path", "")
        if not rel:
            continue
        target = Path(rel)
        if not target.is_absolute():
            target = base / target
        if not target.is_file():
            manifest.problems.append(
                tr("Řádek {line}: soubor {path} neexistuje").format(line=lineno, path=target)
            )
            continue
        task = row.get("task", "")
        if task and task not in contract.TASKS:
            manifest.problems.append(
                tr("Řádek {line}: neznámá úloha „{task}“").format(line=lineno, task=task)
            )
            task = ""
        key = _key(target)
        manifest.rows[key] = {c: row.get(c, "") for c in manifest.columns}
        if task:
            manifest.tasks[key] = task
    if not manifest.rows and not manifest.problems:
        manifest.problems.append(tr("Manifest neobsahuje žádný řádek s nahrávkou."))
    return manifest


def write_normalized(
    target: Path, recordings: list[Path], task: str, manifest: Manifest | None
) -> Path:
    """CSV pro knihovnu: každá nalezená nahrávka, absolutní cesta, úloha, metadata.

    Nahrávky bez řádku v manifestu dostanou prázdná metadata, aby se
    nepřeskočily. Úloha z manifestu má přednost před úlohou protokolu.
    """
    columns = list(manifest.columns) if manifest else []
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["path", "task", *columns])
        for rec in recordings:
            meta = manifest.meta_for(rec) if manifest else None
            row_task = (manifest.tasks.get(_key(rec)) if manifest else None) or task
            writer.writerow([str(rec), row_task, *[(meta or {}).get(c, "") for c in columns]])
    return target
