"""Falešné CLI se musí chovat jako skutečné: stejné příkazy, kódy, tvary."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from speechscope_app import contract
from speechscope_app.backend.library import Library, LibraryError, subprocess_env


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = subprocess_env()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "speechscope_app.fake.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=full_env,
        check=False,
    )


def test_version() -> None:
    assert run("version").stdout.strip() == contract.KNOWN_LIBRARY_VERSION


def test_list_json_filters_by_task() -> None:
    everything = json.loads(run("list", "--json").stdout)
    phonation = json.loads(run("list", "--task", "phonation", "--json").stdout)
    assert {"name", "tasks", "requires", "outputs", "columns"} <= set(everything[0])
    assert 0 < len(phonation) < len(everything)
    assert all("phonation" in f["tasks"] for f in phonation)


def test_params_json_and_unknown() -> None:
    payload = json.loads(run("list", "--params", "acoustic.pitch.f0", "--json").stdout)
    assert payload["params"]["semitone_ref_mode"]["choices"] == ["fmin", "fixed", "median"]
    bare = json.loads(run("list", "--params", "linguistic.syntactic.mlu", "--json").stdout)
    assert bare["params"] == {}
    bad = run("list", "--params", "nope", "--json")
    assert bad.returncode == 2 and "neznámá feature ani provider" in bad.stderr


def test_providers_json_and_provider_params() -> None:
    providers = json.loads(run("list", "--providers", "--json").stdout)
    assert [p["name"] for p in providers] == ["nlp", "phonemes", "segments", "transcript"]
    assert "model" in providers[2]["params"]
    seg = json.loads(run("list", "--params", "segments", "--json").stdout)
    assert seg["kind"] == "provider"
    assert seg["params"]["model"]["choices"] == ["auto", "labels", "pyannote", "conformer"]
    f0 = json.loads(run("list", "--params", "acoustic.pitch.f0", "--json").stdout)
    assert f0["kind"] == "feature"


def test_doctor_json_and_models_dir(tmp_path: Path) -> None:
    proc = run("--models-dir", str(tmp_path), "doctor", "--json")
    info = json.loads(proc.stdout)
    assert proc.returncode == 0 and info["all_ready"]
    assert info["models_dir"] == str(tmp_path)
    missing = run("doctor", "--json", env={"SPEECHSCOPE_FAKE_DOCTOR": "missing"})
    assert missing.returncode == 1
    assert not json.loads(missing.stdout)["providers"]["transcript"]["ready"]


def test_extract_progress_json(recordings: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "story.csv"
    log = tmp_path / "run.log"
    proc = run(
        "extract", str(recordings), "--task", "story", "--domain", "acoustic",
        "--out", str(out), "--progress-json", "--log-file", str(log),
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    events = [contract.parse_event(line) for line in proc.stdout.splitlines()]
    kinds = [type(e).__name__ for e in events]
    assert kinds[0] == "StartEvent" and kinds[-2:] == ["DoneEvent", "SavedEvent"]
    start = events[0]
    assert isinstance(start, contract.StartEvent)
    assert start.total == 4 and start.task == "story"
    assert all(f.startswith("acoustic.") for f in start.features)
    assert "segments" in start.providers and "nlp" not in start.providers
    files = [e for e in events if isinstance(e, contract.FileEvent)]
    assert [f.ok for f in files] == [True, False, True, True]
    assert files[1].msg
    assert isinstance(events[-2], contract.DoneEvent) and events[-2].n_ok == 3
    assert isinstance(events[-1], contract.SavedEvent) and Path(events[-1].out) == out

    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 4
    assert rows[0]["file"] == "p01.wav" and "acoustic.pitch.f0.median" in rows[0]
    assert rows[1]["error"] and rows[2]["notes"]
    assert "linguistic.lexical.mattr" not in rows[0]
    assert "stahuji" not in log.read_text(encoding="utf-8")
    assert "vybráno" in log.read_text(encoding="utf-8")
    # stdout je čistý JSON, log jde jen na stderr
    assert "INFO" in proc.stderr and "INFO" not in proc.stdout


def test_extract_no_recursive(recordings: Path, tmp_path: Path) -> None:
    proc = run(
        "extract", str(recordings), "--task", "phonation", "--no-recursive",
        "--out", str(tmp_path / "o.csv"), "--progress-json",
    )  # fmt: skip
    start = contract.parse_event(proc.stdout.splitlines()[0])
    assert isinstance(start, contract.StartEvent) and start.total == 3


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        (["extract", "{d}", "--out", "{o}"], "zadej --task"),
        (["extract", "{d}", "--task", "phonation", "--features", "linguistic.*"], "vzoru"),
        (
            ["extract", "{d}", "--task", "phonation", "--features", "acoustic.timing.pauses"],
            "nedává smysl",
        ),
        (
            ["extract", "{d}", "--task", "story", "--set", "acoustic.pitch.f0.nope=1"],
            "nemá parametr",
        ),
        (["extract", "{d}", "--task", "story", "--set", "segments.nope=1"], "nemá parametr"),
        (["extract", "{d}", "--task", "story", "--config", "missing.yaml"], "neexistuje"),
        (["extract", "{d}/empty", "--task", "story"], "cesta neexistuje"),
        (["extract", "{d}", "--task", "cooking"], "neznámá úloha"),
    ],
)  # fmt: skip
def test_extract_errors_exit_2(
    recordings: Path, tmp_path: Path, args: list[str], fragment: str
) -> None:
    filled = [a.format(d=recordings, o=tmp_path / "o.csv") for a in args]
    proc = run(*filled, "--progress-json")
    assert proc.returncode == 2
    assert fragment in proc.stderr
    assert proc.stdout == ""


def test_extract_manifest_carries_metadata(recordings: Path, tmp_path: Path) -> None:
    manifest = recordings / "manifest.csv"
    manifest.write_text(
        "path,task,speaker_id,group\np01.wav,phonation,PD001,PD\nsub/p04.flac,story,HC001,HC\n",
        encoding="utf-8",
    )
    out = tmp_path / "m.csv"
    proc = run("extract", "--manifest", str(manifest), "--out", str(out), "--progress-json")
    assert proc.returncode == 0, proc.stderr
    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["speaker_id"] for r in rows] == ["PD001", "HC001"]
    assert rows[0]["task"] == "phonation" and rows[1]["task"] == "story"


def test_segment_and_transcribe_write_work_dir(recordings: Path, tmp_path: Path) -> None:
    work = tmp_path / "work"
    seg = run("segment", str(recordings), "--model", "conformer", "--work-dir", str(work),
              "--progress-json")  # fmt: skip
    assert seg.returncode == 0
    events = [contract.parse_event(line) for line in seg.stdout.splitlines()]
    assert isinstance(events[0], contract.StartEvent) and events[0].providers == ["segments"]
    assert (work / "segments" / "p01.txt").is_file()
    assert (work / "segments" / "p01.json").is_file()

    tr = run("transcribe", str(recordings), "--language", "cs", "--work-dir", str(work),
             "--progress-json")  # fmt: skip
    assert tr.returncode == 0
    assert (work / "transcript" / "p04.txt").read_text(encoding="utf-8")


def test_models_download_logs_progress() -> None:
    proc = run("models", "download", "--only", "onnx")
    assert proc.returncode == 0 and "stahuji onnx" in proc.stderr


def test_library_wrapper_reads_fake(fake_library: Library) -> None:
    assert fake_library.version_matches()
    assert fake_library.doctor()["all_ready"]
    names = {f.name for f in fake_library.features("phonation")}
    assert "acoustic.quality.cpp" in names and "acoustic.timing.pauses" not in names
    f0 = fake_library.params("acoustic.pitch.f0")
    assert f0.vad_supported and [p.name for p in f0.params][:2] == ["f_min", "f_max"]
    assert f0.params[0].bounds == {"gt": 0}
    assert {m["key"] for m in fake_library.models()["models"]} >= {"whisper", "onnx"}
    providers = fake_library.providers()
    assert [p.name for p in providers] == ["nlp", "phonemes", "segments", "transcript"]
    assert all(p.kind == "provider" for p in providers)
    transcript = fake_library.params("transcript")
    assert transcript.kind == "provider"
    assert next(p for p in transcript.params if p.name == "device").choices == [
        "auto",
        "cpu",
        "cuda",
    ]
    with pytest.raises(LibraryError):
        fake_library.params("nope")


def test_library_wrapper_missing_executable(tmp_path: Path) -> None:
    lib = Library([str(tmp_path / "neexistuje.exe")])
    with pytest.raises(LibraryError):
        lib.version()
