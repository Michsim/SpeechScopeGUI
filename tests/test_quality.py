"""Kontrola kvality nahrávek: prahy z `assets/quality.yaml` nad řádkem tabulky."""

from __future__ import annotations

from pathlib import Path

from speechscope_app.backend import quality
from speechscope_app.backend.protocol import Protocol
from speechscope_app.backend.tasks import CustomTask, registry


def _row(**values) -> dict:
    return {"task": "phonation", **values}


def test_prahy_jsou_pribalene_a_pokryvaji_vsechny_ulohy() -> None:
    checks = quality.load_checks()
    assert checks
    for task in ("phonation", "ddk", "reading", "spontaneous"):
        assert any(task in c.tasks for c in checks)
    assert quality.min_duration("phonation") == 5
    assert quality.min_duration("spontaneous") == 60
    assert quality.min_duration("neznama") is None
    for check in checks:
        assert (check.bad < check.warn) if check.kind == "min" else (check.bad > check.warn)


def test_fonace_kratka_a_znela_cast() -> None:
    ok = quality.assess(
        _row(
            **{
                "acoustic.recording.duration.total": 8.0,
                "acoustic.recording.duration.voiced": 6.0,
                "acoustic.recording.snr.snr_db": 30.0,
                "acoustic.recording.clipping.fraction": 0.0,
            }
        ),
        "phonation",
    )
    assert ok == [] and quality.level_of(ok) == ""
    issues = quality.assess(
        _row(
            **{
                "acoustic.recording.duration.total": 8.0,
                "acoustic.recording.duration.voiced": 3.1,
                "acoustic.recording.snr.snr_db": 8.0,
            }
        ),
        "phonation",
    )
    assert [i.check.key for i in issues] == ["snr", "phonation_voiced"]  # horší napřed
    assert [i.level for i in issues] == [quality.BAD, quality.WARN]
    assert quality.level_of(issues) == quality.BAD
    assert "3.1 s < 5 s" in issues[1].text() and "8 dB < 10 dB" in issues[0].text()


def test_chybejici_sloupec_se_preskoci_a_poznamka_je_upozorneni() -> None:
    assert quality.assess({"task": "reading"}, "reading") == []
    issues = quality.assess(
        {"notes": "acoustic.quality.cpp=málo znělé řeči; acoustic.pitch.f0=nic"}, "reading"
    )
    assert len(issues) == 1 and issues[0].check.key == quality.NOTES_KEY
    assert issues[0].level == quality.WARN
    assert "acoustic.quality.cpp, acoustic.pitch.f0" in issues[0].text()
    assert quality.assess({"notes": float("nan")}, "reading") == []


def test_ddk_a_spontanni_slova_a_prebuzeni() -> None:
    ddk = quality.assess({"acoustic.ddk.count.triplets": 2}, "ddk")
    assert ddk and ddk[0].level == quality.BAD and "2 < 3" in ddk[0].text()
    words = quality.assess({"acoustic.timing.speech_rate.word_count": 70}, "spontaneous")
    assert [i.level for i in words] == [quality.WARN]
    assert quality.assess({"acoustic.timing.speech_rate.word_count": 70}, "reading") == []
    clip = quality.assess({"acoustic.recording.clipping.fraction": 2.5}, "reading")
    assert clip[0].level == quality.BAD and "2.5 % > 1 %" in clip[0].text()


def test_vlastni_uloha_se_kontroluje_podle_zakladni() -> None:
    registry().register(CustomTask(key="popis", name="Popis obrázku", base="spontaneous"))
    issues = quality.assess({"acoustic.recording.duration.total": 20}, "popis")
    assert issues and issues[0].check.key == "spontaneous_total"
    assert quality.min_duration("popis") == 60


