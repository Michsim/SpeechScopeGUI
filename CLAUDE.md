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
- **GUI knihovnu neimportuje.** Volá její CLI jako podproces. Laciné
  synchronní dotazy (`version`, `list --json`, `models list --json`) přes
  `backend/library.py` (katalog se načte jednou pro všechny úlohy,
  `features(task)` filtruje podle `tasks`), `doctor --json` (sekundy, v exe
  víc) jen na pozadí přes `backend/query.py` (`AsyncQuery`, QProcess):
  Prostředí ho pustí hned po startu i na Zkontrolovat, výsledek jde signálem
  `report_changed` do všech stránek. Spustit knihovnu v hlavním vlákně nikdy
  nevolá; bez zprávy doctor ukáže „Kontroluji prostředí…“ se Storno a pokračuje
  po jejím příchodu (`BatchPage._run`, `set_doctor`). Dlouhé běhy (`extract`,
  `segment`, `transcribe` s `--progress-json`) přes `backend/runner.py`
  (QProcess).
- **Argumenty CLI skládá jen `backend/command.py`.** Obrazovky příkazovou
  řádku nikdy neskládají.
- **`contract.py` je jediné místo s vědomostmi o knihovně natvrdo**: úlohy,
  přípony, tvar událostí, verze smlouvy (`PROTOCOL_VERSION = 2`, přijímá
  i 1), popisky domén, skupin a providerů, orientační ceny, záložní seznam
  jazyků. Seznam feature, providerů i jejich parametry se nikdy neopisují,
  berou se z `list --json`, `list --providers --json` a `list --params`.
  Jazyky nahrávek se berou při startu z `models list --json` a pak
  z `doctor --json` (`models.stanza.languages`,
  od 12. 9. 2026 cs, en, de, it, es, fr); GUI k nim má jen popisky
  v `LANGUAGE_LABELS`, nový jazyk knihovny se ukáže i bez popisku (kódem).
  Artikulace (phnrec) zůstává jen česká, to hlídá knihovna.
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
  se `ProtocolEditor`), používají ho Analýza i Protokoly. Uživatel výslovně
  nechce editor vestavěný do stránky. Na Analýze v rozšířeném režimu má
  každá karta Upravit… (`dialog.edit(proto, mode="edit"|"copy")`: vlastní
  protokol se přepíše na místě, přibalený se uloží jako kopie) a rámeček
  Feature a parametry má Nový protokol… (`mode="new"`, jméno, popis, úloha,
  výběr feature). Dočasné neuložené úpravy pro jeden běh neexistují:
  `effective_protocol()` je vždy uložený protokol plus jazyk nahrávek
  z lišty. Stránka Protokoly používá `dialog.open_for` bez jména a popisu.
- **Falešná knihovna** `speechscope_app.fake` má stejné příkazy a kódy jako
  skutečné CLI a vrací zachycené fixtury. Testy GUI běží jen proti ní.
  Při změně knihovny se fixtury zachytí znovu:
  `uv run python packaging/capture_fixtures.py` (česky i anglicky,
  `*.en.json`, doctor se složkou modelů repa knihovny). `--models-dir`
  a `--lang` jsou globální volby a patří PŘED podpříkaz
  (`speechscope --models-dir M --lang en list --json`).
- **Popisy feature a parametrů jdou z knihovny v jazyce GUI**: `command._common`
  posílá `--lang` (jazyk z `i18n.language()` při vytvoření `Library`
  v `settings.make_library`), knihovna překládá `description`, `outputs`
  a popisy parametrů (`translations/en.yaml` v repu knihovny), jména
  sloupců a `notes` zůstávají. Názvy skupin (`GROUP_LABELS`) a providerů
  dělá GUI samo.

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
- Jazyk se volí v Nastavení (`ui/language`, prázdné = systém; bez uložené
  volby angličtina), po uložení se nabídne restart. Velikost písma
  (`ui/font_scale`: small = původních 10 bodů, normal, large, largest) násobí všechny velikosti
  v `theme.stylesheet(scale)` (základ 11 bodů) a platí hned
  (`theme.apply_scale`); dlouhé popisky, které nesmí roztahovat layout,
  jsou `widgets/elided_label.py`. Popisy z knihovny
  jdou přes `--lang` (viz výše), log knihovny je anglicky natvrdo.
  Přibalené protokoly mají `name_en`/`description_en`; v UI se používá
  `proto.display_name`, `proto.name` zůstává klíč.

