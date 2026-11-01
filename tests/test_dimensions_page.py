"""Stránka Dimenze: úprava dimenzí pavouka, uložení do dat aplikace, import a export."""

from __future__ import annotations

from pathlib import Path

import pytest

from speechscope_app.backend import dimensions as dm
from speechscope_app.backend.norms import LOWER


def test_dump_roundtrip_and_disabled_items() -> None:
    dims = dm.load_dimensions()
    dims[1].items[0].enabled = False
    dims[2].enabled = False
    again = dm.parse_dimensions(dm.dump_dimensions(dims))
    assert dm.dump_dimensions(again) == dm.dump_dimensions(dims)
    active = dm.active_dimensions(again)
    assert len(active) == len(dims) - 1  # vypnutá dimenze vypadne
    assert dims[2].key not in {d.key for d in active}
    articulation = next(d for d in active if d.key == dims[1].key)
    assert len(articulation.items) == len(dims[1].items) - 1  # vypnutá položka taky


def test_load_effective_falls_back_and_problems(tmp_path: Path) -> None:
    path = tmp_path / "dimensions.yaml"
    dims, error = dm.load_effective(path)
    assert not error and len(dims) == len(dm.load_dimensions())  # bez souboru přibalené
    path.write_text(
        "dimensions:\n  - key: x\n    items: [{column: a, tasks: [ddk], direction: nahoru}]\n",
        encoding="utf-8",
    )
    dims, error = dm.load_effective(path)
    assert error and len(dims) == len(dm.load_dimensions())
    bad = [dm.Dimension("a", "", [dm.Item("c", ())]), dm.Dimension("a", "B", [])]
    text = "\n".join(dm.problems(bad))
    assert "bez názvu" in text and "stejným klíčem" in text and "nemá úlohu" in text
    assert "žádná zapnutá položka" in text
    assert dm.new_key("Kvalita hlasu", {"kvalita-hlasu"}) == "kvalita-hlasu-2"


@pytest.fixture
def window(qtbot, tmp_path: Path, monkeypatch):  # noqa: ANN001, ANN201
    from speechscope_app.backend.settings import AppSettings
    from speechscope_app.ui.main_window import MainWindow

    monkeypatch.setattr(
        "speechscope_app.backend.settings.app_data_dir", lambda: tmp_path / "appdata"
    )
    settings = AppSettings(tmp_path / "settings.ini")
    settings.use_fake_library = True
    settings.models_dir = tmp_path / "models"
    settings.work_root = tmp_path / "work"
    settings.advanced = True
    win = MainWindow(settings)
    qtbot.addWidget(win)
    return win


def test_page_edit_save_and_reset(window, tmp_path: Path) -> None:  # noqa: ANN001
    from speechscope_app.ui.main_window import PAGE_DIMENSIONS, PAGE_ENV, PAGE_NORMS

    assert (
        PAGE_NORMS < PAGE_DIMENSIONS < PAGE_ENV and window.nav_labels[PAGE_DIMENSIONS] == "Dimenze"
    )
    window.nav.setCurrentRow(PAGE_DIMENSIONS)
    page = window.dimensions_page
    path = window.settings.dimensions_file()
    assert len(page.dimensions()) == len(dm.load_dimensions()) and not page.dirty()
    assert page.dirty_bar.isHidden() and page.readonly_bar.isHidden()

    assert page.select_dimension("articulation")
    item = page.current().items[0]
    page._set(item, enabled=False)
    page._set(page.current().items[1], direction=LOWER)
    assert page.dirty() and page.changed_keys() == {"articulation"}
    assert page.badge(page.current())[0] == "upraveno" and not page.dirty_bar.isHidden()
    assert page.add_item("acoustic.pitch.f0.std", ("reading",))
    assert page.table.rowCount() == len(page.current().items)
    key = page.new_dimension("Moje dimenze")
    assert page.current().key == key == "moje-dimenze"
    assert not page.save(notify=False)  # nová dimenze bez položky nejde uložit
    page.delete_dimension(confirm=False)
    page.move_dimension("articulation", 0)
    assert page.dimensions()[0].key == "articulation" and page.change_count() >= 2

    before = len(window.assessment_page._dimensions[1].items)
    assert page.save(notify=False) and path.is_file() and not page.dirty()
    saved = dm.load_dimensions(path)
    assert saved[0].key == "articulation" and not saved[0].items[0].enabled
    pacienti = window.assessment_page._dimensions
    assert pacienti[0].key == "articulation"
    assert len(pacienti[0].items) == len(saved[0].items) - 1  # vypnutá položka nejde do výpočtu
    assert before  # (původně druhá dimenze)

    exported = page.export_file(tmp_path / "export.yaml")
    page.reset_defaults(confirm=False)
    assert not path.exists() and page.dimensions()[0].key == "voice"
    assert window.assessment_page._dimensions[0].key == "voice"
    assert page.import_file(exported) and page.dirty()  # import = neuložené úpravy
    assert page.dimensions()[0].key == "articulation"
    page.discard()
    assert not page.dirty() and page.dimensions()[0].key == "voice"


def test_readonly_without_advanced(window) -> None:  # noqa: ANN001
    from speechscope_app.ui.main_window import PAGE_DIMENSIONS

    page = window.dimensions_page
    page.set_advanced(False)
    window.nav.setCurrentRow(PAGE_DIMENSIONS)
    assert not page.readonly_bar.isHidden() and page.add_btn.isHidden()
    assert page.import_btn.isHidden() and not page.export_btn.isHidden()
    assert page.table.isColumnHidden(0) and not page.name_edit.isEnabled()


def test_picker_offers_catalog_columns(window, qtbot) -> None:  # noqa: ANN001
    from speechscope_app.ui.pages.dimensions import FeaturePickerDialog

    page = window.dimensions_page
    used = {"acoustic.pitch.f0.std": ["Prozódie"]}
    dialog = FeaturePickerDialog(page.catalog(), used, page.coverage, "Prozódie", page)
    qtbot.addWidget(dialog)
    dialog.search.setText("f0")
    assert dialog.select("acoustic.pitch.f0.std")
    dialog._accept_item(dialog.tree.currentItem())
    column, tasks = dialog.chosen
    assert column == "acoustic.pitch.f0.std" and tasks
