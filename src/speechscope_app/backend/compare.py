"""Porovnání skupin z hotových běhů (Výsledky › Porovnat…).

Mockup https://claude.ai/artifact/LaQwfCHYfEx4kptyEDoWk3 (`docs/mockups/porovnani-skupin/`
mimo git), rozhodnuto 31. 10. 2026:

- skupiny podle **běhu** (výchozí), nebo ručně podle **sloupce metadat** (hodnoty napříč
  všemi vybranými běhy, spojí se podle hodnoty);
- **průměr po osobě** (výchozí): víc nahrávek jedné osoby ve skupině se zprůměruje po
  feature, n jsou osoby; bez sloupce `subject` po nahrávkách;
- metoda **neparametrická** (výchozí: Mann-Whitney / Kruskal-Wallis, Cliffovo δ / ε²),
  **parametrická** (Welchův t-test / Welchova ANOVA, Hedgesovo g / η²) nebo **podle
  normality** (u každé feature Shapiro-Wilk ve všech skupinách); normalita je vidět vždy;
- korekce FDR (Benjamini-Hochberg) přes feature, u víc skupin i párová porovnání;
- srovnávají se jen **společné** feature, ostatní jsou v `only_in`; jiná verze feature
  mezi skupinami = varování; pod `MIN_N` hodnot ve skupině se netestuje;
- chybné řádky a nahrávky, které kontrola kvality označila jako nepoužitelné, se
  vynechají (`include_bad` je vrátí).

Nic se neukládá, porovnání je pohled nad `features.csv` běhů.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats

from ..i18n import tr
from . import meta, quality, tasks
from .history import RunInfo
from .norms import is_feature_column

NONPARAMETRIC, PARAMETRIC, AUTO = "nonparametric", "parametric", "auto"
METHODS = (NONPARAMETRIC, PARAMETRIC, AUTO)
BY_RUN, BY_COLUMN = "run", "column"
MIN_N = 3  # méně hodnot ve skupině = bez testu
ALPHA = 0.05  # hranice „liší se“ (q) i normality (Shapiro p)
MAX_GROUP_VALUES = 20  # sloupec s víc hodnotami není skupina
NO_GROUP = ""  # prázdná hodnota sloupce skupiny


@dataclass(slots=True)
class Group:
    name: str
    frame: pd.DataFrame  # řádky skupiny (po vynechání chybných a nepoužitelných)
    runs: list[RunInfo] = field(default_factory=list)

    @property
    def subjects(self) -> int:
        if meta.SUBJECT not in self.frame:
            return len(self.frame)
        return int(self.frame[meta.SUBJECT].astype(str).str.strip().replace("", np.nan).nunique())


@dataclass(slots=True)
class Pair:
    a: int  # index skupiny
    b: int
    effect: float | None
    p: float | None
    q: float | None = None


@dataclass(slots=True)
class FeatureResult:
    column: str
    n: list[int]  # počet hodnot po skupinách
    center: list[float | None]  # medián (neparametrická) nebo průměr (parametrická)
    median: list[float | None]
    mean: list[float | None]
    sd: list[float | None]
    q1: list[float | None]
    q3: list[float | None]
    values: list[np.ndarray]  # hodnoty po skupinách (po logaritmu, když je zapnutý)
    shapiro: list[float | None]  # p po skupinách
    skew: list[float | None]
    normal: bool | None  # všechny skupiny Shapiro p ≥ ALPHA; None = málo hodnot
    test: str = ""  # MW | t | KW | ANOVA
    statistic: float | None = None
    p: float | None = None
    q: float | None = None
    effect: float | None = None  # δ / g u dvou skupin, ε² / η² u víc
    effect_name: str = ""  # delta | g | epsilon2 | eta2
    other_effect: float | None = None  # druhá míra pro srovnání (g u δ, δ u g)
    pairs: list[Pair] = field(default_factory=list)
    note: str = ""  # few | log (proč se netestovalo)
    versions: dict[str, str] = field(default_factory=dict)  # skupina → verze, když se liší

    @property
    def tested(self) -> bool:
        return self.p is not None

    @property
    def significant(self) -> bool:
        return self.q is not None and self.q < ALPHA


@dataclass(slots=True)
class Comparison:
    groups: list[Group]
    results: list[FeatureResult]
    method: str
    by_subject: bool
    log: bool
    only_in: dict[str, list[str]]  # skupina → sloupce, které ostatní nemají
    excluded_errors: int = 0
    excluded_bad: int = 0
    no_subject: bool = False  # běhy bez sloupce subject: po nahrávkách
    shared_subjects: list[str] = field(default_factory=list)  # osoby ve víc skupinách

    @property
    def significant(self) -> int:
        return sum(1 for r in self.results if r.significant)


# --- načtení a skupiny ------------------------------------------------------------


def task_problem(runs: list[RunInfo]) -> str:
    """Proč tyto běhy nejde porovnat (různé úlohy); prázdné = jde."""
    bases = {tasks.base_task(r.task) for r in runs if r.task}
    if len(bases) > 1:
        names = ", ".join(sorted(tasks.label(b) for b in bases))
        return tr("běhy musí mít stejnou úlohu ({tasks})").format(tasks=names)
    return ""


def read_table(run: RunInfo) -> pd.DataFrame:
    frame = pd.read_csv(run.dir / "features.csv")
    frame = frame.rename(columns={c: meta.canonical_column(str(c)) for c in frame.columns})
    # oba sloupce naráz (assign) na scelené tabulce: přidávání po jednom do tabulky se
    # stovkami sloupců pandas hlásil jako PerformanceWarning („highly fragmented“)
    return frame.copy().assign(
        _run=str(run.dir),  # složka běhu: názvy běhů se mohou opakovat
        _task=run.task or "",
    )


def group_columns(frames: list[pd.DataFrame]) -> dict[str, dict[str, int]]:
    """Kategoriální sloupce metadat a počty řádků po hodnotách (kandidáti na skupiny)."""
    skip = {"file", "path", "task", "error", "speechscope_version", "notes", meta.SUBJECT}
    skip |= {meta.AGE, "_run", "_task"}
    out: dict[str, dict[str, int]] = {}
    for frame in frames:
        for column in frame.columns:
            if column in skip or is_feature_column(str(column)) or "." in str(column):
                continue
            values = frame[column].map(lambda v: "" if pd.isna(v) else str(v).strip())
            counts = values.value_counts().to_dict()
            target = out.setdefault(str(column), {})
            for value, n in counts.items():
                target[value] = target.get(value, 0) + int(n)
    return {
        c: dict(sorted(v.items()))
        for c, v in out.items()
        if 2 <= len([k for k in v if k]) <= MAX_GROUP_VALUES
    }


def run_names(runs: list[RunInfo]) -> list[str]:
    """Názvy skupin podle běhu; běhy se stejným názvem odliší datum, pak pořadí."""
    original = [r.display_name for r in runs]
    names = list(original)
    for i, run in enumerate(runs):
        if original.count(run.display_name) > 1 and run.started is not None:
            names[i] = f"{run.display_name} ({run.started:%d. %m. %H:%M})"
    seen: dict[str, int] = {}
    out = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name} #{seen[name]}")
    return out


def _clean(frame: pd.DataFrame, include_bad: bool) -> tuple[pd.DataFrame, int, int]:
    """Bez chybných řádků a (bez `include_bad`) bez nepoužitelných nahrávek."""
    errors = bad = 0
    keep = []
    for _i, row in frame.iterrows():
        error = row.get("error")
        if isinstance(error, str) and error.strip():
            errors += 1
            keep.append(False)
            continue
        if not include_bad:
            task = str(row.get("_task") or row.get("task") or "")
            level = quality.level_of(quality.assess(row.to_dict(), tasks.base_task(task) or task))
            if level == quality.BAD:
                bad += 1
                keep.append(False)
                continue
        keep.append(True)
    return frame[keep], errors, bad


def make_groups(
    runs: list[RunInfo],
    by: str = BY_RUN,
    column: str = "",
    values: list[str] | None = None,
    *,
    include_bad: bool = False,
) -> tuple[list[Group], int, int]:
    """Skupiny z běhů: běh = skupina, nebo hodnoty sloupce napříč běhy (`values`)."""
    frames = [(run, read_table(run)) for run in runs]
    errors = bad = 0
    cleaned = []
    for run, frame in frames:
        frame, e, b = _clean(frame, include_bad)
        errors += e
        bad += b
        cleaned.append((run, frame))
    if by == BY_RUN:
        names = run_names([run for run, _f in cleaned])
        groups = [Group(n, f, [r]) for n, (r, f) in zip(names, cleaned, strict=True)]
        return groups, errors, bad
    merged = pd.concat([f for _r, f in cleaned], ignore_index=True, sort=False)
    if column not in merged:
        return [], errors, bad
    labels = merged[column].map(lambda v: "" if pd.isna(v) else str(v).strip())
    wanted = values if values is not None else sorted(v for v in labels.unique() if v)
    groups = []
    for value in wanted:
        part = merged[labels == value]
        names = sorted(set(part["_run"]))
        groups.append(Group(value, part, [r for r, _f in cleaned if str(r.dir) in names]))
    return groups, errors, bad


# --- statistika -------------------------------------------------------------------


def _finite(values: pd.Series) -> np.ndarray:
    arr = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return arr[np.isfinite(arr)]


def _per_subject(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Průměr feature po osobě (řádky bez pacienta zůstávají samostatně)."""
    if meta.SUBJECT not in frame:
        return frame[columns]
    subject = frame[meta.SUBJECT].map(lambda v: "" if pd.isna(v) else str(v).strip())
    numeric = frame[columns].apply(pd.to_numeric, errors="coerce")
    with_subject = numeric[subject != ""].groupby(subject[subject != ""]).mean()
    without = numeric[subject == ""]
    return pd.concat([with_subject, without], ignore_index=True)


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """δ = P(a > b) − P(a < b); kladné = skupina a má vyšší hodnoty."""
    u = stats.mannwhitneyu(a, b, alternative="two-sided").statistic
    return float(2 * u / (len(a) * len(b)) - 1)


