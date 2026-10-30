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
Pořadí v nabídce (od 30. 10. 2026, mockup
https://claude.ai/artifact/UkcxmVZDY3M9n89UQFuhdq, `docs/mockups/domu-pacienti/` mimo
git): Domů · Analýza nahrávek · Výpočet · Výsledky · Pacienti, dole Protokoly · Normy ·
Prostředí (`PAGE_HOME` … `PAGE_ENV`; popis stránek Pacienti a Normy je v sekci
Normy a dimenze). Uvítání mimo nabídku už není.
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
deleguje atributy na `widget`). Pod kartami pruh „právě běží“ (`set_running`
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
obrazovku), tabulka nahrávek v kartě, výsledek jako štítek přes buňku; starý štítek
se před výměnou skryje, Qt ho maže až z fronty událostí) · Výsledky (historie běhů ze složek v Dokumentech přes
`backend/history.py` a `run.json` jako karty (`widgets/run_history.py`, `RunCard`
se štítkem stavu, API `count/card/current_row/select_row`), stavová karta se
štítky počtů (klik přepne filtr), tabulka vybraného běhu v kartě se zmrazeným prvním
sloupcem (`widgets/frozen_table.py`; šířky sloupců se měří z hlavičky a prvních
`SAMPLE_ROWS` řádků, celý jen sloupec s názvem souboru, u 500 × 135 buněk trvalo
měření všech 10 s), hledání sloupce, popis sloupce
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
- **Hodnocení se zakládají ručně** (rozhodnuto 28. 10., mockup
  https://claude.ai/artifact/2UwaQmndVNFBCMSHmehQ8A, `docs/mockups/hodnoceni/`
  mimo git): `backend/assessments.py`, `Assessment` (pacient, věk, pohlaví,
  datum, `RecordingRef` = složka běhu, řádek, soubor, úloha) jako JSON
  v `settings.assessments_dir()`; `resolve` z něj složí `Visit` (řádek
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
  `AssessmentPage`, `PAGE_ASSESSMENT`, od 30. 10. 2026): vlevo karta na
  pacienta (`PatientCard`; pacient = `patient_key(subject)`, zatím jen řetězec
  bez mezer a velikosti písmen, rozhodnuto 30. 10.; štítek a pohlaví/věk podle
  posledního vyšetření, u víc vyšetření „n vyšetření · první → poslední“;
  `_groups` = vyšetření pacienta od nejnovějšího, `_shown` = právě vybrané),
  štítek nejhoršího pásma `worst_band`: v normě, hraniční, mimo normu, bez
  normy; úlohy s počtem nahrávek `task_count`) a hledání pacienta, nahoře Nové
  hodnocení…; pod hlavičkou řada vyšetření (`visit_bar`, tlačítka s datem,
  vybrané modré, `_select_visit`, `select_assessment(id)` přepne pacienta i
  vyšetření, `patient_entries()`) a vpravo Nové vyšetření… (`new_visit`:
  `AssessmentDialog(template=…)` předvyplní pacienta, věk a pohlaví bez
  nahrávek a data). Staré tlačítko se při výměně řady napřed skryje
  (`deleteLater` kreslil zbytkový modrý pruh). Vpravo hlavička
  pacienta se štítky dimenzí a Upravit…, Uložit obrázek… (PNG pavouka),
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
  s nulou uprostřed, `ScoreBar` ve stromu též od nuly. Dřívější podoba 2b
  (28. 10., mockup https://claude.ai/artifact/Bgky5AXRhPZvjuBZnixhu7, dovnitř
  „větší = zdravější“) zůstává v kódu jako `INWARD` (`set_orientation`,
  `ui/spider_orientation`, výchozí `outward`), přepínač na stránce je skrytý
  (`orientation_combo.hide()`)), strom
  dimenze → položka (úloha, pacient, norma, z, percentil), chybějící dimenze
  s důvodem, tabulka Nahrávky hodnocení (úloha, nahrávka, běh, s normou)
  s poznámkou o průměru, jiném protokolu a chybějících odkazech, O normě.
  Bez normy ukáže jen nahrávky a kde normu vzít. Dialog
  (`ui/assessment_dialog.py`, `AssessmentDialog`): pacient, věk, pohlaví,
  datum, pro každou úlohu nahrávky s Přidat… (`PickRecordingDialog`:
  nahoře podle manifestu pacienta, pak podle názvu souboru, ostatní jen bez
  hledání; `assessments.candidates_for`) a Odebrat; první nahrávka
  předvyplní prázdné údaje z manifestu (`assessments.prefill`, datum z
  `visit`, jinak z běhu). Na Výsledcích pravé tlačítko „Otevřít v Hodnocení“
  (`ResultsPage.assess_requested` → `MainWindow.show_assessment` →
  `show_row`) otevře dialog s tou nahrávkou; po uložení přepne na Hodnocení.
  Stránka se obnoví při zobrazení a po skončeném běhu. Vyhodnocení pacientů
  se drží v `_eval_cache` podle id hodnocení, dokud se nezmění norma, okno nebo
  pohlaví (`_cached_evaluation`, `_eval_key`); `refresh` cache maže. Kliknutí
  na kartu tak nepřepočítává ostatní (60 pacientů dřív ~3,5 s, teď ~50 ms);
  `Selection` si cachuje hodnoty sloupců a průměry po osobách
  (`subject_means`, jeden groupby pro všechny feature).
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
  dávek a Dopočítat zbývající, Založit z hotových, Zahodit…. Zbývá: PDF,
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
  Koše, běhy zůstávají). Do složek s nahrávkami se nic nezapisuje. Zbývá:
  varianta z hotových běhů, Přidat kontroly… a varování na Hodnocení, když
  pacient nemá protokol z `NormInfo.protocols`.
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
- Změny GUI nejdřív navrhnout (text, mockup), pak psát; snímek před
  commitem. Návrhy jsou v `docs/navrh-gui-2.md`.
