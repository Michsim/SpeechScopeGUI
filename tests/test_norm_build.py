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


# --- norma z hotových běhů, přidané kontroly ----------------------------------------


def _run(tmp_path: Path, name: str, rows: list[dict], task: str = "phonation"):  # noqa: ANN202
    """Hotový běh se zadanými řádky, protokolem a run.json; vrátí RunInfo."""
    from speechscope_app.backend.history import read_run

    run = tmp_path / "work" / name
    run.mkdir(parents=True)
    (run / "speechscope.log").write_text("", encoding="utf-8")
    proto = default_protocol(task, builtin_protocols())
    proto.save(run / "protocol.yaml")
    write_run_file(run, status="ok", kind="extract", protocol=proto.name, total=len(rows))
    frame = pd.DataFrame([{"file": Path(r["path"]).name, "task": task, **r} for r in rows])
    frame.to_csv(run / "features.csv", index=False)
    info = read_run(run)
    assert info is not None
    return info


def _rec(tmp_path: Path, name: str) -> str:
    path = tmp_path / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF")
    return str(path)


HNR = "acoustic.quality.perturbation.hnr"


def test_norm_from_runs_filters_group_and_prefers_newer_run(tmp_path: Path) -> None:
    from speechscope_app.backend.norm_build import (
        controls_from_scan,
        plan_from_runs,
        run_columns,
        scan_runs,
    )

    a, b, p = _rec(tmp_path, "HC01.wav"), _rec(tmp_path, "HC02.wav"), _rec(tmp_path, "PD01.wav")
    older = _run(
        tmp_path,
        "2026-10-14_10-00-00_fonace",
        [
            {"path": a, "subject": "HC01", "age": 60, "sex": "m", "group": "HC", HNR: 10.0},
            {"path": p, "subject": "PD01", "age": 66, "sex": "f", "group": "PD", HNR: 5.0},
        ],
    )
    newer = _run(
        tmp_path,
        "2026-10-20_10-00-00_fonace-doplneni",
        [
            {"path": a, "subject": "HC01", "age": 60, "sex": "m", "group": "HC", HNR: 20.0},
            {"path": b, "subject": "HC02", "age": "", "sex": "f", "group": "HC", HNR: 21.0},
        ],
    )
    runs = {"phonation": [newer, older]}
    assert run_columns([older, newer])["group"] == ["HC", "PD"]
    scan = scan_runs(runs, "group", "HC")
    assert scan.outside == 1 and len(scan.rows) == 2  # PD vynechán, HC01 jen jednou
    assert {r.run_dir for r in scan.rows} == {newer.dir}  # novější běh vyhrál
    controls = controls_from_scan(scan)
    assert controls["HC02"].age is None
    controls["HC02"].age = 58  # doplněno ručně v průvodci
    plan, problems = plan_from_runs(
        NormInfo(name="Z běhů"), tmp_path / "norms" / "z-behu", runs, scan, controls
    )
    assert problems == [] and [t.key for t in plan.tasks] == ["phonation"]
    assert plan.all_ok and plan.remaining() == []  # nic se nepočítá
    plan.save()
    norm = finish_plan(plan)
    assert norm.subjects == 2
    people = norm.controls.set_index("subject")
    assert people.loc["HC01", HNR] == 20.0  # z novějšího běhu
    assert people.loc["HC02", "age"] == 58  # věk z tabulky kontrol přebil prázdnou buňku
    assert "PD01" not in people.index
    # přepočet: dávky z běhů se dají spočítat znovu, nahrávky jsou na disku
    loaded = load_plan(plan.folder)
    assert loaded is not None and loaded.missing_inputs() == []


def test_two_runs_of_one_task_are_two_batches(tmp_path: Path) -> None:
    from speechscope_app.backend.norm_build import plan_from_runs, scan_runs, task_of_key

    a, b = _rec(tmp_path, "HC01.wav"), _rec(tmp_path, "HC02.wav")
    r1 = _run(tmp_path, "2026-10-14_10-00-00_f1", [{"path": a, "subject": "HC01", HNR: 1.0}])
    r2 = _run(tmp_path, "2026-10-15_10-00-00_f2", [{"path": b, "subject": "HC02", HNR: 2.0}])
    runs = {"phonation": [r1, r2]}
    plan, _ = plan_from_runs(NormInfo(name="N"), tmp_path / "n", runs, scan_runs(runs), {})
    assert [t.key for t in plan.tasks] == ["phonation", "phonation#1"]
    assert task_of_key("phonation#1") == "phonation" and task_of_key("ddk@2026-10-01") == "ddk"
    plan.save()
    assert [t.key for t in NormPlan.load(plan.folder).tasks] == ["phonation", "phonation#1"]
    assert finish_plan(plan).subjects == 2


