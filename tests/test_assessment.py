"""Stránka Hodnocení: ručně založená hodnocení, pavouk, strom, úprava, zkratka z Výsledků."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot
from test_norms import HNR, JIT, RATE, controls_frame
from test_visits import make_run

from speechscope_app.backend import dimensions as evaluate_mod
from speechscope_app.backend import tasks
from speechscope_app.backend.assessment_build import Patient, new_plan
from speechscope_app.backend.dimensions import Dimension, Item
from speechscope_app.backend.history import write_run_file
from speechscope_app.backend.norm_build import TaskPlan, default_protocol
from speechscope_app.backend.norms import BOTH, LOWER, Norm, NormInfo
from speechscope_app.backend.protocol import builtin_protocols
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import PAGE_ASSESSMENT, PAGE_RESULTS, MainWindow
from speechscope_app.ui.pages.assessment import PendingCard, format_number, worst_band
from speechscope_app.ui.widgets.spider_chart import Axis, SpiderChart, format_sd

PAUSE = "acoustic.timing.pauses.median"  # jediná položka dimenze Pauzy


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setattr(
        "speechscope_app.backend.settings.app_data_dir", lambda: tmp_path / "appdata"
    )
    s = AppSettings(tmp_path / "settings.ini")
    s.use_fake_library = True
    s.models_dir = tmp_path / "models"
    s.work_root = tmp_path / "work"
    s.advanced = True
    return s


def make_runs(root: Path) -> None:
    make_run(
        root,
        "2026-10-14_10-12-00_fonace",
        [
            {
                "file": "P-017_fonace.wav",
                "path": str(root / "P-017_fonace.wav"),
                "task": "phonation",
                "subject": "P-017",
                "age": 64,
                "sex": "m",
                "visit": "2026-10-14",
                JIT: 3.0,
                HNR: 12.0,
            },
            {
                "file": "P-018_fonace.wav",
                "path": str(root / "P-018_fonace.wav"),
                "task": "phonation",
                "subject": "P-018",
                "age": 70,
                "sex": "f",
                "visit": "2026-10-14",
                JIT: 0.8,
                HNR: 21.0,
            },
        ],
    )
    make_run(
        root,
        "2026-10-14_10-40-00_cteni",
        [
            {
                "file": "P-017_cteni.wav",
                "path": str(root / "P-017_cteni.wav"),
                "task": "reading",
                "subject": "P-017",
                "age": 64,
                "sex": "m",
                "visit": "2026-10-14",
                RATE: 2.5,
                PAUSE: 1.6,
            }
        ],
    )
    make_run(  # bez manifestu: samostatné vyšetření podle souboru
        root,
        "2026-10-13_09-00-00_ddk",
        [
            {
                "file": "p03_short.wav",
                "path": str(root / "p03_short.wav"),
                "task": "ddk",
                "acoustic.ddk.rate": 5.5,
            }
        ],
    )


def save_norm(folder: Path) -> Norm:
    controls = controls_frame()
    reading = controls["task"] == "reading"
    controls.loc[reading, PAUSE] = [0.3 + 0.01 * i for i in range(int(reading.sum()))]  # 0,3 až 0,9
    norm = Norm(
        NormInfo(name="Testovací norma", names={"en": "Test norm"}, source="SAMI", language="cs"),
        controls,
    )
    norm.save(folder / "testovaci-norma")
    return norm


def recording(page, file: str):  # noqa: ANN001, ANN201
    return next(r for r in page.recordings_pool() if r.file == file)


def test_manual_assessment_create_score_edit_and_delete(
    qtbot: QtBot, settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    assert window.nav_labels[PAGE_ASSESSMENT] == "Pacienti"

    # nic se neskládá samo: bez uložených hodnocení je seznam prázdný
    window.nav.setCurrentRow(PAGE_ASSESSMENT)
    assert page.count() == 0 and not page.empty.isHidden()
    assert not page.edit_btn.isEnabled() and not page.delete_btn.isEnabled()

    # tlačítko Nové hodnocení otevře prázdný dialog (clicked nesmí poslat checked do initial)
    opened: list = []

    def fake_exec(dialog) -> int:  # noqa: ANN001
        opened.append(dialog)
        return QDialog.DialogCode.Rejected

    choice_dialog = "speechscope_app.ui.pages.assessment.NewAssessmentChoiceDialog"
    with monkeypatch.context() as patch:  # undo() by vrátilo i podvržené AppData z fixtury
        patch.setattr("speechscope_app.ui.pages.assessment.AssessmentDialog.exec", fake_exec)
        patch.setattr(choice_dialog + ".exec", lambda self: QDialog.DialogCode.Accepted)
        patch.setattr(choice_dialog + ".choice", lambda self: "runs")
        page.new_btn.click()
    assert len(opened) == 1 and opened[0].summary.text() == "" and page.count() == 0
    # volba „z nahrávek“ jen požádá hlavní okno o průvodce
    asked: list = []
    page.new_from_recordings_requested.connect(lambda: asked.append(True))
    with monkeypatch.context() as patch:
        patch.setattr(choice_dialog + ".exec", lambda self: QDialog.DialogCode.Accepted)
        patch.setattr(choice_dialog + ".choice", lambda self: "recordings")
        patch.setattr(  # hlavní okno by jinak otevřelo modální průvodce
            "speechscope_app.ui.main_window.AssessmentFromRecordingsDialog.exec",
            lambda self: QDialog.DialogCode.Rejected,
        )
        assert page.new_assessment() is None
    assert asked == [True] and len(opened) == 1

    # nové hodnocení: první nahrávka předvyplní pacienta z manifestu
    dialog = page.make_dialog()
    qtbot.addWidget(dialog)
    assert not dialog.ok_btn.isEnabled()
    assert dialog.add_recording(recording(page, "P-017_fonace.wav"))
    assert dialog.subject.text() == "P-017" and dialog.age.text() == "64"
    assert (
        dialog.sex.currentData() == "m"
        and dialog.date.date().toString("yyyy-MM-dd") == "2026-10-14"
    )
    assert "Z manifestu" in dialog.prefill_note.text()
    assert not dialog.add_recording(recording(page, "P-017_fonace.wav"))  # dvakrát ne
    # výběr čtení: podle manifestu pacienta nahoře
    pick = dialog.pick_dialog("reading")
    qtbot.addWidget(pick)
    assert pick.chosen() is not None and pick.chosen().file == "P-017_cteni.wav"
    assert "podle manifestu" in pick.list.item(0).text()
    dialog.add_recording(pick.chosen())
    # fonace jiného pacienta je až mezi ostatními (bez hledání)
    pick = dialog.pick_dialog("phonation")
    qtbot.addWidget(pick)
    assert pick.chosen() is None
    pick.search.clear()
    assert pick.chosen() is not None and pick.chosen().file == "P-018_fonace.wav"
    assert dialog.ok_btn.isEnabled() and "nahrávek: 2" in dialog.summary.text()
    saved = page.save_from(dialog)
    assert saved is not None and page.count() == 1

    # vyhodnocení bez normy, pak s normou
    assert page.card(0).title.text() == "P-017" and page.card(0).pill.text() == "bez normy"
    assert page.header.title.text() == "P-017"
    assert "muž, 64 let · vyšetření 14. 10. 2026 · Fonace, Čtení" == page.header.subtitle.text()
    assert page.recordings.rowCount() == 2 and not page.image_btn.isEnabled()
    save_norm(settings.norms_dir())
    page.reload_norms()
    ev = page.evaluation()
    assert ev is not None and [r.dimension.key for r in ev.shown] == ["voice", "pauses"]
    assert worst_band(ev) == "bad" and page.header.property("role") == "missing"
    assert page.card(0).pill.text() == "mimo normu"
    axes = page.chart.axes()
    assert [a.label for a in axes] == ["Kvalita hlasu", "Pauzy"] and axes[0].band == "bad"
    assert "Syntax: potřebuje úlohu Spontánní řeč" in page.missing_label.text()
    assert "Slovní zásoba: potřebuje úlohu Spontánní řeč" in page.missing_label.text()
    assert page.tree.topLevelItemCount() == 8
    voice = page.tree.topLevelItem(0)
    assert voice.text(0) == "Kvalita hlasu" and voice.childCount() == 2 and voice.isExpanded()
    hnr = voice.child(0)  # HNR a CPP z fonace; CPP ze spontánní řeči tu není
    assert hnr.text(1) == "Fonace" and hnr.text(2).startswith("12,0")
    assert "Fonace: " in page.selection_label.text() and "jen muži" in page.selection_label.text()
    assert "60 osob" in page.about.text() and page.image_btn.isEnabled()
    assert page.recordings.item(0, 3).text() == ""  # norma protokoly nezná
    page.same_sex.setChecked(False)
    page.window_combo.setCurrentIndex(3)  # bez omezení
    assert page.evaluation().selections["phonation"].n == 60
    out = page.save_image(settings.work_root / "pavouk.png")
    # tlačítko Uložit obrázek… nesmí dostat `checked` jako cestu (ukládalo soubor „False“)
    target = settings.work_root / "z-tlacitka.png"
    monkeypatch.setattr(
        "speechscope_app.ui.pages.assessment.QFileDialog.getSaveFileName",
        lambda *_a, **_k: (str(target), "Obrázek PNG (*.png)"),
    )
    page.image_btn.click()
    assert target.is_file() and not Path("False").exists()
    assert out is not None and out.is_file() and out.stat().st_size > 0

    # úprava: druhá fonace se zprůměruje
    edit = page.make_dialog(assessment=page.current_assessment())
    qtbot.addWidget(edit)
    assert edit.boxes["phonation"].list.count() == 1 and edit.subject.text() == "P-017"
    edit.add_recording(recording(page, "P-018_fonace.wav"))
    assert "zprůměrují" in edit.boxes["phonation"].note.text()
    page.save_from(edit)
    assert page.count() == 1 and page.current_visit().count("phonation") == 2
    assert "Fonace (2)" in page.header.subtitle.text()
    assert "zprůměruje" in page.recordings_note.text()

    # z Výsledků: dialog s nahrávkou předvyplněnou, po uložení Hodnocení
    monkeypatch.setattr(
        "speechscope_app.ui.pages.assessment.AssessmentDialog.exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    window.nav.setCurrentRow(PAGE_RESULTS)
    assert window.show_assessment(settings.work_root / "2026-10-14_10-12-00_fonace", 1)
    assert window.nav.currentRow() == PAGE_ASSESSMENT and page.count() == 2
    assert page.current_assessment().subject == "P-018"
    assert not window.show_assessment(settings.work_root / "neexistuje", 0)

    # přejmenovaný běh: odkazy hodnocení jdou za ním
    old = settings.work_root / "2026-10-14_10-40-00_cteni"
    new = settings.work_root / "2026-10-14_10-40-00_cteni-p017"
    old.rename(new)
    page.relocate(old, new)
    assert page.select_assessment(saved.id) and page.current_visit().count("reading") == 1
    stored = next(a for a in page.assessments() if a.id == saved.id)
    assert {r.run for r in stored.recordings} >= {new.name}

    # smazání
    assert page.delete_current(confirm=False)
    assert page.count() == 1 and page.current_assessment().subject == "P-018"

    # Výsledky: pravé tlačítko „Otevřít v Hodnocení“ posílá složku a řádek
    results = window.results_page
    window.nav.setCurrentRow(PAGE_RESULTS)
    assert results.history.select_dir(settings.work_root / "2026-10-14_10-12-00_fonace")
    results.show_run(results.history.current())
    got: list = []
    results.assess_requested.connect(lambda d, r: got.append((d, r)))
    results.assess_row(1)
    assert got == [(settings.work_root / "2026-10-14_10-12-00_fonace", 1)]


def test_norm_protocol_mismatch_is_shown(qtbot: QtBot, settings: AppSettings) -> None:
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    norm = save_norm(settings.norms_dir())
    norm.info.protocols = {"phonation": "Fonace, základní"}
    norm.save(norm.path)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    dialog = page.make_dialog(initial=recording(page, "P-017_fonace.wav"))
    qtbot.addWidget(dialog)
    item = dialog.boxes["phonation"].list.item(0).text()
    assert "jiný protokol než norma" in item  # běh nemá protocol.yaml Fonace
    page.save_from(dialog)
    assert page.recordings.item(0, 3).text() == "jiný protokol než norma"
    assert "jen orientační" in page.recordings_note.text()


def test_recompute_with_pending_plan_in_list(qtbot: QtBot, settings: AppSettings) -> None:
    """Rozpracované hodnocení v seznamu nesmí shodit přepočet štítků (PendingCard nemá visit)."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    save_norm(settings.norms_dir())
    proto = default_protocol("phonation", builtin_protocols()).to_dict()
    new_plan(
        settings.assessments_dir(),
        Patient("P-099", 70, "f", "2026-10-20"),
        "cs",
        [TaskPlan("phonation", proto, [settings.work_root / "x.wav"])],
    ).save()
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    window.nav.setCurrentRow(PAGE_ASSESSMENT)
    dialog = page.make_dialog(initial=recording(page, "P-017_fonace.wav"))
    qtbot.addWidget(dialog)
    assert page.save_from(dialog) is not None
    assert page.count() == 2 and isinstance(page.card(0), PendingCard)
    page.select_row(1)
    assert page.card(1).pill.text() == "mimo normu"
    page.same_sex.setChecked(False)  # přepočet projde všechny karty včetně rozpracované
    page.window_combo.setCurrentIndex(3)
    assert page.evaluation() is not None and page.card(1).pill.text() == "mimo normu"
    assert page.card(0).title.text() == "P-099"
    page.select_row(0)
    assert page.current_plan() is not None and not page.plan_detail.isHidden()


