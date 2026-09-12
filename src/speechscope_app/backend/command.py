"""Skládání argumentů pro CLI knihovny.

Čisté funkce bez vedlejších efektů, ať se dají testovat bez Qt i bez
knihovny. Vrací argumenty *za* spustitelným souborem; ten přidává
`Library.argv()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _common(models_dir: Path | None, lang: str | None = None) -> list[str]:
    """Globální volby, které jdou před jméno příkazu.

    `--models-dir` se posílá vždycky, když ho známe. Bez něj knihovna hledá
    modely v aktuálním adresáři, což u GUI spuštěného odkudkoli selže.
    `--lang` je jazyk popisů feature a parametrů (`list`), jinak knihovna
    vrací češtinu.
    """
    args = ["--models-dir", str(models_dir)] if models_dir else []
    if lang:
        args += ["--lang", lang]
    return args


def _paths(paths: list[Path]) -> list[str]:
    if not paths:
        raise ValueError("dávka nemá žádný vstup")
    return [str(p) for p in paths]


@dataclass(slots=True)
class ExtractRequest:
    inputs: list[Path]
    task: str | None = None
    features: list[str] = field(default_factory=list)
    domain: str | None = None
    manifest: Path | None = None
    config: Path | None = None
    sets: list[str] = field(default_factory=list)
    vad: bool | None = None
    out: Path | None = None
    work_dir: Path | None = None
    recursive: bool = True
    log_file: Path | None = None
    log_level: str = "INFO"

    def validate(self) -> None:
        if self.task is None and self.manifest is None:
            raise ValueError("zadej úlohu, nebo manifest")


def extract_args(
    req: ExtractRequest, *, models_dir: Path | None = None, lang: str | None = None
) -> list[str]:
    req.validate()
    args = [*_common(models_dir, lang), "extract", *_paths(req.inputs)]
    if req.task:
        args += ["--task", req.task]
    if req.features:
        args += ["--features", ",".join(req.features)]
    if req.domain:
        args += ["--domain", req.domain]
    if req.manifest:
        args += ["--manifest", str(req.manifest)]
    if req.config:
        args += ["--config", str(req.config)]
    for item in req.sets:
        args += ["--set", item]
    if req.vad is True:
        args.append("--vad")
    elif req.vad is False:
        args.append("--no-vad")
    if req.out:
        args += ["--out", str(req.out)]
    if req.work_dir:
        args += ["--work-dir", str(req.work_dir)]
    args.append("--recursive" if req.recursive else "--no-recursive")
    args.append("--progress-json")
    if req.log_file:
        args += ["--log-file", str(req.log_file)]
    args += ["--log-level", req.log_level]
    return args


@dataclass(slots=True)
class PrepareRequest:
    """Společný tvar pro `segment` a `transcribe`."""

    inputs: list[Path]
    work_dir: Path | None = None
    manifest: Path | None = None
    config: Path | None = None
    sets: list[str] = field(default_factory=list)
    recursive: bool = True
    log_file: Path | None = None
    log_level: str = "INFO"


def _prepare_tail(req: PrepareRequest) -> list[str]:
    args: list[str] = []
    if req.work_dir:
        args += ["--work-dir", str(req.work_dir)]
    if req.manifest:
        args += ["--manifest", str(req.manifest)]
    if req.config:
        args += ["--config", str(req.config)]
    for item in req.sets:
        args += ["--set", item]
    args.append("--recursive" if req.recursive else "--no-recursive")
    args.append("--progress-json")
    if req.log_file:
        args += ["--log-file", str(req.log_file)]
    args += ["--log-level", req.log_level]
    return args


def segment_args(
    req: PrepareRequest,
    *,
    model: str = "conformer",
    cut_audio: bool = False,
    models_dir: Path | None = None,
    lang: str | None = None,
) -> list[str]:
    args = [*_common(models_dir, lang), "segment", *_paths(req.inputs), "--model", model]
    if cut_audio:
        args.append("--cut-audio")
    return args + _prepare_tail(req)


def transcribe_args(
    req: PrepareRequest,
    *,
    language: str = "cs",
    model: str = "large-v3",
    models_dir: Path | None = None,
    lang: str | None = None,
) -> list[str]:
    args = [
        *_common(models_dir, lang),
        "transcribe",
        *_paths(req.inputs),
        "--language",
        language,
        "--model",
        model,
    ]
    return args + _prepare_tail(req)


def list_args(
    *,
    task: str | None = None,
    domain: str | None = None,
    models_dir: Path | None = None,
    lang: str | None = None,
) -> list[str]:
    args = [*_common(models_dir, lang), "list"]
    if task:
        args += ["--task", task]
    if domain:
        args += ["--domain", domain]
    return args + ["--json"]


def params_args(name: str, *, models_dir: Path | None = None, lang: str | None = None) -> list[str]:
    """Parametry jedné feature nebo providera (`segments`, `transcript`, ...)."""
    return [*_common(models_dir, lang), "list", "--params", name, "--json"]


def providers_args(*, models_dir: Path | None = None, lang: str | None = None) -> list[str]:
    return [*_common(models_dir, lang), "list", "--providers", "--json"]


def doctor_args(*, models_dir: Path | None = None, lang: str | None = None) -> list[str]:
    return [*_common(models_dir, lang), "doctor", "--json"]


def models_list_args(*, models_dir: Path | None = None, lang: str | None = None) -> list[str]:
    return [*_common(models_dir, lang), "models", "list", "--json"]


def models_download_args(
    *,
    only: list[str] | None = None,
    models_dir: Path | None = None,
    lang: str | None = None,
) -> list[str]:
    args = [*_common(models_dir, lang), "models", "download"]
    if only:
        args += ["--only", ",".join(only)]
    return args


def models_unpack_args(
    archive: Path, *, models_dir: Path | None = None, lang: str | None = None
) -> list[str]:
    """Instalace modelů z balíku (`models pack`), ověřuje se otisk každého souboru."""
    return [*_common(models_dir, lang), "models", "unpack", str(archive)]


def version_args() -> list[str]:
    return ["version"]
