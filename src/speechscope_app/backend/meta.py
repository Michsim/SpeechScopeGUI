"""Vyhrazené sloupce metadat: pacient, návštěva, věk, pohlaví, skupina.

Manifest má libovolné sloupce a knihovna je jen propíše do tabulky. Pro
srovnání s normou a pro skládání vyšetření ale GUI potřebuje vědět, který
sloupec je pacient, věk a pohlaví. Klinik je píše různě (`id`, `pacient`,
`vek`…), proto se názvy převádějí na kanonické při čtení; hodnoty pohlaví
a věku se čistí tady, ne v každé obrazovce zvlášť.
"""

from __future__ import annotations

import datetime as dt
import math
import unicodedata

from ..i18n import N_, Labels

SUBJECT = "subject"
VISIT = "visit"
AGE = "age"
SEX = "sex"
GROUP = "group"
META_COLUMNS: tuple[str, ...] = (SUBJECT, VISIT, AGE, SEX, GROUP)

# Popisky vyhrazených sloupců pro UI (hlavičky, nabídka + Sloupec…); klíče zůstávají anglické.
LABELS = Labels(
    {
        SUBJECT: N_("pacient"),
        VISIT: N_("datum vyšetření"),
        AGE: N_("věk"),
        SEX: N_("pohlaví"),
        GROUP: N_("skupina"),
    }
)


def label(column: str) -> str:
    """Český (přeložený) popisek vyhrazeného sloupce; jiný sloupec vrátí svůj klíč."""
    return LABELS.get(column, column)


# Alias (bez diakritiky, malými písmeny) -> kanonický sloupec.
COLUMN_ALIASES: dict[str, str] = {
    "id": SUBJECT,
    "subject_id": SUBJECT,
    "pacient": SUBJECT,
    "patient": SUBJECT,
    "patient_id": SUBJECT,
    "speaker": SUBJECT,
    "speaker_id": SUBJECT,
    "osoba": SUBJECT,
    "navsteva": VISIT,
    "session": VISIT,
    "datum": VISIT,
    "date": VISIT,
    "vek": AGE,
    "age_years": AGE,
    "pohlavi": SEX,
    "gender": SEX,
    "skupina": GROUP,
    "diagnoza": GROUP,
    "diagnosis": GROUP,
}

FEMALE = "f"
MALE = "m"
_SEX_VALUES: dict[str, str] = {
    "f": FEMALE,
    "z": FEMALE,
    "zena": FEMALE,
    "female": FEMALE,
    "w": FEMALE,
    "woman": FEMALE,
    "m": MALE,
    "muz": MALE,
    "male": MALE,
    "man": MALE,
}


def _fold(text: str) -> str:
    """Malými písmeny, bez diakritiky, mezery a pomlčky jako podtržítko."""
    plain = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return plain.strip().lower().replace(" ", "_").replace("-", "_")


def canonical_column(name: str) -> str:
    """Kanonické jméno sloupce metadat; cizí sloupce zůstávají, jak jsou."""
    folded = _fold(name)
    if folded in META_COLUMNS:
        return folded
    return COLUMN_ALIASES.get(folded, name)


def parse_sex(value: object) -> str:
    """`f`, `m`, nebo prázdné, když hodnota není rozpoznatelná."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return _SEX_VALUES.get(_fold(str(value)), "")


def parse_age(value: object) -> float | None:
    """Věk v letech; bere i `64,5`; prázdné a nesmysly jako None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        age = float(text)
    except ValueError:
        return None
    return age if 0 <= age < 130 else None


def normalize_visit(text: object) -> str:
    """Datum vyšetření jako ISO (rok-měsíc-den) z ISO i z „14. 10. 2026“; jinak prázdné."""
    text = str(text or "").strip()
    if not text:
        return ""
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass
    parts = [p.strip() for p in text.replace("/", ".").split(".") if p.strip()]
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        try:
            return dt.date(int(parts[2]), int(parts[1]), int(parts[0])).isoformat()
        except ValueError:
            return ""
    return ""


def normalize_value(column: str, text: str) -> str:
    """Hodnota metadat jako v manifestu: věk číslo, pohlaví f/m, datum ISO; jinak text.

    Nerozpoznaná hodnota zůstane, jak ji uživatel napsal (uvidí ji v tabulce)."""
    text = text.strip()
    if not text:
        return ""
    if column == AGE:
        age = parse_age(text)
        return f"{age:g}" if age is not None else text
    if column == SEX:
        return parse_sex(text) or text
    if column == VISIT:
        return normalize_visit(text) or text
    return text
