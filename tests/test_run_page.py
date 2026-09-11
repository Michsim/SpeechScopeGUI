"""Stránka Běh nad falešnou knihovnou a nad ručně krmenými událostmi."""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from speechscope_app import contract
from speechscope_app.backend.command import ExtractRequest, extract_args
from speechscope_app.backend.library import Library
from speechscope_app.ui.pages.run import RunPage, estimate_per_file, format_seconds


def test_format_seconds() -> None:
    assert format_seconds(0.4) == "0 s"
    assert format_seconds(41) == "41 s"
    assert format_seconds(380) == "6 min 20 s"
    assert format_seconds(3900) == "1 h 05 min"


def test_estimate_skips_model_loading_in_first_file() -> None:
    assert estimate_per_file([]) is None
    assert estimate_per_file([30.0]) == 30.0
    assert estimate_per_file([30.0, 10.0]) == 20.0
    assert estimate_per_file([30.0, 10.0, 12.0]) == 11.0


def test_run_page_fills_provider_columns(
    qtbot: QtBot, fake_library: Library, recordings: Path, tmp_path: Path
) -> None:
    page = RunPage()
    qtbot.addWidget(page)
    progress: list[str] = []
    page.progress_changed.connect(progress.append)
    inputs = sorted(p for p in recordings.rglob("*") if p.suffix in (".wav", ".flac"))
    req = ExtractRequest(inputs=inputs, task="story", domain="acoustic", out=tmp_path / "o.csv")
    with qtbot.waitSignal(page.finished, timeout=15000):
        page.start(
            fake_library.argv(extract_args(req, models_dir=tmp_path)),
            title="Pohádka",
            inputs=inputs,
            expected_seconds=2.0,
        )
    state = page.runner.state
    assert state.detailed and state.total == 4
    headers = [page.files.horizontalHeaderItem(c).text() for c in range(page.files.columnCount())]
    assert headers[0] == "nahrávka" and headers[-2:] == ["výsledek", "poznámka"]
    # akustika pro pohádku: segmentace, fonémy i přepis (speech_rate), ale ne Stanza
    assert {"segmentace", "fonémy", "přepis"} <= set(headers) and "jaz. rozbor" not in headers
    col = headers.index("segmentace")
    cells = {page.files.item(r, 0).text(): page.files.item(r, col) for r in range(4)}
    assert cells["p01.wav"].text().startswith("✓")
    assert cells["p02_bad.wav"] is None or cells["p02_bad.wav"].text() == ""
    statuses = {
        page.files.item(r, 0).text(): page.files.item(r, page.col_status).text() for r in range(4)
    }
    assert statuses["p02_bad.wav"] == "chyba" and statuses["p01.wav"] == "ok"
    assert page.files.item(list(statuses).index("p02_bad.wav"), page.col_note).text()
    assert progress[0] == "0/4" and progress[-1] == "" and "4/4" in progress
    assert page.seconds_per_file() is not None
    assert not page.log.isVisible()


def test_run_page_shows_running_stage_from_events(qtbot: QtBot) -> None:
    page = RunPage()
    qtbot.addWidget(page)
    page.show()
    page._paths = [Path("a.wav"), Path("b.wav")]
    page._started_at = 0.0

    def feed(event: contract.Event) -> None:
        # totéž, co dělá Runner: nejdřív stav, pak signál
        page.runner.state.apply(event)
        page._on_event(event)

    feed(contract.StartEvent(total=2, task="story", features=["f"], providers=["segments", "nlp"]))
    assert page.files.rowCount() == 2
    assert page.files.item(0, page.col_status).text() == "čeká"
    feed(contract.BeginEvent(index=0, path="C:/x/a.wav"))
    feed(contract.StageEvent(index=0, provider="segments", status="running"))
    assert page.current.text().startswith("Právě: a.wav (1/2)")
    assert "segmentace …" in page.current.text()
    assert page.files.item(0, 1).text().startswith("…")
    feed(contract.StageEvent(index=0, provider="segments", status="done", seconds=4.2))
    assert page.files.item(0, 1).text() == "✓ 4 s"
    feed(contract.StageEvent(index=0, provider="nlp", status="error", seconds=1, msg="prázdný"))
    assert page.files.item(0, 2).text() == "✗" and page.files.item(0, 2).toolTip() == "prázdný"
    feed(contract.FileEvent(index=0, path="C:/x/a.wav", status="ok", msg="nlp=prázdný"))
    assert page.files.item(0, page.col_status).text() == "ok"
    assert page.files.item(0, page.col_note).text() == "nlp=prázdný"
    assert page.current.text() == ""
    assert "1 z 2 hotovo" in page.timing.text()
    feed(contract.BeginEvent(index=1, path="C:/x/b.wav"))
    feed(contract.StageEvent(index=1, provider="segments", status="cached", seconds=0.01))
    assert page.files.item(1, 1).text() == "z cache"
    # starší knihovna bez begin/stage: jen výsledek
    feed(contract.StartEvent(total=1, task=None, features=[], providers=[], protocol=1))
    assert page.files.columnCount() == 3
    assert "jen dokončené" in page.current.text()
