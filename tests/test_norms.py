"""Normy zdravých kontrol: výběr podle věku a pohlaví, z-skóre, stavba a soubory."""

from __future__ import annotations

import math
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from speechscope_app.backend import meta
from speechscope_app.backend import norms as norms_mod
from speechscope_app.backend.norms import (
    BOTH,
    HIGHER,
    LOWER,
    Norm,
    NormInfo,
    age_histogram,
    band,
    build_norm,
    export_norm,
    import_norm,
    norm_problems,
    read_norm_file,
    task_summaries,
    trash_norm,
    user_norms,
)

JIT = "acoustic.quality.perturbation.jitter_local"
HNR = "acoustic.quality.perturbation.hnr"
RATE = "acoustic.timing.speech_rate.speech_rate"


def controls_frame(n: int = 60, seed: int = 1) -> pd.DataFrame:
    """Syntetické kontroly: věk 40 až 85, střídavě ženy a muži, dvě úlohy."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        age = 40 + (i * 45) // (n - 1)
        sex = "f" if i % 2 == 0 else "m"
        rows.append(
            {
                "subject": f"HC{i:03d}",
                "task": "phonation",
                "age": age,
                "sex": sex,
                JIT: float(np.exp(rng.normal(math.log(0.8), 0.4))),
                HNR: float(rng.normal(20, 3)),
            }
        )
        rows.append(
            {
                "subject": f"HC{i:03d}",
                "task": "reading",
                "age": age,
                "sex": sex,
                RATE: float(rng.normal(4.2, 0.5)),
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def norm() -> Norm:
    return Norm(NormInfo(name="Test"), controls_frame())


def test_select_age_window_and_sex(norm: Norm) -> None:
    sel = norm.select("phonation", age=64)  # výchozí ±5: 60 kontrol 40 až 85 dá v okně 15
    assert sel.window == 5 and not sel.widened and sel.n >= 15
    lo, hi = sel.age_range
    assert lo >= 59 and hi <= 69
    wide = norm.select("phonation", age=64, window=10)
    assert wide.window == 10 and not wide.widened and wide.n > sel.n
    both = norm.select("phonation", age=64, sex="m", same_sex=True)
    assert both.same_sex and both.n_female == 0 and both.n_male == both.n
    # bez věku celá norma
    whole = norm.select("phonation")
    assert whole.window is None and whole.n == 60
    # neznámé pohlaví: přepínač se tiše ignoruje
    assert not norm.select("phonation", age=64, sex="", same_sex=True).same_sex


def test_select_widens_when_few_controls() -> None:
    norm = Norm(NormInfo(name="Malá"), controls_frame(n=24))
    sel = norm.select("phonation", age=85)  # na kraji věku je v okně ±5 jen pár lidí
    assert sel.widened and sel.n >= 15 and sel.window in (10, 15, 20, None)
    sel = norm.select("phonation", age=85, window=10)  # rozšiřuje se jen nad zvolené okno
    assert sel.widened and sel.window in (15, 20, None)
    tiny = Norm(NormInfo(name="Mini"), controls_frame(n=6))
    sel = tiny.select("phonation", age=60)
    assert sel.window is None and sel.n == 6  # ani bez okna není dost, vrátí se vše


def test_score_direction_log_and_percentile(norm: Norm) -> None:
    sel = norm.select("phonation")
    worse = sel.score(JIT, 3.0, direction=HIGHER, log=True)
    assert worse is not None and worse.z > 2 and worse.percentile > 95 and worse.band == "bad"
    better = sel.score(JIT, 0.3, direction=HIGHER, log=True)
    assert better is not None and better.z < 0 and better.band == "ok"
    low_hnr = sel.score(HNR, 12.0, direction=LOWER)
    assert low_hnr is not None and low_hnr.z > 2  # nižší HNR = horší, z kladné
    assert sel.score(HNR, 20.0, direction=LOWER).band == "ok"
    assert sel.score(JIT, None) is None and sel.score(JIT, float("nan")) is None
    assert sel.score(JIT, -1.0, log=True) is None  # logaritmus záporného čísla
    assert sel.score("acoustic.nope", 1.0) is None
    assert band(0.99) == "ok" and band(1.0) == "warn" and band(2.0) == "bad"
    # strana odchylky zůstává i po otočení
    assert worse.mean == pytest.approx(sel.stats(JIT).mean) and worse.n == 60
    assert low_hnr.signed_z < 0 and worse.signed_z > 0


def test_score_both_directions(norm: Norm) -> None:
    """Směr BOTH: odchylka na kteroukoli stranu je horší, z v absolutní hodnotě."""
    sel = norm.select("reading")
    slow = sel.score(RATE, 3.0, direction=BOTH)
    fast = sel.score(RATE, 5.4, direction=BOTH)
    assert slow is not None and fast is not None
    assert slow.z > 2 and slow.signed_z < 0 and slow.band == "bad"
    assert fast.z > 2 and fast.signed_z > 0 and fast.band == "bad"
    assert slow.percentile > 95 and fast.percentile > 95
    typical = sel.score(RATE, 4.2, direction=BOTH)
    assert typical is not None and typical.z < 0.5 and typical.percentile < 40
    # kontroly: |z| po osobách, průměr nad nulou
    cz = sel.control_z(RATE, direction=BOTH)
    assert len(cz) == sel.n and (cz >= 0).all() and 0.6 < cz.mean() < 1.0
    plain = sel.control_z(RATE, direction=LOWER)
    assert abs(float(plain.mean())) < 1e-9 and float(plain.std(ddof=1)) == pytest.approx(1.0)


def test_build_norm_from_run_tables_with_aliases(tmp_path: Path) -> None:
    run = pd.DataFrame(
        {
            "file": ["a.wav", "b.wav", "c.wav", "d.wav"],
            "path": ["C:/x/a.wav", "C:/x/b.wav", "C:/x/c.wav", "C:/x/d.wav"],
            "task": ["story", "story", "story", "story"],
            "Pacient": ["HC1", "HC2", "PD1", ""],
            "Věk": ["61", "70,5", "66", "50"],
            "Pohlaví": ["Ž", "muž", "F", "M"],
            "skupina": ["HC", "HC", "PD", "HC"],
            "speechscope_version": ["0.4.0"] * 4,
            RATE: [4.1, 3.9, 2.5, 4.0],
        }
    )
    norm = build_norm([run], NormInfo(name="Vlastní"), group_column="skupina", group_value="HC")
    assert norm.tasks == ["spontaneous"]  # alias úlohy převedený
    assert norm.subjects == 2  # PD1 vyřazen skupinou, prázdný pacient vyřazen
    assert list(norm.controls.columns) == [
        "task",
        meta.SUBJECT,
        meta.AGE,
        meta.SEX,
        meta.GROUP,
        RATE,
    ]
    assert norm.controls[meta.AGE].tolist() == [61.0, 70.5]
    assert norm.controls[meta.SEX].tolist() == ["f", "m"]
    assert "path" not in norm.controls and "file" not in norm.controls
    assert norm.info.created
    problems = norm_problems(norm)
    assert any("jen 2 kontrol" in p for p in problems)

    folder = tmp_path / "norms" / "vlastni"
    norm.save(folder)
    assert (folder / "norm.yaml").is_file() and (folder / "controls.csv").is_file()
    loaded = user_norms(tmp_path / "norms")
    assert len(loaded) == 1 and loaded[0].info.name == "Vlastní" and loaded[0].subjects == 2
    assert loaded[0].path == folder and not loaded[0].builtin

    with pytest.raises(ValueError):
        build_norm([run.drop(columns=["Pacient"])], NormInfo(name="bez pacienta"))
    with pytest.raises(ValueError):
        build_norm([run], NormInfo(name="nic"), group_column="skupina", group_value="XY")


def test_norm_info_roundtrip_and_display_name() -> None:
    info = NormInfo(name="Norma", names={"en": "Norm"}, source="SAMI", library_version="0.4.0")
    data = info.to_dict()
    assert data["name_en"] == "Norm" and data["format"] == 1
    copy = NormInfo.from_dict(data)
    assert copy == info
    with pytest.raises(ValueError):
        NormInfo.from_dict({"name": "x", "format": 99})


def test_task_summaries_and_age_histogram(norm: Norm) -> None:
    summaries = task_summaries(norm)
    assert [s.task for s in summaries] == ["phonation", "reading"]  # pořadí úloh knihovny
    phon = summaries[0]
    assert phon.subjects == 60 and phon.female == 30 and phon.male == 30
    assert phon.age_range == (40.0, 85.0) and phon.columns == 2
    assert summaries[1].columns == 1
    bins = age_histogram(norm)
    assert bins[0][0] == 40 and bins[-1][0] == 80 and sum(n for _s, n in bins) == 60
    assert age_histogram(Norm(NormInfo(name="x"), norm.controls.drop(columns=["age"]))) == []


def test_export_import_and_trash(tmp_path: Path, norm: Norm, monkeypatch) -> None:
    norm.info = NormInfo(name="Zdravé kontroly", source="SAMI")
    target = export_norm(norm, tmp_path / "out" / "kontroly.ssnorm")
    folder = tmp_path / "norms"
    first = import_norm(target, folder)
    second = import_norm(target, folder)  # podruhé vedle, nepřepíše
    assert first.path == folder / "zdrave-kontroly" and second.path == folder / "zdrave-kontroly-2"
    loaded = user_norms(folder)
    assert len(loaded) == 2 and loaded[0].subjects == 60 and loaded[0].info.source == "SAMI"

    broken = tmp_path / "rozbita.ssnorm"
    broken.write_text("není zip", encoding="utf-8")
    with pytest.raises(ValueError):
        read_norm_file(broken)
    with zipfile.ZipFile(tmp_path / "pul.ssnorm", "w") as zf:
        zf.writestr("norm.yaml", "name: jen popis\n")
    with pytest.raises(ValueError):
        read_norm_file(tmp_path / "pul.ssnorm")

    trashed: list[Path] = []
    monkeypatch.setattr(norms_mod, "send_to_trash", trashed.append)
    trash_norm(first)
    assert trashed == [first.path]
    first.builtin = True
    with pytest.raises(ValueError):
        trash_norm(first)
