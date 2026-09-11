"""Překlady: každý text z kódu má anglický překlad a přepnutí jazyka funguje."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from speechscope_app import contract, i18n
from speechscope_app.backend.protocol import Protocol, builtin_protocols

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
from extract_strings import collect  # noqa: E402

PLACEHOLDER = re.compile(r"\{[a-z_]+(?:![rs])?(?::[^}]*)?\}")


@pytest.fixture(autouse=True)
def czech_after_test():
    yield
    i18n.activate("cs")


def test_every_source_text_has_english() -> None:
    keys = collect()
    en = json.loads((ROOT / "src/speechscope_app/assets/i18n/en.json").read_text("utf-8"))
    missing = sorted(k for k in keys if not en.get(k))
    assert not missing, f"bez překladu: {missing[:10]}"
    stale = sorted(set(en) - set(keys))
    assert not stale, f"přebývá v en.json: {stale[:10]}"
    # zástupné symboly musí sedět, jinak .format() spadne až u uživatele
    for key, value in en.items():
        assert set(PLACEHOLDER.findall(key)) == set(PLACEHOLDER.findall(value)), key


def test_activate_and_labels() -> None:
    assert i18n.available()[:2] == ["cs", "en"]
    assert i18n.activate("cs") == "cs"
    assert i18n.tr("Nahrávky") == "Nahrávky"
    assert contract.TASK_LABELS["story"] == "Vyprávění pohádky"
    assert i18n.activate("en") == "en"
    assert i18n.tr("Nahrávky") == "Recordings"
    assert i18n.tr("text bez překladu") == "text bez překladu"
    assert contract.TASK_LABELS["story"] == "Story retelling"
    assert contract.TASK_LABELS.get("story") == "Story retelling"
    assert dict(contract.PROVIDER_SHORT.items())["nlp"] == "linguistics"
    assert contract.language_label("cs") == "Czech" and contract.language_label("xx") == "xx"
    assert i18n.activate("xx") == "cs"  # neznámý jazyk = čeština
    assert i18n.activate("") in i18n.available()


def test_protocol_translated_names_roundtrip(tmp_path: Path) -> None:
    protocols = {p.name: p for p in builtin_protocols()}
    story = protocols["Pohádka, akustika"]
    assert story.names["en"] == "Story, acoustics" and story.descriptions["en"]
    assert all(p.names.get("en") and p.descriptions.get("en") for p in protocols.values())
    assert story.display_name == "Pohádka, akustika"
    i18n.activate("en")
    assert story.display_name == "Story, acoustics"
    assert story.display_description.startswith("Acoustic features")
    assert story.slug() == "pohadka-akustika"  # klíč se nepřekládá

    copy = Protocol.from_dict(story.to_dict())
    assert copy.names == story.names and copy.descriptions == story.descriptions
    plain = Protocol(name="Moje", task="story")
    assert plain.display_name == "Moje" and "name_en" not in plain.to_dict()
    path = tmp_path / "p.yaml"
    story.save(path)
    assert "name_en: Story, acoustics" in path.read_text(encoding="utf-8")