def hedges_g(a: np.ndarray, b: np.ndarray) -> float | None:
    n1, n2 = len(a), len(b)
    pooled = ((n1 - 1) * a.var(ddof=1) + (n2 - 1) * b.var(ddof=1)) / (n1 + n2 - 2)
    if pooled <= 0:
        return None
    d = (a.mean() - b.mean()) / math.sqrt(pooled)
    return float(d * (1 - 3 / (4 * (n1 + n2) - 9)))


def welch_anova(groups: list[np.ndarray]) -> tuple[float, float]:
    """Welchova ANOVA (nestejné rozptyly): F a p."""
    k = len(groups)
    n = np.array([len(g) for g in groups], dtype=float)
    means = np.array([g.mean() for g in groups])
    var = np.array([g.var(ddof=1) for g in groups])
    if np.any(var <= 0):
        raise ValueError("zero variance")
    w = n / var
    grand = float(np.sum(w * means) / np.sum(w))
    a = np.sum(w * (means - grand) ** 2) / (k - 1)
    tmp = np.sum((1 - w / np.sum(w)) ** 2 / (n - 1))
    b = 1 + 2 * (k - 2) / (k**2 - 1) * tmp
    f = float(a / b)
    df2 = float((k**2 - 1) / (3 * tmp))
    return f, float(stats.f.sf(f, k - 1, df2))


