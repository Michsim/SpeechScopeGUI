"""Pracovní složka podle úlohy a snímek mezivýsledků do složky běhu."""

from __future__ import annotations

from pathlib import Path

from speechscope_app.backend.work import collect_prepare_outputs, work_dir_for


def test_work_dir_for(tmp_path: Path) -> None:
    assert work_dir_for(tmp_path, "story") == tmp_path / "work" / "story"
    assert work_dir_for(tmp_path, None) == tmp_path / "work"


def test_collect_prepare_outputs(tmp_path: Path) -> None:
    work = tmp_path / "work" / "story"
    (work / "transcript").mkdir(parents=True)
    (work / "segments" / "cut").mkdir(parents=True)
    for stem in ("p01", "p02"):
        (work / "transcript" / f"{stem}.txt").write_text("text", encoding="utf-8")
        (work / "transcript" / f"{stem}.json").write_text("{}", encoding="utf-8")
        (work / "segments" / f"{stem}.txt").write_text("0\t1\tsv", encoding="utf-8")
        (work / "segments" / "cut" / f"{stem}.wav").write_bytes(b"RIFF")
    (work / "transcript" / "p01.words.json").write_text("[]", encoding="utf-8")
    inputs = [tmp_path / "data" / "p01.wav", tmp_path / "data" / "p03.wav"]  # p03 nevznikl
    run = tmp_path / "beh-prepis"
    run.mkdir()

    got = collect_prepare_outputs(work, "transcript", inputs, run)
    assert got.copied == 2 and got.moved_cut == 0
    assert sorted(p.name for p in (run / "transcript").iterdir()) == ["p01.txt", "p01.words.json"]
    assert (work / "transcript" / "p01.txt").is_file()  # kopie, živý soubor zůstal

    run2 = tmp_path / "beh-segmentace"
    run2.mkdir()
    got = collect_prepare_outputs(work, "segments", inputs, run2)
    assert got.copied == 1 and got.moved_cut == 1
    assert (run2 / "segments" / "p01.txt").is_file() and (run2 / "cut" / "p01.wav").is_file()
    assert not (work / "segments" / "cut" / "p01.wav").exists()
    assert (work / "segments" / "cut" / "p02.wav").exists()  # cizí nahrávky se nesahá
    assert collect_prepare_outputs(work, "nlp", inputs, run2).copied == 0  # neznámý druh