def test_tree_marks_side_of_both_direction_item(qtbot: QtBot, settings: AppSettings) -> None:
    """Položka se směrem both ukáže ve stromu, na kterou stranu pacient vybočil."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    save_norm(settings.norms_dir())
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    page._dimensions = [Dimension("tempo", "Tempo", [Item(RATE, ("reading",), BOTH)])]
    dialog = page.make_dialog(initial=recording(page, "P-017_cteni.wav"))
    qtbot.addWidget(dialog)
    assert page.save_from(dialog) is not None
    top = page.tree.topLevelItem(0)
    assert top.text(0) == "Tempo" and top.childCount() == 1
    child = top.child(0)
    assert child.text(4).endswith("▼")  # tempo 2,5 je pod kontrolami (4,2)
    assert "pod průměrem kontrol" in child.toolTip(4)
    assert page.evaluation().results[0].band == "bad"
    # šipka i u osy pavouka a ve štítku hlavičky
    assert page.chart.axes()[0].label == "Tempo ▼"
    assert page.header._pill_widgets == []  # štítky dimenzí v hlavičce nejsou
    assert page.chart.axes()[0].label.startswith("Tempo ▼")  # šipka zůstává v pavouku


def test_click_does_not_reevaluate_other_cards(
    qtbot: QtBot, settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Štítky karet závisí jen na normě, okně a pohlaví: kliknutí nepočítá ostatní pacienty."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    save_norm(settings.norms_dir())
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    calls: list[str] = []
    real = evaluate_mod.evaluate

    def counted(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append(args[1] and next(iter(args[1])))
        return real(*args, **kwargs)

    monkeypatch.setattr("speechscope_app.ui.pages.assessment.evaluate", counted)
    dialog = page.make_dialog(initial=recording(page, "P-017_fonace.wav"))
    qtbot.addWidget(dialog)
    for subject in ("P-A", "P-B", "P-C"):
        dialog.subject.setText(subject)
        assert page.save_from(dialog) is not None
    assert page.count() == 3
    page.flush_badges()
    calls.clear()
    page.select_row(1)
    page.select_row(2)
    assert calls == []  # vše už je v cache z uložení
    page.same_sex.setChecked(False)
    assert len(calls) == 1 and page.badges_pending() == 0  # okno a pohlaví platí per pacient
    page._make_default()  # teprve „výchozí pro všechny“ přepočítá ostatní, a to líně
    assert len(calls) == 1 and page.badges_pending() == 2
    page.flush_badges()
    assert len(calls) == 3  # každého jednou
    calls.clear()
    page.select_row(0)
    assert calls == []
    page.refresh()
    page.flush_badges()
    assert calls == []  # tabulky se nezměnily: otisk sedí, nic se nepřepočítává
    # změněná tabulka běhu přepočítá jen pacienty, kteří na ni ukazují
    run = settings.work_root / "2026-10-14_10-12-00_fonace"
    frame = pd.read_csv(run / "features.csv")
    frame.loc[0, HNR] = 25.0
    frame.to_csv(run / "features.csv", index=False)
    page.refresh()
    page.flush_badges()
    assert len(calls) == 3  # všichni tři ukazují na touž tabulku


def test_spider_chart_and_formatting(qtbot: QtBot) -> None:
    chart = SpiderChart()
    qtbot.addWidget(chart)
    chart.resize(400, 400)
    chart.set_axes([Axis("A", 2.3, "bad"), Axis("B", -0.4, "ok"), Axis("C", 1.2, "warn")])
    assert not chart.grab().isNull()
    chart.set_axes([Axis("Jen jedna", 1.5, "warn")])  # jedna osa = pruh
    assert not chart.grab().isNull() and len(chart.axes()) == 1
    chart.set_axes([])
    assert not chart.grab().isNull()
    assert (
        format_sd(2.34) == "+2,3 SD"
        and format_sd(-0.04) == "0,0 SD"
        and format_sd(-1.2) == "-1,2 SD"
    )
    assert format_number(123.4) == "123" and format_number(12.34) == "12,3"
    assert format_number(1.234) == "1,23" and format_number(None) == "–"


def test_column_labels_for_single_value_features(fake_library) -> None:  # noqa: ANN001
    """acoustic.ddk.rate má jedinou hodnotu: popis z feature, ne „rate acoustic.ddk.rate“."""
    from speechscope_app.ui.recording_detail import column_index

    index = column_index(fake_library.features())
    rate = index["acoustic.ddk.rate"]
    assert rate.short == "rate" and rate.description.startswith("Tempo DDK")
    jitter = index["acoustic.quality.perturbation.jitter_local"]
    assert jitter.short == "jitter_local" and jitter.description.startswith("Jitter")


def test_spider_orientation_switch(qtbot: QtBot) -> None:
    from speechscope_app.ui.widgets.spider_chart import INWARD, OUTWARD

    chart = SpiderChart()
    qtbot.addWidget(chart)
    chart.resize(400, 400)
    chart.set_axes([Axis("A", 2.3, "bad"), Axis("B", -1.4, "ok"), Axis("C", 1.2, "warn")])
    assert chart.orientation() == OUTWARD and not chart.grab().isNull()
    chart.set_orientation(INWARD)
    assert chart.orientation() == INWARD and not chart.grab().isNull()
    chart.set_orientation("nesmysl")
    assert chart.orientation() == INWARD


def test_custom_task_from_norm_in_dialog_and_tree(qtbot: QtBot, settings: AppSettings) -> None:
    """Vlastní úloha z normy: dialog ji nabídne, nahrávka jejího běhu se k ní přiřadí
    a ve stromu se srovnává s kontrolami téže úlohy přes základní úlohu dimenze."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    run = settings.work_root / "2026-10-14_11-00-00_obrazek"
    run.mkdir()
    (run / "speechscope.log").write_text("", encoding="utf-8")
    write_run_file(run, status="ok", kind="extract", protocol="P", total=1, task="popis-obrazku")
    pd.DataFrame(
        [
            {
                "file": "P-017_obrazek.wav",
                "path": str(settings.work_root / "P-017_obrazek.wav"),
                "task": "reading",
                "subject": "P-017",
                "age": 64,
                "sex": "m",
                RATE: 3.1,
            }
        ]
    ).to_csv(run / "features.csv", index=False)
    obrazek = tasks.CustomTask("popis-obrazku", "Popis obrázku", "reading", "Čtení, akustika")
    controls = controls_frame()
    extra = controls[controls["task"] == "reading"].copy()
    extra["task"] = "popis-obrazku"
    Norm(
        NormInfo(name="S obrázkem", custom_tasks=[obrazek], protocols={"popis-obrazku": "P"}),
        pd.concat([controls, extra]),
    ).save(settings.norms_dir() / "obrazek")

    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_ASSESSMENT)
    page = window.assessment_page
    page._dimensions = [Dimension("tempo", "Tempo", [Item(RATE, ("reading",), LOWER)])]
    dialog = page.make_dialog(initial=recording(page, "P-017_obrazek.wav"))
    qtbot.addWidget(dialog)
    assert list(dialog.boxes) == ["phonation", "ddk", "spontaneous", "reading", "popis-obrazku"]
    assert dialog.boxes["popis-obrazku"].label.text() == "Popis obrázku"
    assert dialog.boxes["popis-obrazku"].list.count() == 1  # nahrávka běhu vlastní úlohy
    dialog.add_recording(recording(page, "P-017_cteni.wav"))
    assert page.save_from(dialog) is not None
    ev = page.evaluation()
    assert ev is not None and [i.task for i in ev.results[0].items] == ["reading", "popis-obrazku"]
    assert all(i.score is not None for i in ev.results[0].items)
    top = page.tree.topLevelItem(0)
    assert top.text(1) == "Čtení, Popis obrázku" and top.child(1).text(1) == "Popis obrázku"
    assert "Popis obrázku: " in page.selection_label.text()
    cells = [page.recordings.item(r, 0).text() for r in range(page.recordings.rowCount())]
    assert "Popis obrázku" in cells and page.card(0).detail.text().endswith("Popis obrázku")