def eta_squared(groups: list[np.ndarray]) -> float:
    allv = np.concatenate(groups)
    grand = allv.mean()
    ss_between = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
    ss_total = float(np.sum((allv - grand) ** 2))
    return float(ss_between / ss_total) if ss_total > 0 else 0.0


def fdr(pvalues: list[float | None]) -> list[float | None]:
    """Benjamini-Hochberg; None (netestováno) zůstává None a do korekce nejde."""
    idx = [i for i, p in enumerate(pvalues) if p is not None and not math.isnan(p)]
    out: list[float | None] = [None] * len(pvalues)
    if not idx:
        return out
    adjusted = stats.false_discovery_control([pvalues[i] for i in idx], method="bh")
    for i, q in zip(idx, adjusted, strict=True):
        out[i] = float(q)
    return out


def _describe(values: np.ndarray) -> tuple:
    if not len(values):
        return None, None, None, None, None, None
    q1, med, q3 = np.percentile(values, [25, 50, 75])
    sd = float(values.std(ddof=1)) if len(values) > 1 else None
    shapiro = None
    if len(values) >= MIN_N and np.ptp(values) > 0:
        shapiro = float(stats.shapiro(values).pvalue)
    skew = float(stats.skew(values)) if len(values) >= MIN_N and np.ptp(values) > 0 else None
    return float(med), float(values.mean()), sd, (float(q1), float(q3)), shapiro, skew


