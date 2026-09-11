"""Překlady textů GUI.

Zdrojové texty v kódu jsou české a zároveň slouží jako klíče. Překlady
leží v `assets/i18n/<jazyk>.json` jako slovník český text → překlad.
Nový jazyk = nový soubor; klíče vyrobí `packaging/extract_strings.py`
z volání `tr(...)` a `N_(...)` v kódu.

`tr` překládá hned, `N_` jen označí text pro extrakci (tabulky v
`contract.py`, které se překládají až při zobrazení). Modul na Qt
nezávisí, ať jde použít i v backendu.
"""

from __future__ import annotations

import json
import locale
from importlib import resources

SOURCE_LANGUAGE = "cs"
LANGUAGE_NAMES: dict[str, str] = {
    "cs": "čeština",
    "en": "English",
    "de": "Deutsch",
    "sk": "slovenčina",
}

_current = SOURCE_LANGUAGE
_table: dict[str, str] = {}


def available() -> list[str]:
    """Jazyky, pro které je překlad, čeština první."""
    out = [SOURCE_LANGUAGE]
    root = resources.files("speechscope_app") / "assets" / "i18n"
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".json"):
            code = entry.name[:-5]
            if code not in out:
                out.append(code)
    return out


def system_language() -> str:
    """Jazyk systému, když je pro něj překlad, jinak čeština."""
    try:
        code = (locale.getlocale()[0] or "").split("_")[0].lower()
    except ValueError:
        code = ""
    return code if code in available() else SOURCE_LANGUAGE


def activate(language: str | None) -> str:
    """Zapne jazyk (`""`/None = podle systému). Vrací, co se zapnulo."""
    global _current, _table
    code = language or system_language()
    if code == SOURCE_LANGUAGE or code not in available():
        _current, _table = SOURCE_LANGUAGE, {}
        return _current
    path = resources.files("speechscope_app") / "assets" / "i18n" / f"{code}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    _table = {k: v for k, v in data.items() if isinstance(v, str) and v}
    _current = code
    return _current


def language() -> str:
    return _current


def tr(text: str) -> str:
    """Překlad textu; bez překladu vrátí zdrojový český text."""
    if _current == SOURCE_LANGUAGE:
        return text
    return _table.get(text, text)


def N_(text: str) -> str:  # noqa: N802
    """Označí text pro extrakci; překlad udělá až `tr` při zobrazení."""
    return text


class Labels(dict):
    """Slovník kód → český popisek, který při čtení překládá.

    Hodnoty se v kódu píší jako `N_("…")`, aby je extrakce našla; `[]`,
    `get`, `values` a `items` vrací přeložený text pro aktuální jazyk.
    """

    def __getitem__(self, key):  # noqa: ANN001, ANN204
        return tr(super().__getitem__(key))

    def get(self, key, default=None):  # noqa: ANN001, ANN201
        if key in self:
            return self[key]
        return tr(default) if isinstance(default, str) else default

    def values(self):  # noqa: ANN201
        return [tr(v) for v in super().values()]

    def items(self):  # noqa: ANN201
        return [(k, tr(v)) for k, v in super().items()]
