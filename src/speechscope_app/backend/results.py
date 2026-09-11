"""Práce s tabulkou výsledků mimo GUI: řádky s chybou a jejich nahrazení.

„Spočítat znovu chybné“ pustí knihovnu jen nad nahrávkami, které v tabulce
skončily s chybou, do nové složky běhu, a výsledek pak vloží zpátky do
původní tabulky místo chybných řádků. Nová složka zůstane v historii.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def failed_paths(frame: pd.DataFrame) -> list[Path]:
    """Cesty nahrávek, jejichž řádek má neprázdný `error`."""
    if "error" not in frame.columns or "path" not in frame.columns:
        return []
    mask = frame["error"].notna() & (frame["error"].astype(str).str.strip() != "")
    return [Path(str(p)) for p in frame.loc[mask, "path"]]


def merge_results(target: Path, source: Path) -> int:
    """Řádky ze `source` nahradí řádky stejné nahrávky v `target`; vrací počet.

    Klíč je `path`, náhradně `file`. Pořadí řádků v cílové tabulce zůstane,
    nové sloupce se připojí na konec. Nahrávky, které v cíli nejsou, se
    přidají na konec.
    """
    old = pd.read_csv(target)
    new = pd.read_csv(source)
    key = "path" if "path" in old.columns and "path" in new.columns else "file"
    if key not in old.columns or key not in new.columns or new.empty:
        return 0
    replaced = 0
    for column in new.columns:
        if column not in old.columns:
            old[column] = pd.NA
    for column in old.columns:
        if column not in new.columns:
            new[column] = pd.NA
    new = new[old.columns]
    by_key = {str(k): i for i, k in enumerate(new[key].astype(str))}
    for i, k in enumerate(old[key].astype(str)):
        j = by_key.pop(k, None)
        if j is None:
            continue
        old.iloc[i] = new.iloc[j]
        replaced += 1
    if by_key:
        extra = new.iloc[sorted(by_key.values())]
        old = pd.concat([old, extra], ignore_index=True)
        replaced += len(extra)
    # jako knihovna: notes a error jen když v nich něco je
    for column in ("notes", "error"):
        if column in old.columns and old[column].isna().all():
            old = old.drop(columns=[column])
    old.to_csv(target, index=False, encoding="utf-8")
    return replaced
