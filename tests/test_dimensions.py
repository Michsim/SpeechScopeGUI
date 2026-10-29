"""Dimenze řeči: přibalená definice sedí na katalog knihovny, skóre vyšetření proti normě."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from test_norms import HNR, JIT, RATE, controls_frame

from speechscope_app import contract, i18n
from speechscope_app.backend.dimensions import (
    Dimension,
    Item,
    evaluate,
    load_dimensions,
    parse_dimensions,
    visit_values,
)
from speechscope_app.backend.library import Library
from speechscope_app.backend.norms import BOTH, HIGHER, LOWER, Norm, NormInfo


def test_bundled_dimensions_match_library_catalog(fake_library: Library) -> None:
    dims = load_dimensions()
    assert [d.key for d in dims] == [
        "voice",
        "articulation",
        "tempo",
        "pauses",
        "prosody",
        "breathing",
        "language",
    ]
    columns_by_task: dict[str, set[str]] = {}
    for task in contract.TASKS:
        columns_by_task[task] = {c for f in fake_library.features(task) for c in f.columns}
    for dim in dims:
        assert dim.items and dim.display_name
        for item in dim.items:
            for task in item.tasks:
                assert task in contract.TASKS, (dim.key, item.column, task)
                assert item.column in columns_by_task[task], (dim.key, item.column, task)
    i18n.activate("en")
    try:
        assert dims[0].display_name == "Voice quality"
    finally:
        i18n.activate("cs")


def test_parse_dimensions_validates() -> None:
    dims = parse_dimensions(
        {
            "dimensions": [
                {
                    "key": "x",
                    "name": "X",
                    "items": [
                        {"column": "a.b.c", "task": "story", "direction": "lower"},
                        {"column": "a.b.d", "tasks": ["ddk"], "log": True},
                        {"column": "a.b.e", "tasks": ["ddk"], "direction": "both"},
                    ],
                }
            ]
        }
    )
    assert dims[0].items[0].tasks == ("spontaneous",) and dims[0].items[0].direction == LOWER
    assert dims[0].items[1].direction == HIGHER and dims[0].items[1].log
    assert dims[0].items[2].direction == BOTH
    assert dims[0].items[0].feature == "a.b.c" and Item("a.b.c.d", ("ddk",)).feature == "a.b.c"
    with pytest.raises(ValueError):
        parse_dimensions({"dimensions": [{"key": "x", "name": "X", "items": [{"column": "c"}]}]})
    with pytest.raises(ValueError):
        parse_dimensions(
            {
                "dimensions": [
                    {
                        "key": "x",
                        "name": "X",
                        "items": [{"column": "c", "task": "ddk", "direction": "up"}],
                    }
                ]
            }
        )
    with pytest.raises(ValueError):
        parse_dimensions({"format": 99})


def test_visit_values_average_over_recordings() -> None:
    rows = pd.DataFrame(
        {"file": ["a", "b", "c", "d"], JIT: [1.0, 2.0, 6.0, "x"], "subject": ["p"] * 4}
    )
    values = visit_values({"story": rows})
    assert values == {"spontaneous": {JIT: 3.0}}  # průměr platných (medián by byl 2), alias


DIMS = [
    Dimension(
        "voice",
        "Kvalita hlasu",
        [Item(JIT, ("phonation",), HIGHER, log=True), Item(HNR, ("phonation",), LOWER)],
    ),
    Dimension("pauses", "Pauzy a tempo", [Item(RATE, ("spontaneous", "reading"), LOWER)]),
    Dimension("language", "Jazyk", [Item("linguistic.lexical.mattr", ("spontaneous",), LOWER)]),
]


def test_evaluate_scores_dimensions_and_explains_missing(tmp_path: Path) -> None:
    norm = Norm(NormInfo(name="Test"), controls_frame())
    phon = pd.DataFrame({"path": ["p.wav"], JIT: [3.0], HNR: [12.0]})
    read = pd.DataFrame({"path": ["r1.wav", "r2.wav"], RATE: [2.5, 2.7]})
    ev = evaluate(
        DIMS, {"phonation": phon, "reading": read}, norm, age=64, sex="muž", same_sex=True
    )
    voice, pauses, language = ev.results
    assert voice.shown and voice.score > 2 and voice.band == "bad" and len(voice.scored) == 2
    assert all(i.score.z > 0 for i in voice.items)  # obě položky otočené k patologii
    assert pauses.shown and pauses.items[0].task == "reading" and pauses.items[0].value == 2.6
    assert pauses.score == pytest.approx(pauses.items[0].score.z)  # jedna položka: SD kontrol 1
    assert pauses.control_sd == pytest.approx(1.0) and pauses.n_controls == pauses.items[0].score.n
    assert pauses.percentile == pytest.approx(pauses.items[0].score.percentile)
    # dvě málo korelované položky: průměr jejich z má u kontrol SD pod 1, skóre se přeškáluje
    assert voice.control_sd is not None and 0.5 < voice.control_sd < 1
    assert voice.score == pytest.approx(voice.raw / voice.control_sd, rel=0.05)
    assert voice.score > voice.raw and voice.percentile is not None
    assert not language.shown and language.reason == "potřebuje úlohu Spontánní řeč"
    assert [r.dimension.key for r in ev.shown] == ["voice", "pauses"]
    assert ev.missing == [language]
    assert set(ev.selections) == {"phonation", "reading"}
    phon_sel = ev.selections["phonation"]
    assert phon_sel.same_sex and phon_sel.widened and phon_sel.window == 15  # mužů v ±10 málo

    # norma bez úlohy: položka má důvod, dimenze chybí
    only_phon = Norm(NormInfo(name="Fonace"), controls_frame()[lambda f: f["task"] == "phonation"])
    ev2 = evaluate(DIMS, {"phonation": phon, "reading": read}, only_phon, age=64)
    assert ev2.results[1].reason == "norma nemá úlohu Čtení" and not ev2.results[1].shown
    # chybějící hodnota ve vyšetření
    ev3 = evaluate(DIMS, {"phonation": pd.DataFrame({JIT: [float("nan")], HNR: [20.0]})}, norm)
    item_jit, item_hnr = ev3.results[0].items
    assert item_jit.score is None and item_jit.reason == "hodnota chybí" and item_hnr.score
    assert ev3.selections["phonation"].window is None  # bez věku celá norma


def test_dimension_score_is_standardized_by_controls() -> None:
    """Průměr k nezávislých z-skóre má SD 1/√k; po standardizaci kontrolami je zase 1."""
    rng = np.random.default_rng(3)
    n = 200
    cols = [f"acoustic.x.y.c{i}" for i in range(4)]
    frame = pd.DataFrame({"subject": [f"HC{i}" for i in range(n)], "task": "phonation"})
    for c in cols:
        frame[c] = rng.normal(0, 1, n)
    norm = Norm(NormInfo(name="Nezávislé"), frame)
    dims = [Dimension("d", "D", [Item(c, ("phonation",), HIGHER) for c in cols])]
    patient = pd.DataFrame({c: [1.5] for c in cols})  # každá položka +1,5 SD
    ev = evaluate(dims, {"phonation": patient}, norm)
    (d,) = ev.results
    assert d.raw == pytest.approx(1.5, abs=0.15)
    assert d.control_sd == pytest.approx(0.5, abs=0.08)  # 1/√4
    assert d.score == pytest.approx(3.0, abs=0.4) and d.band == "bad"
    assert d.n_controls == n and d.percentile is not None and d.percentile > 95
    # kontrola bez jedné položky se do rozdělení nebere, dokud je úplných dost
    frame.loc[frame.index[:10], cols[0]] = float("nan")
    ev2 = evaluate(dims, {"phonation": patient}, Norm(NormInfo(name="Díry"), frame))
    assert ev2.results[0].n_controls == n - 10
    # pod MIN_REFERENCE úplných kontrol se berou i neúplné
    frame.loc[frame.index[10:], cols[1]] = float("nan")
    ev3 = evaluate(dims, {"phonation": patient}, Norm(NormInfo(name="Děravé"), frame))
    assert ev3.results[0].n_controls == n and ev3.results[0].score is not None


def test_evaluate_both_direction_item() -> None:
    """Položka `both`: pomalé i rychlé tempo vyjde stejně špatně, strana je ve signed_z."""
    norm = Norm(NormInfo(name="Test"), controls_frame())
    dims = [Dimension("tempo", "Tempo", [Item(RATE, ("reading",), BOTH)])]
    slow = evaluate(dims, {"reading": pd.DataFrame({RATE: [3.0]})}, norm).results[0]
    fast = evaluate(dims, {"reading": pd.DataFrame({RATE: [5.4]})}, norm).results[0]
    assert slow.shown and fast.shown and slow.band == "bad" and fast.band == "bad"
    assert slow.items[0].score.signed_z < 0 < fast.items[0].score.signed_z
    # standardizace kontrolami: |z| má u kontrol průměr ~0,8, typický pacient vyjde kolem nuly
    typical = evaluate(dims, {"reading": pd.DataFrame({RATE: [4.2]})}, norm).results[0]
    assert typical.control_sd is not None and typical.score < 0 and typical.band == "ok"
    assert slow.score > slow.raw  # |z| 2,4 minus průměr kontrol 0,8 děleno SD ~0,6
    assert slow.side == -1 and fast.side == 1  # strana pro šipku u osy pavouka
    # smíšené směry nebo položka bez both: bez strany
    mixed = Dimension("m", "M", [Item(RATE, ("reading",), BOTH), Item(HNR, ("phonation",), LOWER)])
    rows = {"reading": pd.DataFrame({RATE: [3.0]}), "phonation": pd.DataFrame({HNR: [12.0]})}
    assert evaluate([mixed], rows, norm).results[0].side == 0
