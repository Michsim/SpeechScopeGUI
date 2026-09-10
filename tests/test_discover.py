from __future__ import annotations

from pathlib import Path

from speechscope_app.backend.discover import find_recordings


def test_finds_recordings_and_sidecars(recordings: Path) -> None:
    found = find_recordings(recordings)
    assert [r.name for r in found] == ["p01.wav", "p02_bad.wav", "p03_short.wav", "p04.flac"]
    first = found[0]
    assert first.has_labels and first.has_transcript
    assert not found[1].has_labels and not found[1].has_transcript


def test_non_recursive(recordings: Path) -> None:
    assert [r.name for r in find_recordings(recordings, recursive=False)] == [
        "p01.wav",
        "p02_bad.wav",
        "p03_short.wav",
    ]


def test_single_file_and_missing(recordings: Path, tmp_path: Path) -> None:
    assert [r.name for r in find_recordings(recordings / "p01.wav")] == ["p01.wav"]
    assert find_recordings(tmp_path / "nic") == []
