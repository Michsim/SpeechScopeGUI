"""Vlastní úlohy: registr, norma s vlastní úlohou, dimenze přes základní úlohu."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from test_norms import HNR, JIT, RATE, controls_frame

from speechscope_app.backend import tasks
from speechscope_app.backend.dimensions import Dimension, Item, evaluate
from speechscope_app.backend.history import read_run, write_run_file
from speechscope_app.backend.manifest import read_manifest
from speechscope_app.backend.norm_build import OK, NormPlan, TaskPlan, finish_plan
from speechscope_app.backend.norms import LOWER, Norm, NormInfo, task_summaries
from speechscope_app.backend.protocol import builtin_protocols
from speechscope_app.backend.tasks import CustomTask, TaskRegistry
from speechscope_app.backend.visits import VisitIndex

OBRAZEK = CustomTask("popis-obrazku", "Popis obrázku", "reading", "Čtení, akustika")


def test_registry_keys_labels_and_file(tmp_path: Path) -> None:
    reg = TaskRegistry()
    reg.load(tmp_path / "tasks.json")  # soubor ještě není
    assert reg.keys() == ["phonation", "ddk", "spontaneous", "reading"]
    assert reg.new_key("Popis obrázku") == "popis-obrazku"
    assert reg.new_key("Čtení") == "cteni" and reg.new_key("reading") == "reading-2"
    reg.register(OBRAZEK, save=True)
    assert reg.new_key("Popis obrázku") == "popis-obrazku-2"
    assert reg.label("popis-obrazku") == "Popis obrázku" and reg.label("reading") == "Čtení"
    assert reg.base("popis-obrazku") == "reading" and reg.base("story") == "spontaneous"
    assert reg.is_custom("popis-obrazku") and not reg.is_custom("reading")
    assert reg.order("popis-obrazku") == 4 and reg.order("ddk") == 1 and reg.order("x") == 99
    # stejný klíč z normy nastavení nepřepíše
    other = CustomTask("popis-obrazku", "Jiný název", "spontaneous")
    assert reg.register(other).name == "Popis obrázku"
    again = TaskRegistry()
    again.load(tmp_path / "tasks.json")
    assert [t.key for t in again.custom] == ["popis-obrazku"]
    assert again.get("popis-obrazku").protocol == "Čtení, akustika"
    with pytest.raises(ValueError):
        CustomTask.from_dict({"key": "reading", "name": "x", "base": "reading"})
    with pytest.raises(ValueError):
        CustomTask.from_dict({"key": "x", "name": "x", "base": "singing"})


def test_norm_records_custom_task_and_registers_it_on_load(tmp_path: Path) -> None:
    controls = controls_frame()
    extra = controls[controls["task"] == "reading"].copy()
    extra["task"] = "popis-obrazku"
    info = NormInfo(name="S obrázkem", custom_tasks=[OBRAZEK], protocols={"popis-obrazku": "x"})
    Norm(info, pd.concat([controls, extra])).save(tmp_path / "norma")
    tasks.registry().clear()
    norm = Norm.load(tmp_path / "norma")
    assert norm.tasks == ["phonation", "popis-obrazku", "reading"]
    assert norm.info.custom_tasks[0].key == "popis-obrazku"
    assert tasks.registry().get("popis-obrazku").base == "reading"  # import normy úlohu zná
    assert [s.task for s in task_summaries(norm)] == ["phonation", "reading", "popis-obrazku"]


def test_evaluate_uses_custom_task_through_its_base(tmp_path: Path) -> None:
    tasks.registry().register(OBRAZEK)
    controls = controls_frame()
    extra = controls[controls["task"] == "reading"].copy()
    extra["task"] = "popis-obrazku"
    extra[RATE] = extra[RATE] + 1.0  # kontroly obrázku mluví rychleji než při čtení
    norm = Norm(NormInfo(name="N", custom_tasks=[OBRAZEK]), pd.concat([controls, extra]))
    dims = [
        Dimension("tempo", "Tempo", [Item(RATE, ("spontaneous", "reading"), LOWER)]),
        Dimension("voice", "Hlas", [Item(HNR, ("phonation",), LOWER)]),
    ]
    rows = {
        "reading": pd.DataFrame({RATE: [4.2]}),
        "popis-obrazku": pd.DataFrame({RATE: [4.2]}),
        "phonation": pd.DataFrame({HNR: [20.0], JIT: [0.8]}),
    }
    ev = evaluate(dims, rows, norm)
    tempo = ev.results[0]
    assert [i.task for i in tempo.items] == ["reading", "popis-obrazku"]
    assert set(ev.selections) == {"reading", "popis-obrazku", "phonation"}
    by_task = {i.task: i for i in tempo.items}
    # stejná hodnota: proti čtení průměr, proti obrázku (kontroly o 1 rychlejší) pomalejší = horší
    assert abs(by_task["reading"].score.z) < 0.5 < by_task["popis-obrazku"].score.z
    # norma bez vlastní úlohy: položka s důvodem, do skóre nejde
    ev2 = evaluate(dims, rows, Norm(NormInfo(name="Bez"), controls))
    missing = next(i for i in ev2.results[0].items if i.task == "popis-obrazku")
    assert missing.score is None and "Popis obrázku" in missing.reason


def test_finish_plan_writes_custom_task_key_into_controls(tmp_path: Path) -> None:
    tasks.registry().register(OBRAZEK)
    proto = next(p for p in builtin_protocols() if p.task == "reading")
    run = tmp_path / "run"
    run.mkdir()
    pd.DataFrame(
        {
            "file": ["HC01.wav", "HC02.wav"],
            "task": "reading",
            "subject": ["HC01", "HC02"],
            RATE: [4, 5],
        }
    ).to_csv(run / "features.csv", index=False)
    plan = NormPlan(
        NormInfo(name="Obrázky", custom_tasks=[OBRAZEK]),
        tmp_path / "norma",
        [TaskPlan("popis-obrazku", proto.to_dict(), [], status=OK, run_dir=run)],
    )
    norm = finish_plan(plan, "0.4.0")
    assert norm.tasks == ["popis-obrazku"] and norm.info.protocols == {"popis-obrazku": proto.name}
    assert (tmp_path / "norma" / "norm.yaml").read_text(encoding="utf-8").count(
        "popis-obrazku"
    ) >= 2


def test_run_of_custom_task_is_indexed_under_its_key(tmp_path: Path) -> None:
    tasks.registry().register(OBRAZEK)
    run = tmp_path / "2026-10-14_10-40-00_obrazek"
    run.mkdir()
    (run / "speechscope.log").write_text("", encoding="utf-8")
    write_run_file(run, status="ok", kind="extract", protocol="P", total=1, task="popis-obrazku")
    pd.DataFrame(
        [{"file": "P-017_obrazek.wav", "task": "reading", "subject": "P-017", RATE: 3.1}]
    ).to_csv(run / "features.csv", index=False)
    info = read_run(run)
    assert info is not None and info.task == "popis-obrazku"
    index = VisitIndex(tmp_path)
    index.refresh()
    recs = index.all_recordings()
    assert [r.task for r in recs] == ["popis-obrazku"]
    # manifest s vlastní úlohou projde, neznámá ne
    (tmp_path / "a.wav").write_bytes(b"RIFF")
    (tmp_path / "manifest.csv").write_text(
        "path,task\na.wav,popis-obrazku\na.wav,zpev\n", encoding="utf-8"
    )
    manifest = read_manifest(tmp_path / "manifest.csv")
    assert list(manifest.tasks.values()) == ["popis-obrazku"]
    assert any("zpev" in p for p in manifest.problems)
