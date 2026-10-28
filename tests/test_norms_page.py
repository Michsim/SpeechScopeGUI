"""Stránka Normy: seznam a detail, export, import a smazání, napojení na Hodnocení."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from test_norms import controls_frame

from speechscope_app.backend import norms as norms_mod
from speechscope_app.backend.norms import Norm, NormInfo
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import PAGE_ENV, PAGE_NORMS, PAGE_PROTOCOLS, MainWindow


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


def test_norms_page_lists_exports_imports_and_deletes(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = controls_frame(n=12)  # málo kontrol: varování
    Norm(NormInfo(name="Malá norma", source="SAMI"), small).save(settings.norms_dir() / "mala")
    Norm(
        NormInfo(name="Zdravé kontroly", conditions="tichá místnost", created="2026-10-28"),
        controls_frame(),
    ).save(settings.norms_dir() / "zdrave")

    window = MainWindow(settings)
    qtbot.addWidget(window)
    assert window.nav_labels[PAGE_NORMS] == "Normy"
    assert PAGE_PROTOCOLS < PAGE_NORMS < PAGE_ENV  # dole mezi Protokoly a Prostředím
    window.nav.setCurrentRow(PAGE_NORMS)
    page = window.norms_page
    assert page.count() == 2 and page.empty.isHidden()
    assert page.card(0).title.text() == "Malá norma" and page.card(0).pill.text() == "varování: 2"
    assert page.card(1).pill.text() == "připravená"
    assert "60 osob · 40 až 85 let · úlohy: 2" in page.card(1).detail.text()
    assert "vznikla 28. 10. 2026" in page.card(1).detail.text()

    # detail: varování u malé normy, tabulka po úlohách
    assert page.current().info.name == "Malá norma"
    assert not page.problems.isHidden() and "jen 12 kontrol" in page.problems.text()
    assert page.table.rowCount() == 2 and page.table.item(0, 0).text().startswith("Fonace")
    assert page.table.item(0, 1).text() == "12" and page.table.item(0, 2).text() == "6 / 6"
    page.list.setCurrentRow(1)
    assert page.problems.isHidden() and page.header.title.text() == "Zdravé kontroly"
    assert "Podmínky: tichá místnost" in page.header.subtitle.text()
    assert page.table.item(0, 3).text() == "40 až 85" and page.histogram.bins()[0] == (40, 14)
    assert page.delete_btn.isEnabled() and page.export_btn.isEnabled()

    # export a import: kopie vedle, Hodnocení ji hned nabízí
    target = page.export_to(tmp_path / "zdrave.ssnorm")
    assert target is not None and target.is_file()
    imported = page.import_from(target)
    assert imported is not None and imported.path == settings.norms_dir() / "zdrave-kontroly"
    assert page.count() == 3 and page.current().path == imported.path
    assert window.assessment_page.norm_combo.count() == 3

    # smazání do Koše (v testu natrvalo)
    monkeypatch.setattr(norms_mod, "send_to_trash", shutil.rmtree)
    assert page.delete_current(confirm=False)
    assert page.count() == 2 and not imported.path.exists()
    assert window.assessment_page.norm_combo.count() == 2


def test_new_norm_wizard_runs_batches_and_builds_norm(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "kontroly"
    for folder, names in (
        ("fonace", ["HC01_a.wav", "HC02_a.wav", "HC03_a.wav"]),
        ("ddk", ["HC01_b.wav", "HC02_b.wav"]),
    ):
        (data / folder).mkdir(parents=True)
        for name in names:
            (data / folder / name).write_bytes(b"RIFF")
    (data / "vek.csv").write_text("subject;age;sex\nHC01;64;m\nHC02;70;f\n", encoding="utf-8")
    before = {p.name for p in data.rglob("*")}

    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_NORMS)
    page = window.norms_page
    assert page.count() == 0 and page.new_btn.isEnabled()

    dialog = window.make_new_norm_dialog()
    qtbot.addWidget(dialog)
    assert dialog.step() == 0 and not dialog.next_btn.isEnabled()  # bez jména a složek
    dialog.name.setText("Zdravé kontroly")
    dialog.set_folder("phonation", data / "fonace")
    dialog.set_folder("ddk", data / "ddk")
    assert dialog.rows["phonation"].count.text() == "3 ✓"
    assert not dialog.rows["phonation"].protocol.isEnabled()  # klinik: jen přibalený
    assert dialog.next_btn.isEnabled() and "nahrávek: 5" in dialog.hint.text()
    dialog.go_next()
    assert dialog.step() == 1 and dialog.table.rowCount() == 3
    assert "HC01_a.wav → HC01" in dialog.example.text()
    assert dialog.load_controls_csv(data / "vek.csv") == 2
    dialog.set_age("HC03", 58)
    controls = dialog.controls()
    assert controls["HC01"].age == 64 and controls["HC02"].sex == "f"
    assert controls["HC03"].age == 58 and controls["HC03"].sex == ""
    assert "chybí pohlaví" in dialog.table.item(2, 4).text()
    assert "bez DDK" in dialog.table.item(2, 4).text()
    dialog.go_next()
    assert dialog.step() == 2 and dialog.batches.rowCount() == 2
    assert not dialog.warnings.isHidden() and "Čtení se vynechá" in dialog.warnings.text()
    plan = dialog.plan()
    assert [t.task for t in plan.tasks] == ["phonation", "ddk"]

    # Spustit: dávky do fronty, rozpracovaná norma na stránce Normy
    assert window.start_norm(plan) == 2
    assert page.count() == 1 and page.current_plan() is not None
    assert "počítá se" in page.card(0).pill.text()
    qtbot.waitUntil(lambda: (plan.folder / "norm.yaml").is_file(), timeout=30000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=10000)
    page.reload()
    assert page.count() == 1 and page.current() is not None
    norm = page.current()
    assert norm.subjects == 3 and norm.tasks == ["ddk", "phonation"]
    assert norm.info.protocols == {"phonation": "Fonace, základní", "ddk": "DDK, základní"}
    people = norm.controls.drop_duplicates("subject").set_index("subject")
    assert people.loc["HC02", "age"] == 70 and people.loc["HC02", "sex"] == "f"
    assert window.assessment_page.norm_combo.count() == 1
    runs = [r for r in window.results_page.history.all_runs() if r.norm]
    assert len(runs) == 2 and all(r.norm == str(plan.folder) for r in runs)
    window.assessment_page.refresh()
    assert window.assessment_page.count() == 0  # kontroly normy nejsou pacienti
    assert {p.name for p in data.rglob("*")} == before  # do složek s nahrávkami nic


def test_pending_norm_build_resume_and_discard(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_norm_build import make_plan

    plan = make_plan(tmp_path)
    plan.folder = settings.norms_dir() / "kontroly"
    plan.save()
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_NORMS)
    page = window.norms_page
    assert page.count() == 1 and page.current_plan() is not None
    assert page.card(0).pill.text() == "dávky 0 z 2" and not page.plan_detail.isHidden()
    assert page.plan_table.rowCount() == 2 and page.plan_table.item(0, 3).text() == "čeká"
    assert not page.build_btn.isEnabled() and page.resume_btn.isEnabled()
    assert not window.build_norm(plan.folder)  # nic hotového

    monkeypatch.setattr("speechscope_app.ui.main_window.send_to_trash", shutil.rmtree)
    assert window.discard_norm(plan.folder, confirm=False)
    assert page.count() == 0 and not plan.folder.exists()


def test_norms_page_empty(qtbot: QtBot, settings: AppSettings) -> None:
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_NORMS)
    page = window.norms_page
    assert page.count() == 0 and not page.empty.isHidden() and page.detail.isHidden()
    assert "stránce Normy" in window.assessment_page.no_norm.text()
