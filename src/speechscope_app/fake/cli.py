"""Falešné `speechscope` CLI. Viz `__init__.py` pro pravidla."""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import os
import sys
import time
from importlib import resources
from pathlib import Path
from typing import Any

FIXTURES = resources.files("speechscope_app.fake") / "fixtures"
AUDIO_SUFFIXES = (".wav", ".flac", ".ogg", ".mp3", ".m4a")
TASKS = ("phonation", "ddk", "story", "monologue", "reading")
PROTOCOL_VERSION = 2
FAKE_VERSION = "0.2.0"

PROVIDERS = ("nlp", "phonemes", "segments", "transcript")


class Fail(Exception):
    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _lang() -> str:
    return os.environ.get("SPEECHSCOPE_LANG", "cs") or "cs"


def _localized(name: str) -> str:
    """`list.json` → `list.en.json`, když je jazyk angličtina a soubor existuje."""
    lang = _lang()
    if lang != "cs":
        stem, dot, ext = name.rpartition(".")
        candidate = f"{stem}.{lang}{dot}{ext}"
        if (FIXTURES / candidate).is_file():
            return candidate
    return name


def _features() -> list[dict[str, Any]]:
    return _load(_localized("list.json"))


def _params_of(name: str) -> dict[str, Any] | None:
    """Parametry feature nebo providera, jako `list --params NAME --json`."""
    path = FIXTURES / _localized(f"params/{name}.json")
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    spec = next((f for f in _features() if f["name"] == name), None)
    if spec is None:
        return None
    # Skutečná knihovna vrací pro feature bez parametrů prázdný slovník.
    return {
        "kind": "feature",
        "name": spec["name"],
        "version": spec["version"],
        "tasks": spec["tasks"],
        "requires": spec["requires"],
        "vad": {
            "supported": False,
            "required": False,
            "default": dict.fromkeys(spec["tasks"], False),
        },
        "outputs": spec["outputs"],
        "params": {},
    }


def _delay() -> float:
    return float(os.environ.get("SPEECHSCOPE_FAKE_DELAY", "0.2"))


def _log(msg: str, log_file: Path | None, level: str = "INFO") -> None:
    """Jako skutečná knihovna: s `--log-file` jen do souboru, jinak na stderr."""
    line = f"{level} fake: {msg}"
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
    else:
        print(line, file=sys.stderr, flush=True)


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# --- hledání nahrávek ---------------------------------------------------------


def _discover(paths: list[str], recursive: bool) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES:
            found.append(p)
        elif p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            found.extend(
                q for q in sorted(it) if q.is_file() and q.suffix.lower() in AUDIO_SUFFIXES
            )
        else:
            raise Fail(f"cesta neexistuje: {p}")
    if not found:
        raise Fail(f"nenalezeny žádné nahrávky ({', '.join(AUDIO_SUFFIXES)})")
    return found


def _read_manifest(path: Path) -> list[tuple[Path, str, dict[str, str]]]:
    if not path.is_file():
        raise Fail(f"manifest neexistuje: {path}")
    rows: list[tuple[Path, str, dict[str, str]]] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if (
            not reader.fieldnames
            or "path" not in reader.fieldnames
            or "task" not in reader.fieldnames
        ):
            raise Fail("manifest musí mít sloupce path a task")
        for row in reader:
            task = row["task"]
            if task not in TASKS:
                raise Fail(f"neznámá úloha {task!r} v manifestu")
            meta = {k: v for k, v in row.items() if k not in ("path", "task")}
            rows.append((path.parent / row["path"], task, meta))
    return rows


# --- výběr feature ----------------------------------------------------------


