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
- **Úlohy**: `phonation`, `ddk`, `spontaneous` (spontánní řeč: pohádka
  i monolog), `reading` (čtený text). Úloha je vlastnost nahrávky,
  nedetekuje se. Od 0.4.0 (28. 10. 2026, necommitováno) jsou dřívější
  `story` a `monologue` jedna úloha, nikde se nelišily; stará jména jsou
  aliasy (`signal.TASK_ALIASES`, `canonical_task`, varování jednou za
  proces) v `discover`, `registry.select`, `Vad.default_for`, `api`
  a `load_signal`. Výstupní sloupec `task` je vždy `spontaneous`. GUI musí
  přejít na nové jméno a čekat verzi 0.4.0.
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
  nestačí, zůstane po přerušeném stažení). Výměna staré kopie za novou jde
  přes `<model>.old` a `_rename_retry` (Windows drží složky kvůli antiviru;
  12. 9. 2026 to smazalo starou i novou Stanzu). Stahování i rozbalení jde přes
  `<model>.part` a na místo se přejmenuje až hotové (`_install`). Balík pro
  kliniky: `models pack` → zip bez komprese s `manifest.json` (SHA-256),
  `models unpack` ho ověřuje; verze formátu `BUNDLE_VERSION`. Pro kliniky
  `--only whisper,stanza,onnx,phnrec`; phnrec smí do balíku, provider
  phonemes ho v `models/phnrec` najde sám (`phnrec_dir` prázdný).
- Výjimka jsou váhy vlastního modelu segmentace, ty jdou v balíčku
  (`src/speechscope/models/`), protože jsou naše a mají 21 MB.

## Kde se počítá (změřeno 9. 9. 2026 na RTX 5060 Laptop, 8 GB, driver 577)
- **Whisper jede na grafice sám.** ctranslate2 má vlastní akceleraci nezávislou
  na torchi. `transcript.device` je `auto`, `compute_type` taky `auto` – to
  vybere nejrychlejší podporovanou přesnost. Na `float16` trvá dvouminutová
  nahrávka 97 s, na `int8_float16` 46 s, proto je `auto` výchozí.
- **Whisper bez kontextu předchozího okna.** `transcript.condition_on_previous_text`
  je `false` (11. 9. 2026): u patologické řeči se jinak dekodér zacyklí
  a opakuje jednu větu (těžká nahrávka 156 s → 92 s, text bez smyček).
  `beam_size=5` nechat; beam 1 je 5× rychlejší, ale u těžkých nahrávek
  kazí text. Když model na kartě spadne (CUDA, paměť), `_get_model`
  dopočítá na CPU v `int8` a zaloguje to, běh nepadá.
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

## Vzdálená plocha a virtuální stroje (11. 9. 2026)
- **DirectML ve vzdálené ploše (RDP) kartu nevidí**: chyba `C0262002`
  při vytváření session. `onnx_session.probe_gpu` to zkusí předem,
  při neúspěchu spadne na CPU a `doctor` hlásí `gpu.remote_session`
  (RDP relace podle `SESSIONNAME`/`WTSActiveConsole`) a
  `gpu.onnxruntime_gpu_usable` (false = karta je, ale nejde použít).
  GUI z toho dělá oranžovou kartu v Prostředí.
- **Hlášky onnxruntime jdou na stderr.** DirectML sype varování na stdout
  a rozbíjelo to JSON průběhu; `onnx_session` při vytváření session
  přesměruje stdout (fd 1) na stderr. Na stdout smí jen `--progress-json`.
- **VM s kartou přes passthrough v režimu WDDM** (simekm16, RTX PRO 4500,
  10 vCPU EPYC): Whisper ~80 s na 2 min nahrávky při 8 % využití karty,
  latence na každý krok dekodéru, beam 1 skoro nepomůže, int8 taky ne.
  Řešení je režim TCC (`nvidia-smi -dm 1`), ne parametry knihovny.
  Uživatel zatím nechal být.

