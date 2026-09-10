# SpeechScope – pokyny pro Claude Code

## Co to je
Python knihovna + CLI pro extrakci řečových feature (biomarkerů) z klinických
nahrávek – parkinsonismus, iRBD. Vzniká refaktoringem existujících
laboratorních skriptů. Tohle repo je **jen knihovna a CLI**; desktopová
aplikace (Flet) žije v samostatném repu a volá odsud CLI jako subprocess.

## Architektura – neměnit bez domluvy
- **Provider** = drahý mezivýsledek (`segments`, `transcript`, později `f0`,
  `embeddings`), počítá se nejvýš jednou na nahrávku, sdílí ho všechny feature.
  Registrace `@provider("name", requires=[...], version=...)` v `providers/`.
- **Feature** = funkce `f(sig: Signal, *, cfg, **providery) -> dict | float`.
  Registrace `@feature("domena.skupina.nazev", tasks={...}, requires=[...], version=...)`.
- **Jméno feature** = `acoustic.*` nebo `linguistic.*`. Úloha NENÍ ve jménu –
  feature jen deklaruje `tasks`, pro které dává smysl. Stejný kód pro pauzy
  běží na pohádce, monologu i čtení.
- **Úlohy**: `phonation`, `ddk`, `story` (vyprávění pohádky), `monologue`,
  `reading` (čtený text). Úloha je vlastnost nahrávky, nedetekuje se.
- **Výběr**: `select(task, domain, names)` v `registry.py`. Feature mimo úlohu
  = `SelectionError`, nikdy tiché NaN. Spouští se jen providery, které vybrané
  feature potřebují (`plan()`), takže `--domain acoustic` nikdy nespustí Whisper.
- **Výstupní sloupce**: `acoustic.pitch.f0.median`, `acoustic.quality.cpp.cpp`
  + `file`, `path`, `task`, `speechscope_version`, volitelně `notes` a `error`.
- Jediná dávková smyčka je `api.extract()`. CLI, GUI i Jupyter volají ji.
  `api.prepare()` spustí jediný provider nad dávkou, stojí za `segment`
  a `transcribe`.
- Modely se načítají líně (`_get_model()`), jednou na proces. Těžké importy
  (`torch`, `pyannote`, `transformers`, `faster_whisper`) jsou uvnitř funkcí,
  ne na úrovni modulu.
- Implementace modelů žijí v `providers/backends/`. Registr je při načítání
  přeskakuje, protože se v nich nic neregistruje.

## Parametry
- Každá feature i provider deklaruje parametry přes `Param(default, typ, popis,
  meze/choices)`. Je to typovaná náhrada za `info.json` ze starých modulů
  a jediný zdroj pravdy pro `speechscope list --params` i pro GUI.
- Priorita: výchozí hodnota < YAML z `--config` < `--set jmeno.parametr=hodnota`.
- Neznámý parametr nebo hodnota mimo meze = `ConfigError`, a to **před** otevřením
  první nahrávky. Nikdy tiché ignorování.

## Mezivýsledky a modely
- Segmentace a přepis se ukládají do pracovní složky (`--work-dir`). Provider
  se ptá v pořadí: ruční vstup vedle nahrávky (`p01.labels.txt`, `p01.txt`),
  pak úložiště, teprve pak model.
- U každého mezivýsledku leží `.json` s otiskem (provider, verze, model,
  parametry, velikost a čas změny nahrávky). Nesedící otisk = přepočítat.
  Změna algoritmu ⇒ zvednout `version` provideru, tím se cache zneplatní.
- Do složky s nahrávkami se **nikdy nezapisuje**, je jen ke čtení.
- Segmentace má modely `auto` (výchozí, vezme co je v pracovní složce),
  `labels`, `pyannote` a `conformer`. Vlastní model dává čtyři třídy
  (`sv`, `su`, `ps`, `pb`); co je řeč, říká `segments.speech_labels`.
- Velké modely nejsou v gitu. `modelstore.py` je jediné místo, které ví, kde
  leží: `SPEECHSCOPE_MODELS`, jinak `models/`. Stahuje je
  `speechscope models download`. **Nikdy nic nenačítat ze složky `legacy/`**,
  ta v provozu nebude existovat.
- Model je „na místě“ jen se všemi `markers` z katalogu (samotná složka
  nestačí, zůstane po přerušeném stažení). Stahování i rozbalení jde přes
  `<model>.part` a na místo se přejmenuje až hotové (`_install`). Balík pro
  kliniky: `models pack` → zip bez komprese s `manifest.json` (SHA-256),
  `models unpack` ho ověřuje; verze formátu `BUNDLE_VERSION`.
