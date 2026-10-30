"""Vývoj v čase: skóre dimenzí jednoho pacienta přes jeho vyšetření (30. 10. 2026).

Nic nového se nepočítá. Stránka Pacienti dá vyhodnocení každého vyšetření
(`dimensions.evaluate`, u každého kontroly podle věku při tom vyšetření)
a tady se z nich složí tabulka dimenze × vyšetření: skóre v SD, pásmo,
změna proti předchozímu vyšetření a za celé období. Změna je rozdíl v SD,
ne tvrzení o statistické významnosti (test-retest rozptyl neznáme).

Vyšetření bez data se do vývoje nebere (nejde zařadit na osu); vyšetření,
které se počítalo jinou verzí feature než norma, nese `outdated` a UI ho
kreslí dutým bodem s ⚠. Dvě vyšetření v jeden den jsou vedle sebe v pořadí
založení.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .assessments import Assessment
from .dimensions import Dimension, Evaluation, Item, _item_tasks, visit_values
from .meta import normalize_visit
from .norms import BOTH, HIGHER


@dataclass(slots=True)
class VisitPoint:
    assessment_id: str
    date: str  # ISO
    age: float | None = None
    outdated: bool = False  # některá položka s jinou verzí feature než norma


@dataclass(slots=True)
class TrendRow:
    dimension: Dimension
    scores: list[float | None]  # po vyšetřeních, None = dimenze v tom vyšetření chybí
    bands: list[str] = field(default_factory=list)

    @property
    def shown(self) -> bool:
        return any(s is not None for s in self.scores)

    @property
    def last(self) -> float | None:
        return next((s for s in reversed(self.scores) if s is not None), None)

    @property
    def last_band(self) -> str:
        pairs = list(zip(self.scores, self.bands, strict=True))
        return next((b for s, b in reversed(pairs) if s is not None), "")

    @property
    def first(self) -> float | None:
        return next((s for s in self.scores if s is not None), None)

    @property
    def change_last(self) -> float | None:
        """Rozdíl posledního a předchozího vyšetření s hodnotou (kladný = horší)."""
        known = [s for s in self.scores if s is not None]
        return known[-1] - known[-2] if len(known) >= 2 else None

    @property
    def change_total(self) -> float | None:
        known = [s for s in self.scores if s is not None]
        return known[-1] - known[0] if len(known) >= 2 else None

    @property
    def abnormal(self) -> bool:
        """Poslední známé skóre mimo ±1 SD."""
        last = self.last
        return last is not None and abs(last) >= 1.0


@dataclass(slots=True)
class Timeline:
    visits: list[VisitPoint]
    rows: list[TrendRow]
    skipped: int = 0  # vyšetření bez data, která se do vývoje nevzala

    @property
    def enough(self) -> bool:
        return len(self.visits) >= 2

    def shown_rows(self, *, only_abnormal: bool = False) -> list[TrendRow]:
        rows = [r for r in self.rows if r.shown]
        return [r for r in rows if r.abnormal] if only_abnormal else rows

    @property
    def outdated(self) -> list[VisitPoint]:
        return [v for v in self.visits if v.outdated]


def build_timeline(entries: list[tuple[Assessment, Evaluation]]) -> Timeline:
    """Řada vyšetření jednoho pacienta (v libovolném pořadí) → tabulka dimenze × vyšetření."""
    ordered, skipped = _sorted_entries(entries)
    visits = [VisitPoint(a.id, d, a.age, bool(ev.version_changes)) for d, a, ev in ordered]
    dimensions: list[Dimension] = []
    for _d, _a, ev in ordered:
        for result in ev.results:
            if all(result.dimension.key != x.key for x in dimensions):
                dimensions.append(result.dimension)
    rows: list[TrendRow] = []
    for dim in dimensions:
        scores: list[float | None] = []
        bands: list[str] = []
        for _d, _a, ev in ordered:
            result = next((r for r in ev.results if r.dimension.key == dim.key), None)
            scores.append(result.score if result is not None and result.shown else None)
            bands.append(result.band if result is not None and result.shown else "")
        rows.append(TrendRow(dim, scores, bands))
    return Timeline(visits, rows, skipped)


def _sorted_entries(entries: list[tuple[Assessment, object]]) -> tuple[list[tuple], int]:
    """Vyšetření s datem podle data (pak založení a id) a počet vynechaných bez data."""
    dated = [(normalize_visit(a.date), a, payload) for a, payload in entries]
    skipped = sum(1 for d, _a, _p in dated if not d)
    ordered = sorted(
        ((d, a, p) for d, a, p in dated if d), key=lambda t: (t[0], t[1].created, t[1].id)
    )
    return ordered, skipped


# --- měřítko Hodnoty: surové hodnoty položek, i bez normy (30. 10. 2026 večer) ---------


@dataclass(slots=True)
class ValueRow:
    """Jedna položka dimenze (sloupec × úloha) přes vyšetření, v jednotkách feature."""

    dimension: Dimension
    item: Item
    task: str
    values: list[float | None]  # po vyšetřeních, None = úloha nebo sloupec chybí
    # verze feature jiná než u posledního vyšetření s hodnotou (hodnoty nejsou srovnatelné)
    outdated: list[bool] = field(default_factory=list)

    @property
    def shown(self) -> bool:
        return any(v is not None for v in self.values)

    @property
    def last(self) -> float | None:
        return next((v for v in reversed(self.values) if v is not None), None)

    @property
    def first(self) -> float | None:
        return next((v for v in self.values if v is not None), None)

    @property
    def change_last(self) -> float | None:
        known = [v for v in self.values if v is not None]
        return known[-1] - known[-2] if len(known) >= 2 else None

    @property
    def change_total(self) -> float | None:
        known = [v for v in self.values if v is not None]
        return known[-1] - known[0] if len(known) >= 2 else None

    @property
    def change_percent(self) -> float | None:
        """Změna za období v procentech první hodnoty; bez ní nebo z nuly None."""
        first, delta = self.first, self.change_total
        if first is None or delta is None or first == 0:
            return None
        return 100.0 * delta / abs(first)

    def side(self, delta: float | None) -> int:
        """Kam změna míří podle směru položky: 1 k patologii, -1 od ní, 0 bez soudu
        (položka s patologií oběma směry, beze změny nebo bez hodnoty)."""
        if delta is None or delta == 0 or self.item.direction == BOTH:
            return 0
        worse = delta > 0 if self.item.direction == HIGHER else delta < 0
        return 1 if worse else -1


@dataclass(slots=True)
class ValueTimeline:
    visits: list[VisitPoint]
    rows: list[ValueRow]
    skipped: int = 0

    @property
    def enough(self) -> bool:
        return len(self.visits) >= 2

    def shown_rows(self) -> list[ValueRow]:
        return [r for r in self.rows if r.shown]

    def groups(self) -> list[tuple[Dimension, list[ValueRow]]]:
        """Řádky s hodnotou po dimenzích v pořadí definice; dimenze bez položky se vynechá."""
        out: list[tuple[Dimension, list[ValueRow]]] = []
        for row in self.shown_rows():
            if out and out[-1][0].key == row.dimension.key:
                out[-1][1].append(row)
            else:
                out.append((row.dimension, [row]))
        return out

    @property
    def outdated(self) -> list[VisitPoint]:
        return [v for v in self.visits if v.outdated]


def build_value_timeline(
    dimensions: list[Dimension],
    entries: list[tuple[Assessment, dict[str, pd.DataFrame], dict[str, dict[str, str]]]],
) -> ValueTimeline:
    """Vyšetření (tabulky řádků po úlohách a verze feature) → položky × vyšetření.

    Hodnota položky je týž průměr po úloze, jaký bere `dimensions.evaluate`; norma
    se nepoužije vůbec. Vlastní úloha je další zdroj své základní úlohy jako ve
    stromu. Vyšetření, jehož feature má jinou verzi než u posledního vyšetření
    s hodnotou, dostane v řádku `outdated` (dutý bod, ⚠ v tabulce).
    """
    ordered, skipped = _sorted_entries([(a, (rows, versions)) for a, rows, versions in entries])
    values = [visit_values(rows) for _d, _a, (rows, _v) in ordered]
    versions = [v for _d, _a, (_rows, v) in ordered]
    rows_out: list[ValueRow] = []
    for dim in dimensions:
        for item in dim.items:
            item_tasks: list[str] = []
            for vals in values:
                for task in _item_tasks(item, vals):
                    if task not in item_tasks:
                        item_tasks.append(task)
            for task in item_tasks:
                series = [
                    v if v is not None and math.isfinite(v) else None
                    for v in (vals.get(task, {}).get(item.column) for vals in values)
                ]
                if all(v is None for v in series):
                    continue
                vers = [ver.get(task, {}).get(item.feature) for ver in versions]
                reference = next(
                    (
                        ver
                        for ver, v in zip(reversed(vers), reversed(series), strict=True)
                        if ver and v is not None
                    ),
                    None,
                )
                outdated = [bool(ver and reference and ver != reference) for ver in vers]
                rows_out.append(ValueRow(dim, item, task, series, outdated))
    visits = [
        VisitPoint(a.id, d, a.age, any(r.outdated[i] for r in rows_out))
        for i, (d, a, _p) in enumerate(ordered)
    ]
    return ValueTimeline(visits, rows_out, skipped)


def format_change(delta: float | None) -> str:
    """„▲ +0,5 SD“ (horší), „▼ −0,3 SD“ (lepší), „0,0 SD“ beze změny; bez hodnoty „–“."""
    if delta is None:
        return "–"
    text = f"{delta:+.1f}".replace(".", ",")
    if text in ("+0,0", "-0,0"):
        return "0,0 SD"
    arrow = "▲ " if delta > 0 else "▼ "
    return f"{arrow}{text} SD"


__all__ = [
    "Timeline",
    "TrendRow",
    "ValueRow",
    "ValueTimeline",
    "VisitPoint",
    "build_timeline",
    "build_value_timeline",
    "format_change",
]
