"""Index vyšetření: řádky běhů podle pacienta a návštěvy, cache a běžící běh."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from speechscope_app.backend.history import write_run_file
from speechscope_app.backend.visits import VisitIndex

F0 = "acoustic.pitch.f0.std"
RATE = "acoustic.timing.speech_rate.speech_rate"


def make_run(root: Path, name: str, rows: list[dict], status: str = "ok") -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "speechscope.log").write_text("", encoding="utf-8")
    write_run_file(run, status=status, kind="extract", protocol="P", total=len(rows))
    pd.DataFrame(rows).to_csv(run / "features.csv", index=False)
    return run


def test_index_groups_rows_into_visits(tmp_path: Path) -> None:
    make_run(
        tmp_path,
        "2026-10-14_10-12-00_fonace",
        [
            {
                "file": "P-017_fonace.wav",
                "path": "C:/d/P-017_fonace.wav",
                "task": "phonation",
                "Pacient": "P-017",
                "Věk": "64",
                "Pohlaví": "muž",
                "visit": "2026-10-14",
                F0: 1.9,
            },
            {
                "file": "P-018_fonace.wav",
                "path": "C:/d/P-018_fonace.wav",
                "task": "phonation",
                "Pacient": "P-018",
                "Věk": "",
                "Pohlaví": "Ž",
                "visit": "2026-10-14",
                F0: 0.8,
            },
        ],
    )
    make_run(
        tmp_path,
        "2026-10-14_10-40-00_cteni",
        [
            {
                "file": "P-017_cteni.wav",
                "path": "C:/d/P-017_cteni.wav",
                "task": "reading",
                "Pacient": "P-017",
                "Věk": "64",
                "Pohlaví": "muž",
                "visit": "2026-10-14",
                RATE: 3.1,
            },
            {
                "file": "P-017_cteni2.wav",
                "path": "C:/d/P-017_cteni2.wav",
                "task": "reading",
                "Pacient": "P-017",
                "Věk": "64",
                "Pohlaví": "muž",
                "visit": "2026-10-14",
                RATE: 3.3,
            },
        ],
    )
    make_run(  # starší návštěva téhož pacienta, úloha ještě pod starým jménem
        tmp_path,
        "2026-03-12_09-00-00_pohadka",
        [
            {
                "file": "P-017_p.wav",
                "path": "C:/d/P-017_p.wav",
                "task": "story",
                "Pacient": "P-017",
                "visit": "2026-03-12",
                RATE: 2.9,
            }
        ],
    )
    make_run(  # bez manifestu: pacient = název souboru, návštěva = datum běhu
        tmp_path,
        "2026-09-01_08-00-00_ddk",
        [
            {
                "file": "p03_short.wav",
                "path": "C:/d/p03_short.wav",
                "task": "ddk",
                "acoustic.ddk.rate": 5.5,
            }
        ],
    )
    make_run(  # běžící běh se neotvírá
        tmp_path,
        "2026-10-20_12-00-00_bezi",
        [{"file": "x.wav", "path": "C:/d/x.wav", "task": "phonation", "Pacient": "P-099", F0: 1.0}],
        status="running",
    )

    index = VisitIndex(tmp_path)
    visits = index.refresh()
    assert [(v.subject, v.visit) for v in visits] == [
        ("P-017", "2026-10-14"),
        ("P-018", "2026-10-14"),
        ("p03_short", "2026-09-01"),
        ("P-017", "2026-03-12"),
    ]
    latest = visits[0]
    assert latest.explicit and latest.tasks == ["phonation", "reading"]
    assert latest.age == 64.0 and latest.sex == "m" and latest.count("reading") == 2
    rows = latest.rows_by_task()
    assert set(rows) == {"phonation", "reading"} and len(rows["reading"]) == 2
    assert rows["phonation"]["subject"].tolist() == ["P-017"]  # sloupce po aliasech
    phon = next(r for r in latest.recordings if r.task == "phonation")
    assert phon.values == {F0: 1.9} and phon.file == "P-017_fonace.wav"
    assert visits[1].age is None and visits[1].sex == "f"
    assert not visits[2].explicit and visits[2].tasks == ["ddk"]
    assert visits[3].tasks == ["spontaneous"]  # alias úlohy
    assert index.find("P-017", "2026-03-12") is visits[3]
    assert index.visit_of(tmp_path / "2026-10-14_10-40-00_cteni", 1) is latest
    assert index.visit_of(tmp_path / "2026-10-20_12-00-00_bezi", 0) is None


def test_index_cache_follows_file_changes(tmp_path: Path) -> None:
    run = make_run(
        tmp_path,
        "2026-10-14_10-12-00_fonace",
        [{"file": "a.wav", "path": "C:/d/a.wav", "task": "phonation", "subject": "A", F0: 1.0}],
    )
    index = VisitIndex(tmp_path)
    assert [v.subject for v in index.refresh()] == ["A"]
    first = index._cache[run / "features.csv"][1]
    assert index.refresh() and index._cache[run / "features.csv"][1] is first  # beze změny
    pd.DataFrame(
        [{"file": "b.wav", "path": "C:/d/b.wav", "task": "phonation", "subject": "B", F0: 1.0}]
    ).to_csv(run / "features.csv", index=False)
    os.utime(run / "features.csv", (1_900_000_000, 1_900_000_000))
    assert [v.subject for v in index.refresh()] == ["B"]
    (run / "features.csv").unlink()
    assert index.refresh() == [] and not index._cache
    index.set_work_root(tmp_path / "jinde")
    assert index.refresh() == []
