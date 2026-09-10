# SpeechScope – návrh architektury (v2)

Stav: jádro, CLI a první dvě feature jsou hotové a otestované. Zbytek
tabulky v sekci 10 čeká na přenos.

Hotovo: jádro, CLI, providery `segments` (labely, pyannote, conformer),
`transcript`, `nlp` (Stanza) a `phonemes` (phnrec), a **všech osm legacy
modulů**: `acoustic.quality.cpp`, `acoustic.pitch.f0`,
`acoustic.spectral.ltas`, `acoustic.spectral.mfcc`,
`acoustic.timing.speech_rate`, `acoustic.timing.pauses`,
`acoustic.intensity.stats`, `acoustic.articulation.vowels` a lingvistika
(8 lexikálních a 5 syntaktických feature). 117 testů.

Zlaté testy pokrývají každý přenesený modul a všech devět prošlo naostro
proti kódu v `legacy/` na reálné nahrávce. Staré skripty používají pandas
API odstraněné ve verzi 3; zlaté testy ho překládají fixturou
`legacy_pandas`, do legacy se nesahá.

Artikulace samohlásek: rozpoznávač fonémů phnrec je hotový program z FIT
VUT (jen Windows, jen čeština), volá se jako podproces a leží v úložišti
modelů. Formanty počítá parselmouth se stejným nastavením jako Praat
skript v legacy, ověřeno proti `Praat.exe` bit po bitu. Fonémy se
v úložišti drží v jednotkách 100 ns, protože sekundy v plovoucí čárce
po zaokrouhlení měnily výběr rámců na hranicích samohlásek.

Vědomá odchylka u lingvistiky: šířka okna MATTR byla v legacy odvozená
z celé dávky (nejkratší přepis minus jedna), takže se hodnota nahrávky
měnila podle složky. Teď je to parametr `nlp.mattr_window`, výchozí 50.
Zlatý test ji nastavuje na hodnotu, kterou by legacy odvodil.

---

## 1. Rozložení modulů

```
src/speechscope/
    __init__.py         # __version__, re-export extract(), list_features()
    api.py              # DEFAULTS, extract()  <- jediná dávková smyčka
    signal.py           # Signal, load_signal()
    registry.py         # @provider, @feature, select(), plan(), specs
    params.py           # Param, resolve_params(), validace
    errors.py           # SelectionError, ProviderError, InsufficientData
    config.py           # načtení YAML, merge s DEFAULTS, --set overrides
    logging_setup.py    # jmenný prostor "speechscope", --log-file, --log-level
    _progress.py        # emitor JSON řádků pro GUI
    artifacts.py        # úložiště mezivýsledků, otisk proti zastarání
    cli.py              # typer: list / extract / segment / transcribe / version
    io/
        discover.py     # hledání nahrávek, sidecary, manifest
        writer.py       # zápis CSV
    providers/
        __init__.py     # importuje všechny moduly kvůli registraci
        segments.py     # segmentace, cache, výběr modelu
        transcript.py   # sidecar přepis, cache, jinak Whisper
        backends/
            pyannote_vad.py    # model pyannote/segmentation
            conformer_vad.py   # vlastní WavLM, Conformer a CRF
    features/
        __init__.py
        acoustic/
            quality.py      # cpp, cpps
            pitch.py        # f0 statistiky
            intensity.py    # intenzita a pauzy
            spectral.py     # ltas, mfcc
            timing.py       # speech rate
            articulation.py # vowel articulation
        linguistic/
            lexical.py
            syntactic.py
legacy/                 # staré laboratorní skripty, mimo git
tests/
    data/               # jen syntetické signály
    test_registry.py
    test_params.py
    test_api.py
    test_cli.py
    golden/             # zlaté testy, skipnou se bez referenčních dat
```

---

## 2. Signal

```python
@dataclass(frozen=True, slots=True)
class Signal:
    x: np.ndarray             # float64, mono, rozsah -1..1
    fs: int                   # nativní vzorkovací frekvence souboru
    path: Path
    task: Task
    vad_applied: bool = False # True, když x obsahuje jen řečové segmenty
    text: str | None = None   # ruční přepis ze sidecaru, když existuje

    @property
    def duration(self) -> float: ...
    def resampled(self, fs: int) -> Signal: ...   # cachované
```

