"""Diagnostický balíček: zip, který klinika pošle, když něco nefunguje.

Obsah: `info.json` (verze aplikace a knihovny, Python, systém, nastavení),
`doctor.json`, vlastní protokoly a z posledních běhů log, protokol,
config a run.json. Nikdy ne nahrávky, tabulky výsledků ani manifest
s metadaty pacientů; v logu ale zůstávají názvy souborů nahrávek.
"""

from __future__ import annotations

import json
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__, i18n
from .history import list_runs
from .library import Library, LibraryError
from .settings import AppSettings

RUN_FILES = ("speechscope.log", "protocol.yaml", "config.yaml", "run.json")
MAX_RUNS = 3


def default_name() -> str:
    return f"speechscope-diagnostika-{datetime.now():%Y-%m-%d_%H-%M}.zip"


def collect_info(settings: AppSettings, library: Library | None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "app_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "language": i18n.language(),
        "library_command": settings.effective_command(),
        "use_fake_library": settings.use_fake_library,
        "models_dir": str(settings.models_dir),
        "work_root": str(settings.work_root),
        "protocols_dir": str(settings.protocols_dir()),
        "advanced": settings.advanced,
        "last_protocol": settings.last_protocol,
        "last_language": settings.last_language,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    if library is not None:
        try:
            info["library_version"] = library.version()
        except LibraryError as exc:
            info["library_version_error"] = str(exc)
    return info


def build_bundle(
    target: Path,
    *,
    settings: AppSettings,
    library: Library | None,
    report: dict[str, Any] | None,
    max_runs: int = MAX_RUNS,
) -> list[str]:
    """Zapíše zip do `target`; vrací seznam členů, pro test i pro hlášku."""
    if report is None and library is not None:
        try:
            report = library.doctor()
        except LibraryError as exc:
            report = {"error": str(exc)}
    members: list[str] = []
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:

        def add_text(name: str, text: str) -> None:
            zf.writestr(name, text)
            members.append(name)

        def add_file(name: str, path: Path) -> None:
            if path.is_file():
                zf.write(path, name)
                members.append(name)

        add_text(
            "info.json", json.dumps(collect_info(settings, library), ensure_ascii=False, indent=2)
        )
        add_text("doctor.json", json.dumps(report or {}, ensure_ascii=False, indent=2))
        protocols_dir = settings.protocols_dir()
        if protocols_dir.is_dir():
            for path in sorted(protocols_dir.glob("*.yaml")):
                add_file(f"protocols/{path.name}", path)
        for run in list_runs(settings.work_root)[:max_runs]:
            for name in RUN_FILES:
                add_file(f"runs/{run.dir.name}/{name}", run.dir / name)
    return members
