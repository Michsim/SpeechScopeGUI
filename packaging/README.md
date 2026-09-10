# Balení do instalátoru

Klinik dostane jeden `SpeechScope-Setup-<verze>.exe`, poklepe, a při prvním
spuštění klikne na stažení modelů. Nic jiného.

```
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Výsledek je `dist\SpeechScope\` (jde spustit i bez instalace) a
`dist\SpeechScope-Setup-<verze>.exe`. Přepínače `-SkipGui`, `-SkipLib`,
`-SkipInstaller`, `-SkipSmoke` přeskočí kroky; `-LibraryRepo` ukáže na
repozitář knihovny, když neleží vedle (`..\SpeechScope\SpeechScope`).

## Co instalátor nese

| část | jak vzniká | velikost |
|---|---|---|
| GUI `SpeechScope.exe` + Qt | PyInstaller onedir podle `speechscope-app.spec` (ne onefile: rychlejší start, méně poplachů antiviru) | ~150 MB |
| knihovna `speechscope-lib\` | samostatný Python z `uv python install` (python-build-standalone, přenositelný) + wheel knihovny s extras `whisper,nlp,onnx`, torch jen CPU | ~2 GB |
| ikona, odinstalátor | Inno Setup 6 (`speechscope.iss`), instalace pro uživatele bez admin práv do `%LOCALAPPDATA%\Programs\SpeechScope` | |

Modely (přes 4 GB) v instalátoru nejsou. GUI je při prvním startu nabídne
ke stažení do `%LOCALAPPDATA%\SAMI\SpeechScopeApp\models` (Prostředí →
Stáhnout modely), nebo se v Nastavení ukáže na sdílenou složku kliniky.
phnrec se stáhnout nedá, dialog řekne, kam ho zkopírovat. pyannote chce
token Hugging Face, ONNX segmentace token GitHubu (nebo se vyexportuje
z torche, což zabalené prostředí bez extras `dnn` neumí).

`backend/library.find_default_command()` hledá `speechscope-lib\python.exe`
vedle zabaleného exe a spouští `python -m speechscope.cli`, takže po
instalaci není co nastavovat. Launcher `Scripts\speechscope.exe` se
nepoužívá: má v sobě absolutní cestu k Pythonu z build stroje.

## Proč ne venv

`uv venv --relocatable` udělá přenositelné launchery, ale venv sám ukazuje
na základní Python build stroje (`pyvenv.cfg`), který u kliniky není.
Samostatný Python s balíčky rovnou v jeho `Lib\site-packages` tenhle
problém nemá.

## Na co nezapomenout

- Přepis závislosti `onnxruntime` (`overrides.txt`) je totéž co
  `[tool.uv] override-dependencies` v pyprojectu knihovny. Bez něj
  faster-whisper stáhne základní `onnxruntime` a DirectML se rozbije.
- Torch jde do prostředí jen v CPU sestavení kvůli Stanze (index
  `download.pytorch.org/whl/cpu`). Segmentace počítá přes ONNX, torch na
  ni nepotřebuje.
- Bez podpisového certifikátu SmartScreen varuje. Počítat s tím, nebo
  certifikát pořídit.
- Kouřový test po sestavení: `SpeechScope.exe --fake --smoke` musí skončit
  nulou a zabalené `speechscope doctor --json` musí odpovědět (kód 1 =
  chybí modely, to je v pořádku). Falešnou knihovnu zabalené exe spouští
  jako `SpeechScope.exe --fake-cli ...` (jiný Python u sebe nemá). Nikdy
  ne `SpeechScope.exe -m ...`: to otevře další GUI, které zase volá
  knihovnu, a procesy se množí, dokud stroj nezamrzne. GUI má proti tomu
  pojistku (`SPEECHSCOPE_APP_CHILD` v prostředí podprocesu → kód 2) a
  skript kouřový test hlídá časovým limitem.
- Ikona: `uv run python packaging\make_icon.py` přegeneruje
  `src\speechscope_app\assets\speechscope.ico`.
- Plná offline varianta s modely uvnitř (přes 5 GB) jde udělat stejným
  skriptem s jedním krokem navíc (zkopírovat složku modelů do `dist`),
  pokud kliniky nemají internet.
