"""Hodnocení z nahrávek: rozdělení složky po úlohách, plán, dokončení; průvodce a dávky."""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest
from pytestqt.qtbot import QtBot

from speechscope_app.backend.assessment_build import (
    AssessmentPlan,
    Patient,
    PersonRow,
    finish_plan,
    new_plan,
    normalize_visit,
    patient_from_scan,
    pending_plans,
    people_from_scans,
    plan_from_assessment,
    split_by_task,
    split_by_visit,
)
from speechscope_app.backend.assessments import Assessment, AssessmentStore
from speechscope_app.backend.catalog import Catalog
from speechscope_app.backend.history import RunGroup, read_group, read_run
from speechscope_app.backend.norm_build import ERROR, OK, TaskPlan, default_protocol
from speechscope_app.backend.norms import Norm, NormInfo
from speechscope_app.backend.protocol import Protocol, builtin_protocols
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import (
    PAGE_ASSESSMENT,
    PAGE_HOME,
    PAGE_RESULTS,
    PAGE_RUN,
    MainWindow,
)


def make_folder(root: Path, names: dict[str, list[str]], manifest: str = "") -> Path:
    for sub, files in names.items():
        folder = root / sub if sub else root
        folder.mkdir(parents=True, exist_ok=True)
        for name in files:
            (folder / name).write_bytes(b"RIFF")
    if manifest:
        (root / "manifest.csv").write_text(manifest, encoding="utf-8")
    return root


def test_split_by_task_and_patient_from_manifest(tmp_path: Path) -> None:
    plain = make_folder(tmp_path / "fonace", {"": ["P1_a.wav", "P1_b.wav"]})
    split = split_by_task(plain, "phonation")
    assert list(split) == ["phonation"] and len(split["phonation"].recordings) == 2
    assert patient_from_scan(split["phonation"]) == Patient()

    mixed = make_folder(
        tmp_path / "pacient",
        {"": ["aaa.wav", "pataka.wav", "pohadka.wav", "bez.wav"]},
        "path;task;Pacient;Věk;Pohlaví;visit\n"
        "aaa.wav;phonation;P1;64;muž;2026-10-14\n"
        "pataka.wav;ddk;P1;64;muž;2026-10-14\n"
        "pohadka.wav;story;P1;64;muž;2026-10-14\n",
    )
    split = split_by_task(mixed, "phonation")
    assert {t: [r.name for r in s.recordings] for t, s in split.items()} == {
        "phonation": ["aaa.wav", "bez.wav"],  # bez řádku v manifestu = výchozí úloha
        "ddk": ["pataka.wav"],
        "spontaneous": ["pohadka.wav"],  # alias story
    }
    assert patient_from_scan(split["ddk"]) == Patient("P1", 64.0, "m", "2026-10-14")


def make_plan(tmp_path: Path, folder: Path) -> AssessmentPlan:
    protocols = builtin_protocols()
    recs = sorted(folder.glob("*.wav"))
    return new_plan(
        tmp_path / "assessments",
        Patient("P1", 64, "m", "2026-10-14"),
        "cs",
        [
            TaskPlan("phonation", default_protocol("phonation", protocols).to_dict(), recs),
            TaskPlan("ddk", default_protocol("ddk", protocols).to_dict(), []),
        ],
    )


def test_plan_roundtrip_manifest_and_finish(tmp_path: Path) -> None:
    folder = make_folder(tmp_path / "fonace", {"": ["P1_a.wav", "P1_b.wav"]})
    plan = make_plan(tmp_path, folder)
    plan.save()
    assert plan.path.name.endswith(".plan.json") and plan.created
    assert [p.id for p in pending_plans(tmp_path / "assessments")] == [plan.id]
    loaded = AssessmentPlan.load(plan.path)
    assert loaded.patient == plan.patient and loaded.key == plan.path
    manifest = loaded.manifest_for(loaded.tasks[0])
    row = manifest.meta_for(folder / "P1_a.wav")
    assert row == {"subject": "P1", "age": "64", "sex": "m", "visit": "2026-10-14"}
    assert "Hodnocení P1 · Fonace" == loaded.title(loaded.tasks[0])

    run = tmp_path / "work" / "2026-10-14_10-00-00_fonace"
    run.mkdir(parents=True)
    pd.DataFrame(
        [
            {"file": "P1_a.wav", "task": "phonation", "subject": "P1"},
            {"file": "P1_b.wav", "task": "phonation", "subject": "P1", "error": "spadlo"},
        ]
    ).to_csv(run / "features.csv", index=False)
    loaded.set_status("phonation", OK, run)
    loaded.set_status("ddk", ERROR)
    assert loaded.all_finished and not loaded.all_ok and loaded.buildable
    store = AssessmentStore(tmp_path / "assessments", Catalog(None))
    made = finish_plan(loaded, store)
    assert len(made) == 1
    assessment = made[0]
    assert assessment.id == plan.id and assessment.subject == "P1" and assessment.age == 64
    assert [r.file for r in assessment.recordings] == ["P1_a.wav"]  # chybná nahrávka ne
    assert assessment.recordings[0].run == run.name and assessment.recordings[0].row == 0
    assert not plan.path.exists() and store.all()[0].id == plan.id
    assert pending_plans(tmp_path / "assessments") == []
    with pytest.raises(ValueError):
        finish_plan(make_plan(tmp_path, folder), store)  # bez tabulek


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setattr(
        "speechscope_app.backend.settings.app_data_dir", lambda: tmp_path / "appdata"
    )
    s = AppSettings(tmp_path / "settings.ini")
    s.use_fake_library = True
    s.models_dir = tmp_path / "models"
    s.work_root = tmp_path / "work"
    return s


