"""Nová norma ze složek: kontroly z názvů souborů, plán, manifest dávek, sestavení."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from speechscope_app.backend import meta
from speechscope_app.backend.history import write_run_file
from speechscope_app.backend.norm_build import (
    CANCELLED,
    ERROR,
    OK,
    PENDING,
    Control,
    NormPlan,
    TaskPlan,
    default_protocol,
    finish_plan,
    guess_controls,
    load_plan,
    pending_plans,
    read_controls_csv,
    scan_folder,
    subject_from_name,
    with_language,
)
from speechscope_app.backend.norms import NormInfo, user_norms
from speechscope_app.backend.protocol import builtin_protocols


def test_subject_rules() -> None:
    p = Path("RBD239_6Ra.wav")
    assert subject_from_name(p, "prefix") == "RBD239"
    assert subject_from_name(p, "stem") == "RBD239_6Ra"
    assert subject_from_name(Path("HC014fonace2.wav"), "letters") == "HC014"


def test_default_protocol_and_language() -> None:
    protocols = builtin_protocols()
    spont = default_protocol("spontaneous", protocols)
    assert spont is not None and "lingvistika" in spont.name  # i dimenze Jazyk
    assert default_protocol("phonation", protocols).name == "Fonace, základní"
    copy = with_language(spont, "en")
    assert copy.config["transcript"]["language"] == "en" and copy.config["nlp"]["language"] == "en"
    assert spont.config.get("transcript", {}).get("language") != "en"  # originál beze změny


def test_guess_controls_from_names_and_manifest(tmp_path: Path) -> None:
    phon, ddk = tmp_path / "fonace", tmp_path / "ddk"
    for folder, names in ((phon, ["HC01_a.wav", "HC02_a.wav"]), (ddk, ["HC01_b.wav", "x.wav"])):
        folder.mkdir()
        for name in names:
            (folder / name).write_bytes(b"RIFF")
    (ddk / "manifest.csv").write_text(
        "path;Pacient;Věk;Pohlaví\nx.wav;HC02;71;žena\n", encoding="utf-8"
    )
    scans = {"phonation": scan_folder(phon), "ddk": scan_folder(ddk)}
    assert len(scans["phonation"].recordings) == 2 and scans["ddk"].manifest is not None
    files, controls = guess_controls(scans, "prefix", {"HC01": Control("HC01", 64, "m")})
    assert files[ddk / "x.wav"] == "HC02"  # manifest má přednost před názvem
    assert list(controls) == ["HC01", "HC02"]
    assert controls["HC01"].age == 64 and controls["HC01"].sex == "m"  # ručně vyplněné zůstane
    assert controls["HC02"].age == 71 and controls["HC02"].sex == "f"


def test_read_controls_csv(tmp_path: Path) -> None:
    path = tmp_path / "kontroly.csv"
    path.write_text("﻿id;vek;pohlavi\nHC01;64,5;M\nHC02;;žena\n;50;m\n", encoding="utf-8")
    loaded = read_controls_csv(path)
    assert loaded["HC01"].age == 64.5 and loaded["HC01"].sex == "m"
    assert loaded["HC02"].age is None and loaded["HC02"].sex == "f" and len(loaded) == 2
    bad = tmp_path / "bez.csv"
    bad.write_text("jmeno;vek\nA;1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_controls_csv(bad)


def make_plan(tmp_path: Path) -> NormPlan:
    rec = tmp_path / "fonace" / "HC01_a.wav"
    rec.parent.mkdir(parents=True)
    rec.write_bytes(b"RIFF")
    proto = default_protocol("phonation", builtin_protocols())
    assert proto is not None
    return NormPlan(
        info=NormInfo(name="Kontroly", source="SAMI", language="cs"),
        folder=tmp_path / "norms" / "kontroly",
        tasks=[
            TaskPlan("phonation", proto.to_dict(), [rec]),
            TaskPlan("ddk", default_protocol("ddk", builtin_protocols()).to_dict(), []),
        ],
        files={rec: "HC01"},
        controls={"HC01": Control("HC01", 64.0, "m")},
    )


def test_plan_roundtrip_manifest_and_pending(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    plan.save()
    assert [p.folder for p in pending_plans(tmp_path / "norms")] == [plan.folder]
    loaded = NormPlan.load(plan.folder)
    assert loaded.info.name == "Kontroly" and loaded.controls["HC01"].age == 64.0
    assert loaded.tasks[0].proto().name == "Fonace, základní"
    manifest = loaded.manifest_for(loaded.tasks[0])
    row = manifest.meta_for(tmp_path / "fonace" / "HC01_a.wav")
    assert row == {meta.SUBJECT: "HC01", meta.AGE: "64", meta.SEX: "m"}
    assert [t.task for t in loaded.remaining()] == ["phonation", "ddk"]
    loaded.set_status("phonation", OK, tmp_path / "run1")
    assert NormPlan.load(plan.folder).tasks[0].status == OK
    assert not loaded.all_finished and loaded.done == 1


def test_finish_plan_builds_norm_from_finished_runs(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    run = tmp_path / "run1"
    run.mkdir()
    pd.DataFrame(
        [
            {
                "file": "HC01_a.wav",
                "path": "x",
                "task": "phonation",
                "subject": "HC01",
                "age": 64,
                "sex": "m",
                "acoustic.quality.perturbation.hnr": 20.0,
            }
        ]
    ).to_csv(run / "features.csv", index=False)
    plan.tasks[0].status, plan.tasks[0].run_dir = OK, run
    plan.tasks[1].status = ERROR  # DDK spadla, sestaví se z hotových
    assert plan.all_finished and not plan.all_ok and plan.buildable
    plan.save()
    norm = finish_plan(plan, "0.4.0")
    assert norm.subjects == 1 and norm.tasks == ["phonation"]
    assert norm.info.protocols == {"phonation": "Fonace, základní"}
    assert norm.info.library_version == "0.4.0"
    assert pending_plans(tmp_path / "norms") == []  # hotová už není rozpracovaná
    saved = user_norms(tmp_path / "norms")[0]
    assert saved.info.protocols == {"phonation": "Fonace, základní"}
    plan.tasks[0].run_dir = None
    plan.tasks[0].status = PENDING
    with pytest.raises(ValueError):
        finish_plan(plan)


def test_finish_plan_records_feature_versions_from_runs(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    run = tmp_path / "2026-10-30_10-00-00_norma-kontroly-fonace"
    run.mkdir()
    (run / "speechscope.log").write_text("", encoding="utf-8")
    write_run_file(
        run,
        status="ok",
        kind="extract",
        protocol="P",
        total=1,
        library_version="0.4.0",
        feature_versions={"acoustic.quality.perturbation": "1.1"},
    )
    pd.DataFrame(
        [
            {
                "file": "HC01_a.wav",
                "path": "x",
                "task": "phonation",
                "subject": "HC01",
                "age": 64,
                "sex": "m",
                "acoustic.quality.perturbation.hnr": 20.0,
            }
        ]
    ).to_csv(run / "features.csv", index=False)
    plan.tasks[0].status, plan.tasks[0].run_dir = OK, run
    plan.tasks[1].status = ERROR
    norm = finish_plan(plan)  # bez verze od GUI: vezme se z run.json
    assert norm.info.feature_versions == {"phonation": {"acoustic.quality.perturbation": "1.1"}}
    assert norm.info.library_version == "0.4.0"
    saved = user_norms(tmp_path / "norms")[0]
    assert saved.info.feature_versions == norm.info.feature_versions


def test_recompute_resets_plan_and_keeps_old_norm_until_finished(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    run = tmp_path / "run1"
    run.mkdir()
    pd.DataFrame(
        [{"file": "HC01_a.wav", "path": "x", "task": "phonation", "subject": "HC01", "age": 64}]
    ).to_csv(run / "features.csv", index=False)
    plan.tasks[0].status, plan.tasks[0].run_dir = OK, run
    plan.tasks[1].status = ERROR
    plan.save()
    finish_plan(plan)
    norms_dir = tmp_path / "norms"
    assert load_plan(plan.folder) is not None and pending_plans(norms_dir) == []
    assert load_plan(tmp_path) is None  # složka bez plánu

    plan.reset_for_recompute()
    loaded = load_plan(plan.folder)
    assert loaded is not None and loaded.recomputing
    assert all(t.status == PENDING and t.run_dir is None for t in loaded.tasks)
    assert loaded.missing_inputs() == []
    # norma se počítá znovu: zase rozpracovaná, stará norma ale pořád existuje
    assert [p.folder for p in pending_plans(norms_dir)] == [plan.folder]
    assert user_norms(norms_dir)[0].info.name == "Kontroly"

    # zrušení přepočtu: dávky zrušené, norma zůstává, už není rozpracovaná
    loaded.tasks[0].status = OK
    loaded.cancel_recompute()
    again = load_plan(plan.folder)
    assert not again.recomputing and [t.status for t in again.tasks] == [OK, CANCELLED]
    assert pending_plans(norms_dir) == []

    # přepočet dokončený: finish_plan vlajku zruší, datum je dnešní
    again.reset_for_recompute()
    again.tasks[0].status, again.tasks[0].run_dir = OK, run
    again.tasks[1].status = OK
    norm = finish_plan(again)
    assert norm.info.created == dt.date.today().isoformat()
    assert not load_plan(plan.folder).recomputing and pending_plans(norms_dir) == []

    # chybějící nahrávka
    (tmp_path / "fonace" / "HC01_a.wav").unlink()
    assert load_plan(plan.folder).missing_inputs() == [tmp_path / "fonace" / "HC01_a.wav"]