## DDK (27. 10. 2026, necommitováno)
Zdroj `../ddk_stuff_pro_michala/vycistene/` (Melechovsky), kopie skriptů
v `legacy/ddk/`. Obecná akustika (F0, CPP, LTAS, MFCC) na `ddk` už není.
- Provider `ddk_events` (`providers/ddk.py`), síť a logika
  v `providers/backends/ddk_evo.py`, MATLAB náhrady v `matlab_compat.py`
  (`findpeaks` vrací pozice od 1, logika počítá od 1, ven jde od 0).
  Výběr slabik (`skip_syllables` 3, `max_syllables` 30, `min_syllables` 18)
  jsou parametry provideru, ne feature; nedostatek = `DdkEvents.problem`,
  feature ho vyhodí jako `InsufficientData`. Cache `ddk_events/<zaklad>.txt`
  (celé vzorky na 16 kHz), selhání detekce jako `# chyba: ...`.
- Feature `acoustic.ddk.rate|regularity|deceleration|vot|vowel_length|intensity`
  (`features/acoustic/ddk.py`), jen `tasks={"ddk"}`, jen /pataka/.
- Model `models/ddk-onnx/` (`ddk_evo.onnx` 628 MB float32, `meta.json` se
  SHA-256, `convert.py`): tf2onnx 1.16.1, opset 17, vstup `audio`
  (N, 7200, 1), výstupy E, V, O. Katalog `ddk`, vydání `models-ddk-v1`,
  příloha `ddk-onnx.zip` (`models pack-onnx --model ddk`), **zatím
  nenahráno**. Export z knihovny není (TF nesmí do závislostí).
- Ověřeno: TF vs ONNX max 1.07e-6 na 461 nahrávkách; pozice po step2
  s ONNX a resampy 0.2.2 shodné s autorovým `EVOfinal2fulltest.mat` 461/461.
  **Autor převzorkoval resampy 0.2.2**; resampy 0.4.3 (v `.venv` u DDK)
  dává jiný signál a sedí jen 38/461, takže tvrzení v DDK CLAUDE.md platí
  jen pro step2. Knihovna převzorkovává soxr ze skutečné fs (legacy natvrdo
  48 kHz) – vědomá odchylka. Zlatý test `tests/golden/test_golden_ddk.py`
  (`SPEECHSCOPE_GOLDEN_DDK_DIR`) dává oběma stejné křivky: 461/461 pozice
  i 8 feature (`rtol` 1e-6).
- DirectML: pozice shodné s CPU, 0.23 s proti 1.4 s na nahrávku;
  `onnx_device=auto` výchozí.

## Intenzita 2.0 (29. 10. 2026, necommitováno)
`acoustic.intensity.stats` měří variabilitu hlasitosti **jen ve znělých
rámcích**: obálka Praat `To Intensity` (Kaiser 3.2/`min_pitch`=32 ms,
krok `timestep` 10 ms, dB přepočtené na plný rozsah, sinus amplitudy 1 =
−3 dB), znělost `pitch.swipe_voiced_frames` (stejná pravidla jako F0 1.1:
60–400 Hz, síla 0.3, meze ven). VAD volitelný, výchozí vypnutý; bez
segmentace se liší od conformeru o 0.1–0.3 dB. Nový sloupec
`voiced_fraction`, pod `min_voiced_seconds` (2 s) InsufficientData.
Důvod: std dB obálky přes všechnu řeč byla dvouvrcholová a rostla
s odstupem hlasu od pozadí a s tím, kolik tichého `su` conformer nechá
v řeči (PN693 monolog 10.7 dB proti normě 6.0 ± 0.7, pyannote 7.1, znělé
rámce 4.5). Legacy hodnotu dá `envelope=moving_average`, `frames=all`,
`--vad`; `intensity_stats()` zůstala pro zlatý test beze změny (prošel na
`zkouska.wav` se složkou sestavenou z `data/` + `work/segments`). Norma
z kontrol se musí přepočítat: čtení ~5.2 ± 0.9 dB místo 5.8 ± 0.6. Studie
variant (112 vs 32 ms, Praat ac/cc vs SWIPE, s VAD i bez) je ve scratchpadu
sezení (`voiced_std*.py`, `*_data2.csv`), do repa nepatří. Testy
v `test_features_ported2.py`; `test_intenzita_a_pauzy_pres_extract_s_labely`
teď čeká NaN s důvodem v `notes`, protože syntetika má jen 0.7 s znělé řeči.

