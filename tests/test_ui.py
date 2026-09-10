"""Kouřový test celého okna nad falešnou knihovnou."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from speechscope_app.backend.library import ParamInfo
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import PAGE_RESULTS, MainWindow
from speechscope_app.ui.widgets.param_form import ParamForm


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    # uživatelské protokoly do tmp, ne do AppData
    monkeypatch.setattr(
        "speechscope_app.backend.settings.app_data_dir", lambda: tmp_path / "appdata"
    )
    s = AppSettings(tmp_path / "settings.ini")
    s.use_fake_library = True
    s.models_dir = tmp_path / "models"
    s.work_root = tmp_path / "work"
    s.advanced = True
    return s


def test_window_runs_batch_end_to_end(
    qtbot: QtBot, settings: AppSettings, recordings: Path
) -> None:
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.env_page.refresh()
    assert window.env_page.report and window.env_page.report["all_ready"]

    window.batch_page.set_folder(recordings)
    assert window.batch_page.files.rowCount() == 4
    assert window.batch_page.run_btn.isEnabled()

    idx = window.batch_page.protocol.findText("Fonace, základní")
    window.batch_page.protocol.setCurrentIndex(idx)
    assert window.batch_page.tree.topLevelItemCount() > 0

    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        window.batch_page.run_btn.click()
    state = window.run_page.runner.state
    assert state.finished and state.total == 4
    assert window.nav.currentRow() == PAGE_RESULTS
    assert window.results_page.table.model().rowCount() == 4

    run_dirs = list((settings.work_root).glob("*_fonace-zakladni"))
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "features.csv").is_file()
    assert (run_dirs[0] / "protocol.yaml").is_file()
    assert (run_dirs[0] / "speechscope.log").is_file()


def test_save_protocol_from_advanced_mode(qtbot: QtBot, settings: AppSettings) -> None:
    from PySide6.QtCore import Qt

    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.protocol.setCurrentIndex(page.protocol.findText("Fonace, základní"))
    assert page.save_btn.isEnabled()

    # odškrtnout první feature a upravit parametr providera
    first = page.tree.topLevelItem(0).child(0)
    first.setCheckState(0, Qt.CheckState.Unchecked)
    page._overrides["transcript"] = {"language": "en"}
    proto = page.effective_protocol()
    assert proto is not None and first.data(0, Qt.ItemDataRole.UserRole) not in proto.features
    proto.name = "Moje fonace"
    proto.description = "jen test"

    path = window.save_protocol(proto)
    assert path.is_file() and path.parent == settings.protocols_dir()
    assert page.current_protocol().name == "Moje fonace"
    assert page.protocol.currentText() == "Moje fonace (vlastní)"
    saved = page.current_protocol()
    assert saved.features == proto.features and saved.config == {"transcript": {"language": "en"}}

    # stejné jméno podruhé přepíše soubor, nevznikne druhý
    again = window.save_protocol(proto)
    assert again == path and len(list(settings.protocols_dir().glob("*.yaml"))) == 1


def test_param_form_overrides(qtbot: QtBot) -> None:
    params = [
        ParamInfo("f_min", 60.0, "float", "", bounds={"gt": 0}),
        ParamInfo("mode", "fmin", "str", "", choices=["fmin", "fixed"]),
        ParamInfo("order", 5, "int", "", bounds={"ge": 1}),
        ParamInfo("flag", False, "bool", ""),
        ParamInfo("note", "", "str", ""),
    ]
    form = ParamForm(params)
    qtbot.addWidget(form)
    assert form.overrides() == {}
    form.set_values({"f_min": 75, "mode": "fixed", "flag": True})
    assert form.overrides() == {"f_min": 75.0, "mode": "fixed", "flag": True}
    form.set_value("f_min", 60.0)
    assert "f_min" not in form.overrides()
