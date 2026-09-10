"""Runner nad QProcess proti falešné knihovně."""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from speechscope_app import contract
from speechscope_app.backend.command import ExtractRequest, extract_args, models_download_args
from speechscope_app.backend.library import Library
from speechscope_app.backend.runner import Runner


def test_runner_collects_events(
    qtbot: QtBot, fake_library: Library, recordings: Path, tmp_path: Path
) -> None:
    runner = Runner()
    events: list[contract.Event] = []
    logs: list[str] = []
    runner.event.connect(events.append)
    runner.log.connect(logs.append)

    req = ExtractRequest(inputs=[recordings], task="story", out=tmp_path / "o.csv")
    with qtbot.waitSignal(runner.finished, timeout=15000) as blocker:
        runner.start(fake_library.argv(extract_args(req, models_dir=tmp_path)))
    code, cancelled = blocker.args
    assert code == 0 and not cancelled
    assert runner.state.finished and runner.state.total == 4
    assert runner.state.n_ok == 3 and len(runner.state.errors) == 1
    assert isinstance(events[0], contract.StartEvent)
    assert Path(runner.state.out).is_file()
    assert any("vybráno" in line for line in logs)
    assert not runner.running


def test_runner_tails_log_file(
    qtbot: QtBot, fake_library: Library, recordings: Path, tmp_path: Path
) -> None:
    """S --log-file knihovna na stderr nepíše; log se musí dočítat ze souboru."""
    runner = Runner()
    logs: list[str] = []
    runner.log.connect(logs.append)
    log_file = tmp_path / "run" / "speechscope.log"
    req = ExtractRequest(
        inputs=[recordings], task="story", out=tmp_path / "o.csv", log_file=log_file
    )
    with qtbot.waitSignal(runner.finished, timeout=15000):
        runner.start(fake_library.argv(extract_args(req)), log_file=log_file)
    assert log_file.is_file()
    assert any("vybráno" in line for line in logs)
    assert logs == runner.log_lines and len(logs) == len(set(logs))  # nic dvakrát


def test_runner_log_only_mode(qtbot: QtBot, fake_library: Library, tmp_path: Path) -> None:
    """`models download` nemá --progress-json: stdout i stderr jdou do logu."""
    runner = Runner()
    logs: list[str] = []
    runner.log.connect(logs.append)
    argv = fake_library.argv(models_download_args(only=["onnx"], models_dir=tmp_path))
    with qtbot.waitSignal(runner.finished, timeout=15000) as blocker:
        runner.start(argv, parse_events=False, extra_env={"HF_TOKEN": "x"})
    code, cancelled = blocker.args
    assert code == 0 and not cancelled
    assert any("stahuji onnx" in line for line in logs)
    assert runner.state.total == 0 and not runner.state.finished


def test_runner_cancel(
    qtbot: QtBot, fake_library: Library, recordings: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SPEECHSCOPE_FAKE_DELAY", "5")
    runner = Runner()
    req = ExtractRequest(inputs=[recordings], task="story", out=tmp_path / "o.csv")
    with qtbot.waitSignal(runner.event, timeout=15000):
        runner.start(fake_library.argv(extract_args(req)))
    assert runner.running
    with qtbot.waitSignal(runner.finished, timeout=15000) as blocker:
        runner.cancel()
    _code, cancelled = blocker.args
    assert cancelled and not runner.state.finished


def test_runner_failed_start(qtbot: QtBot, tmp_path: Path) -> None:
    runner = Runner()
    with qtbot.waitSignal(runner.finished, timeout=5000) as blocker:
        runner.start([str(tmp_path / "neexistuje.exe"), "extract"])
    code, cancelled = blocker.args
    assert code == -1 and not cancelled
