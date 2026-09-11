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
    assert [model.headerData(i, horizontal) for i in range(3)] == ["file", "path", "task"]
    col = list(model.frame.columns).index(columns[0])
    tip = model.headerData(col, horizontal, Qt.ItemDataRole.ToolTipRole)
    assert tip.startswith(columns[0].rsplit(".", 1)[0] + ":")  # popis z list --json
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