Vzorkovací frekvence zůstává nativní, jak jsi potvrdil. Feature, která
potřebuje svou vlastní, si ji vezme sama. To přesně odpovídá stávajícímu
kódu: `legacy/cpp/cpp.py` si uvnitř resampluje na 25 kHz přes `resample_poly`
a tenhle krok se přenese beze změny.

```python
Task = Literal["phonation", "ddk", "story", "monologue", "reading"]
```

Stereo se v `load_signal` redukuje na první kanál, stejně jako to dělají
staré skripty, ne průměrem. Jinak by zlaté testy neseděly.

---

## 3. Parametry feature (nové)

Každá feature deklaruje své parametry v kódu, s výchozí hodnotou, typem,
popisem a volitelnými mezemi. Je to strojově čitelná obdoba `info.json`
ze starých modulů, jen typovaná a bez souboru navíc.

```python
@feature(
    "acoustic.quality.cpp",
    tasks={"phonation", "ddk", "story", "monologue", "reading"},
    version="1.0",
    vad=Vad(supported=True, default={"phonation": False, "_": True}),
    params={
        "window_length": Param(0.041, float, "Délka okna (s)", gt=0),
        "window_overlap": Param(90.0, float, "Překryv oken (%)", ge=0, lt=100),
        "time_smoothing": Param(10, int, "Řád časového vyhlazení cepster (jen CPPS)", ge=1),
        "quefrency_smoothing": Param(5, int, "Řád vyhlazení v kvefrenci (jen CPPS)", ge=1),
    },
    outputs={
        "cpp": "Cepstral peak prominence (dB)",
        "cpps": "Cepstral peak prominence smoothed (dB)",
    },
)
def cpp(sig: Signal, *, cfg: Cfg) -> dict[str, float]:
    return dict(zip(("cpp", "cpps"), cepstral_peak_prominence(sig.x, sig.fs, **cfg)))
```

`outputs` slouží k tomu, aby `speechscope list` uměl vypsat sloupce i s
popisem a aby si GUI mohlo vykreslit formulář. Nese to stejnou informaci
jako `features.names` a `features.descriptions` v `info.json`.

Přepis hodnot má tři cesty, v tomhle pořadí priority:

```bash
# 1. výchozí hodnoty z deklarace feature

# 2. YAML soubor
speechscope extract data/ --task phonation --config cfg.yaml
```

```yaml
# cfg.yaml
acoustic.quality.cpp:
  window_length: 0.05
  window_overlap: 95
acoustic.pitch.f0:
  Fmin: 75
providers:
  segments:
    backend: silero
```

```bash
# 3. jednorázový přepis z příkazové řádky
speechscope extract data/ --task phonation \
    --set acoustic.quality.cpp.window_length=0.05 \
    --set acoustic.pitch.f0.Fmin=75
```

Neznámé jméno parametru nebo hodnota mimo meze je chyba hned na začátku,
ne až u desátého souboru.

---

## 4. Registry

```python
@provider("segments", requires=(), version="1.0")
def segments(sig: Signal, *, cfg: Cfg) -> SegmentsResult: ...

@provider("transcript", requires=("segments",), version="1.0")
def transcript(sig: Signal, *, cfg: Cfg, segments: SegmentsResult) -> Transcript: ...
```

Providery se do feature předávají jako keyword argumenty pojmenované podle
providera. Jméno v `requires` je zároveň jméno parametru.

Mapování návratové hodnoty na sloupce:

| návrat feature | sloupce |
|---|---|
| `float` | `acoustic.quality.cpp` |
| `dict[str, float]` | `acoustic.quality.cpp.cpp`, `acoustic.quality.cpp.cpps` |

```python
def select(task, domain=None, names=None) -> list[FeatureSpec]:
    """Glob v names. Neznámé jméno i jméno mimo úlohu -> SelectionError."""

def plan(features) -> list[ProviderSpec]:
    """Tranzitivní uzávěr requires, topologické seřazení."""
```

---

## 5. VAD (nové)

VAD není obyčejný parametr, protože rozhoduje o tom, jestli se vůbec spustí
provider `segments`. Řeším ho jako samostatnou vlastnost feature.

- Feature deklaruje `vad=Vad(supported=..., default=...)`. `default` je
  slovník podle úlohy, klíč `"_"` je hodnota pro zbytek. U fonace tedy
  může být VAD vypnutý a u vyprávění zapnutý, aniž by to uživatel řešil.
