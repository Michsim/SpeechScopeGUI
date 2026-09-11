"""Tabulka výběru feature."""

from __future__ import annotations

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from speechscope_app.backend.library import FeatureInfo
from speechscope_app.ui.widgets.feature_picker import COL_NAME, COL_REQUIRES, FeaturePicker


def _feature(name: str, requires: list[str]) -> FeatureInfo:
    return FeatureInfo(
        name=name,
        version="1",
        tasks=["story"],
        requires=requires,
        description="",
        outputs={"x": "popis x"},
        columns=[f"{name}.x"],
    )


FEATURES = [
    _feature("acoustic.pitch.f0", []),
    _feature("acoustic.timing.pauses", ["segments"]),
    _feature("acoustic.timing.speech_rate", ["transcript"]),
    _feature("linguistic.lexical.mattr", ["nlp"]),
]


def test_groups_and_selection(qtbot: QtBot) -> None:
    picker = FeaturePicker()
    qtbot.addWidget(picker)
    picker.set_features(FEATURES, {"acoustic.pitch.f0", "acoustic.timing.pauses"})
    assert picker.count() == 4
    assert picker.table.rowCount() == 7  # 3 skupiny + 4 feature
    assert picker.selected() == ["acoustic.pitch.f0", "acoustic.timing.pauses"]

    timing = picker.table.item(picker._group_rows["acoustic.timing"], COL_NAME)
    assert timing.text() == "Akustika · časování"
    assert timing.checkState() == Qt.CheckState.PartiallyChecked
    assert picker.table.item(picker._group_rows["acoustic.timing"], COL_REQUIRES).text() == "1 z 2"

    changes: list[int] = []
    picker.selection_changed.connect(lambda: changes.append(1))
    timing.setCheckState(Qt.CheckState.Checked)  # skupina vybere všechny členy
    assert "acoustic.timing.speech_rate" in picker.selected()
    assert timing.checkState() == Qt.CheckState.Checked
    picker.set_checked("acoustic.timing.pauses", False)
    assert timing.checkState() == Qt.CheckState.PartiallyChecked
    assert changes


def test_quick_picks_and_filter(qtbot: QtBot) -> None:
    picker = FeaturePicker()
    qtbot.addWidget(picker)
    picker.set_features(FEATURES, set())
    picker.quick_pick("all")
    assert len(picker.selected()) == 4
    picker.quick_pick("nomodels")
    assert picker.selected() == ["acoustic.pitch.f0"]
    picker.quick_pick("notranscript")
    assert picker.selected() == ["acoustic.pitch.f0", "acoustic.timing.pauses"]
    picker.quick_pick("none")
    assert picker.selected() == []

    picker.search.setText("mattr")
    hidden = [r for r in range(picker.table.rowCount()) if picker.table.isRowHidden(r)]
    assert len(hidden) == 5  # zůstala skupina lexikum a její jediná feature
    picker.search.setText("")
    assert not any(picker.table.isRowHidden(r) for r in range(picker.table.rowCount()))


def test_summary_and_current(qtbot: QtBot) -> None:
    picker = FeaturePicker()
    qtbot.addWidget(picker)
    picker.set_features(FEATURES, {"acoustic.timing.pauses"})
    picker.set_summary(["segments"], 1, "desítky sekund na nahrávku")
    assert "Segmentace" in picker.summary.text() and "1 feature, 1 sloupců" in picker.summary.text()
    current: list[str] = []
    picker.current_changed.connect(current.append)
    picker.table.setCurrentCell(picker._rows["acoustic.pitch.f0"], COL_NAME)
    assert current[-1] == "acoustic.pitch.f0"
    picker._link_activated("provider:segments")
    assert current[-1] == "segments"
    picker.set_warning("Není připraveno: x")
    assert not picker.warning.isHidden() or picker.warning.text()