## Stránky
Pořadí v nabídce: Analýza · Výpočet · Výsledky · Protokoly · Prostředí.
Po startu je vždy Uvítání (`pages/welcome.py`, v zásobníku jako
`PAGE_WELCOME`, v nabídce není): logo, dvě věty, tlačítka Začít analýzu
a Zkontrolovat prostředí; modré je to doporučené (`start_page()`: bez
knihovny nebo modelů Prostředí, a to se rovnou zkontroluje). Dokud chybí
modely, je na uvítání blok První nastavení: složka modelů a výsledků
(Změnit… = `MainWindow.change_models_dir/change_work_root`), volné místo,
Modely ze souboru… a Stáhnout modely…. Tytéž složky jsou i v Prostředí
(karta Složky). Výchozí složka modelů je u zabalené aplikace `{app}\models`
vedle exe (`settings.default_models_dir`; instalátor je per-user, takže
zapisovatelná), při vývoji AppData; starší instalace s modely v AppData
je používá dál.
Prostředí (doctor, modely, Diagnostika… = zip pro podporu z
`backend/diagnostics.py`) · Analýza (dva kroky v `QStackedWidget`:
1 nahrávky s metadaty z `manifest.csv` (`backend/manifest.py`, tolerantní
čtení, normalizovaná kopie do složky běhu), výběr více řádků a Odebrat
(tlačítko, Delete, pravé tlačítko: nahrávka zůstane na disku, jen vypadne
z dávky; `_excluded` přežije přeskenování téže složky, jiná složka ho
zahodí; Vrátit odebrané (k); počty ukazují „(k odebráno)“) a Pokračovat; 2 úloha a jazyk
nahoře v jedné řadě, karty protokolů pod tím, vlevo dole ← Nahrávky, vpravo
Spustit; stránka si krok pamatuje (`go_to_step`, `current_step`); na začátku
kroku 2 pole Název běhu (prázdné, jen nápověda; `run_label()`), ukládá se do
`run.json` jako `label` (`RunInfo.display_name`) a slug názvu je místo slugu
protokolu v názvu složky (`<datum>_<čas>_<slug>`, `unique_run_dir`); historie
umí Přejmenovat… (F2, `MainWindow.rename_run`): přepíše `label` a přejmenuje
i složku (`history.rename_run_dir`, datum a čas zůstávají, prázdný název =
slug protokolu z `protocol.yaml`); běžící běh nebo složku s otevřeným souborem
(Excel) přejmenovat nejde, pak zůstane jen `label`; stránky si cesty přepíší
přes `relocate(old, new)`;
v rozšířeném režimu souhrn feature, Upravit…, Uložit jako protokol…
a pod úlohou řada „Spustit:“ Výpočet feature · Jen segmentace · Jen přepis
(`contract.RUN_MODES`, `BatchPage.set_mode`): krok 3 pak místo karet ukáže
volby mezivýsledku (model segmentace z `contract.SEGMENT_MODELS`, vyříznutá
řeč; model Whisperu z protokolu, jazyk z lišty), bez dialogu; přepis jen pro
story, monologue, reading (`TRANSCRIPT_TASKS`), u fonace jen segmentace,
u DDK žádný mezivýsledek (`SEGMENT_TASKS`, slabiky hledá ddk_events); po startu
vždy výpočet feature; Spustit se před nepřipraveným
providerem zeptá; délka nahrávek z hlavičky WAV/FLAC (`backend/audio.py`) a odhad
dávky podle minut zvuku, statistika `stats/<slug>/seconds_per_audio_minute`) · Protokoly (přibalené ke čtení, kopie, vlastní s editorem, import,
export, smazání) · Výpočet (tabulka nahrávek × providerů, odhad času, Zrušit
s potvrzením, fronta dalších dávek: Spustit během běhu zařadí, po konci
se pustí další, `Job` v `main_window.py`; stavová karta `widgets/status_header.py` nahoře, tabulka
nahrávek v kartě, výsledek jako štítek přes buňku) · Výsledky (historie běhů ze složek v Dokumentech přes
`backend/history.py` a `run.json` jako karty (`widgets/run_history.py`, `RunCard`
se štítkem stavu, API `count/card/current_row/select_row`), stavová karta se
štítky počtů (klik přepne filtr), tabulka vybraného běhu v kartě se zmrazeným prvním
sloupcem (`widgets/frozen_table.py`), hledání sloupce, popis sloupce
v tooltipu z `list --json`, detail nahrávky (`ui/recording_detail.py`),
Spočítat znovu chybné = nová dávka `Job.merge_into`, řádky se vrátí do
původní tabulky přes `backend/results.merge_results`) · Prostředí navíc
Mezivýsledky (`backend/cache.py`, dialog Uvolnit místo). Dvojklik na
nahrávku (Analýza, Výpočet) ji otevře přiřazeným programem, pravé tlačítko
nabídne přehrát, zobrazit ve složce, kopírovat cestu (`ui/file_actions.py`);
na Výsledcích je dvojklik detail a přehrání je v pravém tlačítku.
Historie (Výpočet i Výsledky) umí Smazat… (tlačítko, Delete, pravé tlačítko)
a nabídka Aplikace „Smazat všechny běhy…“: složky běhů jdou do Koše přes
`backend/trash.py` (SHFileOperationW), běžící běh se přeskočí, potvrzení
řeší hlavní okno (`delete_run`, `delete_all_runs`); `work` se nemaže.
V editoru má dlaždice feature zkrácený popis; odkaz s počtem sloupců nebo
dvojklik otevře `ui/feature_detail.py` (tabulka sloupec, jednotka, popis;
jednotka ze závorky na konci popisu), totéž dvojklikem na feature v okně
protokolu.
Dvojklik na kartu protokolu (Analýza, Protokoly) nebo odkaz „co se počítá“
otevře `ui/protocol_detail.py`: strom skupina → feature → sloupec s popisem,
providery se stavem z doctor, parametry nastavené protokolem; jen ke čtení.

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
  whisper,stanza,onnx,phnrec,ddk --out speechscope-modely-v3.zip` (asi 6,2 GB,
  v `dist/`; v2 z 12. 9. je bez `ddk`).
  phnrec v něm být může: provider phonemes ho najde v `models\phnrec`
  sám, pokud parametr `phonemes.phnrec_dir` zůstane prázdný. WavLM
  a pyannote klinika nepotřebuje, segmentace jede přes ONNX.
- DDK (knihovna 0.3.0): provider `ddk_events` (síť E/V/O v ONNX, model `ddk`
  ve složce `ddk-onnx`, 630 MB) a feature `acoustic.ddk.*` jen pro /pataka/;
  obecná akustika se na úloze ddk nepočítá. Model jde jen z vydání na GitHubu
  s tokenem (`models download --only ddk`), pro kliniky z balíku v3. Dotaz
  před Spustit na nepřipravený provider platí i tady.
- Knihovna zapisuje `features.csv` průběžně po každé nahrávce (přes
  `.part` a přejmenování). Po Zrušit nebo chybě GUI ukáže částečnou
  tabulku. Soubor nesmí být během běhu otevřený v Excelu, **ani GUI ho
  během běhu nesmí otvírat** (Windows odmítnou přejmenování, dokud má
  soubor někdo otevřený, a knihovna spadne). Historie proto u běhu se
  stavem „běží“ řádky nepočítá; počet hotových zapisuje hlavní okno do
  `run.json` po každé nahrávce (`_file_done`) a Výsledky běžící běh
  neotvírají.
- Whisper: `beam_size=5` nechat, `condition_on_previous_text=false`
  (výchozí v knihovně) brání smyčkám halucinací u patologické řeči.
  Beam 1 je 5× rychlejší, ale u těžkých nahrávek kazí text. Při pádu
  na kartě knihovna sama dopočítá v int8 na CPU. Jazyk z lišty na Datech
  jde do `transcript.language` i `nlp.language` a přebije protokol.
- Whisper na GPU jede jen přes CUDA (CTranslate2), tedy jen NVIDIA;
  DirectML má jen segmentace. Balík nemá `cublas64_12.dll`, takže bez
  nainstalované CUDA 12 počítá Whisper i s NVIDIÍ na CPU. Odložené
  varianty (14. 9. 2026, zatím nedělat): 1) přibalit wheel
  `nvidia-cublas-cu12` (~0,5 GB, výsledky beze změny, doporučeno jako
  první); 2) whisper.cpp s Vulkanem jako podproces (NVIDIA, AMD, Intel;
  jiné přepisy, znovu ověřit halucinace a zlaté testy); 3) Whisper
  v ONNX Runtime s DirectML (slabší beam search, pomalý dekodér,
  DirectML jen v údržbě).
- Vzdálená plocha (RDP) a virtuální stroje: DirectML v RDP relaci
  k fyzické kartě nemá přístup (chyba C0262002), knihovna dopočítá na
  procesoru a `doctor` to hlásí (`gpu.remote_session`,
  `gpu.onnxruntime_gpu_usable`), Prostředí ukáže oranžovou kartu. Ve VM
  s kartou přes passthrough v režimu WDDM je Whisper pomalý kvůli latenci
  na každý krok dekodéru (8 % využití karty, beam nepomůže); řešení je
  režim TCC (`nvidia-smi -dm 1`), ne parametry knihovny.
- V repu knihovny vždy `uv sync --all-extras`; holé `uv sync` odinstaluje
  extras onnx a whisper a testy se tiše přeskočí.
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
