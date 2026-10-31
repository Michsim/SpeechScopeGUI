"""Kontrola kvality nahrávek: prahy nad sloupci tabulky feature.

Knihovna nahrávku jen měří (`acoustic.recording.duration|snr|clipping`,
`acoustic.ddk.count`, `acoustic.timing.speech_rate.word_count`); jestli je
výsledek podezřelý, rozhoduje tenhle modul podle `assets/quality.yaml`
(úpravy uživatele v datech aplikace, `settings.quality_file()`, jsou jen odchylky
od přibalených: práh a vypnutí podle klíče kontroly). Dvě úrovně:
`warn` = upozornění (žlutá), `bad` = nahrávka nejspíš nepoužitelná (červená).
Nikdy nic nevyřazuje, jen označí; rozhodnutí je na uživateli.

K prahům přibude poznámka knihovny: feature, která skončila
`InsufficientData` (sloupec `notes`), je taky upozornění, protože hodnota
v tabulce chybí.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ..i18n import language, tr
from . import tasks as tasks_mod

logger = logging.getLogger(__name__)

WARN = "warn"
BAD = "bad"
LEVELS = ("", WARN, BAD)  # vzestupně podle závažnosti
NOTES_KEY = "notes"
COLUMN_TOTAL = "acoustic.recording.duration.total"

# Sloupce, které zapisuje knihovna jen kvůli kontrole kvality. GUI je přidá
# ke každému běhu (`protocol.quality_features`) a ve výsledcích patří do
# skupiny „Akustika · nahrávka“.
QUALITY_FEATURES: tuple[str, ...] = (
    "acoustic.recording.duration",
    "acoustic.recording.snr",
    "acoustic.recording.clipping",
)
DDK_QUALITY_FEATURE = "acoustic.ddk.count"


@dataclass(frozen=True, slots=True)
class Check:
    key: str
    column: str
    tasks: frozenset[str]
    kind: str  # min | max
    warn: float
    bad: float
    name: str
    name_en: str = ""
    unit: str = ""
    enabled: bool = True

    @property
    def display_name(self) -> str:
        return self.name_en if language() == "en" and self.name_en else self.name

    def level(self, value: float) -> str:
        """Úroveň pro hodnotu: prázdné = v pořádku."""
        if self.kind == "min":
            if value < self.bad:
                return BAD
            return WARN if value < self.warn else ""
        if value > self.bad:
            return BAD
        return WARN if value > self.warn else ""


@dataclass(frozen=True, slots=True)
class Issue:
    """Jedna neprošlá kontrola jedné nahrávky."""

    check: Check
    level: str
    value: float | None = None
    detail: str = ""  # u poznámek knihovny: které feature chybí

    def text(self) -> str:
        """Např. „znělá část 3,1 s < 5 s“, nebo „bez hodnoty: 2 feature“."""
        if self.check.key == NOTES_KEY:
            return tr("bez hodnoty: {features}").format(features=self.detail)
        unit = f" {self.check.unit}" if self.check.unit else ""
        limit = self.check.warn if self.level == WARN else self.check.bad
        sign = "<" if self.check.kind == "min" else ">"
        return (
            f"{self.check.display_name.lower()} {_num(self.value)}{unit} {sign} {_num(limit)}{unit}"
        )


NOTES_CHECK = Check(
    key=NOTES_KEY,
    column="notes",
    tasks=frozenset(),
    kind="min",
    warn=0,
    bad=0,
    name="Feature bez hodnoty",
    name_en="Feature without a value",
)


def _num(value: float | None) -> str:
    if value is None:
        return "?"
    if abs(value) >= 100 or float(value).is_integer():
        return f"{value:.0f}"
    return f"{value:.3g}"


def parse_checks(data: Mapping[str, Any]) -> list[Check]:
    checks: list[Check] = []
    for raw in data.get("checks") or []:
        kind = str(raw.get("kind", "min"))
        if kind not in ("min", "max"):
            raise ValueError(f"kontrola {raw.get('key')}: kind musí být min nebo max")
        checks.append(
            Check(
                key=str(raw.get("key") or raw["column"]),
                column=str(raw["column"]),
                tasks=frozenset(str(t) for t in raw.get("tasks") or []),
                kind=kind,
                warn=float(raw["warn"]),
                bad=float(raw["bad"]),
                name=str(raw.get("name") or raw.get("key") or raw["column"]),
                name_en=str(raw.get("name_en") or ""),
                unit=str(raw.get("unit") or ""),
            )
        )
    return checks


def apply_overrides(checks: list[Check], overrides: Mapping[str, Any]) -> list[Check]:
    """Přibalené kontroly s odchylkami uživatele (`klíč: {warn, bad, enabled}`)."""
    out: list[Check] = []
    for check in checks:
        raw = overrides.get(check.key)
        if not isinstance(raw, Mapping):
            out.append(check)
            continue
        out.append(
            replace(
                check,
                warn=float(raw.get("warn", check.warn)),
                bad=float(raw.get("bad", check.bad)),
                enabled=bool(raw.get("enabled", check.enabled)),
            )
        )
    return out


_cache: dict[str, list[Check]] = {}
_user_file: Path | None = None


def use_user_file(path: Path | None) -> None:
    """Soubor s úpravami prahů (`settings.quality_file()`), platí pro celou aplikaci."""
    global _user_file
    _user_file = path
    _cache.clear()


def user_file() -> Path | None:
    return _user_file


def default_checks() -> list[Check]:
    """Přibalené prahy z `assets/quality.yaml`."""
    cached = _cache.get("")
    if cached is None:
        text = (resources.files("speechscope_app") / "assets" / "quality.yaml").read_text(
            encoding="utf-8"
        )
        cached = parse_checks(yaml.safe_load(text) or {})
        _cache[""] = cached
    return cached


def load_checks(path: Path | None = None) -> list[Check]:
    """Platné kontroly: přibalené s úpravami ze souboru `path` (jinak uživatele).

    Soubor má `overrides` (odchylky podle klíče); starší ruční kopie s celým
    seznamem `checks` ho nahradí celý. Chybějící nebo vadný soubor = přibalené.
    """
    if path is None:
        path = _user_file
    if path is None or not path.is_file():
        return default_checks()
    key = str(path)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if data.get("checks"):
            checks = parse_checks(data)
        else:
            checks = apply_overrides(default_checks(), data.get("overrides") or {})
    except (ValueError, KeyError, TypeError, AttributeError, OSError, yaml.YAMLError) as exc:
        logger.warning("%s: %s", path, exc)
        checks = default_checks()
    _cache[key] = checks
    return checks


def save_checks(checks: list[Check], path: Path | None = None) -> Path | None:
    """Uloží odchylky od přibalených prahů; žádná odchylka = soubor se smaže.

    Vrátí cestu k souboru, nebo None, když platí přibalené.
    """
    path = path or _user_file
    if path is None:
        raise ValueError("chybí soubor s prahy kvality")
    defaults = {c.key: c for c in default_checks()}
    overrides: dict[str, dict[str, Any]] = {}
    for check in checks:
        base = defaults.get(check.key)
        if base is None:
            continue
        diff: dict[str, Any] = {}
        if check.warn != base.warn:
            diff["warn"] = check.warn
        if check.bad != base.bad:
            diff["bad"] = check.bad
        if check.enabled != base.enabled:
            diff["enabled"] = check.enabled
        if diff:
            overrides[check.key] = diff
    _cache.pop(str(path), None)
    if not overrides:
        path.unlink(missing_ok=True)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Úpravy prahů kontroly kvality proti přibaleným (assets/quality.yaml).\n"
        + yaml.safe_dump({"format": 1, "overrides": overrides}, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def validate(check: Check) -> str:
    """Prázdné = v pořádku, jinak proč práh nedává smysl."""
    if check.kind == "min" and check.bad > check.warn:
        return tr("{name}: práh „nepoužitelná“ musí být nejvýš práh „podezřelá“.").format(
            name=check.display_name
        )
    if check.kind == "max" and check.bad < check.warn:
        return tr("{name}: práh „nepoužitelná“ musí být aspoň práh „podezřelá“.").format(
            name=check.display_name
        )
    return ""


def reset_cache() -> None:
    _cache.clear()


def _library_task(task: str) -> str:
    """Vlastní úloha se kontroluje jako její základní úloha knihovny."""
    custom = tasks_mod.registry().get(task)
    return custom.base if custom is not None else task


def assess(row: Mapping[str, Any], task: str, checks: list[Check] | None = None) -> list[Issue]:
    """Neprošlé kontroly řádku tabulky, horší napřed; prázdné = v pořádku.

    Chybí-li sloupec nebo hodnota (NaN), kontrola se přeskočí: starý běh bez
    feature kvality nic nehlásí. Poznámka knihovny (`notes`) je upozornění.
    """
    base = _library_task(task)
    issues: list[Issue] = []
    for check in load_checks() if checks is None else checks:
        if base not in check.tasks or not check.enabled:
            continue
        value = _float(row.get(check.column))
        if value is None:
            continue
        level = check.level(value)
        if level:
            issues.append(Issue(check, level, value))
    notes = row.get("notes")
    if notes is not None and str(notes).strip() and str(notes).strip().lower() != "nan":
        names = [part.split("=", 1)[0].strip() for part in str(notes).split(";") if part.strip()]
        issues.append(Issue(NOTES_CHECK, WARN, detail=", ".join(names)))
    issues.sort(key=lambda i: (-LEVELS.index(i.level), i.check.key))
    return issues


def level_of(issues: list[Issue]) -> str:
    """Nejhorší úroveň z kontrol (prázdné = v pořádku)."""
    return max((i.level for i in issues), key=LEVELS.index, default="")


def summary(issues: list[Issue]) -> str:
    return "; ".join(i.text() for i in issues)


def min_duration(task: str, checks: list[Check] | None = None) -> float | None:
    """Práh délky nahrávky pro úlohu (sekundy), ke kontrole už z hlavičky souboru."""
    base = _library_task(task)
    for check in load_checks() if checks is None else checks:
        if (
            check.column == COLUMN_TOTAL
            and check.kind == "min"
            and base in check.tasks
            and check.enabled
        ):
            return check.warn
    return None


def quality_features(task: str, catalog_names: set[str] | None = None) -> list[str]:
    """Feature kvality, které GUI přidá ke každému běhu dané úlohy.

    `catalog_names` = feature, které knihovna zná (`list --json`); se starší
    knihovnou bez nich se nic nepřidá, aby `extract` neskončil chybou.
    """
    wanted = list(QUALITY_FEATURES)
    if _library_task(task) == "ddk":
        wanted.append(DDK_QUALITY_FEATURE)
    if catalog_names is not None:
        wanted = [name for name in wanted if name in catalog_names]
    return wanted


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number