def test_changed_feature_version_is_shown(qtbot: QtBot, settings: AppSettings) -> None:
    """Běh pacienta s jinou verzí feature než kontroly normy: ⚠ ve stromu, tabulka, poznámka."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    pert = "acoustic.quality.perturbation"
    write_run_file(
        settings.work_root / "2026-10-14_10-12-00_fonace",
        status="ok",
        kind="extract",
        protocol="P",
        total=2,
        feature_versions={pert: "1.1", "acoustic.quality.cpp": "1.3"},
    )
    norm = save_norm(settings.norms_dir())
    norm.info.feature_versions = {"phonation": {pert: "1.0", "acoustic.quality.cpp": "1.3"}}
    norm.save(norm.path)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    dialog = page.make_dialog(initial=recording(page, "P-017_fonace.wav"))
    qtbot.addWidget(dialog)
    page.save_from(dialog)
    assert page.recordings.item(0, 3).text() == "jiná verze feature"
    assert page.recordings.item(0, 3).toolTip() == f"{pert} 1.1 (norma 1.0)"
    note = page.recordings_note.text()
    assert f"{pert} 1.1 (norma 1.0)" in note and "spočítat znovu" in note
    voice = page.tree.topLevelItem(0)
    hnr = voice.child(0)
    assert hnr.text(0).endswith("\u26a0") and "1.1 u pacienta, 1.0 u kontrol" in hnr.toolTip(0)
    assert hnr.text(4)  # z-skóre se přesto počítá


def test_patient_card_groups_visits_and_new_visit(qtbot: QtBot, settings: AppSettings) -> None:
    """Karta vlevo je pacient; víc vyšetření = řada dat pod hlavičkou, nové vyšetření předvyplní."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    save_norm(settings.norms_dir())
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    first = page.save_from(page.make_dialog(initial=recording(page, "P-017_fonace.wav")))
    assert first is not None and page.count() == 1 and not page.visit_bar.isHidden()
    assert len(page.visit_buttons) == 1 and page.list_title.text() == "Pacienti (1)"

    # druhé vyšetření téhož pacienta: dialog s předvyplněným pacientem, jiné datum a nahrávka
    dialog = page.make_dialog(template=first)
    qtbot.addWidget(dialog)
    assert dialog.subject.text() == "P-017" and dialog.age.text() == "64"
    assert dialog.sex.currentData() == "m" and dialog.windowTitle() == "Nové hodnocení"
    assert all(box.list.count() == 0 for box in dialog.boxes.values())
    dialog.add_recording(recording(page, "P-017_cteni.wav"))
    dialog._set_date("2026-04-12")
    second = page.save_from(dialog)
    assert second is not None and second.id != first.id and second.subject == "P-017"
    assert page.count() == 1 and page.card(0).title.text() == "P-017"
    assert "2 vyšetření" in page.card(0).detail.text()
    assert "12. 4. 2026 → 14. 10. 2026" in page.card(0).detail.text()
    assert page.current_assessment().id == second.id  # uložené vyšetření je vybrané
    assert not page.visit_bar.isHidden() and len(page.visit_buttons) == 2
    assert [b.text() for b in page.visit_buttons] == ["14. 10. 2026", "12. 4. 2026"]
    assert page.visit_buttons[1].property("role") == "primary"
    assert [e[0].id for e in page.patient_entries()] == [first.id, second.id]

    # přepnutí v řadě vyšetření a výběr podle id
    page.visit_buttons[0].click()
    assert page.current_assessment().id == first.id and page.current_visit().count("phonation") == 1
    assert page.visit_buttons[0].property("role") == "primary"
    assert page.select_assessment(second.id) and page.current_assessment().id == second.id
    assert page.current_visit().count("reading") == 1
    # štítek karty patří poslednímu vyšetření, i když je vybrané starší
    page.flush_badges()
    assert page.card(0).pill.text() == "mimo normu"
    page.refresh()
    assert page.current_assessment().id == second.id  # obnova drží vybrané vyšetření

    # druhý pacient = druhá karta; hledání filtruje pacienty
    page.save_from(page.make_dialog(initial=recording(page, "P-018_fonace.wav")))
    assert page.count() == 2 and page.list_title.text() == "Pacienti (2)"
    page.search.setText("018")
    assert page.count() == 1 and page.card(0).title.text() == "P-018"
    page.search.clear()
    # smazání jednoho vyšetření nechá pacienta s tím druhým
    assert page.select_assessment(second.id) and page.delete_current(confirm=False)
    assert page.count() == 2 and page.select_assessment(first.id)
    assert len(page.visit_buttons) == 1 and page.patient_entries()[0][0].id == first.id