- Výjimka jsou váhy vlastního modelu segmentace, ty jdou v balíčku
  (`src/speechscope/models/`), protože jsou naše a mají 21 MB.

## Kde se počítá (změřeno 9. 9. 2026 na RTX 5060 Laptop, 8 GB, driver 577)
- **Whisper jede na grafice sám.** ctranslate2 má vlastní akceleraci nezávislou
  na torchi. `transcript.device` je `auto`, `compute_type` taky `auto` – to
  vybere nejrychlejší podporovanou přesnost. Na `float16` trvá dvouminutová
  nahrávka 97 s, na `int8_float16` 46 s, proto je `auto` výchozí.
- **Segmentace jede přes ONNX na procesoru** (sekce níže); torchová cesta
  zůstává jako záloha a přináší `torch+cpu`. Časy torch na dvouminutové
  nahrávce: pyannote 12 s, conformer 39 s.
  Conformer škáluje lineárně (30 s → 10.8 s, 60 s → 18.9 s, 120 s → 39.4 s).
- **CUDA se neinstaluje.** Balíčky vezou běhové prostředí s sebou, na stroji
  stačí ovladač NVIDIA. Žádný CUDA Toolkit.
- **Verze CUDA musí sedět na ovladač.** Driver 577 zvládá 12.8, ne 13.0.
  Sestavení pro 13.0 se nainstaluje, ale kartu nevidí. Pro Windows a Python
  3.11 končí index `cu128` na torchi 2.9.1, takže by to znamenalo jít dolů
  z 2.14. Neprovedeno, `uv.lock` je čistý.
- **Sestavení s CUDA funguje i bez použitelné karty.** Vypíše varování a
  spočítá to na procesoru. Ověřeno, že labely jsou bit po bitu shodné, takže
  pro rozdávanou aplikaci stačí jedno sestavení pro obojí.
- Conformer v celku se na kartu **nevejde**: paměť roste s druhou mocninou
  délky (60 s = 2.58 GB, 2 min ≈ 10 GB), Windows odkládají do RAM a běh
  nedoběhne. **Vyřešeno okny**: `segments` 2.1 počítá conformer po
  `chunk_seconds` (30) s `chunk_overlap` (5). Na kartě pak 2 min = 5 s,
  na CPU 14.6 s místo 35.5 s. Popisky se liší na 1.9 % rámců (posun hranic
  o rámec), GPU a CPU se stejnými okny jsou shodné do rámce. Detail
  v `docs/design.md`. Torch s CUDA pro tenhle stroj: `torch==2.9.1+cu128`
  z indexu PyTorche, v hlavním prostředí zatím není.
- Do logu se vždy píše, na čem se počítá. Na klinickém počítači to bude
  jediná diagnostika, proč něco trvá dlouho.

## ONNX (hotovo 10. 9. 2026, obě segmentace)
Segmentace běží přes `onnxruntime` bez torche; `segments.runtime=auto` bere
ONNX, když existuje `models/segmentation-onnx`, jinak torch. Torch zůstává
jen pro export (`speechscope models download --only onnx` →
`onnx_export.export_all`) a pro Stanzu.

Soubory a kód:
- `providers/backends/onnx_session.py` – hledání složky, výběr providerů
  (`auto` → DirectML > CUDA > CPU), cache session.
- `providers/backends/conformer_onnx.py` – WavLM vrstva 3 (dva průchody
  0/10 ms) + conformer emise + Viterbi v numpy (`crf_viterbi.py`), okna
  přes `iter_windows`/`stitch` sdílené s torchovou cestou.
- `providers/backends/pyannote_onnx.py` – posouvání okna, `aggregate`
  (Hamming), `crop_loose`, `binarize` (hystereze, `support`), `discretize`;
  přepsáno z pyannote řádek po řádku, každý krok má test proti originálu.
- `onnx_export.py` – exporty: statické na celé okno (opset 17, `dynamo=False`)
  pro DirectML, dynamické (`dynamo=True`, opset 18) pro CPU a poslední kratší
  okno. Po každém exportu kontrola proti torchi. Každý export potřebuje
  **čerstvý modul** (`fresh_wavlm`, `fresh_conformer`), dynamo rozbije
  následný TorchScript export.
- `_patch_for_directml`: GLU přes řezy místo Split (špatně na NVIDII i Intelu),
  bias `(1,1,D)` místo 1-D (špatně na NVIDII). Matematika stejná, 5.7e-06.

