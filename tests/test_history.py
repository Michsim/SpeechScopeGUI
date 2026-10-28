"""Historie běhů ze složek v Dokumentech."""

from __future__ import annotations

from pathlib import Path

import pytest

from speechscope_app.backend.history import (
    RunGroup,
    group_runs,
    list_runs,
    mark_orphans,
    merge_assessment_runs,
    read_group,
    read_run,
    run_key,
    trash_all,
    trash_run,
    write_run_file,
)
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


def test_rename_run_dir_follows_label(tmp_path: Path) -> None:
    from speechscope_app.backend.history import read_run, rename_run_dir, unique_run_dir

    d = _run_dir(tmp_path, "2026-09-13_11-00-00_fonace-zakladni", csv_rows=1, run={"status": "ok"})
    new = rename_run_dir(d, "Pacienti září / kontrola")
    assert new == tmp_path / "2026-09-13_11-00-00_pacienti-zari-kontrola" and new.is_dir()
    assert not d.exists()
    assert read_run(new) is not None and read_run(new).started == read_run(new).started
    assert rename_run_dir(new, "pacienti ZÁŘÍ kontrola") == new  # stejný slug = nic
    # zpět na protokol z protocol.yaml; obsazené jméno dostane -2
    (tmp_path / "2026-09-13_11-00-00_fonace-zakladni").mkdir()
    back = rename_run_dir(new, "")
    assert back == tmp_path / "2026-09-13_11-00-00_fonace-zakladni-2" and back.is_dir()
    assert unique_run_dir(tmp_path, "2026-09-13_11-00-00_fonace-zakladni").name.endswith("-3")
    assert rename_run_dir(tmp_path / "cizi", "x") == tmp_path / "cizi"  # není složka běhu


def _assessment_run(root: Path, name: str, task: str, status: str = "ok") -> Path:
    d = root / name
    d.mkdir(parents=True)
    Protocol(name=f"Proto {task}", task=task).save(d / "protocol.yaml")
    (d / "features.csv").write_text("file,path\np1.wav,x\n", encoding="utf-8-sig")
    write_run_file(d, status=status, assessment="abc123", patient="Novák Jan", total=1, processed=1)
    return d


def test_group_runs_and_merge_into_one_folder(tmp_path: Path) -> None:
    ddk = _assessment_run(tmp_path, "2026-10-28_14-06-00_hodnoceni-novak-ddk", "ddk")
    fon = _assessment_run(tmp_path, "2026-10-28_14-05-00_hodnoceni-novak-fonace", "phonation")
    other = _run_dir(tmp_path, "2026-10-28_09-00-00_fonace-zakladni", csv_rows=1, run=None)
    entries = group_runs(list_runs(tmp_path))
    assert [type(e).__name__ for e in entries] == ["RunGroup", "RunInfo"]
    group = entries[0]
    assert isinstance(group, RunGroup)
    assert group.key == "abc123" and group.patient == "Novák Jan" and group.dir is None
    assert [r.task for r in group.runs] == ["phonation", "ddk"]  # pořadí úloh ze smlouvy
    assert group.status == "ok" and group.total == 2 and group.kind == "assessment"
    assert group.started is not None and group.started.minute == 5
    assert entries[1].kind == "analysis" and entries[1].dir == other

    group_dir, moved = merge_assessment_runs(tmp_path, group)
    assert group_dir.name == "2026-10-28_14-05-00_hodnoceni-novak-jan"
    assert moved == [(fon, group_dir / "fonace"), (ddk, group_dir / "ddk")]
    assert not fon.exists() and not ddk.exists()
    assert read_group(group_dir)["assessment"] == "abc123"
    nested = [r for r in list_runs(tmp_path) if r.group_dir == group_dir]
    assert {r.key for r in nested} == {f"{group_dir.name}/fonace", f"{group_dir.name}/ddk"}
    assert all(r.started == group.started for r in nested)  # datum ze složky vyšetření
    assert read_run(group_dir / "ddk").task == "ddk" and read_run(group_dir) is None
    merged = group_runs(list_runs(tmp_path))[0]
    assert isinstance(merged, RunGroup) and merged.dir == group_dir
    assert run_key(tmp_path, group_dir / "ddk") == f"{group_dir.name}/ddk"
    with pytest.raises(OSError):
        merge_assessment_runs(tmp_path, merged)  # už sloučené


def test_merge_rolls_back_when_a_folder_cannot_move(tmp_path: Path, monkeypatch) -> None:
    fon = _assessment_run(tmp_path, "2026-10-28_14-05-00_hodnoceni-novak-fonace", "phonation")
    ddk = _assessment_run(tmp_path, "2026-10-28_14-06-00_hodnoceni-novak-ddk", "ddk")
    group = group_runs(list_runs(tmp_path))[0]
    real_rename = Path.rename

    def failing_rename(self: Path, target: Path) -> Path:
        if self == ddk:
            raise OSError(32, "otevřený soubor")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    with pytest.raises(OSError):
        merge_assessment_runs(tmp_path, group)
    assert fon.is_dir() and ddk.is_dir()  # vráceno zpět
    assert not list(tmp_path.glob("*_hodnoceni-novak-jan"))  # prázdná složka vyšetření pryč


def test_trash_all_removes_examination_folder_once(tmp_path: Path, monkeypatch) -> None:
    _assessment_run(tmp_path, "2026-10-28_14-05-00_hodnoceni-novak-fonace", "phonation")
    _assessment_run(tmp_path, "2026-10-28_14-06-00_hodnoceni-novak-ddk", "ddk")
    group_dir, _moved = merge_assessment_runs(tmp_path, group_runs(list_runs(tmp_path))[0])
    _run_dir(tmp_path, "2026-10-28_09-00-00_fonace-zakladni", csv_rows=1, run=None)
    trashed: list[Path] = []
    monkeypatch.setattr("speechscope_app.backend.history.send_to_trash", trashed.append)
    done, failed = trash_all(tmp_path)
    assert done == 2 and not failed and group_dir in trashed
    trash_run(group_dir)  # složka vyšetření je platný cíl i pro jednotlivé mazání
    assert trashed[-1] == group_dir


def test_group_with_pending_plan_is_still_computing(tmp_path: Path) -> None:
    _assessment_run(tmp_path, "2026-10-28_14-05-00_hodnoceni-novak-fonace", "phonation")
    plans = {
        "abc123": [("phonation", "ok"), ("ddk", "running"), ("reading", "pending")],
    }
    group = group_runs(list_runs(tmp_path), plans)[0]
    assert isinstance(group, RunGroup) and group.in_progress
    assert group.status == "running" and group.status_label == "počítá se"
    assert group.task_count == 3 and group.done == 1 and group.waiting_tasks() == ["reading"]
    with pytest.raises(OSError):
        merge_assessment_runs(tmp_path, group)  # rozpracované se neslučuje
    finished = group_runs(list_runs(tmp_path))[0]  # plán zmizel: hodnocení založené
    assert finished.status == "ok" and finished.status_label == "hotovo"
    assert finished.task_count == 1 and not finished.waiting_tasks()
