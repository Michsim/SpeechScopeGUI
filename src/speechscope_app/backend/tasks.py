"""Registr úloh: čtyři úlohy knihovny a vlastní úlohy uživatele.

Knihovna umí jen úlohy z `contract.TASKS` (fonace, DDK, spontánní řeč,
čtení). Vlastní úloha (`CustomTask`, třeba „Popis obrázku“) je jedna z nich
pod jiným jménem: má klíč, název, `base` = úlohu knihovny, jako která se
počítá, výchozí protokol a nepovinný pokyn pro pacienta. Knihovna dostává
vždy jen `base` (protokol je protokol základní úlohy); vlastní klíč žije
v GUI: v plánu normy a hodnocení, v `run.json` dávky (`task`), ve sloupci
`task` tabulky kontrol normy a v odkazech hodnocení. Dimenze se vážou na
úlohu knihovny, vlastní úloha se do nich započítá přes `base`.

Vlastní úlohy se zakládají v průvodci Nová norma a ukládají do
`tasks.json` v datech aplikace (`registry().load(path)`); norma si je zapíše
do `norm.yaml` (`NormInfo.custom_tasks`), takže po importu normy na jiném
počítači se zaregistrují samy (`register`, jen v paměti). Na Analýze se
vlastní úlohy nenabízejí, tam zůstávají jen úlohy knihovny.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import contract

TASKS_FILE = "tasks.json"
FORMAT_VERSION = 1


@dataclass(slots=True)
class CustomTask:
    key: str  # slug názvu, nikdy jméno úlohy knihovny ani její alias
    name: str
    base: str  # úloha knihovny, jako která se počítá
    protocol: str = ""  # výchozí protokol (klíč `Protocol.name`), jde změnit
    instruction: str = ""  # pokyn pro pacienta, nepovinné

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"key": self.key, "name": self.name, "base": self.base}
        if self.protocol:
            data["protocol"] = self.protocol
        if self.instruction:
            data["instruction"] = self.instruction
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CustomTask:
        base = contract.canonical_task(str(data.get("base", "")))
        if base not in contract.TASKS:
            raise ValueError(f"vlastní úloha {data.get('key')!r}: neznámá základní úloha {base!r}")
        key = str(data.get("key", "")).strip()
        if not key or key in contract.TASKS or key in contract.TASK_ALIASES:
            raise ValueError(f"vlastní úloha: neplatný klíč {key!r}")
        return cls(
            key=key,
            name=str(data.get("name", "")).strip() or key,
            base=base,
            protocol=str(data.get("protocol", "")),
            instruction=str(data.get("instruction", "")),
        )


def slug(text: str) -> str:
    """Klíč z názvu: bez diakritiky, malá písmena, pomlčky („Popis obrázku“ → popis-obrazku)."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    out = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
    return out or "uloha"


class TaskRegistry:
    """Úlohy knihovny plus vlastní; jedna instance pro celou aplikaci (`registry()`)."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self._custom: dict[str, CustomTask] = {}
        self.version = 0  # roste s každou změnou; index nahrávek podle něj zahodí cache

    # --- soubor -------------------------------------------------------------

    def load(self, path: Path) -> None:
        """Vlastní úlohy ze souboru; další `register(..., save=True)` do něj zapisují."""
        self.path = path
        self._custom = {}
        self.version += 1
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return  # rozbitý soubor nesmí shodit start
        for item in data.get("tasks", []):
            try:
                task = CustomTask.from_dict(item)
            except (ValueError, TypeError, AttributeError):
                continue
            self._custom[task.key] = task

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"format": FORMAT_VERSION, "tasks": [t.to_dict() for t in self.custom]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self) -> None:
        """Pro testy: zapomenout vlastní úlohy i soubor."""
        self.path = None
        self._custom = {}
        self.version += 1

    # --- dotazy -------------------------------------------------------------

    @property
    def custom(self) -> list[CustomTask]:
        return sorted(self._custom.values(), key=lambda t: (t.name.lower(), t.key))

    def get(self, key: str) -> CustomTask | None:
        return self._custom.get(key)

    def is_custom(self, key: str) -> bool:
        return key in self._custom

    def known(self, key: str) -> bool:
        return key in contract.TASKS or key in self._custom

    def keys(self) -> list[str]:
        """Úlohy knihovny v jejich pořadí, pak vlastní podle názvu."""
        return [*contract.TASKS, *(t.key for t in self.custom)]

    def base(self, key: str) -> str:
        """Úloha knihovny, kterou knihovna skutečně počítá."""
        task = self._custom.get(key)
        return task.base if task is not None else contract.canonical_task(key)

    def label(self, key: str) -> str:
        task = self._custom.get(key)
        return task.name if task is not None else contract.TASK_SHORT.get(key, key)

    def long_label(self, key: str) -> str:
        task = self._custom.get(key)
        return task.name if task is not None else contract.TASK_LABELS.get(key, key)

    def order(self, key: str) -> int:
        """Řazení: úlohy knihovny napřed, vlastní podle názvu, neznámé nakonec."""
        if key in contract.TASKS:
            return contract.TASKS.index(key)
        for i, task in enumerate(self.custom):
            if task.key == key:
                return len(contract.TASKS) + i
        return 99

    # --- změny --------------------------------------------------------------

    def new_key(self, name: str) -> str:
        """Volný klíč z názvu; při kolizi s úlohou knihovny nebo jinou vlastní `-2`, `-3`…"""
        base = slug(name)
        taken = set(contract.TASKS) | set(contract.TASK_ALIASES) | set(self._custom)
        key, n = base, 2
        while key in taken:
            key, n = f"{base}-{n}", n + 1
        return key

    def register(self, task: CustomTask, *, save: bool = False) -> CustomTask:
        """Zapíše úlohu; známý klíč nechá, jak je (norma nepřepisuje nastavení)."""
        existing = self._custom.get(task.key)
        if existing is not None:
            return existing
        self._custom[task.key] = task
        self.version += 1
        if save:
            self.save()
        return task

    def remove(self, key: str, *, save: bool = False) -> None:
        if self._custom.pop(key, None) is not None:
            self.version += 1
        if save:
            self.save()


_registry = TaskRegistry()


def registry() -> TaskRegistry:
    return _registry


def label(key: str) -> str:
    return _registry.label(key)


def long_label(key: str) -> str:
    return _registry.long_label(key)


def base_task(key: str) -> str:
    return _registry.base(key)


def is_custom(key: str) -> bool:
    return _registry.is_custom(key)


def task_order(key: str) -> int:
    return _registry.order(key)
