"""Diagnostický balíček: obsah zipu bez citlivých dat."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from speechscope_app.backend.diagnostics import build_bundle, default_name
from speechscope_app.backend.history import write_run_file
from speechscope_app.backend.library import Library
from speechscope_app.backend.protocol import Protocol
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import MainWindow


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


def test_bundle_contents(settings: AppSettings, fake_library: Library, tmp_path: Path) -> None:
    run = settings.work_root / "2026-09-11_10-00-00_fonace-zakladni"
    run.mkdir(parents=True)
    Protocol(name="Fonace, základní", task="phonation").save(run / "protocol.yaml")
    (run / "speechscope.log").write_text("log", encoding="utf-8")
    (run / "features.csv").write_text("file\np01.wav\n", encoding="utf-8")
    (run / "manifest.csv").write_text("path,pacient\np01.wav,PD001\n", encoding="utf-8")
    write_run_file(run, status="ok", processed=1, total=1)
    settings.protocols_dir().mkdir(parents=True)
    Protocol(name="Moje", task="story").save(settings.protocols_dir() / "moje.yaml")

    target = tmp_path / default_name()
    members = build_bundle(target, settings=settings, library=fake_library, report=None)
    assert target.is_file() and default_name().startswith("speechscope-diagnostika-")
    assert set(members) == {
        "info.json",
        "doctor.json",
        "protocols/moje.yaml",
        f"runs/{run.name}/speechscope.log",
        f"runs/{run.name}/protocol.yaml",
        f"runs/{run.name}/run.json",
    }
    with zipfile.ZipFile(target) as zf:
        info = json.loads(zf.read("info.json"))
        doctor = json.loads(zf.read("doctor.json"))
    assert info["app_version"] and info["library_version"] and info["use_fake_library"]
    assert info["models_dir"] == str(settings.models_dir)
    assert doctor["all_ready"] is True  # doctor se doptal sám


def test_main_window_saves_bundle(qtbot: QtBot, settings: AppSettings, tmp_path: Path) -> None:
    window = MainWindow(settings)
    qtbot.addWidget(window)
    target = window.save_diagnostics(tmp_path / "diag.zip")
    assert target is not None and target.is_file()
    with zipfile.ZipFile(target) as zf:
        assert "info.json" in zf.namelist() and "doctor.json" in zf.namelist()
    assert window.env_page.diagnostics_btn.isEnabled()