def test_wizard_runs_batches_and_creates_assessment(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    data = tmp_path / "pacient"
    make_folder(
        data,
        {"": ["aaa1.wav", "aaa2.wav", "pataka.wav"]},
        "path;task;subject;age;sex;visit\n"
        "aaa1.wav;phonation;RBD239;65;m;2026-10-28\n"
        "aaa2.wav;phonation;RBD239;65;m;2026-10-28\n"
        "pataka.wav;ddk;RBD239;65;m;2026-10-28\n",
    )
    before = {p.name for p in data.rglob("*")}
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_ASSESSMENT)
    page = window.assessment_page

    dialog = window.make_assessment_build_dialog()
    qtbot.addWidget(dialog)
    assert dialog.mode() == "choose" and not dialog.next_btn.isEnabled()  # nejdřív typ
    dialog.set_mode("single")
    assert not dialog.next_btn.isEnabled()
    dialog.set_folder("phonation", data)  # manifest rozdělí úlohy a vyplní pacienta
    assert dialog.rows["phonation"].count.text() == "2 ✓"
    assert dialog.rows["ddk"].count.text() == "1 ✓" and dialog.rows["ddk"].folder.text()
    assert dialog.rows["reading"].count.text() == "–"
    assert dialog.subject.text() == "RBD239" and dialog.age.text() == "65"
    assert dialog.sex.currentData() == "m"
    assert dialog.date.date().toString("yyyy-MM-dd") == "2026-10-28"
    assert not dialog.prefill_note.isHidden() and "manifest.csv" in dialog.prefill_note.text()
    assert "bez složky" in dialog.rows["reading"].sub.text()
    # jedno vyšetření: jedna obrazovka, formulář nad úlohami, Spustit rovnou
    assert not dialog.person_box.isHidden() and dialog.step() == 0
    assert dialog.next_btn.isEnabled() and dialog.next_btn.text() == "Spustit hodnocení"
    assert dialog.summary.figures()[0] == "2 úlohy"
    assert "Čtení –" in dialog.task_checks.text()
    plan = dialog.plan()
    assert [t.task for t in plan.tasks] == ["phonation", "ddk"]
    assert plan.patient == Patient("RBD239", 65.0, "m", "2026-10-28")

    assert window.start_plan("assessment", plan) == 2
    assert page.count() == 1 and page.current_plan() is not None
    assert "počítá se" in page.card(0).pill.text() and not page.plan_detail.isHidden()
    assert page.plan_table.rowCount() == 2
    qtbot.waitUntil(lambda: not plan.path.exists(), timeout=30000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=10000)
    assert window.nav.currentRow() == PAGE_ASSESSMENT
    page.refresh()
    assert page.count() == 1 and page.current_plan() is None
    assessment = page.current_assessment()
    assert assessment is not None and assessment.subject == "RBD239" and assessment.age == 65
    assert assessment.tasks == ["phonation", "ddk"] and len(assessment.recordings) == 3
    visit = page.current_visit()
    assert visit is not None and visit.count("phonation") == 2 and visit.sex == "m"
    assert page.recordings.rowCount() == 3
    # běhy vyšetření jsou po poslední dávce v jedné složce, v historii jedna karta
    groups = list(settings.work_root.glob("*_hodnoceni-rbd239"))
    assert len(groups) == 1 and sorted(p.name for p in groups[0].iterdir()) == [
        "ddk",
        "fonace",
        "run.json",
    ]
    assert not list(settings.work_root.glob("*_hodnoceni-rbd239-*"))  # ploché už nejsou
    assert {r.run for r in assessment.recordings} == {
        f"{groups[0].name}/fonace",
        f"{groups[0].name}/ddk",
    }
    runs = window.results_page.history
    runs.refresh()
    assert runs.filter() == "assessment" and runs.count() == 1
    group = runs.current()
    assert isinstance(group, RunGroup) and group.dir == groups[0] and group.status == "ok"
    assert [r.task for r in group.runs] == ["phonation", "ddk"]
    assert runs.card(0).title.text() == "RBD239" and runs.card(0).kind_pill.text() == "hodnocení"
    assert "2 úlohy" in runs.card(0).detail.text()
    assert all(not r.norm for r in runs.all_runs()) and len(runs.all_runs()) == 2
    runs.set_filter("analysis")
    assert runs.count() == 0 and runs.filter_buttons["assessment"].text() == "Hodnocení (1)"
    # Výsledky: karta vyšetření, řada úloh, Otevřít v Hodnocení
    results = window.results_page
    window.nav.setCurrentRow(PAGE_RESULTS)
    assert results.open_run(groups[0] / "ddk") and runs.filter() == "assessment"
    assert not results.task_bar.isHidden() and len(results.task_buttons) == 2
    assert results.task_buttons[1].isChecked() and results.task_buttons[1].text() == "DDK (1)"
    assert results.title_label.text().startswith("Hodnocení RBD239")
    assert results.current_dir() == groups[0] / "ddk"
    results.task_buttons[0].click()
    assert results.current_dir() == groups[0] / "fonace" and results.table.model().rowCount() == 2
    # detail na Výsledcích: řada úloh nad záložkami platí i pro Průběh
    detail = window.runs_page
    assert detail.open_run(groups[0] / "ddk") and detail.task_bar.parentWidget() is not results
    assert detail.task_run() is not None and detail.task_run().dir == groups[0] / "ddk"
    detail.set_tab("progress")
    assert window.run_page.viewing_dir() == groups[0] / "ddk"
    results.task_buttons[0].click()
    assert detail.task_run().dir == groups[0] / "fonace"
    assert window.run_page.viewing_dir() == groups[0] / "fonace"  # Průběh přepnul úlohu
    results.assess_btn.click()
    assert window.nav.currentRow() == PAGE_ASSESSMENT
    assert page.current_assessment().id == assessment.id
    assert {p.name for p in data.rglob("*")} == before  # do složky s nahrávkami nic


