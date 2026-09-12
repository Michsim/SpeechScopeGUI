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
    assert page.current().builtin and page.edit_btn.isHidden()
    assert not page.delete_btn.isEnabled()
    assert "Segmentace" in page.summary.text() and "conformer" in page.summary.text()

    # kopie -> vlastní, editor viditelný
    page.copy_current()
    proto = page.current()
    assert proto is not None and proto.name == "Pohádka, akustika (kopie)" and not proto.builtin
    assert proto.path is not None and proto.path.parent == settings.protocols_dir()
    assert not page.edit_btn.isHidden() and page.delete_btn.isEnabled()
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
    assert page.edit_btn.isHidden() and page.save_btn.isHidden()
    assert page.delete_btn.isEnabled() and page.export_btn.isEnabled()
    assert "rozšířeném režimu" in page.note.text()


def test_protocol_detail_dialog_lists_features(qtbot: QtBot, settings: AppSettings) -> None:
    """Dvojklik na protokol: strom skupina → feature → sloupec s popisem, parametry zvlášť."""
    from speechscope_app.backend.protocol import Protocol
    from speechscope_app.ui.main_window import MainWindow

    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.env_page.refresh()
    page = window.protocols_page
    proto = next(p for p in page._protocols if p.name == "Fonace, základní")
    dialog = page.make_protocol_detail(proto)
    assert dialog.windowTitle() == "Co počítá Fonace, základní"
    assert dialog.summary.columns > 0 and "sloupců" in dialog.counts.text()
    groups = [dialog.tree.topLevelItem(i).text(0) for i in range(dialog.tree.topLevelItemCount())]
    assert any(g.startswith("Akustika") for g in groups) and dialog.params_item is None
    feature = dialog.tree.topLevelItem(0).child(0)
    assert feature.childCount() > 0 and feature.child(0).text(1)  # sloupec má popis
    assert dialog.tree.topLevelItem(0).isExpanded() and not feature.isExpanded()  # sbalené
    dialog.search.setText(feature.child(0).text(0))
    assert feature.isExpanded()  # hledání rozbalí
    dialog.search.clear()
    assert not feature.isExpanded()
    dialog.search.setText("zzz")
    assert dialog.tree.topLevelItem(0).isHidden()

    # lingvistika: feature má jediný sloupec, ukáže se jen název a popis bez podřádku
    ling = next(p for p in page._protocols if p.name == "Pohádka, lingvistika")
    dialog3 = page.make_protocol_detail(ling)
    group = dialog3.tree.topLevelItem(0)
    assert group.text(0).startswith("Lingvistika")
    leaf = group.child(0)
    assert leaf.childCount() == 0 and leaf.text(1)  # popis přímo u feature
    dialog3.search.setText(leaf.text(1)[:12].lower())
    assert not leaf.isHidden()  # hledání i podle popisu

    custom = Protocol(name="S parametry", task="story", config={"transcript": {"language": "en"}})
    dialog2 = window.batch_page.make_protocol_detail(custom)
    assert dialog2.params_item is not None  # transcript z protokolu, nlp z lišty
    assert dialog2.params_item.child(0).text(0) == "transcript.language"
    # Analýza dosadí jazyk nahrávek z lišty (čeština) a řekne to
    assert "cs" in dialog2.params_item.child(0).text(1)
    assert "z lišty" in dialog2.params_item.child(0).text(1)
    assert dialog2.params_item.childCount() == 2  # transcript i nlp
    assert dialog2.provider_pills and dialog2.provider_pills[0].property("role") == "ok"