def _test_feature(column: str, arrays: list[np.ndarray], method: str, log: bool) -> FeatureResult:
    note = ""
    if log:
        if any(np.any(a <= 0) for a in arrays):
            note = "log"
        else:
            arrays = [np.log(a) for a in arrays]
    described = [_describe(a) for a in arrays]
    result = FeatureResult(
        column=column,
        n=[len(a) for a in arrays],
        center=[],
        median=[d[0] for d in described],
        mean=[d[1] for d in described],
        sd=[d[2] for d in described],
        q1=[d[3][0] if d[3] else None for d in described],
        q3=[d[3][1] if d[3] else None for d in described],
        values=arrays,
        shapiro=[d[4] for d in described],
        skew=[d[5] for d in described],
        normal=None,
        note=note,
    )
    shapiros = result.shapiro
    if all(p is not None for p in shapiros):
        result.normal = all(p >= ALPHA for p in shapiros)  # type: ignore[operator]
    parametric = method == PARAMETRIC or (method == AUTO and result.normal is True)
    result.center = result.mean if parametric else result.median
    if note:
        return result
    if any(len(a) < MIN_N for a in arrays):
        result.note = "few"
        return result
    if np.ptp(np.concatenate(arrays)) == 0:
        result.note = "constant"
        return result
    try:
        if len(arrays) == 2:
            a, b = arrays
            delta = cliffs_delta(a, b)
            g = hedges_g(a, b)
            if parametric:
                res = stats.ttest_ind(a, b, equal_var=False)
                result.test, result.effect_name = "t", "g"
                result.effect, result.other_effect = g, delta
            else:
                res = stats.mannwhitneyu(a, b, alternative="two-sided")
                result.test, result.effect_name = "MW", "delta"
                result.effect, result.other_effect = delta, g
            result.statistic, result.p = float(res.statistic), float(res.pvalue)
        else:
            n_total = sum(len(a) for a in arrays)
            if parametric:
                f, p = welch_anova(arrays)
                result.test, result.effect_name = "ANOVA", "eta2"
                result.statistic, result.p, result.effect = f, p, eta_squared(arrays)
            else:
                res = stats.kruskal(*arrays)
                result.test, result.effect_name = "KW", "epsilon2"
                result.statistic, result.p = float(res.statistic), float(res.pvalue)
                result.effect = float(res.statistic / (n_total - 1)) if n_total > 1 else None
            pairs = []
            for i, j in combinations(range(len(arrays)), 2):
                a, b = arrays[i], arrays[j]
                if parametric:
                    p = float(stats.ttest_ind(a, b, equal_var=False).pvalue)
                    effect = hedges_g(a, b)
                else:
                    p = float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)
                    effect = cliffs_delta(a, b)
                pairs.append(Pair(i, j, effect, p))
            for pair, q in zip(pairs, fdr([pr.p for pr in pairs]), strict=True):
                pair.q = q
            result.pairs = pairs
    except ValueError:
        result.note = "constant"
        result.p = None
    return result


