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

from dataclasses import dataclass, field

from .assessments import Assessment
from .dimensions import Dimension, Evaluation
from .meta import normalize_visit


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
    dated = [(normalize_visit(a.date), a, ev) for a, ev in entries]
    skipped = sum(1 for d, _a, _ev in dated if not d)
    ordered = sorted(
        ((d, a, ev) for d, a, ev in dated if d), key=lambda t: (t[0], t[1].created, t[1].id)
    )
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


def format_change(delta: float | None) -> str:
    """„▲ +0,5 SD“ (horší), „▼ −0,3 SD“ (lepší), „0,0 SD“ beze změny; bez hodnoty „–“."""
    if delta is None:
        return "–"
    text = f"{delta:+.1f}".replace(".", ",")
    if text in ("+0,0", "-0,0"):
        return "0,0 SD"
    arrow = "▲ " if delta > 0 else "▼ "
    return f"{arrow}{text} SD"


__all__ = ["Timeline", "TrendRow", "VisitPoint", "build_timeline", "format_change"]
