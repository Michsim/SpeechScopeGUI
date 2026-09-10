"""Nalezení nahrávek ve složce a ručních vstupů vedle nich.

Kopíruje chování `speechscope.io.discover`: stejné přípony, rekurze,
sidecar `p01.labels.txt` (segmentace) a `p01.txt` (přepis). GUI to
potřebuje dřív, než knihovnu spustí, aby ukázalo, co se bude zpracovávat.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import contract


@dataclass(frozen=True, slots=True)
class Recording:
    path: Path
    has_labels: bool
    has_transcript: bool

    @property
    def name(self) -> str:
        return self.path.name


def find_recordings(
    inputs: Path | list[Path],
    *,
    recursive: bool = True,
    suffixes: tuple[str, ...] = contract.AUDIO_SUFFIXES,
) -> list[Recording]:
    roots = [inputs] if isinstance(inputs, Path) else list(inputs)
    wanted = {s.lower() for s in suffixes}
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix.lower() in wanted:
                found.append(root)
            continue
        if not root.is_dir():
            continue
        walker = root.rglob("*") if recursive else root.glob("*")
        found.extend(p for p in sorted(walker) if p.is_file() and p.suffix.lower() in wanted)

    out: list[Recording] = []
    for path in found:
        stem = path.with_suffix("")
        out.append(
            Recording(
                path=path,
                has_labels=Path(str(stem) + contract.LABELS_SUFFIX).is_file(),
                has_transcript=Path(str(stem) + contract.TRANSCRIPT_SUFFIX).is_file(),
            )
        )
    return out
