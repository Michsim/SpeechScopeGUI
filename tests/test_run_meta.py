"""Údaje nahrávek doplněné k hotovému běhu: manifest, zápis do features.csv se zálohou."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from pytestqt.qtbot import QtBot
from test_norm_build import _rec, _run

from speechscope_app.backend import run_meta
from speechscope_app.backend.manifest import read_manifest

HNR = "acoustic.quality.perturbation.hnr"


def _two_runs_rows(tmp_path: Path) -> list[dict]:
    return [
        {"path": _rec(tmp_path, "a1.wav"), HNR: 12.3456789},
        {"path": _rec(tmp_path, "a2.wav"), HNR: 20.5},
        {"path": _rec(tmp_path, "a3.wav"), HNR: 7.0},
    ]


def test_manifest_matches_by_path_then_name_and_writes_with_backup(tmp_path: Path) -> None:
    info = _run(tmp_path, "2026-11-01_10-00-00_fon", _two_runs_rows(tmp_path))
    before = (info.dir / "features.csv").read_text(encoding="utf-8")
    # manifest jinde: a1 cestou (relativní k jeho složce nesedí), a2 jen názvem, a3 chybí
    other = tmp_path / "jinde"
    other.mkdir()
    (other / "manifest.csv").write_text(
        f"path;pacient;vek;pohlavi;group\n{tmp_path / 'data' / 'a1.wav'};P1;64;muž;PD\n"
        "a2.wav;P2;70;žena;HC\n",
        encoding="utf-8",
    )
    table = run_meta.read_table(info.dir)
    assert table.meta_columns() == []  # bez manifestu žádné údaje
    manifest = read_manifest(other / "manifest.csv", keep_missing=True)
    match = run_meta.match_manifest(table, manifest)
    assert match.matched == 2 and match.unmatched == ["a3.wav"]
    assert set(match.columns) == {"subject", "age", "sex", "group"}
    assert run_meta.apply_metadata(info.dir, match.values) == 8
    assert run_meta.has_backup(info.dir)
    assert (info.dir / run_meta.BACKUP).read_text(encoding="utf-8") == before
    frame = pd.read_csv(info.dir / "features.csv", dtype=str, keep_default_na=False)
    # údaje před feature, kanonicky a normalizované; hodnoty feature beze změny
    assert list(frame.columns)[:6] == ["file", "task", "path", "subject", "age", "sex"]
    assert list(frame["subject"]) == ["P1", "P2", ""]
    assert list(frame["sex"]) == ["m", "f", ""]
    assert list(frame[HNR]) == ["12.3456789", "20.5", "7.0"]
    # zpět na tabulku z výpočtu
    assert run_meta.restore_original(info.dir)
    assert (info.dir / "features.csv").read_text(encoding="utf-8") == before
    assert not run_meta.has_backup(info.dir)


def test_results_page_metadata_dialog_saves_and_reloads(
    qtbot: QtBot, tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    from speechscope_app.backend.settings import AppSettings
    from speechscope_app.ui.main_window import MainWindow

    monkeypatch.setattr(
        "speechscope_app.backend.settings.app_data_dir", lambda: tmp_path / "appdata"
    )
    settings = AppSettings(tmp_path / "settings.ini")
    settings.use_fake_library = True
    settings.models_dir = tmp_path / "models"
    settings.work_root = tmp_path / "work"
    info = _run(tmp_path, "2026-11-01_10-00-00_fon", _two_runs_rows(tmp_path))
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.results_page
    page.load(info.dir / "features.csv")
    assert page.meta_btn.isEnabled()
    from speechscope_app.ui.run_meta_dialog import RunMetaDialog

    dialog = RunMetaDialog(info.dir)
    qtbot.addWidget(dialog)
    (tmp_path / "data" / "manifest.csv").write_text(
        "path;subject;group\na1.wav;P1;PD\na2.wav;P2;HC\na3.wav;P3;PD\n", encoding="utf-8"
    )
    assert dialog.load_manifest(tmp_path / "data" / "manifest.csv") == (3, 0)
    assert dialog.save_btn.isEnabled() and "spárováno 3 z 3" in dialog.status.text()
    target = dialog.save_manifest(tmp_path / "udaje.csv")
    assert target is not None and "P3" in target.read_text(encoding="utf-8-sig")
    assert dialog.save() and dialog.changed_cells == 6
    frame = pd.read_csv(info.dir / "features.csv")
    assert list(frame["group"]) == ["PD", "HC", "PD"]
    # znovu otevřené okno má údaje jako základ a nabídne vrácení
    again = RunMetaDialog(info.dir)
    qtbot.addWidget(again)
    assert "subject" in again.meta.columns and not again.restore_btn.isHidden()


def test_remove_one_metadata_column(tmp_path: Path, qtbot: QtBot) -> None:
    """Odebrat jen jeden sloupec údajů: ostatní a feature zůstanou, záloha jde vrátit."""
    from speechscope_app.ui.run_meta_dialog import RunMetaDialog

    info = _run(tmp_path, "2026-11-01_10-00-00_fon", _two_runs_rows(tmp_path))
    run_meta.apply_metadata(
        info.dir, {0: {"subject": "P1", "group": "PD"}, 1: {"subject": "P2", "group": "HC"}}
    )
    dialog = RunMetaDialog(info.dir)
    qtbot.addWidget(dialog)
    assert dialog.meta.columns == ["subject", "group"]
    assert dialog.remove_column("group") and dialog.meta.columns == ["subject"]
    assert dialog.save_btn.isEnabled() and "odebrat: group" in dialog.status.text()
    assert dialog.save()
    frame = pd.read_csv(info.dir / "features.csv", dtype=str, keep_default_na=False)
    assert "group" not in frame.columns and list(frame["subject"]) == ["P1", "P2", ""]
    assert list(frame[HNR]) == ["12.3456789", "20.5", "7.0"]
    # chráněné sloupce a feature smazat nejde
    assert run_meta.apply_metadata(info.dir, {}, remove=["task", HNR]) == 0
