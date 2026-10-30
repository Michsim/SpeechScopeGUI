"""Vývoj v čase: tabulka dimenze × vyšetření z hotových vyhodnocení, změny, vynechaná data."""

from __future__ import annotations

import pandas as pd
import pytest
from test_dimensions import DIMS
from test_norms import HNR, JIT, RATE, controls_frame

from speechscope_app.backend.assessments import Assessment
from speechscope_app.backend.dimensions import Item, evaluate
from speechscope_app.backend.longitudinal import (
    ValueRow,
    build_timeline,
    build_value_timeline,
    format_change,
)
from speechscope_app.backend.norms import BOTH, Norm, NormInfo


def _evaluation(norm: Norm, jit: float, hnr: float, rate: float | None = None):  # noqa: ANN202
    rows = {"phonation": pd.DataFrame({"path": ["p.wav"], JIT: [jit], HNR: [hnr]})}
    if rate is not None:
        rows["reading"] = pd.DataFrame({"path": ["r.wav"], RATE: [rate]})
    return evaluate(DIMS, rows, norm, age=64, sex="m")


def test_build_timeline_orders_visits_and_computes_changes() -> None:
    norm = Norm(NormInfo(name="Test"), controls_frame())
    pert = "acoustic.quality.perturbation"
    outdated_norm = Norm(
        NormInfo(name="Test", feature_versions={"phonation": {pert: "1.0"}}), controls_frame()
    )
    later = Assessment("b", "P1", 65, "m", "2026-10-14", created="2026-10-14T10:00:00")
    earlier = Assessment("a", "P1", 64, "m", "14. 10. 2025", created="2025-10-14T10:00:00")
    undated = Assessment("c", "P1", 64, "m", "", created="2025-01-01T10:00:00")
    ev_later = _evaluation(norm, 3.0, 12.0, 2.5)  # s čtením: Pauzy a tempo má hodnotu
    ev_earlier = evaluate(
        DIMS,
        {"phonation": pd.DataFrame({"path": ["p.wav"], JIT: [1.5], HNR: [16.0]})},
        outdated_norm,
        age=64,
        sex="m",
        versions={"phonation": {pert: "1.1"}},
    )
    timeline = build_timeline(
        [(later, ev_later), (undated, _evaluation(norm, 1, 20)), (earlier, ev_earlier)]
    )
    assert [v.date for v in timeline.visits] == [
        "2025-10-14",
        "2026-10-14",
    ]  # podle data, ne pořadí
    assert timeline.skipped == 1 and timeline.enough
    assert [v.outdated for v in timeline.visits] == [True, False]
    assert [v.assessment_id for v in timeline.outdated] == ["a"]
    voice, pauses, language = timeline.rows
    assert voice.dimension.key == "voice" and voice.shown
    assert voice.scores[0] is not None and voice.scores[1] is not None
    assert voice.scores[1] > voice.scores[0]  # horší hodnoty = vyšší skóre
    assert voice.change_last == voice.change_total == voice.scores[1] - voice.scores[0]
    assert voice.last == voice.scores[1] and voice.first == voice.scores[0]
    assert voice.last_band == ev_later.results[0].band and voice.abnormal
    assert pauses.scores == [None, pauses.scores[1]] and pauses.change_last is None
    assert pauses.change_total is None and pauses.shown
    assert not language.shown and language.scores == [None, None]
    assert [r.dimension.key for r in timeline.shown_rows()] == ["voice", "pauses"]
    assert all(r.abnormal for r in timeline.shown_rows(only_abnormal=True))

    # jedno vyšetření s datem nestačí
    single = build_timeline([(later, ev_later)])
    assert not single.enough and len(single.visits) == 1
    assert format_change(None) == "–" and format_change(0.04) == "0,0 SD"
    assert format_change(0.5) == "▲ +0,5 SD" and format_change(-1.25) == "▼ -1,2 SD"


def test_build_value_timeline_items_changes_and_versions() -> None:
    """Měřítko Hodnoty: položky v jednotkách bez normy, směr změny, jiná verze feature."""
    pert = "acoustic.quality.perturbation"
    later = Assessment("b", "P1", 65, "m", "2026-10-14", created="2026-10-14T10:00:00")
    earlier = Assessment("a", "P1", 64, "m", "14. 10. 2025", created="2025-10-14T10:00:00")
    undated = Assessment("c", "P1", 64, "m", "", created="2025-01-01T10:00:00")
    rows_later = {
        "phonation": pd.DataFrame({"path": ["p.wav", "q.wav"], JIT: [3.0, 1.0], HNR: [12.0, 14.0]}),
        "reading": pd.DataFrame({"path": ["r.wav"], RATE: [2.5]}),
    }
    rows_earlier = {"phonation": pd.DataFrame({"path": ["p.wav"], JIT: [1.5], HNR: [16.0]})}
    timeline = build_value_timeline(
        DIMS,
        [
            (later, rows_later, {"phonation": {pert: "1.1"}}),
            (undated, rows_earlier, {}),
            (earlier, rows_earlier, {"phonation": {pert: "1.0"}}),
        ],
    )
    assert [v.date for v in timeline.visits] == ["2025-10-14", "2026-10-14"]
    assert timeline.skipped == 1 and timeline.enough
    groups = timeline.groups()
    assert [(d.key, [r.item.column for r in rows]) for d, rows in groups] == [
        ("voice", [JIT, HNR]),
        ("pauses", [RATE]),
    ]
    jit, hnr = groups[0][1]
    assert jit.values == [1.5, 2.0]  # dvě nahrávky fonace = průměr
    assert jit.change_last == 0.5 and jit.side(jit.change_last) == 1  # vyšší = horší
    assert hnr.values == [16.0, 13.0] and hnr.side(hnr.change_total) == 1  # nižší = horší
    assert hnr.change_percent == pytest.approx(-18.75)
    # 2025 má perturbace 1.0, poslední vyšetření 1.1: starší bod dutý, vyšetření označené
    assert jit.outdated == [True, False] and [v.outdated for v in timeline.visits] == [True, False]
    rate = groups[1][1][0]
    assert rate.values == [None, 2.5] and rate.change_last is None
    assert rate.change_percent is None and rate.side(None) == 0 and rate.outdated == [False, False]
    both = ValueRow(DIMS[0], Item(RATE, ("reading",), BOTH), "reading", [1.0, 2.0])
    assert both.side(both.change_total) == 0 and both.change_percent == 100.0
    # jedno vyšetření s datem nestačí, bez položek prázdné skupiny
    single = build_value_timeline(DIMS, [(later, rows_later, {})])
    assert not single.enough and len(single.groups()) == 2
    assert build_value_timeline(DIMS, [(later, {}, {}), (earlier, {}, {})]).groups() == []
