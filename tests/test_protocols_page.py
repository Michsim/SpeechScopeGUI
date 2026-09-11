"""Stránka Protokoly: kopie, úprava, export, import, smazání."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import PAGE_PROTOCOLS, MainWindow


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


def test_copy_edit_export_import_delete(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.nav.setCurrentRow(PAGE_PROTOCOLS)
    page = window.protocols_page
    assert window.nav_labels[PAGE_PROTOCOLS] == "Protokoly"

    # přibalený: jen souhrn, bez editoru a bez mazání
    assert page.select("Pohádka, akustika")
    assert page.current().builtin and page.editor.isHidden() and not page.delete_btn.isEnabled()
    assert "Segmentace" in page.summary.text() and "conformer" in page.summary.text()

    # kopie -> vlastní, editor viditelný
    page.copy_current()
    proto = page.current()
    assert proto is not None and proto.name == "Pohádka, akustika (kopie)" and not proto.builtin
    assert proto.path is not None and proto.path.parent == settings.protocols_dir()
    assert not page.editor.isHidden() and page.delete_btn.isEnabled()
    assert "Pohádka, akustika (kopie)" in window.batch_page.protocols.names()

    # úprava: přejmenovat, odškrtnout feature, uložit
    page.name_edit.setText("Moje pohádka")
    first = page.editor.selected()[0]
    page.editor.picker.set_checked(first, False)
    assert page.save_btn.isEnabled()
    page.save_current()
    saved = page.current()
    assert saved is not None and saved.name == "Moje pohádka" and first not in saved.features
    assert saved.path == proto.path  # přejmenování nezakládá druhý soubor
    assert window.batch_page.select_protocol("Moje pohádka")

    # export a import pod jiným jménem (kolize se řeší bez dotazu jen u přibalených)
    target = tmp_path / "export.yaml"
    page.export_to(target)
    assert target.is_file()
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )
    page.import_from(target)
    assert page.current().name == "Moje pohádka (2)"
    assert len([p for p in window.batch_page._protocols if not p.builtin]) == 2

    # rozbitý soubor neshodí aplikaci
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\ntask: neexistuje\n", encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    page.import_from(bad)
    assert page.current().name == "Moje pohádka (2)"

    # smazání s potvrzením
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    path = page.current().path
    page.delete_current()
    assert path is not None and not path.exists()
    assert "Moje pohádka (2)" not in window.batch_page.protocols.names()


def test_basic_mode_shows_summary_only(qtbot: QtBot, settings: AppSettings) -> None:
    settings.advanced = False
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.protocols_page
    page.copy_current()
    assert not page.current().builtin
    assert page.editor.isHidden() and page.save_btn.isHidden()
    assert page.delete_btn.isEnabled() and page.export_btn.isEnabled()
    assert "rozšířeném režimu" in page.note.text()