def test_new_visit_offers_recordings_with_prefilled_patient(
    qtbot: QtBot, settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nové vyšetření… na Pacientech: z nahrávek otevře průvodce s pacientem, věkem a pohlavím."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    first = page.save_from(page.make_dialog(initial=recording(page, "P-017_fonace.wav")))
    assert first is not None
    got: list = []
    page.new_visit_from_recordings_requested.connect(got.append)
    opened: list = []

    def fake_exec(self):  # noqa: ANN001, ANN202
        opened.append((self.subject.text(), self.age.text(), self.sex.currentData()))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(
        "speechscope_app.ui.assessment_build_dialog.AssessmentFromRecordingsDialog.exec", fake_exec
    )
    monkeypatch.setattr(
        "speechscope_app.ui.pages.assessment.NewAssessmentChoiceDialog.exec",
        lambda self: QDialog.DialogCode.Accepted,
    )  # výchozí volba je „z nahrávek“: hlavní okno otevře průvodce s pacientem
    assert page.new_visit() is None and [a.id for a in got] == [first.id]
    assert opened == [("P-017", "64", "m")]
    assert window.new_visit_from_recordings(first) == 0 and len(opened) == 2


def test_trend_view_for_patient_with_two_visits(qtbot: QtBot, settings: AppSettings) -> None:
    """Vývoj v čase: od dvou vyšetření s datem a normou mřížka grafů a tabulka, export PNG."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    save_norm(settings.norms_dir())
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    first = page.save_from(page.make_dialog(initial=recording(page, "P-017_fonace.wav")))
    assert first is not None and not page.view_bar.isHidden()
    assert not page.view_trend_btn.isEnabled() and page.view() == "visit"  # jedno vyšetření
    dialog = page.make_dialog(template=first)
    qtbot.addWidget(dialog)
    dialog.add_recording(recording(page, "P-017_cteni.wav"))
    dialog._set_date("2025-10-14")
    second = page.save_from(dialog)
    assert second is not None and page.view_trend_btn.isEnabled()
    # přepínač je nad řadou vyšetření, takže při přepnutí neposkočí
    column = page.view_bar.parentWidget().layout()
    order = [column.indexOf(w) for w in (page.view_bar, page.trend_bar, page.visit_bar)]
    assert order == sorted(order)
    page.set_view("trend")
    assert page.view() == "trend" and not page.trend_panel.isHidden() and page.middle.isHidden()
    assert page.bottom.isHidden() and not page.trend_bar.isHidden()
    timeline = page.timeline()
    assert timeline is not None and [v.date for v in timeline.visits] == [
        "2025-10-14",
        "2026-10-14",
    ]
    shown = timeline.shown_rows()
    assert [r.dimension.key for r in shown] == ["voice", "pauses"]
    assert len(page.trend_grid.charts) == 2
    assert page.trend_table.rowCount() == 2 and page.trend_table.columnCount() == 4
    headers = [page.trend_table.horizontalHeaderItem(c).text() for c in range(4)]
    assert headers == ["dimenze", "14. 10. 2025", "14. 10. 2026", "změna za období"]
    assert page.trend_table.item(0, 0).text() == "Kvalita hlasu"
    # 2025 má jen čtení, 2026 jen fonaci: v tabulce mezery místo nul
    assert page.trend_table.item(0, 1).text() == "–"
    assert page.trend_table.item(1, 2).text() == "–"
    assert page.trend_table.item(0, 2).text() and page.trend_table.item(1, 1).text()
    assert "rozdíl v SD" in page.trend_note.text()
    out = page.save_image(settings.work_root / "vyvoj.png")
    assert out is not None and out.is_file()
    # grafy jen pro zaškrtnuté dimenze; výchozí = hraniční nebo mimo normu naposledy
    assert page.trend_selection() == [r.dimension.key for r in shown if r.abnormal]
    assert len(page.trend_grid.charts) == len(page.trend_selection())
    # měřítko Hodnoty: položky v jednotkách feature po skupinách dimenzí, bez pásem
    assert page.trend_scale() == "sd" and not page.scale_sd_btn.isHidden()
    assert page.scale_note.isHidden()
    got: list[str] = []
    page.scale_changed.connect(got.append)
    page.set_scale("values")
    assert got == ["values"] and page.effective_scale() == "values"
    assert page.timeline() is None
    values = page.value_timeline()
    assert values is not None and "voice" in [d.key for d, _r in values.groups()]
    assert len(page.trend_grid.charts) == len(values.shown_rows()) >= 2
    assert len(page.trend_grid.headers) == len(values.groups())
    assert page.trend_grid.charts[0].series.scale == "values"
    assert page.trend_table.columnCount() == 6
    assert page.trend_table.item(0, 0).text() == "Kvalita hlasu"
    assert page.trend_table.columnSpan(0, 0) == 6  # řádek skupiny přes celou šířku
    assert page.trend_table.horizontalHeaderItem(1).text() == "jednotka"
    assert page.trend_table.horizontalHeaderItem(5).text() == "změna v %"
    assert "SD proti normě" in page.trend_note.text()
    out = page.save_image(settings.work_root / "hodnoty.png")
    assert out is not None and out.is_file()
    page.set_scale("sd")
    assert page.timeline() is not None and page.value_timeline() is None
    assert got == ["values", "sd"]
    # podoby Vývoje: výchozí Grafy = přehled s výběrem + grafy; Pavouk jen do tří vyšetření
    forms: list[str] = []
    page.form_changed.connect(forms.append)
    picks: list[tuple[str, object]] = []
    page.trend_selection_changed.connect(lambda k, v: picks.append((k, v)))
    assert page.trend_form() == "charts" and page.effective_form() == "charts"
    assert not page.map_card.isHidden() and not page.grid_card.isHidden()
    assert page.table_card.isHidden() and page.spider_card.isHidden()
    assert page.trend_map.rowCount() == 2 and page.trend_map.columnCount() == 6
    assert page.trend_map.horizontalHeaderItem(0).text() == "graf"
    assert page.trend_map.horizontalHeaderItem(2).text() == "průběh"
    # zaškrtnutí všech řádků přidá grafy, výběr se hlásí ven a pamatuje
    page.trend_map.set_all(True)
    assert picks and picks[-1][0] == "dims" and len(picks[-1][1]) == 2
    assert len(page.trend_grid.charts) == 2
    page.trend_map.set_all(False)
    assert len(page.trend_grid.charts) == 0 and "Zaškrtněte" in page.trend_grid.empty.text()
    page.set_trend_selection(["voice"], None)
    assert page.trend_selection() == ["voice"] and len(page.trend_grid.charts) == 1
    assert page.form_spider_btn.isEnabled() and not page.trend_bar.isHidden()
    page.set_form("spider")
    assert forms == ["spider"] and not page.spider_card.isHidden() and page.map_card.isHidden()
    assert not page.table_card.isHidden() and len(page.trend_spider.overlays()) == 1
    assert "2025" in page.spider_legend.text()
    page.set_form("charts")
    assert not page.grid_card.isHidden() and page.spider_card.isHidden()
    out = page.save_image(settings.work_root / "grafy.png")
    assert out is not None and out.is_file()
    # přehled přepne kliknutím na sloupec na to vyšetření
    page.trend_map.visit_clicked.emit(0)
    assert page.view() == "visit" and page.current_assessment().date == "2025-10-14"
    page.set_view("trend")
    # v Hodnotách překryv není; přehled jde po položkách s vlastním výběrem
    page.set_scale("values")
    page.set_form("spider")
    assert page.effective_form() == "charts" and not page.form_spider_btn.isEnabled()
    assert page.trend_map.horizontalHeaderItem(2).text() == "jedn."
    assert page.trend_selection() and ":" in page.trend_selection()[0]
    page.trend_map.set_all(False)
    assert picks[-1][0] == "items" and page.trend_grid.charts == []
    page.set_scale("sd")
    assert page.trend_selection() == ["voice"]  # výběr dimenzí zůstal
    page.set_form("charts")
    # výslovná volba „žádná norma“ platí jen pro vybraného pacienta (odchylka v katalogu)
    page.set_view("visit")
    norm_path = str(page.current_norm().path)
    assert page.norm_choice() == norm_path
    assert settings.norm_choice == page.norm_choice()  # uloží se i výchozí první norma
    page.flush_badges()
    page.norm_combo.setCurrentIndex(page.norm_combo.count() - 1)
    assert page.current_norm() is None and page.norm_choice() == norm_path  # výchozí beze změny
    assert window.catalog.patient_prefs()["p-017"].norm == "none"
    assert page.chart_card.isHidden() and not page.prefs_label.isHidden()
    assert not page.window_combo.isEnabled() and page.tree.isColumnHidden(4)
    assert [c.pill.text() for c in page._cards] == ["bez normy"]  # jen tento pacient
    page.set_view("trend")
    assert page.effective_scale() == "values" and page.scale_note.text() == "vyberte normu"
    again = MainWindow(settings)
    qtbot.addWidget(again)
    again.assessment_page.select_assessment(first.id)
    assert again.assessment_page.current_norm() is None  # odchylka přežila restart
    again.assessment_page.select_assessment(second.id)
    assert again.assessment_page.current_norm() is None  # táž osoba, obě vyšetření
    # „nastavit jako výchozí pro všechny“ udělá z „žádné“ výchozí normu
    page.set_view("visit")
    page._make_default()
    assert settings.norm_choice == "none" and page.norm_choice() == "none"
    assert window.catalog.patient_prefs() == {}
    page.norm_combo.setCurrentIndex(0)  # zpět na normu, teď zase jen pro P-017
    assert page.current_norm() is not None and page.window_combo.isEnabled()
    assert window.catalog.patient_prefs()["p-017"].norm == norm_path
    page._make_default()
    assert settings.norm_choice == norm_path and window.catalog.patient_prefs() == {}
    # přepnutí pacienta bez druhého vyšetření vrátí Vyšetření
    page.save_from(page.make_dialog(initial=recording(page, "P-018_fonace.wav")))
    assert page.current_assessment().subject == "P-018"
    assert page.view() == "visit" and not page.view_trend_btn.isEnabled()
    assert page.trend_panel.isHidden() and not page.middle.isHidden()


def test_trend_values_without_norm(qtbot: QtBot, settings: AppSettings) -> None:
    """Bez normy jde Vývoj jen v měřítku Hodnoty: SD neaktivní s vysvětlením, PNG jde."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    first = page.save_from(page.make_dialog(initial=recording(page, "P-017_fonace.wav")))
    assert first is not None
    dialog = page.make_dialog(template=first)
    qtbot.addWidget(dialog)
    dialog.add_recording(recording(page, "P-017_cteni.wav"))
    dialog._set_date("2025-10-14")
    assert page.save_from(dialog) is not None
    assert page.current_norm() is None and page.view_trend_btn.isEnabled()
    # záložka Vyšetření bez normy: strom jen s hodnotami, pavouk a sloupce normy schované
    assert not page.middle.isHidden() and page.chart_card.isHidden()
    assert page.tree.isColumnHidden(3) and page.tree.isColumnHidden(6)
    assert not page.tree.isColumnHidden(2) and "Normy" in page.tree_hint.text()
    tops = [page.tree.topLevelItem(i) for i in range(page.tree.topLevelItemCount())]
    # vybrané vyšetření má jen čtení: Kvalita hlasu bez dat, položka ze čtení s hodnotou
    assert tops[0].text(0) == "Kvalita hlasu" and tops[0].text(2).startswith("bez dat")
    filled = [t for t in tops if t.childCount()]
    assert filled and filled[0].child(0).text(2) != "–" and filled[0].child(0).text(4) == ""
    page.set_view("trend")
    assert page.view() == "trend" and page.effective_scale() == "values"
    assert page.trend_scale() == "sd"
    assert not page.scale_sd_btn.isEnabled() and not page.scale_note.isHidden()
    assert page.scale_values_btn.isChecked()
    values = page.value_timeline()
    assert values is not None and values.enough and page.timeline() is None
    assert len(page.trend_grid.charts) == len(values.shown_rows()) >= 1
    assert page.effective_form() == "charts" and not page.form_spider_btn.isEnabled()
    assert page.trend_map.rowCount() >= 2  # skupina dimenze + položka
    assert "Normy" in page.trend_note.text() and page.image_btn.isEnabled()
    out = page.save_image(settings.work_root / "bez-normy.png")
    assert out is not None and out.is_file()
    page.set_view("visit")
    assert not page.image_btn.isEnabled()  # bez normy není pavouk
    assert not page.middle.isHidden() and page.chart_card.isHidden()


def test_nice_axis_and_ticks() -> None:
    from speechscope_app.ui.widgets.trend_chart import format_tick, nice_axis

    ymin, ymax, ticks = nice_axis([360.0, 395.0, 412.0])
    assert ymin <= 360 and ymax >= 412 and 2 <= len(ticks) <= 5
    assert ticks[0] == ymin and ticks[-1] == ymax
    assert format_tick(400.0, ticks) == "400"
    assert format_tick(0.5, [0.0, 0.5, 1.0]) == "0,5" and format_tick(-2.0, [-2.0, 0.0]) == "−2"
    ymin, ymax, ticks = nice_axis([5.0])  # jedna hodnota: okolí místo nulového rozsahu
    assert ymin < 5.0 < ymax and len(ticks) >= 2


def test_window_and_sex_are_per_patient(qtbot: QtBot, settings: AppSettings) -> None:
    """Okno a pohlaví platí pro vybraného pacienta: ostatní se nepřepočítají, volba se
    pamatuje v katalogu a jde z ní udělat výchozí pro všechny."""
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    save_norm(settings.norms_dir())
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    a = page.save_from(page.make_dialog(initial=recording(page, "P-017_fonace.wav")))
    b = page.save_from(page.make_dialog(initial=recording(page, "P-018_fonace.wav")))
    assert a is not None and b is not None
    page.flush_badges()
    page.select_assessment(a.id)
    before_b = page._eval_cache[b.id][1]
    default_index = page.window_combo.currentIndex()
    assert page.prefs_label.isHidden() and page.eval_defaults() == (5, True)
    page.window_combo.setCurrentIndex(3)  # bez omezení, jen pro P-017
    page.same_sex.setChecked(False)
    assert page.has_override("P-017") and not page.has_override("P-018")
    assert not page.prefs_label.isHidden()
    assert page._eval_cache[b.id][1] is before_b  # P-018 beze změny, nic se nepočítalo
    assert page._lookup(a, page.current_visit(), page.current_norm()) is not None
    prefs = window.catalog.patient_prefs()
    assert prefs["p-017"].window == 0 and prefs["p-017"].same_sex is False
    # jiný pacient ukáže výchozí, návrat ukáže odchylku
    page.select_assessment(b.id)
    assert page.window_combo.currentIndex() == default_index and page.same_sex.isChecked()
    assert page.prefs_label.isHidden()
    page.select_assessment(a.id)
    assert page.window_combo.currentIndex() == 3 and not page.same_sex.isChecked()
    # po restartu zůstává
    again = MainWindow(settings)
    qtbot.addWidget(again)
    again.assessment_page.select_assessment(a.id)
    assert again.assessment_page.window_combo.currentIndex() == 3
    # nastavit jako výchozí pro všechny: odchylky pryč, nastavení uložené, P-018 nově bez okna
    page._make_default()
    assert settings.eval_window == 0 and settings.eval_same_sex is False
    assert window.catalog.patient_prefs() == {} and page.prefs_label.isHidden()
    page.select_assessment(b.id)
    assert page.window_combo.currentIndex() == 3 and not page.same_sex.isChecked()
    assert page._eval_cache[b.id][1] is not before_b  # P-018 se přepočítal podle nového výchozího
    # návrat na výchozí hodnotu odchylku zruší
    page.window_combo.setCurrentIndex(1)
    assert page.has_override("P-018")
    page.window_combo.setCurrentIndex(3)
    assert not page.has_override("P-018")
