"""Jazyk popisů z knihovny: `--lang` v argumentech, anglické fixtury, výchozí angličtina."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from speechscope_app.backend import command
from speechscope_app.backend.library import Library, fake_command
from speechscope_app.backend.settings import AppSettings


def test_lang_goes_before_subcommand(tmp_path: Path) -> None:
    args = command.list_args(models_dir=tmp_path, lang="en")
    assert args[:4] == ["--models-dir", str(tmp_path), "--lang", "en"] and args[4] == "list"
    assert "--lang" not in command.list_args(models_dir=tmp_path)
    assert command.params_args("nlp", lang="en")[:2] == ["--lang", "en"]


def test_fake_cli_serves_english_descriptions() -> None:
    def run(*args: str) -> list[dict]:
        proc = subprocess.run(
            [*fake_command(), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return json.loads(proc.stdout)

    cs = {f["name"]: f for f in run("list", "--json")}
    en = {f["name"]: f for f in run("--lang", "en", "list", "--json")}
    assert set(cs) == set(en)
    f0_cs, f0_en = cs["acoustic.pitch.f0"], en["acoustic.pitch.f0"]
    assert f0_cs["columns"] == f0_en["columns"]
    assert "Medián" in f0_cs["outputs"]["median"] and "Median" in f0_en["outputs"]["median"]
    # neznámý jazyk spadne na češtinu
    de = {f["name"]: f for f in run("--lang", "de", "list", "--json")}
    assert de["acoustic.pitch.f0"]["outputs"] == f0_cs["outputs"]


def test_library_wrapper_passes_language(tmp_path: Path) -> None:
    lib = Library(fake_command(), models_dir=tmp_path / "models", lang="en")
    outputs = {f.name: f.outputs for f in lib.features("phonation")}
    assert "Median" in outputs["acoustic.pitch.f0"]["median"]
    by_name = {p.name: p for p in lib.params("transcript").params}
    assert "Language code" in by_name["language"].description
    providers = {p.name: p for p in lib.providers()}
    nlp = {p.name: p for p in providers["nlp"].params}
    assert "Analysis language" in nlp["language"].description


def test_ui_language_defaults_to_english(tmp_path: Path) -> None:
    s = AppSettings(tmp_path / "s.ini")
    assert s.ui_language == "en"
    s.ui_language = ""  # podle systému, uložené výslovně
    assert AppSettings(tmp_path / "s.ini").ui_language == ""
    s.ui_language = "cs"
    assert AppSettings(tmp_path / "s.ini").ui_language == "cs"
    assert sys.executable  # jen ať je import využitý i bez zabalení
