# SpeechScope App – pokyny pro Claude Code

## Co to je
PySide6 desktopová aplikace nad knihovnou SpeechScope (extrakce řečových
biomarkerů z klinických nahrávek). Knihovna žije v samostatném repu
(`..\SpeechScope\SpeechScope`, GitHub `Michsim/SpeechScope`), její
dokumentace je zkopírovaná v `docs/speechscope-library/` jen ke čtení.
GUI je na GitHubu `Michsim/SpeechScopeGUI`. Uživatelé: klinici (protokol
a tři kliknutí) i výzkumníci (rozšířený režim s výběrem feature
a parametrů). Cíl: jeden instalátor exe pro Windows.

## Architektura – neměnit bez domluvy
- **GUI knihovnu neimportuje.** Volá její CLI jako podproces. Synchronní
  dotazy (`version`, `list --json`, `doctor --json`, `models list --json`)
  přes `backend/library.py`, dlouhé běhy (`extract`, `segment`,
  `transcribe` s `--progress-json`) přes `backend/runner.py` (QProcess).
- **Argumenty CLI skládá jen `backend/command.py`.** Obrazovky příkazovou
  řádku nikdy neskládají.
- **`contract.py` je jediné místo s vědomostmi o knihovně natvrdo**: úlohy,
  přípony, tvar událostí, verze smlouvy (`PROTOCOL_VERSION = 2`, přijímá
  i 1), popisky domén, skupin a providerů, orientační ceny, záložní seznam
  jazyků. Seznam feature, providerů i jejich parametry se nikdy neopisují,
  berou se z `list --json`, `list --providers --json` a `list --params`.
  Jazyky nahrávek se berou z `doctor --json` (`models.stanza.languages`).
- **Události smlouvy 2**: `start` → pro každou nahrávku `begin`, `stage`
  (provider: running, pak done/cached/error se `seconds`) a `file` →
  `done` → `saved`. `BatchState` v `contract.py` z nich skládá stav.
- **Protokol** (`backend/protocol.py`) = YAML s úlohou, výběrem feature
  a `config` ve tvaru, který knihovna bere přes `--config`. Klinik protokol
  vybírá, výzkumník ho v rozšířeném režimu vyrábí. Souhrn „co protokol
  stojí“ (providery, sloupce, cena) počítá `protocol.summarize`
  a `card_infos`; doba na nahrávku z minulého běhu je v nastavení
  (`stats/<slug>/seconds_per_file`).
- **Editor feature a parametrů je jedno okno** (`ProtocolEditorDialog`
  se `ProtocolEditor`), používají ho Data i Protokoly. Na stránkách je jen
  souhrn a tlačítko Upravit. Uživatel výslovně nechce editor vestavěný do
  stránky.
- **Falešná knihovna** `speechscope_app.fake` má stejné příkazy a kódy jako
  skutečné CLI a vrací zachycené fixtury. Testy GUI běží jen proti ní.
  Při změně knihovny se fixtury zachytí znovu (návod v `fake/__init__.py`);
  `--models-dir` je globální volba a patří PŘED podpříkaz
  (`speechscope --models-dir M doctor --json`).

## Jazyky GUI
- Zdrojové texty jsou české a jsou zároveň klíče. Každý text pro uživatele
  jde přes `tr("…")` z `i18n.py`; texty v tabulkách (`contract.py`,
  popisky modelů) se píší jako `N_("…")` v `i18n.Labels`, který překládá
  při čtení. Proměnné do textu jen přes `tr("… {n} …").format(n=…)`,
  nikdy f-string.
- Překlady jsou v `assets/i18n/<jazyk>.json`. Po přidání textů spustit
  `uv run python packaging/extract_strings.py` a doplnit překlad;
  `tests/test_i18n.py` hlídá, že nic nechybí a že sedí zástupné symboly.
  Nový jazyk = nový JSON + název v `i18n.LANGUAGE_NAMES`.
- Jazyk se volí v Nastavení (`ui/language`, prázdné = systém), platí po
  restartu. Texty z knihovny (popisy feature, parametrů, log) zůstávají
  české. Přibalené protokoly mají `name_en`/`description_en`;
  v UI se používá `proto.display_name`, `proto.name` zůstává klíč.