- Feature, která VAD nepodporuje nebo ho naopak vyžaduje vždycky, to řekne
  v deklaraci a přepis se odmítne s chybou.
- Přepis: `--vad` a `--no-vad` na celou dávku, nebo cíleně
  `--set acoustic.pitch.f0.use_vad=false`.

Když je VAD pro danou feature aktivní, **aplikuje ho běhové prostředí, ne
feature**. Feature dostane `Signal`, jehož `x` už obsahuje jen zřetězené
řečové segmenty a `vad_applied=True`. To je přesně to, co dnes dělá ručně
každý `main.py` ve starých modulech, takže vnitřní výpočetní funkce se
přenesou beze změny a zlaté testy sednou.

Provider `segments` má dvě větve, stejně jako `transcript`:

1. Vedle nahrávky je soubor s labely ve tvaru `zacatek<TAB>konec<TAB>popisek`,
   filtruje se `speech`. Jméno je `<zaklad>.labels.txt`, tedy `p01.wav` a
   `p01.labels.txt`, aby nekolidovalo se sidecarem ručního přepisu `p01.txt`.
   Adresář se dá přesměrovat parametrem `segments.labels_dir`.
2. Jinak se sáhne do pracovní složky a teprve pak se spustí model. Podrobně
   v sekci 14.

Do té doby `segments` bez labelů skončí `ProviderError` s jasnou hláškou,
ne tichým fallbackem na celý signál.

---

## 6. Chybějící data, NaN a logování (nové)

Tři úrovně, aby bylo jasné, co se stalo:

| situace | výsledek |
|---|---|
| feature vyhodí `InsufficientData("příliš krátký úsek znělé řeči")` | její sloupce = NaN, ostatní feature v řádku se spočítají, důvod jde do sloupce `notes` a do logu |
| provider selže | NaN u všech feature, které ho potřebují, důvod do `notes` |
| soubor nejde načíst | celý řádek prázdný, důvod do sloupce `error` |

Sloupec `notes` má tvar `acoustic.pitch.f0=nenalezena znělá řeč; ...`.

Logování přes standardní `logging` ve jmenném prostoru `speechscope`.
CLI má `--log-file` a `--log-level`. Každý záznam nese cestu k souboru a
jméno feature, takže se dá zpětně dohledat, proč konkrétní buňka chybí.
Při `--progress-json` jde log na stderr, stdout zůstává čistý.

Vědomá odchylka od starých skriptů: `cpp.py` i `f0.py` dnes při selhání
vrací `0.0`. To je nebezpečné, protože nula je u CPP platná hodnota v dB a
v analýze se tváří jako měření. Nová implementace vrátí NaN. **Tohle je
změna chování, potřebuje tvoje potvrzení** a ve zlatém testu se takové
nahrávky vyloučí z porovnání.

---

## 7. api.extract a metadata

```python
def extract(
    inputs: Path | Iterable[Path],
    *,
    task: Task | None = None,
    features: Sequence[str] | None = None,
    domain: str | None = None,
    cfg: Config | None = None,
    manifest: Path | None = None,
    vad: bool | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> pd.DataFrame
```

Sloupce výstupu v tomto pořadí:

```
file, path, task, [další sloupce z manifestu], speechscope_version,
<feature sloupce v pořadí výběru>, notes, error
```

`file` je jméno souboru bez cesty, jak jsi chtěl. Když použiješ manifest a
má další sloupce (`speaker_id`, `group`, `visit`), přenesou se do výstupu
beze změny. To je druhá, pohodlnější cesta k metadatům než parsování názvu.

Manifest zůstává jako záložní varianta pro smíšené složky. Běžný provoz je
jedna složka na úlohu, případně složka pacienta s podsložkami úloh:

```bash
speechscope extract data/PD001/phonation --task phonation --out out/phon.csv
speechscope extract data/ --recursive --manifest data/manifest.csv --out out/all.csv
```

Průběh zůstává: `select()` a `plan()` jednou dopředu, pak soubor po souboru,
cache providerů se po každém souboru zahodí, modely zůstávají v procesu.
Dávka běží sekvenčně.

---

## 8. CLI

```
speechscope list [--task story] [--domain acoustic] [--params NAME] [--json]
speechscope extract PATH... --task story
        [--features "acoustic.timing.*,linguistic.lexical.*"]
        [--domain acoustic] [--manifest m.csv] [--config cfg.yaml]
        [--set NAME.PARAM=VALUE]... [--vad/--no-vad]
        [--out out.csv] [--progress-json] [--log-file f.log] [--log-level INFO]
        [--recursive/--no-recursive]
speechscope version
```

