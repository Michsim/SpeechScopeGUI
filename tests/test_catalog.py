"""Katalog SQLite: index běhů přežije restart, nezměněné CSV se nečte znovu."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from test_visits import make_run

from speechscope_app.backend.catalog import Catalog, plain
from speechscope_app.backend.visits import VisitIndex

JIT = "acoustic.quality.perturbation.jitter_local"


def test_plain_values() -> None:
    assert plain(pd.Series([1.5])[0]) == 1.5 and plain(float("nan")) is None
    assert plain(pd.Series([3])[0]) == 3 and plain("x") == "x"


def test_index_uses_catalog_across_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = make_run(
        tmp_path,
        "2026-10-14_10-12-00_fonace",
        [
            {"file": "P1_a.wav", "path": "x", "task": "phonation", "subject": "P1", JIT: 1.0},
            {
                "file": "P2_a.wav",
                "path": "",
                "task": "phonation",
                "subject": "P2",
                JIT: float("nan"),
            },
        ],
    )
    make_run(tmp_path, "2026-10-15_09-00-00_cteni", [{"file": "P1_c.wav", "task": "reading"}])
    catalog = Catalog(tmp_path / "appdata" / "catalog.sqlite")
    index = VisitIndex(tmp_path, catalog=catalog)
    index.refresh()
    assert sorted(catalog.indexed_runs(tmp_path)) == [
        "2026-10-14_10-12-00_fonace",
        "2026-10-15_09-00-00_cteni",
    ]
    rows = catalog.load_run(tmp_path, "2026-10-14_10-12-00_fonace")
    assert [r.file for r in rows] == ["P1_a.wav", "P2_a.wav"] and rows[0].path == "x"
    assert rows[0].data[JIT] == 1.0 and rows[1].data[JIT] is None  # NaN → None

    # „restart“: nový index nad týmž katalogem CSV vůbec nečte
    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("CSV se nemělo číst")

    monkeypatch.setattr("speechscope_app.backend.visits.pd.read_csv", boom)
    again = VisitIndex(tmp_path, catalog=Catalog(tmp_path / "appdata" / "catalog.sqlite"))
    again.refresh()
    recs = again.all_recordings()
    assert sorted(r.file for r in recs) == ["P1_a.wav", "P1_c.wav", "P2_a.wav"]
    p1 = next(r for r in recs if r.file == "P1_a.wav")
    assert p1.values == {JIT: 1.0} and p1.path == Path("x") and p1.task == "phonation"
    p2 = next(r for r in recs if r.file == "P2_a.wav")
    assert p2.values == {} and p2.path is None
    monkeypatch.undo()

    # změněné CSV se přečte znovu; smazaný běh z katalogu zmizí
    pd.DataFrame(
        [{"file": "P1_a.wav", "path": "x", "task": "phonation", "subject": "P1", JIT: 9.0}]
    ).to_csv(run / "features.csv", index=False)
    import shutil

    shutil.rmtree(tmp_path / "2026-10-15_09-00-00_cteni")
    again.refresh()
    recs = again.all_recordings()
    assert [r.file for r in recs] == ["P1_a.wav"] and recs[0].values == {JIT: 9.0}
    assert catalog.indexed_runs(tmp_path) == ["2026-10-14_10-12-00_fonace"]
    assert catalog.load_run(tmp_path, "2026-10-14_10-12-00_fonace")[0].data[JIT] == 9.0


def test_clear_patients_keeps_run_index(tmp_path: Path) -> None:
    """Smazat pacienty a hodnocení: katalog pacientů prázdný, index běhů zůstává."""
    from speechscope_app.backend.assessments import Assessment, RecordingRef

    make_run(tmp_path, "2026-10-14_10-12-00_fonace", [{"file": "P1_a.wav", "task": "phonation"}])
    catalog = Catalog(tmp_path / "appdata" / "catalog.sqlite")
    VisitIndex(tmp_path, catalog=catalog).refresh()
    catalog.save_assessment(
        Assessment("a1", "P1", 60, "m", "2026-10-14", [RecordingRef("r", 0, "a.wav", "phonation")])
    )
    catalog.set_birth_year("P1", 1966)
    assert catalog.assessments() and catalog.patients()
    assert catalog.clear_patients() == (1, 1)
    assert catalog.assessments() == [] and catalog.patients() == []
    assert catalog.indexed_runs(tmp_path)


def test_window_clears_patients_and_shows_data_dir(qtbot, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    from speechscope_app.backend.assessments import Assessment
    from speechscope_app.backend.settings import AppSettings
    from speechscope_app.ui.main_window import MainWindow

    data = tmp_path / "appdata"
    monkeypatch.setattr("speechscope_app.backend.settings.app_data_dir", lambda: data)
    settings = AppSettings(tmp_path / "settings.ini")
    settings.use_fake_library = True
    settings.models_dir = tmp_path / "models"
    settings.work_root = tmp_path / "work"
    window = MainWindow(settings)
    qtbot.addWidget(window)
    assert window.env_page.data_path.text() == str(data)
    window.catalog.save_assessment(Assessment("a1", "P1", 60, "m", "2026-10-14"))
    plan = settings.assessments_dir() / "x.plan.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("speechscope_app.ui.main_window.send_to_trash", lambda p: p.unlink())
    assert window.clear_patients(confirm=False) == 1
    assert window.catalog.assessments() == [] and not plan.exists()
