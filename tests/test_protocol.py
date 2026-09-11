from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from speechscope_app.backend.library import FeatureInfo, FeatureParams
from speechscope_app.backend.protocol import (
    Protocol,
    all_protocols,
    builtin_protocols,
    slugify,
    summarize,
)


def test_roundtrip(tmp_path: Path) -> None:
    proto = Protocol(
        name="Test",
        task="story",
        description="popis",
        features=["acoustic.quality.*"],
        vad=True,
        config={"acoustic.pitch.f0": {"f_min": 75}, "segments": {"model": "conformer"}},
    )
    path = tmp_path / "test.yaml"
    proto.save(path)
    loaded = Protocol.load(path)
    assert loaded.name == "Test" and loaded.task == "story" and loaded.vad is True
    assert loaded.features == ["acoustic.quality.*"]
    assert loaded.config == proto.config
    assert loaded.path == path


def test_config_yaml_is_library_shape() -> None:
    proto = Protocol(name="x", task="story", config={"segments": {"model": "pyannote"}})
    assert yaml.safe_load(proto.config_yaml()) == {"segments": {"model": "pyannote"}}
    assert proto.set_items() == ["segments.model=pyannote"]
    assert proto.segments_model() == "pyannote"


def test_to_request(tmp_path: Path) -> None:
    proto = Protocol(name="x", task="phonation", domain="acoustic", vad=False)
    req = proto.to_request(
        [tmp_path], out=tmp_path / "o.csv", work_dir=tmp_path / "w", config_path=None
    )
    assert req.task == "phonation" and req.domain == "acoustic" and req.vad is False


def test_unknown_task_rejected() -> None:
    with pytest.raises(ValueError):
        Protocol(name="x", task="cooking")


def test_builtin_protocols_load() -> None:
    protos = builtin_protocols()
    assert len(protos) >= 3 and all(p.builtin for p in protos)
    assert any(p.task == "phonation" for p in protos)


def test_broken_user_protocol_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "ok.yaml").write_text("name: A\ntask: story\n", encoding="utf-8")
    (tmp_path / "bad.yaml").write_text("name: B\ntask: cooking\n", encoding="utf-8")
    (tmp_path / "junk.yaml").write_text(":::", encoding="utf-8")
    names = [p.name for p in all_protocols(tmp_path) if not p.builtin]
    assert names == ["A"]


def _feature(name: str, requires: list[str], n: int = 2) -> FeatureInfo:
    return FeatureInfo(
        name=name,
        version="1",
        tasks=["story"],
        requires=requires,
        description="",
        outputs={f"o{i}": "" for i in range(n)},
        columns=[f"{name}.o{i}" for i in range(n)],
    )


CATALOG = [
    _feature("acoustic.pitch.f0", [], 3),
    _feature("acoustic.timing.pauses", ["segments"]),
    _feature("linguistic.lexical.mattr", ["nlp"], 1),
]
PROVIDERS = [
    FeatureParams("nlp", "provider", False, False, {}, [], requires=["transcript"]),
    FeatureParams("transcript", "provider", False, False, {}, [], requires=[]),
]


def test_protocol_select_uses_domain_and_patterns() -> None:
    names = [f.name for f in CATALOG]
    assert Protocol(name="a", task="story", domain="acoustic").select(names) == names[:2]
    assert Protocol(name="a", task="story", features=["*.mattr", "acoustic.pitch.*"]).select(
        names
    ) == ["linguistic.lexical.mattr", "acoustic.pitch.f0"]
    assert Protocol(name="a", task="story").select(names) == names


def test_summarize_counts_columns_and_provider_dependencies() -> None:
    s = summarize(["acoustic.pitch.f0"], CATALOG)
    assert s.providers == [] and s.columns == 3 and s.cost_hint() == "sekundy na nahrávku"
    s = summarize(["acoustic.pitch.f0", "acoustic.timing.pauses"], CATALOG)
    assert s.providers == ["segments"] and s.columns == 5
    assert s.cost_hint() == "desítky sekund na nahrávku"
    s = summarize(["linguistic.lexical.mattr"], CATALOG, PROVIDERS)
    assert s.providers == ["transcript", "nlp"] and s.cost_hint() == "minuty na nahrávku"


def test_slug() -> None:
    assert slugify("Pohádka, akustika i lingvistika") == "pohadka-akustika-i-lingvistika"
    assert slugify("   ") == "davka"


def test_describe_and_unique_name(tmp_path: Path) -> None:
    from speechscope_app.backend.protocol import describe, import_protocol, unique_name

    proto = Protocol(
        name="Test", task="story", domain="acoustic", config={"segments": {"model": "conformer"}}
    )
    info = describe(proto, CATALOG, PROVIDERS)
    assert info.providers == ["segments"]
    assert info.features_text.startswith("2 z 3 (Akustika 2)")
    assert info.params_text == "segments.model=conformer"
    assert describe(proto, [], None).features_text == "všechny pro doménu acoustic"

    assert unique_name("A", set()) == "A"
    assert unique_name("A", {"A", "A (2)"}) == "A (3)"

    folder = tmp_path / "protokoly"
    first = import_protocol(proto, folder, [])
    assert first == folder / "test.yaml" and first.is_file()
    existing = [Protocol.load(first)]
    second = import_protocol(proto, folder, existing)  # bez přepisu vedle
    assert second == folder / "test-2.yaml"
    third = import_protocol(proto, folder, existing, overwrite=True)
    assert third == first
