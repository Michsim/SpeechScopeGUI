"""Dimenze řeči (kvalita hlasu, artikulace, prozódie…) a jejich skóre proti normě.

Definice je v `assets/dimensions.yaml`: dimenze → položky (sloupec, úlohy,
směr patologie `higher` / `lower` / `both`, logaritmus). Směr `both` = odchylka
na kteroukoli stranu je horší (tempo řeči: zrychlené i zpomalené); z-skóre se
bere v absolutní hodnotě, percentil mezi kontrolami podle |z|, a strana
odchylky zůstává ve `Score.signed_z`. Vyhodnocení dostane hodnoty vyšetření po
úlohách (jedna nebo víc nahrávek na úlohu, bere se průměr), normu a věk a
pohlaví pacienta, a vrátí pro každou dimenzi skóre, položky s čísly a důvod,
proč dimenze nebo položka chybí. Pavouk kreslí jen dimenze se skóre.

Skóre dimenze: průměr z-skóre položek otočených k patologii se ještě
standardizuje rozdělením téhož průměru u vybraných kontrol. Průměr víc
z-skóre má totiž SD menší než 1 (u nezávislých položek 1/√k), takže by
hranice 1 a 2 SD byly příliš mírné a dimenze s různým počtem položek
nesrovnatelné. Kontrola dostane skóre jen z položek, které má pacient;
kontroly bez některé z nich se do rozdělení neberou (když by jich zbylo
méně než `MIN_REFERENCE`, vezmou se i neúplné). Percentil dimenze je
pořadí pacienta mezi kontrolami.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .. import contract, i18n
from ..i18n import tr
from . import meta, tasks
from .norms import (
    BOTH,
    DEFAULT_WINDOW,
    HIGHER,
    LOWER,
    Norm,
    Score,
    Selection,
    band,
    is_feature_column,
)

DIMENSIONS_FORMAT = 1


@dataclass(slots=True)
class Item:
    column: str
    tasks: tuple[str, ...]
    direction: int = HIGHER  # HIGHER: vyšší = horší, LOWER: nižší = horší, BOTH: obojí
    log: bool = False

    @property
    def feature(self) -> str:
        """Jméno feature bez sloupce (`acoustic.quality.cpp.cpps` → `acoustic.quality.cpp`)."""
        parts = self.column.split(".")
        return ".".join(parts[:3]) if len(parts) > 3 else self.column


@dataclass(slots=True)
class Dimension:
    key: str
    name: str
    items: list[Item]
    names: dict[str, str] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.names.get(i18n.language()) or self.name

    @property
    def tasks(self) -> list[str]:
        return sorted({t for item in self.items for t in item.tasks})


def _parse_item(data: dict[str, Any]) -> Item:
    direction = str(data.get("direction", "higher")).lower()
    directions = {"higher": HIGHER, "lower": LOWER, "both": BOTH}
    if direction not in directions:
        raise ValueError(f"položka {data.get('column')!r}: směr musí být higher, lower nebo both")
    tasks = data.get("tasks") or ([data["task"]] if data.get("task") else [])
    if not tasks:
        raise ValueError(f"položka {data.get('column')!r}: chybí úlohy")
    return Item(
        column=str(data["column"]),
        tasks=tuple(contract.canonical_task(str(t)) for t in tasks),
        direction=directions[direction],
        log=bool(data.get("log", False)),
    )


def parse_dimensions(data: dict[str, Any]) -> list[Dimension]:
    if int(data.get("format", 1)) > DIMENSIONS_FORMAT:
        raise ValueError("definice dimenzí je z novější verze aplikace")
    out: list[Dimension] = []
    for entry in data.get("dimensions") or []:
        out.append(
            Dimension(
                key=str(entry["key"]),
                name=str(entry["name"]),
                items=[_parse_item(i) for i in entry.get("items") or []],
                names={
                    k[len("name_") :]: str(v)
                    for k, v in entry.items()
                    if k.startswith("name_") and isinstance(v, str)
                },
            )
        )
    return out


def load_dimensions(path: Path | None = None) -> list[Dimension]:
    """Dimenze ze souboru, bez cesty ty přibalené."""
    if path is None:
        text = (resources.files("speechscope_app") / "assets" / "dimensions.yaml").read_text(
            encoding="utf-8"
        )
    else:
        text = path.read_text(encoding="utf-8")
    return parse_dimensions(yaml.safe_load(text) or {})


# --- vyšetření ------------------------------------------------------------------


def visit_values(rows_by_task: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
    """Hodnoty feature po úlohách; víc nahrávek téže úlohy = průměr po sloupcích."""
    out: dict[str, dict[str, float]] = {}
    for task, frame in rows_by_task.items():
        values: dict[str, float] = {}
        for column in frame.columns:
            if not is_feature_column(str(column)):
                continue
            nums = pd.to_numeric(frame[column], errors="coerce").dropna()
            if len(nums):
                values[str(column)] = float(nums.mean())
        out[contract.canonical_task(task)] = values
    return out


@dataclass(slots=True)
class ItemResult:
    item: Item
    task: str
    value: float | None
    score: Score | None
    reason: str = ""  # proč bez skóre; prázdné = v pořádku
    # (verze feature u pacienta, u kontrol normy), když se liší; jinak None
    versions: tuple[str, str] | None = None

    @property
    def band(self) -> str:
        return self.score.band if self.score else ""


MIN_REFERENCE = 5  # pod tolik úplných kontrol se rozdělení dimenze bere i z neúplných


@dataclass(slots=True)
class DimensionResult:
    dimension: Dimension
    score: float | None  # SD od kontrol (kladné = horší), viz docstring modulu; None = bez dat
    items: list[ItemResult]
    reason: str = ""  # proč dimenze chybí; prázdné = má skóre
    raw: float | None = None  # prostý průměr z-skóre položek před standardizací
    percentile: float | None = None  # podíl kontrol, které jsou na tom lépe nebo stejně
    control_sd: float | None = None  # SD prostého průměru u kontrol; None = nešlo spočítat
    n_controls: int = 0  # kolik kontrol rozdělení dimenze dalo

    @property
    def band(self) -> str:
        return band(self.score) if self.score is not None else ""

    @property
    def scored(self) -> list[ItemResult]:
        return [i for i in self.items if i.score is not None]

    @property
    def side(self) -> int:
        """Strana odchylky u dimenze ze samých položek `both`: 1 nad kontrolami,
        -1 pod nimi, 0 když se položky neshodnou nebo směr není `both`."""
        scored = self.scored
        if not scored or any(i.item.direction != BOTH for i in scored):
            return 0
        signs = {1 if i.score.signed_z >= 0 else -1 for i in scored}
        return signs.pop() if len(signs) == 1 else 0

    @property
    def shown(self) -> bool:
        return self.score is not None


@dataclass(slots=True)
class Evaluation:
    results: list[DimensionResult]
    selections: dict[str, Selection]  # úloha → vybrané kontroly

    @property
    def shown(self) -> list[DimensionResult]:
        return [r for r in self.results if r.shown]

    @property
    def missing(self) -> list[DimensionResult]:
        return [r for r in self.results if not r.shown]

    @property
    def version_changes(self) -> dict[tuple[str, str], tuple[str, str]]:
        """(úloha, feature) -> (verze u pacienta, v normě) u položek s jinou verzí."""
        return {
            (i.task, i.item.feature): i.versions
            for r in self.results
            for i in r.items
            if i.versions is not None
        }


def _task_label(task: str) -> str:
    return tasks.label(task)


def _item_tasks(item: Item, values: dict[str, dict[str, float | None]]) -> list[str]:
    """Úlohy vyšetření, ze kterých položka bere hodnotu: úlohy knihovny v pořadí položky,
    pak vlastní úlohy, které se počítají jako některá z nich."""
    present = [t for t in item.tasks if t in values]
    custom = sorted(
        (t for t in values if tasks.is_custom(t) and tasks.base_task(t) in item.tasks),
        key=tasks.task_order,
    )
    return present + custom


def _reference(items: list[ItemResult], selections: dict[str, Selection]) -> pd.Series:
    """Prostý průměr z-skóre týchž položek u každé kontroly (index = osoba)."""
    columns = {
        f"{i.task}:{i.item.column}": selections[i.task].control_z(
            i.item.column, direction=i.item.direction, log=i.item.log
        )
        for i in items
        if i.score is not None
    }
    table = pd.DataFrame(columns)
    if table.empty:
        return pd.Series(dtype=float)
    complete = table.dropna()
    if len(complete) >= MIN_REFERENCE:
        return complete.mean(axis=1)
    return table.dropna(how="all").mean(axis=1)


def _changed(
    item: Item, task: str, versions: dict[str, dict[str, str]] | None, norm: Norm
) -> tuple[str, str] | None:
    """(verze u pacienta, v normě), když se feature položky počítala jinou verzí."""
    if not versions:
        return None
    patient = versions.get(task, {}).get(item.feature)
    control = norm.info.feature_versions.get(task, {}).get(item.feature)
    if patient and control and patient != control:
        return patient, control
    return None


def _dimension_result(
    dim: Dimension, items: list[ItemResult], reference: pd.Series
) -> DimensionResult:
    zs = [i.score.z for i in items if i.score is not None]
    raw = sum(zs) / len(zs)
    result = DimensionResult(dim, raw, items, raw=raw)
    if len(reference) < 2:
        return result
    sd = float(reference.std(ddof=1))
    if not sd > 0:
        return result
    result.score = (raw - float(reference.mean())) / sd
    result.percentile = 100.0 * float((reference <= raw).mean())
    result.control_sd = sd
    result.n_controls = int(len(reference))
    return result


def evaluate(
    dimensions: list[Dimension],
    rows_by_task: dict[str, pd.DataFrame],
    norm: Norm,
    *,
    age: float | None = None,
    sex: str = "",
    window: int | None = DEFAULT_WINDOW,
    same_sex: bool = False,
    versions: dict[str, dict[str, str]] | None = None,
) -> Evaluation:
    """Skóre dimenzí jednoho vyšetření proti normě.

    `window` None = bez věkového omezení. Položka úlohy, kterou vyšetření
    nemá, se vynechá bez výčitek; položka, kterou nemá norma, dostane důvod.
    `versions` jsou verze feature běhů pacienta po úlohách (`Visit.versions_by_task`);
    položka s jinou verzí než u kontrol normy dostane `ItemResult.versions`,
    skóre se počítá dál.
    """
    values = visit_values(rows_by_task)
    sex = meta.parse_sex(sex) if sex else ""
    selections: dict[str, Selection] = {}
    needed = {t for d in dimensions for t in d.tasks}
    for task in sorted(values, key=tasks.task_order):
        if tasks.base_task(task) in needed and task in norm.tasks:
            selections[task] = norm.select(task, age=age, sex=sex, window=window, same_sex=same_sex)

    results: list[DimensionResult] = []
    for dim in dimensions:
        items: list[ItemResult] = []
        for item in dim.items:
            for task in _item_tasks(item, values):
                value = values[task].get(item.column)
                if task not in selections:
                    items.append(
                        ItemResult(
                            item,
                            task,
                            value,
                            None,
                            tr("norma nemá úlohu {task}").format(task=_task_label(task)),
                        )
                    )
                    continue
                if value is None or not math.isfinite(value):
                    items.append(ItemResult(item, task, None, None, tr("hodnota chybí")))
                    continue
                score = selections[task].score(
                    item.column, value, direction=item.direction, log=item.log
                )
                reason = "" if score else tr("norma nemá pro tento sloupec dost kontrol")
                items.append(
                    ItemResult(
                        item, task, value, score, reason, _changed(item, task, versions, norm)
                    )
                )
        if any(i.score is not None for i in items):
            results.append(_dimension_result(dim, items, _reference(items, selections)))
            continue
        if items:
            # vyšetření úlohu má, ale norma nebo tabulka ne: důvod z položky
            if all(i.reason == tr("hodnota chybí") for i in items):
                reason = tr("v tabulce chybí hodnoty")  # jiný protokol než dimenze čeká
            else:
                reason = items[0].reason or tr("bez dat")
        else:
            needed = [t for t in dim.tasks if t not in values]
            reason = (
                tr("potřebuje úlohu {tasks}").format(
                    tasks=tr(" nebo ").join(_task_label(t) for t in needed)
                )
                if needed
                else tr("bez dat")
            )
        results.append(DimensionResult(dim, None, items, reason))
    return Evaluation(results, selections)
