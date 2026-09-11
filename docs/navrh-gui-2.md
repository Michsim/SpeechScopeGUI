# Návrh: výběr feature, průběh výpočtu, protokoly

Stav: navrženo i implementováno 11. 9. 2026 (knihovna e6f0902, GUI
commity od 6a762f7 po stránku Protokoly). Otevřené otázky dole jsou
rozhodnuté: tabulka, samostatná stránka Protokoly, smlouva verze 2.
Odchylky od návrhu: úlohy na Datech jsou přepínač nad kartami místo
seskupených karet (šetří výšku); editor protokolu je společný widget
`ProtocolEditor` pro Data i Protokoly.

Původní text návrhu k 11. 9. 2026 ráno: Tři přání z 10. 9.: (a) jiný výběr feature v rozšířeném
režimu, (b) viditelný průběh výpočtu, (c) přehlednější protokoly. Návrh je
napsaný tak, aby se dal dělat po částech a každá část byla samostatný commit.

## Co dnes vadí

- **Data, rozšířený režim.** Strom feature (domény → feature, zaškrtávátka,
  providery jako falešné řádky `[Segmentace řeči]` na konci) je nahuštěný
  v pravé polovině splitteru, parametry vybrané feature se tlačí pod něj
  v poměru 2:1. Není vidět, co feature dává (sloupce), ani co která
  volba stojí (které modely spustí).
- **Běh.** Vidíme jen soubor X z N po dokončení souboru. U protokolů
  s Whisperem trvá jeden soubor minutu a půl a mezi tím se nic nehýbe.
  Nevíme, který provider běží, a odhad času je jen poměr z hotových
  souborů (první soubor navíc obsahuje načtení modelů, takže odhad
  zpočátku přestřeluje).
- **Protokoly.** Rozbalovací seznam osmi jmen, popis pod ním. Klinik
  nevidí, které protokoly patří k jeho úloze, co spustí a jak dlouho to
  potrvá. Vlastní protokol jde jen uložit (rozšířený režim), ne smazat,
  přejmenovat, poslat kolegovi ani přinést od kolegy.

## Společná páteř: co protokol „stojí“

Z `list --json` (pole `requires`) a z výběru feature GUI už dnes umí
spočítat, které providery se spustí. Z toho se všude odvozuje stejný
souhrn, počítá ho jedna funkce v `backend/protocol.py`
(`Protocol.summary(features)`):

| provider   | štítek v UI       | orientační cena |
|------------|-------------------|-----------------|
| žádný      | bez modelů        | sekundy na nahrávku |
| segments   | segmentace        | desítky sekund |
| phonemes   | fonémy (phnrec)   | sekundy |
| transcript | přepis (Whisper)  | minuty |
| nlp        | jazykový rozbor   | + sekundy |

Orientační cena je jen text pro první běh. Po každém dokončeném běhu si
GUI uloží do nastavení skutečnou střední dobu na nahrávku pro daný
protokol (`stats/<slug>/seconds_per_file`) a příště ukáže „naposledy
41 s na nahrávku“. Totéž číslo používá odhad v (b) ještě před dokončením
prvního souboru.

## (a) Výběr feature: tabulka místo stromu

Widget `FeaturePicker` (`ui/widgets/feature_picker.py`), použitý na Datech
v rozšířeném režimu a v editoru protokolu (c). Nahrazuje `QTreeWidget`.

```
 Hledat: [__________]   Rychle: (Vše) (Nic) (Jen bez modelů) (Bez přepisu)
 ┌──┬────────────────────────┬──────────┬──────────────────┬─────────┐
 │  │ feature                │ sloupců  │ potřebuje        │         │
 ├──┼────────────────────────┼──────────┼──────────────────┼─────────┤
 │▣ │ AKUSTIKA · výška       │          │                  │  4/4    │
 │☑ │   f0                   │ 12       │                  │         │
 │▣ │ AKUSTIKA · časování    │          │                  │  2/3    │
 │☑ │   pauses               │ 9        │ segmentace       │         │
 │☑ │   segmentation         │ 7        │ segmentace       │         │
 │☐ │   speech_rate          │ 4        │ přepis           │         │
 │☐ │ LINGVISTIKA · lexikum  │          │                  │  0/8    │
 │☐ │   mattr                │ 1        │ jazykový rozbor  │         │
 │  │   …                    │          │                  │         │
 └──┴────────────────────────┴──────────┴──────────────────┴─────────┘
 Spustí se: segmentace, fonémy · 14 feature, 96 sloupců · naposledy 12 s na nahrávku
 Chybí model: conformer (Prostředí)                                   ← jen když chybí
```