Ověřeno: na skutečné dvouminutové nahrávce oba modely **shoda všech rámců
po 10 ms** s torchem (conformer 534 segmentů, pyannote 71). Časy na CPU
včetně načtení: conformer 52 → 34.7 s, pyannote 10.3 → 4.3 s. WavLM na okno
30 s: torch 1 666 ms, ONNX CPU 611, DirectML NVIDIA 195, Intel Arc 95.

Známé meze:
- pyannote je ve **float32 sám nestabilní na digitálním tichu** (nuly, šum
  1e-3): torch32 vs torch64 až 0.12, ONNX pak posune hranici o rámec. Se
  šumem 1e-2 shoda 2e-4; testy proto používají šumové pozadí, ne nuly.
- Extra `onnx` dává na Windows `onnxruntime-directml` (karta i CPU), jinde
  základní `onnxruntime`. faster-whisper chce základní `onnxruntime`, proto
  `[tool.uv] override-dependencies` v pyproject ho na Windows vypouští.
  **Oba balíčky nesmí být vedle sebe** (stejný modul; odinstalace jednoho
  smaže soubory druhému, pak `uv sync --reinstall-package
  onnxruntime-directml`). GUI repo musí mít stejný přepis.
- `onnx_session.Session` obaluje sezení: `run(feed)` při pádu na kartě
  (např. paměť) otevře sezení znovu jen na CPU, zaloguje varování a okno
  přepočítá. Test `test_pad_na_karte_prepne_na_procesor`.
- pyannote na kartě nic nezíská (13 ms CPU vs 18 ms DML na okno), proto
  `onnx_device=auto` u něj znamená CPU; conformer bere kartu.
- Reálná nahrávka 2 min, DirectML: conformer 8.1 s (CPU 11.6, torch 19.4),
  popisky shodné na všech třech cestách.
- **Distribuce ONNX modelů:** zip jako příloha vydání `models-onnx-v1`
  v soukromém repu (`ONNX_RELEASE_REPO/TAG/ASSET` v `modelstore.py`).
  `_download_onnx` nejdřív zkusí stáhnout (token z `GITHUB_TOKEN`/`GH_TOKEN`
  nebo `gh auth token`; přesměrování na úložiště zahazuje hlavičku
  Authorization), bez tokenu nebo při chybě spadne na export z torche.
  `speechscope models pack` vyrobí zip (287 MB, deflate). Do gitu se modely
  nedávají: soubory nad 100 MB GitHub odmítá, LFS má malé limity. Změna
  exportu = nová značka vydání. Zip nahrává uživatel ručně přes web, `gh`
  na tomhle stroji není.
- Cache artefaktů nese `runtime` v podpisu, přepnutí cesty přepočítá.
- `doctor` má jednu položku `segments` s `paths` (onnx, conformer,
  pyannote); připravená je při jedné funkční cestě. Ověřeno v prostředí
  jen s extra `onnx`: bez torche segmentace obou modelů, popisky shodné
  s hlavním prostředím.
- `--vad` u feature, která VAD neumí: chyba jen při zadání jménem nebo
  cíleném `--set feature.use_vad=true`; přes `--domain`/hvězdičku se
  spočítá bez VAD a zaloguje (`Vad.resolve(strict=False)` v `api._prepare`).
- Log jde jen na stderr; soubor přes `--log-file`, úroveň `--log-level`.
  GUI má `--log-file` předávat, jinak se nic neukládá.

## Pravidla pro přenos starých skriptů
1. Feature funkce nesmí dělat I/O – dostane `Signal`, vrátí čísla.
2. Konstanty natvrdo → deklarované parametry.
3. Dávkové smyčky a zápis CSV ze skriptu zahodit.
4. `print` → `logging`.
5. Ke každému přenesenému modulu zlatý test: výstup nové implementace na
   referenčních nahrávkách musí sedět na starý skript (`assert_allclose`,
   `rtol=1e-6`). Refaktoring a změna algoritmu nikdy v jednom commitu.
6. Změna algoritmu = zvednout `version` u feature/provideru.
7. Staré skripty vracely při chybě `0.0`. Nová implementace místo toho
   vyhodí `InsufficientData`, sloupce budou NaN a důvod skončí v `notes`.
   Nula je platná hodnota a nesmí se tvářit jako měření.

## Závislosti a prostředí
- Správa přes `uv`; `uv.lock` se commituje. Python 3.11.
- Základ: numpy, scipy, soundfile, praat-parselmouth, pandas, pyyaml, typer,
  rich, libf0.
