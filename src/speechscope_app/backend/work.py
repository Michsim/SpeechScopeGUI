"""Pracovní složka knihovny (`work`) a co z ní patří do složky běhu.

Knihovna si mezivýsledky ukládá pod `--work-dir` podle jména nahrávky
(`segments\\p01.txt`, `transcript\\p01.txt`); GUI jí dává `work\\<úloha>`,
aby se stejnojmenné nahrávky různých úloh nepřetlačovaly a aby šlo
mezivýsledky najít. Úklid v Prostředí prochází celou `work`.

Po běhu Jen segmentace / Jen přepis se do složky běhu dá snímek toho, co
vzniklo (přepisy, segmentace), a vyříznutý zvuk (`segments\\cut`) se tam
přesune celý: výpočet feature ho nepotřebuje, je pro člověka. Živé
mezivýsledky, které výpočet feature bere, zůstávají ve `work\\<úloha>`;
opravy přepisů se dělají tam.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

WORK_DIR_NAME = "work"

# Co se po samostatném běhu kopíruje do složky běhu: podsložka ve work a přípony.
PREPARE_OUTPUTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "segments": ("segments", (".txt",)),
    "transcript": ("transcript", (".txt", ".words.json")),
}
CUT_DIR = "cut"


def work_dir_for(work_root: Path, task: str | None) -> Path:
    """`work\\<úloha>`; bez úlohy holá `work` (starší běhy, testy)."""
    base = work_root / WORK_DIR_NAME
    return base / task if task else base


@dataclass(slots=True)
class Collected:
    copied: int = 0  # přepisy nebo segmentace zkopírované do složky běhu
    moved_cut: int = 0  # vyříznuté wav přesunuté do složky běhu


def collect_prepare_outputs(
    work_dir: Path, kind: str, inputs: list[Path], run_dir: Path
) -> Collected:
    """Snímek mezivýsledků nahrávek `inputs` do `run_dir`; vyříznutý zvuk přesune.

    Kopíruje se jen to, co existuje; `.json` s otiskem zůstává jen ve work,
    do složky běhu nepatří. Chyby disku se přeskočí, běh už proběhl.
    """
    result = Collected()
    spec = PREPARE_OUTPUTS.get(kind)
    if spec is None:
        return result
    sub, suffixes = spec
    source = work_dir / sub
    target = run_dir / sub
    for path in inputs:
        for suffix in suffixes:
            src = source / f"{path.stem}{suffix}"
            if not src.is_file():
                continue
            try:
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target / src.name)
                result.copied += 1
            except OSError:
                continue
    if kind == "segments":
        cut_source = source / CUT_DIR
        for path in inputs:
            src = cut_source / f"{path.stem}.wav"
            if not src.is_file():
                continue
            try:
                (run_dir / CUT_DIR).mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(run_dir / CUT_DIR / src.name))
                result.moved_cut += 1
            except OSError:
                continue
        try:
            cut_source.rmdir()  # jen když zůstala prázdná
        except OSError:
            pass
    return result
