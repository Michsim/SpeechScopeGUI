"""Normy zdravých kontrol: tabulka kontrol a z-skóre pacienta proti ní.

Norma je složka `norms/<slug>/` se dvěma soubory: `norm.yaml` (jméno, původ,
jazyk, podmínky nahrávání, verze knihovny) a `controls.csv`, což je tabulka
`features.csv` z běhů nad zdravými kontrolami se sloupci `subject`, `task`,
`age`, `sex` z manifestu a se sloupci feature. Surová tabulka kontrol, ne
souhrny: věk pacienta vybírá kontroly z věkového okna až při srovnání.
Přibalené normy jsou ke čtení v `speechscope_app/norms/`, vlastní v AppData
(`settings.norms_dir`), stejně jako protokoly.

Z-skóre se počítá z průměru a směrodatné odchylky vybraných kontrol, u
šikmých sloupců po logaritmu. Směr patologie otáčí znaménko, takže kladné
z = horší než norma. Percentil je podíl kontrol, které jsou na tom lépe
nebo stejně, tedy vyšší = horší.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .. import contract, i18n
from ..i18n import tr
from . import meta
from .trash import send_to_trash

NORM_FILE = "norm.yaml"
CONTROLS_FILE = "controls.csv"
FORMAT_VERSION = 1

DEFAULT_WINDOW = 10  # ± let kolem věku pacienta
WIDER_WINDOWS: tuple[int | None, ...] = (15, 20, None)  # když je kontrol málo; None = bez okna
MIN_CONTROLS = 15  # pod tolik se okno rozšíří
RECOMMENDED_CONTROLS = 20  # pod tolik vlastní norma varuje

HIGHER = 1  # vyšší hodnota = horší
LOWER = -1

_FEATURE_PREFIXES = tuple(f"{d}." for d in contract.DOMAINS)


def is_feature_column(name: str) -> bool:
    return name.startswith(_FEATURE_PREFIXES)


# --- popis normy --------------------------------------------------------------


@dataclass(slots=True)
class NormInfo:
    name: str
    description: str = ""
    language: str = ""  # jazyk nahrávek kontrol (cs, en…); prázdné = nezáleží
    source: str = ""  # odkud kontroly jsou (pracoviště, studie)
    conditions: str = ""  # nahrávací podmínky (místnost, mikrofon)
    library_version: str = ""
    created: str = ""  # ISO datum
    names: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.names.get(i18n.language()) or self.name

    @property
    def display_description(self) -> str:
        return self.descriptions.get(i18n.language()) or self.description

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"format": FORMAT_VERSION, "name": self.name}
        for lang, text in self.names.items():
            data[f"name_{lang}"] = text
        data["description"] = self.description
        for lang, text in self.descriptions.items():
            data[f"description_{lang}"] = text
        data.update(
            language=self.language,
            source=self.source,
            conditions=self.conditions,
            library_version=self.library_version,
            created=self.created,
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NormInfo:
        if int(data.get("format", 1)) > FORMAT_VERSION:
            raise ValueError("norma je z novější verze aplikace")
        return cls(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            language=str(data.get("language", "")),
            source=str(data.get("source", "")),
            conditions=str(data.get("conditions", "")),
            library_version=str(data.get("library_version", "")),
            created=str(data.get("created", "")),
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


# --- výběr kontrol a skóre ----------------------------------------------------


@dataclass(slots=True)
class Stats:
    mean: float
    sd: float
    n: int


@dataclass(slots=True)
class Score:
    value: float
    z: float  # otočené k patologii: kladné = horší než norma
    percentile: float  # podíl kontrol, které jsou na tom lépe nebo stejně (0 až 100)
    mean: float  # průměr kontrol v původních jednotkách
    sd: float
    n: int

    @property
    def band(self) -> str:
        return band(self.z)


def band(z: float) -> str:
    """`ok` do 1 SD, `warn` do 2 SD, `bad` nad 2 SD; lepší než norma je `ok`."""
    if z < 1:
        return "ok"
    return "warn" if z < 2 else "bad"


@dataclass(slots=True)
class Selection:
    """Kontroly vybrané pro jednoho pacienta a jednu úlohu."""

    task: str
    frame: pd.DataFrame
    age: float | None
    window: int | None  # ± let; None = bez věkového omezení
    same_sex: bool
    widened: bool  # okno se muselo rozšířit, kontrol bylo málo

    @property
    def n(self) -> int:
        return int(self.frame[meta.SUBJECT].nunique()) if len(self.frame) else 0

    @property
    def age_range(self) -> tuple[float, float] | None:
        ages = self.frame[meta.AGE].dropna() if meta.AGE in self.frame else pd.Series(dtype=float)
        if ages.empty:
            return None
        return float(ages.min()), float(ages.max())

    @property
    def n_female(self) -> int:
        return int((self.frame[meta.SEX] == meta.FEMALE).sum()) if meta.SEX in self.frame else 0

    @property
    def n_male(self) -> int:
        return int((self.frame[meta.SEX] == meta.MALE).sum()) if meta.SEX in self.frame else 0

    def values(self, column: str, *, log: bool = False) -> pd.Series:
        """Platné hodnoty sloupce u vybraných kontrol, případně po logaritmu."""
        if column not in self.frame:
            return pd.Series(dtype=float)
        vals = pd.to_numeric(self.frame[column], errors="coerce").dropna()
        if log:
            vals = vals[vals > 0]
            vals = vals.map(math.log)
        return vals.astype(float)

    def stats(self, column: str) -> Stats | None:
        """Průměr a SD v původních jednotkách; None, když chybí data nebo rozptyl."""
        vals = self.values(column)
        if len(vals) < 2:
            return None
        sd = float(vals.std(ddof=1))
        if not sd > 0:
            return None
        return Stats(float(vals.mean()), sd, int(len(vals)))

    def score(
        self, column: str, value: float | None, *, direction: int = HIGHER, log: bool = False
    ) -> Score | None:
        """Z-skóre hodnoty pacienta proti vybraným kontrolám; None bez dat."""
        if value is None or not math.isfinite(value):
            return None
        plain = self.stats(column)
        if plain is None:
            return None
        if log:
            if value <= 0:
                return None
            vals = self.values(column, log=True)
            if len(vals) < 2 or not float(vals.std(ddof=1)) > 0:
                return None
            z = (math.log(value) - float(vals.mean())) / float(vals.std(ddof=1))
            raw = self.values(column)
            raw = raw[raw > 0]
        else:
            z = (value - plain.mean) / plain.sd
            raw = self.values(column)
        z *= direction
        signed_controls = raw * direction
        percentile = 100.0 * float((signed_controls <= value * direction).mean())
        return Score(
            value=float(value),
            z=float(z),
            percentile=percentile,
            mean=plain.mean,
            sd=plain.sd,
            n=plain.n,
        )


@dataclass(slots=True)
class Norm:
    info: NormInfo
    controls: pd.DataFrame  # subject, task, age, sex + sloupce feature
    path: Path | None = None
    builtin: bool = False

    @property
    def tasks(self) -> list[str]:
        if "task" not in self.controls:
            return []
        return sorted(str(t) for t in self.controls["task"].dropna().unique())

    @property
    def subjects(self) -> int:
        return int(self.controls[meta.SUBJECT].nunique()) if len(self.controls) else 0

    @property
    def feature_columns(self) -> list[str]:
        return [c for c in self.controls.columns if is_feature_column(c)]

    def has_column(self, task: str, column: str) -> bool:
        if column not in self.controls or "task" not in self.controls:
            return False
        rows = self.controls[self.controls["task"] == task]
        return bool(pd.to_numeric(rows[column], errors="coerce").notna().any())

    def select(
        self,
        task: str,
        *,
        age: float | None = None,
        sex: str = "",
        window: int | None = DEFAULT_WINDOW,
        same_sex: bool = False,
        min_controls: int = MIN_CONTROLS,
    ) -> Selection:
        """Kontroly úlohy ve věkovém okně pacienta; málo kontrol = okno se rozšíří.

        Bez věku pacienta se bere celá norma. `same_sex` omezí na pohlaví
        pacienta, jen když je známé; když by pak kontrol bylo málo, pohlaví
        se pustí dřív než věk.
        """
        rows = (
            self.controls[self.controls["task"] == task]
            if "task" in self.controls
            else self.controls.iloc[0:0]
        )
        want_sex = same_sex and sex in (meta.FEMALE, meta.MALE) and meta.SEX in rows
        for use_sex in (True, False) if want_sex else (False,):
            pool = rows[rows[meta.SEX] == sex] if use_sex else rows
            windows: tuple[int | None, ...] = (
                (window, *WIDER_WINDOWS) if age is not None else (None,)
            )
            for i, w in enumerate(windows):
                if w is None or meta.AGE not in pool:
                    chosen = pool
                else:
                    ages = pd.to_numeric(pool[meta.AGE], errors="coerce")
                    chosen = pool[(ages >= age - w) & (ages <= age + w)]
                if chosen[meta.SUBJECT].nunique() >= min_controls:
                    return Selection(task, chosen, age, w, use_sex, widened=i > 0)
        # ani bez okna není dost: vrátí se, co je, a volající to řekne
        return Selection(task, rows, age, None, False, widened=age is not None)

    # --- soubory ----------------------------------------------------------------

    @classmethod
    def load(cls, folder: Path) -> Norm:
        data = yaml.safe_load((folder / NORM_FILE).read_text(encoding="utf-8")) or {}
        info = NormInfo.from_dict(data)
        controls = pd.read_csv(folder / CONTROLS_FILE)
        return cls(info, normalize_controls(controls), path=folder)

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / NORM_FILE).write_text(
            yaml.safe_dump(self.info.to_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.controls.to_csv(folder / CONTROLS_FILE, index=False)
        self.path = folder


def normalize_controls(frame: pd.DataFrame) -> pd.DataFrame:
    """Kanonické sloupce metadat, čisté pohlaví a věk, dnešní jména úloh.

    Zahodí cesty k nahrávkám a vše, co není metadata nebo feature: v normě
    nemá být nic, co by kontroly identifikovalo nebo prozradilo, odkud jsou.
    """
    out = frame.rename(columns={c: meta.canonical_column(str(c)) for c in frame.columns})
    keep = [c for c in out.columns if c in meta.META_COLUMNS or c == "task" or is_feature_column(c)]
    out = out[keep].copy()
    if "task" in out:
        out["task"] = out["task"].map(lambda t: contract.canonical_task(str(t)))
    if meta.SEX in out:
        out[meta.SEX] = out[meta.SEX].map(meta.parse_sex)
    if meta.AGE in out:
        out[meta.AGE] = out[meta.AGE].map(meta.parse_age)
    if meta.SUBJECT in out:
        out[meta.SUBJECT] = out[meta.SUBJECT].astype(str)
    return out.reset_index(drop=True)


def build_norm(
    frames: list[pd.DataFrame],
    info: NormInfo,
    *,
    group_column: str | None = None,
    group_value: str | None = None,
) -> Norm:
    """Norma z tabulek běhů (features.csv), případně jen z řádků jedné skupiny.

    Vyžaduje sloupce `subject` a `task` (po převodu aliasů). Řádky bez
    pacienta se vynechají; chybějící věk nebo pohlaví nevadí, jen zužují,
    co jde při srovnání vybrat.
    """
    parts = []
    for frame in frames:
        part = frame.rename(columns={c: meta.canonical_column(str(c)) for c in frame.columns})
        if group_column is not None and group_value is not None:
            column = meta.canonical_column(group_column)
            if column not in part:
                continue
            part = part[part[column].astype(str).str.strip() == str(group_value).strip()]
        parts.append(part)
    if not parts:
        raise ValueError(tr("Tabulky neobsahují žádné řádky kontrol."))
    merged = pd.concat(parts, ignore_index=True, sort=False)
    if meta.SUBJECT not in merged or "task" not in merged:
        raise ValueError(
            tr("Tabulka nemá sloupec s pacientem (subject, id, pacient…) nebo sloupec task.")
        )
    merged = merged[
        merged[meta.SUBJECT].notna() & (merged[meta.SUBJECT].astype(str).str.strip() != "")
    ]
    controls = normalize_controls(merged)
    if controls.empty:
        raise ValueError(tr("Tabulky neobsahují žádné řádky kontrol."))
    if not info.created:
        info.created = dt.date.today().isoformat()
    return Norm(info, controls)


def norm_problems(norm: Norm) -> list[str]:
    """Co klinik má vědět, než normu použije: málo kontrol, chybí věk nebo pohlaví."""
    problems: list[str] = []
    for task in norm.tasks:
        rows = norm.controls[norm.controls["task"] == task]
        n = int(rows[meta.SUBJECT].nunique())
        if n < RECOMMENDED_CONTROLS:
            problems.append(
                tr("Úloha {task}: jen {n} kontrol, doporučeno aspoň {min}.").format(
                    task=contract.TASK_SHORT.get(task, task), n=n, min=RECOMMENDED_CONTROLS
                )
            )
    if meta.AGE not in norm.controls or norm.controls[meta.AGE].isna().all():
        problems.append(tr("Kontroly nemají věk, věkové okno nepůjde použít."))
    if meta.SEX not in norm.controls or (norm.controls[meta.SEX] == "").all():
        problems.append(tr("Kontroly nemají pohlaví, výběr podle pohlaví nepůjde použít."))
    return problems


# --- seznam norem ---------------------------------------------------------------


def builtin_norms() -> list[Norm]:
    """Normy přibalené k aplikaci (`speechscope_app/norms/<slug>/`)."""
    out: list[Norm] = []
    root = resources.files("speechscope_app") / "norms"
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.is_dir() and (entry / NORM_FILE).is_file():
            try:
                norm = Norm.load(Path(str(entry)))
            except (ValueError, KeyError, OSError, yaml.YAMLError):
                continue
            norm.builtin = True
            out.append(norm)
    return out


def user_norms(folder: Path) -> list[Norm]:
    if not folder.is_dir():
        return []
    out: list[Norm] = []
    for path in sorted(folder.iterdir()):
        if path.is_dir() and (path / NORM_FILE).is_file():
            try:
                out.append(Norm.load(path))
            except (ValueError, KeyError, OSError, yaml.YAMLError):
                continue  # rozbitá norma nesmí shodit okno
    return out


def all_norms(folder: Path) -> list[Norm]:
    return builtin_norms() + user_norms(folder)


# --- přehled a správa (stránka Normy) -------------------------------------------

NORM_SUFFIX = ".ssnorm"  # export: zip s norm.yaml a controls.csv


@dataclass(slots=True)
class TaskSummary:
    """Kontroly jedné úlohy: kolik osob, kolik žen a mužů, rozpětí věku."""

    task: str
    subjects: int
    female: int
    male: int
    age_range: tuple[float, float] | None
    columns: int  # sloupce feature s aspoň jednou hodnotou


def task_summaries(norm: Norm) -> list[TaskSummary]:
    """Přehled po úlohách v pořadí úloh knihovny; osoby se počítají jednou."""
    order = {t: i for i, t in enumerate(contract.TASKS)}
    out: list[TaskSummary] = []
    for task in sorted(norm.tasks, key=lambda t: (order.get(t, len(order)), t)):
        rows = norm.controls[norm.controls["task"] == task]
        people = rows.drop_duplicates(meta.SUBJECT)
        sexes = people[meta.SEX] if meta.SEX in people else pd.Series(dtype=str)
        ages = (
            pd.to_numeric(people[meta.AGE], errors="coerce").dropna()
            if meta.AGE in people
            else pd.Series(dtype=float)
        )
        filled = [
            c for c in norm.feature_columns if pd.to_numeric(rows[c], errors="coerce").notna().any()
        ]
        out.append(
            TaskSummary(
                task=task,
                subjects=int(people[meta.SUBJECT].nunique()),
                female=int((sexes == meta.FEMALE).sum()),
                male=int((sexes == meta.MALE).sum()),
                age_range=(float(ages.min()), float(ages.max())) if not ages.empty else None,
                columns=len(filled),
            )
        )
    return out


def age_histogram(norm: Norm, step: int = 10) -> list[tuple[int, int]]:
    """Počet osob po věkových pásmech (dolní mez pásma, počet); bez věku prázdné."""
    if meta.AGE not in norm.controls or not len(norm.controls):
        return []
    people = norm.controls.drop_duplicates(meta.SUBJECT)
    ages = pd.to_numeric(people[meta.AGE], errors="coerce").dropna()
    if ages.empty:
        return []
    low, high = int(ages.min()) // step * step, int(ages.max()) // step * step
    return [
        (start, int(((ages >= start) & (ages < start + step)).sum()))
        for start in range(low, high + step, step)
    ]


def norm_slug(name: str) -> str:
    """Jméno normy jako název složky: bez diakritiky a mezer."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "norma"


