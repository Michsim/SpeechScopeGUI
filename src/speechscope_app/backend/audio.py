"""Délka nahrávky z hlavičky souboru, bez knihovny a bez dekódování.

WAV a FLAC mají délku v hlavičce, čte se pár set bajtů. U mp3, ogg a m4a
by bylo potřeba projít celý soubor, tam se vrací `None` a GUI napíše
„délka neznámá“. Poškozený nebo cizí soubor nikdy nevyhodí výjimku,
odhad času má být jen orientační.
"""

from __future__ import annotations

import struct
from pathlib import Path


def duration_seconds(path: Path) -> float | None:
    try:
        with path.open("rb") as fh:
            head = fh.read(12)
            if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
                return _wav(fh)
            if head[:4] == b"fLaC":
                fh.seek(4)
                return _flac(fh)
    except (OSError, struct.error, ValueError, ZeroDivisionError):
        return None
    return None


def _wav(fh) -> float | None:  # noqa: ANN001
    """Prochází bloky RIFF: `fmt ` dá bajty za sekundu, `data` velikost."""
    byte_rate: int | None = None
    data_size: int | None = None
    while True:
        header = fh.read(8)
        if len(header) < 8:
            break
        chunk_id, size = struct.unpack("<4sI", header)
        if chunk_id == b"fmt ":
            fmt = fh.read(size + (size & 1))
            if len(fmt) < 16:
                return None
            _tag, _channels, _rate, byte_rate = struct.unpack("<HHII", fmt[:12])
        elif chunk_id == b"data":
            data_size = size
            break
        else:
            fh.seek(size + (size & 1), 1)
        if byte_rate is not None and data_size is not None:
            break
    if not byte_rate or data_size is None:
        return None
    return data_size / byte_rate


def _flac(fh) -> float | None:  # noqa: ANN001
    """Blok STREAMINFO: vzorkovací frekvence (20 bitů) a počet vzorků (36 bitů)."""
    header = fh.read(4)
    if len(header) < 4 or header[0] & 0x7F != 0:  # první blok musí být STREAMINFO
        return None
    info = fh.read(34)
    if len(info) < 18:
        return None
    packed = int.from_bytes(info[10:18], "big")
    sample_rate = packed >> 44
    total_samples = packed & ((1 << 36) - 1)
    if not sample_rate or not total_samples:
        return None
    return total_samples / sample_rate


def format_duration(seconds: float | None) -> str:
    """1:05 nebo 12:03:07; prázdný řetězec pro neznámou délku."""
    if seconds is None:
        return ""
    s = max(0, int(round(seconds)))
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"