- Extras: `[whisper]` faster-whisper, `[onnx]` onnxruntime + onnx + onnxscript
  (export), `[pyannote]`
  pyannote.audio a torch, `[dnn]` torch, torchaudio, transformers, pytorch-crf.
  **Ne openai-whisper, ne TensorFlow** – TF modely se převádí do ONNX.
  Torch je jen v extras, nikdy v základu.
- Ověřeno, že se extras neperou: `uv sync --all-extras` nesnižuje numpy,
  pandas ani scipy.
- pyannote.audio 3.x už nejde použít, import padá na `torchaudio.AudioMetaData`.
- Dev: pytest, ruff.

## Příkazy
```
uv sync --all-extras
uv run speechscope models download
uv run speechscope list --task story
uv run speechscope list --params acoustic.pitch.f0
uv run speechscope segment data/ --model conformer --work-dir work/
uv run speechscope transcribe data/ --language cs --work-dir work/
uv run speechscope extract data/ --task phonation --out out/phon.csv
uv run speechscope extract data/ --task story --vad --work-dir work/ --progress-json
uv run pytest
uv run ruff check src tests
```

## Rozhraní pro GUI (smlouva, neměnit bez zvednutí verze)
`speechscope extract ... --progress-json` píše na stdout JSON řádky:
`start` (total, task, features, providers) → `file` (index, path, status ok|error, msg)
→ `done` (n_ok) → `saved` (out). Všechno ostatní jde na stderr.

Totéž `--progress-json` mají `segment` a `transcribe` (`features` prázdné,
`providers` jednoprvkové, `saved` = pracovní složka).

Seznam feature a jejich parametry si GUI vytáhne přes `list --json`
a `list --params NAME --json`, nic neduplikuje. `--params` bere i jméno
provideru (`segments`, `transcript`, …), odpověď má `kind`
(`feature`/`provider`); `list --providers --json` dá všechny providery
rovnou s parametry.

Stdout i stderr jsou vždy UTF-8: vstupní bod `cli.run()` volá
`_utf8_streams()`, které přesměrované proudy přepne z cp1252
(`sys.stdout.reconfigure`). `PYTHONUTF8=1` tedy není nutné. Entry point
v pyproject je `speechscope.cli:run`, ne `:app`; `app` zůstává pro testy
(`CliRunner`).

## Stav práce (10. 9. 2026)
Na `main` je všechno: jádro, CLI, providery, všech osm přenesených
legacy modulů, okna conformeru, doctor, ONNX segmentace. Poslední commit
182a051 (časování ze segmentace), 163 testů. Nic necommitovaného kromě tohoto souboru.

**Nálezy z GUI (10. 9. 2026), opraveno, necommitováno:** UTF-8 výstup bez
`PYTHONUTF8`, `list --params <provider>` a `list --providers`; 5 nových
testů v `test_cli.py`. GUI má dočasně opsané parametry providerů
v `contract.PROVIDER_PARAMS`, po téhle změně je může smazat.

**Přenesené moduly (117 testů + 9 zlatých naostro):**
- provider `nlp` (Stanza) a 13 lingvistických feature v
  `features/linguistic/lexical.py` a `syntactic.py`
- `acoustic.spectral.ltas`, `acoustic.spectral.mfcc` v `spectral.py`
- `acoustic.timing.speech_rate` a `acoustic.timing.pauses` v `timing.py`
- `acoustic.timing.segmentation` (DPI, DBI, VSDR, STE, ΔSSR) v `timing.py`,
  z notebooku `legacy/timing`. Jen čtyři třídy conformeru, na pyannote
  `InsufficientData`. Test vytahuje funkce přímo z notebooku (12 náhodných
  posloupností + zlatý test na labelech). **Notebook má i ruční posuny
  hodnot podle diagnostické skupiny** (`all_ps_vals` v `extract_features`,
  entropie v buňce 3); ty do feature nepatří a nepřenesly se, feature
  odpovídají nezměněnému `get_advanced_features`.
- `acoustic.intensity.stats` v `intensity.py` (VAD vyžaduje vždy)
- provider `phonemes` (phnrec jako podproces) a
  `acoustic.articulation.vowels` v `articulation.py` (formanty parselmouth)
- `SegmentsResult.non_speech` (úseky s neřečovým popiskem, tak jak je dal model)
- `Vad(required=True)` ignoruje globální `--no-vad` a jen to zaloguje
- `FeatureSpec.description` pro feature, které vrací jediné číslo
- zlaté testy pro všechno v `tests/golden/`, fixtura `legacy_pandas`
  překládá pandas API odstraněné ve verzi 3, které staré skripty volají
