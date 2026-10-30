"""Stránka Normy: seznam a detail, export, import a smazání, napojení na Hodnocení."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from test_norms import controls_frame

from speechscope_app.backend import norms as norms_mod
from speechscope_app.backend import tasks
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
    assert window.assessment_page.norm_combo.count() == 3 + 1  # + „žádná norma“

    # smazání do Koše (v testu natrvalo)
    monkeypatch.setattr(norms_mod, "send_to_trash", shutil.rmtree)
    assert page.delete_current(confirm=False)
    assert page.count() == 2 and not imported.path.exists()
    assert window.assessment_page.norm_combo.count() == 2 + 1


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
    assert window.assessment_page.norm_combo.count() == 1 + 1
    runs = [r for r in window.results_page.history.all_runs() if r.norm]
    assert len(runs) == 2 and all(r.norm == str(plan.folder) for r in runs)
    history = window.results_page.history
    history.refresh()
    cards = [history.card(i) for i in range(history.count())]
    assert all(c.norm_pill.text() == "norma" and "kontroly normy" in c.detail.text() for c in cards)
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


def test_new_norm_wizard_with_custom_task(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    """Vlastní úloha v kroku 1: řádek s podtitulkem, dávka jako základní úloha, norma
    si klíč zapíše do tabulky kontrol i do norm.yaml a registr ji uloží pro příště."""
    data = tmp_path / "data"
    for folder in ("fonace", "obrazek"):
        (data / folder).mkdir(parents=True)
        for name in ("HC01_a.wav", "HC02_a.wav"):
            (data / folder / name).write_bytes(b"RIFF")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_NORMS)
    page = window.norms_page
    dialog = window.make_new_norm_dialog()
    qtbot.addWidget(dialog)
    dialog.name.setText("Obrázky")
    dialog.set_folder("phonation", data / "fonace")
    obrazek = tasks.CustomTask("popis-obrazku", "Popis obrázku", "phonation", "Fonace, základní")
    row = dialog.add_custom_task(obrazek)
    assert row.label.text() == "Popis obrázku" and "jako Fonace" in row.sub.text()
    assert row.current_protocol() is not None and row.current_protocol().task == "phonation"
    assert dialog.rows["popis-obrazku"] is row and row.remove.isVisibleTo(dialog)
    extra = dialog.add_custom_task(tasks.CustomTask("zpev", "Zpěv", "reading"))
    dialog.remove_custom_task("zpev")
    assert "zpev" not in dialog.rows and extra.folder.isHidden()
    dialog.set_folder("popis-obrazku", data / "obrazek")
    assert "úloh: 2" in dialog.hint.text()
    dialog.go_next()
    assert dialog.step() == 1 and dialog.table.rowCount() == 2
    dialog.set_age("HC01", 60)
    dialog.set_age("HC02", 70)
    dialog.go_next()
    assert dialog.batches.item(1, 0).text() == "Popis obrázku"
    plan = dialog.plan()
    assert [t.task for t in plan.tasks] == ["phonation", "popis-obrazku"]
    assert plan.tasks[1].proto().task == "phonation"  # knihovna dostane základní úlohu
    assert [t.key for t in plan.info.custom_tasks] == ["popis-obrazku"]
    assert settings.tasks_file().is_file() and tasks.registry().get("popis-obrazku") is not None

    assert window.start_norm(plan) == 2
    qtbot.waitUntil(lambda: (plan.folder / "norm.yaml").is_file(), timeout=30000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=10000)
    page.reload()
    norm = page.current()
    assert norm is not None and norm.tasks == ["phonation", "popis-obrazku"]
    assert norm.info.protocols["popis-obrazku"] == "Fonace, základní"
    assert norm.info.custom_tasks[0].name == "Popis obrázku"
    assert "Popis obrázku" in page.table.item(1, 0).text()
    # běh dávky je v historii pod vlastním jménem
    runs = window.results_page.history
    assert any("Popis obrázku" in runs.card(i).title.text() for i in range(runs.count()))


def test_norms_page_warns_about_changed_feature_versions(
    qtbot: QtBot, settings: AppSettings
) -> None:
    Norm(
        NormInfo(
            name="Stará",
            feature_versions={
                "phonation": {"acoustic.quality.cpp": "1.2"},
                "reading": {"acoustic.quality.cpp": "1.2", "acoustic.pitch.f0": "1.1"},
            },
        ),
        controls_frame(),
    ).save(settings.norms_dir() / "stara")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.norms_page
    assert page.current().info.name == "Stará"
    text = page.problems.text()  # faleška má CPP 1.3 a F0 1.1
    assert not page.problems.isHidden()
    assert "acoustic.quality.cpp 1.2 \u2192 1.3 (Fonace, Čtení)" in text
    assert "f0" not in text and "spočítat znovu" in text


def test_recompute_norm_reruns_batches_and_overwrites(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    from test_norm_build import make_plan

    plan = make_plan(tmp_path)
    plan.folder = settings.norms_dir() / "kontroly"
    plan.tasks = plan.tasks[:1]  # jen fonace, DDK v plánu nemá nahrávky
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_NORMS)
    page = window.norms_page
    assert window.start_norm(plan) == 1
    qtbot.waitUntil(lambda: (plan.folder / "norm.yaml").is_file(), timeout=30000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=10000)
    page.reload()
    first = page.current()
    assert first is not None and first.info.feature_versions["phonation"]
    assert page.recompute_btn.isEnabled() and "přepíše" in page.recompute_btn.toolTip()
    history = window.results_page.history
    history.refresh()
    assert len([r for r in history.all_runs() if r.norm]) == 1

    # přepočet: norma je zase rozpracovaná, stará zůstává, pak se přepíše
    assert window.recompute_norm(plan.folder, confirm=False) == 1
    assert page.current_plan() is not None and "přepočítává se" in page.card(0).pill.text()
    assert "Přepočet normy" in page.plan_header.subtitle.text()
    assert page.discard_btn.text() == "Zrušit přepočet…"
    assert window.assessment_page.norm_combo.count() == 1 + 1  # stará norma dál použitelná
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=30000)
    from speechscope_app.backend.norm_build import load_plan

    qtbot.waitUntil(lambda: not load_plan(plan.folder).recomputing, timeout=5000)
    page.reload()
    assert page.count() == 1 and page.current_plan() is None and page.current() is not None
    history.refresh()
    assert len([r for r in history.all_runs() if r.norm]) == 2
    assert not page.recompute_btn.toolTip().startswith("Chybí")

    # zrušení přepočtu bez běžící dávky: norma zůstane
    plan_now = norms_mod.Norm.load(plan.folder)
    load_plan(plan.folder).reset_for_recompute()
    page.reload()
    assert page.current_plan() is not None
    assert window.discard_norm(plan.folder, confirm=False)
    assert page.current_plan() is None and page.current().info.name == plan_now.info.name
    assert not load_plan(plan.folder).recomputing

    # chybějící nahrávka: tlačítko neaktivní s důvodem, přepočet odmítne
    (tmp_path / "fonace" / "HC01_a.wav").unlink()
    page.reload()
    assert not page.recompute_btn.isEnabled() and "Chybí 1" in page.recompute_btn.toolTip()
    assert window.recompute_norm(plan.folder, confirm=False) == 0

    # norma bez plánu (import): přepočítat nejde
    Norm(NormInfo(name="Importovaná"), controls_frame()).save(settings.norms_dir() / "import")
    page.reload()
    assert page.select(settings.norms_dir() / "import")
    assert not page.recompute_btn.isEnabled() and "uložené zdroje" in page.recompute_btn.toolTip()
