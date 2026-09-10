from __future__ import annotations

from pathlib import Path

import pytest

from speechscope_app.backend import command


def test_extract_args_full() -> None:
    req = command.ExtractRequest(
        inputs=[Path("data")],
        task="story",
        features=["acoustic.quality.*", "acoustic.pitch.f0"],
        config=Path("cfg.yaml"),
        sets=["segments.model=conformer"],
        vad=True,
        out=Path("out/story.csv"),
        work_dir=Path("work"),
        log_file=Path("run.log"),
    )
    args = command.extract_args(req, models_dir=Path("M"))
    assert args[:2] == ["--models-dir", "M"]
    assert args[2:4] == ["extract", "data"]
    assert "--features" in args
    assert args[args.index("--features") + 1] == "acoustic.quality.*,acoustic.pitch.f0"
    assert args[args.index("--set") + 1] == "segments.model=conformer"
    assert "--vad" in args and "--no-vad" not in args
    assert "--progress-json" in args
    assert args[args.index("--log-file") + 1] == "run.log"
    assert args[-2:] == ["--log-level", "INFO"]


def test_extract_args_no_vad_and_manifest() -> None:
    req = command.ExtractRequest(inputs=[Path("d")], manifest=Path("m.csv"), vad=False)
    args = command.extract_args(req)
    assert "--no-vad" in args and "--task" not in args and "--models-dir" not in args
    assert args[args.index("--manifest") + 1] == "m.csv"


def test_extract_requires_task_or_manifest() -> None:
    with pytest.raises(ValueError):
        command.extract_args(command.ExtractRequest(inputs=[Path("d")]))
    with pytest.raises(ValueError):
        command.extract_args(command.ExtractRequest(inputs=[], task="story"))


def test_segment_and_transcribe_args() -> None:
    req = command.PrepareRequest(inputs=[Path("d")], work_dir=Path("w"))
    seg = command.segment_args(req, model="pyannote", cut_audio=True, models_dir=Path("M"))
    assert seg[2:5] == ["segment", "d", "--model"] and "--cut-audio" in seg
    assert "--progress-json" in seg
    tr = command.transcribe_args(req, language="en")
    assert tr[:2] == ["transcribe", "d"] and tr[tr.index("--language") + 1] == "en"


def test_query_args() -> None:
    assert command.list_args(task="story") == ["list", "--task", "story", "--json"]
    assert command.params_args("acoustic.pitch.f0") == [
        "list",
        "--params",
        "acoustic.pitch.f0",
        "--json",
    ]
    assert command.providers_args() == ["list", "--providers", "--json"]
    assert command.doctor_args(models_dir=Path("M")) == ["--models-dir", "M", "doctor", "--json"]
    assert command.models_download_args(only=["onnx", "whisper"]) == [
        "models",
        "download",
        "--only",
        "onnx,whisper",
    ]


def test_models_unpack_args() -> None:
    assert command.models_unpack_args(Path("modely.zip"), models_dir=Path("M")) == [
        "--models-dir",
        "M",
        "models",
        "unpack",
        "modely.zip",
    ]