def test_extend_plan_skips_or_replaces_and_cancel_drops(tmp_path: Path) -> None:
    from speechscope_app.backend.norm_build import Extension, extend_plan, plan_from_runs, scan_runs

    a, b = _rec(tmp_path, "HC01.wav"), _rec(tmp_path, "HC01_novy.wav")
    c = _rec(tmp_path, "HC03.wav")
    base = _run(tmp_path, "2026-10-14_10-00-00_f", [{"path": a, "subject": "HC01", HNR: 1.0}])
    runs = {"phonation": [base]}
    plan, _ = plan_from_runs(NormInfo(name="N"), tmp_path / "n", runs, scan_runs(runs), {})
    plan.save()
    finish_plan(plan)

    def ext() -> Extension:
        proto = plan.protocol_for("phonation")
        assert proto is not None
        return Extension(
            [TaskPlan("phonation", proto, [Path(a), Path(b), Path(c)])],
            {Path(a): "HC01", Path(b): "HC01", Path(c): "HC03"},
            {"HC03": Control("HC03", 70.0, "f")},
        )

    # přeskočit: HC01 už v normě je (a stejná nahrávka vždy), přidá se jen HC03
    assert extend_plan(plan, ext(), replace=False) == (1, 2)
    added = plan.tasks[-1]
    assert added.added and added.key == "phonation#1" and added.inputs == [Path(c)]
    assert plan.recomputing and plan.extending and plan.controls["HC03"].age == 70.0
    plan.cancel_recompute()  # zrušit: přidané zmizí, norma zůstane
    assert [t.key for t in plan.tasks] == ["phonation"] and Path(c) not in plan.files

    # nahradit: nová nahrávka HC01 nahradí starou, až má dávka tabulku
    assert extend_plan(plan, ext(), replace=True) == (2, 1)
    added = plan.tasks[-1]
    assert added.inputs == [Path(b), Path(c)] and added.replaces == [Path(a)]
    newer = _run(
        tmp_path,
        "2026-10-21_10-00-00_f-pridane",
        [{"path": b, "subject": "HC01", HNR: 9.0}, {"path": c, "subject": "HC03", HNR: 3.0}],
    )
    added.status, added.run_dir = OK, newer.dir
    norm = finish_plan(plan)
    people = norm.controls.set_index("subject")
    assert norm.subjects == 2 and people.loc["HC01", HNR] == 9.0  # stará HC01 vypadla
    assert not plan.recomputing and not plan.extending
    assert Path(a) not in plan.files and [t.key for t in plan.tasks] == ["phonation#1"]


def test_run_without_subject_column_uses_file_names(tmp_path: Path) -> None:
    from speechscope_app.backend.norm_build import scan_runs

    a, b = _rec(tmp_path, "HC01_ddk.wav"), _rec(tmp_path, "HC02_ddk.wav")
    run = _run(tmp_path, "2026-10-27_23-58-05_ddk", [{"path": a}, {"path": b}], task="ddk")
    scan = scan_runs({"ddk": [run]})  # analýza bez manifestu: žádný sloupec subject
    assert [r.subject for r in scan.rows] == ["HC01", "HC02"]
    assert scan.from_name == 2 and scan.no_subject == 0
    assert [r.subject for r in scan_runs({"ddk": [run]}, rule="stem").rows] == [
        "HC01_ddk",
        "HC02_ddk",
    ]


def test_norm_protocols_from_norm_installed_or_missing(tmp_path: Path) -> None:
    """Protokol kontrol: celý z normy, jinak nainstalovaný stejného jména, jinak chybí."""
    from speechscope_app.backend.norm_build import norm_protocols
    from speechscope_app.backend.norms import Norm, NormInfo
    from speechscope_app.backend.protocol import Protocol, builtin_protocols

    stored = Protocol(name="Fonace, kontroly", task="phonation", features=["acoustic.*"])
    ddk = next(p for p in builtin_protocols() if p.task == "ddk")
    info = NormInfo(
        name="N",
        protocols={"phonation": stored.name, "ddk": ddk.name, "reading": "Čtení, cizí"},
        protocol_data={"phonation": stored.to_dict()},
    )
    norm = Norm(info, pd.DataFrame({"subject": [], "task": []}))
    found, missing = norm_protocols(norm, builtin_protocols())
    assert found["phonation"].name == stored.name and found["phonation"].features == ["acoustic.*"]
    assert found["ddk"].name == ddk.name
    assert missing == {"reading": "Čtení, cizí"}
    # protocol_data jde do norm.yaml i zpět
    again = NormInfo.from_dict(info.to_dict())
    assert again.protocol_data["phonation"]["name"] == stored.name


