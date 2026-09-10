"""Smlouva mezi GUI a knihovnou SpeechScope.

Všechno, co GUI o knihovně ví napevno, je tady. Zbytek (feature a providery,
jejich parametry, stav modelů) si GUI tahá za běhu přes `list --json`,
`list --providers --json`, `list --params NAME --json` a `doctor --json`.

Tvar událostí `--progress-json` je smlouva knihovny (viz její `_progress.py`):
``start`` -> ``file`` (pro každou nahrávku) -> ``done`` -> ``saved``.
Při změně tvaru knihovna zvedne `protocol` v události `start`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Verze smlouvy událostí, kterou GUI umí. Porovnává se s polem `protocol`
# v události `start`.
PROTOCOL_VERSION = 1

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

DOMAINS: tuple[str, ...] = ("acoustic", "linguistic")

DOMAIN_LABELS: dict[str, str] = {
    "acoustic": "Akustika",
    "linguistic": "Lingvistika",
}

# Stejné jako `speechscope.signal.AUDIO_SUFFIXES`.
AUDIO_SUFFIXES: tuple[str, ...] = (".wav", ".flac", ".ogg", ".mp3", ".m4a")

# Ruční vstupy vedle nahrávky, které knihovna bere přednostně před modelem.
LABELS_SUFFIX = ".labels.txt"
TRANSCRIPT_SUFFIX = ".txt"

PROVIDER_LABELS: dict[str, str] = {
    "segments": "Segmentace řeči",
    "transcript": "Přepis (Whisper)",
    "nlp": "Jazykový rozbor (Stanza)",
    "phonemes": "Fonémy (phnrec)",
}

# --- události --progress-json -------------------------------------------------


@dataclass(frozen=True, slots=True)
class StartEvent:
    total: int
    task: str | None
    features: list[str]
    providers: list[str]
    protocol: int = PROTOCOL_VERSION


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


Event = StartEvent | FileEvent | DoneEvent | SavedEvent


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
            protocol = int(payload.get("protocol", PROTOCOL_VERSION))
            if protocol != PROTOCOL_VERSION:
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
    files: list[FileEvent] = field(default_factory=list)
    n_ok: int | None = None
    out: str | None = None

    @property
    def processed(self) -> int:
        return len(self.files)

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
                self.files.clear()
                self.n_ok = None
                self.out = None
            case FileEvent():
                self.files.append(event)
            case DoneEvent():
                self.n_ok = event.n_ok
            case SavedEvent():
                self.out = event.out