def test_pending_assessment_plan_discard(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    folder = make_folder(tmp_path / "fonace", {"": ["P1_a.wav"]})
    plan = make_plan(tmp_path, folder)
    plan.path = settings.assessments_dir() / plan.path.name
    plan.save()
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_ASSESSMENT)
    page = window.assessment_page
    assert page.count() == 1 and page.current_plan() is not None
    assert page.card(0).pill.text() == "dávky 0 z 2"
    assert page.resume_btn.isEnabled() and not page.build_btn.isEnabled()
    assert not window.build_plan("assessment", plan.path)  # nic hotového
    assert window.discard_assessment_plan(plan.path, confirm=False)
    assert page.count() == 0 and not plan.path.exists()
    shutil.rmtree(tmp_path / "assessments", ignore_errors=True)


def test_recompute_assessment_reruns_and_replaces(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    """Přepočítat…: stejné nahrávky a protokoly znovu, hodnocení se stejným id se nahradí."""
    folder = make_folder(tmp_path / "fonace", {"": ["P1_a.wav"]})
    plan = make_plan(tmp_path, folder)
    plan.tasks = plan.tasks[:1]  # jen fonace
    plan.path = settings.assessments_dir() / plan.path.name
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_ASSESSMENT)
    page = window.assessment_page
    assert window.start_plan("assessment", plan) == 1
    qtbot.waitUntil(lambda: not plan.path.exists(), timeout=30000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=10000)
    page.refresh()
    first = page.current_assessment()
    assert first is not None and first.id == plan.id and page.count() == 1
    old_runs = {r.run for r in first.recordings}
    assert page.recompute_btn.isEnabled() and "nahradí" in page.recompute_btn.toolTip()
    history = window.results_page.history
    history.refresh()
    assert history.filter_buttons["assessment"].text() == "Hodnocení (1)"

    # plán přepočtu: tytéž nahrávky, protokol z běhu
    rec_plan, problems = page.recompute_plan(first.id)
    assert problems == [] and rec_plan is not None and rec_plan.id == first.id
    assert [t.task for t in rec_plan.tasks] == ["phonation"]
    assert rec_plan.tasks[0].inputs == [folder / "P1_a.wav"]
    assert rec_plan.tasks[0].proto().name == "Fonace, základní"
    assert rec_plan.patient == Patient("P1", 64.0, "m", "2026-10-14")
    assert page.recompute_plan("neni") == (None, ["hodnocení už neexistuje"])

    # přepočet: v seznamu jen karta „počítá se“, po skončení totéž id s novými běhy
    assert window.recompute_assessment(first.id, confirm=False) == 1
    assert page.count() == 1 and page.current_plan() is not None
    assert page.current_plan().id == first.id and "počítá se" in page.card(0).pill.text()
    assert window.recompute_assessment(first.id, confirm=False) == 0  # už běží
    qtbot.waitUntil(lambda: not rec_plan.path.exists(), timeout=30000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=10000)
    page.refresh()
    second = page.current_assessment()
    assert second is not None and second.id == first.id and page.count() == 1
    new_runs = {r.run for r in second.recordings}
    assert new_runs and new_runs.isdisjoint(old_runs)
    assert page.current_visit() is not None and page.current_visit().count("phonation") == 1
    # staré běhy zůstaly, ale už nepatří ke kartě hodnocení
    history.refresh()
    assert history.filter_buttons["assessment"].text() == "Hodnocení (1)"
    assert history.filter_buttons["analysis"].text() == "Analýzy (1)"
    retired = [r for r in history.all_runs() if not r.assessment]
    assert len(retired) == 1 and retired[0].status == "ok"
    assert read_run(retired[0].dir, group_dir=retired[0].group_dir) is not None
    assert "Hodnocení P1" in retired[0].display_name

    # důvody, proč přepočítat nejde: chybějící protokol, nahrávka mimo disk
    run_dir = page.current_visit().recordings[0].run.dir
    (run_dir / "protocol.yaml").rename(run_dir / "protocol.bak")
    page.refresh()
    assert not page.recompute_btn.isEnabled()
    assert "nemá uložený protokol" in page.recompute_btn.toolTip()
    (run_dir / "protocol.bak").rename(run_dir / "protocol.yaml")
    (folder / "P1_a.wav").unlink()
    page.refresh()
    assert not page.recompute_btn.isEnabled() and "není na disku" in page.recompute_btn.toolTip()
    assert window.recompute_assessment(first.id, confirm=False) == 0
    none_plan, why = plan_from_assessment(second, page.current_visit(), [], tmp_path)
    assert none_plan is None and why == ["nahrávka P1_a.wav už není na disku"]


