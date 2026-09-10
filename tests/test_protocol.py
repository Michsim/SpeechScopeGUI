from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from speechscope_app.backend.protocol import Protocol, all_protocols, builtin_protocols


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
