"""Historie běhů ze složek v Dokumentech."""

from __future__ import annotations

from pathlib import Path

from speechscope_app.backend.history import list_runs, read_run, write_run_file
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
