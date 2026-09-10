<#
.SYNOPSIS
SestavÃ­ SpeechScope pro Windows: GUI (PyInstaller), prostÅ™edÃ­ knihovny,
instalÃ¡tor (Inno Setup) a kouÅ™ovÃ½ test.

.DESCRIPTION
VÃ½sledek: dist\SpeechScope\ (spustitelnÃ¡ sloÅ¾ka) a dist\SpeechScope-Setup-<verze>.exe.
Modely se nebalÃ­, GUI je pÅ™i prvnÃ­m spuÅ¡tÄ›nÃ­ nabÃ­dne ke staÅ¾enÃ­.

Kroky (kaÅ¾dÃ½ jde pÅ™eskoÄit pÅ™epÃ­naÄem):
  1. GUI          uv run --group build pyinstaller packaging\speechscope-app.spec
  2. knihovna     samostatnÃ½ Python (uv python install) + wheel knihovny
                  s extras whisper, nlp, onnx; torch jen CPU
  3. instalÃ¡tor   ISCC packaging\speechscope.iss (kdyÅ¾ je Inno Setup)
  4. kouÅ™ovÃ½ test SpeechScope.exe --fake --smoke a speechscope doctor --json

.PARAMETER LibraryRepo
Cesta k repozitÃ¡Å™i knihovny SpeechScope (vÃ½chozÃ­ ..\SpeechScope\SpeechScope vedle tohoto repa).

