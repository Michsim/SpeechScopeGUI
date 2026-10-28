"""Stránka Normy: seznam a detail, export, import a smazání, napojení na Hodnocení."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from test_norms import controls_frame

from speechscope_app.backend import norms as norms_mod
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
    assert window.assessment_page.norm_combo.count() == 3

    # smazání do Koše (v testu natrvalo)
    monkeypatch.setattr(norms_mod, "send_to_trash", shutil.rmtree)
    assert page.delete_current(confirm=False)
    assert page.count() == 2 and not imported.path.exists()
    assert window.assessment_page.norm_combo.count() == 2


def test_norms_page_empty(qtbot: QtBot, settings: AppSettings) -> None:
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_NORMS)
    page = window.norms_page
    assert page.count() == 0 and not page.empty.isHidden() and page.detail.isHidden()
    assert "stránce Normy" in window.assessment_page.no_norm.text()
