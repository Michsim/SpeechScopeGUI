"""Historie běhů ze složek v Dokumentech."""

from __future__ import annotations

from pathlib import Path

import pytest

from speechscope_app.backend.history import list_runs, mark_orphans, read_run, write_run_file
from speechscope_app.backend.protocol import Protocol


def _run_dir(root: Path, name: str, *, csv_rows: int | None, run: dict | None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    Protocol(name="Fonace, základní", task="phonation", names={"en": "Phonation, basic"}).save(
        d / "protocol.yaml"
    )
    (d / "speechscope.log").write_text("log", encoding="utf-8")
    if csv_rows is not None:
        (d / "features.csv").write_text(
            "file,path\n" + "".join(f"p{i}.wav,x\n" for i in range(csv_rows)), encoding="utf-8-sig"
        )
    if run is not None:
        write_run_file(d, **run)
    return d


def test_list_runs_newest_first_with_status(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()  # pracovní složka není běh
    (tmp_path / "nahodna").mkdir()
    _run_dir(tmp_path, "2026-09-10_14-30-02_fonace-zakladni", csv_rows=2, run=None)
    _run_dir(
        tmp_path,
        "2026-09-11_09-00-00_fonace-zakladni",
        csv_rows=1,
        run={"status": "cancelled", "processed": 1, "total": 4, "seconds": 12.5},
    )
    _run_dir(
        tmp_path,
        "2026-09-11_10-00-00_fonace-zakladni-prepis",
        csv_rows=None,
        run={"status": "prepare", "processed": 3, "total": 3},
    )
    runs = list_runs(tmp_path)
    assert [r.dir.name[:16] for r in runs] == [
        "2026-09-11_10-00",
        "2026-09-11_09-00",
        "2026-09-10_14-30",
    ]
    prepare, cancelled, old = runs
    assert prepare.status == "prepare" and prepare.csv_path is None and prepare.rows is None
    assert cancelled.status == "cancelled" and cancelled.processed == 1 and cancelled.rows == 1
    assert old.status == "ok" and old.rows == 2 and old.protocol_name == "Fonace, základní"
    assert old.started is not None and old.started.hour == 14
    assert old.status_label == "hotovo"
    assert read_run(tmp_path / "nahodna") is None
    assert list_runs(tmp_path / "neexistuje") == []


def test_running_run_counts_rows_and_orphans_get_interrupted(tmp_path: Path) -> None:
    """Rozpracovaná dávka není „hotovo“, i když features.csv už leží ve složce."""
    d = _run_dir(
        tmp_path,
        "2026-09-11_12-00-00_fonace-zakladni",
        csv_rows=2,
        run={"status": "running", "total": 5, "protocol": "Fonace, základní", "processed": 2},
    )
    info = read_run(d)
    assert info is not None and info.status == "running" and info.status_label == "běží"
    assert info.processed == 2 and info.total == 5
    assert info.rows is None  # tabulka běžícího běhu se neotvírá (zámek pro knihovnu)

    assert mark_orphans(tmp_path) == [d]
    info = read_run(d)
    assert info is not None and info.status == "interrupted" and info.processed == 2
    assert info.rows == 2  # po konci už se tabulka počítá
    assert mark_orphans(tmp_path) == []  # podruhé už není co značit


def test_trash_run_and_all_skip_running(tmp_path: Path, monkeypatch) -> None:
    import shutil

    from speechscope_app.backend import history

    monkeypatch.setattr(history, "send_to_trash", lambda p: shutil.rmtree(p))
    a = _run_dir(tmp_path, "2026-09-12_08-00-00_fonace-zakladni", csv_rows=1, run={"status": "ok"})
    b = _run_dir(tmp_path, "2026-09-12_09-00-00_fonace-zakladni", csv_rows=1, run={"status": "ok"})
    c = _run_dir(
        tmp_path, "2026-09-12_10-00-00_fonace-zakladni", csv_rows=None, run={"status": "running"}
    )
    history.trash_run(a)
    assert not a.exists() and b.exists()
    (tmp_path / "cizi").mkdir()
    with pytest.raises(OSError):
        history.trash_run(tmp_path / "cizi")  # není složka běhu
    done, failed = history.trash_all(tmp_path)
    assert done == 1 and failed == [] and not b.exists() and c.exists()  # běžící zůstal


def test_rename_run_label(tmp_path: Path) -> None:
    from speechscope_app.backend.history import read_run, rename_run

    d = _run_dir(tmp_path, "2026-09-13_11-00-00_fonace-zakladni", csv_rows=1, run={"status": "ok"})
    info = read_run(d)
    assert info is not None and info.label == "" and info.display_name == "Fonace, základní"
    rename_run(d, "  Kontrola po 3 měsících ")
    info = read_run(d)
    assert info is not None and info.label == "Kontrola po 3 měsících"
    assert info.display_name == "Kontrola po 3 měsících" and info.status == "ok"  # zbytek zůstal
    rename_run(d, "")
    assert read_run(d).display_name == "Fonace, základní"