## Stránky
Prostředí (doctor, modely, Diagnostika… = zip pro podporu z
`backend/diagnostics.py`) · Data (dva sloupce: vlevo nahrávky s metadaty
z `manifest.csv` (`backend/manifest.py`, tolerantní čtení, normalizovaná
kopie do složky běhu), úloha a jazyk, vpravo karty protokolů;
v rozšířeném režimu souhrn feature, Upravit…, Uložit jako protokol…,
Jen segmentace, Jen přepis; Spustit se před nepřipraveným providerem
zeptá) · Protokoly (přibalené ke čtení, kopie, vlastní s editorem, import,
export, smazání) · Běh (tabulka nahrávek × providerů, odhad času, Zrušit
s potvrzením, fronta dalších dávek: Spustit během běhu zařadí, po konci
se pustí další, `Job` v `main_window.py`) · Výsledky (historie běhů ze složek v Dokumentech přes
`backend/history.py` a `run.json`, tabulka vybraného běhu).

## Pasti z knihovny, které GUI hlídá
- Podproces vždy s `PYTHONUTF8=1` (`library.subprocess_env`). Bez toho
  knihovna při přesměrovaném stdout na Windows spadne na cp1252.
- Vždy posílat `--models-dir` a `--log-file`.
- `segments.model=auto` bere jen hotovou cache. Protokol, který potřebuje
  segmentaci, musí mít v `config` `segments.model` nastavený; samostatná
  segmentace (Jen segmentace) `auto` nenabízí.
- `doctor --json` končí kódem 1, když něco chybí; to není chyba.
- Modely pro kliniky jdou z balíku (`models unpack BALÍK`, dialog
  „Modely ze souboru“): kód 1 = některý model selhal, kód 2 = není to
  balík. Stahování z Hugging Face je záloha. Balík pro kliniky se dělá
  v repu knihovny: `uv run speechscope models pack --only
  whisper,stanza,onnx,phnrec --out speechscope-modely-v1.zip` (5,25 GB).
  phnrec v něm být může: provider phonemes ho najde v `models\phnrec`
  sám, pokud parametr `phonemes.phnrec_dir` zůstane prázdný. WavLM
  a pyannote klinika nepotřebuje, segmentace jede přes ONNX.
- Knihovna zapisuje `features.csv` průběžně po každé nahrávce (přes
  `.part` a přejmenování). Po Zrušit nebo chybě GUI ukáže částečnou
  tabulku. Soubor nesmí být během běhu otevřený v Excelu.
- Whisper: `beam_size=5` nechat, `condition_on_previous_text=false`
  (výchozí v knihovně) brání smyčkám halucinací u patologické řeči.
  Beam 1 je 5× rychlejší, ale u těžkých nahrávek kazí text. Při pádu
  na kartě knihovna sama dopočítá v int8 na CPU. Jazyk z lišty na Datech
  jde do `transcript.language` i `nlp.language` a přebije protokol.
- Do složky s nahrávkami se nikdy nezapisuje. Výstupy do
  `Dokumenty\SpeechScope\<datum>_<protokol>\`, mezivýsledky do `work\`.
- Skripty na snímky obrazovky (mimo testy) musí nastavit
  `QApplication` organizaci a jméno (SAMI, SpeechScopeApp) nebo podstrčit
  `app_data_dir`, jinak vlastní protokoly zapisují do
  `AppData\Local\python`. `QT_QPA_PLATFORM=offscreen` nemá písma, snímky
  dělat s `windows` a `widget.grab()`.

## Příkazy
```
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run speechscope-app --fake        # GUI proti falešné knihovně
uv run speechscope-fake doctor --json
uv run --with pillow python packaging\make_icon.py   # ikona a wordmark z icon\
```

## Styl
- Typované signatury, `from __future__ import annotations`.
- Komentáře, docstringy a texty v UI česky, identifikátory anglicky.
- Nahrávky pacientů nikdy do gitu. `.gitignore` blokuje `data/`, `work/`,
  `out/`, `models/` a všechna audio mimo `tests/data/`.
- Refaktoring a změna chování nikdy v jednom commitu.
- Změny GUI nejdřív navrhnout (text, mockup), pak psát; snímek před
  commitem. Návrhy jsou v `docs/navrh-gui-2.md`.
