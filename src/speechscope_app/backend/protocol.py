"""Protokol: pojmenované nastavení dávky, které klinika používá pořád stejně.

Ukládá se jako YAML. Část `config` je přesně to, co knihovna bere přes
`--config`, takže výsledek jde zopakovat i z příkazové řádky bez GUI.
"""

from __future__ import annotations

import fnmatch
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .. import contract, i18n
from ..i18n import tr
from .command import ExtractRequest

if TYPE_CHECKING:
    from .library import FeatureInfo, FeatureParams

FORMAT_VERSION = 1


def slugify(text: str) -> str:
    """Jméno protokolu jako část názvu složky nebo klíč: bez diakritiky a mezer."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "davka"


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
    # překlady jména a popisu: kód jazyka -> text (v YAML `name_en`, `description_en`)
    names: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.task not in contract.TASKS:
            raise ValueError(f"neznámá úloha {self.task!r}")

    @property
    def display_name(self) -> str:
        """Jméno v jazyce aplikace; `name` zůstává klíčem (soubor, statistika)."""
        return self.names.get(i18n.language()) or self.name

    @property
    def display_description(self) -> str:
        return self.descriptions.get(i18n.language()) or self.description

    # --- serializace ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "format": FORMAT_VERSION,
            "name": self.name,
            "task": self.task,
        }
        if self.description:
            data["description"] = self.description
        for lang, text in sorted(self.names.items()):
            if text:
                data[f"name_{lang}"] = text
        for lang, text in sorted(self.descriptions.items()):
            if text:
                data[f"description_{lang}"] = text
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
            names={
                k[len("name_") :]: str(v)
                for k, v in data.items()
                if k.startswith("name_") and isinstance(v, str)
            },
            descriptions={
                k[len("description_") :]: str(v)
                for k, v in data.items()
                if k.startswith("description_") and isinstance(v, str)
            },
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
        manifest: Path | None = None,
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
            manifest=manifest,
        )

    def segments_model(self) -> str:
        return str(self.config.get("segments", {}).get("model", "auto"))

    def slug(self) -> str:
        return slugify(self.name)

    def select(self, available: list[str]) -> list[str]:
        """Jména feature z katalogu, která protokol vybírá (doména a vzory)."""
        pool = [n for n in available if not self.domain or n.startswith(self.domain + ".")]
        if not self.features:
            return list(pool)
        out: list[str] = []
        for pat in self.features:
            out.extend(n for n in pool if fnmatch.fnmatchcase(n, pat) and n not in out)
        return out


@dataclass(slots=True)
class Summary:
    """Co výběr feature stojí: providery, počet sloupců, orientační cena."""

    features: list[str]
    providers: list[str]
    columns: int

    def cost_hint(self) -> str:
        for provider, hint in contract.PROVIDER_COST:
            if provider in self.providers:
                return tr(hint)
        return tr(contract.NO_MODELS_COST)


def card_infos(
    protocols: list[Protocol],
    catalog: list[FeatureInfo],
    providers: list[FeatureParams] | None,
    seconds_per_file: Callable[[str], float | None],
) -> dict[str, tuple[list[str], str]]:
    """Pro každý protokol providery a nápis o ceně (naposledy změřená, jinak odhad).

    `catalog` je celý `list --json` bez úlohy; feature se filtrují podle
    `tasks`, takže na všechny protokoly stačí jeden dotaz na knihovnu.
    """
    out: dict[str, tuple[list[str], str]] = {}
    for proto in protocols:
        pool = [f for f in catalog if proto.task in f.tasks]
        summary = summarize(proto.select([f.name for f in pool]), pool, providers)
        seconds = seconds_per_file(proto.slug())
        hint = (
            tr("naposledy {n:.0f} s na nahrávku").format(n=seconds)
            if seconds
            else summary.cost_hint()
        )
        out[proto.name] = (summary.providers, hint)
    return out


def summarize(
    names: list[str],
    catalog: list[FeatureInfo],
    providers: list[FeatureParams] | None = None,
) -> Summary:
    """Souhrn výběru z katalogu `list --json`.

    `providers` (z `list --providers`) doplní závislosti providerů
    mezi sebou (jazykový rozbor potřebuje přepis).
    """
    chosen = set(names)
    needed: set[str] = set()
    columns = 0
    for f in catalog:
        if f.name in chosen:
            needed.update(f.requires)
            columns += len(f.columns) or len(f.outputs) or 1
    deps = {p.name: list(p.requires) for p in providers or []}
    queue = list(needed)
    while queue:
        for dep in deps.get(queue.pop(), []):
            if dep not in needed:
                needed.add(dep)
                queue.append(dep)
    return Summary(
        features=[f.name for f in catalog if f.name in chosen],
        providers=sorted(needed, key=provider_order),
        columns=columns,
    )


def provider_order(name: str) -> tuple[int, str]:
    """Pořadí, v jakém providery v běhu přijdou na řadu (podle `PROVIDER_LABELS`)."""
    known = list(contract.PROVIDER_LABELS)
    return (known.index(name) if name in known else len(known), name)


@dataclass(slots=True)
class Description:
    """Souhrn protokolu pro čtení: providery, text o feature a o parametrech."""

    providers: list[str]
    features_text: str
    params_text: str


def describe(
    proto: Protocol,
    catalog: list[FeatureInfo],
    providers: list[FeatureParams] | None = None,
) -> Description:
    pool = [f for f in catalog if proto.task in f.tasks]
    chosen = proto.select([f.name for f in pool])
    summary = summarize(chosen, pool, providers)
    if pool:
        per_domain: dict[str, int] = {}
        for name in chosen:
            domain = name.split(".", 1)[0]
            per_domain[domain] = per_domain.get(domain, 0) + 1
        parts = ", ".join(
            f"{contract.DOMAIN_LABELS.get(d, d)} {n}" for d, n in sorted(per_domain.items())
        )
        features_text = tr("{n} z {total} ({parts}), {columns} sloupců").format(
            n=len(chosen), total=len(pool), parts=parts, columns=summary.columns
        )
    elif proto.features:
        features_text = ", ".join(proto.features)
    else:
        features_text = (
            tr("všechny pro doménu {domain}").format(domain=proto.domain)
            if proto.domain
            else tr("všechny")
        )
    params_text = ", ".join(proto.set_items())
    return Description(summary.providers, features_text, params_text)


def unique_name(name: str, taken: set[str]) -> str:
    """`Jméno`, `Jméno (2)`, `Jméno (3)`… první volné."""
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


def import_protocol(
    proto: Protocol, folder: Path, existing: list[Protocol], *, overwrite: bool = False
) -> Path:
    """Uloží cizí protokol mezi vlastní. Přepis stejného jména jen s `overwrite`."""
    match = next((p for p in existing if p.name == proto.name and not p.builtin), None)
    if match is not None and match.path is not None and overwrite:
        path = match.path
    else:
        path = folder / f"{proto.slug()}.yaml"
        n = 2
        while path.exists():
            path = folder / f"{proto.slug()}-{n}.yaml"
            n += 1
    copy = Protocol.from_dict(proto.to_dict())
    copy.save(path)
    return path


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