`speechscope list --params acoustic.quality.cpp` vypíše parametry, výchozí
hodnoty, meze a popisy. Tohle je zdroj dat pro formulář v GUI, aby nemuselo
duplikovat žádný seznam.

Smlouva `--progress-json` beze změny podle CLAUDE.md.

---

## 9. Testy

- `test_registry.py` – select, chybové stavy, plan neplánuje Whisper pro
  `--domain acoustic`.
- `test_params.py` – priorita default < YAML < `--set`, odmítnutí neznámého
  parametru a hodnoty mimo meze.
- `test_api.py` – syntetické signály, jména a pořadí sloupců, `notes` a
  `error`, průchod metadat z manifestu.
- `test_cli.py` – `CliRunner`, tvar JSON řádků.
- `tests/golden/` – `assert_allclose(rtol=1e-6)` proti starým skriptům,
  skipne se bez `SPEECHSCOPE_GOLDEN_DIR`.

---

## 10. Mapování starých modulů na nová jména

| legacy | nové jméno | úlohy | providery |
|---|---|---|---|
| `cpp` | `acoustic.quality.cpp` | všechny | segments (volitelně) |
| `f0` | `acoustic.pitch.f0` | všechny | segments (volitelně) |
| `int_pauses` | `acoustic.intensity.stats`, `acoustic.timing.pauses` | story, monologue, reading | segments |
| `timing` (notebook) | `acoustic.timing.segmentation` (DPI, DBI, VSDR, STE, ΔSSR) | story, monologue, reading | segments, jen čtyři třídy conformeru |
| `ltas` | `acoustic.spectral.ltas` | všechny | segments (volitelně) |
| `mfcc` | `acoustic.spectral.mfcc` | všechny | segments (volitelně) |
| `speech_rate` | `acoustic.timing.speech_rate` | story, monologue, reading | transcript |
| `vowel_articulation` | `acoustic.articulation.vowels` | jen čeština | vlastní LSTM detektor |
| `linguistics` | `linguistic.lexical.*`, `linguistic.syntactic.*` | story, monologue, reading | transcript |

Jazyk je parametr, ne úloha. `speech_rate` a `linguistics` už dnes berou
`language` jako vstup, takže vícejazyčnost je v pořádku. Jediná výjimka je
`vowel_articulation`, kde je detektor fonémů natrénovaný jen na češtinu.
Ta feature bude na jinou než českou nahrávku hlásit `InsufficientData`.

---

## 11. Nálezy v legacy kódu, které chci potvrdit

1. **`cpp/main.py` má jiný default překryvu než `cpp/info.json`.** JSON říká
   90, fallback v kódu 95. Která hodnota je ta správná? Do knihovny dám tu,
   kterou určíš.
2. **`f0.py` převádí na půltóny vůči `f_min`**, tedy
   `12 * log2(voiced / f_min)`. Změna parametru `Fmin` tím posune všechny
   statistiky, ne jen ořez rozsahu. Je to záměr, nebo má být reference pevná
   (typicky 100 Hz nebo medián mluvčího)? Zatím přenesu beze změny.
3. **`useVAD` je dnes řetězec `"True"` / `"False"`.** V nové knihovně to bude
   `bool`. Žádný dopad na výsledky.
4. **Parametr `extension`** je záležitost dávkové smyčky, ne feature.
   Nahradí ho `--recursive` a filtr přípon v `discover.py`.

---

## 12. Závislosti k doplnění

`libf0` kvůli SWIPE. Později `pyphen` a `stanza` pro lingvistiku,
`faster-whisper` do extras `[whisper]`, `onnxruntime` do `[onnx]` pro
segmentaci bez torche (oba modely převedené z torchových vah, viz README).

---

## 13. Pořadí prací

1. `signal.py`, `errors.py`, `params.py`, `registry.py`, `config.py`,
   `logging_setup.py` a jejich testy.
2. `api.extract()` a `cli.py` včetně `--progress-json`.
3. `acoustic.quality.cpp` a `acoustic.pitch.f0` přenesené beze změny
   algoritmu, se zlatým testem.
4. Provider `segments` nad label soubory, až dodáš segmentaci.
5. Zbytek modulů z tabulky v sekci 10, každý jako samostatný commit.

