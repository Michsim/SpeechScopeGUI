"""Synchronní dotazy na knihovnu: verze, katalog feature, doctor, modely.

Každý dotaz je krátký podproces. Dlouhé běhy (`extract`, `segment`,
`transcribe`) tudy nejdou, ty řídí `runner.Runner`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import contract
from . import command


class LibraryError(RuntimeError):
    """Knihovna skončila chybou nebo vrátila něco, čemu GUI nerozumí."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


def subprocess_env() -> dict[str, str]:
    """Prostředí pro každý podproces knihovny.

    `PYTHONUTF8=1` je nutné: bez něj knihovna při přesměrovaném stdout
    píše v cp1252 a na první české hlášce spadne na `UnicodeEncodeError`.
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def creation_flags() -> int:
    """Na Windows bez konzolového okna, které by u zabaleného GUI blikalo."""
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def find_default_command() -> list[str] | None:
    """Kde hledat knihovnu, když uživatel nic nenastavil.

    1. Prostředí přibalené k zabalené aplikaci (`speechscope-lib/` vedle exe).
    2. `speechscope` na PATH.
    """
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).parent / "speechscope-lib" / "Scripts" / "speechscope.exe"
        if bundled.is_file():
            return [str(bundled)]
    exe = shutil.which("speechscope")
    if exe:
        return [exe]
    return None


def fake_command() -> list[str]:
    """Falešná knihovna pro vývoj bez modelů."""
    return [sys.executable, "-m", "speechscope_app.fake.cli"]


@dataclass(slots=True)
class FeatureInfo:
    name: str
    version: str
    tasks: list[str]
    requires: list[str]
    description: str | None
    outputs: dict[str, str]
    columns: list[str]

    @property
    def domain(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def group(self) -> str:
        return ".".join(self.name.split(".")[:2])

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> FeatureInfo:
        return cls(
            name=item["name"],
            version=str(item.get("version", "")),
            tasks=list(item.get("tasks", [])),
            requires=list(item.get("requires", [])),
            description=item.get("description"),
            outputs=dict(item.get("outputs", {})),
            columns=list(item.get("columns", [])),
        )


@dataclass(slots=True)
class ParamInfo:
    name: str
    default: Any
    type: str
    description: str
    choices: list[Any] | None = None
    bounds: dict[str, float] = field(default_factory=dict)  # gt, ge, lt, le

    @classmethod
    def from_json(cls, name: str, item: dict[str, Any]) -> ParamInfo:
        bounds = {k: item[k] for k in ("gt", "ge", "lt", "le") if k in item}
        return cls(
            name=name,
            default=item.get("default"),
            type=str(item.get("type", "str")),
            description=str(item.get("description", "")),
            choices=list(item["choices"]) if "choices" in item else None,
            bounds=bounds,
        )


@dataclass(slots=True)
class FeatureParams:
    name: str
    vad_supported: bool
    vad_required: bool
    vad_default: dict[str, bool]
    params: list[ParamInfo]

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> FeatureParams:
        vad = item.get("vad", {})
        return cls(
            name=item["name"],
            vad_supported=bool(vad.get("supported", False)),
            vad_required=bool(vad.get("required", False)),
            vad_default=dict(vad.get("default", {})),
            params=[ParamInfo.from_json(k, v) for k, v in item.get("params", {}).items()],
        )


class Library:
    """Jedna nakonfigurovaná cesta ke knihovně."""

    def __init__(self, command: list[str], *, models_dir: Path | None = None) -> None:
        if not command:
            raise ValueError("prázdný příkaz knihovny")
        self.command = list(command)
        self.models_dir = models_dir
        self._features: dict[str | None, list[FeatureInfo]] = {}
        self._params: dict[str, FeatureParams] = {}

    def argv(self, args: list[str]) -> list[str]:
        return [*self.command, *args]

    # --- nízká úroveň ---------------------------------------------------------

    def run(self, args: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                self.argv(args),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=subprocess_env(),
                creationflags=creation_flags(),
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise LibraryError(f"knihovnu nejde spustit: {self.command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise LibraryError(f"knihovna neodpověděla do {timeout:.0f} s") from exc

    def run_json(self, args: list[str], *, ok_codes: tuple[int, ...] = (0,)) -> Any:
        proc = self.run(args)
        if proc.returncode not in ok_codes:
            msg = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "bez hlášky"
            raise LibraryError(
                f"`{' '.join(args)}` skončil kódem {proc.returncode}: {msg}",
                returncode=proc.returncode,
                stderr=proc.stderr,
            )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LibraryError(
                f"`{' '.join(args)}` nevrátil JSON", returncode=proc.returncode, stderr=proc.stderr
            ) from exc

    # --- dotazy ---------------------------------------------------------------

    def version(self) -> str:
        proc = self.run(command.version_args())
        if proc.returncode != 0:
            raise LibraryError("`version` selhal", returncode=proc.returncode, stderr=proc.stderr)
        return proc.stdout.strip()

    def version_matches(self) -> bool:
        return self.version() == contract.KNOWN_LIBRARY_VERSION

    def doctor(self) -> dict[str, Any]:
        """`doctor --json`; kód 1 znamená jen "něco chybí", ne chybu."""
        return self.run_json(command.doctor_args(models_dir=self.models_dir), ok_codes=(0, 1))

    def models(self) -> dict[str, Any]:
        return self.run_json(command.models_list_args(models_dir=self.models_dir))

    def features(self, task: str | None = None) -> list[FeatureInfo]:
        if task not in self._features:
            payload = self.run_json(command.list_args(task=task, models_dir=self.models_dir))
            self._features[task] = [FeatureInfo.from_json(item) for item in payload]
        return list(self._features[task])

    def params(self, name: str) -> FeatureParams:
        if name not in self._params:
            payload = self.run_json(command.params_args(name, models_dir=self.models_dir))
            self._params[name] = FeatureParams.from_json(payload)
        return self._params[name]

    def provider_params(self, provider: str) -> list[ParamInfo]:
        """Parametry providerů, dokud je knihovna nevydává sama."""
        declared = contract.PROVIDER_PARAMS.get(provider, {})
        return [ParamInfo.from_json(k, v) for k, v in declared.items()]

    def clear_cache(self) -> None:
        self._features.clear()
        self._params.clear()