- Plochá tabulka, skupiny jako nerozbalovací řádky s tristate
  zaškrtávátkem a počtem vybraných. Skupina = `doména · druhá část jména`
  (`acoustic.timing.*` → „Akustika · časování“), popisky skupin
  v `contract.GROUP_LABELS`; neznámá skupina se ukáže surově, nikdy
  nespadne.
- Sloupec „sloupců“ = `len(outputs)`; tooltip vypíše názvy a popisy
  sloupců (dnes je to jen v tooltipu bez počtu).
- Vyhledávání filtruje řádky podle jména i popisu sloupců. Rychlé volby
  pracují s `requires`: „Jen bez modelů“ = feature s prázdným
  `requires`, „Bez přepisu“ = bez `transcript` a `nlp`.
- Providery nejsou řádky v tabulce. Ukazují se jen v souhrnu pod ní
  jako klikací štítky; klik otevře jejich parametry vpravo, stejně jako
  klik na feature.
- Parametry jdou do pravého panelu splitteru (`ParamForm` beze změny),
  ne pod tabulku. Panel má nadpis (jméno feature nebo provideru) a tlačítko
  „Výchozí“, které vrátí parametry na hodnoty z knihovny.
- Řádek „Chybí model“ bere stav z `doctor --json` (stránka Prostředí ho
  už má v cache). Spuštění se nezakáže, jen se varuje; knihovna to
  stejně ohlásí v `notes`.

Zvažovaná alternativa: karty po skupinách (šest karet v mřížce, v každé
zaškrtávátka). Vypadá vzdušněji, ale na 720 px výšky se nevejde bez
rolování, hůř se v ní hledá a neumí ukázat sloupce a cenu na jednom
řádku. Tabulka je stejný ovládací prvek jako seznam nahrávek vedle,
takže stránka zůstane jednotná.

## (b) Průběh výpočtu

### Změna v knihovně (smlouva verze 2)

Dnešní události končí u granularity souboru. Bez pomoci knihovny se
běžící provider dá jen hádat z logu, a to až po jeho dokončení. Proto
dvě nové události, `PROTOCOL_VERSION = 2`:

```json
{"event":"begin","index":4,"path":"…/zkouska5.wav"}
{"event":"stage","index":4,"provider":"transcript","status":"running"}
{"event":"stage","index":4,"provider":"transcript","status":"done","seconds":38.2}
{"event":"stage","index":4,"provider":"segments","status":"cached"}
{"event":"stage","index":4,"provider":"nlp","status":"error","msg":"…"}
{"event":"file","index":4,"path":"…","status":"ok"}
```

- `begin` vyšle `api._process` před načtením signálu.
- `stage` vysílá místo, kde `_run_feature` sahá pro provider (cache
  v `_process`): `running` před výpočtem, `done`/`cached`/`error` po
  něm. Provider se v jednom souboru počítá jednou, takže na soubor
  přijde nejvýš tolik `stage` dvojic, kolik je providerů.
- Nic dalšího se nemění; `segment` a `transcribe` vysílají totéž
  se svým jediným providerem.
- GUI umí verzi 2 a verzi 1 přijme také (jen bez řádku „právě běží“),
  aby šla stará knihovna dál používat. Falešná knihovna vysílá verzi 2
  s pauzami mezi `stage`, aby šel průběh vidět i bez modelů.

### Stránka Běh

