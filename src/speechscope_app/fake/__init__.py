"""Falešná knihovna SpeechScope pro vývoj a testy GUI bez modelů.

Přijímá stejné příkazy a volby jako skutečné CLI a vrací JSON ve stejném
tvaru, včetně událostí `--progress-json` smlouvy verze 2 (`begin`,
`stage`). Fixtury v `fixtures/` jsou zachycené výstupy skutečné knihovny
(verze 0.1.0, 10. 9. 2026). Když se knihovna změní, zachytí se znovu:

    uv run speechscope list --json                > fixtures/list.json
    uv run speechscope list --providers --json    > fixtures/providers.json
    uv run speechscope doctor --json              > fixtures/doctor.json
    uv run speechscope models list --json         > fixtures/models.json
    uv run speechscope list --params NAME --json  > fixtures/params/NAME.json

`NAME` je feature i provider (`segments`, `transcript`, `nlp`, `phonemes`).
Feature bez vlastního souboru v `params/` dostane prázdné parametry.

Chování se řídí proměnnými prostředí:

    SPEECHSCOPE_FAKE_DELAY    sekund na nahrávku (výchozí 0.2)
    SPEECHSCOPE_FAKE_DOCTOR   "missing" -> transcript a nlp nepřipravené, kód 1
    SPEECHSCOPE_FAKE_VERSION  co vrátí `version` (výchozí z fixtur)

Nahrávka se jménem obsahujícím `bad` skončí chybou načtení, jméno
s `short` dostane poznámku v `notes`.
"""
