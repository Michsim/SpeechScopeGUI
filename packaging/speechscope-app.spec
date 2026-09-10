# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: GUI jako onedir `dist/SpeechScope/`.

Onedir schválně (rychlejší start, méně poplachů antiviru). Knihovna
SpeechScope se sem nebalí, build.ps1 ji dá vedle jako `speechscope-lib/`.
Spouští se: uv run --group build pyinstaller packaging/speechscope-app.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

datas = collect_data_files(
    "speechscope_app",
    includes=["protocols/*.yaml", "fake/fixtures/*.json", "fake/fixtures/params/*.json", "assets/*"],
)

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=["speechscope_app.fake.cli"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtPdf",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="SpeechScope",
    icon=str(SRC / "speechscope_app" / "assets" / "speechscope.ico"),
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SpeechScope",
)