def _select(task: str, domain: str | None, patterns: list[str]) -> list[dict[str, Any]]:
    if task not in TASKS:
        raise Fail(f"neznámá úloha {task!r}, možnosti: {', '.join(TASKS)}")
    pool = [f for f in _features() if task in f["tasks"]]
    if domain:
        if domain not in ("acoustic", "linguistic"):
            raise Fail(f"neznámá doména {domain!r}")
        pool = [f for f in pool if f["name"].startswith(domain + ".")]
    if not patterns:
        return pool
    chosen: list[dict[str, Any]] = []
    for pat in patterns:
        hits = [f for f in pool if fnmatch.fnmatchcase(f["name"], pat)]
        if not hits:
            known = next((f for f in _features() if f["name"] == pat), None)
            if known is not None:
                raise Fail(f"feature {pat!r} nedává smysl pro úlohu {task!r}")
            raise Fail(f"vzoru {pat!r} neodpovídá žádná feature")
        for f in hits:
            if f not in chosen:
                chosen.append(f)
    return chosen


def _validate_sets(items: list[str]) -> None:
    names = {f["name"] for f in _features()}
    for item in items:
        if "=" not in item:
            raise Fail(f"--set čeká NAME.PARAM=VALUE, dostal {item!r}")
        key, _ = item.split("=", 1)
        owner, _, param = key.rpartition(".")
        if owner not in names and owner not in PROVIDERS:
            raise Fail(f"neznámá feature nebo provider {owner!r}")
        spec = _params_of(owner) or {}
        if param not in spec.get("params", {}) and param != "use_vad":
            raise Fail(f"{owner!r} nemá parametr {param!r}")


def _validate_config(path: str | None) -> None:
    if path and not Path(path).is_file():
        raise Fail(f"konfigurace neexistuje: {path}")


# --- příkazy ----------------------------------------------------------------


def cmd_version(_: argparse.Namespace) -> int:
    print(os.environ.get("SPEECHSCOPE_FAKE_VERSION", FAKE_VERSION))
    return 0


