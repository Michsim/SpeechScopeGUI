"""Dimenze řeči (kvalita hlasu, artikulace, prozódie…) a jejich skóre proti normě.

Definice je v `assets/dimensions.yaml`: dimenze → položky (sloupec, úlohy,
směr patologie, logaritmus). Vyhodnocení dostane hodnoty vyšetření po
úlohách (jedna nebo víc nahrávek na úlohu, bere se průměr), normu a věk a
pohlaví pacienta, a vrátí pro každou dimenzi skóre (průměr z-skóre položek
otočených k patologii), položky s čísly a důvod, proč dimenze nebo položka
chybí. Pavouk kreslí jen dimenze se skóre.
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
from . import meta
from .norms import (
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
    direction: int = HIGHER  # HIGHER: vyšší = horší, LOWER: nižší = horší
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
    if direction not in ("higher", "lower"):
        raise ValueError(f"položka {data.get('column')!r}: směr musí být higher nebo lower")
    tasks = data.get("tasks") or ([data["task"]] if data.get("task") else [])
    if not tasks:
        raise ValueError(f"položka {data.get('column')!r}: chybí úlohy")
    return Item(
        column=str(data["column"]),
        tasks=tuple(contract.canonical_task(str(t)) for t in tasks),
        direction=HIGHER if direction == "higher" else LOWER,
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

    @property
    def band(self) -> str:
        return self.score.band if self.score else ""


@dataclass(slots=True)
class DimensionResult:
    dimension: Dimension
    score: float | None  # průměr z položek otočených k patologii; None = bez dat
    items: list[ItemResult]
    reason: str = ""  # proč dimenze chybí; prázdné = má skóre

    @property
    def band(self) -> str:
        return band(self.score) if self.score is not None else ""

    @property
    def scored(self) -> list[ItemResult]:
        return [i for i in self.items if i.score is not None]

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


def _task_label(task: str) -> str:
    return contract.TASK_SHORT.get(task, task)


def evaluate(
    dimensions: list[Dimension],
    rows_by_task: dict[str, pd.DataFrame],
    norm: Norm,
    *,
    age: float | None = None,
    sex: str = "",
    window: int | None = DEFAULT_WINDOW,
    same_sex: bool = False,
) -> Evaluation:
    """Skóre dimenzí jednoho vyšetření proti normě.

    `window` None = bez věkového omezení. Položka úlohy, kterou vyšetření
    nemá, se vynechá bez výčitek; položka, kterou nemá norma, dostane důvod.
    """
    values = visit_values(rows_by_task)
    sex = meta.parse_sex(sex) if sex else ""
    selections: dict[str, Selection] = {}
    for task in sorted({t for d in dimensions for t in d.tasks}):
        if task in values and task in norm.tasks:
            selections[task] = norm.select(task, age=age, sex=sex, window=window, same_sex=same_sex)

    results: list[DimensionResult] = []
    for dim in dimensions:
        items: list[ItemResult] = []
        for item in dim.items:
            for task in item.tasks:
                if task not in values:
                    continue  # vyšetření tuhle úlohu nemá
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
                items.append(ItemResult(item, task, value, score, reason))
        scored = [i.score.z for i in items if i.score is not None]
        if scored:
            results.append(DimensionResult(dim, sum(scored) / len(scored), items))
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
