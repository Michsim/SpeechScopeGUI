"""Smlouva mezi GUI a knihovnou SpeechScope.

Všechno, co GUI o knihovně ví napevno, je tady. Zbytek (feature a providery,
jejich parametry, stav modelů) si GUI tahá za běhu přes `list --json`,
`list --providers --json`, `list --params NAME --json` a `doctor --json`.

Tvar událostí `--progress-json` je smlouva knihovny (viz její `_progress.py`):
``start`` -> pro každou nahrávku ``begin``, ``stage`` (provider: running, pak
done/cached/error) a ``file`` -> ``done`` -> ``saved``. Při změně tvaru
knihovna zvedne `protocol` v události `start`. Verze 1 (bez ``begin``
a ``stage``) se stále přijímá, jen GUI neukáže průběh uvnitř nahrávky.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Verze smlouvy událostí, kterou GUI umí. Porovnává se s polem `protocol`
# v události `start`. Starší verze v `SUPPORTED_PROTOCOLS` GUI také přijme.
PROTOCOL_VERSION = 2
SUPPORTED_PROTOCOLS: tuple[int, ...] = (1, 2)

# Verze knihovny, proti které bylo GUI naposledy ověřené (`speechscope version`).
KNOWN_LIBRARY_VERSION = "0.1.0"

TASKS: tuple[str, ...] = ("phonation", "ddk", "story", "monologue", "reading")

TASK_LABELS: dict[str, str] = {
    "phonation": "Fonace (prodloužená samohláska)",
    "ddk": "Diadochokineze (pa-ta-ka)",
    "story": "Vyprávění pohádky",
    "monologue": "Monolog",
    "reading": "Čtený text",
}

# Jazyk nahrávek: nabídka se bere z `doctor --json` (models.stanza.languages),
# tohle je záloha pro starší knihovnu a popisky.
DEFAULT_LANGUAGES: tuple[str, ...] = ("cs", "en")
LANGUAGE_LABELS: dict[str, str] = {
    "cs": "čeština",
    "en": "angličtina",
    "sk": "slovenština",
    "de": "němčina",
}


def language_label(code: str) -> str:
    return LANGUAGE_LABELS.get(code, code)


TASK_SHORT: dict[str, str] = {
    "phonation": "Fonace",
    "ddk": "DDK",
    "story": "Pohádka",
    "monologue": "Monolog",
    "reading": "Čtení",
}

DOMAINS: tuple[str, ...] = ("acoustic", "linguistic")

DOMAIN_LABELS: dict[str, str] = {
    "acoustic": "Akustika",
    "linguistic": "Lingvistika",
}

# Skupina = doména a druhá část jména feature (`acoustic.timing.pauses`).
# Neznámá skupina se v UI ukáže surově, nikdy nespadne.
GROUP_LABELS: dict[str, str] = {
    "acoustic.articulation": "Akustika · artikulace",
    "acoustic.intensity": "Akustika · intenzita",
    "acoustic.pitch": "Akustika · výška",
    "acoustic.quality": "Akustika · kvalita hlasu",
    "acoustic.spectral": "Akustika · spektrum",
    "acoustic.timing": "Akustika · časování",
    "linguistic.lexical": "Lingvistika · lexikum",
    "linguistic.syntactic": "Lingvistika · syntax",
}

# Stejné jako `speechscope.signal.AUDIO_SUFFIXES`.
AUDIO_SUFFIXES: tuple[str, ...] = (".wav", ".flac", ".ogg", ".mp3", ".m4a")

# Ruční vstupy vedle nahrávky, které knihovna bere přednostně před modelem.
LABELS_SUFFIX = ".labels.txt"
TRANSCRIPT_SUFFIX = ".txt"

# Balík modelů z `speechscope models pack`: zip s `manifest.json`. Instaluje
# ho `models unpack`; kód 1 = některý model selhal, kód 2 = není to balík.
MODELS_BUNDLE_FILTER = "Balík modelů SpeechScope (*.zip)"

# Pořadí = pořadí, v jakém providery v běhu přicházejí na řadu.
PROVIDER_LABELS: dict[str, str] = {
    "segments": "Segmentace řeči",
    "phonemes": "Fonémy (phnrec)",
    "transcript": "Přepis (Whisper)",
    "nlp": "Jazykový rozbor (Stanza)",
}

# Krátké popisky do tabulek.
PROVIDER_SHORT: dict[str, str] = {
    "segments": "segmentace",
    "transcript": "přepis",
    "nlp": "jaz. rozbor",
    "phonemes": "fonémy",
}

# Orientační cena běhu podle nejdražšího provideru, dokud není změřená.
# Pořadí od nejdražšího.
PROVIDER_COST: tuple[tuple[str, str], ...] = (
    ("transcript", "minuty na nahrávku"),
    ("nlp", "minuty na nahrávku"),
    ("segments", "desítky sekund na nahrávku"),
    ("phonemes", "sekundy na nahrávku"),
)
NO_MODELS_COST = "sekundy na nahrávku"

# --- události --progress-json -------------------------------------------------


@dataclass(frozen=True, slots=True)
class StartEvent:
    total: int
    task: str | None
    features: list[str]
    providers: list[str]
    protocol: int = PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class BeginEvent:
    """Nahrávka se začíná zpracovávat (smlouva 2)."""

    index: int
    path: str


STAGE_STATUSES: tuple[str, ...] = ("running", "done", "cached", "error")


@dataclass(frozen=True, slots=True)
class StageEvent:
    """Provider nad jednou nahrávkou (smlouva 2)."""

    index: int
    provider: str
    status: str  # "running" | "done" | "cached" | "error"
    seconds: float | None = None
    msg: str | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"


@dataclass(frozen=True, slots=True)
class FileEvent:
    index: int
    path: str
    status: str  # "ok" | "error"
    msg: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True, slots=True)
class DoneEvent:
    n_ok: int


@dataclass(frozen=True, slots=True)
class SavedEvent:
    out: str


Event = StartEvent | BeginEvent | StageEvent | FileEvent | DoneEvent | SavedEvent


class ContractError(ValueError):
    """Řádek na stdout, kterému GUI nerozumí."""


def parse_event(line: str) -> Event | None:
    """Přeloží jeden řádek stdout na událost.

    Prázdné řádky vrací `None`. Řádek, který není JSON objekt s polem
    `event`, je porušení smlouvy: stdout má být při `--progress-json`
    čistý.
    """
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"stdout není JSON: {text[:200]!r}") from exc
    if not isinstance(payload, dict) or "event" not in payload:
        raise ContractError(f"chybí pole event: {text[:200]!r}")

    kind = payload["event"]
    try:
        if kind == "start":
            protocol = int(payload.get("protocol", 1))
            if protocol not in SUPPORTED_PROTOCOLS:
                raise ContractError(
                    f"knihovna mluví smlouvou {protocol}, GUI umí {PROTOCOL_VERSION}"
                )
            return StartEvent(
                total=int(payload["total"]),
                task=payload.get("task"),
                features=list(payload.get("features", [])),
                providers=list(payload.get("providers", [])),
                protocol=protocol,
            )
        if kind == "begin":
            return BeginEvent(index=int(payload["index"]), path=str(payload["path"]))
        if kind == "stage":
            status = str(payload["status"])
            if status not in STAGE_STATUSES:
                raise ContractError(f"neznámý stav stage {status!r}")
            seconds = payload.get("seconds")
            return StageEvent(
                index=int(payload["index"]),
                provider=str(payload["provider"]),
                status=status,
                seconds=float(seconds) if seconds is not None else None,
                msg=payload.get("msg"),
            )
        if kind == "file":
            return FileEvent(
                index=int(payload["index"]),
                path=str(payload["path"]),
                status=str(payload["status"]),
                msg=payload.get("msg"),
            )
        if kind == "done":
            return DoneEvent(n_ok=int(payload["n_ok"]))
        if kind == "saved":
            return SavedEvent(out=str(payload["out"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"událost {kind!r} má špatný tvar: {text[:200]!r}") from exc
    raise ContractError(f"neznámá událost {kind!r}")


@dataclass(slots=True)
class BatchState:
    """Souhrn průběhu jedné dávky, skládaný z událostí."""

    total: int = 0
    task: str | None = None
    features: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    protocol: int = PROTOCOL_VERSION
    files: list[FileEvent] = field(default_factory=list)
    # Nahrávka, která se právě zpracovává (mezi `begin` a `file`).
    current: BeginEvent | None = None
    # index nahrávky -> provider -> poslední `stage`
    stages: dict[int, dict[str, StageEvent]] = field(default_factory=dict)
    n_ok: int | None = None
    out: str | None = None

    @property
    def processed(self) -> int:
        return len(self.files)

    @property
    def detailed(self) -> bool:
        """Knihovna posílá `begin` a `stage` (smlouva 2)."""
        return self.protocol >= 2

    def running_stage(self) -> StageEvent | None:
        """Provider, který právě běží nad aktuální nahrávkou."""
        if self.current is None:
            return None
        for stage in self.stages.get(self.current.index, {}).values():
            if stage.running:
                return stage
        return None

    @property
    def errors(self) -> list[FileEvent]:
        return [f for f in self.files if not f.ok]

    @property
    def finished(self) -> bool:
        return self.out is not None

    def apply(self, event: Event) -> None:
        match event:
            case StartEvent():
                self.total = event.total
                self.task = event.task
                self.features = list(event.features)
                self.providers = list(event.providers)
                self.protocol = event.protocol
                self.files.clear()
                self.current = None
                self.stages.clear()
                self.n_ok = None
                self.out = None
            case BeginEvent():
                self.current = event
            case StageEvent():
                self.stages.setdefault(event.index, {})[event.provider] = event
            case FileEvent():
                self.files.append(event)
                self.current = None
            case DoneEvent():
                self.n_ok = event.n_ok
            case SavedEvent():
                self.out = event.out