def test_home_runs_wizard_in_place(qtbot: QtBot, settings: AppSettings, tmp_path: Path) -> None:
    """Nové hodnocení… na Domů: průvodce v kroku 2, Spustit zařadí dávky a vrátí karty."""
    data = make_folder(tmp_path / "pacient", {"": ["aaa1.wav"]})
    window = MainWindow(settings)
    qtbot.addWidget(window)
    home = window.home_page
    assert window.nav.currentRow() == PAGE_HOME and home.step() == 0
    home.assess_btn.click()
    builder = home.builder
    assert home.step() == 1 and builder is not None and not builder.next_btn.isEnabled()
    assert builder.mode() == "choose"
    builder.mode_cards["single"].click()
    builder.subject.setText("P1")
    builder.set_folder("phonation", data)
    assert builder.rows["phonation"].count.text() == "1 ✓" and builder.next_btn.isEnabled()
    builder.go_next()  # Spustit hodnocení
    assert window.nav.currentRow() == PAGE_RUN and home.step() == 0 and home.builder is None
    # nabídka další návštěvy téhož pacienta: průvodce s předvyplněným pacientem
    assert not home.next_visit.isHidden() and "P1" in home.next_visit_text.text()
    home.next_visit_btn.click()
    assert window.nav.currentRow() == PAGE_HOME and home.step() == 1
    # další vyšetření = Vývoj v čase s pacientem
    assert home.builder.mode() == "series" and home.next_visit.isHidden()
    assert home.builder.series.subject_text() == "P1"
    home.home_btn.click()
    assert home.step() == 0
    page = window.assessment_page
    page.refresh()
    assert page.count() == 1 and page.current_plan() is not None
    plan = page.current_plan()
    qtbot.waitUntil(lambda: not plan.path.exists(), timeout=30000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=10000)
    assert window.nav.currentRow() == PAGE_ASSESSMENT
    page.refresh()
    assert page.current_assessment() is not None and page.current_assessment().subject == "P1"
    # dialogová podoba průvodce dál funguje a sdílí widget
    dialog = window.make_assessment_build_dialog()
    qtbot.addWidget(dialog)
    assert dialog.mode() == "choose" and not dialog.next_btn.isEnabled()