```
 Pohádka, akustika i lingvistika · 12 nahrávek · segmentace, přepis (Whisper), jazykový rozbor
 [██████████████░░░░░░░░░░░░░░░░░░░░░░░░░]  4 z 12 hotovo, 0 chyb
 uplynulo 6 min 20 s · zbývá asi 11 min (naposledy 92 s na nahrávku)

 Právě: zkouska5.wav                       segmentace ✓ 4 s · fonémy ✓ 2 s · přepis … 41 s · jazykový rozbor
 ┌───────────────┬────────────┬─────────┬───────────┬────────────┬──────────┬───────────────────────┐
 │ nahrávka      │ segmentace │ fonémy  │ přepis    │ jaz. rozbor│ výsledek │ poznámka              │
 ├───────────────┼────────────┼─────────┼───────────┼────────────┼──────────┼───────────────────────┤
 │ zkouska1.wav  │ ✓ 5 s      │ ✓ 2 s   │ ✓ 71 s    │ ✓ 3 s      │ ok       │                       │
 │ zkouska2.wav  │ z cache    │ ✓ 2 s   │ ✓ 64 s    │ ✗          │ ok       │ nlp: přepis prázdný   │
 │ zkouska5.wav  │ ✓ 4 s      │ ✓ 2 s   │ … 41 s    │            │ běží     │                       │
 │ zkouska6.wav  │            │         │           │            │ čeká     │                       │
 └───────────────┴────────────┴─────────┴───────────┴────────────┴──────────┴───────────────────────┘
 ▸ Log knihovny (sbalený, rozbalí se klikem; při chybě se rozbalí sám)
                                                                          [Zrušit]
```

- Tabulka se naplní všemi nahrávkami hned při `start` (GUI seznam zná,
  posílá ho), stav „čeká“. Sloupce providerů jsou jen ty ze `start.providers`.
  Do buňky se píše výsledek `stage` a doba; běžící buňka tiká každou
  sekundu z lokálního časovače.
- Odhad zbývajícího času: dokud není hotový žádný soubor, z uložené
  statistiky protokolu (je-li); potom z mediánu hotových souborů,
  při třech a víc souborech bez prvního (v něm je načtení modelů).
- Nabídka vlevo ukazuje během běhu „Běh · 4/12“, aby bylo vidět postup
  i z jiné stránky. Titulek okna totéž.
- Log se schová do rozbalovacího panelu. Klinik ho nepotřebuje, výzkumník
  si ho rozbalí; při kódu ≠ 0 nebo `contract_error` se rozbalí sám.
- Po dokončení zůstane tabulka viditelná (dnes se přepne na Výsledky;
  přepnutí zůstane, ale Běh ukáže souhrn „11 z 12 ok, 1 chyba“ a čas).

## (c) Protokoly

### Výběr na Datech

Rozbalovací seznam nahradí seznam karet seskupený podle úlohy. Klinik
nejdřív vidí svou úlohu, pak dva až tři protokoly k ní.

```
 Protokol
 ┌ POHÁDKA ─────────────────────────────────────────────────────────────┐
 │ ● Pohádka, akustika                   [segmentace] [fonémy]  ~15 s   │
 │   Akustické feature z vyprávění včetně pauz a časování.              │
 │ ○ Pohádka, akustika i lingvistika     [segmentace] [Whisper] [Stanza]│
 │   Všechny feature. Trvá minuty na nahrávku.            ~90 s        │
 │ ○ Moje pohádka bez fonémů  (vlastní)  [segmentace]           ~10 s   │
 └──────────────────────────────────────────────────────────────────────┘
 ┌ MONOLOG ─────────────────────────────────────────────────────────────┐
 │ …                                                                    │
```

- Štítky providerů a čas přijdou ze společného souhrnu. Vlastní protokoly
  mají štítek „vlastní“ místo dnešní přípony v závorce.
- Když složka s nahrávkami obsahuje jen jednu úlohu podle názvu souborů
  (discover to dnes neumí, `Item.task` určuje knihovna z `--task`),
  nic se nefiltruje. Skupiny podle úlohy stačí.
- V rozšířeném režimu je pod seznamem `FeaturePicker` (a) s parametry.
  Jakmile výzkumník něco změní, karta dostane štítek „upraveno“ a
  tlačítko „Uložit jako protokol…“ (dnešní dialog) se zapne. Běh se
  spouští s upravenou verzí, do `run_dir/protocol.yaml` se ukládá to,
  co se opravdu spustilo (beze změny).

