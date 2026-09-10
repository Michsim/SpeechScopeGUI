# SpeechScope App

Desktopová aplikace (PySide6) nad knihovnou [SpeechScope](docs/speechscope-library/README.md).
Klinik vybere složku s nahrávkami a protokol, aplikace na pozadí spustí
knihovnu a ukáže výsledky. Výzkumník si v rozšířeném režimu vybere feature
a parametry a uloží je jako nový protokol.

Aplikace knihovnu **neimportuje**. Volá její CLI jako podproces a rozumí
jen jeho JSON výstupům (`--progress-json`, `list --json`, `doctor --json`).
Díky tomu pád modelu nesestřelí okno a knihovna i GUI se aktualizují
nezávisle.

## Vývoj

Potřebuješ [uv](https://docs.astral.sh/uv/) a Python 3.11 až 3.13.

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
```

Spuštění bez knihovny a bez modelů, proti falešné knihovně:

```bash
uv run speechscope-app --fake
```

Falešná knihovna (`speechscope-fake`, modul `speechscope_app.fake`) přijímá
stejné příkazy jako skutečné CLI a vrací zachycené výstupy skutečné
knihovny. Nahrávka se jménem obsahujícím `bad` skončí chybou, jméno
se `short` dostane poznámku. Rychlost řídí `SPEECHSCOPE_FAKE_DELAY`.

Proti skutečné knihovně: v Nastavení ukaž na `speechscope.exe`
z jejího prostředí (typicky `..\SpeechScope\SpeechScope\.venv\Scripts\speechscope.exe`),
nastav složku s modely a vypni falešnou knihovnu.

## Rozložení

```
src/speechscope_app/
    contract.py         # co GUI o knihovně ví napevno: úlohy, události, verze smlouvy
    backend/
        command.py      # skládání argumentů CLI, čisté funkce
        library.py      # synchronní dotazy: version, list, doctor, models
        runner.py       # dlouhé běhy přes QProcess, události, log, zrušení
        settings.py     # QSettings: cesta ke knihovně, modely, pracovní složka
        protocol.py     # protokol = uložené nastavení dávky (YAML)
        discover.py     # nalezení nahrávek a ručních vstupů vedle nich
    ui/
        main_window.py  # boční menu, skládání běhu z protokolu
        pages/          # Prostředí, Data, Běh, Výsledky
        widgets/        # formulář parametrů z deklarace Param
    protocols/          # protokoly přibalené k aplikaci
    fake/               # falešná knihovna a zachycené fixtury
tests/
docs/speechscope-library/   # dokumentace knihovny, jen ke čtení
packaging/                  # plán sestavení instalátoru
```

## Kam jdou soubory

Do složky s nahrávkami se nikdy nezapisuje. Každý běh dostane vlastní
složku `Dokumenty\SpeechScope\<datum>_<protokol>\` s `features.csv`,
`protocol.yaml` (co přesně se spustilo), `config.yaml` (parametry pro
`--config`) a `speechscope.log`. Mezivýsledky (segmentace, přepisy) sdílí
všechny běhy ve `Dokumenty\SpeechScope\work\`.

## Balení

Viz [packaging/README.md](packaging/README.md).
