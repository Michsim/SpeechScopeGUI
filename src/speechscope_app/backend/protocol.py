"""Protokol: pojmenované nastavení dávky, které klinika používá pořád stejně.

Ukládá se jako YAML. Část `config` je přesně to, co knihovna bere přes
`--config`, takže výsledek jde zopakovat i z příkazové řádky bez GUI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .. import contract
from .command import ExtractRequest

FORMAT_VERSION = 1


@dataclass(slots=True)
class Protocol:
    name: str
    task: str
    description: str = ""
    features: list[str] = field(default_factory=list)  # prázdné = všechno pro úlohu
    domain: str | None = None
    vad: bool | None = None
    # feature/provider -> {parametr: hodnota}; jen hodnoty odlišné od výchozích
    config: dict[str, dict[str, Any]] = field(default_factory=dict)
    builtin: bool = False
    path: Path | None = None

    def __post_init__(self) -> None:
        if self.task not in contract.TASKS:
            raise ValueError(f"neznámá úloha {self.task!r}")

    # --- serializace ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "format": FORMAT_VERSION,
            "name": self.name,
            "task": self.task,
        }
        if self.description:
            data["description"] = self.description
        if self.features:
            data["features"] = list(self.features)
        if self.domain:
            data["domain"] = self.domain
        if self.vad is not None:
            data["vad"] = self.vad
        if self.config:
            data["config"] = {k: dict(v) for k, v in self.config.items() if v}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, path: Path | None = None) -> Protocol:
        if int(data.get("format", 1)) > FORMAT_VERSION:
            raise ValueError("protokol je z novější verze aplikace")
        return cls(
            name=str(data["name"]),
            task=str(data["task"]),
            description=str(data.get("description", "")),
            features=[str(f) for f in data.get("features", [])],
            domain=data.get("domain"),
            vad=data.get("vad"),
            config={str(k): dict(v) for k, v in (data.get("config") or {}).items()},
            path=path,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        self.path = path

    @classmethod
    def load(cls, path: Path) -> Protocol:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.from_dict(data, path=path)

    # --- převod na běh --------------------------------------------------------

    def config_yaml(self) -> str:
        """Obsah souboru pro `--config`."""
        return yaml.safe_dump(self.config, allow_unicode=True, sort_keys=True)

    def set_items(self) -> list[str]:
        """Totéž jako `--set NAME.PARAM=VALUE`, pro log a pro ladění."""
        return [
            f"{name}.{k}={v}" for name, params in self.config.items() for k, v in params.items()
        ]

    def to_request(
        self,
        inputs: list[Path],
        *,
        out: Path,
        work_dir: Path,
        config_path: Path | None,
        log_file: Path | None = None,
    ) -> ExtractRequest:
        return ExtractRequest(
            inputs=inputs,
            task=self.task,
            features=list(self.features),
            domain=self.domain,
            config=config_path,
            vad=self.vad,
            out=out,
            work_dir=work_dir,
            log_file=log_file,
        )

    def segments_model(self) -> str:
        return str(self.config.get("segments", {}).get("model", "auto"))


def builtin_protocols() -> list[Protocol]:
    """Protokoly přibalené k aplikaci."""
    out: list[Protocol] = []
    root = resources.files("speechscope_app") / "protocols"
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith((".yaml", ".yml")):
            data = yaml.safe_load(entry.read_text(encoding="utf-8")) or {}
            proto = Protocol.from_dict(data)
            proto.builtin = True
            out.append(proto)
    return out


def user_protocols(folder: Path) -> list[Protocol]:
    if not folder.is_dir():
        return []
    out: list[Protocol] = []
    for path in sorted(folder.glob("*.yaml")):
        try:
            out.append(Protocol.load(path))
        except (ValueError, KeyError, yaml.YAMLError):
            continue  # rozbitý soubor nesmí shodit start aplikace
    return out


def all_protocols(folder: Path) -> list[Protocol]:
    return builtin_protocols() + user_protocols(folder)