def test_normalize_visit_and_people_helpers(tmp_path: Path) -> None:
    assert normalize_visit("2026-10-14T10:00") == "2026-10-14"
    assert (
        normalize_visit("14. 10. 2026") == "2026-10-14"
        and normalize_visit("14.10.2026") == "2026-10-14"
    )
    assert normalize_visit("říjen") == "" and normalize_visit("") == ""
    folder = make_folder(
        tmp_path / "p",
        {"": ["a.wav", "b.wav", "c.wav"]},
        "path;pacient;datum;task;vek;pohlavi\n"
        "a.wav;P1;14. 10. 2025;phonation;63;muž\n"
        "b.wav;P1;2026-10-14;phonation;64;muž\n"
        "c.wav;P2;;phonation;;\n",
    )
    scan = split_by_task(folder, "phonation")["phonation"]
    people = people_from_scans({"phonation": scan})
    a, b, c = (people[(folder / n).resolve()] for n in ("a.wav", "b.wav", "c.wav"))
    assert a == PersonRow("P1", "2025-10-14", 63.0, "m") and b.visit == "2026-10-14"
    assert c == PersonRow("P2", "", None, "")
    groups = split_by_visit(scan.recordings, people)
    assert [(k, [p.name for p in v]) for k, v in groups.items()] == [
        ("", ["c.wav"]),
        ("2025-10-14", ["a.wav"]),
        ("2026-10-14", ["b.wav"]),
    ]


