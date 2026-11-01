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
- **`contract.py` je jediné místo s vědomostmi o knihovně natvrdo**: úlohy
  (od knihovny 0.4.0 phonation, ddk, spontaneous, reading; story a monologue
  jsou aliasy spontaneous, `canonical_task` je převádí při čtení protokolu
  i manifestu, starší složky `work\story` zůstávají),
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
  se `ProtocolEditor`) a je **jen na stránce Protokoly** (`dialog.open_for`).
  Uživatel výslovně nechce editor vestavěný do stránky. Od 31. 10. 2026
  (rozhodnuto) Analýza protokoly nezakládá ani neupravuje: bez Upravit…,
  Nového protokolu i rámečku Feature a parametry; u řádku úlohy zůstává jen ⓘ
  (co protokol počítá, jen ke čtení). Dočasné neuložené úpravy pro jeden běh
  neexistují: `effective_protocol()` je vždy uložený protokol plus jazyk nahrávek.
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
Pořadí v nabídce (od 30. 10. 2026, mockup
https://claude.ai/artifact/UkcxmVZDY3M9n89UQFuhdq, `docs/mockups/domu-pacienti/` mimo
git): Domů · Analýza nahrávek · Výsledky · Pacienti, dole Protokoly · Normy ·
Prostředí (`PAGE_HOME` … `PAGE_ENV`; popis stránek Pacienti a Normy je v sekci
Normy a dimenze). Uvítání mimo nabídku už není. **Levá lišta** (`widgets/sidebar_nav.py`, `SidebarNav`, od 31. 10. 2026
podoba B se sbalením a světle modrou barvou `theme.SIDEBAR`, mockupy
https://claude.ai/artifact/WrwYsgM4VjgRZ39WXDFfYP a https://claude.ai/artifact/8Hf6JPLaZd8XZ83FQx3itR,
`docs/mockups/lista/` mimo git): značka z `mark.png` a název, položky `NavItem` s čárovými
ikonami kreslenými v QPainter (`draw_icon`, bez SVG), dole nadpis Nastavení, stav prostředí
tečkou (`set_environment` z `_sidebar_state`/`_sidebar_report`, klik → Prostředí) a Sbalit
lištu (Ctrl+B, 56 px, `ui/sidebar_collapsed`); Ctrl+1…7 přepíná stránky. Běžící dávka je
štítek a tenký průběh u Výsledků (`set_badge`, `parse_progress`), text položky se nemění. **Výsledky a Výpočet jsou od
31. 10. 2026 jedna stránka** (`pages/runs.py`, `RunsPage`, mockup
https://claude.ai/artifact/9oecmvHjjNY99LeW3vvxZb, `docs/mockups/vysledky-vypocet/` mimo
git; `PAGE_RUN` = `PAGE_RESULTS`): přehled = oddíl „Běží a ve frontě“ (`set_running`,
`set_progress`, `set_queue`, `clear_running` z hlavního okna vedle pruhu na Domů;
Průběh, Zrušit…, Vyndat z fronty → `RunPage.queue_remove`) a tabulka běhů přes celou
šířku (běh, úloha, protokol, nahrávky, stav, doba, spuštěno; filtr Analýzy · Hodnocení
· Normy, hledání, pravé tlačítko, Delete, F2); zdroj je skrytá `ResultsPage.history`
(opisuje se po signálu `RunHistory.rebuilt`, mazání a přejmenování jdou přes její
signály; uhlazená tabulka podoby A: záhlaví velkými písmeny, štítky úlohy a stavu
(`_cell_widget`, `_pill`; buňka pod štítkem je prázdná, text v `TEXT_ROLE`, `cell_text`), průběh v řádku u běžícího běhu, relativní datum `_when`, šipka ›;
sloupce podle obsahu, název běhu nejvýš `NAME_MAX`). Dvojklik nebo Enter na řádek = detail (`open_row`, `open_entry`, `open_run`): „‹ Výsledky“
(Esc), šipky na sousední běh (`step`) a záložky Hodnoty · Průběh (`set_tab`): Hodnoty =
`ResultsPage`, Průběh = `RunPage`, obě vložené přes `set_embedded` (bez nadpisu a
historie). Přehled běhů
stylují sdílené `widgets/clean_table.py` (`style_table(selectable=True)`, `pill`, `cell`,
`card`). U vyšetření je řada úloh `ResultsPage.task_bar` přenesená nad záložky a platí
pro obě (`ResultsPage.task_shown` → `RunsPage._task_shown` přehraje Průběh té úlohy,
výchozí běžící úloha `prefer_running`; `open_run` otevře danou úlohu). Běžící běh má Hodnoty neaktivní (features.csv se během běhu neotvírá),
Průběh jiného běhu během výpočtu neaktivní. Spustit a pruh na Domů otevřou detail na
Průběhu (`MainWindow.show_running`, `RunsPage.show_running`), po dávce
`_go_after_batch` na Hodnoty (`show_values`). Popis Výpočtu a Výsledků níže platí pro
obsah záložek.
**Domů** (`pages/home.py`, `HomePage`, po startu vždy, `show_home`): rozcestník
podle výstupu, dvě velké karty `ActionCard` Hodnocení pacienta a Analýza nahrávek
(obě s modrým tlačítkem, dál vede jen tlačítko, ne klik do karty; → Analýza).
Nové hodnocení… nepřeskakuje na Pacienty: Domů má dva kroky jako Analýza,
krok 2 (`HomePage.show_builder`, `MainWindow.new_assessment_from_home`) je
průvodce z nahrávek `AssessmentBuildWidget` se záhlavím „‹ Domů“ a odkazem
„…nebo z hotových běhů“ (`runs_requested` → `new_assessment_from_runs`: Pacienti
a `AssessmentPage.new_from_runs`, dialog bez volby zdroje); Spustit
(`_start_home_plan`) zařadí dávky a vrátí karty, Zpět rozdělaný průvodce bez
ptaní zahodí (`show_cards`). Týž widget je uvnitř tenkého
`AssessmentFromRecordingsDialog` (Nové hodnocení… na Pacientech; dialog
deleguje atributy na `widget`). Po Spustit jednoho pacienta Domů nabídne „Další
vyšetření téhož pacienta…“ (`offer_next_visit`, `next_visit_requested` →
`MainWindow.next_visit_from_home` s `_last_patient`: průvodce s předvyplněným
pacientem, věkem a pohlavím přes `AssessmentBuildWidget.prefill`, datum a složky
prázdné). Pod kartami pruh „právě běží“ (`set_running`, ve frontě první dávka
jménem a počet dalších; `set_running`
z `_show_progress`, `set_progress` z `_file_done`, klik → Výpočet, po konci
`clear_running`); dole pod čarou tlačítko Nová norma… (→ Normy a průvodce) a
stav: štítek knihovna/modely jako dřív na uvítání (`set_state`, `set_report`,
`recommends_environment`, modré tlačítko Prostředí), pod ním varování o normách
se změněnou feature (`_refresh_home` přes `outdated_features`, odkaz na Normy).
Rozhodnuto 30. 10.: žádné Pokračovat, žádné poslední běhy. Dokud chybí
modely, je na Domů blok První nastavení: složka modelů a výsledků
(Změnit… = `MainWindow.change_models_dir/change_work_root`), volné místo,
Modely ze souboru… a Stáhnout modely…. Tytéž složky jsou i v Prostředí
(karta Složky). Výchozí složka modelů je u zabalené aplikace `{app}\models`
vedle exe (`settings.default_models_dir`; instalátor je per-user, takže
zapisovatelná), při vývoji AppData; starší instalace s modely v AppData
je používá dál.
Prostředí (doctor, modely, Diagnostika… = zip pro podporu z
`backend/diagnostics.py`) · Analýza (dva kroky v `QStackedWidget`:
1 nahrávky s metadaty z `manifest.csv` (`backend/manifest.py`, tolerantní
čtení, normalizovaná kopie do složky běhu); tabulka nahrávek je od 30. 10. 2026
`widgets/meta_table.py` (`MetaTable`, sdílená s průvodcem z nahrávek; sloupce podle obsahu, název
nahrávky nejvýš `NAME_MAX` se zkrácením uprostřed, volné místo za posledním sloupcem;
od 31. 10. 2026 podoba A z mockupu https://claude.ai/artifact/RbdMDqP2NUunQRc1RdH1VD,
`docs/mockups/analyza-tabulka/` mimo git: záhlaví velkými písmeny s popisky sloupců (klíč
v tooltipu, `MetaTable.columns`), řádky bez mřížky, tučný název, délka vpravo; podbarvení,
modrou tečku u upravené buňky a „doplnit“ v prázdné povinné kreslí `_CellDelegate`, protože
styl `::item` barvu položky přebíjí; **zjednodušeno 31. 10. 2026** (mockup
https://claude.ai/artifact/Ldth4usZWzyaKP28da6nmu, `docs/mockups/analyza-zjednoduseni/` mimo git):
v hlavičce kroky „1 Nahrávky › 2 Úlohy“ (`step_label`, `_steps_html`), podtitulek jen v tooltipu
nadpisu; složka jedním řádkem (i podsložky, Vybrat…/Změnit…); místo dlaždic souhrn jedním řádkem
(`step1_hint`: nahrávky, zvuk, pacienti), štítek manifestu (`manifest_label`, ✓ / n z m / chyba,
sloupce v tooltipu) a žlutý odkaz „n bez údajů · doplnit ›“ (`missing_tile`, seznam k doplnění;
pacient, věk a pohlaví jsou povinné jen vzhledem); lišta nad tabulkou (`bars`): hledání, Seskupit,
Vrátit odebrané a **Metadata ▾** (`meta_menu`: + Sloupec, Uložit manifest… `save_action`, Načíst
jiný manifest…, Nepoužít manifest, Zahodit úpravy `edits_action`); s výběrem místo ní „vybráno n ·
Vyplnit… · Odebrat · ✕ zrušit výběr“; dole jen Pokračovat; Seskupit
(`set_group_by`, výchozí nic) jen podle sloupce metadat nebo podsložky (`GROUP_FOLDER`, jen
když nahrávky v podsložkách jsou), nic se neodhaduje z názvů; nadpis skupiny přes celou šířku
(hodnota, shodné údaje, počet a délka, prázdná hodnota na konci), klik označí skupinu; řádek
tabulky pak není index nahrávky, stránka převádí přes `data_index`, `table_row`,
`selected_indices`, `select_index`): vlevo
pevné sloupce jen ke čtení (název, délka, ruční vstupy), vpravo metadata
upravitelná dvojklikem, + Sloupec… (nabídka vyhrazených `meta.META_COLUMNS`
s popisky `meta.label`, nebo vlastní název, kanonizuje se `meta.canonical_column`),
Vyplnit vybrané… (jedna hodnota pro označené řádky, `fill_selected`), úpravy
drží tabulka podle cesty nahrávky (`edits`, přežijí přeskenování téže složky,
jiná složka = `reset`), hodnoty se normalizují `meta.normalize_value` (věk,
pohlaví f/m, datum ISO, `meta.normalize_visit`), upravená buňka je podbarvená
s původní hodnotou v tooltipu, prázdná povinná (`set_required`) žlutě.
`BatchPage.manifest()` skládá soubor plus úpravy do jednoho `Manifest` pro běh
(`base_manifest()` = jen soubor), řádek pod tabulkou říká „upraveno: n“ a
Zahodit úpravy; do složky s nahrávkami se nic nezapisuje samo, jen výslovné
Uložit manifest… (`save_manifest`: CSV se `;`, cesty relativní k cíli, uložení
do složky nahrávek manifest znovu načte a úpravy se stanou základem; rozhodnuto:
běh + výslovné uložení, ne katalog); dvojklik na metadata upravuje, na název
přehrává (`open_recording` před `files.meta_from`); `set_meta`, `add_meta_column`,
`fill_selected`, `discard_meta_edits` i pro testy. Výběr více řádků a Odebrat
(tlačítko, Delete, pravé tlačítko: nahrávka zůstane na disku, jen vypadne
z dávky; `_excluded` přežije přeskenování téže složky, jiná složka ho
zahodí; Vrátit odebrané (k); počty ukazují „(k odebráno)“) a Pokračovat; 2 jazyk nahoře,
řádky úloh pod ním (viz dál), vlevo dole ← Nahrávky, vpravo
Spustit; stránka si krok pamatuje (`go_to_step`, `current_step`); **krok 2 = řádky úloh**
(31. 10. 2026, mockup https://claude.ai/artifact/Nuh6L6PrsVcY5xAizg113o podoba 1 modernější,
`docs/mockups/analyza-ulohy/` mimo git): lišta úloh a karty protokolů (`ProtocolList`) na
Analýze zmizely; `widgets/task_rows.py` `TaskRowWidget` = vypínač, ikona úlohy (QPainter,
`TASK_ICONS`), název s počtem a délkou zvuku, `ProtocolCombo` (QComboBox kreslený na dva
řádky: název, pod ním `SUB_ROLE` = sloupce a cena), stav tečkou (`set_status`), odhad
(`set_count`), co se počítá, Upravit… (rozšířený režim); pod řádky `SummaryBar` (běhy
a nahrávky, zvuk, odhad, `ShareBar` podíl úloh). Bez sloupce `task` v manifestu jeden řádek
s výběrem úlohy (`_single_task`; od 1. 11. 2026 bez předvolby: „vyberte úlohu…“, řádek červeně
`state=missing`, protokol prázdný „protokol podle úlohy“, Spustit neaktivní a v patičce „Vyberte úlohu.“,
jiná složka volbu vynuluje); se sloupcem úloha řádek na úlohu, nahrávky bez
úlohy „počítat jako“ ve žlutém pruhu nad úlohami (`unassigned_row`, `unassigned_combo`, výchozí
první úloha, která v tabulce chybí, `set_unassigned_task`; odkaz „nebo doplnit v kroku 1 ›“ =
`show_without_task`: krok 1 se seznamem k doplnění úloh, viz níže; cílový
řádek má štítek „+n bez úlohy“) a **každá zapnutá úloha = samostatný běh** (rozhodnuto: bez skupiny,
název „název běhu · úloha“): `planned_runs`, jeden běh dál `run_requested`/`prepare_requested`,
víc `runs_requested(kind, [(Protocol, cesty)], volby)` → `MainWindow._start_runs` →
`_submit_all`. Protokol si pamatuje úloha (`_row_choice`), `current_protocol` = řádek, se kterým
se naposledy pracovalo; `select_protocol` přepne i úlohu. **Úloha je sloupec metadat
`task`** (`batch.TASK`): z manifestu, nebo přidaný přes + Sloupec… › úloha; `MetaTable.set_choices`
= dvojklik otevře rozbalovací seznam úloh, buňka ukazuje popisek (`display`), prázdná buňka šedě
kurzívou „jako …“ (`set_hint`, `HINT_ROLE`); úpravy, Vyplnit vybrané… (u úlohy výběr ze seznamu)
a Uložit manifest… fungují jako u ostatních metadat; `manifest()` dá úlohu do `tasks`, ne mezi
sloupce; `task_of`, `manifest_task` (z tabulky, cache `_task_cache`). Prázdná úloha nic
neblokuje (rozhodnuto 31. 10. 2026); **seznam k doplnění** (31. 10. 2026, varianta A): dlaždice
„bez údajů“ i „doplnit v kroku 1“ zamrazí právě neúplné řádky (`start_fixing("missing"|"task")`,
`MetaTable.freeze`, `frozen_keys`), vyplněný řádek nezmizí, dostane zelenou fajfku u názvu
(`set_done_check`, `refresh_done`, `DONE_ROLE`), nad tabulkou modrý pruh „Doplňuješ údaje ·
hotovo x z n“ (`fix_banner`), po doplnění všeho zelený s Uložit do manifestu…; Hotovo
(`end_fixing`, i druhý klik na dlaždici) vrátí celou tabulku, jiná složka seznam zruší; práh krátkých nahrávek podle úlohy řádku. `write_normalized` píše
knihovně vždy základní úlohu (`tasks.base_task`). Tentýž řádek mají průvodci Hodnocení
z nahrávek a Nová norma (`TaskRow.widget` se složkou, `refresh_look`: pod názvem počet a délka,
bez složky šedě); na začátku
kroku 2 pole Název běhu (prázdné, jen nápověda; `run_label()`), ukládá se do
`run.json` jako `label` (`RunInfo.display_name`) a slug názvu je místo slugu
protokolu v názvu složky (`<datum>_<čas>_<slug>`, `unique_run_dir`); historie
umí Přejmenovat… (F2, `MainWindow.rename_run`): přepíše `label` a přejmenuje
i složku (`history.rename_run_dir`, datum a čas zůstávají, prázdný název =
slug protokolu z `protocol.yaml`); běžící běh nebo složku s otevřeným souborem
(Excel) přejmenovat nejde, pak zůstane jen `label`; stránky si cesty přepíší
přes `relocate(old, new)`;
krok 2 (zjednodušený 31. 10. 2026): Název běhu nahoře
v kartě úloh, hlavička „Úlohy“ s nápovědou, vpravo jazyk jen když ho některý protokol potřebuje
(`_update_language_visibility`: přepis nebo jazykový rozbor), patička s ← Nahrávky, souhrnem
(`SummaryBar`) a Spustit; varování o krátkých nahrávkách jedním řádkem; karta kroku 2 je v posuvné ploše a stav v patičce je `ElidedLabel` (1. 11. 2026:
okno při přepnutí Výpočet feature · Jen segmentace · Jen přepis měnilo velikost);
v rozšířeném režimu v hlavičce úloh přepínač Výpočet feature · Jen segmentace · Jen přepis
(`contract.RUN_MODES`, `BatchPage.set_mode`): krok 3 pak místo karet ukáže
volby mezivýsledku (model segmentace z `contract.SEGMENT_MODELS`, vyříznutá
řeč; model Whisperu z protokolu, jazyk z lišty), bez dialogu; přepis jen pro
spontaneous a reading (`TRANSCRIPT_TASKS`), u fonace jen segmentace,
u DDK žádný mezivýsledek (`SEGMENT_TASKS`, slabiky hledá ddk_events); po startu
vždy výpočet feature; Spustit se před nepřipraveným
providerem zeptá; délka nahrávek z hlavičky WAV/FLAC (`backend/audio.py`) a odhad
dávky podle minut zvuku, statistika `stats/<slug>/seconds_per_audio_minute`) · Protokoly (přibalené ke čtení, kopie, vlastní s editorem, import,
export, smazání) · Výpočet (tabulka nahrávek × providerů bez `ResizeToContents`, šířky přepočítá
`_fit_columns` po naplnění a po každé nahrávce, při přehrání z historie jednou na
konci s vypnutým překreslováním, jinak 500 nahrávek trvalo 46 s; odhad času, Zrušit
s potvrzením, fronta dalších dávek: Spustit během běhu zařadí, po konci
se pustí další, `Job` v `main_window.py`; stavová karta `widgets/status_header.py` nahoře (štítky v zalamovaném
`widgets/flow_layout.py`, řada štítků jinak tlačila minimální šířku okna přes
obrazovku), tabulka nahrávek v kartě (od 31. 10. 2026 uhlazená přes `clean_table.style_table`: záhlaví
velkými, bez mřížky a střídání řádků, název tučně, poznámka šedě), výsledek jako štítek přes buňku; starý štítek
se před výměnou skryje, Qt ho maže až z fronty událostí) · Výsledky (historie běhů ze složek v Dokumentech přes
`backend/history.py` a `run.json` jako karty (`widgets/run_history.py`, `RunCard`
se štítkem stavu, API `count/card/current_row/select_row`), stavová karta se
štítky počtů (klik přepne filtr; od 31. 10. 2026 dvouřádková jako hlavička pacienta:
`StatusHeader(subtitle_below=True, pills_inline=True)`, název, štítky a tlačítka v řádku,
souhrn pod nimi), tabulka vybraného běhu v kartě se zmrazeným prvním
sloupcem (od 31. 10. 2026 podoba „A + karta sloupce“, mockup
https://claude.ai/artifact/3zTRx5diHQquy8ZjSMU6WW, `docs/mockups/vysledky-hodnoty/` mimo git;
`widgets/values_table.py`: `ValuesModel` ukazuje nahrávka · stav · pacient zmrazené vlevo
(`FrozenTableView.set_frozen_count`), metadata, feature, technické sloupce, `frame` zůstává
v pořadí CSV; `BandHeader` = záhlaví ve dvou patrech, pruh feature a krátký název z katalogu
s jednotkou ze závorky popisu; čísla vpravo, desetinná místa podle velikosti (`decimals_for`);
stav štítkem `StatusDelegate`; chybný řádek má span přes feature a text chyby kreslí
`ErrorSpanDelegate` za zmrazenou částí; `FooterBar` = průměr ± SD z řádků bez chyby;
přepínač skupin feature `set_feature_group`; klik na záhlaví číselného sloupce = `ColumnCard`
jako panel vpravo vedle tabulky přes její výšku (od 31. 10. 2026 podoba B, mockup
https://claude.ai/artifact/7kwbvudwbeWkc7ZMh49iqG, `docs/mockups/vysledky-karta/` mimo git;
dřív karta nad tabulkou brala ~190 px a na 1366 × 768 zbylo 6 řádků; `CARD_WIDTH`;
histogram, průměr ± SD, medián, rozsah, počet, ‹ předchozí · další ›, `open_card`,
`close_card`, `step_card`); výběr stylem `TABLE_STYLE`, jinak Windows 11 kreslil čárku u každé buňky;
`widgets/frozen_table.py`; šířky sloupců se měří z hlavičky a prvních
`SAMPLE_ROWS` řádků, celý jen sloupec s názvem souboru, u 500 × 135 buněk trvalo
měření všech 10 s; od 31. 10. 2026 měří `FrozenTableView.resizeColumnsToContents` samo
přes `sizeHintForIndex`, protože Qt ve skryté tabulce, tedy u detailu plněného z přehledu,
omezení řádků ignoroval a první otevření normy 500 × 150 trvalo 4,3 s, teď 0,2 s;
`ValuesModel` drží hodnoty jako seznamy (`value(key, row)`, `_levels`, `_span`) místo
pandas `.iloc` v `data()`, a `BandHeader.sectionSizeFromContents` bere výšku z `_height()`,
ne ze `sizeHint()`, který se ptal na všechny sloupce, takže měření rostlo kvadraticky), hledání sloupce, popis sloupce
v tooltipu z `list --json`, detail nahrávky (`ui/recording_detail.py`),
Spočítat znovu chybné = nová dávka `Job.merge_into`, řádky se vrátí do
původní tabulky přes `backend/results.merge_results`) · Prostředí navíc
Mezivýsledky (`backend/cache.py`, dialog Uvolnit místo). Dvojklik na
nahrávku (Analýza, Výpočet) ji otevře přiřazeným programem, pravé tlačítko
nabídne přehrát, zobrazit ve složce, kopírovat cestu (`ui/file_actions.py`);
na Výsledcích je dvojklik detail a přehrání je v pravém tlačítku.
Historie (Výpočet i Výsledky) má přepínač Analýzy · Hodnocení · Normy (viz
Běhy hodnocení v historii) a umí Smazat… (tlačítko, Delete, pravé tlačítko)
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

## Normy a dimenze (srovnání s normou, pavouk)
- **Norma** = složka `norms/<slug>/` s `norm.yaml` a `controls.csv`
  (`backend/norms.py`): tabulka kontrol se sloupci `subject`, `task`, `age`,
  `sex` a feature, žádné cesty ani jména. Přibalené v `speechscope_app/norms/`
  (zatím žádná), vlastní v `settings.norms_dir()`. `Norm.select(task, age, sex)`
  vybere kontroly ve věkovém okně ±5 let (`DEFAULT_WINDOW`, do 30. 10. 2026 ±10;
  na Hodnocení volba ±3, ±5, ±10, bez omezení), pod 15 kontrol
  (`MIN_CONTROLS`) okno rozšíří na další z ±5, ±10, ±15, ±20, pak bez okna
  (`WIDER_WINDOWS`) a označí `widened`;
  `same_sex` se pustí dřív než věk. `Selection.score` dává z-skóre otočené
  k patologii (kladné = horší), percentil mezi kontrolami a pásmo
  `ok` < 1 SD < `warn` < 2 SD < `bad`; `log=True` počítá po logaritmu.
  `build_norm` vyrobí normu z tabulek běhů (filtr podle sloupce skupiny).
- **Vyhrazené sloupce metadat** (`backend/meta.py`): `subject`, `visit`, `age`,
  `sex`, `group`, s aliasy bez diakritiky (`pacient`, `vek`, `pohlavi`…);
  pohlaví se normalizuje na `f`/`m`, věk na číslo. Manifest převádí názvy
  sloupců na kanonické už při čtení (`read_manifest`), takže `features.csv`
  má vždy `subject`, `age`…; kolize dvou sloupců na jedno jméno nechá druhý
  původní.
- **Index nahrávek** (`backend/visits.py`, `VisitIndex`): řádky všech
  skončených běhů (běžící se neotvírá, běhy norem s `RunInfo.norm` taky ne)
  jako `Recording`; cache v paměti podle cesty, mtime a velikosti
  `features.csv`. Automatické skládání do vyšetření (`group_visits`,
  `visits.json`) stránka už nepoužívá; `Visit` zůstává jako vstup výpočtu
  (`rows_by_task()` → `dimensions.evaluate`).
- **Katalog SQLite** (`backend/catalog.py`, `Catalog`, soubor
  `settings.catalog_path()` = `catalog.sqlite` v datech aplikace; 30. 10. 2026,
  rozhodnuto: katalog, ne náhrada složek): pacienti (`patients`: klíč
  `assessments.patient_key`, označení, pohlaví, `birth_year` zatím nevyužitý)
  a hodnocení (`assessments`, `assessment_recordings`) jsou v něm **pravda**;
  starší JSONy ze složky `assessments` naimportuje `import_assessments` při
  otevření `AssessmentStore` a přesune je do `assessments/imported/`, plány
  `*.plan.json` zůstávají soubory. Běhy a jejich řádky (`runs`, `recordings`
  s JSON `data`, klíč složka výsledků + `RunInfo.key`) jsou jen **index**:
  `VisitIndex(root, catalog=…)` čte CSV, jen když se změnil (mtime, velikost,
  verze registru úloh), jinak bere řádky z katalogu i po restartu
  (`_from_catalog`, `_to_catalog`, `drop_runs` pro zmizelé běhy). Smazání
  katalogu nic neztratí kromě hodnocení, proto JSONy zůstávají v `imported/`.
  Hlavní okno katalog otevře v konstruktoru a dá ho stránce Pacienti
  (`set_catalog` před `set_work_root`); bez hlavního okna si stránka udělá
  katalog v paměti (`Catalog(None)`). Jeden proces, hlavní vlákno, WAL.
  **Historie běhů přes katalog** (tabulka `run_info`, 30. 10. 2026 večer):
  `history.use_catalog(catalog)` nastaví hlavní okno globálně; `read_run` pak
  porovná otisk `run.json`, `protocol.yaml`, `features.csv` a logu (čas změny
  a velikost, `_run_signature`) a při shodě vrátí `RunInfo` z katalogu bez YAML
  a bez počítání řádků CSV; běžící běh se čte vždy ze souborů a neukládá se.
  `list_runs` po průchodu zapomene běhy, které pod složkou nejsou
  (`prune_run_info`). Platí pro Výsledky, Výpočet i index nahrávek. Testy
  začínají bez katalogu historie (`conftest.no_history_catalog`).
- **Hodnocení se zakládají ručně** (rozhodnuto 28. 10., mockup
  https://claude.ai/artifact/2UwaQmndVNFBCMSHmehQ8A, `docs/mockups/hodnoceni/`
  mimo git): `backend/assessments.py`, `Assessment` (pacient, věk, pohlaví,
  datum, `RecordingRef` = složka běhu, řádek, soubor, úloha) v katalogu
  (`AssessmentStore(folder, catalog)`, dřív JSON); `resolve` z něj složí `Visit` (řádek
  záložně podle souboru a úlohy, když se tabulka přepočítala; chybějící
  odkazy vrátí zvlášť). Přejmenovaný běh přepíše odkazy
  (`AssessmentStore.relocate` z `MainWindow._run_moved`). Norma se volí až
  při prohlížení. `norm_match` porovná protokol běhu (`protocol.yaml`)
  s `NormInfo.protocols`.
- **Dimenze** (`assets/dimensions.yaml`, `backend/dimensions.py`): dimenze →
  položky (sloupec, úlohy, směr `higher`/`lower`/`both`, `log`). Směr `both`
  (29. 10. 2026, tempo řeči je patologické oběma směry): `Selection.score`
  i `control_z` berou |z|, percentil podle |z|, strana odchylky je ve
  `Score.signed_z`; strom na Hodnocení u takové položky ukáže ▲/▼ a tooltip,
  pásmo „lepší než kontroly“ u ní neexistuje. Dimenze ze samých položek
  `both`, které vybočily stejným směrem, má šipku i u osy pavouka a ve
  štítku hlavičky (`DimensionResult.side`, `_dimension_label`). `evaluate` bere
  hodnoty vyšetření po úlohách (víc nahrávek = průměr po feature); skóre dimenze
  je průměr z-skóre položek **standardizovaný rozdělením téhož průměru
  u vybraných kontrol** (`Selection.control_z` po osobách, `_reference`:
  kontroly s úplnou sadou položek pacienta, pod `MIN_REFERENCE` = 5 i neúplné;
  `DimensionResult.raw`, `control_sd`, `n_controls`, `percentile` = pořadí
  mezi kontrolami, tooltip na z ve stromu). Důvod (29. 10. 2026, připomínka
  kolegy): průměr k z-skóre má SD < 1 (nezávislé 1/√k), takže by hranice
  1 a 2 SD byly příliš mírné a dimenze s různým počtem položek nesrovnatelné.
  Dimenze bez položky má `reason` a v pavouku není.
  `test_dimensions` hlídá, že každá položka existuje v katalogu falešky.
  Výběr položek určuje uživatel sám v YAML (klinický rozum, neměnit bez
  domluvy; naposledy upravil 29. 10. 2026, pořadí voice,
  articulation, tempo, pauses, prosody, breathing, language): Kvalita hlasu
  = HNR, CPP (`cpp.cpp`, ne CPPS) z fonace + CPP ze spontánní řeči (bez F0 SD);
  Artikulace = VOT medián, FRI ze čtení a spontánní řeči, DDK regularita (log)
  (RIRV zakomentované); Tempo řeči = DDK tempo a artikulační tempo ze čtení
  a spontánní řeči, obojí `both`; Pauzy = medián pauz (log); Prozódie = F0 SD
  a SD intenzity ze čtení a spontánní řeči; Dýchání = nádechy za minutu
  a medián (log), obojí `both`; Syntax = MLU, SDL, vedlejší věty na výpověď
  (`csr`); Slovní zásoba = MATTR, opakování frází (`ngrams`, vyšší = horší),
  hustota obsahových slov (Jazyk rozdělen 30. 10. 2026, entropie slov vyřazena). Změna
  YAML se projeví hned při příštím zobrazení (norma má jen surové hodnoty
  kontrol, z-skóre, log i směr se počítají až při prohlížení), aplikaci stačí
  restartovat; nová feature v dimenzi musí být i v tabulce normy.
- **Verze feature** (30. 10. 2026): verze knihovny se mění málo, feature často
  (intenzita 2.0, CPP 1.3 při téže 0.4.0). Hlavní okno proto při spuštění dávky
  zapíše do `run.json` `library_version` a `feature_versions` (feature → verze
  z katalogu `list --json` pro feature protokolu, `_feature_versions`;
  `RunInfo.feature_versions`). `norm_build.finish_plan` je opíše z `run.json`
  dávek do `norm.yaml` (`NormInfo.feature_versions`, úloha → feature → verze,
  jde i do exportu). `dimensions.evaluate(versions=Visit.versions_by_task())`
  dá položce s jinou verzí než u kontrol `ItemResult.versions` (pacient, norma),
  skóre počítá dál; strom na Hodnocení přidá ⚠ a tooltip, tabulka nahrávek
  „jiná verze feature“ s poznámkou (`norms.version_changes`). Stránka Normy
  porovná normu s katalogem nainstalované knihovny (`norms.outdated_features`,
  `outdated_text`) a ve varováních řekne, co se změnilo. Bez záznamu na jedné
  straně (starší běh, import) se nic nehlásí.
- **Stránka Pacienti** (dřív Hodnocení; `ui/pages/assessment.py`,
  `AssessmentPage`, `PAGE_ASSESSMENT`, od 30. 10. 2026; **od 31. 10. varianta B**:
  `page_stack` = přehled `widgets/patient_overview.py` `PatientOverview` (podoba D,
  mockup https://claude.ai/artifact/J8iCqC36qpFSDbyip27jb6: jeden seznam řádků `PatientRowWidget`
  bez skupin, pruh stavu (štítek stavu v řádku není), jméno, „muž · 64 let · 3 vyšetření“, vpravo datum a úlohy,
  šipka ›; výsledky dimenzí v řádku záměrně nejsou; rozpracované má úlohy se stavem;
  řazení poslední vyšetření · jméno · stav `set_sort`, hledání, filtr všichni · mimo normu ·
  počítá se; řádky z `_cards` přes
  `_fill_overview`, dopočítaný štítek karty přepíše řádek přes `PatientCard.on_band`),
  dvojklik nebo Enter = detail přes celou šířku (`open_overview_row`, „‹ Pacienti“ a Esc
  `show_overview`, šipky ‹ › a Alt+←/→ `step` v pořadí a filtru přehledu, „i z n“);
  `refresh(select=…)` a `select_assessment` otevřou detail rovnou; mockup
  https://claude.ai/artifact/D34hrQNAdgecczoUitFNx3, `docs/mockups/seznam-detail/` mimo
  git. Seznam karet níže zůstal skrytý (`list_panel`) jako zdroj výběru): vlevo karta na
  pacienta (`PatientCard`; pacient = `patient_key(subject)`, zatím jen řetězec
  bez mezer a velikosti písmen, rozhodnuto 30. 10.; štítek a pohlaví/věk podle
  posledního vyšetření, u víc vyšetření „n vyšetření · první → poslední“;
  `_groups` = vyšetření pacienta od nejnovějšího, `_shown` = právě vybrané),
  štítek nejhoršího pásma `worst_band`: v normě, hraniční, mimo normu, bez
  normy; úlohy s počtem nahrávek `task_count`) a hledání pacienta, nahoře Nové
  hodnocení…; pod hlavičkou řada vyšetření (`visit_bar`, tlačítka s datem,
  vybrané modré, `_select_visit`, `select_assessment(id)` přepne pacienta i
  vyšetření, `patient_entries()`) a vpravo Nové vyšetření… (`new_visit`: volba
  z nahrávek / z hotových běhů; z nahrávek
  `new_visit_from_recordings_requested` → `MainWindow.new_visit_from_recordings`,
  dialog průvodce s `prefill`; z běhů `AssessmentDialog(template=…)` předvyplní
  pacienta, věk a pohlaví bez nahrávek a data). Nad řadou vyšetření (aby při přepnutí neposkočil, řada se ve Vývoji schovává) přepínač
  Vyšetření · Vývoj v čase (`view_bar`, `set_view`, `view()`; Vývoj aktivní od dvou
  vyšetření s datem, `_update_view_bar`; bez normy jen v měřítku Hodnoty, viz
  níže): Vývoj schová pavouk, strom i
  nahrávky a ukáže `trend_panel` = mřížka malých grafů po dimenzích
  (`widgets/trend_chart.py`, `TrendGrid`/`TrendChart`: osa y −2 až +3 SD, zelený
  pás ±1, čárkovaně ±2, body barvou pásma, dutý bod = jiná verze feature než
  norma, popisky „10/25“) a tabulka dimenze × datum se změnou za období
  (`backend/longitudinal.py`: `build_timeline` z hotových vyhodnocení, nic nového
  nepočítá; kontroly podle věku při každém vyšetření, změna = rozdíl v SD bez
  významnosti, vyšetření bez data se vynechá, dvě v jeden den vedle sebe podle
  založení; `format_change` ▲ horší ▼ lepší). „jen dimenze mimo normu“ filtruje
  podle posledního skóre (|z| ≥ 1). Uložit obrázek… ve Vývoji uloží mřížku s
  tabulkou (`trend_panel.grab`, přípona `-vyvoj`). **Měřítko Hodnoty** (30. 10. 2026
  večer, mockup https://claude.ai/artifact/Nw7jUHXFYWFv7MHQ7eb1Dm,
  `docs/mockups/vyvoj-hodnoty/` mimo git): ve Vývoji přepínač „Měřítko: SD proti
  normě · Hodnoty“ (`scale_sd_btn`, `scale_values_btn`, `set_scale`, `trend_scale()`
  = volba uložená v `ui/trend_scale` přes `scale_changed`, `effective_scale()` = bez
  normy vždy Hodnoty; SD bez normy neaktivní se štítkem „potřebuje normu“,
  `_update_trend_controls`). Hodnoty = mřížka **po položkách** dimenzí ve skupinách
  s nadpisem dimenze a směrem (`TrendGrid.set_groups`, `TrendSeries.scale="values"`:
  osa v jednotkách feature podle hodnot pacienta přes `nice_axis`/`format_tick`,
  bez pásem, čára a body černé, v hlavičce poslední hodnota a změna se šipkou
  ▲ k patologii / ▼ od ní podle směru položky, u `both` jen číslo; dutý bod = jiná
  verze feature než u posledního vyšetření) a tabulka položka × datum s jednotkou,
  změnou v jednotkách a v procentech, řádky skupin přes celou šířku
  (`_fill_values`; `backend/longitudinal.py` `build_value_timeline` → `ValueTimeline`,
  `ValueRow.side`, `change_percent`; hodnoty = `dimensions.visit_values`, norma se
  nepoužije). Jednotka z popisu sloupce v katalogu (`_item_label`). Filtr „jen mimo
  normu“ není. **Podoby Vývoje** (31. 10. 2026, mockup
  https://claude.ai/artifact/DgxUjxJmh5SAihwZ4SkQog, `docs/mockups/vyvoj-2/` mimo git;
  přepínač „Podoba: Pavouk · Grafy“ na druhém řádku `trend_bar` pod záložkami, volba
  v `ui/trend_form` přes `set_form`/`trend_form()`/`form_changed`, `effective_form()`):
  **Grafy** (výchozí) = nahoře přehled `widgets/trend_map.py` (`TrendMap`/`MapRow`/
  `Sparkline`: název podle obsahu nejvýš `NAME_MAX`, průběh hned za ním, volné místo
  až za posledním sloupcem; řádek dimenze, buňka vyšetření podbarvená pásmem s SD, poslední tučně,
  průběh, změna; v Hodnotách řádky = položky ve skupinách s jednotkou, šedé buňky;
  klik na sloupec `visit_clicked` → `_trend_visit_clicked` otevře to vyšetření) s
  políčkem „graf“ v prvním sloupci a tlačítky vše · nic; pod ním mřížka grafů jen
  zaškrtnutých řádků (po dvou, pásma v pozadí, hodnota u bodu, celé datum do čtyř
  vyšetření `_visit_labels`). Výběr se pamatuje zvlášť pro dimenze (`ui/trend_dims`)
  a položky (`ui/trend_items`, klíč `úloha:sloupec` `_item_key`) přes
  `trend_selection_changed`/`set_trend_selection`; bez volby = dimenze hraniční nebo
  mimo normu naposledy, resp. dvě položky s největší změnou v procentech. Tabulka
  `trend_table` zůstává jen u Pavouka (název podle obsahu, `_fit_name_column`). **Pavouk** (`can_overlay`: jen SD a 2 až 3
  vyšetření; `SpiderChart.set_axes(axes, overlays)` s `Overlay`: poslední vyšetření
  černě, prostřední tmavě modře čárkovaně se čtverečky, nejstarší světle modře
  tečkovaně s kroužky a slabou výplní, legenda `spider_legend`; u jedné dimenze
  značky na pruhu) s tabulkou změn pod ním. Samostatná Mapa a „První → poslední“
  zamítnuty. Norma je v pavouku i v grafech jen tenká čárkovaná čára (silná černá
  rušila; platí i pro pavouk na záložce Vyšetření a stupnici). PNG má příponu
  `-vyvoj` nebo `-vyvoj-pavouk` (+ `-hodnoty`). Staré tlačítko se při výměně řady napřed skryje
  (`deleteLater` kreslil zbytkový modrý pruh). Vpravo hlavička
  pacienta (štítky dimenzí odstraněny 31. 10. 2026, totéž říká pavouk) s barevným
  pruhem podle nejhoršího pásma, podtitul pod jménem i tlačítky na jeden řádek
  s výpustkou (`StatusHeader(subtitle_below=True)`) a Upravit…, Uložit obrázek… (PNG pavouka),
  Smazat…, výběr normy, věkového okna a „jen stejné pohlaví“ (řádek pod tím
  říká, kolik kontrol se vybralo po úlohách); pod hlavičkou se roluje
  (`self.scroll`): pavouk přes celou šířku s legendou vedle, pod ním strom na
  celou šířku (první sloupec se roztahuje); pavouk (`widgets/spider_chart.py`,
  `SpiderChart.set_axes`; podoba = varianta I z 29. 10. 2026 večer, mockup
  https://claude.ai/artifact/9JEdgtkBexeDXjmm6ciRS4, soubory `docs/mockups/pavouk-2/`
  mimo git, varianty A až I tam zůstávají pro diskusi uživatele s týmem: patologie
  ven, střed −2 SD, silná černá kružnice 0 SD = norma, okraj +3 SD, osa lineární
  v SD; jediné vybarvené pásmo je zelený prstenec „v normě“ −1 až +1 SD se
  zelenými hranami a popiskem „+1 SD“, +2 SD čárkovaně, zbytek bílý; čára
  pacienta černá, bod a popisek (název i SD) vždy barvou pásma, dimenze nad 1 SD
  mají průsvitnou výseč od normy k bodu (nejvýš ±20°); význam kružnic říká
  svislá stupnice `ScaleLegend` vedle grafu (do PNG se exportuje výřez pavouka
  `content_rect` i stupnice, `_chart_image`, se zachovaným poměrem pixelů
  displeje; dialog Uložit obrázek… začíná ve složce výsledků); jedna osa = pruh
  s nulou uprostřed. Dřívější podoba 2b
  (28. 10., mockup https://claude.ai/artifact/Bgky5AXRhPZvjuBZnixhu7, dovnitř
  „větší = zdravější“) zůstává v kódu jako `INWARD` (`set_orientation`,
  `ui/spider_orientation`, výchozí `outward`), přepínač na stránce je skrytý
  (`orientation_combo.hide()`)), strom
  dimenze → položka (úloha, pacient, norma, z, percentil; od 31. 10. 2026 podoba A
  z mockupu https://claude.ai/artifact/9nB73DCNv65AxBRkAh2Wuh, `docs/mockups/strom-dimenzi/`
  mimo git, `widgets/dimension_tree.py`: `DimensionTree` se záhlavím stupnice `AxisHeader`,
  sloupec osy `TREE_AXIS` = 6 zobrazený před z, `DeviationAxis` = bod na společné ose
  −2 až +3 SD se zeleným pásem ±1 a nožičkou od nuly, dimenze kosočtverec, položka
  kroužek; barvu nese jen štítek pásma za názvem dimenze, bod a číslo SD, řádky bez
  podbarvení, položka navíc tečkou (`band_dot`) a názvem barvou pásma (i zelená v normě,
  bez hodnoty šedě), úloha položky šedým štítkem (`DimensionTreeDelegate`); šířku názvu a osy
  dopočítá `fit_columns` včetně sbalených položek; od 1. 11. 2026 název podle obsahu nejvýš
  `NAME_MAX` = 320 px (delší zkrácený s tooltipem, v úzkém okně až `NAME_MIN`), osa hned za ním,
  volné místo až za posledním sloupcem (dřív dostal název všechno volné místo a čísla odjela); ve výchozím
  stavu vše sbalené, „rozbalit vše“ · „sbalit vše“; strom je vysoký podle viditelných
  řádků (`fit_height`, `refit`) bez vlastního svislého posuvníku, roluje stránka),
  chybějící dimenze
  s důvodem ve sloupci osy, tabulka Nahrávky hodnocení (úloha, nahrávka, běh, s normou)
  s poznámkou o průměru, jiném protokolu a chybějících odkazech, O normě.
  Bez normy (31. 10. 2026) ukáže místo pavouka strom jen s hodnotami položek
  v jednotkách (`_fill_values_tree` přes `build_value_timeline` s jedním vyšetřením,
  sloupce norma/z/percentil skryté `_set_norm_columns`, `chart_card` skrytá),
  nahrávky a kde normu vzít. Nabídka Norma má na konci „žádná norma (jen hodnoty)“
  (`NO_NORM`), volba se pamatuje v `ui/norm` (`settings.norm_choice`: cesta normy,
  „none“, prázdné = první; `norm_choice_changed`, `set_norm_choice` před
  `set_norms_dir`); to je **výchozí** norma. Nabídka u vybraného pacienta mění jen
  jeho normu (odchylka `PatientPrefs.norm` v katalogu, `_store_norm_override`,
  `_norm_for(subject)`, `current_norm()` = norma vybraného pacienta; štítky karet
  každý podle své normy `_known_band`; „nastavit jako výchozí pro všechny“ zapíše
  i normu); bez normy jsou věkové okno a pohlaví neaktivní. **Okno a pohlaví
  platí per pacient** (31. 10. 2026, rozhodnuto variantou 1): výchozí z nastavení
  (`settings.eval_window` 0 = bez omezení, `eval_same_sex`; `set_eval_defaults`,
  `eval_defaults_changed`), odchylka pacienta v katalogu (`patients.window`,
  `same_sex`, `Catalog.patient_prefs/set_patient_prefs/clear_patient_prefs`,
  `PatientPrefs`); ovládání ukazuje vybraného pacienta (`_show_prefs`), změna
  (`_prefs_changed`) uloží odchylku a přepočítá jen jeho, protože okno a pohlaví
  jsou v otisku vyšetření (`_visit_signature`), ne v globálním klíči cache; štítek
  „platí jen pro tohoto pacienta · nastavit jako výchozí pro všechny“
  (`_make_default`: nové výchozí do nastavení, odchylky smazané). Dialog
  (`ui/assessment_dialog.py`, `AssessmentDialog`): pacient, věk, pohlaví,
  datum, pro každou úlohu nahrávky s Přidat… (`PickRecordingDialog`:
  nahoře podle manifestu pacienta, pak podle názvu souboru, ostatní jen bez
  hledání; `assessments.candidates_for`) a Odebrat; první nahrávka
  předvyplní prázdné údaje z manifestu (`assessments.prefill`, datum z
  `visit`, jinak z běhu). Na Výsledcích pravé tlačítko „Otevřít v Hodnocení“
  (`ResultsPage.assess_requested` → `MainWindow.show_assessment` →
  `show_row`) otevře dialog s tou nahrávkou; po uložení přepne na Hodnocení.
  Stránka se obnoví při zobrazení a po skončeném běhu. Vyhodnocení pacientů
  se drží v `_eval_cache` podle id hodnocení s otiskem vyšetření (údaje pacienta,
  odkazy a čas změny tabulek běhů z `VisitIndex.signature`), dokud se nezmění
  norma, okno nebo pohlaví (`_cached_evaluation`, `_lookup`, `_eval_key`);
  `refresh` cache nemaže, přepočítá se jen pacient se změněnou tabulkou
  (30. 10. 2026). Štítky karet se počítají **líně**: seznam se ukáže hned,
  karta bez vyhodnocení má „…“ (`PENDING_BAND`), `_schedule_badges` je dopočítá
  po `BADGE_CHUNK` kartách přes `QTimer.singleShot(0)` (generace zahodí tik po
  novém seznamu; `flush_badges` pro testy, `badges_pending`). Kliknutí
  na kartu tak nepřepočítává ostatní (60 pacientů dřív ~3,5 s, teď ~50 ms);
  `Selection` si cachuje hodnoty sloupců a průměry po osobách
  (`subject_means`, jeden groupby pro všechny feature).
