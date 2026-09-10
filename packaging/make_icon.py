"""Vyrobí ikonu aplikace (`src/speechscope_app/assets/speechscope.ico` a .png).

Modrý zaoblený čtverec s bílou vlnou. Spouští se jednorázově:

    uv run python packaging/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QPainter, QPen

OUT = Path(__file__).resolve().parents[1] / "src" / "speechscope_app" / "assets"
BARS = (0.30, 0.55, 0.85, 0.60, 1.0, 0.70, 0.45, 0.80, 0.35)


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#2563eb")))
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    pen = QPen(QColor("white"))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setWidthF(size * 0.055)
    painter.setPen(pen)
    n = len(BARS)
    step = size * 0.72 / (n - 1)
    x0 = size * 0.14
    for i, h in enumerate(BARS):
        x = x0 + i * step
        half = h * size * 0.30
        painter.drawLine(QPointF(x, size / 2 - half), QPointF(x, size / 2 + half))
    painter.end()
    return image


def main() -> int:
    _app = QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    render(256).save(str(OUT / "speechscope.png"), "PNG")
    # ICO z Qt nese jednu velikost; 256 px Windows škáluje bez problémů
    ok = render(256).save(str(OUT / "speechscope.ico"), "ICO")
    print("ico:", ok, OUT)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
