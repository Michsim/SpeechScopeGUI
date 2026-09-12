"""Zachytí fixtury falešné knihovny ze skutečné knihovny, česky i anglicky.

Spouští `speechscope.exe` z prostředí knihovny (`..\\SpeechScope\\SpeechScope\\.venv`)
se složkou modelů uživatele, aby `doctor` hlásil skutečný stav. Parametry
se ukládají jen pro feature a providery, které nějaké mají; ostatní si
falešná knihovna doplní sama.

    uv run python packaging/capture_fixtures.py [--library-exe CESTA] [--models-dir CESTA]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "src" / "speechscope_app" / "fake" / "fixtures"
LANGS = ("cs", "en")


def default_exe() -> Path:
    return ROOT.parent / "SpeechScope" / "SpeechScope" / ".venv" / "Scripts" / "speechscope.exe"


def default_models() -> Path:
    """Složka modelů repa knihovny (kompletní sada), jinak modely uživatele."""
    repo_models = ROOT.parent / "SpeechScope" / "SpeechScope" / "models"
    if repo_models.is_dir():
        return repo_models
    base = os.environ.get("LOCALAPPDATA", str(Path.home()))
    return Path(base) / "SAMI" / "SpeechScopeApp" / "models"


def run(exe: Path, models: Path, lang: str, args: list[str], *, ok_codes=(0,)) -> str:
    env = dict(os.environ, PYTHONUTF8="1")
    cmd = [str(exe), "--models-dir", str(models), "--lang", lang, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    if proc.returncode not in ok_codes:
        sys.exit(f"{' '.join(args)} skončil kódem {proc.returncode}:\n{proc.stderr}")
    return proc.stdout


def save(name: str, text: str) -> None:
    path = FIXTURES / name
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(text)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("  ", path.relative_to(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library-exe", type=Path, default=default_exe())
    ap.add_argument("--models-dir", type=Path, default=default_models())
    ns = ap.parse_args()
    exe, models = ns.library_exe, ns.models_dir
    if not exe.is_file():
        sys.exit(f"knihovna nenalezena: {exe}")
    print("knihovna:", run(exe, models, "cs", ["version"]).strip())

    for lang in LANGS:
        suffix = "" if lang == "cs" else f".{lang}"
        listing = run(exe, models, lang, ["list", "--json"])
        save(f"list{suffix}.json", listing)
        save(f"providers{suffix}.json", run(exe, models, lang, ["list", "--providers", "--json"]))
        names = [f["name"] for f in json.loads(listing)]
        for provider in json.loads(run(exe, models, lang, ["list", "--providers", "--json"])):
            names.append(provider["name"])
        for name in names:
            payload = json.loads(run(exe, models, lang, ["list", "--params", name, "--json"]))
            if payload.get("params"):
                save(f"params/{name}{suffix}.json", json.dumps(payload))
    save("doctor.json", run(exe, models, "cs", ["doctor", "--json"], ok_codes=(0, 1)))
    save("models.json", run(exe, models, "cs", ["models", "list", "--json"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
