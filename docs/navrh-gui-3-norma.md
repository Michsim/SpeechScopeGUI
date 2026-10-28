# Návrh GUI 3: hodnocení pacienta proti normě (pavouk)

Stav k 28. 10. 2026. Mockup obsahu je v `docs/mockups/srovnani-s-normou/`
(artefakt https://claude.ai/artifact/5ET5kDvGthqgXDtNXvAA8v). Backend
z kroku 1 je hotový: `backend/norms.py`, `backend/dimensions.py`,
`backend/meta.py`, `assets/dimensions.yaml`. Rozhodnuto 28. 10.: srovnání
je **samostatná stránka Hodnocení**, ne dialog z Výsledků.

## Cíl

Klinik vybere vyšetření pacienta a vidí, v kterých dimenzích řeči
(kvalita hlasu, artikulace, rytmus, prozódie, pauzy a tempo, dýchání,
jazyk) se liší od zdravých kontrol stejného věku a pohlaví. Pavouk
ukazuje dimenze, strom položky, zpráva jde do PDF. Orientační srovnání,
ne diagnóza. Později na téže stránce vývoj pacienta v čase.

## Proč stránka

- Pro kliniku je to hlavní výstup, ne doplněk tabulky; v nabídce ho najde.
- Vyšetření je napříč běhy (fonace, DDK, čtení = tři běhy), Výsledky
  ukazují jeden běh.
- Longitudinální pohled potřebuje seznam pacienta a jeho návštěv,
  stránka ho má, dialog ne.

## Nabídka

Analýza · Výpočet · Výsledky · **Hodnocení** · Protokoly a normy · Prostředí.
Normy nejsou další položka: správa norem je druhá část stránky Protokoly
(stejný vzor karet, import, export, smazání). Uložit jako normu… zůstává na
Výsledcích, protože vychází z běhu.

## Krok 2: index vyšetření (`backend/visits.py`)

- Projde běhy z `history.list_runs` se stavem jiným než „běží“ (běžící
  `features.csv` se neotvírá, viz CLAUDE.md), načte tabulku a každý řádek
  je nahrávka (`Recording`: běh, řádek, úloha, cesta, hodnoty).
- **Vyšetření** (`Visit`) = pacient + návštěva. Pacient ze sloupce `subject`
  (po aliasech `pacient`, `id`…), bez něj název souboru bez přípony a
  vyšetření je „neurčené“ (`explicit=False`). Návštěva ze sloupce `visit`,
  bez něj datum běhu.
- Věk, pohlaví a skupina z prvního řádku, který je má.
- Cache v paměti podle cesty, času změny a velikosti `features.csv`;
  obnovení po každém skončeném běhu a po smazání či přejmenování běhu
  (`relocate`). Trvalá cache na disku až kdyby to bylo pomalé.
- Manifest na Analýze převádí aliasy názvů sloupců na kanonické
  (`meta.canonical_column`) už při čtení, takže `features.csv` má vždy
  `subject`, `age`, `sex`, `visit`, `group`; při kolizi (dva sloupce na
  jedno jméno) zůstává druhý původní.

## Krok 3: stránka Hodnocení

```
┌ Hodnocení ──────────────────────────────────────────────────────────────────────────────────┐
│ ┌ Vyšetření ───────────┐ ┌ P-017 · muž, 64 let · 14. 10. 2026 · fonace, DDK, čtení  [bad] ┐ │
│ │ [hledat pacienta…  ] │ │                              [Uložit obrázek…] [Zpráva (PDF)…] │ │
│ │ P-017  14.10.2026    │ ├ Norma [▾ …]  Věkové okno [▾ ± 10 let]  [x] jen stejné pohlaví   │ │
│ │  fonace, DDK, čtení  │ │ Použito 23 kontrol, muži 54 až 74 let. Okno rozšířeno na ±15.   │ │
│ │  ● mimo normu        │ ├──────────────────────────┬──────────────────────────────────────┤ │
│ │ P-017  12.03.2026    │ │ Odchylka od normy        │ Dimenze a jejich položky             │ │
│ │  fonace, čtení       │ │      [pavouk]            │ ▾ Kvalita hlasu  fonace  +2,3  98   │ │
│ │  ● hraniční          │ │  ● do 1 SD ● 1–2 ● nad 2 │    jitter (local) 1,92 % 0,81±… +2,9│ │
│ │ p03_short.wav        │ │ V grafu není: Jazyk: …   │ ▸ Artikulace …                       │ │
│ │  (bez pacienta)      │ ├──────────────────────────┴──────────────────────────────────────┤ │
│ │                      │ │ Nahrávky vyšetření: … [Přidat nahrávku z jiného běhu…]          │ │
│ │                      │ │ O normě: …   Orientační srovnání s normou, ne diagnóza.         │ │
│ └──────────────────────┘ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

- Vlevo seznam vyšetření z indexu, nejnovější první, karty jako
  v historii běhů (`RunCard` vzor): pacient, datum, úlohy, štítek
  nejhorší dimenze podle poslední vybrané normy. Hledání podle pacienta.
  Vyšetření bez pacienta jsou šedá, pojmenovaná souborem.
- Vpravo `StatusHeader` s rolí podle nejhorší dimenze a tlačítky.
- Řádek normy: `QComboBox` norem (`all_norms(settings.norms_dir())`,
  display_name, v nápovědě popis a počet osob), `QComboBox` okna (±10, ±15,
  ±20, bez omezení), `QCheckBox` jen stejné pohlaví, věta o použitých
  kontrolách ze `Selection` (počet, rozpětí věku, pohlaví, „okno rozšířeno“,
  „pacient nemá věk, celá norma“). Změna přepočítá `evaluate` a překreslí.
- `SpiderChart` (`widgets/spider_chart.py`, QPainter): osy = dimenze se
  skóre, kružnice 1 a 2 SD, pásma pozadí, bod barvou pásma, popisek
  s názvem a „+2,3 SD“; skóre pod nulou na středu; jedna dimenze = jeden
  vodorovný pruh místo pavouka. Pod grafem legenda a „V grafu není“ s důvody
  z `DimensionResult.reason`.
- `QTreeWidget` vpravo: dimenze (název, úlohy, skóre, percentil, pruh),
  položky (název sloupce z katalogu přes `column_descriptions`, úloha,
  hodnota s jednotkou, průměr ± SD, z, percentil, pruh), barva podle pásma,
  dimenze bez skóre šedě s důvodem. Dvojklik na položku otevře detail
  nahrávky (`RecordingDetailDialog`).
- Dole nahrávky vyšetření (soubor, protokol, běh; dvojklik přehraje, pravé
  tlačítko `file_menu`) s Přidat nahrávku z jiného běhu… (výběr běh → řádek,
  pro data bez manifestu; přidání se ukládá do `visits.json` v AppData), a
  karta O normě (`NormInfo`, zdroj, podmínky, jazyk, verze knihovny) s větou
  o orientačním srovnání.
- Uložit obrázek… = `SpiderChart.grab()` do PNG. Zpráva (PDF)… až v kroku 5.
- Výsledky: pravé tlačítko řádku „Otevřít v Hodnocení“ přepne stránku a
  vybere vyšetření toho řádku.
- Bez normy: pravá část ukáže jen nahrávky vyšetření a text, že norma
  chybí, s odkazem na Protokoly a normy.

### Hranice

- Chybí věk nebo pohlaví pacienta: celá norma, věta to řekne, přepínač
  pohlaví vypnutý.
- Jiný jazyk normy než nahrávky: žlutá věta u normy, nic se nezakazuje.
- Jiná verze knihovny v normě: šedá věta.
- Dvě nahrávky téže úlohy: medián, u úlohy „medián ze 2 nahrávek“.

### Pro vývoj

- Testy proti falešce: pomocná funkce vyrobí syntetickou normu
  (`build_norm` nad tabulkou náhodných kontrol se sloupci z katalogu
  falešky). Do aplikace se žádná ukázková norma nebalí.
- Snímek stránky před commitem.

## Krok 4: vlastní normy a část Normy na Protokolech

- Výsledky: Uložit jako normu… nad vybraným během; dialog s výběrem
  dalších běhů, filtrem „sloupec = hodnota“ (typicky `group = HC`), jménem,
  popisem, zdrojem, podmínkami; varování z `norm_problems`.
- Protokoly a normy: druhá sekce s kartami norem (přibalené ke čtení,
  vlastní), detail (úlohy, osob, věk, pohlaví, jazyk, verze), import
  a export zipu, smazání.

## Krok 5: zpráva

- `QTextDocument` s hlavičkou pacienta, obrázkem pavouka, tabulkou dimenzí
  a položek, větou o normě a výběru kontrol, disclaimer; `QPrinter` do PDF.

## Krok 6: vývoj v čase

- Záložka na Hodnocení: pacient, návštěvy na ose, čáry po dimenzích nebo
  sloupcích, pásmo normy na pozadí, tabulka změn proti minulé návštěvě.
  Práh spolehlivé změny až z test-retest dat.

## Otevřené otázky

1. Tabulka dimenzí a směrů v `assets/dimensions.yaml` je návrh, potvrdit.
2. Je `acoustic.pitch.f0.std` v půltónech? Jinak u prozódie jen stejné
   pohlaví.
3. Přibalená norma: kdy bude kontrolní korpus přepočítaný knihovnou 0.4.0
   s manifestem (věk, pohlaví) po úlohách?
