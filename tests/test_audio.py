"""Délka nahrávky z hlavičky WAV a FLAC; cizí soubory dají None."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

from speechscope_app.backend.audio import duration_seconds, format_duration
from speechscope_app.backend.cache import format_size, purge, scan
from speechscope_app.backend.discover import find_recordings


def _wav(path: Path, seconds: float, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(b"\0\0" * int(seconds * rate))


def _flac(path: Path, samples: int, rate: int = 44100) -> None:
    packed = (rate << 44) | (1 << 41) | (15 << 36) | samples  # mono, 16 bitů
    info = bytes(10) + packed.to_bytes(8, "big") + bytes(16)
    path.write_bytes(b"fLaC" + bytes([0x80]) + (34).to_bytes(3, "big") + info)


def test_wav_and_flac_durations(tmp_path: Path) -> None:
    _wav(tmp_path / "a.wav", 2.5)
    _flac(tmp_path / "b.flac", 44100 * 61)
    (tmp_path / "c.mp3").write_bytes(b"ID3" + bytes(100))
    (tmp_path / "d.wav").write_bytes(b"RIFF")  # useknutá hlavička
    (tmp_path / "e.wav").write_bytes(b"RIFF" + struct.pack("<I", 0) + b"WAVEfmt " + bytes(4))
    assert duration_seconds(tmp_path / "a.wav") == 2.5
    assert duration_seconds(tmp_path / "b.flac") == 61
    assert duration_seconds(tmp_path / "c.mp3") is None
    assert duration_seconds(tmp_path / "d.wav") is None
    assert duration_seconds(tmp_path / "e.wav") is None
    assert duration_seconds(tmp_path / "neexistuje.wav") is None


def test_wav_with_extra_chunk_before_data(tmp_path: Path) -> None:
    """Nahrávky z některých rekordérů mají před daty LIST; délka se čte i tak."""
    rate, seconds = 8000, 3
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    data = b"\0" * (rate * 2 * seconds)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
    body += b"LIST" + struct.pack("<I", 5) + b"INFOx" + b"\0"  # liché, s výplní
    body += b"data" + struct.pack("<I", len(data)) + data
    (tmp_path / "r.wav").write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    assert duration_seconds(tmp_path / "r.wav") == seconds


def test_discover_carries_duration(tmp_path: Path) -> None:
    _wav(tmp_path / "p01.wav", 1.0)
    (tmp_path / "p02.ogg").write_bytes(b"OggS")
    found = find_recordings(tmp_path)
    assert [r.duration for r in found] == [1.0, None]


def test_format_duration() -> None:
    assert format_duration(None) == ""
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"


def test_cache_scan_and_purge(tmp_path: Path) -> None:
    import os
    import time

    work = tmp_path / "work"
    (work / "segments").mkdir(parents=True)
    (work / "transcript").mkdir()
    old = work / "segments" / "p01.txt"
    old.write_bytes(b"0\t1\tspeech\n")
    fresh = work / "transcript" / "p01.txt"
    fresh.write_bytes(b"ahoj")
    stamp = time.time() - 40 * 86400
    os.utime(old, (stamp, stamp))

    info = scan(work)
    assert info.files == 2 and info.size == 11 + 4
    assert info.oldest is not None and (time.time() - info.oldest.timestamp()) > 39 * 86400

    removed = purge(work, older_than_days=30)
    assert removed.files == 1 and not old.exists() and fresh.exists()
    assert not (work / "segments").exists()  # prázdná podsložka pryč
    removed = purge(work)
    assert removed.files == 1 and work.is_dir() and scan(work).empty
    assert scan(tmp_path / "nic").empty and purge(tmp_path / "nic").empty


def test_format_size() -> None:
    assert format_size(0) == "0 B"
    assert format_size(12_300) == "12,3 kB"
    assert format_size(3_400_000) == "3,4 MB"
    assert format_size(1_200_000_000) == "1,2 GB"
    assert format_size(250_000_000) == "250 MB"