def cmd_list(ns: argparse.Namespace) -> int:
    if ns.params:
        payload = _params_of(ns.params)
        if payload is None:
            raise Fail(f"neznámá feature ani provider {ns.params!r}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if ns.providers:
        payload = _load(_localized("providers.json"))
        if ns.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for item in payload:
                print(f"{item['name']:12} {', '.join(item['params']) or '-'}")
        return 0
    specs = _features()
    if ns.task:
        if ns.task not in TASKS:
            raise Fail(f"neznámá úloha {ns.task!r}")
        specs = [f for f in specs if ns.task in f["tasks"]]
    if ns.domain:
        specs = [f for f in specs if f["name"].startswith(ns.domain + ".")]
    if ns.json:
        print(json.dumps(specs, ensure_ascii=False, indent=2))
    else:
        for f in specs:
            print(f"{f['name']:40} {', '.join(f['tasks'])}")
    return 0


def _models_dir(ns: argparse.Namespace) -> str:
    return ns.models_dir or os.environ.get("SPEECHSCOPE_MODELS") or str(Path("models").resolve())


def cmd_doctor(ns: argparse.Namespace) -> int:
    info = _load("doctor.json")
    root = _models_dir(ns)
    info["models_dir"] = root
    info["models_dir_exists"] = Path(root).is_dir()
    info["models_dir_source"] = "--models-dir" if ns.models_dir else "aktuální adresář"
    for m in info["models"].values():
        m["path"] = str(Path(root) / Path(m["path"]).name)
    if os.environ.get("SPEECHSCOPE_FAKE_DOCTOR") == "missing":
        for key in ("transcript", "nlp"):
            info["providers"][key]["ready"] = False
        info["models"]["whisper"]["present"] = False
        info["models"]["stanza"]["present"] = False
        info["all_ready"] = False
    if ns.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        for name, p in info["providers"].items():
            print(f"{name:12} {'připraven' if p['ready'] else 'chybí: ' + p['needs']}")
    return 0 if info["all_ready"] else 1


def cmd_models_list(ns: argparse.Namespace) -> int:
    info = _load("models.json")
    root = _models_dir(ns)
    info["dir"] = root
    for m in info["models"]:
        m["path"] = str(Path(root) / Path(m["path"]).name)
        if os.environ.get("SPEECHSCOPE_FAKE_DOCTOR") == "missing" and m["key"] in (
            "whisper",
            "stanza",
        ):
            m["present"] = False
    if ns.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        for m in info["models"]:
            print(f"{m['name']:40} {'je' if m['present'] else 'chybí'} {m['size_mb']} MB")
    return 0


def cmd_models_download(ns: argparse.Namespace) -> int:
    only = ns.only.split(",") if ns.only else ["whisper", "wavlm", "pyannote", "stanza", "onnx"]
    for key in only:
        for pct in (0, 50, 100):
            _log(f"stahuji {key}: {pct} %", None)
            time.sleep(_delay() / 3)
    _log(f"modely jsou v {_models_dir(ns)}", None)
    return 0


def cmd_models_unpack(ns: argparse.Namespace) -> int:
    """Jako skutečné `models unpack`: zip s manifestem projde, cokoli jiného kód 2."""
    import zipfile

    archive = Path(ns.archive)
    if not archive.is_file():
        print(f"soubor {archive} neexistuje", file=sys.stderr)
        return 2
    try:
        with zipfile.ZipFile(archive) as zf:
            manifest = json.loads(zf.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError):
        print(f"{archive.name} není balík modelů SpeechScope (chybí manifest)", file=sys.stderr)
        return 2
    if manifest.get("format") != "speechscope-models":
        print(f"{archive.name} není balík modelů SpeechScope", file=sys.stderr)
        return 2
    keys = ns.only.split(",") if ns.only else list(manifest.get("models", {}))
    _log(f"rozbaluji {archive.name} do {_models_dir(ns)}", None)
    for key in keys:
        for pct in (25, 50, 75, 100):
            _log(f"{key}: rozbaleno {pct} %", None)
            time.sleep(_delay() / 4)
    return 0


def _value(name: str, column: str) -> float:
    digest = hashlib.md5(f"{name}:{column}".encode()).hexdigest()
    return round(int(digest[:8], 16) / 0xFFFFFFFF * 100, 4)


def cmd_extract(ns: argparse.Namespace) -> int:
    log_file = Path(ns.log_file) if ns.log_file else None
    _validate_config(ns.config)
    _validate_sets(ns.set or [])
    patterns = [p.strip() for p in (ns.features or "").split(",") if p.strip()]

    if ns.manifest:
        items = _read_manifest(Path(ns.manifest))
    else:
        if not ns.task:
            raise Fail("zadej --task, nebo --manifest")
        items = [(p, ns.task, {}) for p in _discover(ns.paths, ns.recursive)]

    plans = {t: _select(t, ns.domain, patterns) for t in sorted({t for _, t, _ in items})}
    features = sorted({f["name"] for specs in plans.values() for f in specs})
    providers = sorted({r for specs in plans.values() for f in specs for r in f["requires"]})
    if ns.vad is True and any(t != "phonation" for t in plans) and "segments" not in providers:
        providers.append("segments")
    columns: list[str] = []
    for specs in plans.values():
        for f in specs:
            columns.extend(c for c in f["columns"] if c not in columns)
    meta_cols: list[str] = []
    for _, _, meta in items:
        meta_cols.extend(k for k in meta if k not in meta_cols)

    _log(f"vybráno {len(features)} feature, providery: {', '.join(providers) or '-'}", log_file)
    out = Path(ns.out) if ns.out else Path("out.csv")
    _log(f"modely: {_models_dir(ns)}", log_file)
    if ns.progress_json:
        _emit({
            "event": "start", "protocol": PROTOCOL_VERSION, "total": len(items),
            "task": ns.task, "features": features, "providers": providers,
        })  # fmt: skip

    rows: list[dict[str, Any]] = []
    n_ok = 0
    work_dir = Path(ns.work_dir) if ns.work_dir else None
    for index, (path, task, meta) in enumerate(items):
        if ns.progress_json:
            _emit({"event": "begin", "index": index, "path": str(path)})
        row: dict[str, Any] = {"file": path.name, "path": str(path), "task": task}
        row.update(meta)
        row["speechscope_version"] = FAKE_VERSION
        stem = path.stem.lower()
        if "bad" in stem:
            msg = "soubor nejde načíst: falešná chyba"
            row["error"] = msg
            _log(f"{path.name}: {msg}", log_file, "ERROR")
            if ns.progress_json:
                _emit(
                    {
                        "event": "file",
                        "index": index,
                        "path": str(path),
                        "status": "error",
                        "msg": msg,
                    }
                )
        else:
            _fake_stages(index, path, providers, work_dir, ns.progress_json)
            for col in columns:
                row[col] = _value(path.name, col)
            if "short" in stem:
                first = features[0] if features else "acoustic.pitch.f0"
                row["notes"] = f"{first}=příliš krátký úsek řeči"
                for col in columns:
                    if col.startswith(first):
                        row[col] = "nan"
                _log(f"{path.name}: {row['notes']}", log_file, "WARNING")
            n_ok += 1
        rows.append(row)
        # jako knihovna: průběžný zápis před událostí file, přes .part
        _write_rows(rows, columns, meta_cols, out, partial=True)
        if ns.progress_json and "bad" not in stem:
            _emit({"event": "file", "index": index, "path": str(path), "status": "ok"})

    if ns.progress_json:
        _emit({"event": "done", "n_ok": n_ok})

    _write_rows(rows, columns, meta_cols, out, partial=False)
    if ns.progress_json:
        _emit({"event": "saved", "out": str(out)})
    else:
        _log(f"zapsáno {out}", log_file)
    return 0


def _write_rows(
    rows: list[dict[str, Any]],
    columns: list[str],
    meta_cols: list[str],
    out: Path,
    *,
    partial: bool,
) -> None:
    """Zápis tabulky; průběžný nese notes i error vždy, konečný jen když jsou."""
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["file", "path", "task", *meta_cols, "speechscope_version", *columns]
    if partial or any("notes" in r for r in rows):
        fieldnames.append("notes")
    if partial or any("error" in r for r in rows):
        fieldnames.append("error")
    tmp = out.with_name(out.name + ".part") if partial else out
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    if partial:
        os.replace(tmp, out)


def _fake_stages(
    index: int, path: Path, providers: list[str], work_dir: Path | None, progress: bool
) -> None:
    """Předstírá běh providerů nad nahrávkou: `stage` running a pak done.

    Provider, jehož mezivýsledek už leží v pracovní složce (od `segment`
    nebo `transcribe`), skončí jako `cached`. Doba běhu na nahrávku je
    `SPEECHSCOPE_FAKE_DELAY`, rozdělená mezi providery; bez providerů se
    prostě čeká.
    """
    if not providers:
        time.sleep(_delay())
        return
    per_provider = _delay() / len(providers)
    for provider in providers:
        if progress:
            _emit({"event": "stage", "index": index, "provider": provider, "status": "running"})
        sub = {"segments": "segments", "transcript": "transcript"}.get(provider)
        cached = bool(work_dir and sub and (work_dir / sub / f"{path.stem}.txt").is_file())
        took = per_provider / 10 if cached else per_provider
        time.sleep(took)
        if progress:
            _emit({
                "event": "stage", "index": index, "provider": provider,
                "status": "cached" if cached else "done", "seconds": round(took, 3),
            })  # fmt: skip


def _cmd_prepare(ns: argparse.Namespace, provider: str) -> int:
    log_file = Path(ns.log_file) if ns.log_file else None
    _validate_config(ns.config)
    _validate_sets(ns.set or [])
    if ns.manifest:
        items = [p for p, _, _ in _read_manifest(Path(ns.manifest))]
    else:
        items = _discover(ns.paths, ns.recursive)
    root = Path(ns.work_dir) if ns.work_dir else Path("work")
    sub = root / ("segments" if provider == "segments" else "transcript")
    sub.mkdir(parents=True, exist_ok=True)
    detail = f"model {ns.model}" if provider == "segments" else f"jazyk {ns.language}, {ns.model}"
    _log(f"{provider}: {detail}", log_file)

    if ns.progress_json:
        _emit({
            "event": "start", "protocol": PROTOCOL_VERSION, "total": len(items),
            "task": None, "features": [], "providers": [provider],
        })  # fmt: skip
    n_ok = 0
    for index, path in enumerate(items):
        if ns.progress_json:
            _emit({"event": "begin", "index": index, "path": str(path)})
        if "bad" in path.stem.lower():
            msg = "soubor nejde načíst: falešná chyba"
            if ns.progress_json:
                _emit(
                    {
                        "event": "file",
                        "index": index,
                        "path": str(path),
                        "status": "error",
                        "msg": msg,
                    }
                )
            continue
        _fake_stages(index, path, [provider], None, ns.progress_json)
        if provider == "segments":
            (sub / f"{path.stem}.txt").write_text(
                "0.000\t1.200\tsv\n1.200\t1.500\tps\n1.500\t3.000\tsu\n", encoding="utf-8"
            )
        else:
            (sub / f"{path.stem}.txt").write_text("falešný přepis nahrávky", encoding="utf-8")
        (sub / f"{path.stem}.json").write_text(
            json.dumps({"provider": provider, "fake": True}), encoding="utf-8"
        )
        n_ok += 1
        if ns.progress_json:
            _emit({"event": "file", "index": index, "path": str(path), "status": "ok"})
    if ns.progress_json:
        _emit({"event": "done", "n_ok": n_ok})
        _emit({"event": "saved", "out": str(root)})
    else:
        _log(f"mezivýsledky jsou v {root}", log_file)
    return 0


# --- parser -------------------------------------------------------------------


def _add_batch_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("paths", nargs="*")
    p.add_argument("--manifest")
    p.add_argument("--config")
    p.add_argument("--set", action="append")
    p.add_argument("--work-dir")
    rec = p.add_mutually_exclusive_group()
    rec.add_argument("--recursive", dest="recursive", action="store_true", default=True)
    rec.add_argument("--no-recursive", dest="recursive", action="store_false")
    p.add_argument("--progress-json", action="store_true")
    p.add_argument("--log-file")
    p.add_argument("--log-level", default="INFO")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="speechscope", description="Falešná knihovna SpeechScope")
    parser.add_argument("--models-dir")
    parser.add_argument("--lang")  # jako knihovna: nastaví SPEECHSCOPE_LANG pro popisy
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version").set_defaults(func=cmd_version)

    p = sub.add_parser("list")
    p.add_argument("--task")
    p.add_argument("--domain")
    p.add_argument("--params")
    p.add_argument("--providers", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("doctor")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    models = sub.add_parser("models").add_subparsers(dest="models_cmd", required=True)
    p = models.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_models_list)
    p = models.add_parser("download")
    p.add_argument("--only")
    p.add_argument("--log-level", default="INFO")
    p.set_defaults(func=cmd_models_download)
    p = models.add_parser("unpack")
    p.add_argument("archive")
    p.add_argument("--only")
    p.add_argument("--log-level", default="INFO")
    p.set_defaults(func=cmd_models_unpack)

    p = sub.add_parser("extract")
    _add_batch_options(p)
    p.add_argument("--task")
    p.add_argument("--features")
    p.add_argument("--domain")
    vad = p.add_mutually_exclusive_group()
    vad.add_argument("--vad", dest="vad", action="store_true", default=None)
    vad.add_argument("--no-vad", dest="vad", action="store_false")
    p.add_argument("--out")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("segment")
    _add_batch_options(p)
    p.add_argument("--model", default="auto")
    p.add_argument("--cut-audio", action="store_true")
    p.set_defaults(func=lambda ns: _cmd_prepare(ns, "segments"))

    p = sub.add_parser("transcribe")
    _add_batch_options(p)
    p.add_argument("--language", default="cs")
    p.add_argument("--model", default="large-v3")
    p.set_defaults(func=lambda ns: _cmd_prepare(ns, "transcript"))

    return parser


def main(argv: list[str] | None = None) -> int:
    # Skutečná knihovna bez PYTHONUTF8 na Windows padá; falešná ne, ať se
    # testy netočí kolem konzole. GUI proměnnou nastavuje vždycky.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ns = build_parser().parse_args(argv)
    if ns.lang:
        os.environ["SPEECHSCOPE_LANG"] = ns.lang
    try:
        return int(ns.func(ns))
    except Fail as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
