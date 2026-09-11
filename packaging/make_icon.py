"""Složí ikonu aplikace z hotových PNG ve složce `icon/` (grafika od autora).

Vstup: `icon/icon-16.png` … `icon/icon-256.png`, `icon/mark.png`
a `icon/wordmark.png`. Výstup do `src/speechscope_app/assets/`:
`speechscope.ico` (všechny velikosti), `speechscope.png` (256 px),
`mark.png` a `wordmark.png` pro boční panel. Spouští se jednorázově po
změně grafiky:

    uv run --with pillow python packaging/make_icon.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "icon"
OUT = ROOT / "src" / "speechscope_app" / "assets"
SIZES = (16, 32, 48, 64, 128, 256)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    images = [Image.open(SRC / f"icon-{s}.png").convert("RGBA") for s in SIZES]
    images[-1].save(
        OUT / "speechscope.ico",
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[:-1],
    )
    shutil.copyfile(SRC / "icon-256.png", OUT / "speechscope.png")
    for name in ("mark.png", "wordmark.png"):
        shutil.copyfile(SRC / name, OUT / name)
    print("ikona a wordmark v", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
