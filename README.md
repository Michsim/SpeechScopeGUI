# SpeechScope App

Desktopová aplikace (PySide6) nad knihovnou [SpeechScope](docs/speechscope-library/README.md).
Klinik vybere složku s nahrávkami, úlohu a protokol, aplikace na pozadí
spustí knihovnu a ukáže výsledky. Výzkumník si v rozšířeném režimu vybere
feature a parametry a uloží je jako nový protokol.

Aplikace knihovnu **neimportuje**. Volá její CLI jako podproces a rozumí
jen jeho JSON výstupům (`--progress-json`, `list --json`, `doctor --json`).
Díky tomu pád modelu nesestřelí okno a knihovna i GUI se aktualizují
nezávisle.

## Co aplikace umí

- **Uvítání** po každém startu: logo, doporučení, kam jít, a při prvním
  spuštění blok „První nastavení“ se složkou modelů (návrh vedle
  aplikace) a složkou výsledků, instalace modelů z balíku nebo stažení.
- **Analýza**: složka s nahrávkami (délka z hlavičky WAV a FLAC, metadata
  z `manifest.csv`), úloha, jazyk nahrávek, protokol. Odhad doby výpočtu
  podle minut zvuku z minulého běhu. Kontrola před spuštěním, že jsou
  připravené modely, které protokol potřebuje. Dvojklik nahrávku přehraje.
  V rozšířeném režimu výběr feature a parametrů v samostatném okně,
  uložení jako protokol, jen segmentace nebo jen přepis.
- **Výpočet**: tabulka nahrávek × providerů s průběhem, odhad zbývajícího
  času, Zrušit s potvrzením, fronta dalších dávek, historie běhů
  s přehráním průběhu ze záznamu.
- **Výsledky**: historie běhů, tabulka se zmrazeným sloupcem nahrávky,
  hledání sloupce, popis sloupce v tooltipu, detail jedné nahrávky po
  skupinách feature, Spočítat znovu chybné (řádky s chybou se přepočítají
  a vrátí do původní tabulky).
- **Protokoly**: přibalené ke čtení, kopie, vlastní s editorem, import,
  export.
- **Prostředí**: kontrola knihovny, modelů a grafické karty (včetně
  varování ve vzdálené ploše), složky modelů a výsledků, stažení modelů
  nebo instalace z balíku, úklid mezivýsledků, diagnostický zip pro
  podporu.
- Anglicky (výchozí) a česky, volba v Nastavení; popisy feature
  a parametrů jdou z knihovny ve zvoleném jazyce. Rozšířený režim pro
  výzkumníky.

## Vývoj

Potřebuješ [uv](https://docs.astral.sh/uv/) a Python 3.11 až 3.13.

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run python packaging/extract_strings.py --check   # překlady bez děr
```

Spuštění bez knihovny a bez modelů, proti falešné knihovně:

```bash
uv run speechscope-app --fake
```

Falešná knihovna (`speechscope-fake`, modul `speechscope_app.fake`) přijímá
stejné příkazy jako skutečné CLI a vrací zachycené výstupy skutečné
knihovny (po změně knihovny `uv run python packaging/capture_fixtures.py`). Nahrávka se jménem obsahujícím `bad` skončí chybou, jméno
se `short` dostane poznámku. Rychlost řídí `SPEECHSCOPE_FAKE_DELAY`,
`SPEECHSCOPE_FAKE_DOCTOR=missing` předstírá chybějící modely. Přepínač
`--fake` platí jen pro daný běh, do nastavení se neukládá.

Proti skutečné knihovně: v Nastavení ukaž na `speechscope.exe`
z jejího prostředí (typicky `..\SpeechScope\SpeechScope\.venv\Scripts\speechscope.exe`)
a na složku s modely. V repu knihovny vždy `uv sync --all-extras`.

Texty v GUI jsou české a zároveň klíče překladu; každý jde přes `tr()`
z `i18n.py`, překlady jsou v `assets/i18n/en.json`. Po přidání textu spusť
`packaging/extract_strings.py` a doplň anglickou verzi.

## Rozložení

```
src/speechscope_app/
    contract.py         # co GUI o knihovně ví napevno: úlohy, události, popisky, verze smlouvy
    i18n.py             # tr(), N_(), překlady z assets/i18n
    backend/
        command.py      # skládání argumentů CLI, čisté funkce
        library.py      # synchronní dotazy: version, list, doctor, models
        runner.py       # dlouhé běhy přes QProcess, události, log, zrušení
        settings.py     # QSettings: knihovna, modely, složka výsledků, jazyk, statistiky
        protocol.py     # protokol = uložené nastavení dávky (YAML), souhrny
        discover.py     # nalezení nahrávek, ručních vstupů a délky zvuku
        audio.py        # délka nahrávky z hlavičky WAV a FLAC
        manifest.py     # metadata k nahrávkám (manifest.csv)
        history.py      # historie běhů (run.json ve složkách běhů)
        results.py      # řádky s chybou a jejich nahrazení po přepočtu
        cache.py        # velikost a úklid mezivýsledků ve work
        diagnostics.py  # zip pro podporu
    ui/
        main_window.py  # boční menu, skládání běhu z protokolu, fronta, uvítání
        pages/          # welcome, batch (Analýza), run (Výpočet), results, protocols, environment
        widgets/        # editor protokolu, výběr feature, karty protokolů, historie, tabulky
        *_dialog.py     # nastavení, modely, úklid, segmentace a přepis
        recording_detail.py, file_actions.py
    assets/             # ikona, wordmark, překlady
    protocols/          # protokoly přibalené k aplikaci
    fake/               # falešná knihovna a zachycené fixtury
tests/
docs/speechscope-library/   # dokumentace knihovny, jen ke čtení
docs/navrh-gui-2.md         # návrhy změn GUI
packaging/                  # PyInstaller, přenosný Python s knihovnou, Inno Setup, ikona
```

## Kam jdou soubory

Do složky s nahrávkami se nikdy nezapisuje. Každý běh dostane vlastní
složku `Dokumenty\SpeechScope\<datum>_<protokol>\` s `features.csv`
(zapisuje se průběžně po každé nahrávce), `protocol.yaml` (co přesně se
spustilo), `config.yaml` (parametry pro `--config`), `manifest.csv`
(metadata, když byla), `run.json` (stav běhu), `events.jsonl` (záznam
průběhu) a `speechscope.log`. Mezivýsledky (segmentace, přepisy) sdílí
všechny běhy ve `Dokumenty\SpeechScope\work\`; uklidit je jde v Prostředí.
Složku výsledků i modelů lze změnit na uvítání, v Prostředí nebo
v Nastavení.

Modely nejsou součástí instalátoru. U nainstalované aplikace se navrhnou
do složky `models` vedle ní; klinika je dostane z balíku
(`speechscope models pack`, dialog „Modely ze souboru“), zálohou je
stažení z Hugging Face.

## Balení

Viz [packaging/README.md](packaging/README.md).