def test_wizard_with_manifest_runs_batches_per_date_and_creates_visits(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    """Víc pacientů a dat z manifestu: tabulka nahrávek, dávka na úlohu a datum, N vyšetření."""
    data = make_folder(
        tmp_path / "pacienti",
        {"": ["p1_2025.wav", "p1_2026.wav", "p2_2026.wav", "p2_bezdata.wav"]},
        "path;subject;visit;task;age;sex\n"
        "p1_2025.wav;P1;2025-10-14;phonation;63;m\n"
        "p1_2026.wav;P1;2026-10-14;phonation;64;m\n"
        "p2_2026.wav;P2;14. 10. 2026;phonation;70;f\n"
        "p2_bezdata.wav;P2;;phonation;70;f\n",
    )
    before = {p.name for p in data.rglob("*")}
    window = MainWindow(settings)
    qtbot.addWidget(window)
    dialog = window.make_assessment_build_dialog()
    qtbot.addWidget(dialog)
    dialog.set_mode("multi")
    dialog.set_folder("phonation", data)
    assert dialog.is_multi() and dialog.step() == 0
    # krok 1 napoví, co čeká v kroku 2
    assert (
        dialog.next_btn.text() == "Další › Pacienti"
        and "manifest: 2 pacienti" in dialog.hint.text()
    )
    assert "1 údajů chybí" in dialog.hint.text()
    dialog.go_next()
    assert dialog.step() == 1 and dialog.person_box.isHidden() and not dialog.people_box.isHidden()
    # řádek = vyšetření (pacient × datum), úlohy ve sloupcích s počtem nahrávek
    table = dialog.visits_table
    headers = [table.horizontalHeaderItem(c).text().lower() for c in range(table.columnCount())]
    assert headers == ["pacient", "datum vyšetření", "věk", "pohlaví", "fonace"]
    rows = [[table.item(r, c).text() for c in range(5)] for r in range(table.rowCount())]
    assert rows == [
        ["P1", "14. 10. 2025", "63", "muž", "1"],
        ["P1", "14. 10. 2026", "64", "muž", "1"],
        ["P2", "14. 10. 2026", "70", "žena", "1"],
    ]
    assert dialog.people_summary.text() == "2 pacienti · 3 vyšetření"
    assert dialog.totals.text() == "celkem: Fonace 3"
    assert dialog.filter_buttons["all"].text() == "všechna 3"
    # nepřiřazená nahrávka (bez data) ve žlutém oddílu
    assert not dialog.unassigned_box.isHidden()
    assert dialog.next_btn.isEnabled() and dialog.next_btn.text() == "Spustit · 3 hodnocení"
    assert "1 nahrávek se jen spočítá" in dialog.hint.text()
    # věk vyšetření platí pro všechny jeho nahrávky; filtr „chybí údaje“ = seznam k doplnění
    dialog.set_visit_value("P2", "2026-10-14", "age", "")
    assert dialog.filter_buttons["missing"].text() == "chybí údaje 1"
    dialog.set_visit_filter("missing")
    assert table.rowCount() == 1 and table.item(0, 0).text() == "P2"
    dialog.set_visit_value("P2", "2026-10-14", "age", "70")
    assert table.rowCount() == 1 and table.item(0, 0).text().endswith("✓")  # zůstane s fajfkou
    dialog.set_visit_filter("all")
    # šablona manifestu kam uživatel ukáže; do složky s nahrávkami nic
    template = dialog.save_template(tmp_path / "sablona.csv")
    assert template is not None and template.read_text(encoding="utf-8-sig").startswith(
        "path;subject;visit;task;age;sex"
    )
    assert "p2_bezdata.wav;;;phonation;;" in template.read_text(encoding="utf-8-sig")
    assert {p.name for p in data.rglob("*")} == before
    # přiřazení nepřiřazené nahrávky jen pro tento výpočet: datum → nové vyšetření
    loose = (data / "p2_bezdata.wav").resolve()
    assert dialog.unassigned() == [loose]
    dialog.set_person(loose, visit="20. 10. 2026")
    assert dialog.people_summary.text() == "2 pacienti · 4 vyšetření"
    assert dialog.next_btn.text() == "Spustit · 4 hodnocení"
    # přiřazená zůstane s ✓, dokud nepadne Hotovo (seznam se při doplňování nepřeskládá)
    assert not dialog.unassigned_box.isHidden()
    assert dialog.unassigned_table.item(0, 1).text().endswith("✓")
    # věk a pohlaví z manifestu (vlastní hodnoty, ne předvyplněné)
    assert dialog.unassigned_table.item(0, 5).text() == "žena"
    assert not dialog.unassigned_table.item(0, 5).font().italic()
    assert dialog.unassigned_table.item(0, 4).text() == "70"
    dialog.end_unassigned()
    assert dialog.unassigned_box.isHidden()
    assert dialog.summary.figures()[:2] == ["2 pacienti", "3 dávek"]  # fonace × 3 různá data
    plan = dialog.plan()
    assert plan.multi and plan.visits() == 4 and plan.subjects() == ["P1", "P2"]
    assert [t.key for t in plan.tasks] == [
        "phonation@2025-10-14",
        "phonation@2026-10-14",
        "phonation@2026-10-20",
    ]
    manifest = plan.manifest_for(next(t for t in plan.tasks if t.visit == "2026-10-14"))
    assert manifest.meta_for(data / "p2_2026.wav") == {
        "subject": "P2",
        "age": "70",
        "sex": "f",
        "visit": "2026-10-14",
    }
    assert plan.title(plan.tasks[0]) == "Hodnocení P1 · Fonace · 2025-10-14"
    assert plan.title(plan.tasks[1]) == "Hodnocení P1, P2 · Fonace · 2026-10-14"
    reloaded = AssessmentPlan.load(plan.save())
    assert reloaded.multi and reloaded.tasks[0].visit == "2025-10-14"
    assert reloaded.people[(data / "p1_2025.wav").resolve()].age == 63.0

    assert window.start_plan("assessment", reloaded) == 3
    qtbot.waitUntil(lambda: not reloaded.path.exists(), timeout=60000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=10000)
    page = window.assessment_page
    page.refresh()
    assessments = page.assessments()
    assert sorted((a.subject, a.date) for a in assessments) == [
        ("P1", "2025-10-14"),
        ("P1", "2026-10-14"),
        ("P2", "2026-10-14"),
        ("P2", "2026-10-20"),
    ]
    assert all(a.id != reloaded.id for a in assessments)
    assert page.count() == 2  # dvě karty pacientů
    p2 = next(a for a in assessments if a.subject == "P2" and a.date == "2026-10-14")
    assert p2.age == 70 and p2.sex == "f" and [r.file for r in p2.recordings] == ["p2_2026.wav"]
    # běhy zůstaly mezi analýzami: žádná karta hodnocení, žádné slučování do složky pacienta
    history = window.results_page.history
    history.refresh()
    assert history.filter_buttons["analysis"].text() == "Analýzy (3)"
    assert history.filter_buttons["assessment"].text() == "Hodnocení"
    assert not any(read_group(p) for p in settings.work_root.iterdir() if p.is_dir())
    assert {p.name for p in data.rglob("*")} == before


def test_wizard_many_unassigned_keeps_window_small(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    """Stovky nahrávek bez data nezvětší okno (posuvník) a datum jde dát všem naráz."""
    names = [f"m{i}.wav" for i in range(60)]
    rows = "".join(f"{n};P{i % 12};;phonation;60;m\n" for i, n in enumerate(names))
    data = make_folder(tmp_path / "mnoho", {"": names}, "path;subject;visit;task;age;sex\n" + rows)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    dialog = window.make_assessment_build_dialog()
    qtbot.addWidget(dialog)
    dialog.set_mode("multi")
    dialog.set_folder("phonation", data)
    dialog.go_next()
    assert dialog.is_multi() and not dialog.unassigned_box.isHidden()
    assert dialog.minimumSizeHint().height() < 900  # dřív rostlo s každým řádkem
    assert not dialog.next_btn.isEnabled()  # bez data žádné vyšetření
    assert len(dialog.unassigned()) == 60
    # Ctrl+A a Vyplnit… s datem = datum všem (jedna přestavba)
    dialog.unassigned_table.selectAll()
    assert not dialog.fill_btn.isHidden() and dialog.selection_label.text() == "vybráno 60"
    paths = dialog.selected_unassigned()
    assert dialog.fill_unassigned(paths, visit="14. 10. 2026") == 60
    assert dialog.people_counts()[:2] == (12, 12)
    assert dialog.next_btn.text() == "Spustit · 12 hodnocení"
    assert dialog.unassigned_head.text() == "přiřazeno 60 z 60"
    dialog.end_unassigned()
    assert dialog.unassigned_box.isHidden()


def test_wizard_unassigned_prefills_from_catalog(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    """Pacient z katalogu: pohlaví a věk z roku narození se předvyplní (šedě), ruční vyhraje."""
    names = ["x1.wav", "x2.wav"]
    rows = "".join(f"{n};;;phonation;;\n" for n in names)
    data = make_folder(tmp_path / "kat", {"": names}, "path;subject;visit;task;age;sex\n" + rows)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.catalog.save_assessment(Assessment("t1", "P-040", 63.0, "m", "2023-01-01"))
    window.catalog.set_birth_year("P-040", 1960)
    dialog = window.make_assessment_build_dialog()
    qtbot.addWidget(dialog)
    dialog.set_mode("multi")
    dialog.set_folder("phonation", data)
    dialog.go_next()
    paths = dialog.unassigned()
    assert len(paths) == 2
    dialog.fill_unassigned(paths, subject="p-040", visit="2026-10-14")
    people = dialog.people()
    assert {(p.age, p.sex) for p in people.values()} == {(66.0, "m")}
    table = dialog.visits_table
    assert table.item(0, 2).text() == "66" and table.item(0, 2).font().italic()
    assert "1960" in table.item(0, 2).toolTip()
    # ruční hodnota předvyplněnou přebije
    dialog.fill_unassigned(paths[:1], age="65")
    assert dialog.people()[paths[0]].age == 65.0


def test_series_wizard_runs_visits_of_one_patient(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    """Vývoj v čase: pacient jednou, mřížka datum × úloha, věk z roku narození, dvě hodnocení."""
    oct_fon = make_folder(tmp_path / "rijen" / "fonace", {"": ["a1.wav", "a2.wav"]})
    oct_ddk = make_folder(tmp_path / "rijen" / "ddk", {"": ["p1.wav"]})
    dec_fon = make_folder(tmp_path / "prosinec" / "fon", {"": ["a3.wav"]})
    window = MainWindow(settings)
    qtbot.addWidget(window)
    dialog = window.make_assessment_build_dialog()
    qtbot.addWidget(dialog)
    dialog.mode_cards["series"].click()
    series = dialog.series
    assert dialog.mode() == "series" and not dialog.next_btn.isEnabled()
    series.subject.setCurrentText("P-017")
    series.sex.setCurrentIndex(series.sex.findData("m"))
    series.birth_year.setText("1962")
    # nic se nehádá: datum ručně, ke každé úloze vlastní složka
    series.set_folder(0, "phonation", oct_fon)
    series.set_folder(0, "ddk", oct_ddk)
    assert "datum" in dialog.hint.text().lower() and not dialog.next_btn.isEnabled()
    series.set_date(0, "14. 10. 2026")
    second = series.add_visit()
    series.set_folder(second, "phonation", dec_fon)
    series.set_date(second, "2026-12-03")
    assert dialog.next_btn.isEnabled() and dialog.next_btn.text() == "Spustit · 2 hodnocení"
    assert dialog.summary.figures()[0] == "2 vyšetření"
    series.set_date(second, "14. 10. 2026")  # stejné datum dvakrát
    assert not dialog.next_btn.isEnabled() and "Stejné datum" in dialog.hint.text()
    series.set_date(second, "3. 12. 2026")
    plan = dialog.plan()
    assert plan.multi and plan.visits() == 2 and plan.subjects() == ["P-017"]
    assert sorted(t.key for t in plan.tasks) == [
        "ddk@2026-10-14",
        "phonation@2026-10-14",
        "phonation@2026-12-03",
    ]
    ages = {p.visit: p.age for p in plan.people.values()}
    assert ages == {"2026-10-14": 64.0, "2026-12-03": 64.0}  # z roku narození
    # Spustit: rok narození k pacientovi do katalogu, po výpočtu dvě hodnocení
    window._remember_birth_year(dialog.widget)
    assert window.start_plan("assessment", plan) == 3
    qtbot.waitUntil(lambda: not plan.path.exists(), timeout=40000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=10000)
    stored = [a for a in window.catalog.assessments() if a.subject == "P-017"]
    assert sorted(a.date for a in stored) == ["2026-10-14", "2026-12-03"]
    patient = next(p for p in window._series_patients() if p.subject == "P-017")
    assert patient.birth_year == 1962 and len(patient.visits) == 2
    # další vyšetření: pacient z katalogu doplní pohlaví a rok, dosavadní řádky šedě
    Protocol(name="Fonace, vlastní", task="phonation").save(
        settings.protocols_dir() / "fonace-vlastni.yaml"
    )
    again = window.make_assessment_build_dialog()
    qtbot.addWidget(again)
    again.prefill("P-017", 64.0, "m")
    assert again.mode() == "series" and again.series.birth_year.text() == "1962"
    # řádek protokolů, dvě z katalogu a jedno nové
    assert again.series.table.rowCount() == 1 + 2 + 1
    # protokoly jako minule (z protocol.yaml běhů posledního vyšetření)
    s2 = again.series
    used = plan.tasks[0].proto().name
    assert s2.previous_protocol("phonation") == used == s2.protocol_for("phonation").name
    assert s2.changed_tasks() == [] and s2.protocol_warning.isHidden()
    s2.set_protocol("phonation", "Fonace, vlastní")
    assert s2.changed_tasks() == ["phonation"] and not s2.protocol_warning.isHidden()
    assert s2.table.item(1, 2).text() == "spočítáno ⚠"
    s2.set_date(0, "1. 2. 2027")
    s2.set_folder(0, "phonation", dec_fon)
    asked: list[str] = []
    accepted: list[bool] = []
    again.widget.accepted.connect(lambda: accepted.append(True))
    again.widget.confirm = lambda text: asked.append(text) or False
    again.widget.go_next()
    assert asked and "Fonace" in asked[0] and not accepted  # odmítnuto: nespustí se
    assert again.widget._task_plans()[0].proto().name == "Fonace, vlastní"
    s2.revert_protocols()
    assert s2.changed_tasks() == [] and s2.protocol_for("phonation").name == used


def test_wizard_protocols_and_language_follow_norm(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    """Hodnocení podle normy: protokol úlohy a jazyk z normy, klinik je nemění; bez normy
    zase výchozí. Norma nese celý protokol, takže stačí i bez souboru protokolu."""
    from test_norms import controls_frame

    fon = Protocol(name="Fonace, kontroly", task="phonation", features=["acoustic.*"])
    info = NormInfo(
        name="Kontroly",
        language="en",
        protocols={"phonation": fon.name},
        protocol_data={"phonation": fon.to_dict()},
    )
    Norm(info, controls_frame()).save(settings.norms_dir() / "kontroly")
    data = make_folder(tmp_path / "fon", {"": ["a1.wav"]})
    reading = make_folder(tmp_path / "cteni", {"": ["c1.wav"]})
    window = MainWindow(settings)
    qtbot.addWidget(window)
    dialog = window.make_assessment_build_dialog()
    qtbot.addWidget(dialog)
    dialog.set_mode("single")
    assert not dialog.norm_bar.isHidden() and dialog.selected_norm().info.name == "Kontroly"
    dialog.subject.setText("P-1")
    dialog.set_folder("phonation", data)
    dialog.set_folder("reading", reading)
    row = dialog.rows["phonation"]
    assert row.current_protocol().name == "Fonace, kontroly"
    assert not row.protocol.isEnabled()  # klinik protokol normy nemění
    if dialog.language.findData("en") >= 0:
        assert dialog.language.currentData() == "en" and not dialog.language.isEnabled()
    # čtení norma nemá: spočítá se výchozím protokolem, ale s normou se nesrovná
    assert "norma tuto úlohu nemá" in dialog.norm_warning.text()
    plan = dialog.plan()
    assert plan.norm == "Kontroly"
    assert {t.task: t.proto().name for t in plan.tasks}["phonation"] == "Fonace, kontroly"
    window._remember_birth_year(dialog.widget)
    assert window.assessment_page.norm_key_for("P-1") == str(settings.norms_dir() / "kontroly")
    # bez normy: výchozí přibalený protokol a volný jazyk
    assert dialog.select_norm("none")
    default = default_protocol("phonation", builtin_protocols())
    assert row.current_protocol().name == default.name and dialog.language.isEnabled()
    assert dialog.plan().norm == ""
