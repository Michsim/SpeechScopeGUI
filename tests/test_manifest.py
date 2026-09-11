"""Manifest s metadaty: tolerantní čtení a normalizovaný zápis pro knihovnu."""

from __future__ import annotations

import csv
from pathlib import Path

from speechscope_app.backend.manifest import find_manifest, read_manifest, write_normalized


def test_reads_excel_style_csv(tmp_path: Path) -> None:
    (tmp_path / "p01.wav").write_bytes(b"RIFF")
    (tmp_path / "p02.wav").write_bytes(b"RIFF")
    manifest = tmp_path / "Manifest.csv"
    manifest.write_text(
        "\ufeffpath;pacient;skupina\np01.wav;PD001;PD\np02.wav;HC001;HC\nchybi.wav;X;Y\n",
        encoding="utf-8",
    )
    assert find_manifest(tmp_path) == manifest
    m = read_manifest(manifest, tmp_path)
    assert m.columns == ["pacient", "skupina"]
    assert m.meta_for(tmp_path / "p01.wav") == {"pacient": "PD001", "skupina": "PD"}
    assert m.matched([tmp_path / "p01.wav", tmp_path / "p02.wav", tmp_path / "p03.wav"]) == 2
    assert len(m.problems) == 1 and "chybi.wav" in m.problems[0]


def test_missing_path_column_and_task(tmp_path: Path) -> None:
    (tmp_path / "p01.wav").write_bytes(b"RIFF")
    bad = tmp_path / "m.csv"
    bad.write_text("soubor,pacient\np01.wav,PD001\n", encoding="utf-8")
    assert "path" in read_manifest(bad).problems[0]
    ok = tmp_path / "m2.csv"
    ok.write_text(
        "path,task,pacient\np01.wav,story,PD001\np01.wav,neexistuje,X\n", encoding="utf-8"
    )
    m = read_manifest(ok)
    assert m.tasks[(tmp_path / "p01.wav").resolve()] == "story" or m.problems
    assert find_manifest(tmp_path) is None  # jmenuje se jinak než manifest.csv


def test_write_normalized_covers_all_recordings(tmp_path: Path) -> None:
    for name in ("p01.wav", "p02.wav"):
        (tmp_path / name).write_bytes(b"RIFF")
    src = tmp_path / "manifest.csv"
    src.write_text("path;pacient\np01.wav;PD001\n", encoding="utf-8")
    m = read_manifest(src, tmp_path)
    out = write_normalized(
        tmp_path / "run" / "manifest.csv", [tmp_path / "p01.wav", tmp_path / "p02.wav"], "story", m
    )
    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["task"] for r in rows] == ["story", "story"]
    assert rows[0]["pacient"] == "PD001" and rows[1]["pacient"] == ""
    assert Path(rows[1]["path"]).is_absolute()