## Ořez na hlas u fonace (29. 10. 2026, necommitováno)
Fonace nemá segmentaci a ticho před nasazením hlasu (PN693a1: 3.3 s) šlo
do výpočtu. Sdílený helper v `pitch.py`: `voiced_extent` (první až poslední
znělý rámec SWIPE s rezervou), `should_trim`, `trim_to_voice`, parametry
`TRIM_PARAMS` (`trim_silence` = phonation | always | never, `trim_margin`
0.05 s). S VAD (`sig.spans` není None) se ořez nikdy neuplatní, souvislá
řeč je beze změny, zlaté testy prošly. Zapojeno: perturbace 1.1
(`keep_middle` se bere z hlasu, ne ze souboru; jitter se podle ticha
před hlasem měnil o 30 %), CPP 1.2 (medián klesal o 2–5 %), MFCC 1.2
(std koeficientů několikanásobně). Po ořezu originál a ručně ořezaná kopie
dávají u CPP a MFCC totéž, u perturbace do 7 % (poloha okna na hranici).
F0 ořez nemá, `voiced_fraction` u fonace je proto podíl ticha v souboru.
Testy `tests/test_trim_voice.py`; verze v `test_revision_*` zvednuty.

## CPP 1.3 (30. 10. 2026, necommitováno)
`acoustic.quality.cpp` počítá medián **jen ze znělých rámců** (`frames` =
voiced | all, výchozí voiced): znělost SWIPE jako F0 1.1 a intenzita 2.0
(`f_min` 60, `f_max` 400, `timestep` 0.01, `strength_threshold` 0.3,
`drop_bounds`), rámec CPP je znělý podle rámce trackeru nejblíž jeho
středu (`pitch.voiced_at`, sdílí ho intenzita). CPPS se vyhlazuje až po
výběru, zvlášť v každém souvislém běhu znělých rámců (`_voiced_runs`,
hranice = vynechaný rámec nebo hranice úseku VAD); u `all` přes všechny
rámce za sebou jako 1.2. Pod `min_voiced_seconds` (1 s) InsufficientData,
nový sloupec `voiced_fraction`. Zlatý test nastavuje `frames=all`
(prošel na zkouska.wav a PN648a). Testy `tests/test_cpp_voiced.py`.
Studie (30 nahrávek čtení Zahradníka PN648–659 a RBD108/117/122 a/b z
`Downloads/data`, 10 kontrol HC06/07/23/104, HN27/49, HC880 a/b, HN06/07;
skripty `cpp_varianty.py` a CSV ve scratchpadu sezení): proti sv+su
zvedne CPP i CPPS o ~2 dB (sd rozdílu 0.7–1 dB), Spearman 0.94, HC–RBD
d 1.9 → 3.2 (CPPS), HC–PN 0.2 → 0.6 (slabé, n malé). Jen `sv` conformeru
dává skoro totéž (d 0.49) a potřebuje segmentaci; `novad` nejhorší.
Praat sám znělost neřeší (všechny rámce, průměr), znělé úseky vybírá
AVQI pitch trackerem. **Normy z kontrol se musí přepočítat.**

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
uv run speechscope list --task spontaneous
uv run speechscope list --params acoustic.pitch.f0
uv run speechscope segment data/ --model conformer --work-dir work/
uv run speechscope transcribe data/ --language cs --work-dir work/
uv run speechscope extract data/ --task phonation --out out/phon.csv
uv run speechscope extract data/ --task spontaneous --vad --work-dir work/ --progress-json
uv run pytest
uv run ruff check src tests
```

## Rozhraní pro GUI (smlouva, neměnit bez zvednutí verze)
`speechscope extract ... --progress-json` píše na stdout JSON řádky
(smlouva verze 2, pole `protocol` v události `start`):
`start` (total, task, features, providers) → pro každou nahrávku
`begin` (index, path), `stage` (index, provider, status running|done|cached|error,
seconds, msg) a `file` (index, path, status ok|error, msg) → `done` (n_ok)
→ `saved` (out). Všechno ostatní jde na stderr. `stage` přijde jen pro
providery, které se pro danou nahrávku opravdu volají; `cached` znamená
mezivýsledek z pracovní složky.

Totéž `--progress-json` mají `segment` a `transcribe` (`features` prázdné,
`providers` jednoprvkové, `saved` = pracovní složka).

**Tabulka se zapisuje průběžně.** `extract` s `--out` po každé nahrávce
přepíše CSV (`api._write_partial`: zápis do `<out>.part` a `os.replace`),
takže po zrušení nebo pádu zůstane částečný výsledek. Důsledek: během
běhu nesmí mít soubor nikdo otevřený (Excel, ani GUI), Windows jinak
přejmenování odmítnou a běh spadne; GUI proto tabulku běžícího běhu
nečte. Sloupce `notes` a `error` jsou v průběžné verzi vždy, v konečné
jen když v nich něco je.

**Jazyky modelů.** `doctor --json` i `models list --json` mají u modelu
pole `languages` (Stanza: `["cs", "en"]`); GUI z něj skládá výběr jazyka
nahrávek a posílá ho do `transcript.language` i `nlp.language`.

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

## Stav práce (11. 9. 2026)
Na `main` je všechno: jádro, CLI, providery, všech osm přenesených
legacy modulů, okna conformeru, doctor, ONNX segmentace, balík modelů.
Verze 0.2.0 (b8f42bc): smlouva 2 (`begin`, `stage`), průběžný CSV,
`languages` u modelů, Whisper bez kontextu s dopočtem na CPU; poslední
commit 04b8df6 (onnxruntime mimo stdout, doctor a vzdálená plocha).
GUI 0.2.0 tuhle verzi očekává (`contract.KNOWN_LIBRARY_VERSION`).
Instalátor 0.2.0 z rána poslední dvě opravy neobsahuje, přebalit.

**Nálezy z GUI (10. 9. 2026), opraveno, necommitováno:** UTF-8 výstup bez
`PYTHONUTF8`, `list --params <provider>` a `list --providers`; 5 nových
testů v `test_cli.py`. GUI má dočasně opsané parametry providerů
v `contract.PROVIDER_PARAMS`, po téhle změně je může smazat.

**Revize feature (12. 9. 2026), necommitováno:** lingvistické feature
vyhazují `InsufficientData` místo `0.0` (MATTR/PMI s oknem širším než
přepis, n-gramy pod 30 slov, žádné sloveso, žádná tečka); zlatý test to
čeká v `notes`, protože legacy odvozoval okno z počtu tokenů s mezerou, ale
slova počítal bez interpunkce a `X`. `linguistic.lexical.npmi` přejmenováno
na `pmi` (počítá obyčejné PMI z NLTK). MFCC převzorkovává přes `soxr`
(v základu) místo librosy, bit po bitu shodné s `librosa.resample`, test
`test_prevzorkovani_mfcc_sedi_na_librosu`. Referenční složka pro zlaté
testy jde sestavit z `data/` + `work/` (labels, txt, words.json).
Pokus LTAS: bez převzorkování dává stejná nahrávka ve 48/44.1/22.05 kHz
jiná čísla (centroid 498/498/494/480, šikmost 6.9/6.8/6.1/5.4, špičatost
73/71/54/43 pro 48/44.1/22.05/16 kHz); po převzorkování na 16 kHz jsou
všechny shodné. **Zavedeno:** feature `ltas` verze 1.1 s parametrem
`analysis_fs` (16000), převzorkování přes `resample_like_librosa` (soxr);
funkce `ltas()` sama je beze změny, zlatý test wrapperu dává legacy
stejně převzorkovaný signál. Změna algoritmu, patří do samostatného commitu.

**Revize akustických feature (12. 9. 2026), necommitováno, 247 testů +
22 zlatých na referenční složce.** Studie a skripty variant jsou ve
scratchpadu sezení (`studie_akusticke_feature.md`, `exp_*.py`), do repa
nepatří. Každá změna algoritmu má parametr pro reprodukci legacy hodnoty a
zlatý test ho nastavuje (nebo dá legacy ekvivalentní vstup):
- `Signal.spans` + `regions`: po VAD rozsahy řečových úseků ve zřetězeném
  poli (`SegmentsResult.concatenate_with_spans`, `api._run_feature`,
  `scale_spans` při převzorkování). CPP 1.1, LTAS 1.2, MFCC 1.1 rámcují po
  úsecích; bez VAD bit po bitu jako dřív. Tvrdé zřetězení dávalo CPP
  o 0.7–2.1 dB níž (19–21 % rámců přes hranici).
- CPP 1.1: `f0_min` 60, `f0_max` 333, `search_offset` 0, `trend_min_q`
  0.001, `trend_max_q` 0.05 (regresní přímka přes vlastní oblast jako
  Praat; 0/0 = přes oblast hledání), `aggregate` median (tým chce medián;
  mean = Praat); nulové rámce se poznávají před normalizací a vždy vyloučí
  (`_normalize(frames, signal)`). VAD výchozí u story/monologue/reading
  (i LTAS a MFCC). Legacy: 55/500/0.001/0/0 a `--no-vad`. Proti Praatovu
  CPPS je průměr s trendem 1–50 ms blíž na 5 ze 6 nahrávek než trend přes
  oblast hledání, ale rozdíl zůstává 0.5 až 2.3 dB: jiný per-frame
  algoritmus (Praat preemfáze 50 Hz, krok 2 ms, vyhlazení 20 ms/0.5 ms,
  robustní fit). Trend nezvedá CPPS systematicky, na PN512 medián
  9.15 → 6.95.
- F0 1.1: `drop_bounds` True (rámce na f_min/f_max ven; legacy False),
  `outlier_semitones` 12 (legacy 0), výstupy `median_hz`,
  `mean_hz`, `range` (95.–5. percentil, st), `voiced_fraction`; rámec se
  silou NaN/inf ze SWIPE je neznělý (libf0 dává na tichu F0 = f_min).
- Nová `acoustic.quality.perturbation` 1.0 (`perturbation.py`, jen
  `phonation`): jitter local/rap/ppq5, shimmer local/apq3/apq11 v %, HNR dB,
  parselmouth s výchozími hodnotami Praatu, `keep_middle` 2/3 (prostřední
  část nahrávky, `middle_portion`); registrace automaticky přes
  `walk_packages`.
- Tempo 1.1: `requires` i `segments`; `syllable_method` nuclei (legacy
  pyphen), `speech_time` words (výchozí, legacy) | segments_in_words
  (průnik řeči segmentace s intervaly slov, `speech_time_in_words`) |
  segments; interpunkce se ze slov
  vždy odstraní (Pyphen dělil `let,` na dvě slabiky); tempo od prvního
  slova. Zlatý test dává legacy slova bez interpunkce posunutá na nulu.
- Pauzy 1.2 (tým, varianta B): `pauses` = všechna neřeč (conformer ps+pb,
  pyannote non-speech), `silences` = jen ps, `breaths` = jen pb (obě jen
  čtyřtřídní, jinak InsufficientData), `rhythm` = dřívější `segmentation`
  bez dpi/dbi (DPI = silences.median, DBI = breaths.median s
  trim_edges=false; `segmentation_timing()` zůstává celá pro zlatý test
  notebooku). Všechny tři mají stejné sloupce `count`, `per_minute`,
  `ratio`, `median`, `mean`, `std`, `long_count`, `long_median` a
  parametry `merge_adjacent` False, `trim_edges` True, `min_pause` 0,
  `long_pause` 0.22. `breath_ratio` zrušen (= breaths.ratio). Legacy
  `trim_edges=false`; běžná definice navíc merge True a min_pause 0.25.
- Artikulace 2.1: `center_method` median (legacy kmeans), `vowel_portion`
  middle_third (legacy full, náhrada rámcem nejblíž středu), `time_step`
  0.01 (legacy 0.02), `vowel_quantity` short (vrcholy a poměry),
  `area_quantity` both (VSA a VAI; legacy short). Skupiny podle kvantity
  v `QUANTITY_GROUPS`, phnrec značí délku `a:`. VSA klesne
  o 46–60 %, dnešní hodnoty se s novými nedají srovnávat. Dlouhé samohlásky
  (`a:` vs `aː`) vyřešeny parametry kvantity (verze 2.1); se započtením
  F1 /i/ −13 %, jinak do 9 %.
- Nezavedeno (tým): reference půltónů (`fmin` zůstává, doporučeno `fixed`
  100 Hz), sloučení pauz a práh (parametry), CPP těsněji k Praatu.
- Pozor: `data/` má nové nahrávky (HN06, HN07, PN511, PN512); běh
  `extract data/ --work-dir work` je přepsal Whisperem a `zkouska.words.json`
  ve `work/` má teď 243 slov místo 258. Referenční kopie pro zlaté testy je
  ve scratchpadu (`golden/`), sestavuje se z wav + labels.txt + txt +
  words.json + phonemes.txt.

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

**Překlady popisů (12. 9. 2026), necommitováno:** `texts.py` + katalog
`translations/en.yaml` (221 klíčů: feature.<jméno>.description|outputs.<sloupec>|params.<parametr>,
provider.<jméno>.params.<parametr>). `--lang` na úrovni aplikace nastaví
`SPEECHSCOPE_LANG`, `list` (tabulka, `--json`, `--params`, `--providers`)
překládá jen popisy; chybějící klíč = česká předloha. `test_texts.py`
hlídá úplnost každého katalogu proti registru (`texts.all_texts()`).
Kostru nového jazyka dá `all_texts()`; skript pro angličtinu byl ve
scratchpadu (`make_en.py`). Nepřekládají se `notes` ani CLI hlášky. **Log je anglicky natvrdo**
(všech 80 `logger.*` volání, 12. 9. 2026); texty výjimek, které se do
logu dostávají přes `%s`, zůstávají česky (jsou to `notes`).
GUI si názvy skupin (např. „Výška“ pro `pitch`) dělá samo; kdyby to
mělo jít z knihovny, přidat klíče `group.<doména>.<skupina>` do katalogu
a do `list --json`.

**Jazyky (12. 9. 2026):** `STANZA_LANGUAGES` = cs, en, de,
it, es, fr (`models download --only stanza` stáhne všechny; jen procesory
`STANZA_PROCESSORS` = tokenize, mwt, pos, lemma, depparse, bez `default.zip`,
změřeno 1.5 GB: cs 238, en 322, de 315, it 323, es 314 MB; celý balík by
byl 5.3 GB). Pipeline v `nlp.py` bere stejné procesory, načtení de 1.3 s. `transcript.check_language` ověří kód proti
`faster_whisper.tokenizer._LANGUAGE_CODES` před cache i modelem; zjištěný
jazyk se ukládá do otisku přepisu (`extra={"language": ...}`) a cache ho
vrací, když je `transcript.language` prázdné. Slabiky: `SyllableRules` a
`SYLLABLE_RULES` (cs, de, it, es, fr; fr má `separate` a `silent_final_e`)
v `timing.py`, `syllables_nuclei(word,
language)`; jazyk bez pravidel spadne na pyphen (log), bez slovníku
InsufficientData. Testy `tests/test_languages.py` (slova s ručně danými
slabikami). Artikulace zůstává jen česká (phnrec).

**Dál na řadě (12. 9. 2026):** pojmenování a popisy jednotlivých feature a jejich výstupů (`description`, `outputs`, jména sloupců, tabulka v README, mapování v `docs/design.md` sekce 10). Revize akustických feature je hotová a pushnutá (commity a84e4b4 až cec5d0b).

**Dál na řadě (starší):** rozhodnutí týmu o odchylkách výše (MATTR okno, dlouhé
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
