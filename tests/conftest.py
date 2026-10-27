from __future__ import annotations

import os
from pathlib import Path

import pytest

from speechscope_app.backend.library import Library, fake_command


@pytest.fixture(autouse=True)
def fast_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEECHSCOPE_FAKE_DELAY", "0")
    monkeypatch.delenv("SPEECHSCOPE_FAKE_DOCTOR", raising=False)


@pytest.fixture
def fake_library(tmp_path: Path) -> Library:
    return Library(fake_command(), models_dir=tmp_path / "models")


@pytest.fixture
def recordings(tmp_path: Path) -> Path:
    """Složka se třemi "nahrávkami": jedna se sidecary, jedna vadná."""
    folder = tmp_path / "data"
    folder.mkdir()
    for name in ("p01.wav", "p02_bad.wav", "p03_short.wav"):
        (folder / name).write_bytes(b"RIFF")
    (folder / "p01.labels.txt").write_text("0\t1\tspeech\n", encoding="utf-8")
    (folder / "p01.txt").write_text("ruční přepis", encoding="utf-8")
    (folder / "sub").mkdir()
    (folder / "sub" / "p04.flac").write_bytes(b"fLaC")
    (folder / "poznamky.txt").write_text("není nahrávka", encoding="utf-8")
    return folder


def wait_doctor(qtbot, window) -> None:  # noqa: ANN001
    """Počká na zprávu doctor z pozadí (po startu běží sama); bez ní ji spustí."""
    env = window.env_page
    if not env.checking and env.report is not None:
        return
    with qtbot.waitSignal(env.report_changed, timeout=15000):
        if not env.checking:
            env.refresh()


@pytest.fixture(scope="session", autouse=True)
def qt_offscreen() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
