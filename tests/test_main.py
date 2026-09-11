"""Vstupní bod: režim `--fake-cli` a pojistka proti spouštění GUI jako podprocesu."""

from __future__ import annotations

import sys

import pytest

from speechscope_app import main as app_main
from speechscope_app.backend import library
from speechscope_app.backend.settings import AppSettings


def test_fake_command_frozen_never_uses_dash_m(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zabalené exe s `-m` by spustilo další GUI (množení procesů)."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert library.fake_command() == [sys.executable, "--fake-cli"]
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert library.fake_command() == [sys.executable, "-m", "speechscope_app.fake.cli"]


def test_subprocess_env_marks_child() -> None:
    assert library.subprocess_env()[library.CHILD_ENV] == "1"


def test_fake_cli_route_runs_without_gui(capsys: pytest.CaptureFixture[str]) -> None:
    code = app_main.main(["--fake-cli", "version"])
    assert code == 0
    assert capsys.readouterr().out.strip() == "0.2.0"


def test_child_guard_exits_before_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(library.CHILD_ENV, "1")
    monkeypatch.setattr(app_main, "QApplication", None)  # kdyby se GUI přece tvořilo, spadne
    assert app_main.main([]) == app_main.EXIT_CHILD_GUARD


def test_fake_override_is_not_persisted(tmp_path) -> None:
    settings = AppSettings(tmp_path / "s.ini")
    settings.fake_override = True
    assert settings.use_fake_library
    assert settings.effective_command() == library.fake_command()
    assert not AppSettings(tmp_path / "s.ini").use_fake_library
