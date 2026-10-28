"""Stránka Hodnocení: vyšetření proti normě, pavouk, strom, ruční zařazení, zkratka z Výsledků."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from test_norms import HNR, JIT, RATE, controls_frame
from test_visits import make_run

from speechscope_app.backend.norms import Norm, NormInfo
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import PAGE_ASSESSMENT, PAGE_RESULTS, MainWindow
from speechscope_app.ui.pages.assessment import format_number, worst_band
from speechscope_app.ui.widgets.spider_chart import Axis, SpiderChart, format_sd


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
    norm = Norm(
        NormInfo(name="Testovací norma", names={"en": "Test norm"}, source="SAMI", language="cs"),
        controls_frame(),
    )
    norm.save(folder / "testovaci-norma")
    return norm


def test_assessment_page_lists_visits_and_scores(qtbot: QtBot, settings: AppSettings) -> None:
    settings.work_root.mkdir(parents=True)
    make_runs(settings.work_root)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.assessment_page
    assert window.nav_labels[PAGE_ASSESSMENT] == "Hodnocení"

    # bez normy: seznam je, pravá část jen říká, kde normu vzít
    window.nav.setCurrentRow(PAGE_ASSESSMENT)
    assert page.count() == 3 and not page.no_norm.isHidden() and page.middle.isHidden()
    assert page.card(0).title.text() == "P-017" and page.card(0).pill.text() == "bez normy"
    assert page.card(2).title.text() == "p03_short (bez pacienta)"
    assert page.header.title.text() == "P-017"
    assert "muž, 64 let · vyšetření 14. 10. 2026 · Fonace, Čtení" == page.header.subtitle.text()
    assert page.recordings.count() == 2 and not page.image_btn.isEnabled()

    # norma se objeví po uložení a znovunačtení
    save_norm(settings.norms_dir())
    page.reload_norms()
    assert page.norm_combo.count() == 1 and page.no_norm.isHidden() and not page.middle.isHidden()
    ev = page.evaluation()
    assert ev is not None and [r.dimension.key for r in ev.shown] == ["voice", "pauses"]
    assert worst_band(ev) == "bad" and page.header.property("role") == "missing"
    assert page.card(0).pill.text() == "mimo normu" and page.card(1).pill.text() == "v normě"
    assert page.card(2).pill.text() == "bez normy"  # DDK norma nemá
    axes = page.chart.axes()
    assert [a.label for a in axes] == ["Kvalita hlasu", "Pauzy a tempo"] and axes[0].band == "bad"
    assert "Jazyk: potřebuje úlohu Spontánní řeč" in page.missing_label.text()
    assert "Artikulace" in page.missing_label.text()
    assert page.tree.topLevelItemCount() == 7
    voice = page.tree.topLevelItem(0)
    assert voice.text(0) == "Kvalita hlasu" and voice.childCount() == 5 and voice.isExpanded()
    assert [voice.child(i).text(3) for i in range(5)].count("hodnota chybí") == 3  # jen 2 z 5
    jitter = voice.child(0)
    assert jitter.text(1) == "Fonace" and jitter.text(2).startswith("3,00")
    assert jitter.text(4).startswith("+") and jitter.text(5).isdigit()
    language = page.tree.topLevelItem(6)
    assert language.text(0) == "Jazyk" and language.text(2).startswith("bez dat")
    assert "Fonace: " in page.selection_label.text() and "jen muži" in page.selection_label.text()
    assert "Testovací norma" not in page.about.text() or "SAMI" in page.about.text()
    assert "60 osob" in page.about.text() and page.image_btn.isEnabled()

    # věkové okno a pohlaví přepočítají
    page.same_sex.setChecked(False)
    assert "jen muži" not in page.selection_label.text()
    page.window_combo.setCurrentIndex(3)  # bez omezení
    assert "až" not in page.selection_label.text().split("·")[0]
    assert page.evaluation().selections["phonation"].n == 60

    # obrázek
    out = page.save_image(settings.work_root / "pavouk.png")
    assert out is not None and out.is_file() and out.stat().st_size > 0

    # z Výsledků: řádek běhu → vyšetření
    assert window.show_assessment(settings.work_root / "2026-10-14_10-40-00_cteni", 0)
    assert window.nav.currentRow() == PAGE_ASSESSMENT and page.current_visit().subject == "P-017"
    assert not window.show_assessment(settings.work_root / "neexistuje", 0)
    page.search.setText("p03")
    assert page.count() == 1 and page.current_visit().subject == "p03_short"
    page.search.clear()
    assert page.count() == 3

    # ruční zařazení nahrávky bez pacienta k vyšetření P-017, platí přes restart
    assert page.select_visit(("P-017", "2026-10-14"))
    page._index.assign(page._index.visits[2].recordings[0], page.current_visit())
    page.refresh()
    assert page.count() == 2 and page.current_visit().tasks == ["phonation", "ddk", "reading"]
    assert any("(zařazeno ručně)" in page.recordings.item(i).text() for i in range(3))
    saved = json.loads((settings.norms_dir().parent / "visits.json").read_text(encoding="utf-8"))
    assert saved == {"2026-10-13_09-00-00_ddk#0": ["P-017", "2026-10-14"]}
    page.recordings.setCurrentRow(
        next(i for i in range(3) if "(zařazeno ručně)" in page.recordings.item(i).text())
    )
    assert not page.remove_btn.isHidden()
    page.remove_recording()
    assert page.count() == 3

    # Výsledky: pravé tlačítko „Otevřít v Hodnocení“ posílá složku a řádek
    window.nav.setCurrentRow(PAGE_RESULTS)
    results = window.results_page
    assert results.history.select_dir(settings.work_root / "2026-10-14_10-12-00_fonace")
    results.show_run(results.history.current())
    got: list = []
    results.assess_requested.connect(lambda d, r: got.append((d, r)))
    results.assess_row(1)
    assert got == [(settings.work_root / "2026-10-14_10-12-00_fonace", 1)]


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
