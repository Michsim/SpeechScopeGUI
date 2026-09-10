# Balení do instalátoru

Plán, zatím bez skriptů. Klinik dostane jeden `SpeechScope-Setup.exe`,
poklepe, a při prvním spuštění klikne na stažení modelů. Nic jiného.

## Co instalátor nese

| část | jak vzniká | velikost |
|---|---|---|
| GUI | PyInstaller, onedir (ne onefile: rychlejší start, méně poplachů antiviru) | ~150 MB |
| prostředí knihovny `speechscope-lib/` | `uv venv --relocatable` + `uv pip install speechscope[whisper,nlp,onnx]` z wheelu | ~2 GB |
| ikona, odinstalátor | Inno Setup, instalace pro uživatele bez admin práv do `%LOCALAPPDATA%` | |

Modely (3 GB a víc) v instalátoru nejsou. GUI je při prvním startu nabídne
ke stažení do `%LOCALAPPDATA%\SAMI\SpeechScopeApp\models`, nebo se ukáže
na sdílenou složku kliniky. phnrec se kopíruje ručně, aplikace řekne kam.

`backend/library.find_default_command()` hledá `speechscope-lib\Scripts\speechscope.exe`
vedle zabaleného exe, takže po instalaci není co nastavovat.

## Na co nezapomenout

- Přepis závislosti `onnxruntime` z `[tool.uv] override-dependencies`
  v pyprojectu knihovny se musí zopakovat při sestavování prostředí, jinak
  faster-whisper stáhne základní `onnxruntime` a DirectML se rozbije.
- Torch jde do prostředí jen v CPU sestavení kvůli Stanze (~300 MB).
- Bez podpisového certifikátu SmartScreen varuje. Počítat s tím, nebo
  certifikát pořídit.
- Kouřový test po sestavení: zabalené `speechscope doctor --json` musí
  naběhnout a GUI se musí spustit s `--fake`.
- Plná offline varianta s modely uvnitř (5 GB a víc) jde udělat stejným
  skriptem s jedním přepínačem navíc, pokud kliniky nemají internet.

## Pořadí prací

1. `packaging/build.ps1`: PyInstaller GUI → prostředí knihovny → Inno Setup → kouřový test.
2. `packaging/speechscope.iss` pro Inno Setup.
3. Podpis, až bude certifikát.
