"""Vytáhne texty z volání `tr(...)` a `N_(...)` a doplní překladové soubory.

    uv run python packaging/extract_strings.py            # doplní chybějící klíče
    uv run python packaging/extract_strings.py --check    # jen ohlásí chybějící

Klíč je český zdrojový text. Nové klíče dostanou prázdný překlad, který
test `tests/test_i18n.py` hlásí jako chybu; klíče, které v kódu už nejsou,
se smažou.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "speechscope_app"
I18N = SRC / "assets" / "i18n"
MARKERS = {"tr", "N_"}


def collect(src: Path = SRC) -> dict[str, list[str]]:
    """Text → soubory, kde se používá."""
    found: dict[str, list[str]] = {}
    for path in sorted(src.rglob("*.py")):
        if "fake" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            )
            if name not in MARKERS or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.strip():
                found.setdefault(arg.value, []).append(path.relative_to(ROOT).as_posix())
    return found


def sync(check_only: bool) -> int:
    keys = collect()
    missing_total = 0
    for path in sorted(I18N.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        merged = {k: data.get(k, "") for k in sorted(keys)}
        missing = [k for k, v in merged.items() if not v]
        stale = sorted(set(data) - set(keys))
        missing_total += len(missing)
        print(f"{path.name}: {len(keys)} textů, {len(missing)} bez překladu, {len(stale)} zbylých")
        for k in missing[:20]:
            print("   chybí:", k)
        if not check_only:
            path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    return 1 if check_only and missing_total else 0


if __name__ == "__main__":
    raise SystemExit(sync("--check" in sys.argv[1:]))
