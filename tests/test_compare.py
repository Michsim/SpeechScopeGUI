"""Porovnání skupin z běhů: skupiny podle běhu i sloupce, po osobě, metody, FDR."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from test_norm_build import HNR, _rec, _run

from speechscope_app.backend import compare
from speechscope_app.backend.history import read_run

JITTER = "acoustic.quality.perturbation.jitter"
ONLY_PD = "linguistic.syntax.mlu"


def _rows(tmp_path: Path, prefix: str, values: list[float], **extra) -> list[dict]:
    return [
        {"path": _rec(tmp_path, f"{prefix}{i:02d}.wav"), "subject": f"{prefix}{i:02d}",
         HNR: v, JITTER: 1.0 + (i % 3) * 0.1, **extra}
        for i, v in enumerate(values)
    ]  # fmt: skip


@pytest.fixture
def two_runs(tmp_path: Path):  # noqa: ANN201
    pd_rows = _rows(tmp_path, "PD", [10, 11, 12, 13, 12, 11, 10, 9], group="PD")
    for row in pd_rows:
        row[ONLY_PD] = 5.0
    # PD00 má dvě nahrávky: po osobě se zprůměrují
    pd_rows.append({**pd_rows[0], "path": _rec(tmp_path, "PD00_b.wav"), HNR: 14.0})
    hc_rows = _rows(tmp_path, "HC", [20, 21, 22, 19, 20, 23, 21, 22], group="HC")
    pd_run = _run(tmp_path, "2026-10-14_10-00-00_pd", pd_rows)
    hc_run = _run(tmp_path, "2026-10-15_10-00-00_hc", hc_rows)
    return pd_run, hc_run


def test_two_groups_by_run(two_runs) -> None:  # noqa: ANN001
    groups, errors, bad = compare.make_groups(list(two_runs))
    assert [g.name for g in groups] == compare.run_names(list(two_runs))
    assert groups[0].name.endswith("(14. 10. 10:00)")  # stejný protokol: odliší datum
    result = compare.compare(groups)
    assert result.only_in == {groups[0].name: [ONLY_PD]}  # jen společné feature
    hnr = next(r for r in result.results if r.column == HNR)
    assert hnr.n == [8, 8]  # po osobě: PD00 jednou
    assert hnr.test == "MW" and hnr.effect_name == "delta"
    assert hnr.effect == pytest.approx(-1.0)  # PD všude nižší než HC
    assert hnr.significant and result.significant >= 1
    assert result.results[0].column == HNR  # řazení podle velikosti efektu

    per_rec = compare.compare(groups, by_subject=False)
    assert next(r for r in per_rec.results if r.column == HNR).n == [9, 8]

    para = compare.compare(groups, compare.PARAMETRIC)
    hnr_t = next(r for r in para.results if r.column == HNR)
    assert hnr_t.test == "t" and hnr_t.effect_name == "g" and hnr_t.effect < -2
    assert hnr_t.center == hnr_t.mean and hnr.center == hnr.median

    auto = compare.compare(groups, compare.AUTO)
    hnr_a = next(r for r in auto.results if r.column == HNR)
    assert hnr_a.normal is not None and hnr_a.test == ("t" if hnr_a.normal else "MW")

    logged = compare.compare(groups, log=True)
    assert next(r for r in logged.results if r.column == HNR).values[0].max() < 3
    frame = compare.to_frame(result)
    assert {"feature", "q", "effect"} <= set(frame.columns) and len(frame) == len(result.results)


def test_three_groups_by_column_with_pairs(tmp_path: Path) -> None:
    rows = (
        _rows(tmp_path, "PD", [10, 11, 12, 13, 11], group="PD")
        + _rows(tmp_path, "HC", [20, 21, 22, 19, 20], group="HC")
        + _rows(tmp_path, "RB", [15, 16, 14, 15, 17], group="RBD")
        + [{"path": _rec(tmp_path, "X.wav"), "subject": "X", HNR: 1.0}]  # bez skupiny
    )
    run = _run(tmp_path, "2026-10-12_10-00-00_pilot", rows)
    columns = compare.group_columns([compare.read_table(run)])
    assert columns["group"] == {"": 1, "HC": 5, "PD": 5, "RBD": 5}
    groups, _e, _b = compare.make_groups([run], compare.BY_COLUMN, "group", ["PD", "HC", "RBD"])
    assert [g.subjects for g in groups] == [5, 5, 5]
    result = compare.compare(groups)
    hnr = next(r for r in result.results if r.column == HNR)
    assert hnr.test == "KW" and hnr.effect_name == "epsilon2" and len(hnr.pairs) == 3
    assert all(p.q is not None for p in hnr.pairs)
    para = compare.compare(groups, compare.PARAMETRIC)
    assert next(r for r in para.results if r.column == HNR).test == "ANOVA"


def test_few_values_versions_errors_and_tasks(tmp_path: Path) -> None:
    a = _rows(tmp_path, "A", [1, 2, 3, 4])
    a[0]["error"] = "selhalo"
    b = _rows(tmp_path, "B", [5, 6])  # jen dvě osoby: bez testu
    run_a = _run(tmp_path, "2026-10-14_10-00-00_a", a)
    run_b = _run(tmp_path, "2026-10-15_10-00-00_b", b)
    for run, version in ((run_a, "1.1"), (run_b, "1.2")):
        path = run.dir / "run.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["feature_versions"] = {"acoustic.quality.perturbation": version}
        path.write_text(json.dumps(data), encoding="utf-8")
    runs = [read_run(run_a.dir), read_run(run_b.dir)]
    groups, errors, _bad = compare.make_groups(runs)
    assert errors == 1 and groups[0].subjects == 3
    result = compare.compare(groups, excluded_errors=errors)
    hnr = next(r for r in result.results if r.column == HNR)
    assert hnr.note == "few" and not hnr.tested and hnr.q is None
    assert hnr.versions == {groups[0].name: "1.1", groups[1].name: "1.2"}
    ddk = _run(tmp_path, "2026-10-16_10-00-00_ddk", _rows(tmp_path, "D", [1, 2, 3]), task="ddk")
    assert compare.task_problem([run_a, ddk]) and not compare.task_problem([run_a, run_b])


def test_effect_sizes_and_fdr() -> None:
    a, b = np.array([1.0, 2.0, 3.0]), np.array([4.0, 5.0, 6.0])
    assert compare.cliffs_delta(a, b) == -1.0 and compare.cliffs_delta(b, a) == 1.0
    assert compare.hedges_g(a, b) < 0
    assert compare.fdr([0.01, None, 0.04]) == [pytest.approx(0.02), None, pytest.approx(0.04)]
    f, p = compare.welch_anova([a, b, np.array([1.5, 2.5, 3.5])])
    assert f > 0 and 0 < p < 1


def test_compare_from_results_page(qtbot, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    from PySide6.QtCore import QItemSelectionModel

    from speechscope_app.backend.settings import AppSettings
    from speechscope_app.ui.main_window import PAGE_RESULTS, MainWindow

    monkeypatch.setattr(
        "speechscope_app.backend.settings.app_data_dir", lambda: tmp_path / "appdata"
    )
    settings = AppSettings(tmp_path / "settings.ini")
    settings.use_fake_library = True
    settings.models_dir = tmp_path / "models"
    settings.work_root = tmp_path / "work"
    pd_rows = _rows(tmp_path, "PD", [10, 11, 12, 13, 12], group="PD")
    hc_rows = _rows(tmp_path, "HC", [20, 21, 22, 19, 20], group="HC")
    _run(tmp_path, "2026-10-14_10-00-00_pd", pd_rows)
    _run(tmp_path, "2026-10-15_10-00-00_hc", hc_rows)
    _run(tmp_path, "2026-10-16_10-00-00_mix", pd_rows + hc_rows)
    _run(tmp_path, "2026-10-17_10-00-00_ddk", _rows(tmp_path, "D", [1, 2, 3]), task="ddk")

    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_RESULTS)
    runs = window.runs_page
    names = [runs.cell_text(r, 0) for r in range(runs.table.rowCount())]
    assert len(names) == 4

    def select(*dirs: str) -> None:
        runs.table.clearSelection()
        model = runs.table.selectionModel()
        for r, entry in enumerate(runs._rows):
            if entry.dir.name.endswith(dirs):
                flags = QItemSelectionModel.SelectionFlag.Select
                flags |= QItemSelectionModel.SelectionFlag.Rows
                model.select(runs.table.model().index(r, 0), flags)

    select("_pd")
    assert runs.compare_bar.isHidden()  # jeden běh = běžný klik
    select("_pd", "_ddk")
    assert not runs.compare_bar.isHidden() and not runs.compare_btn.isEnabled()
    assert "stejnou úlohu" in runs.compare_reason.text()
    select("_pd", "_hc")
    assert runs.compare_btn.isEnabled() and "vybráno 2" in runs.compare_count.text()
    assert runs.compare_selected() and runs.in_compare()

    page = runs.compare_page
    result = page.comparison()
    assert result is not None and len(result.groups) == 2
    assert page.by_key() == compare.BY_RUN and page.method_key() == compare.NONPARAMETRIC
    # výchozí filtr Liší se (něco se liší), Všechny ukážou celou tabulku
    assert page.only.current() == page.SIGNIFICANT
    assert page.table.rowCount() == result.significant and page.panel_title.text()
    page.only.set_current(page.ALL)
    assert page.table.rowCount() == len(result.results)
    assert "Liší se 1 z 2 feature" in page.verdict.text() and len(page._cards) == 3  # A, proti, B
    assert page.table.horizontalHeaderItem(3).text() == "Cliffovo δ"
    # buňka feature jako v Hodnotách: „feature · sloupec“, popis jen v tooltipu
    assert page.feature_label(HNR)[0] == "perturbation · hnr"
    assert page.select_feature(HNR) and page.panel_title.text().startswith("perturbation · hnr")
    assert page.groups_table.rowCount() == 2 and page.normal_flow.count() == 2
    assert "Mann-Whitney" in page.test_label.text() and "δ" in page.effect_label.text()
    assert page.pairs_table.isHidden()  # párová porovnání jen u víc skupin
    # řazení podle feature: nadpisy feature přes celou šířku, výběr na sloupcích
    page.sort.set_current(page.BY_FEATURE)
    headers = [r for r in range(page.table.rowCount()) if page.table.columnSpan(r, 0) > 1]
    assert headers and page.table.rowCount() == len(result.results) + len(headers)
    assert page.select_feature(JITTER) and page.panel_title.text().startswith("perturbation")
    page.sort.set_current(page.BY_EFFECT)
    page.set_method(compare.PARAMETRIC)
    assert page.table.horizontalHeaderItem(3).text() == "Hedgesovo g"
    page.only.set_current(page.SIGNIFICANT)
    assert len(page.visible_results()) == page.comparison().significant
    target = page.export_csv(tmp_path / "porovnani.csv")
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# method: parametric") and "feature" in text

    # jeden běh se sloupcem group: Porovnat skupiny… podle sloupce
    runs.show_overview()
    mix = next(e for e in runs._rows if e.dir.name.endswith("_mix"))
    assert runs.compare_problem([mix]) == ""
    assert runs.open_compare([mix])
    assert page.by_key() == compare.BY_COLUMN and page.chosen_values() == ["HC", "PD"]
    assert [g.name for g in page.comparison().groups] == ["HC", "PD"]
    assert not page.groups_row.isHidden()  # skupiny podle sloupce vidět v hlavičce
    # Porovnat skupiny… z Hodnot běhu se sloupcem group
    runs.show_overview()
    runs.open_entry(mix)
    assert not runs.results_page.groups_btn.isHidden()
    assert "PD" in runs.results_page.groups_btn.toolTip()
    runs.results_page.groups_btn.click()
    assert runs.in_compare() and page.by_key() == compare.BY_COLUMN
    page.set_value_checked("HC", False)
    assert page.comparison() is None  # jedna skupina nestačí
