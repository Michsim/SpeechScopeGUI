"""Vyhrazené sloupce metadat: aliasy názvů, pohlaví a věk."""

from __future__ import annotations

from speechscope_app.backend import meta


def test_canonical_column_aliases_and_passthrough() -> None:
    assert meta.canonical_column("Pacient") == "subject"
    assert meta.canonical_column("speaker_id") == "subject"
    assert meta.canonical_column("Věk") == "age"
    assert meta.canonical_column("Pohlaví") == "sex"
    assert meta.canonical_column("Návštěva") == "visit"
    assert meta.canonical_column("Skupina") == "group"
    assert meta.canonical_column("AGE") == "age"
    assert meta.canonical_column("UPDRS III") == "UPDRS III"  # cizí sloupec beze změny


def test_parse_sex_and_age() -> None:
    assert meta.parse_sex("Ž") == "f" and meta.parse_sex("female") == "f"
    assert meta.parse_sex("muž") == "m" and meta.parse_sex("M") == "m"
    assert meta.parse_sex("") == "" and meta.parse_sex(None) == "" and meta.parse_sex("?") == ""
    assert meta.parse_sex(float("nan")) == ""
    assert meta.parse_age("64") == 64.0 and meta.parse_age("64,5") == 64.5
    assert meta.parse_age(70) == 70.0 and meta.parse_age(float("nan")) is None
    assert meta.parse_age("") is None and meta.parse_age("abc") is None
    assert meta.parse_age("1964") is None  # rok narození není věk
