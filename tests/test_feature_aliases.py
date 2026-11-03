"""Dřívější jména feature (pauzy před 2. 11. 2026) se převádějí na dnešní."""

from __future__ import annotations

import pandas as pd

from speechscope_app import contract
from speechscope_app.backend.dimensions import _parse_item
from speechscope_app.backend.norms import normalize_controls
from speechscope_app.backend.protocol import Protocol


def test_canonical_names() -> None:
    assert contract.canonical_feature("acoustic.timing.pauses") == "acoustic.pauses.all"
    assert contract.canonical_feature("acoustic.timing.*") == "acoustic.timing.*"
    assert contract.canonical_feature("acoustic.timing.rhythm") == "acoustic.timing.rhythm"
    assert (
        contract.canonical_column("acoustic.timing.breaths.per_minute")
        == "acoustic.pauses.breath.per_minute"
    )
    # jen celé jméno feature, ne předpona jiného jména
    assert contract.canonical_column("acoustic.timing.pausesx.a") == "acoustic.timing.pausesx.a"


def test_versions_renamed_only() -> None:
    versions = contract.canonical_versions(
        {"acoustic.timing.pauses": "1.2", "acoustic.timing.silences": "0.9", "x.y.z": "1.0"}
    )
    # přejmenování verzi nemění, starší algoritmus zůstane starší verzí
    assert versions == {
        "acoustic.pauses.all": "1.3",
        "acoustic.pauses.silent": "0.9",
        "x.y.z": "1.0",
    }


def test_frame_and_row() -> None:
    frame = pd.DataFrame({"file": ["a.wav"], "acoustic.timing.pauses.median": [0.4]})
    assert list(contract.canonical_frame(frame).columns) == ["file", "acoustic.pauses.all.median"]
    both = pd.DataFrame(
        {"acoustic.timing.pauses.median": [1.0], "acoustic.pauses.all.median": [2.0]}
    )
    assert contract.canonical_frame(both)["acoustic.pauses.all.median"].tolist() == [2.0]
    row = contract.canonical_row({"file": "a", "acoustic.timing.breaths.median": 1.0})
    assert row == {"file": "a", "acoustic.pauses.breath.median": 1.0}


def test_old_norm_dimension_and_protocol() -> None:
    controls = normalize_controls(
        pd.DataFrame(
            {"subject": ["k1"], "task": ["reading"], "acoustic.timing.pauses.median": [0.5]}
        )
    )
    assert "acoustic.pauses.all.median" in controls
    item = _parse_item(
        {"column": "acoustic.timing.pauses.median", "tasks": ["reading"], "direction": "higher"}
    )
    assert item.column == "acoustic.pauses.all.median"
    proto = Protocol.from_dict(
        {
            "name": "x",
            "task": "reading",
            "features": ["acoustic.timing.pauses"],
            "config": {"acoustic.timing.pauses": {"min_pause": 0.25}},
        }
    )
    assert proto.features == ["acoustic.pauses.all"]
    assert "acoustic.pauses.all" in proto.config