---

## 14. Mezivýsledky na disku

Legacy měl segmentaci a přepis jako „helper" moduly. Nepočítaly žádnou
feature, jen zapsaly labely do `output/segmentation/labels/` a přepisy do
`output/transcription/`, a feature moduly si to odtamtud přečetly.

Tady je to totéž, ale přes jedny dveře. Provider je ta jediná cesta
k segmentaci a přepisu a sám se stará o to, odkud je vezme:

1. **Ruční vstup vedle nahrávky.** `p01.labels.txt` pro segmentaci,
   `p01.txt` pro přepis. Člověk má vždycky přednost před modelem.
2. **Pracovní složka.** Dřív spočítaný výsledek, pokud sedí otisk.
3. **Model.** Teprve teď se platí, a výsledek se rovnou uloží.

Proto `speechscope segment` není zvláštní režim výpočtu. Je to jen
předplnění cache, po kterém `extract` běží bez modelu:

```bash
speechscope segment data\ --model pyannote --cut-audio --work-dir work\
speechscope transcribe data\ --language cs --work-dir work\
speechscope extract data\ --task story --vad --work-dir work\ --out out\story.csv
```

Kdo napíše rovnou třetí řádek, dostane úplně stejný výsledek. Segmentace
se spočítá cestou a uloží se do stejné složky.

### Rozvržení pracovní složky

```
work/
    segments/p01.txt          zacatek<TAB>konec<TAB>popisek
    segments/p01.json         čím a s jakými parametry to vzniklo
    segments/cut/p01.wav      jen řeč, když se vyžádá --cut-audio
    transcript/p01.txt        holý text
    transcript/p01.words.json slova s časy
    transcript/p01.json       čím a s jakými parametry to vzniklo
```

Do složky s nahrávkami se nikdy nezapisuje, ta je jen ke čtení. Bez
`--work-dir` jde pracovní složka vedle výstupního CSV.

### Otisk, aby se nepoužila cizí segmentace

Soubor `.json` u každého mezivýsledku nese jméno provideru, jeho verzi,
model, parametry ovlivňující výsledek a velikost i čas změny nahrávky.
Když cokoli z toho nesedí, výsledek se spočítá znovu. Bez toho by
přepnutí z pyannote na vlastní model tiše použilo staré labely.

### Dva modely segmentace

| model | popisky | poznámka |
|---|---|---|
| `labels` | podle souboru | jen čte, nic nepočítá |
| `pyannote` | `speech`, `non-speech` | vlastní binarizace jako v legacy |
| `conformer` | `sv`, `su`, `ps`, `pb` | znělá a neznělá řeč, ticho, nádech |

Vlastní model dává víc informace než pyannote, protože rozlišuje ticho od
nádechu. `SegmentsResult` proto nese všechny popisky, ne jen řeč, a modul
s pauzami z toho bude moci těžit. Co se počítá jako řeč, říká parametr
`segments.speech_labels`, výchozí je `speech,sv,su`.

Výběr modelu: `--model` u příkazu `segment`, nebo
`--set segments.model=conformer` kdekoli jinde.

### Okna u vlastního modelu segmentace

WavLM i Conformer mají self-attention, jejíž paměť roste s druhou mocninou
počtu rámců. Při kroku 10 ms má dvouminutová nahrávka 12 tisíc rámců
a výpočet v celku potřebuje kolem 10 GB. Změřeno na RTX 5060 s 8 GB:
60 s zvuku vezme 2.58 GB, dvě minuty se nevejdou a Windows začnou
odkládat do systémové paměti, takže výpočet po dvanácti minutách
nedoběhl.

Provider `segments` verze 2.1 proto počítá conformer po oknech
(`chunk_seconds`, výchozí 30, a `chunk_overlap`, výchozí 5). Překryv se
dělí napůl, první polovinu dá dřívější okno, druhou pozdější, takže každý
rámec má kontext aspoň půl překryvu na obě strany. Oba parametry jsou
v otisku cache, změna je přepočítá.

Změřený dopad na dvouminutové nahrávce proti výpočtu v celku:

| okna / překryv | čas na CPU | segmentů | řeč | jiné rámce |
|---|---|---|---|---|
| celek | 35.5 s | 534 | 98.31 s | – |
| 30 / 5 | 14.6 s | 534 | 98.30 s | 1.90 % |
| 30 / 10 | 18.1 s | 534 | 98.40 s | 1.80 % |
| 20 / 5 | 12.7 s | 539 | 98.08 s | 2.24 % |
| 60 / 10 | 25.0 s | 531 | 98.54 s | 1.26 % |

Rozdíly jsou posuny hranic o jeden rámec, ne jiné segmenty. Výchozí 30/5
je kompromis mezi pamětí (gigabajt, vejde se i na slabé karty) a shodou.
Na kartě trvá dvouminutová nahrávka 5 s a s procesorem se shoduje do
posledního rámce. Výpočet v celku zůstává přes `chunk_seconds=0`.

### Váhy modelů

Váhy vlastního modelu se vezou s knihovnou jako
`speechscope/models/segmentation_conformer.pt` a jsou i ve wheelu, takže
`--set segments.model=conformer` funguje bez nastavování cest. Jiné váhy
se podstrčí přes `segments.model_path`.

Velké modely se do repa nedávají. Whisper se bere z cesty nebo z Hugging
Face, pyannote taky, WavLM si stáhne `transformers` do své cache.

### Stav ověření

| část | stav |
|---|---|
| ruční labely, cache, otisk, `segment`, `transcribe` | ověřeno testy i naostro |
| přepis přes faster-whisper | ověřeno na lokálním modelu CT2 large-v3 |
| pyannote | ověřeno na lokálních vahách z legacy |
| conformer | ověřeno na vahách dodaných s knihovnou |
| celý řetěz nad reálnou nahrávkou | ověřeno s oběma modely segmentace |

Obě segmentace se na dvouminutovém monologu shodly na množství řeči
(99.94 s proti 98.31 s) a feature z nich vyšly do jednoho procenta stejně,
kromě minima F0, což je jediná krajní hodnota a je proto citlivější.

### Závislosti a prostředí

Jedno prostředí, jeden příkaz:

```bash
uv sync --all-extras
```

Oba modely potřebují torch. `pyannote` navíc `pyannote.audio` a
`omegaconf`, `conformer` ještě `transformers` a `pytorch-crf`. Jsou
v extras, takže kdo je nepotřebuje, nainstaluje si jen základ a knihovna
mu při pokusu o jejich použití řekne, co doplnit.

Ověřeno, že se extras neperou. Po `uv sync --all-extras` zůstávají
`numpy` 2.4.6, `pandas` 3.0.5, `scipy` 1.17.1 i `soundfile` 0.14.0 na
svých verzích, nic se neodebírá ani nesnižuje. Cena je zhruba 2,5 GB
navíc kvůli torchi.

Verze `pyannote.audio` 3.x, kterou pinuje legacy, už použít nejde: její
import padá na `torchaudio.AudioMetaData`, které bylo z torchaudio
odstraněno. Proto je v extras verze 4.

### Kde leží modely

Modul `modelstore.py` je jediné místo, které o tom rozhoduje. Hledá se
v adresáři z proměnné `SPEECHSCOPE_MODELS`, jinak v `models/` vedle
projektu. Nic se nikdy nebere ze složky `legacy/`, ta je jen archiv
původních skriptů a v provozu nebude existovat.

| model | soubor v adresáři | velikost |
|---|---|---|
| Whisper large-v3 pro CTranslate2 | `whisper-large-v3-ct2/` | 3 GB |
| WavLM base | `wavlm-base/` | 380 MB |
| pyannote/segmentation | `pyannote_segmentation.bin` | 17 MB |

Do gitu nejdou, `models/` je v `.gitignore`. Kdo si repozitář naklonuje,
spustí `speechscope models download`. Příkaz `models list` ukáže, co
chybí. Snapshot pyannote v legacy byl navíc rozbitý, soubory ve
`snapshots/` měly nulovou velikost a data ležela ve `blobs/`.

Rozhodnutí nedávat modely do gitu: tři a půl gigabajtu by z každého klonu
udělalo dlouhé stahování a historie by se tím nafoukla natrvalo, protože
git binární soubory nediferencuje. Git LFS by to zmírnil, ale znamená
další nastavení a kvótu na serveru. Stahovací příkaz je levnější a modely
se stejně mění málokdy.

Váhy vlastního modelu jsou výjimka a jdou přímo v balíčku
(`src/speechscope/models/segmentation_conformer.pt`), protože jsou naše
a mají 21 MB.