def test_vlastni_soubor_prahu_ma_prednost(tmp_path: Path) -> None:
    custom = tmp_path / "quality.yaml"
    custom.write_text(
        "format: 1\nchecks:\n  - key: x\n    column: acoustic.recording.duration.total\n"
        "    tasks: [phonation]\n    kind: min\n    warn: 10\n    bad: 1\n    name: Délka\n",
        encoding="utf-8",
    )
    checks = quality.load_checks(custom)
    assert [c.key for c in checks] == ["x"]
    assert (
        quality.assess({"acoustic.recording.duration.total": 8}, "phonation", checks)[0].level
        == quality.WARN
    )
    assert quality.load_checks(tmp_path / "neni.yaml") == quality.default_checks()


def test_feature_kvality_pro_beh() -> None:
    names = {"acoustic.recording.duration", "acoustic.recording.snr", "acoustic.ddk.count"}
    assert quality.quality_features("phonation", names) == [
        "acoustic.recording.duration",
        "acoustic.recording.snr",
    ]
    assert quality.quality_features("ddk", names)[-1] == "acoustic.ddk.count"
    assert quality.quality_features("phonation", set()) == []  # starší knihovna bez nich


def test_protokol_s_domenou_prida_feature_kvality_pres_vzor(tmp_path: Path) -> None:
    proto = Protocol(name="x", task="phonation", domain="acoustic")
    req = proto.to_request(
        [tmp_path / "a.wav"],
        out=tmp_path / "f.csv",
        work_dir=tmp_path,
        config_path=None,
        extra_features=["acoustic.recording.snr"],
    )
    assert req.features == ["acoustic.*", "acoustic.recording.snr"] and req.domain is None
    proto = Protocol(name="y", task="phonation", features=["acoustic.pitch.f0"])
    req = proto.to_request(
        [tmp_path / "a.wav"],
        out=tmp_path / "f.csv",
        work_dir=tmp_path,
        config_path=None,
        extra_features=["acoustic.pitch.f0", "acoustic.recording.snr"],
    )
    assert req.features == ["acoustic.pitch.f0", "acoustic.recording.snr"]
    plain = proto.to_request(
        [tmp_path / "a.wav"], out=tmp_path / "f.csv", work_dir=tmp_path, config_path=None
    )
    assert plain.features == ["acoustic.pitch.f0"]


def test_upravy_prahu_se_ukladaji_jako_odchylky(tmp_path: Path) -> None:
    from dataclasses import replace

    path = tmp_path / "quality.yaml"
    quality.use_user_file(path)
    checks = quality.load_checks()
    assert checks == quality.default_checks()
    changed = [replace(c, warn=4.0) if c.key == "phonation_voiced" else c for c in checks]
    changed = [replace(c, enabled=False) if c.key == "clipping" else c for c in changed]
    assert quality.save_checks(changed) == path
    text = path.read_text(encoding="utf-8")
    assert "phonation_voiced" in text and "clipping" in text and "snr" not in text
    quality.use_user_file(path)  # nové načtení
    loaded = {c.key: c for c in quality.load_checks()}
    assert loaded["phonation_voiced"].warn == 4.0 and not loaded["clipping"].enabled
    # vypnutá kontrola se nehlásí, upravený práh platí
    row = {
        "acoustic.recording.duration.voiced": 4.5,
        "acoustic.recording.clipping.fraction": 5.0,
    }
    assert quality.assess(row, "phonation") == []
    # bez odchylek se soubor smaže a platí přibalené
    assert quality.save_checks(quality.default_checks()) is None and not path.exists()
    assert quality.load_checks() == quality.default_checks()


def test_validace_prahu() -> None:
    from dataclasses import replace

    voiced = next(c for c in quality.default_checks() if c.key == "phonation_voiced")
    assert quality.validate(voiced) == ""
    assert "nejvýš" in quality.validate(replace(voiced, bad=6))
    clip = next(c for c in quality.default_checks() if c.key == "clipping")
    assert "aspoň" in quality.validate(replace(clip, bad=0.05))


def test_vadny_soubor_prahu_da_vychozi(tmp_path: Path) -> None:
    path = tmp_path / "quality.yaml"
    path.write_text("overrides: [nesmysl", encoding="utf-8")
    assert quality.load_checks(path) == quality.default_checks()
