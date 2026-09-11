"""Mezivýsledky knihovny ve složce `work`: kolik zabírají a jejich úklid.

Knihovna si do `work\\segments` a `work\\transcript` ukládá segmentaci
a přepisy, aby je při dalším běhu nemusela počítat znovu. Nikdy je sama
nemaže, takže za měsíce provozu narostou na gigabajty. Složky běhů
(`<datum>_<protokol>` vedle `work`) jsou výsledky a úklid se jich netýká.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class CacheInfo:
    files: int
    size: int  # bajtů
    oldest: datetime | None

    @property
    def empty(self) -> bool:
        return self.files == 0


def _walk(work_dir: Path):  # noqa: ANN202
    for root, _dirs, files in os.walk(work_dir):
        for name in files:
            path = Path(root) / name
            try:
                yield path, path.stat()
            except OSError:
                continue


def scan(work_dir: Path, *, older_than_days: int | None = None) -> CacheInfo:
    """Co ve složce leží; s `older_than_days` jen soubory starší než N dní."""
    limit = time.time() - older_than_days * 86400 if older_than_days is not None else None
    files = size = 0
    oldest: float | None = None
    if work_dir.is_dir():
        for _path, st in _walk(work_dir):
            if limit is not None and st.st_mtime >= limit:
                continue
            files += 1
            size += st.st_size
            if oldest is None or st.st_mtime < oldest:
                oldest = st.st_mtime
    return CacheInfo(files, size, datetime.fromtimestamp(oldest) if oldest else None)


def purge(work_dir: Path, *, older_than_days: int | None = None) -> CacheInfo:
    """Smaže mezivýsledky (všechny, nebo starší než N dní); vrací, co se smazalo.

    Prázdné podsložky se uklidí také, složka `work` sama zůstane, aby
    knihovna měla kam psát.
    """
    if not work_dir.is_dir():
        return CacheInfo(0, 0, None)
    limit = time.time() - older_than_days * 86400 if older_than_days is not None else None
    files = size = 0
    oldest: float | None = None
    for path, st in list(_walk(work_dir)):
        if limit is not None and st.st_mtime >= limit:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        files += 1
        size += st.st_size
        if oldest is None or st.st_mtime < oldest:
            oldest = st.st_mtime
    for root, dirs, _files in os.walk(work_dir, topdown=False):
        for name in dirs:
            try:
                (Path(root) / name).rmdir()  # jen prázdné
            except OSError:
                pass
    return CacheInfo(files, size, datetime.fromtimestamp(oldest) if oldest else None)


def format_size(size: int) -> str:
    """12 kB, 3,4 MB, 1,2 GB."""
    value = float(size)
    for unit in ("B", "kB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            break
        value /= 1000
    if unit == "B":
        return f"{int(value)} B"
    text = f"{value:.1f}".replace(".", ",") if value < 100 else f"{value:.0f}"
    return f"{text} {unit}"