def test_assign_controls_only_from_manifest(tmp_path: Path) -> None:
    """Kontrola jen z manifestu; bez pacienta se nehádá, jde doplnit nebo vynechat."""
    from speechscope_app.backend.norm_build import FROM_MANUAL, FROM_NAME, assign_controls

    phon, ddk = tmp_path / "fonace", tmp_path / "ddk"
    for folder, names in ((phon, ["HC01_a.wav", "HC02_a.wav"]), (ddk, ["x.wav", "y.wav"])):
        folder.mkdir()
        for name in names:
            (folder / name).write_bytes(b"RIFF")
    (ddk / "manifest.csv").write_text("path;Pacient\nx.wav;HC02\ny.wav;\n", encoding="utf-8")
    scans = {"phonation": scan_folder(phon), "ddk": scan_folder(ddk)}
    found = assign_controls(scans)
    assert found.files == {ddk / "x.wav": "HC02"} and list(found.controls) == ["HC02"]
    reasons = {p.name: r for _t, p, r in found.loose}
    assert reasons == {"HC01_a.wav": "bez manifestu", "HC02_a.wav": "bez manifestu",
                       "y.wav": "prázdný pacient"}  # fmt: skip
    assert found.source_of() == "manifest"
    # ručně doplněná a vynechaná nahrávka; zbytek nouzově z názvu
    found = assign_controls(
        scans,
        rule="prefix",
        assigned={ddk / "y.wav": "HC01"},
        excluded={phon / "HC02_a.wav"},
    )
    assert found.files[ddk / "y.wav"] == "HC01" and phon / "HC02_a.wav" not in found.files
    assert found.files[phon / "HC01_a.wav"] == "HC01" and FROM_NAME in found.sources["HC01"]
    assert FROM_MANUAL in found.sources["HC01"] and found.source_of() == FROM_NAME
    # doplněná zůstává v seznamu (✓ v průvodci), odhadnutá z názvu ne
    assert {p.name for _t, p, _r in found.loose} == {"y.wav"}


def test_manifest_source_splits_tasks_and_rekeys_by_name(tmp_path: Path) -> None:
    """Jeden manifest pro všechny úlohy; odjinud se páruje i podle jedinečného názvu."""
    from speechscope_app.backend.norm_build import manifest_source, rekey_manifest

    data = tmp_path / "nahravky"
    data.mkdir()
    for name in ("HC01_a.wav", "HC01_b.wav", "HC02_a.wav"):
        (data / name).write_bytes(b"RIFF")
    path = tmp_path / "jinde" / "kontroly.csv"
    path.parent.mkdir()
    path.write_text(
        "path;pacient;task;věk\n"
        f"{data / 'HC01_a.wav'};HC01;phonation;64\n"
        f"{data / 'HC01_b.wav'};HC01;ddk;64\n"
        f"{data / 'HC02_a.wav'};HC02;phonation;58\n"
        f"{data / 'chybi.wav'};HC03;phonation;50\n",
        encoding="utf-8",
    )
    source = manifest_source(path)
    assert sorted(source.scans) == ["ddk", "phonation"] and source.missing == 1
    assert source.recordings == 3 and source.subjects == 2
    # manifest bez úlohy a s relativními cestami jinam: řádek podle názvu souboru
    other = tmp_path / "jinde" / "udaje.csv"
    other.write_text("path;pacient\nstare/HC02_a.wav;HC02\n", encoding="utf-8")
    from speechscope_app.backend.manifest import read_manifest

    rekeyed = rekey_manifest(read_manifest(other, keep_missing=True), [data / "HC02_a.wav"])
    assert rekeyed.meta_for(data / "HC02_a.wav") == {"subject": "HC02"}


def test_runs_without_subject_skip_when_names_off(tmp_path: Path) -> None:
    from speechscope_app.backend.norm_build import scan_runs

    a = _rec(tmp_path, "HC01_ddk.wav")
    run = _run(tmp_path, "2026-10-27_23-58-05_ddk", [{"path": a}], task="ddk")
    scan = scan_runs({"ddk": [run]}, rule="")
    assert not scan.rows and scan.unnamed == 1


def test_norm_info_keeps_controls_source() -> None:
    info = NormInfo(name="N", controls_from="name")
    assert NormInfo.from_dict(info.to_dict()).controls_from == "name"
    assert NormInfo.from_dict({"name": "Stará"}).controls_from == ""