def _feature_of(column: str, versions: dict[str, str]) -> str | None:
    return next((f for f in versions if column.startswith(f + ".")), None)


def compare(
    groups: list[Group],
    method: str = NONPARAMETRIC,
    *,
    by_subject: bool = True,
    log: bool = False,
    excluded_errors: int = 0,
    excluded_bad: int = 0,
) -> Comparison:
    """Porovná skupiny po společných feature; výsledky seřazené podle velikosti efektu."""
    present: list[set[str]] = []
    for group in groups:
        cols = {
            str(c)
            for c in group.frame.columns
            if is_feature_column(str(c)) and group.frame[c].notna().any()
        }
        present.append(cols)
    shared = set.intersection(*present) if present else set()
    only_in = {
        g.name: sorted(cols - set.union(*(p for j, p in enumerate(present) if j != i)))
        for i, (g, cols) in enumerate(zip(groups, present, strict=True))
    }
    only_in = {k: v for k, v in only_in.items() if v}
    no_subject = any(meta.SUBJECT not in g.frame for g in groups)
    columns = sorted(shared)
    tables = [
        _per_subject(g.frame, columns) if by_subject and not no_subject else g.frame[columns]
        for g in groups
    ]
    results = [_test_feature(c, [_finite(t[c]) for t in tables], method, log) for c in columns]
    for result, q in zip(results, fdr([r.p for r in results]), strict=True):
        result.q = q
    # jiná verze feature mezi skupinami (z run.json běhů)
    for result in results:
        per_group: dict[str, str] = {}
        for group in groups:
            found = {
                v
                for run in group.runs
                if (f := _feature_of(result.column, run.feature_versions)) is not None
                for v in [run.feature_versions[f]]
            }
            if found:
                per_group[group.name] = ", ".join(sorted(found))
        if len(set(per_group.values())) > 1:
            result.versions = per_group
    shared_subjects: list[str] = []
    if not no_subject:
        seen: dict[str, set[int]] = {}
        for i, g in enumerate(groups):
            for s in g.frame[meta.SUBJECT].dropna().astype(str).str.strip():
                if s:
                    seen.setdefault(s, set()).add(i)
        shared_subjects = sorted(s for s, idx in seen.items() if len(idx) > 1)
    results.sort(key=lambda r: (not r.tested, -abs(r.effect or 0.0)))
    return Comparison(
        groups=groups,
        results=results,
        method=method,
        by_subject=by_subject and not no_subject,
        log=log,
        only_in=only_in,
        excluded_errors=excluded_errors,
        excluded_bad=excluded_bad,
        no_subject=no_subject,
        shared_subjects=shared_subjects,
    )


def to_frame(comparison: Comparison) -> pd.DataFrame:
    """Tabulka porovnání pro export CSV (jedna řádka na feature)."""
    rows = []
    for r in comparison.results:
        row: dict[str, object] = {"feature": r.column}
        for i, g in enumerate(comparison.groups):
            row[f"{g.name}_n"] = r.n[i]
            row[f"{g.name}_median"] = r.median[i]
            row[f"{g.name}_mean"] = r.mean[i]
            row[f"{g.name}_sd"] = r.sd[i]
            row[f"{g.name}_shapiro_p"] = r.shapiro[i]
        row.update(
            test=r.test,
            statistic=r.statistic,
            p=r.p,
            q=r.q,
            effect=r.effect,
            effect_name=r.effect_name,
            normal=r.normal,
            note=r.note,
            versions="; ".join(f"{k} {v}" for k, v in r.versions.items()),
        )
        for pair in r.pairs:
            name = f"{comparison.groups[pair.a].name}-{comparison.groups[pair.b].name}"
            row[f"{name}_effect"] = pair.effect
            row[f"{name}_q"] = pair.q
        rows.append(row)
    return pd.DataFrame(rows)
