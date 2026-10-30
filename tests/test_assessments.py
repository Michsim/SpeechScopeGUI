"""Uložená hodnocení: soubor, řazení, přejmenovaný běh, dohledání řádku, nabídka nahrávek."""

from __future__ import annotations

import json
from pathlib import Path

from test_visits import make_run

from speechscope_app.backend.assessments import (
    Assessment,
    AssessmentStore,
    RecordingRef,
    candidates_for,
    prefill,
    resolve,
)
from speechscope_app.backend.catalog import Catalog
from speechscope_app.backend.visits import VisitIndex

JIT = "acoustic.quality.perturbation.jitter_local"


def runs(root: Path) -> VisitIndex:
    make_run(
        root,
        "2026-10-14_10-12-00_fonace",
        [
            {
                "file": "P1_a.wav",
                "task": "phonation",
                "subject": "P1",
                "age": 60,
                "sex": "žena",
                "visit": "2026-10-13",
                JIT: 1.0,
            },
            {"file": "P2_a.wav", "task": "phonation", "subject": "P2", JIT: 2.0},
            {"file": "x_P1_b.wav", "task": "phonation", JIT: 3.0},
        ],
    )
    make_run(root, "2026-10-15_09-00-00_cteni", [{"file": "P1_c.wav", "task": "reading"}])
    index = VisitIndex(root)
    index.refresh()
    return index


def test_store_roundtrip_order_relocate_delete(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite")
    store = AssessmentStore(tmp_path / "assessments", catalog)
    old = Assessment(
        "", "P1", 60.0, "f", "2026-10-13", [RecordingRef("run-a", 0, "P1_a.wav", "phonation")]
    )
    new = Assessment(
        "", "P2", None, "", "2026-10-20", [RecordingRef("run-b", 1, "P2_a.wav", "phonation")]
    )
    store.save(old)
    store.save(new)
    assert old.id and old.created
    loaded = store.all()
    assert [a.subject for a in loaded] == ["P2", "P1"]  # nejnovější vyšetření první
    assert loaded[1] == old
    assert store.relocate("run-a", "run-a2") == 1
    assert store.all()[1].recordings[0].run == "run-a2"
    assert [(p.subject, p.visits) for p in catalog.patients()] == [("P2", 1), ("P1", 1)]
    store.delete(new)
    assert [a.subject for a in store.all()] == ["P1"]
    assert [p.subject for p in catalog.patients()] == ["P1"]  # pacient bez vyšetření zmizí

    # starší JSONy ve složce: při otevření úložiště se naimportují a přesunou, rozbitý zůstane
    legacy = Assessment(
        "legacy1",
        " p1 ",
        61.0,
        "m",
        "2026-10-01",
        [RecordingRef("run-c", 0, "P1_c.wav", "reading")],
    )
    store.folder.mkdir(parents=True, exist_ok=True)
    (store.folder / "legacy1.json").write_text(json.dumps(legacy.to_dict()), encoding="utf-8")
    (store.folder / "rozbity.json").write_text("{", encoding="utf-8")
    (store.folder / "x.plan.json").write_text("{}", encoding="utf-8")
    again = AssessmentStore(store.folder, Catalog(tmp_path / "catalog.sqlite"))
    assert [a.id for a in again.all()] == [old.id, "legacy1"]
    assert (store.folder / "imported" / "legacy1.json").is_file()
    assert (store.folder / "rozbity.json").is_file() and (store.folder / "x.plan.json").is_file()
    assert [p.subject for p in again.catalog.patients()] == ["p1"]  # „ p1 “ je týž pacient jako P1
    assert len(AssessmentStore(store.folder, again.catalog).all()) == 2  # podruhé se nic nezdvojí


def test_resolve_prefill_and_candidates(tmp_path: Path) -> None:
    index = runs(tmp_path)
    recs = index.all_recordings()
    p1 = next(r for r in recs if r.file == "P1_a.wav")
    assert prefill(p1) == ("P1", 60.0, "f", "2026-10-13")  # visit z manifestu
    p2 = next(r for r in recs if r.file == "P2_a.wav")
    assert prefill(p2) == ("P2", None, "", "2026-10-14")  # bez visit: datum běhu

    a = Assessment(
        "x",
        "P1",
        recordings=[
            RecordingRef.of(p1),
            RecordingRef(
                "2026-10-14_10-12-00_fonace", 7, "x_P1_b.wav", "phonation"
            ),  # řádek posunutý
            RecordingRef("smazany-beh", 0, "P1_z.wav", "ddk"),
        ],
    )
    visit, missing = resolve(a, recs)
    assert [r.file for r in visit.recordings] == ["P1_a.wav", "x_P1_b.wav"]
    assert [m.file for m in missing] == ["P1_z.wav"]
    assert visit.subject == "P1" and visit.count("phonation") == 2

    manifest, by_name, others = candidates_for("phonation", "P1", recs)
    assert [r.file for r in manifest] == ["P1_a.wav"]
    assert [r.file for r in by_name] == ["x_P1_b.wav"]
    assert [r.file for r in others] == ["P2_a.wav"]
    assert candidates_for("reading", "", recs)[2][0].file == "P1_c.wav"