- **Typy hodnocení** (1. 11. 2026, mockup https://claude.ai/artifact/MDbiZZGRgG5Wnyzk4GPVfv,
  `docs/mockups/hodnoceni-typy/` mimo git; rozhodnuto: vyplňuje se ručně, z názvů ani složek se nic
  neodvozuje, jen z manifestu): průvodce `AssessmentBuildWidget(mode=…)` začíná volbou typu
  (`CHOOSE`, tři karty `mode_cards`, Začít) a v hlavičce má přepínač `mode_switch`: **Jedno
  vyšetření** (`SINGLE`, formulář pacienta na stránce zdrojů, jeden krok), **Vývoj v čase**
  (`SERIES`, `ui/assessment_series.py` `SeriesEditor`: pacient z katalogu nebo nový, pohlaví,
  rok narození → věk u každého vyšetření `age_for`; mřížka datum × úloha, vyšetření z katalogu
  šedě „spočítáno“, nová s polem data (prázdné žlutě), věkem a Vybrat…/Změnit… na úlohu, ✕;
  „Načíst z manifestu…“ jen předvyplní, manifest se sloupcem `task` ve složce doplní ostatní
  prázdné úlohy téhož vyšetření; `problems()` hlídá datum i duplicitu proti katalogu; dávka na
  úlohu (od 1. 11. 2026 jedna pro všechna data, modely se načtou jednou), rok narození do katalogu `Catalog.set_birth_year` přes
  `MainWindow._remember_birth_year`, pacienti z `_series_patients`), **Víc pacientů** (`MULTI`,
  jen s manifestem, dva kroky jako dřív, víc pacientů se už nedetekuje samo). Další vyšetření
  (`prefill`) otevře Vývoj v čase. Buňky mřížky mají pevnou minimální šířku (`_polished_width`),
  sizeHint prvků v buňce styl nezná. **Protokoly ve Vývoji** (1. 11. 2026, mockup
  https://claude.ai/artifact/PiCgr7oP7N8VABhECAQ6gQ, `docs/mockups/vyvoj-protokoly/` mimo git):
  první řádek mřížky (`PROTOCOL_ROW`) má u úlohy výběr protokolu pro všechna nová vyšetření
  (u jediné možnosti jen název); výchozí = protokol posledního vyšetření pacienta s úlohou
  z `protocol.yaml` běhu (`run_protocol` ← `MainWindow._run_protocol`, `_load_previous`,
  „jako minule“), jinak výchozí přibalený („výchozí · minule nebylo“); protokol z minula je
  v nabídce i u klinika. Jiný než minule (`changed_tasks`) = oranžově s „minule: …“,
  „spočítáno ⚠“ v šedých buňkách (tooltip s protokolem), pruh `protocol_warning`
  s „vrátit jako minule“ (`revert_protocols`) a Spustit se zeptá (`confirm`). Dávky berou
  `series.protocol_for`. Jazyk průvodce se ve Vývoji přesune nad mřížku (`_place_language`,
  `head_extra`), ukáže se jen u úloh s přepisem (`needs_language`) a předvyplní se
  z minulého běhu (`previous_loaded`). **Jiný protokol ve Vývoji na Pacientech** (1. 11. 2026): `longitudinal.mark_protocols` porovná protokol úlohy (`RunInfo.protocol_name` běhů nahrávek, `_visit_protocols`) s posledním vyšetřením, které úlohu má; `VisitPoint.protocol_changes`, `flagged` = jiná verze feature nebo protokol: ⚠ u data, dutý bod (v Hodnotách jen u položek té úlohy), protokoly v tooltipu data (`_protocol_tips`) a poznámka pod tabulkou (`_protocol_notes`). Nezavedeno: otevření Vývoje po Spustit.
- **Hodnocení podle normy** (1. 11. 2026, varianta A, mockup
  https://claude.ai/artifact/PA7sSRD3cTtK13oDKcM4id, `docs/mockups/hodnoceni-podle-normy/` mimo git):
  průvodce má nahoře pruh „Srovnávat s normou“ (`norm_bar`, `norm_combo`, `select_norm`, `norm_key`,
  `selected_norm`; předvolba = norma pacienta přes `norm_key_for` ← `AssessmentPage.norm_key_for`,
  dokud ji uživatel nezmění, `_norm_touched`); protokol úlohy = protokol kontrol normy
  (`norm_build.norm_protocols(norm, installed)`: celý protokol z `NormInfo.protocol_data`, jinak
  z `plan.json` normy, jinak nainstalovaný téhož jména, jinak „chybí“), u klinika zamčený, v rozšířeném
  režimu jde přepsat s varováním (`norm_changed_tasks`, `norm_warning` s „vrátit podle normy“, Spustit
  se zeptá); úloha mimo normu výchozím protokolem s poznámkou; jazyk z `NormInfo.language` (zamčený);
  Vývoj v čase `SeriesEditor.set_norm`: ruční volba > norma > jako minule > výchozí, šedé buňky ⚠
  podle `old_differs`. Norma nově nese celé protokoly (`protocol_data` v norm.yaml i .ssnorm, plní
  `finish_plan`). Po Spustit se volba uloží jako norma pacienta (`set_patient_norm` v
  `MainWindow._remember_birth_year`), plán a hodnocení si pamatují jméno normy (`AssessmentPlan.norm`,
  `Assessment.norm`, sloupec `assessments.norm` v katalogu). **Pacienti:** nabídka norem
  `_fill_norm_combo` = normy se stejnými protokoly nahoře (✓), pod čarou ostatní „· jiné: úlohy“;
  hlavička „podle normy X“, nebo „jiný protokol než norma: …“ s tlačítkem Přepočítat podle této normy…
  (ve Vývoji „všechna vyšetření“; `norm_recompute_ids`, `recompute_norm_requested` →
  `MainWindow.recompute_by_norm` → `recompute_assessment(protocols=…, norm_language=…, norm=…)` →
  `plan_from_assessment` s protokoly normy pro její úlohy).
- **Průvodci zjednodušení** (31. 10. 2026, mockup https://claude.ai/artifact/GX5BLKd1EcRkrvUTvvvGrg,
  `docs/mockups/pruvodci-zjednoduseni/` mimo git): Hodnocení z nahrávek ve **dvou krocích**
  (31. 10. 2026 večer, mockup https://claude.ai/artifact/LqdUbs3QgkT4YPuHdsKN6z,
  `docs/mockups/hodnoceni-pacienti/` mimo git): **1 Nahrávky** jen složky, protokoly a jazyk (patička
  napoví „manifest: n pacientů · k údajů chybí“, Další › Pacienti); **2 Pacienti** vždy (rozhodnuto):
  jeden pacient = formulář (prázdný věk a pohlaví žlutě, Spustit jde i bez nich, rozhodnuto) a řádek
  „Úlohy vyšetření“ s chybějícími úlohami oranžově (`task_checks`); víc pacientů = tabulka
  **po vyšetřeních** `visits_table` (pacient × datum, věk a pohlaví jednou za vyšetření `_visit_edited`
  / `set_visit_value`, úlohy ve sloupcích s počtem nahrávek, „–“ = chybí, názvy v tooltipu), filtr
  všechna · chybí údaje (zamrazený seznam s ✓) · neúplná sada úloh (`set_visit_filter`), řádek
  celkem (`totals`), **nepřiřazené nahrávky** ve žlutém oddílu `unassigned_box` přiřazené přímo tam
  (**od 1. 11. 2026 tabulka** `unassigned_table`, mockup https://claude.ai/artifact/RXmri6zs9jpW7EhVp89Pfo, `docs/mockups/neprirazene-tabulka/` mimo git: úloha, nahrávka, pacient, datum, věk, pohlaví; úprava dvojklikem (`_UnassignedDelegate`, `_commit_cell`), výběr více řádků a Vyplnit… (`FillPeopleDialog`, `fill_selected`, `fill_unassigned`: prázdné nechá, jedna přestavba); seznam se nepřeskládává, přiřazené zůstanou se ✓ do Hotovo (`_loose_frozen`, `end_unassigned`, `unassigned()`); „datum pro všechny“ nahradilo Ctrl+A a Vyplnit…; **předvyplnění** v `_merged_people` (cache podle `_people_version`/`_people_changed`): pohlaví z jiné nahrávky pacienta nebo z katalogu, věk z téhož vyšetření nebo z roku narození v katalogu, šedě kurzívou se zdrojem v tooltipu (`_suggested`, `_mark_suggested`), ruční vyhraje; šířky sloupců obou tabulek se měří jednou na konci (ResizeToContents = 11 ms na setItem, 400 nahrávek 38 s → 0,1 s), `_recording_tasks` se drží podle prohledání složek). Oba kroky jsou v `_scrolled` (260 nahrávek bez data dřív zvětšilo minimální výšku okna přes 10 000 px). Opravy jsou
  `_overrides` po nahrávkách, jen pro tento výpočet; patička se souhrnem a Spustit · n hodnocení. Nová norma na **dva kroky**
  (1 Zdroj a úlohy, 2 Kontroly; Souhrn zrušen, `last_step` = 1): zdroj z nahrávek / z hotových běhů
  jako přepínač v řádku kroků (`folders_card`, `runs_card` = tlačítka), název velký a Zdroj
  s Podmínkami vedle sebe v `about_box`, jazyk u nadpisu Úlohy, varování pod tabulkou kontrol
  (`warnings`), souhrn a Spustit/Sestavit/Přidat v patičce (`_fill_summary`, hint).
- **Hodnocení z nahrávek** (mockup https://claude.ai/artifact/EELCDHfXjB8aHzdxxV2DJ5,
  `docs/mockups/hodnoceni-z-nahravek/` mimo git): Nové hodnocení… se nejdřív
  zeptá (`NewAssessmentChoiceDialog`: dvě klikací karty `ChoiceCard` z nahrávek /
  z hotových běhů, role karty `option`/`chosen` v theme, dvojklik potvrdí;
  z Výsledků rovnou z hotových). Průvodce `ui/assessment_build_dialog.py`
  (`AssessmentFromRecordingsDialog`): pacient, věk, pohlaví, datum, jazyk a
  složka s nahrávkami na úlohu (sdílí `TaskRow` s Novou normou; složka
  s `manifest.csv` se sloupcem `task` vyplní i ostatní úlohy a údaje
  pacienta přes `assessment_build.split_by_task` a `patient_from_scan`),
  pak souhrn dávek. Výsledek `AssessmentPlan` (`backend/assessment_build.py`,
  `assessments/<id>.plan.json`, `AssessmentStore.all` tyto soubory
  přeskakuje) jede obecnou cestou plánů v hlavním okně (`Job.plan_kind`
  „assessment“, `start_plan`, `build_plan`, `resume_plan`,
  `discard_assessment_plan`; stav dávek `norm_build.PlanState`); dávky mají
  manifest se `subject`, `age`, `sex`, `visit`, takže běhy jsou běžné běhy
  pacienta (ne `RunInfo.norm`). Po poslední dávce `finish_plan` založí
  hodnocení z řádků tabulek (chybné nahrávky vynechá), smaže plán a
  `_after_batch_page` přepne na Hodnocení místo Výsledků. Rozpracované
  hodnocení je na stránce jako `PendingCard` („počítá se k z n“) s detailem
  dávek a Dopočítat zbývající, Založit z hotových, Zahodit…. **Víc pacientů
  a dat najednou** (30. 10. 2026, mockup
  https://claude.ai/artifact/UkcxmVZDY3M9n89UQFuhdq artboard 4, rozhodnuto: jen
  manifest, ze struktury složek se nic neodvozuje): když `manifest.csv`
  (sloupce pacient, datum, úloha, věk, pohlaví) dá víc pacientů nebo dat
  (`assessment_build.is_multi`, `people_from_scans`, `normalize_visit` bere
  ISO i „14. 10. 2026“), průvodce schová pole pacienta a ukáže tabulku
  nahrávek (`people_table` = `MetaTable` s českými hlavičkami a povinnými pacient
  a datum; prázdné dopsat dvojklikem, jen pro tento výpočet, `set_person`,
  úpravy přežijí přeskenování, `_merged_people`), dávka je od 1. 11. 2026 **jedna na úlohu**
  pro všechny pacienty a data (každá dávka = nový proces knihovny a nové načtení Whisperu
  a Stanzy; dřív úloha × datum, `TaskPlan.visit` a klíč `úloha@datum` zůstávají pro starší plány), `AssessmentPlan.people`
  (nahrávka → `PersonRow`) jde do manifestu dávky, běhy zůstávají mezi
  analýzami (`Job.plan_group=False`, v `run.json` bez `assessment`), po
  poslední dávce `finish_plan` vrátí seznam vyšetření po (pacient, datum),
  řádek bez data se spočítá, ale nezaloží. „Uložit šablonu manifestu…“
  (`write_manifest_template`) zapíše CSV se seznamem nahrávek, kam uživatel
  ukáže. Jeden pacient a jedno datum = dnešní cesta beze změny. Zbývá: PDF,
  vývoj v čase, složka s víc pacienty.
- **Běhy hodnocení v historii** (mockup https://claude.ai/artifact/FMkyX9ddZ6WNv2kMoXTSYt,
  `docs/mockups/historie-filtr/` mimo git, rozhodnuto 28. 10. večer): dávky
  hodnocení mají v `run.json` `assessment` (id) a `patient` (`Job.subject`,
  `MainWindow._plan_run_fields`). Historie Výpočtu i Výsledků má přepínač
  Analýzy · Hodnocení · Normy (`RunHistory.set_filter`, `RunInfo.kind`,
  počty v tlačítkách; výchozí Analýzy, společný oběma stránkám a uložený
  v `ui/history_filter`); výběr běhu z jiné skupiny (`select_dir`,
  `refresh(select=…)`, tedy i po skončené dávce) filtr přepne sám. Běhy
  jednoho hodnocení jsou jedna karta (`history.RunGroup` z `group_runs`:
  pacient, štítek „hodnocení“, souhrnný stav, „k z n úloh hotové · DDK běží ·
  čeká: čtení“); dokud existuje plán hodnocení, dostane `group_runs` jeho
  úlohy (`RunHistory.set_plans_lookup` ← `MainWindow._pending_assessment_tasks`),
  takže karta hlásí „počítá se“ i mezi dávkami a zná úlohy bez běhu
  (`RunGroup.planned`, `waiting_tasks`, v řadě úloh Výsledků jako neaktivní
  „Čtení · čeká“); historie se obnoví i při spuštění dávky (`_launch`) a po
  dávce až po zápisu stavu do plánu. `selected` nese skupinu, Výsledky ukážou řadu úloh nad tabulkou
  (`ResultsPage.task_bar`, `_show_group`, `open_run(run_dir)`) a tlačítko
  Otevřít v Hodnocení (`open_assessment_requested` → `MainWindow.open_assessment`),
  Výpočet přehraje běžící, jinak nejnovější běh (`RunGroup.latest`). Smazat
  smaže všechny běhy vyšetření (`_delete_group`), Přejmenovat skupina nemá.
  Po založení hodnocení se ploché běhy přesunou do jedné složky
  `<datum>_<čas>_hodnoceni-<pacient>\<fonace|ddk|cteni|spontanni-rec>\`
  (`merge_assessment_runs`: nahoře `run.json` s `group`, podsložky jsou úplné
  běhy, `list_runs` je vrací s `group_dir`, datum berou ze složky vyšetření);
  když přesun nejde (otevřený Excel), vrátí se zpět a zkusí se při příštím
  otevření Výsledků (`_merge_finished_groups`, přeskočí rozpracované plány
  a běžící dávky). Odkazy na běh (`RecordingRef.run`, `Recording.ref`,
  `AssessmentStore.relocate`) jsou `RunInfo.key`: název složky, u vnořeného
  `<vyšetření>/<úloha>` (`history.run_key`); `_run_moved` přepíše i
  `_running_out`, aby `_batch_finished` po sloučení našel tabulku. Na
  Hodnocení je dvojklik na nahrávku detail hodnot (přehrání v nabídce) a
  v nabídce „Otevřít běh ve Výsledcích“ (`open_run_requested` →
  `MainWindow.open_run`). Běhy norem zůstávají ploché, jen pod filtrem Normy.
- **Stránka Normy** (`ui/pages/norms.py`, `NormsPage`; mockup schválený 28. 10.
  je artefakt https://claude.ai/artifact/XTwhcSUAjC5vUuq5MPMZzn, soubory
  `docs/mockups/normy/` mimo git): vlevo karty norem (`NormCard`, vlastní
  napřed, štítek varování z `norm_problems`), vpravo hlavička s Export…
  a Smazat…, varování, tabulka Kontroly po úlohách (`norms.task_summaries`:
  osoby jednou, ženy / muži, věk, sloupce s hodnotou), histogram věku
  (`age_histogram`, `AgeHistogram`) a O normě. Export = jeden soubor
  `.ssnorm` (zip s norm.yaml a controls.csv, `export_norm`); import ho čte
  z paměti (`read_norm_file`, nic se nerozbaluje) a uloží vedle pod
  `unique_norm_dir`, nikdy nepřepíše; Smazat jde do Koše (`trash_norm`),
  přibalená se nemaže. Po importu a smazání `norms_changed` →
  `AssessmentPage.reload_norms`. Běhy dávek normy mají v `run.json` `norm`
  (složka plánu, `RunInfo.norm`): v historii Výpočtu i Výsledků karta se
  štítkem „norma“ a „kontroly normy“ (`RunCard.norm_pill`), index nahrávek
  pro Hodnocení je přeskočí.
- **Nová norma** (`ui/new_norm_dialog.py`, `NewNormDialog`, backend
  `backend/norm_build.py`): 1 název, zdroj, podmínky, jazyk nahrávek a pro
  každou úlohu složka (bez podsložek, pokud se nezaškrtne) a protokol
  (`DEFAULT_PROTOCOLS`, spontánní řeč i s lingvistikou kvůli dimenzi Jazyk;
  jiný jen v rozšířeném režimu; jazyk přes `with_language` jako na Analýze),
  2 jedna tabulka kontrol pro všechny úlohy: kontrola z názvu souboru
  (`SUBJECT_RULES`) nebo z `manifest.csv` složky, věk a pohlaví ručně nebo
  z CSV (`read_controls_csv`), 3 souhrn s odhadem podle
  `seconds_per_audio_minute`. Výsledek `NormPlan` se uloží jako `plan.json`
  do budoucí složky normy (`unique_norm_dir`); bez `norm.yaml` je norma
  rozpracovaná (`pending_plans`, `PlanCard` na stránce Normy). Hlavní okno
  (`new_norm`, `start_norm`, `_submit_all` = jedna otázka na frontu) zařadí
  jednu běžnou dávku na úlohu (`Job.norm_plan`, manifest z tabulky kontrol přes
  `NormPlan.manifest_for`, takže `features.csv` má subject, age, sex); stav
  dávky jde do plánu v `_launch` a `_batch_finished`. Po poslední dávce bez
  chyby `finish_plan` sestaví normu z tabulek běhů a zapíše
  `NormInfo.protocols` (úloha → protokol) a verzi knihovny. Rozpracovaná
  norma má Dopočítat zbývající (`resume_norm`, přeskočí, co běží nebo čeká),
  Sestavit z hotových (`build_norm`) a Zahodit… (`discard_norm`, plán do
  Koše, běhy zůstávají). Do složek s nahrávkami se nic nezapisuje.
- **Norma z hotových běhů a Přidat kontroly** (31. 10. 2026, mockup
  https://claude.ai/artifact/VtrL28QPS7Hz2ydEWWGC25, `docs/mockups/normy-porovnani-dimenze/`
  mimo git, část 1): průvodce má v kroku 1 karty Z nahrávek · Z hotových běhů (bez kolečka
  přepínače, jen modré zvýraznění, `ChoiceCard` stejně i v Novém hodnocení; detail na Normách je v posuvné ploše
  s minimální šířkou podle obsahu, tlačítka hlavičky mají minimální šířku podle textu)
  (`set_source(RUNS)`, `set_runs(task, runs)` přes `PickRunsDialog`, tabulka `runs_table`
  úloha · běhy · protokol (štítek „2 protokoly“) · nahrávek · kontrol podle živého `scan_runs`,
  filtr „Jen řádky, kde sloupec = hodnota (n z m)“ `set_group_filter`; kroky 1 a 3 jsou
  v `QScrollArea`, jinak minimální výška okna přetekla obrazovku a Windows hlásily
  `QWindowsWindow::setGeometry`; `PickRunsDialog` má přepínač Analýzy · Hodnocení (výchozí
  Analýzy, rozhodnuto: hodnocení jsou většinou pacienti), hledání podle běhu i pacienta,
  Vybrat vše · Nic pro viditelné řádky; souhrn varuje, když je kontrola zároveň pacientem
  z katalogu (`patients`, `patients_in_norm`); běh bez sloupce pacienta dá kontrolu z názvu
  souboru podle pravidla z kroku 2 (`RunRow.from_name`); běhy z `MainWindow.norm_source_runs`: hotové výpočty
  feature, ne normy, ne jen segmentace či přepis). Rozhodnuto: jiný protokol nebo verze feature
  mezi běhy jedné úlohy jen varuje (`runs_problems`). Backend: týž `NormPlan`, každý zdrojový
  běh je dávka OK s `run_dir`, víc běhů jedné úlohy = `TaskPlan.part` (klíč `úloha#n`,
  `task_of_key`); `plan_from_runs`, `scan_runs` (novější běh vyhrává u téže nahrávky),
  `controls_from_scan`. `finish_plan` bere jen řádky z `NormPlan.files` podle sloupce `path`
  (`_select_rows`; tabulka bez jediné nahrávky plánu = starší běh, zůstane celá), věk a pohlaví
  z tabulky kontrol přebijí metadata, duplicitní nahrávka = pozdější dávka. Plán bez dávek
  k výpočtu `start_norm` rovnou sestaví. **Přidat kontroly…** na Normách (`add_controls_requested`
  → `MainWindow.add_controls`, týž průvodce s `extend=plan`, jen dva kroky jako v mockupu 1b:
  protokoly normy, tabulka kontrol se štítkem po úlohách nová · už v normě · přeskočí se ·
  nahradí se (`addition_state`) a nad ní přeskočit · nahradit `set_replace`, `extension()`)
  → `extend_plan`: dávky
  s `added` a `replaces`; z běhů se norma sestaví hned, z nahrávek se zařadí jen přidané dávky
  (`start_plan(only=…)`), norma je do sloučení rozpracovaná („přidávají se kontroly“,
  `NormPlan.extending`, `complete` nečeká na starší spadlé dávky), Zrušit přidání… =
  `cancel_recompute` přidané dávky zahodí. Na Pacientech štítek „jiný protokol než norma: úlohy“
  v hlavičce (`protocol_mismatch`, oba protokoly v tooltipu).
- **Přepočet normy** (30. 10. 2026): `plan.json` zůstává ve složce normy i po
  sestavení (do `.ssnorm` nejde, importovaná a přibalená norma přepočet nemá).
  Na Normách Přepočítat… (`recompute_requested` → `MainWindow.recompute_norm`),
  aktivní jen když plán existuje a nahrávky jsou na disku
  (`NormPlan.missing_inputs`, důvod v tooltipu). `reset_for_recompute` vrátí
  dávky na „čeká“ bez `run_dir` a nastaví `NormPlan.recomputing`; dokud platí,
  `pending_plans` normu vrací jako rozpracovanou (karta „přepočítává se k z n“,
  stránka ji neukazuje dvakrát), stará `norm.yaml` zůstává použitelná a
  `finish_plan` ji po poslední dávce přepíše (vlajku zruší, `created` = dnešek).
  Zahodit… je u přepočtu „Zrušit přepočet…“: `discard_norm` zavolá
  `cancel_recompute` (nespuštěné dávky zrušené, norma beze změny), nic do Koše.
- **Přepočet hodnocení** (30. 10. 2026): na Hodnocení Přepočítat…
  (`recompute_requested(id)` → `MainWindow.recompute_assessment`) pro každé
  hodnocení, i založené z hotových běhů: `assessment_build.plan_from_assessment`
  složí plán s id hodnocení z cest nahrávek v tabulkách běhů a `protocol.yaml`
  každého běhu (včetně jazyka); chybějící nahrávka, běh nebo protokol = tlačítko
  neaktivní s důvodem (`AssessmentPage.recompute_plan`). Před spuštěním
  `history.retire_assessment_runs` vyprázdní `assessment` v `run.json`
  dosavadních běhů (`former_assessment` zůstane), takže v historii spadnou mezi
  analýzy a karta hodnocení patří novým dávkám; jinak by `group_runs` míchal
  staré a nové běhy jedné úlohy. Během výpočtu je v seznamu místo karty
  hodnocení `PendingCard` téhož id (`_fill_list` kartu hodnocení skryje),
  Zahodit… vrátí staré hodnocení; `finish_plan` ho pak přepíše na místě
  (stejné id, nové odkazy), běhy se sloučí do nové složky vyšetření.
- **Vlastní úlohy** (`backend/tasks.py`, rozhodnuto 29. 10. 2026 večer, mockup
  https://claude.ai/artifact/7tDEUpd2eP7RVwrXdPG5vz, `docs/mockups/vlastni-uloha/` mimo
  git): `CustomTask` = klíč (slug názvu, `registry().new_key`), název, `base` = úloha
  knihovny, jako která se počítá, výchozí protokol (jde změnit jako u ostatních
  řádků) a pokyn pro pacienta. Knihovna dostává vždy jen `base` (protokol je
  protokol základní úlohy, `work\<base>`); vlastní klíč žije v GUI: `TaskPlan.task`
  a `Job.plan_task`, `run.json` dávky (`task`, `RunInfo.task` ho upřednostní před
  `protocol.yaml`), index nahrávek (`VisitIndex`: celý běh s vlastním klíčem),
  sloupec `task` v `controls.csv` normy (`finish_plan` přepíše základní úlohu
  z `features.csv`), `NormInfo.protocols` a odkazy hodnocení. Registr
  (`tasks.registry()`, `tasks.json` v datech aplikace, `settings.tasks_file()`)
  načte hlavní okno při startu a hned k němu přidá úlohy ze všech `norm.yaml`
  (`norms.register_custom_tasks`, dřív než se čtou běhy; `NormInfo.from_dict`
  registruje jen do paměti, průvodce ukládá se `save=True`); `registry().version`
  je v klíči cache indexu, aby nová úloha překlasifikovala běhy. Popisky všude přes
  `tasks.label` / `long_label` / `task_order` (ne `contract.TASK_SHORT`), manifest
  bere i vlastní klíče. Zakládá se jen v průvodci Nová norma („+ Přidat vlastní
  úlohu…“ → `ui/custom_task_dialog.py`, řádek `TaskRow(custom=…)` s podtitulkem
  a ×); Hodnocení z nahrávek a dialog Nového hodnocení nabízejí všechny vlastní
  úlohy z registru za úlohami knihovny; Analýza je nenabízí (rozhodnutí
  uživatele). Dimenze se vážou na úlohu knihovny: `evaluate` bere vlastní úlohu
  jako další zdroj jejího `base` (`_item_tasks`: položka „F0 SD · Popis obrázku“
  vedle „· Čtení“), kontroly se vybírají podle vlastního klíče, norma bez něj dá
  položce důvod. Vlastní osa v pavouku zatím není (šlo by klíčem v
  `dimensions.yaml`). Testy `tests/test_tasks.py`, průvodce v `test_norms_page`,
  dialog a strom v `test_assessment`; `conftest` registr před každým testem maže.

## Údaje nahrávek k hotovému běhu (1. 11. 2026, varianta A)
- Výsledky › Hodnoty › **Údaje nahrávek…** (`ResultsPage.meta_btn`, `edit_metadata`; ne u běžícího běhu
  ani bez tabulky) otevře `ui/run_meta_dialog.py` `RunMetaDialog`: `MetaTable` jako v kroku 1 Analýzy
  (dvojklik, + Sloupec, Vyplnit vybrané…), Načíst manifest… (`read_manifest(keep_missing=True)`:
  řádky i bez souboru vedle manifestu), Uložit manifest…, Vrátit původní tabulku. Backend
  `backend/run_meta.py`: `read_table` čte `features.csv` jako text (hodnoty feature se přepíší přesně
  beze změny), `match_manifest` páruje podle cesty, pak podle jedinečného názvu souboru,
  `apply_metadata` zapíše změněné buňky (nové sloupce před první feature, kanonické názvy,
  `meta.normalize_value`; `task`, `path`, `file`… chráněné `PROTECTED`), poprvé zálohuje
  `features.puvodni.csv`, nahrazuje přes dočasný soubor a `os.replace` (otevřený Excel = OSError
  a hláška); `restore_original` zálohu vrátí. Porovnání, Hodnocení a Norma z hotových běhů i index
  nahrávek čtou tutéž tabulku, takže údaje vidí bez další úpravy (katalog podle otisku souboru).
- **Výběr v tabulce metadat** (1. 11. 2026): lišta hledání / Seskupit / Metadata ▾ v kroku 1 Analýzy
  zůstává vždy, „vybráno n · Vyplnit… · Odebrat · ✕“ (`selection_bar`) se přidá vpravo až od dvou
  řádků (jeden vybraný úpravou buňky lištu neschová; Vyplnit… je i v pravém tlačítku); `MetaTable`:
  Esc a klik do prázdna zruší výběr, psaní do buňky začne úpravu (AnyKeyPressed), Enter po úpravě
  posune kurzor na buňku pod ní (`_editor_closed` na signálu delegáta, slot `closeEditor` přepsaný
  v Pythonu Qt nevolá; `move_down` přeskočí nadpisy skupin a skryté řádky).
- **Odebrat jeden sloupec** (1. 11. 2026, varianta 1 + 2): `MetaTable` má pravé tlačítko na záhlaví
  sloupce metadat (`_header_menu` → `request_remove`, dotaz `confirm_remove`) a `remove_column`:
  přidaný sloupec zmizí i s hodnotami, sloupec ze základu (manifest) jen v `_removed` (soubor se
  nemění, nejde knihovně; `removed_columns`, `restore_columns`, Zahodit úpravy vrátí). Analýza:
  Metadata ▾ › Odebrat sloupec › a Vrátit odebrané sloupce (n); sloupec úloha s dotazem (vrátí režim
  jedné úlohy, `_confirm_remove_column`). Údaje nahrávek: + Sloupec › Odebrat sloupec, po Uložit do
  výsledků se smaže i z `features.csv` (`apply_metadata(remove=…)`, jen sloupce údajů, se zálohou).

## Porovnání skupin (31. 10. 2026)
- Mockup https://claude.ai/artifact/LaQwfCHYfEx4kptyEDoWk3 (`docs/mockups/porovnani-skupin/`
  mimo git). Na Výsledcích Ctrl/Shift+klik na 2 a víc běhů → lišta „vybráno n · Porovnat…“
  (`RunsPage.compare_bar`, `compare_problem`: jen hotové běhy výpočtu, stejná základní úloha,
  ne vyšetření pacienta); jeden běh se sloupcem skupiny přes pravé tlačítko „Porovnat
  skupiny…“. Porovnání je třetí obrazovka `RunsPage` (`open_compare`, `in_compare`, Esc zpět),
  obsah `ui/pages/compare.py` (`ComparePage`), výpočet `backend/compare.py`; nic se neukládá,
  Export CSV… s metodou v řádcích `#`.
- Rozhodnuto: skupiny **podle běhu** (výchozí), podle **sloupce metadat** jen ručně (hodnoty
  napříč běhy, spojí se podle hodnoty, zaškrtávátka s počty, pod 3 osoby nezaškrtnuté);
  **průměr po osobě** výchozí (bez `subject` po nahrávkách, štítek); metoda
  **neparametrická** výchozí (Mann-Whitney / Kruskal-Wallis, Cliffovo δ / ε², hodnoty
  medián), **parametrická** (Welchův t-test / Welchova ANOVA, Hedgesovo g / η², průměr),
  **podle normality** (Shapiro-Wilk ve všech skupinách, u q značka testu); normalita vždy
  ve sloupci; FDR (Benjamini-Hochberg) přes feature, u víc skupin párová porovnání s FDR
  v rámci feature; jen společné feature (`only_in` ve štítku), jiná verze feature = ⚠;
  pod `MIN_N` = 3 bez testu; chybné a nepoužitelné nahrávky vynechané (vypínač
  „I nepoužitelné“). Řazení podle |efektu|.
- Nová závislost **scipy** (rozhodnuto 31. 10. 2026). Při spuštěné aplikaci `uv add`
  neprojde (zamčený `speechscope-app.exe`): `uv add --no-sync` + `uv pip install`, testy
  `uv run --no-sync`. V instalátoru ověřit, že PyInstaller scipy přibalí (hook má).
- Dlouhé názvy skupin (běhy) jsou v tabulce, grafu a panelu písmena A, B, C (`short`,
  `SHORT_MAX`), celý název v hlavičce; stejné názvy běhů odliší datum (`run_names`).
  Sloupec feature bere zbylé místo a zkrátí se, n je v tooltipu buňky; panel popisuje
  skupiny pod sebou (úzký). Testy `tests/test_compare.py`.
- Úprava 1. 11. 2026 (mockup část 4 a 5): buňka feature jako v Hodnotách „feature · **sloupec**
  jednotka“ (`feature_parts`, `feature_label`; obecné jméno feature jako `stats` dostane i
  skupinu, `GENERIC_FEATURES`), popis z knihovny jen v tooltipu a pod nadpisem panelu; rich text
  přes buňku, text položky prázdný a skutečný v `TEXT_ROLE` (výběr řádku jinak prosvítal).
  Přepínač **Řadit: podle efektu · podle feature**: podle feature nadpis feature přes celou šířku
  (`setSpan`, `_row_items`, pořadí feature = nejmenší pořadí jejích sloupců v katalogu). Panel
  z widgetů: krabice s „n · medián“ vpravo, test jedním řádkem (p/q přes `html.escape`, jinak
  „<0.001“ zmizí jako značka), efekt s pruhem a slovem, tabulka skupin se dvojicí podle metody
  (medián a IQR, nebo x̄ a SD; druhá v tooltipu, čtyři čísla se do panelu nevešla), normalita
  štítky, párová porovnání; tabulky v panelu `_narrow` (bez vodorovného posuvníku, šířku určuje
  panel). Úprava 1. 11. 2026 večer: záhlaví „● A“, „● B“ v tabulce barvou skupiny jako v grafu
  (`clean_table.ColorHeader` kreslí text barvou z `setForeground` záhlaví, styl tabulky ji jinak
  přebije); u krabice v grafu jen „n 6“, medián je v tabulce skupin pod grafem.
- **Zjednodušeno 1. 11. 2026 večer** (mockup https://claude.ai/artifact/EAcPNALzd8xARofqmr3t3F,
  `docs/mockups/porovnani-zjednoduseni/` mimo git; panel vpravo beze změny, rozhodnutí uživatele):
  hlavička = karty skupin barvou grafu (`_set_cards`: zkratka, n osob nebo nahrávek, celý název) a věta
  „Liší se k z n feature · test, po osobě…, korekce FDR · změnit“ (`verdict`, štítky jen v jedné skupině,
  vynechané a sdílené osoby `_set_notes`); zřídka měněné volby v **Nastavení ▾** (`open_settings`,
  `settings_panel` v QMenu přes QWidgetAction, vlastní pozadí; nápověda metody `METHOD_HINTS`); nad
  tabulkou jen „Liší se (k) · Všechny (n)“ (`only`, výchozí Liší se, když se něco liší, `_only_auto`)
  a hledání; řazení klikem na záhlaví feature / rozdíl (`_header_clicked`, `sort` skrytý, šipka přes
  sort indicator); tabulka bez sloupce normalita, rozdíl s pruhem a slovem, q se zelenou tečkou.
- **Skupiny podle sloupce viditelně** (1. 11. 2026): v hlavičce Porovnání řádek „Skupiny: běhu ·
  sloupce [group ▾] a zaškrtávátka hodnot“ (`groups_row`, jen když běhy mají sloupec skupin
  `group_columns`; Nastavení ▾ má jen metodu, po osobě, log, nepoužitelné); v Hodnotách tlačítko
  Porovnat skupiny… (`ResultsPage.groups_btn`, `_update_groups_btn`, tooltip „group: PD 12 · HC 10“,
  `compare_groups_requested` → `RunsPage._compare_groups` → `open_compare([běh])`).

## Kontrola kvality nahrávek (31. 10. 2026)
- **Knihovna měří, GUI soudí.** Knihovna má od 31. 10. 2026 feature
  `acoustic.recording.duration` (`total`, `voiced` = součet znělých rámců SWIPE,
  `voiced_fraction`), `acoustic.recording.snr` (`snr_db` = 90. − 10. percentil
  energie rámců, `noise_db`, `speech_db`), `acoustic.recording.clipping`
  (`fraction` v %, `peak_dbfs`) pro všechny úlohy bez provideru a
  `acoustic.ddk.count` (`syllables`, `triplets`, `analyzed`; vydá se i pod
  `min_syllables`). Verze knihovny zůstala 0.4.0, fixtury falešky obnovené.
  GUI je přidá ke každému běhu výpočtu feature (`quality.quality_features`
  → `Protocol.to_request(extra_features=…)` v `MainWindow._launch`; protokol
  s doménou se přepíše na vzor `doména.*`, protože knihovna doménou jen
  filtruje zadaný seznam; se starší knihovnou bez nich se nic nepřidá).
  `protocol.summarize` je nepočítá (nejsou v protokolu). Skupina
  `acoustic.recording` = „Akustika · nahrávka“ v `GROUP_LABELS`.
- **Prahy** jsou v `assets/quality.yaml` (`backend/quality.py`: `Check`
  s `column`, `tasks`, `kind` min|max, `warn` žlutá, `bad` červená; vlastní
  úloha se kontroluje podle `base`): fonace délka i znělá část < 5 s (bad < 2),
  DDK trojic /pataka/ < 5 (bad < 3), čtení délka < 20 s (bad < 10), spontánní
  délka < 60 s (bad < 30) a slov < 100 (bad < 50), všude SNR < 20 dB (bad < 10)
  a přebuzení > 0,1 % (bad > 1 %). Rozhodnuto: prahy výchozí a **společné pro
  celou aplikaci**, mění se v dialogu Prahy kontroly kvality (`ui/quality_dialog.py`,
  `QualityThresholdsDialog`: žlutý a červený práh a vypínač `Check.enabled` po
  úlohách, upravená hodnota podbarvená s výchozí vedle, Obnovit výchozí; z nabídky
  Aplikace, z Nastavení řádkem Kontrola kvality a z dialogu Kontroly a kvalita);
  uloží se jen odchylky (`quality.save_checks` → `overrides` v `settings.quality_file()`,
  žádná odchylka = soubor smazán; starší ruční kopie s `checks` nahradí celé),
  `validate` hlídá pořadí prahů, `MainWindow.quality_changed` překreslí Výsledky,
  Pacienty, Normy a varování na Analýze, nic se nepřepočítává. `assess(row, task)` vrátí
  `Issue` (horší napřed; poznámka knihovny `notes` = `warn` „bez hodnoty“),
  `level_of`, `summary`, `min_duration(task)` pro kontrolu z hlavičky. Nic se
  nevyřazuje samo, jen se označí. Očekávaný počet slov textu čtení GUI nezná
  (rozhodnuto).
- **Kde se ukazuje:** Analýza krok 2 = žlutý řádek pod úlohou s nahrávkami
  kratšími než práh úlohy podle hlavičky (`BatchPage.short_warning`,
  `short_recordings`), Spustit se zeptá; Výsledky › Hodnoty = štítek stavu
  „kvalita“ (warn) / „nepoužitelná“ (bad, oranžový řádek `BAD_ROW`) s důvody
  v tooltipu (`ValuesModel(quality=…)`, `quality_level`), štítky v hlavičce
  „n podezřelých“ / „n nepoužitelných“ a filtr „jen problémové“ je bere;
  Pacienti = sloupec „kvalita“ v tabulce nahrávek s poznámkou pod ní a ⚠ u
  položek stromu z takové nahrávky (`_task_quality`, tooltip).
- **Kvalita kontrol normy** (31. 10. 2026, mockup
  https://claude.ai/artifact/ALTCFxkegtMF7zTpGrxb15, `docs/mockups/kvalita-normy/` mimo
  git; rozhodnuto: nic samo, vyřazuje se **nahrávka** (kontrola × úloha), ne osoba):
  `controls.csv` sloupce kvality nese, prahy se použijí při zobrazení
  (`norms.control_quality`, `quality_problems`, `has_quality_data`). Vyřazení je
  `NormInfo.excluded` (`Excluded`: subject, task, reason) v `norm.yaml` i v exportu;
  řádky v `controls.csv` zůstávají, výběr kontrol, `has_column`, `norm_problems`
  a `task_summaries` (`excluded`) berou `Norm.active`; `set_excluded`. Přepočet
  normy (`finish_plan`) vyřazení převezme pro nahrávky, které v nové tabulce jsou.
  Stránka Normy: štítek „kvalita: n“ na kartě (nevyřazené problémy), rámeček
  `quality_box` s výčtem a tlačítkem Kontroly a kvalita… (`edit_quality`,
  `make_quality_dialog`, `save_excluded` → `norms_changed`), sloupec „vyřazeno“.
  Dialog `ui/norm_quality_dialog.py` (`NormQualityDialog`: jen problémové / všechny,
  filtr úlohy, políčko použít, Vyřadit nepoužitelné = jen červené, `excluded()`);
  přibalená a importovaná norma (`Norm.editable` = má `plan.json`) jen ke čtení. Faleška
  dává sloupcům kvality pevné hodnoty (`fake.cli.QUALITY_VALUES`), název se
  „short“ / „noisy“ / „clipped“ práh podleze (`QUALITY_STEMS`).

## Pasti z knihovny, které GUI hlídá
- Podproces vždy s `PYTHONUTF8=1` (`library.subprocess_env`). Bez toho
  knihovna při přesměrovaném stdout na Windows spadne na cp1252.
- Vždy posílat `--models-dir` a `--log-file`.
- **Nahrávky nikdy na příkazovou řádku** (31. 10. 2026): Windows ji omezují na 32 767 znaků
  a dávka se stovkami nahrávek v hlubší složce se vůbec nespustila (QProcess -1, v historii
  „chyba“ bez tabulky; 500 cest po ~75 znacích). `MainWindow._launch` píše vždy
  `manifest.csv` do složky běhu (`write_normalized`: absolutní cesty, úloha, případná
  metadata) a knihovně předá `--manifest` a jako cestu jen složku běhu, u `extract`
  i `segment`/`transcribe`. Knihovna z cesty bere jen základ relativních cest.
- **Průběh se přehrává líně** (31. 10. 2026): otevření běhu na Hodnotách už nepřehrává
  `events.jsonl` (stálo ~0,2 s z každého kliknutí u 500 nahrávek); `RunsPage._defer_recorded`
  si běh zapamatuje a `set_tab(PROGRESS)` ho přehraje (`_show_pending_recorded`). Štítek
  výsledku v tabulce Průběhu kreslí `clean_table.PillDelegate` z textu a `PILL_ROLE`, ne
  widget v buňce. Kliknutí na běh s 500 nahrávkami: 0,7–1,3 s → 0,15–0,3 s.
- **Přechod na krok 2 Analýzy** (31. 10. 2026): pole složky spouští `rescan` jen při změně
  textu (`_folder_edited`), `editingFinished` přišel i při ztrátě zaměření na Pokračovat a celá
  složka se prohledávala znovu; `manifest._key` (`resolve()`) má `lru_cache`, bylo 8 volání na
  nahrávku; `MetaTable` hledá řádek přes `_index`. 260 nahrávek: 0,64 s → 0,04 s.
  `MetaTable` při přestavbě neměří šířky průběžně (sloupce Interactive, `resizeColumnToContents`
  jednou na konci; s ResizeToContents 1,35 milionu měření) a změna buňky přepíše jen ji
  (popisek úlohy přes `display`, `_mark`), ne celou tabulku: změna úlohy u 260 nahrávek
  10,6 s → 0,07 s, Vyplnit vybrané 0,15 s.
- **Okamžitá odezva kliknutí** (31. 10. 2026, varianta A bez vláken): `RunsPage.open_row`
  (klik, Enter, ‹ ›) hned přepne na detail s `ResultsPage.show_loading` („Načítám…“)
  a běh otevře až v dalším kroku smyčky (`QTimer.singleShot(0)` → `_finish_open`);
  `_open_generation` zahodí starší klik i otevření po návratu na přehled. Ostatní cesty
  (`open_entry` z Pacientů, `open_run` po dávce, `show_running`) zůstávají synchronní.
  Testy po `open_row` čekají `qtbot.waitUntil(lambda: not runs.loading())`. Načítání
  na pozadí ve vlákně (varianta B) jen podle měření z kliniky.
- **Líný start** (31. 10. 2026, krok 1 rychlejšího startu): skryté stránky se při startu
  neplní, `_apply_folders` předá `load=`/`scan=` jen u právě zobrazené stránky
  (`AssessmentPage.set_assessments_dir(load=…)`, `NormsPage.set_norms_dir(load=…)`,
  `EnvironmentPage.set_work_dir(scan=…)`); naplní je `_page_shown` jako dřív, dotazy odjinud
  (`count`, `current`, `select_assessment`, `recompute_plan`…) přes `_ensure_loaded`. Start ve
  vývoji 13 s → 5,4 s, z toho 3,7 s synchronní dotazy na knihovnu.
- **Úvodní okno a souběžné dotazy** (31. 10. 2026, místo cache katalogu, ta zamítnuta: nic
  se nesmí dodatečně překreslovat): `main.py` ukáže hned `ui/splash.py` („Načítám…“, 0,25 s)
  a před importem hlavního okna zavolá `Library.prefetch()`: `list --json`,
  `list --providers --json` a `models list --json` běží ve vláknech souběžně (1,9 s místo
  3,7 s) a `Library.run` si hotový výsledek vezme podle celé příkazové řádky
  (`_prefetched`), místo aby pustil nový podproces. Hlavní okno se ukáže celé hotové
  (`splash.finish`). Start do okna 6,5 s → 3,8 s ve vývoji. V exe zvážit i splash
  PyInstalleru ze spouštěče.
- **Časy startu** (`backend/startup_log.py`): každý start připíše řádek do
  `settings.startup_log()` (`startup.log` v datech aplikace, posledních 200, jde do zipu
  Diagnostiky): `total` od vzniku procesu (`GetProcessTimes`), `python`, fáze `qt`, `splash`,
  `import`, `window`, `shown` (přírůstky od `main()`) a `lib_list`, `lib_providers`,
  `lib_models` z `library.prefetch_seconds`. Ve vývoji 31. 10.: total ~4,2, splash ~1,05
  (první okno na Windows), dotazy souběžně ~2,1.
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
- Intenzita 2.0 a fonace (knihovna 29. 10. 2026, verze zůstala 0.4.0):
  `acoustic.intensity.stats` počítá jen ze znělých rámců podle SWIPE
  (Praatova obálka, nový sloupec `voiced_fraction`, VAD volitelný a výchozí
  vypnutý, `requires` prázdné); perturbace 1.1, CPP 1.2 a MFCC 1.2 u fonace
  nejdřív ořežou ticho na hlas (`trim_silence`, `trim_margin`). Hodnoty SD
  intenzity (dimenze Prozódie) i jitter, shimmer, HNR, CPP z fonace jsou
  proto jiné než ve starších bězích a normách; normu i hodnocení je třeba
  spočítat znovu, GUI to nepozná (verze knihovny se nezměnila). Fixtury
  falešky zachycené znovu, kopie dokumentace v `docs/speechscope-library/`.
- CPP 1.3 (knihovna 30. 10. 2026, verze stále 0.4.0): `acoustic.quality.cpp`
  počítá medián jen ze znělých rámců podle SWIPE (parametr `frames` =
  voiced | all, `all` dá hodnotu 1.2), CPPS se vyhlazuje zvlášť v každém
  souvislém znělém běhu, pod `min_voiced_seconds` (1 s) nic nevydá, nový
  sloupec `voiced_fraction` (třetí sloupec feature, v dimenzi Kvalita hlasu
  zůstává `cpp.cpp`). CPP i CPPS u čtení a spontánní řeči stoupnou asi o 2 dB
  proti starším běhům, normy i hodnocení znovu spočítat. Fixtury a kopie
  dokumentace obnovené 30. 10. 2026.
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
- Tlačítko nikdy nenapojovat přímo na metodu s nepovinným prvním parametrem
  (`clicked.connect(self.save_image)`): Qt předá `checked=False` jako argument
  a metoda dostane `False` místo cesty (ukládalo soubor „False“). Vždy
  `clicked.connect(lambda: self.save_image())`.
- Komentáře, docstringy a texty v UI česky, identifikátory anglicky.
- Nahrávky pacientů nikdy do gitu. `.gitignore` blokuje `data/`, `work/`,
  `out/`, `models/` a všechna audio mimo `tests/data/`.
- Refaktoring a změna chování nikdy v jednom commitu.
- **Vstupy podoby B** (31. 10. 2026, mockup https://claude.ai/artifact/14g3AuV9rwngo5QNx1zC23,
  `docs/mockups/ciselna-pole/` mimo git): čísla `widgets/inputs.py` `NumberField` (bez šipek,
  číslo vpravo, jednotka šedě uvnitř, desetinná **tečka** a bez zbytečných nul `format_value`,
  ↑↓ a kolečko krokují, nečitelné nebo mimo meze červeně a nepřijme se, `set_changed` podbarví),
  ano/ne `ToggleSwitch`, 2–3 možnosti `Segmented`. Formulář parametrů feature
  (`widgets/param_form.py`) je řádek seznamu: vlevo popis z knihovny bez jednotky (titulek do
  dvojtečky, `split_title`), pod ním šedě technické jméno a výchozí, vpravo pole a ↺; nad
  `SEGMENT_MAX` možností rozbalovací seznam. Dialog prahů kvality má tatáž pole, „pod / nad“
  v záhlaví a u názvu. Spinboxy Qt jinde v aplikaci zůstávají (jen počet dní v Uvolnit místo).
- **Rozbalovací seznamy** (31. 10. 2026, mockup https://claude.ai/artifact/VDkmusEWGcnrKAdSKCFkHf,
  `docs/mockups/rozbalovaci-seznamy/` mimo git): styl `QComboBox` v `theme.py` (šipka, 36 px,
  `combobox-popup: 0` = seznam pod polem, otevřený seznam jako karta) a `widgets/combo_style.py`:
  šipky kreslené v QPainter do PNG v dočasné složce (`arrow_urls`, do šablony stylu),
  `ComboPolisher` (filtr událostí z `theme.apply`) dá každému neupravitelnému seznamu
  `ComboDelegate` (najetí světle modře, vybraná hodnota modře s ✓) a průhledné okno seznamu
  (`comboPopup`, jen kontejner, jinak seznam ztratil podklad). Výběr protokolu ukazuje
  v seznamu jen názvy (rozhodnuto). Sloupec s nabídkou v `MetaTable` (úloha) má v buňce
  šedou šipku, editor v buňce je kompaktní (vlastní styl, `updateEditorGeometry` aspoň na
  nejdelší položku) a seznam otevře až `QTimer.singleShot(0, showPopup)`; otevřený hned
  v `setEditorData` se s novým stylem neukázal.
- **Tabulky všude stejně** (31. 10. 2026, mockup https://claude.ai/artifact/JEpsb6wKch8WBXTYT7yXC8,
  `docs/mockups/tabulky/` mimo git): každá tabulka a strom jde přes `widgets/clean_table.py`
  (`style_table`, `style_tree`, `card`, `set_text(bold/muted/right)`, `set_pill` = štítek přes
  buňku s neviditelným textem položky, `fit_widget_column`, `fit_height`, `right_header`,
  `style_group_item`): záhlaví velkými písmeny, bez mřížky a střídání řádků, čára mezi řádky,
  řádek 40 px (44 tam, kde je v buňce pole), hlavní sloupec tučně, vedlejší šedě, stavy
  štítkem. Dávky rozpracované normy i hodnocení sdílejí `norms.make_plan_table` /
  `fill_plan_table`. Vlastní styl ve stejné podobě mají jen Hodnoty, `MetaTable`, strom
  dimenzí a `TrendMap`. Testy porovnávají záhlaví přes `.lower()`.
- Změny GUI nejdřív navrhnout (text, mockup), pak psát; snímek před
  commitem. Návrhy jsou v `docs/navrh-gui-2.md`.