.EXAMPLE
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -SkipLib -SkipInstaller
#>
param(
    [string]$LibraryRepo = "",
    [string]$PythonVersion = "3.11",
    [switch]$SkipGui,
    [switch]$SkipLib,
    [switch]$SkipInstaller,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Dist = Join-Path $Root "dist\SpeechScope"
$Lib = Join-Path $Dist "speechscope-lib"
if (-not $LibraryRepo) { $LibraryRepo = Join-Path (Split-Path $Root -Parent) "SpeechScope\SpeechScope" }

function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what skonÄilo kÃ³dem $LASTEXITCODE" } }

Push-Location $Root
try {
    # --- 1. GUI --------------------------------------------------------------
    if (-not $SkipGui) {
        Step "GUI pÅ™es PyInstaller"
        if (Test-Path $Dist) { Remove-Item -Recurse -Force $Dist }
        # --extra dev, jinak `uv sync` z venv odstranÃ­ ruff a pytest
        uv sync --extra dev --group build; Check "uv sync"
        uv run --group build pyinstaller "packaging\speechscope-app.spec" --noconfirm `
            --distpath "dist" --workpath "build\pyinstaller"
        Check "pyinstaller"
    }

    # --- 2. prostÅ™edÃ­ knihovny -----------------------------------------------
    if (-not $SkipLib) {
        Step "ProstÅ™edÃ­ knihovny z $LibraryRepo"
        if (-not (Test-Path (Join-Path $LibraryRepo "pyproject.toml"))) {
            throw "RepozitÃ¡Å™ knihovny nenalezen: $LibraryRepo (parametr -LibraryRepo)"
        }
        $wheelDir = Join-Path $Root "build\lib-wheel"
        if (Test-Path $wheelDir) { Remove-Item -Recurse -Force $wheelDir }
        Push-Location $LibraryRepo
        try { uv build --wheel --out-dir $wheelDir; Check "uv build" } finally { Pop-Location }
        $wheel = Get-ChildItem (Join-Path $wheelDir "speechscope-*.whl") | Select-Object -First 1
        if ($null -eq $wheel) { throw "wheel knihovny nevznikl" }

        # samostatnÃ½ Python (python-build-standalone je pÅ™enositelnÃ½), bez venv:
        # venv by ukazoval na Python build stroje, kterÃ½ u kliniky nenÃ­
        $pyRoot = Join-Path $Root "build\python"
        if (Test-Path $pyRoot) { Remove-Item -Recurse -Force $pyRoot }
        # --no-bin: nezakládat spouštěč v ~\.local\bin (build stroj ho nepotřebuje)
        uv python install $PythonVersion --install-dir $pyRoot --no-bin; Check "uv python install"
        # vedle skuteÄnÃ© sloÅ¾ky vznikÃ¡ i junction bez patch verze; ten nechceme
        $pyDir = Get-ChildItem $pyRoot -Directory |
            Where-Object { -not $_.LinkType -and (Test-Path (Join-Path $_.FullName "python.exe")) } |
            Select-Object -First 1
        if ($null -eq $pyDir) { throw "ve $pyRoot nenÃ­ sloÅ¾ka s python.exe" }
        if (Test-Path $Lib) { Remove-Item -Recurse -Force $Lib }
        Move-Item $pyDir.FullName $Lib
        $python = Join-Path $Lib "python.exe"
        if (-not (Test-Path $python)) { throw "po pÅ™esunu chybÃ­ $python" }
        # uv oznaÄuje svÃ© Pythony jako "externally managed" (PEP 668) a pak do
        # nich odmÃ­tÃ¡ instalovat; tahle kopie uÅ¾ uv nepatÅ™Ã­, znaÄka pryÄ
        Remove-Item (Join-Path $Lib "Lib\EXTERNALLY-MANAGED") -ErrorAction SilentlyContinue

        uv pip install --python $python `
            --override (Join-Path $Root "packaging\overrides.txt") `
            --extra-index-url "https://download.pytorch.org/whl/cpu" `
            --index-strategy unsafe-best-match `
            "$($wheel.FullName)[whisper,nlp,onnx]"
        Check "uv pip install"
        # uklidit, co u kliniky nikdo nepotÅ™ebuje
        Get-ChildItem $Lib -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
        Get-ChildItem $Lib -Recurse -Directory -Filter "tests" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        & $python -m speechscope.cli version; Check "speechscope version"
    }

    # --- 3. instalÃ¡tor --------------------------------------------------------
    if (-not $SkipInstaller) {
        Step "InstalÃ¡tor (Inno Setup)"
        $iscc = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($null -eq $iscc) {
            Write-Warning "Inno Setup 6 nenÃ­ nainstalovanÃ½ (https://jrsoftware.org/isdl.php), instalÃ¡tor pÅ™eskoÄen. dist\SpeechScope\ jde spustit i bez nÄ›j."
        } else {
            & $iscc "packaging\speechscope.iss"; Check "ISCC"
        }
    }

    # --- 4. kouÅ™ovÃ½ test ------------------------------------------------------
    if (-not $SkipSmoke) {
        Step "KouÅ™ovÃ½ test"
        # S ÄasovÃ½m limitem: GUI, kterÃ© se neukonÄÃ­ (nebo se mnoÅ¾Ã­), musÃ­
        # test shodit, ne zablokovat. PÅ™eÅ¾ivÅ¡Ã­ procesy se ukonÄÃ­.
        $exe = Join-Path $Dist "SpeechScope.exe"
        $proc = Start-Process -FilePath $exe -ArgumentList "--fake", "--smoke" -PassThru
        if (-not $proc.WaitForExit(60000)) {
            Get-Process SpeechScope -ErrorAction SilentlyContinue | Stop-Process -Force
            throw "SpeechScope.exe --fake --smoke se do 60 s neukonÄilo"
        }
        $stray = @(Get-Process SpeechScope -ErrorAction SilentlyContinue)
        if ($stray.Count -gt 0) {
            $stray | Stop-Process -Force
            throw "po kouÅ™ovÃ©m testu zÅ¯stalo $($stray.Count) procesÅ¯ SpeechScope.exe"
        }
        if ($proc.ExitCode -ne 0) { throw "SpeechScope.exe --fake --smoke skonÄilo kÃ³dem $($proc.ExitCode)" }
        Write-Host "GUI s faleÅ¡nou knihovnou nabÄ›hlo a skonÄilo."
        $python = Join-Path $Lib "python.exe"
        if (Test-Path $python) {
            $env:PYTHONUTF8 = "1"
            & $python -m speechscope.cli doctor --json | Out-Null
            if ($LASTEXITCODE -notin 0, 1) { throw "doctor skonÄil kÃ³dem $LASTEXITCODE" }
            Write-Host "ZabalenÃ¡ knihovna odpovÃ­dÃ¡ (doctor kÃ³d $LASTEXITCODE; 1 = chybÃ­ modely, to je v poÅ™Ã¡dku)."
        } else {
            Write-Warning "ZabalenÃ¡ knihovna chybÃ­ (SkipLib), test doctor pÅ™eskoÄen."
        }
    }

    Step "Hotovo: $Dist"
} finally {
    Pop-Location
}