### Stránka Protokoly

Nová položka v nabídce mezi Data a Běh. Levý seznam, vpravo detail.

```
 Protokoly                                      [Nový…] [Import…] [Otevřít složku]
 ┌ Přibalené ──────────┐ ┌ Pohádka, akustika i lingvistika ─────── (přibalený) ┐
 │ Fonace, základní    │ │ Popis: Všechny feature pro vyprávění…               │
 │ DDK, základní       │ │ Úloha: Vyprávění pohádky                            │
 │ Pohádka, akustika   │ │ Spustí se: segmentace (conformer), Whisper (cs),    │
 │▸Pohádka, ak. i ling.│ │            Stanza · naposledy 92 s na nahrávku      │
 │ …                   │ │ Feature: 22 (Akustika 9, Lingvistika 13)  [ukázat]  │
 ├ Vlastní ────────────┤ │ Změněné parametry: segments.model=conformer,        │
 │ Moje pohádka        │ │                    transcript.language=cs           │
 └─────────────────────┘ │                                                     │
                         │ [Vytvořit kopii] [Export…]                          │
                         └─────────────────────────────────────────────────────┘
```

- Přibalené protokoly jsou jen ke čtení: „Vytvořit kopii“ založí vlastní
  s příponou „(kopie)“ a otevře ho k úpravě.
- Vlastní protokol má vpravo editor: jméno, popis, úloha (změna úlohy
  vyprázdní výběr feature, protože nabídka feature na úloze závisí),
  `FeaturePicker` s parametry, tlačítka Uložit, Smazat, Export….
  Editor je tentýž widget jako v rozšířeném režimu na Datech; v základním
  režimu se místo něj ukáže jen souhrn ze schématu výše.
- Import = kopie YAML do složky protokolů s kontrolou `format` a úlohy;
  při kolizi jména se zeptá (přepsat / uložit jako „… (2)“). Export =
  „Uložit jako“ dialog na YAML. Vlastní protokoly tak jdou poslat
  e-mailem mezi pracovišti.
- „Otevřít složku“ otevře `Dokumenty\SpeechScope\protokoly` v Průzkumníku
  pro ty, kdo YAML raději upraví ručně.
- Základní režim vidí stránku také (import, export, smazání, souhrn),
  jen bez editoru. Klinik tak dokáže přinést protokol od výzkumníka.

## Pořadí prací a commity

1. **Knihovna:** události `begin` a `stage`, `PROTOCOL_VERSION = 2`,
   testy, CLAUDE.md a README. Jeden commit v repu knihovny.
2. **GUI, smlouva:** `contract.py` verze 2 s tolerancí verze 1, falešná
   knihovna s novými událostmi, zachycené fixtury, `BatchState` s průběhem
   providerů. Commit.
3. **GUI, stránka Běh** podle (b) včetně statistiky doby na nahrávku
   a stavu v nabídce. Commit.
4. **GUI, `FeaturePicker`** (a) a společný souhrn protokolu; na Datech
   nahradí strom, parametry do pravého panelu. Commit.
5. **GUI, karty protokolů** na Datech (c, první část). Commit.
6. **GUI, stránka Protokoly** s editorem, importem a exportem. Commit.

Body 1 až 3 jsou nezávislé na 4 až 6, dají se udělat a vyzkoušet
v klinice dřív. Každý bod se hlídá testy proti falešné knihovně
(`tests/test_ui.py`) a pytest-qt.

## Otevřené otázky

1. Tabulka (doporučeno) nebo karty po skupinách pro výběr feature?
2. Stránka Protokoly jako samostatná položka nabídky, nebo správa jen
   v dialogu z Dat? Samostatná stránka je doporučená kvůli importu
   a exportu, které klinik potřebuje bez rozšířeného režimu.
3. Souhlas se změnou smlouvy knihovny na verzi 2 (dvě nové události)?
   Bez ní se dá udělat jen tabulka souborů a lepší odhad času, ne
   „právě běží přepis“.
