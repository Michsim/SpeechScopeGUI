# SpeechScope

Knihovna a CLI pro extrakci řečových feature z klinických nahrávek.
Vzniká refaktoringem laboratorních skriptů používaných na výzkum skupiny SAMI.

---

## Instalace

Potřebuješ [uv](https://docs.astral.sh/uv/) a Python 3.11.

```bash
uv sync --all-extras
```

Tím se nainstalují všechny závislosti. Zabere to zhruba 1,4 GB, protože se
stahuje torch. Modely jsou zvlášť, o nich je řeč níž.

Jestli ti stačí akustické feature nad hotovými labely, vystačíš si s
menší instalací:

```bash
uv sync
```

| co chceš | příkaz |
|---|---|
| jen akustické feature | `uv sync` |
| navíc přepis Whisperem | `uv sync --extra whisper` |
| navíc segmentaci pyannote | `uv sync --extra pyannote` |
| navíc vlastní model segmentace | `uv sync --extra dnn` |
| navíc lingvistiku přes Stanzu | `uv sync --extra nlp` |
| všechno | `uv sync --all-extras` |

Knihovna běží i bez extras. Když si vyžádáš něco, na co nemá závislosti,
řekne ti, co doinstalovat, místo aby spadla.

### Po naklonování, v kostce

1. `uv sync --all-extras`
2. Nastav tokeny: `HF_TOKEN` pro pyannote (nejdřív odsouhlas podmínky
   modelu na Hugging Face) a `GITHUB_TOKEN` pro hotové ONNX modely (bez něj
   se vyexportují z torche, jen to trvá déle).
3. `uv run speechscope models download`
4. phnrec zkopíruj ručně do `models/phnrec/`, není nikde ke stažení.
   Bez něj nejde jen artikulace samohlásek.
5. `uv run speechscope doctor` musí skončit kódem 0.

Bez tokenů to jde taky: kdo už modely má, zabalí je přes `models pack`
a ty je nainstaluješ ze souboru přes `models unpack` (sekce „Balík
modelů pro kliniky“). Pak stačí `uv sync --extra whisper --extra nlp
--extra onnx`, torch není potřeba.

Detaily ke každému kroku jsou níž.

### Modely

Velké modely nejsou v gitu, mají dohromady přes tři gigabajty. Po
naklonování repozitáře si je stáhneš jedním příkazem:

```bash
uv run speechscope models download
uv run speechscope models list
```

```
adresář s modely: C:\...\SpeechScope\models
┌────────────────────────────────┬──────┬──────────┬──────────────────────┐
│ model                          │ stav │ velikost │ používá              │
├────────────────────────────────┼──────┼──────────┼──────────────────────┤
│ Whisper large-v3 (CTranslate2) │ je   │ 3000 MB  │ provider transcript  │
│ WavLM base                     │ je   │  380 MB  │ segmentace conformer │
│ pyannote/segmentation          │ je   │   17 MB  │ segmentace pyannote  │
│ Stanza (cs, en)                │ je   │  900 MB  │ provider nlp         │
│ phnrec, rozpoznávač fonémů     │ je   │   40 MB  │ provider phonemes    │
│ segmentace v ONNX              │ je   │  500 MB  │ segmentace bez torche│
└────────────────────────────────┴──────┴──────────┴──────────────────────┘
```

phnrec je hotový program z FIT VUT v Brně, jen pro Windows a jen pro
češtinu. Není nikde ke stažení, `models download` ti řekne, kam zkopírovat
složku s `phnrec.exe`, `atlas.dll` a modelem `PHN_CZ_SPDAT_LCRC_N1500`.

Ukládají se do `models/` vedle projektu, nebo do adresáře z proměnné
`SPEECHSCOPE_MODELS`. Jednotlivě se dají dotáhnout přes
`models download --only wavlm`.

Model je „na místě“, jen když má všechny soubory, které provider čte
(u Whisperu `model.bin`, u Stanzy `resources.json` a jazyky, u ONNX
`meta.json`). Stahuje se do složky `<model>.part` a na místo se
přejmenuje až hotové; po chybě se rozpracovaná kopie smaže. `doctor`
tedy nikdy nehlásí hotový model, který není. Cache Hugging Face se
během stahování drží ve složce modelů (`.hf-cache`, bez symbolických
odkazů, po stažení se smaže), protože výchozí cache v profilu uživatele
na Windows zlobí; kdo má `HF_HUB_CACHE` nastavenou sám, tomu se nechá.
Whisper se stahuje jako plochá kopie repozitáře přes `snapshot_download`,
tedy přesně v tvaru, který provider čte.

### Balík modelů pro kliniky

Klinika nepotřebuje Hugging Face ani tokeny. Na počítači, kde modely
jsou, se zabalí do jednoho zipu, a na klinice se nainstalují ze souboru
(v aplikaci tlačítkem „Modely ze souboru“, nebo příkazem):

```bash
uv run speechscope models pack --only whisper,stanza,onnx --out speechscope-modely-v1.zip
uv run speechscope models unpack speechscope-modely-v1.zip
```

Zip je bez komprese (váhy se stlačit nedají, balení i rozbalení jde
rychlostí disku) a nese `manifest.json` s otiskem SHA-256 každého
souboru. `unpack` každý soubor při zápisu ověřuje, rozbaluje přes
`.part` a poškozený balík nechá na disku to, co tam bylo. Modely, které
už jsou na místě, balík nahradí. Kódy: 1 = některý model se nepovedl,
2 = soubor není balík modelů SpeechScope. Bez `--only` se balí všechno,
co je na místě, včetně phnrec.

Váhy vlastního modelu segmentace jsou výjimka, ty se vezou přímo
v balíčku, protože jsou naše a mají jen 21 MB.

pyannote je za přihlášením. Než ho stáhneš, odsouhlas podmínky modelu
`pyannote/segmentation` na Hugging Face a nastav token:

```bash
set HF_TOKEN=hf_...
uv run speechscope models download --only pyannote
```

### Výpočet na grafické kartě

Nic se kvůli tomu neinstaluje. Balíčky si běhové prostředí CUDA vezou
s sebou, na počítači stačí ovladač NVIDIA, který přijde s kartou nebo
přes Windows Update. Žádný CUDA Toolkit.

| co | čím se řídí |
|---|---|
| přepis Whisperem | vlastní vrstva v ctranslate2, kartu použije sám; když kartu neunese (málo paměti, nepodporovaný typ výpočtu), dopočítá v int8 na procesoru a zapíše to do logu |
| segmentace přes ONNX (výchozí, když jsou modely) | na Windows libovolná karta přes DirectML, jinak procesor; při pádu na kartě dopočítá na procesoru |
| segmentace přes torch | kartu použije jen sestavení torche s CUDA |

Základní instalace přináší torch bez CUDA, takže segmentace přes torch
počítá na procesoru. Sestavení s CUDA se instaluje z indexu PyTorche,
verze musí sedět na tvůj ovladač.

Vlastní model segmentace počítá po oknech s překryvem, výchozí je 30 s
a 5 s. Bez toho roste paměť s druhou mocninou délky nahrávky a dvě
minuty zvuku potřebují kolem 10 GB, což se na běžnou kartu nevejde.
S okny stačí gigabajt a délka nahrávky nehraje roli. Na dvouminutové
nahrávce to vyšlo takhle:

| výpočet | čas |
|---|---|
| procesor, v celku | 35.5 s |
| procesor, okna 30 s | 14.6 s |
| grafická karta, okna 30 s | 5 s |

Okna mění popisky na necelých dvou procentech rámců, a to jen posunem
hranic o setinu sekundy, počet segmentů je stejný. Karta a procesor se
se stejnými okny shodují do posledního rámce. Výpočet v celku vrátíš přes
`--set segments.chunk_seconds=0`.

Do logu se vždycky zapíše, na čem se počítalo:

```
INFO pyannote_onnx: pyannote (ONNX) běží na DmlExecutionProvider
INFO conformer_onnx: conformer (ONNX) běží na CPUExecutionProvider
INFO pyannote_vad: pyannote běží na NVIDIA GeForce RTX 5060 Laptop GPU
INFO conformer_vad: conformer běží na procesoru, grafická karta není k dispozici
```

Sestavení s CUDA funguje i na počítači bez použitelné karty. Vypíše
varování a spočítá to na procesoru. Ověřeno, že výsledky jsou v obou
případech bit po bitu shodné, takže pro rozdávanou aplikaci stačí jedno
sestavení pro obojí.

### Segmentace bez torche (ONNX)

Oba modely segmentace umí běžet v `onnxruntime`, bez torche, pyannote
a transformers. Je to výchozí cesta, jakmile modely existují.

Hotové modely leží jako zip u vydání `models-onnx-v1` tohoto (soukromého)
repozitáře. `models download` je odtud stáhne, když má token GitHubu
s právem číst obsah repozitáře:

```bash
set GITHUB_TOKEN=github_pat_...
uv run speechscope models download --only onnx
```

Token vyrobíš na GitHubu v Settings → Developer settings → Fine-grained
tokens, pro repozitář SpeechScope s oprávněním Contents: Read. Kdo má
přihlášené `gh`, token nastavovat nemusí.

Bez tokenu se modely vyrobí exportem z torchových vah přímo na místě, což
potřebuje torch, WavLM a pyannote. Vznikne složka `models/segmentation-onnx`
(500 MB), kterou lze také jen zkopírovat. Na počítačích, kde se počítá,
torch být nemusí.

Nový export do vydání dostaneš takhle: na počítači s torchem spusť export,
zabal ho a zip nahraj jako přílohu vydání (Releases → Draft a new release,
značka `models-onnx-v1`, soubor `segmentation-onnx.zip`):

```bash
uv run speechscope models download --only onnx    # export, potřebuje wavlm a pyannote
uv run speechscope models pack-onnx --out out\segmentation-onnx.zip   # 287 MB
```

`pack-onnx` je jiný zip než `models pack`: obsahuje jen složku
`segmentation-onnx`, je komprimovaný a čte ho `models download --only
onnx`. Slouží jen k tomu, aby se hotový export dostal na GitHub. Pro
rozdávání modelů lidem je tu `models pack`, které zabalí i ONNX
(`--only onnx`) a nepotřebuje token.

Když se změní export (jiné okno, jiné váhy), zvedni značku v
`modelstore.ONNX_RELEASE_TAG`, ať si staré instalace nestáhnou něco jiného,
než na co byly otestované.

| parametr | hodnoty | význam |
|---|---|---|
| `segments.runtime` | `auto`, `onnx`, `torch` | `auto` bere ONNX, když jsou modely, jinak torch |
| `segments.onnx_device` | `auto`, `cpu`, `gpu`, `gpu:1` | `auto` bere kartu, když ji onnxruntime umí |
| `segments.onnx_dir` | cesta | jiné umístění složky s modely |

Výsledky sedí na torch do posledního rámce po 10 ms. Na skutečné
dvouminutové nahrávce to vyšlo takhle (včetně načtení modelů a otevření
sezení, procesor i karta ve stejném prostředí):

| model | torch, procesor | ONNX, procesor | ONNX, karta (DirectML) | shoda popisků |
|---|---|---|---|---|
| conformer | 19.4 s | 11.6 s | 8.1 s | všech 534 segmentů stejných na všech třech |
| pyannote | 2.4 s | 2.6 s | 4.6 s | všech 71 segmentů stejných na všech třech |

pyannote je malý model a na kartě nic nezíská, proto `onnx_device=auto`
u něj znamená procesor. Kartu dostane jen s `gpu`.

Jedna výhrada: pyannote je ve float32 sám o sobě nestabilní na
digitálním tichu (samé nuly nebo šum pod 1e-3), kde se proti float64
liší až o desetinu. Tam může ONNX proti torchi posunout hranici o rámec.
Skutečné nahrávky mají šum mikrofonu a tenhle stav v nich nenastává.

Grafická karta bez CUDA: extra `onnx` instaluje na Windows balíček
`onnxruntime-directml`, který umí libovolnou kartu (NVIDIA, AMD, Intel)
i procesor. Karta se použije sama, když je; když se nepovede otevřít,
nebo výpočet na ní spadne třeba kvůli paměti, dopočítá se okno na
procesoru a do logu jde varování. Na Linuxu a macOS zůstává základní
`onnxruntime` (jen procesor). `speechscope doctor` ukáže, co onnxruntime
umí. Změřeno na oknu 30 s modelu WavLM (největší část výpočtu):

| běh | čas na okno |
|---|---|
| torch, procesor | 1 666 ms |
| ONNX, procesor | 611 ms |
| ONNX, DirectML, NVIDIA RTX 5060 | 195 ms |
| ONNX, DirectML, Intel Arc | 95 ms |

DirectML potřebuje pevné tvary, takže na kartě se používají exporty na
celé okno (soubory `*_static*.onnx`) a poslední kratší okno se dopočítá
na procesoru přes exporty s proměnnou délkou. Popisky jsou v obou
případech stejné.

Poznámka k balíčkům: faster-whisper si žádá základní `onnxruntime`, který
by se na Windows pral s `onnxruntime-directml` (stejný modul, poslední
instalace vyhrává). `pyproject.toml` to řeší přepisem závislosti v sekci
`[tool.uv]`. Kdo instaluje SpeechScope jako závislost do jiného projektu
(třeba GUI), musí stejný přepis mít i tam.

---

## Rychlý start

```bash
# co všechno umím spočítat pro vyprávění pohádky
uv run speechscope list --task story

# akustické feature z fonace
uv run speechscope extract data\ --task phonation --out out\phon.csv
```

Úloha je vlastnost nahrávky a musíš ji zadat. Možnosti jsou `phonation`,
`ddk`, `story`, `monologue` a `reading`. Feature, která pro danou úlohu
nedává smysl, skončí chybou, ne sloupcem plným NaN.

### Přehled příkazů

| příkaz | co dělá |
|---|---|
| `speechscope list` | vypíše feature, jejich sloupce a parametry |
| `speechscope extract` | spočítá feature a zapíše CSV |
| `speechscope segment` | spočítá jen segmentaci a uloží labely |
| `speechscope transcribe` | spočítá jen přepis a uloží text |
| `speechscope models` | ukáže a stáhne modely |
| `speechscope doctor` | zkontroluje knihovny, modely a grafickou kartu |
| `speechscope version` | vypíše verzi |

Nápovědu ke každému dostaneš přes `--help`.

### Kontrola prostředí

Když něco nejde, začni tímhle. Nic nestahuje ani nespouští, jen se
podívá, co je nainstalované, které modely leží na místě a co z toho plyne
pro jednotlivé providery:

```bash
uv run speechscope doctor
uv run speechscope doctor --json     # pro GUI, končí kódem 1 když něco chybí
```

Segmentace je připravená, jakmile funguje aspoň jedna cesta (ONNX, nebo
torch s conformerem či pyannote); v tabulce je vidět které. Na klinice
tak stačí extras `whisper`, `nlp`, `onnx` a zkopírované modely, torch
tam být nemusí.

Log jde na stderr. Do souboru ho dostaneš přes `--log-file`, úroveň
řídí `--log-level` (výchozí INFO):

```bash
uv run speechscope extract data\ --task story --log-file out\extract.log --log-level DEBUG
```

Modely se hledají v `models/` vedle projektu, nebo v adresáři
z proměnné `SPEECHSCOPE_MODELS`. Kdo knihovnu spouští z jiné složky,
typicky GUI, předá `--models-dir` a nezáleží na tom, odkud se spouští:

```bash
uv run speechscope --models-dir D:\speechscope\models extract data\ --task story
```

### Co už je hotové

Přenos laboratorních skriptů běží. Hotové jsou tyhle feature:

| jméno | co počítá | sloupce |
|---|---|---|
| `acoustic.quality.cpp` | cepstrální vrcholová prominence | `cpp`, `cpps` |
| `acoustic.pitch.f0` | statistiky základní frekvence v půltónech | `std`, `median`, `mean`, `max`, `min` |
| `acoustic.spectral.ltas` | tvar dlouhodobého průměrného spektra | `mean`, `std`, `skew`, `kurt` |
| `acoustic.spectral.mfcc` | statistiky MFCC c1 až c16 a jejich delt | 66 sloupců, `mean_c1` až `global_delta_c1_16` |
| `acoustic.timing.speech_rate` | tempo řeči ze slabik v přepisu | `speech_rate`, `articulation_rate`, `speech_rate_variability`, `word_count` |
| `acoustic.intensity.stats` | relativní intenzita v řeči | `median`, `mean`, `std`, `kurt` |
| `acoustic.timing.pauses` | délky pauz ze segmentace | `median`, `mean`, `std` |
| `acoustic.timing.segmentation` | časování řeči a dechu ze čtyř tříd conformeru (jen model conformer) | `dpi`, `dbi`, `vsdr`, `ste`, `ssr_change` |
| `acoustic.articulation.vowels` | samohláskový trojúhelník z formantů | `f1a` až `f2u`, `VSA`, `VAI`, `IU`, `FRI`, `SFRI` |
| `linguistic.lexical.*` | slovní zásoba, 8 feature | `content_density`, `content_words`, `function_words`, `mattr`, `word_entropy`, `ngrams`, `npmi`, `rrtr` |
| `linguistic.syntactic.*` | stavba výpovědí, 5 feature | `mlu`, `ccsr`, `scsr`, `csr`, `sdl` |

Tím je přenesených všech osm původních modulů.

Whisper běží s `beam_size=5` a `condition_on_previous_text=false`: model
nedostává předchozí text jako kontext, což u patologické řeči brání
smyčkám halucinací („základní základní základní…“) a přepis je zhruba
o třetinu rychlejší. Na čisté dlouhé řeči to stojí pár slov; zapnout
jde přes `--set transcript.condition_on_previous_text=true`. Nižší
`beam_size` zrychlí několikanásobně, ale u těžkých nahrávek kazí text.

Tempo řeči potřebuje přepis s časy slov, tedy z Whisperu. Lingvistika
potřebuje jazykový rozbor Stanzou, tedy `uv sync --all-extras` a
`speechscope models download --only stanza`. Jazyk se bere z přepisu,
přepíšeš ho přes `--set nlp.language=en`.

Intenzita a pauzy potřebují segmentaci. Intenzita si VAD zapne vždy,
protože se počítá jen z řeči, a globální `--no-vad` se na ni nevztahuje.

Artikulace samohlásek potřebuje rozpoznávač fonémů phnrec, který umí jen
češtinu a jen Windows. Není ke stažení, viz modely níž. Formanty počítá
parselmouth, tedy tentýž Praat, jaký používal původní skript.

Segmentace a přepis hotové jsou, viz níž.

---

## Jak se to používá

### Výběr feature

```bash
# všechno, co dává smysl pro danou úlohu
uv run speechscope extract data\ --task story

# jen akustika, Whisper se vůbec nespustí
uv run speechscope extract data\ --task story --domain acoustic

# konkrétní jména nebo vzory
uv run speechscope extract data\ --task story --features "acoustic.quality.*,acoustic.pitch.f0"
```

Vzor, kterému neodpovídá žádná feature, je chyba. Aktuální seznam vždycky
vypíše `speechscope list`.

### Nastavení parametrů

Každá feature má vlastní parametry s výchozími hodnotami. Vypíšeš si je:

```bash
uv run speechscope list --params acoustic.pitch.f0
```

Změníš je buď jednorázově, nebo souborem:

```bash
uv run speechscope extract data\ --task phonation ^
    --set acoustic.pitch.f0.f_min=75 ^
    --set acoustic.quality.cpp.window_length=0.05

uv run speechscope extract data\ --task phonation --config cfg.yaml
```

```yaml
# cfg.yaml
acoustic.pitch.f0:
  f_min: 75
  semitone_ref_mode: fixed
segments:
  model: conformer
```

Priorita je výchozí hodnota, pak YAML, pak `--set`. Neznámý parametr nebo
hodnota mimo meze je chyba hned na začátku, ne až u desátého souboru.

### Segmentace a přepis

Segmentace a přepis jsou drahé, proto se počítají jednou a ukládají se do
pracovní složky. Můžeš je spustit dopředu:

```bash
uv run speechscope segment    data\ --model conformer --work-dir work\
uv run speechscope transcribe data\ --language cs --work-dir work\
uv run speechscope extract    data\ --task story --vad --work-dir work\ --out out\story.csv
```

Nebo rovnou poslední řádek. Výsledek je stejný, segmentace se spočítá
cestou a uloží se do stejné složky. Modely se berou ze stažené kopie,
cestu jim říkat nemusíš.

Pořadí hledání je vždycky:

1. **Ruční vstup vedle nahrávky.** `p01.labels.txt` pro segmentaci,
   `p01.txt` pro přepis. Člověk má přednost před modelem.
2. **Pracovní složka.** Dřív spočítaný výsledek, pokud sedí otisk.
3. **Model.** Teprve teď se platí.

U každého mezivýsledku leží `.json` s otiskem: který model ho udělal,
s jakými parametry a na jaké nahrávce. Když cokoli z toho nesedí, počítá
se znovu, takže se ti nikdy nepodstrčí segmentace od jiného modelu.

### Modely segmentace

```bash
uv run speechscope segment data\ --model conformer   # vlastní, čtyři třídy
uv run speechscope segment data\ --model pyannote    # řeč a ticho
uv run speechscope segment data\ --model labels      # jen čte hotové labely
uv run speechscope segment data\ --model conformer --cut-audio  # uloží i wav jen s řečí
```

| model | popisky | co je řeč |
|---|---|---|
| `conformer` | `sv` znělá řeč, `su` neznělá řeč, `ps` ticho, `pb` nádech | `sv` a `su` |
| `pyannote` | `speech`, `non-speech` | `speech` |

Vlastní model rozlišuje čtyři třídy, ale pro VAD se `sv` a `su` slévají do
řeči a `ps` s `pb` do pauzy. Zbylé rozlišení se nezahazuje, drží se
v segmentaci, takže z něj půjde počítat třeba podíl nádechů.

Změníš to parametrem `segments.speech_labels`, výchozí hodnota je
`speech,sv,su`:

```bash
# jen znělá řeč, neznělé úseky se budou počítat jako pauza
uv run speechscope extract data\ --task story --vad --set segments.speech_labels=sv
```

Při extrakci model zadávat nemusíš. Bez zadání se vezme, co je v pracovní
složce, a do logu se napíše, čím to vzniklo.

### VAD

Feature dostane signál složený jen z řeči, když je pro ni VAD zapnutý.
Výchozí stav si každá feature určuje sama podle úlohy, přepíšeš ho takhle:

```bash
uv run speechscope extract data\ --task story --vad      # zapnout všem
uv run speechscope extract data\ --task story --no-vad   # vypnout všem
uv run speechscope extract data\ --task story --set acoustic.pitch.f0.use_vad=false
```

Některé feature VAD neumí, třeba pauzy, které řečové úseky samy měří.
S `--vad` a výběrem přes `--domain` nebo hvězdičku se takové feature
spočítají z celé nahrávky a do logu se to napíše. Když feature zadáš
jménem nebo jí VAD zapneš přes `--set`, je to chyba, protože ses o to
řekl výslovně.

### Smíšené složky a metadata

Když máš v jedné složce víc úloh, popiš je manifestem místo `--task`:

```csv
path,task,speaker_id,group
p01_aaa.wav,phonation,PD001,PD
p01_pohadka.wav,story,PD001,PD
p02_aaa.wav,phonation,HC001,HC
```

```bash
uv run speechscope extract data\ --manifest data\manifest.csv --out out\vse.csv
```

Sloupce navíc, tady `speaker_id` a `group`, se přenesou do výstupu.

---

## Výstup

CSV s jedním řádkem na nahrávku:

```
file, path, task, [sloupce z manifestu], speechscope_version,
<feature sloupce>, notes, error
```

Sloupce feature se jmenují podle feature, u vícehodnotových se přidá klíč:
`acoustic.pitch.f0.median`, `acoustic.quality.cpp.cpps`.

Chybějící hodnoty mají tři úrovně, aby bylo jasné, co se stalo:

| situace | výsledek |
|---|---|
| feature nemá na nahrávce z čeho počítat | její sloupce NaN, důvod v `notes` |
| selhal provider | NaN u feature, které ho potřebují, důvod v `notes` |
| nahrávka nejde načíst | prázdný řádek, důvod v `error` |

Sloupce `notes` a `error` se ve výstupu objeví jen tehdy, když je opravdu
co hlásit. Podrobnosti jdou do logu:

```bash
uv run speechscope extract data\ --task story --log-file bezeh.log --log-level DEBUG
```

---

## Použití z Pythonu

```python
from pathlib import Path

import speechscope
from speechscope import config

cfg = config.build(set_items=["acoustic.pitch.f0.semitone_ref_mode=fixed"])

df = speechscope.extract(
    Path("data"),
    task="story",
    features=["acoustic.quality.*", "acoustic.pitch.f0"],
    config=cfg,
    work_dir=Path("work"),
)
```

`speechscope.extract()` je jediná dávková smyčka v projektu. CLI, GUI
i Jupyter volají ji.

Když přidáš `vad=True` do `config.build()`, musí být segmentace po ruce.
Jinak řádky projdou, ale dotčené sloupce budou NaN a důvod bude ve sloupci
`notes`. Spusť si nejdřív `speechscope segment` do stejné `work_dir`.

---

## Rozhraní pro GUI

```bash
uv run speechscope extract data\ --task story --progress-json --out out\story.csv
```

Na stdout jde jeden JSON objekt na řádek, všechno ostatní na stderr:

```json
{"event":"start","protocol":2,"total":42,"task":"story","features":["..."],"providers":["segments","transcript"]}
{"event":"begin","index":0,"path":"..."}
{"event":"stage","index":0,"provider":"segments","status":"running"}
{"event":"stage","index":0,"provider":"segments","status":"done","seconds":4.1}
{"event":"stage","index":0,"provider":"transcript","status":"running"}
{"event":"stage","index":0,"provider":"transcript","status":"cached","seconds":0.01}
{"event":"file","index":0,"path":"...","status":"ok"}
{"event":"begin","index":1,"path":"..."}
{"event":"file","index":1,"path":"...","status":"error","msg":"..."}
{"event":"done","n_ok":41}
{"event":"saved","out":"out/story.csv"}
```

S `--out` se tabulka zapisuje průběžně: po každé nahrávce leží na disku
celá (zápis jde přes dočasný `.part` a přejmenování), takže po zabití
procesu zůstanou hotové řádky. Průběžná verze má i prázdné sloupce
`notes` a `error`, konečná je bez nich.

`begin` přijde před zpracováním nahrávky, `file` po něm. `stage` hlásí
každý provider, který se pro nahrávku opravdu spustil: `running` na
začátku a pak `done`, `cached` (vzal se mezivýsledek z pracovní složky)
nebo `error` (s `msg`), vždy se `seconds`. Provider se volá jednou na
nahrávku, i když ho potřebuje víc feature. Nahrávka, kterou nejde
načíst, má jen `begin` a `file`.

Tvar událostí je smlouva a nemění se bez zvednutí verze (pole `protocol`
v `start`, teď 2). Stejný
`--progress-json` mají i `segment` a `transcribe`; u nich je `features`
prázdný seznam, `providers` má jediný prvek a `saved` nese pracovní složku.

Stdout i stderr jsou **vždy UTF-8**, i když jsou přesměrované do roury nebo
do souboru. CLI si to nastaví samo, `PYTHONUTF8=1` není potřeba. GUI tedy
čte oba proudy jako UTF-8.

Seznam feature, providerů i jejich parametry si GUI vytáhne strojově,
takže nemusí nic duplikovat:

```bash
uv run speechscope list --task story --json
uv run speechscope list --params acoustic.quality.cpp --json
uv run speechscope list --providers --json          # včetně parametrů
uv run speechscope list --params segments --json    # totéž pro jeden provider
```

Odpověď na `--params` má klíč `kind` (`feature` nebo `provider`); provider
nemá `tasks`, `vad` ani `outputs`.

Co GUI předává při každém volání:

- `--models-dir`, jinak se modely hledají v `models/` v aktuálním
  adresáři, což u podprocesu z jiného projektu není to pravé.
- `--work-dir`, aby se segmentace a přepis ukládaly a nepočítaly znovu.
- `--log-file`, jinak se log nikam neukládá a na klinice nebude co číst.
- Před `extract --vad` buď zavolat `segment --model ...`, nebo poslat
  `--set segments.model=conformer`. Výchozí `auto` bere jen hotovou
  segmentaci z pracovní složky a bez ní skončí chybou u každé nahrávky.

---

## Vývoj

```bash
uv run pytest
uv run ruff check src tests
```

Zlaté testy porovnávají novou implementaci se starými skripty. Přeskočí
se, dokud nenastavíš cestu k referenčním nahrávkám:

```bash
set SPEECHSCOPE_GOLDEN_DIR=C:\data\golden
uv run pytest tests\golden
```

Návrh architektury a rozhodnutí za ním jsou v [docs/design.md](docs/design.md).

### Složka legacy

Původní laboratorní skripty se drží v `legacy/` jako předloha pro přenos.
Není v gitu a knihovna ji **nikdy nepotřebuje**, ani na modely. Kdo si
repozitář naklonuje, `legacy/` mít nebude a všechno mu poběží.

Pravidla pro přenos: feature nesmí dělat I/O, konstanty se mění na
parametry, dávkové smyčky a zápis CSV se zahazují, `print` jde do
`logging`. Refaktoring a změna algoritmu nikdy nesmí být v jednom commitu.
Ke každému přenesenému modulu patří zlatý test.

**Nahrávky pacientů nikdy nepatří do gitu.** `.gitignore` je na to
nastavený, `tests/data/` obsahuje jen syntetické signály.
