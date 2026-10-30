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
from speechscope_app.backend.assessments import AssessmentStore
from speechscope_app.backend.catalog import Catalog
from speechscope_app.backend.history import RunGroup, read_group, read_run
from speechscope_app.backend.norm_build import ERROR, OK, TaskPlan, default_protocol
from speechscope_app.backend.protocol import builtin_protocols
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
    assert not dialog.next_btn.isEnabled()
    dialog.set_folder("phonation", data)  # manifest rozdělí úlohy a vyplní pacienta
    assert dialog.rows["phonation"].count.text() == "2 ✓"
    assert dialog.rows["ddk"].count.text() == "1 ✓" and dialog.rows["ddk"].folder.text()
    assert dialog.rows["reading"].count.text() == "–"
    assert dialog.subject.text() == "RBD239" and dialog.age.text() == "65"
    assert dialog.sex.currentData() == "m"
    assert dialog.date.date().toString("yyyy-MM-dd") == "2026-10-28"
    assert not dialog.prefill_note.isHidden() and "manifest.csv" in dialog.prefill_note.text()
    assert dialog.next_btn.isEnabled() and "nahrávek: 3" in dialog.hint.text()
    dialog.go_next()
    assert dialog.step() == 1 and dialog.batches.rowCount() == 2
    assert "Čtení" in dialog.warnings.text() and "Spontánní řeč" in dialog.warnings.text()
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
    builder.subject.setText("P1")
    builder.set_folder("phonation", data)
    assert builder.rows["phonation"].count.text() == "1 ✓" and builder.next_btn.isEnabled()
    builder.go_next()
    assert builder.step() == 1 and builder.batches.rowCount() == 1
    builder.go_next()  # Spustit
    assert window.nav.currentRow() == PAGE_RUN and home.step() == 0 and home.builder is None
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
    assert dialog.step() == 0 and dialog.widget.step() == 0 and not dialog.next_btn.isEnabled()


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
    dialog.set_folder("phonation", data)
    assert dialog.is_multi() and dialog.person_box.isHidden() and not dialog.people_box.isHidden()
    assert dialog.people_table.rowCount() == 4 and dialog.template_btn.isEnabled()
    assert (
        dialog.people_summary.text()
        == "2 pacienti · vyšetření: 3 · nahrávek: 4 · bez pacienta nebo data: 1"
    )
    assert [dialog.people_table.item(r, 0).text() for r in range(4)] == [
        "p1_2025.wav",
        "p1_2026.wav",
        "p2_bezdata.wav",
        "p2_2026.wav",
    ]
    assert (
        dialog.people_table.item(2, 3).text() == ""
        and dialog.people_table.item(3, 3).text() == "2026-10-14"
    )
    assert [dialog.people_table.item(r, 1).text() for r in range(4)] == ["Fonace"] * 4
    headers = [dialog.people_table.horizontalHeaderItem(c).text() for c in range(6)]
    assert headers == ["soubor", "úloha", "pacient", "datum vyšetření", "věk", "pohlaví"]
    assert dialog.next_btn.isEnabled() and "2 pacienti · vyšetření: 3" in dialog.hint.text()
    # šablona manifestu kam uživatel ukáže; do složky s nahrávkami nic
    template = dialog.save_template(tmp_path / "sablona.csv")
    assert template is not None and template.read_text(encoding="utf-8-sig").startswith(
        "path;subject;visit;task;age;sex"
    )
    assert "p2_bezdata.wav;;;phonation;;" in template.read_text(encoding="utf-8-sig")
    assert {p.name for p in data.rglob("*")} == before
    # doplněné datum jen pro tento výpočet
    dialog.set_person((data / "p2_bezdata.wav").resolve(), visit="2026-10-20")
    assert dialog.people_summary.text() == "2 pacienti · vyšetření: 4 · nahrávek: 4"
    dialog.go_next()
    assert dialog.step() == 1 and dialog.batches.rowCount() == 3  # fonace × 3 různá data
    assert dialog.summary_title.text() == "2 pacienti · 4 vyšetření"
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
