"""Výběr feature: skupiny vlevo, karty uprostřed."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from speechscope_app.backend.library import FeatureInfo
from speechscope_app.ui.widgets.feature_picker import ROLE_KEY, ROLE_KIND, FeaturePicker


def _feature(name: str, requires: list[str]) -> FeatureInfo:
    return FeatureInfo(
        name=name,
        version="1",
        tasks=["story"],
        requires=requires,
        description=f"popis {name.rsplit('.', 1)[-1]}",
        outputs={"x": "popis x"},
        columns=[f"{name}.x"],
    )


FEATURES = [
    _feature("acoustic.pitch.f0", []),
    _feature("acoustic.timing.pauses", ["segments"]),
    _feature("acoustic.timing.speech_rate", ["transcript"]),
    _feature("linguistic.lexical.mattr", ["nlp"]),
]


def _group_items(picker: FeaturePicker) -> dict[str, str]:
    out = {}
    for row in range(picker.groups.count()):
        item = picker.groups.item(row)
        if item.data(ROLE_KIND) == "group":
            out[item.data(ROLE_KEY)] = item.text()
    return out


def test_groups_and_cards(qtbot: QtBot) -> None:
    picker = FeaturePicker()
    qtbot.addWidget(picker)
    picker.set_features(FEATURES, {"acoustic.pitch.f0", "acoustic.timing.pauses"})
    assert picker.count() == 4
    assert picker.selected() == ["acoustic.pitch.f0", "acoustic.timing.pauses"]
    groups = _group_items(picker)
    assert set(groups) == {"acoustic.pitch", "acoustic.timing", "linguistic.lexical"}
    assert "1/2" in groups["acoustic.timing"] and "0/1" in groups["linguistic.lexical"]
    # první skupina je zobrazená, karty jen pro ni
    assert picker.current_group() == "acoustic.pitch"
    assert list(picker._cards) == ["acoustic.pitch.f0"]

    picker.show_group_of("acoustic.timing.pauses")
    assert picker.current_group() == "acoustic.timing"
    assert set(picker._cards) == {"acoustic.timing.pauses", "acoustic.timing.speech_rate"}
    assert picker.group_title.text() == "Akustika · časování"
    assert picker.group_count.text() == "1 z 2 vybráno"

    changes: list[int] = []
    picker.selection_changed.connect(lambda: changes.append(1))
    picker._cards["acoustic.timing.speech_rate"].check.setChecked(True)
    assert "acoustic.timing.speech_rate" in picker.selected() and changes
    assert picker.group_check.isChecked()
    picker.group_check.click()  # zruší všechny zobrazené
    assert not any(n.startswith("acoustic.timing") for n in picker.selected())

    current: list[str] = []
    picker.current_changed.connect(current.append)
    picker._cards["acoustic.timing.pauses"].clicked.emit("acoustic.timing.pauses")
    assert current[-1] == "acoustic.timing.pauses"


def test_quick_picks_search_and_providers(qtbot: QtBot) -> None:
    picker = FeaturePicker()
    qtbot.addWidget(picker)
    picker.set_features(FEATURES, set())
    picker.set_providers(["segments", "transcript"])
    picker.quick_pick("all")
    assert len(picker.selected()) == 4
    picker.quick_pick("nomodels")
    assert picker.selected() == ["acoustic.pitch.f0"]
    picker.quick_pick("notranscript")
    assert picker.selected() == ["acoustic.pitch.f0", "acoustic.timing.pauses"]
    picker.quick_pick("none")
    assert picker.selected() == []

    picker.search.setText("mattr")
    assert list(picker._cards) == ["linguistic.lexical.mattr"]
    assert "Hledání" in picker.group_title.text()
    picker.search.setText("popis")  # hledá i v popisu
    assert len(picker._cards) == 4
    picker.search.setText("")
    assert picker.current_group() == "acoustic.pitch"

    current: list[str] = []
    picker.current_changed.connect(current.append)
    picker.show_group_of("transcript")
    assert picker.current_provider() == "transcript" and current[-1] == "transcript"
    assert not picker._cards and "speech_rate" in picker._extras[0].text()
    picker._link_activated("provider:segments")
    assert current[-1] == "segments"


def test_summary_and_overridden(qtbot: QtBot) -> None:
    picker = FeaturePicker()
    qtbot.addWidget(picker)
    picker.set_features(FEATURES, {"acoustic.timing.pauses"})
    picker.set_providers(["segments"])
    picker.set_summary(["segments"], 1, "desítky sekund na nahrávku")
    assert "Segmentace" in picker.summary.text() and "1 feature, 1 sloupců" in picker.summary.text()
    picker.set_overridden({"acoustic.timing.pauses", "segments"})
    assert "(upraveno)" in picker.summary.text()
    groups = _group_items(picker)
    assert "●" in groups["acoustic.timing"] and "●" not in groups["acoustic.pitch"]
    picker.show_group_of("acoustic.timing.pauses")
    assert picker._cards["acoustic.timing.pauses"].modified.text()
    picker.set_overridden(set())
    assert "(upraveno)" not in picker.summary.text()
    assert not picker._cards["acoustic.timing.pauses"].modified.text()
    picker.set_warning("Není připraveno: x")
    assert picker.warning.text()


def test_card_short_description_and_details_signal(qtbot: QtBot) -> None:
    from speechscope_app.ui.feature_detail import FeatureDetailDialog, split_unit
    from speechscope_app.ui.widgets.feature_picker import short_description

    assert short_description("a b c", 10) == "a b c"
    long = "; ".join(f"slovo{i}" for i in range(40))
    cut = short_description(long, 60)
    assert cut.endswith("…") and len(cut) <= 61 and not cut[:-1].endswith(";")
    assert split_unit("Medián kontury F0 (půltóny)") == ("Medián kontury F0", "půltóny")
    assert split_unit("Podíl znělých rámců (0 až 1)") == ("Podíl znělých rámců", "0 až 1")
    assert split_unit("bez jednotky") == ("bez jednotky", "")

    picker = FeaturePicker()
    qtbot.addWidget(picker)
    picker.set_features(FEATURES, set())
    asked: list[str] = []
    picker.details_requested.connect(lambda info: asked.append(info.name))
    card = picker._cards["acoustic.pitch.f0"]
    card.cols.linkActivated.emit("#")
    assert asked == ["acoustic.pitch.f0"]
    assert card.text.toolTip() and len(card.text.text()) <= 151

    from speechscope_app.backend.library import Library, fake_command

    catalog = Library(fake_command()).features("phonation")
    info = next(f for f in catalog if f.name == "acoustic.pitch.f0")
    dialog = FeatureDetailDialog(info)
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == len(info.columns)
    names = [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]
    assert "median" in names and "median_hz" in names
    row = names.index("median")
    assert dialog.table.item(row, 1).text()  # jednotka z popisu
    dialog.search.setText("median")
    hidden = [dialog.table.isRowHidden(r) for r in range(dialog.table.rowCount())]
    assert hidden.count(False) == 2  # median a median_hz