def unique_norm_dir(folder: Path, name: str) -> Path:
    """Volná složka pro novou normu: slug jména, případně s číslem."""
    base = norm_slug(name)
    target = folder / base
    n = 2
    while target.exists():
        target = folder / f"{base}-{n}"
        n += 1
    return target


def export_norm(norm: Norm, target: Path) -> Path:
    """Norma jako jeden soubor (zip) pro kolegu nebo jiné pracoviště."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            NORM_FILE,
            yaml.safe_dump(norm.info.to_dict(), allow_unicode=True, sort_keys=False),
        )
        zf.writestr(CONTROLS_FILE, norm.controls.to_csv(index=False))
    return target


def read_norm_file(source: Path) -> Norm:
    """Norma ze souboru exportu; jen dva známé soubory, nic se nerozbaluje na disk.

    Soubory smějí být i v jedné podsložce zipu (zabalená složka normy).
    """
    try:
        with zipfile.ZipFile(source) as zf:
            names = {Path(n).name: n for n in zf.namelist() if not n.endswith("/")}
            if NORM_FILE not in names or CONTROLS_FILE not in names:
                raise ValueError(tr("V souboru chybí norm.yaml nebo controls.csv."))
            data = yaml.safe_load(zf.read(names[NORM_FILE]).decode("utf-8")) or {}
            controls = pd.read_csv(io.BytesIO(zf.read(names[CONTROLS_FILE])))
    except zipfile.BadZipFile as exc:
        raise ValueError(tr("Soubor není export normy.")) from exc
    if not isinstance(data, dict) or "name" not in data:
        raise ValueError(tr("norm.yaml nemá jméno normy."))
    controls = normalize_controls(controls)
    if meta.SUBJECT not in controls or "task" not in controls:
        raise ValueError(tr("Tabulka kontrol nemá sloupce subject a task."))
    return Norm(NormInfo.from_dict(data), controls)


def import_norm(source: Path, folder: Path) -> Norm:
    """Uloží normu ze souboru exportu jako vlastní (vedle, nikdy nepřepíše)."""
    norm = read_norm_file(source)
    norm.save(unique_norm_dir(folder, norm.info.name))
    return norm


def trash_norm(norm: Norm) -> None:
    """Vlastní normu do Koše; přibalená se nemaže."""
    if norm.builtin or norm.path is None:
        raise ValueError(tr("Přibalenou normu nejde smazat."))
    if not (norm.path / NORM_FILE).is_file():
        raise ValueError(tr("Složka není norma: {path}").format(path=norm.path))
    send_to_trash(norm.path)