- `transcript.compute_type` z `default` na `auto` (dvojnásobné zrychlení);
  log říká, na čem se počítá; README o grafické kartě
- extras `nlp`; v `modelstore` Stanza (stahuje se) a phnrec (nestahuje se,
  jen se zkopíruje do `models/phnrec/`); `scikit-learn` a `pyphen` v základu

Ověřeno naostro na `data/zkouska.wav`: všechny feature bez poznámek.
Referenční složka pro zlaté testy se sestaví z nahrávky plus
`<zaklad>.labels.txt`, `.phonemes.txt`, `.words.json` a `.txt` vedle ní
(vše jsou výstupy providerů z pracovní složky).

**Vědomé odchylky proti legacy (kromě NaN místo 0.0):**
- `nlp.mattr_window` je parametr s výchozí hodnotou 50; legacy odvozoval
  šířku okna z celé dávky (nejkratší přepis minus jedna), takže hodnota
  nahrávky závisela na složce. Zlatý test nastavuje legacy hodnotu ručně.
- Provider `nlp` **nikdy nestahuje** modely sám (`download_method=None`),
  stahuje jen `speechscope models download`.
- MFCC převzorkovává na 16 kHz přes `librosa.resample`, stejně jako
  `librosa.load` v legacy; `Signal.resampled` (scipy) se tam nepoužívá.
- phnrec dostává 8 kHz přes `resample_poly`, legacy přes pydub. Výstup jsou
  diskrétní popisky, zlatý test porovnává až post-processing nad nimi.
- Artikulace: legacy má seznam samohlásek s `a:` a mapu s IPA `aː`, takže
  dlouhé samohlásky a `e`, `o` do skupin nespadnou. **Přeneseno tak, jak
  bylo.** Oprava by byla změna algoritmu, rozhodnout musí tým.
- Fonémy v úložišti jsou v jednotkách 100 ns (provider `phonemes` verze
  1.1), protože sekundy v plovoucí čárce měnily výběr rámců na hranicích.
- Dvě různé `remove_outliers_mahal` (MFCC vs. artikulace) jsou schválně
  dvě funkce, legacy je měl různé a dávají různé výsledky.

**Tenhle soubor se schválně necommituje**, je jen lokální.

**Po revizi (pushnuto v 5009142, 130 testů):**
- conformer po oknech (`_predict_chunked`, `stitch` v `conformer_vad.py`),
  parametry `segments.chunk_seconds` a `chunk_overlap`, provider `segments`
  verze 2.1 (stará cache se přepočítá). Testy sešívání v `test_chunking.py`.
- `speechscope doctor` (`doctor.py`): knihovny, modely, GPU, připravenost
  providerů; `--json` a exit 1 když něco chybí. První věc pro kliniku i GUI.
- `--models-dir` na úrovni celé aplikace, nastaví `SPEECHSCOPE_MODELS`.
  GUI ho má předávat vždy, protože `models_dir()` jinak bere aktuální
  adresář.
- `_run_provider` cachuje i neočekávaný pád provideru (zabalí do
  `ProviderError`), aby drahý model nespadl znovu pro každou feature.
- `list --json` nese `description` a `outputs`.
- `pyproject` má skutečný popis místo šablony.

Ověřeno: čistý klon z GitHubu, `uv sync --all-extras`, 117 testů prošlo
bez modelů. Celá dávka (2 nahrávky, všechny feature pro monolog,
`--progress-json`) dala 112 sloupců bez poznámek za 272 s, z toho většina
Whisper.

**Dál na řadě:** rozhodnutí týmu o odchylkách výše (MATTR okno, dlouhé
samohlásky); GUI musí volat `segment` před `extract --vad`, nebo posílat
`--set segments.model=...`, protože `auto` bere jen hotovou cache.

**Otevřené otázky:**
- Mapování starých modulů na nová jména v `docs/design.md` sekce 10 čeká na
  potvrzení, ať se jména neusadí špatně.
- Reference pro převod na půltóny: výchozí je `fmin` kvůli shodě se starým
  skriptem, doporučená volba je pevných 100 Hz podle konvence Praatu.
- Váhy `segmentation_conformer.pt` nejsou verzované v názvu. Při přetrénování
  zvážit `_v2` a zvednout `version` provideru `segments`.

## Styl
- Typované signatury, `from __future__ import annotations`.
- Komentáře a docstringy česky, identifikátory anglicky.
- Nahrávky pacientů nikdy do gitu; `tests/data/` obsahuje jen syntetické signály.
  `.gitignore` blokuje `data/`, `legacy/`, `models/`, `work/` a všechna `*.wav`
  mimo `tests/data/`.
