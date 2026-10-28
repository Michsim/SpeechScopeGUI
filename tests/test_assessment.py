"""Stránka Hodnocení: ručně založená hodnocení, pavouk, strom, úprava, zkratka z Výsledků."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot
from test_norms import HNR, JIT, RATE, controls_frame
from test_visits import make_run

from speechscope_app.backend.norms import Norm, NormInfo
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import PAGE_ASSESSMENT, PAGE_RESULTS, MainWindow
from speechscope_app.ui.pages.assessment import format_number, worst_band
from speechscope_app.ui.widgets.spider_chart import Axis, SpiderChart, format_sd

PAUSE = "acoustic.timing.pauses.median"  # jediná položka dimenze Pauzy a tempo


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
    assert window.nav_labels[PAGE_ASSESSMENT] == "Hodnocení"

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
    assert [a.label for a in axes] == ["Kvalita hlasu", "Pauzy a tempo"] and axes[0].band == "bad"
    assert "Jazyk: potřebuje úlohu Spontánní řeč" in page.missing_label.text()
    assert page.tree.topLevelItemCount() == 7
    voice = page.tree.topLevelItem(0)
    assert voice.text(0) == "Kvalita hlasu" and voice.childCount() == 3 and voice.isExpanded()
    hnr = voice.child(0)  # HNR, CPPS, F0 SD z fonace; CPPS ze spontánní řeči tu není
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
    stored = json.loads((settings.assessments_dir() / f"{saved.id}.json").read_text("utf-8"))
    assert {r["run"] for r in stored["recordings"]} >= {new.name}

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
    assert chart.orientation() == INWARD and not chart.grab().isNull()
    chart.set_orientation(OUTWARD)
    assert chart.orientation() == OUTWARD and not chart.grab().isNull()
    chart.set_orientation("nesmysl")
    assert chart.orientation() == OUTWARD
