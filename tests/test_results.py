"""Výsledky: popisy sloupců, hledání, detail nahrávky, sloučení znovu spočítaných."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from speechscope_app.backend.library import Library
from speechscope_app.backend.protocol import Protocol
from speechscope_app.backend.results import failed_paths, merge_results
from speechscope_app.ui.pages.results import ResultsPage
from speechscope_app.ui.widgets.values_table import (
    BAND_ROLE,
    ERROR_TEXT_ROLE,
    FOOTER_ROLE,
    STATUS,
    column_stats,
    decimals_for,
)


def _csv(path: Path, library: Library) -> list[str]:
    columns = [c for f in library.features("phonation") for c in f.columns][:6]
    rows = [
        {"file": "p01.wav", "path": str(path.parent / "p01.wav"), "task": "phonation"}
        | {c: 1.5 for c in columns},
        {"file": "p02_bad.wav", "path": str(path.parent / "p02_bad.wav"), "task": "phonation"}
        | {"error": "soubor nejde načíst"},
        {"file": "p03.wav", "path": str(path.parent / "p03.wav"), "task": "phonation"}
        | {c: float("nan") for c in columns}
        | {"notes": "příliš krátký"},
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return columns


def test_results_page_descriptions_search_and_detail(
    qtbot: QtBot, fake_library: Library, tmp_path: Path
) -> None:
    run = tmp_path / "2026-09-11_10-00-00_fonace-zakladni"
    run.mkdir()
    Protocol(name="Fonace, základní", task="phonation").save(run / "protocol.yaml")
    columns = _csv(run / "features.csv", fake_library)
    page = ResultsPage()
    qtbot.addWidget(page)
    page.set_library(fake_library)
    page.set_work_root(tmp_path)
    page.load(run / "features.csv")

    model = page.table.model()
    horizontal = Qt.Orientation.Horizontal
    # nahrávka a stav vlevo (zmrazené), feature, na konci technické sloupce
    assert [model.key(i) for i in range(2)] == ["file", STATUS]
    assert model.frozen == 2 and page.table.frozen_count == 2
    assert [model.key(i) for i in range(model.columnCount())][-3:] == ["error", "task", "path"]
    assert model.headerData(0, horizontal) == "NAHRÁVKA"
    col = model.position(columns[0])
    tip = model.headerData(col, horizontal, Qt.ItemDataRole.ToolTipRole)
    assert columns[0] in tip and "verze" in tip  # popis, klíč a verze z list --json
    assert model.headerData(col, horizontal) == columns[0].rsplit(".", 1)[1].upper()
    assert model.headerData(col, horizontal, BAND_ROLE)  # pruh feature nad sloupcem
    status = model.position(STATUS)
    assert [model.index(r, status).data() for r in range(3)] == ["hotovo", "chyba", "poznámka"]
    assert page.rerun_btn.isVisibleTo(page)  # je řádek s chybou a protocol.yaml

    # hledání sloupce nechá název nahrávky, chybu a poznámku
    page.search.setText(columns[0].rsplit(".", 1)[1])
    shown = list(page.visible_frame().columns)
    assert shown[0] == "file" and columns[0] in shown and "path" not in shown
    assert "error" in shown and "notes" in shown

    # detail: skupina → feature → sloupec s hodnotou a popisem, i při hledání
    page.search.setText("zzz-nic")
    dialog = page.make_detail(0)
    assert dialog is not None and dialog.windowTitle() == "Nahrávka p01.wav"
    tree = dialog.tree
    assert tree.topLevelItemCount() >= 1
    group = tree.topLevelItem(0)
    value_item = group.child(0).child(0)
    assert value_item.text(1) == "1.5" and value_item.text(2)  # hodnota a popis
    dialog.search.setText("neexistujici-sloupec")
    assert group.isHidden()
    dialog.search.setText(value_item.text(0))
    assert not group.isHidden() and not value_item.isHidden()
    # prázdná hodnota jako pomlčka
    third = page.make_detail(2)
    assert third is not None and third.tree.topLevelItem(0).child(0).child(0).text(1) == "—"
    page.search.clear()
    assert len(page.visible_frame().columns) == len(pd.read_csv(run / "features.csv").columns)


def test_failed_paths_and_merge(tmp_path: Path, fake_library: Library) -> None:
    target = tmp_path / "features.csv"
    _csv(target, fake_library)
    frame = pd.read_csv(target)
    assert [p.name for p in failed_paths(frame)] == ["p02_bad.wav"]

    source = tmp_path / "znovu.csv"
    pd.DataFrame(
        [
            {"file": "p02_bad.wav", "path": str(tmp_path / "p02_bad.wav"), "task": "phonation"}
            | {frame.columns[3]: 9.0, "nova_kolona": "x"},
        ]
    ).to_csv(source, index=False)
    assert merge_results(target, source) == 1
    merged = pd.read_csv(target)
    assert list(merged["file"]) == ["p01.wav", "p02_bad.wav", "p03.wav"]  # pořadí zůstalo
    row = merged[merged["file"] == "p02_bad.wav"].iloc[0]
    assert row[frame.columns[3]] == 9.0 and row["nova_kolona"] == "x"
    assert "error" not in merged.columns  # už žádná chyba, sloupec zmizel jako u knihovny
    assert merged.loc[merged["file"] == "p03.wav", "notes"].iloc[0] == "příliš krátký"
    assert failed_paths(merged) == []


def test_values_table_card_footer_groups_and_error_row(
    qtbot: QtBot, fake_library: Library, tmp_path: Path
) -> None:
    """Hodnoty: průměr ± SD bez chybných řádků, karta sloupce, skupiny sloupců, text chyby."""
    run = tmp_path / "2026-10-31_10-00-00_cteni"
    run.mkdir()
    Protocol(name="Čtení", task="reading").save(run / "protocol.yaml")
    features = fake_library.features("reading")
    pitch = next(f for f in features if f.name == "acoustic.pitch.f0")
    other = next(f for f in features if f.group != pitch.group)
    a, b, c = pitch.columns[0], pitch.columns[1], other.columns[0]
    rows = [
        {"file": f"p0{i}.wav", "path": str(run / f"p0{i}.wav"), "subject": f"P{i}"}
        | {a: v, b: 100.0 + i, c: 2.0}
        for i, v in enumerate([2.0, 3.0, 4.0], start=1)
    ]
    rows.append({"file": "p09.wav", "path": str(run / "p09.wav"), "error": "nejde načíst"})
    pd.DataFrame(rows).to_csv(run / "features.csv", index=False)
    page = ResultsPage()
    qtbot.addWidget(page)
    page.set_library(fake_library)
    page.load(run / "features.csv")
    model = page.table.model()
    assert [model.key(i) for i in range(3)] == ["file", STATUS, "subject"]
    # souhrn pod tabulkou jen z řádků bez chyby
    horizontal = Qt.Orientation.Horizontal
    assert model.headerData(model.position(a), horizontal, FOOTER_ROLE) == "3.0 ±1.0"
    # chybný řádek: text chyby v prvním sloupci feature přes všechny feature
    first = model.feature_span()[0]
    assert model.index(3, first).data(ERROR_TEXT_ROLE) == "nejde načíst"
    assert model.index(3, first).data() == ""  # šířku sloupce text chyby neroztáhne
    assert page.table.columnSpan(3, first) == model.feature_span()[1]
    # karta sloupce: klik na záhlaví, statistika, vybraná nahrávka, šipky, zavření
    page.table.horizontalHeader().sectionClicked.emit(model.position(a))
    card = page.column_card
    assert page.card_key() == a and not card.isHidden()
    assert card.stat_labels["mean"].text() == "3.0 ± 1.0"
    assert card.stat_labels["range"].text() == "2 – 4"
    assert card.stat_labels["n"].text().startswith("3 ")  # 3 hodnoty, 1 s chybou
    page.table.selectRow(2)
    assert card.histogram.selected == 4.0
    assert model.data(model.index(0, model.position(a)), Qt.ItemDataRole.BackgroundRole)
    page.step_card(1)
    assert page.card_key() == b and card.stat_labels["median"].text() == "102"
    page.table.horizontalHeader().sectionClicked.emit(model.position(b))
    assert page.card_key() == ""
    page.table.horizontalHeader().sectionClicked.emit(model.position(STATUS))
    assert page.card_key() == ""  # stav není číselný sloupec
    # skupiny sloupců: jen pitch nechá nahrávku, pacienta, chybu a sloupce pitch
    assert not page.group_bar.isHidden() and pitch.group in page.group_buttons
    page.open_card(c)
    page.set_feature_group(pitch.group)
    shown = list(page.visible_frame().columns)
    assert a in shown and b in shown and c not in shown and "subject" in shown
    assert page.card_key() == ""  # sloupec karty zmizel z tabulky
    page.set_feature_group("")
    assert c in list(page.visible_frame().columns)


def test_column_stats_and_decimals() -> None:
    stats = column_stats(pd.Series([1.0, 2.0, float("nan"), 3.0]))
    assert stats is not None and stats.n == 3 and stats.mean == 2.0 and stats.sd == 1.0
    assert column_stats(pd.Series([float("nan")])) is None
    assert decimals_for(pd.Series([0.012, 0.031])) == 4
    assert decimals_for(pd.Series([118.4, 196.2])) == 0
    assert decimals_for(pd.Series([7.9, 8.4])) == 2
    assert decimals_for(pd.Series([3, 4])) == 0


def test_column_widths_sample_rows_even_when_hidden(qtbot: QtBot) -> None:
    """Šířky sloupců z prvních SAMPLE_ROWS řádků i ve skryté tabulce, název celý.

    Qt ve skryté tabulce měřil všechny buňky (norma 500 × 150: 4,3 s při prvním otevření).
    """
    from speechscope_app.ui.widgets.frozen_table import SAMPLE_ROWS, FrozenTableView
    from speechscope_app.ui.widgets.values_table import ValuesModel

    n = 300
    frame = pd.DataFrame(
        {
            "file": [f"r{i:03d}.wav" for i in range(n - 1)] + ["velmi_dlouhy_nazev_nahravky.wav"],
            "a": [1.5] * n,
            "b": [2.5] * n,
        }
    )
    view = FrozenTableView()
    qtbot.addWidget(view)  # nikdy nezobrazená, jako detail běhu plněný z přehledu
    model = ValuesModel(frame)
    view.setModel(model)
    calls: list[int] = []
    original = view.sizeHintForIndex
    view.sizeHintForIndex = lambda index: (calls.append(index.column()), original(index))[1]
    view.resizeColumnsToContents()
    assert calls.count(0) == n  # název nahrávky se měří celý
    assert all(calls.count(c) == SAMPLE_ROWS for c in range(1, model.columnCount()))
    longest = view.sizeHintForIndex(model.index(n - 1, 0)).width()
    assert view.columnWidth(0) >= longest
    assert model.value("a", 0) == 1.5 and model.value("neni", 0) is None
