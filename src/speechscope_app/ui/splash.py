"""Úvodní okno „Načítám…“ po dobu startu (importy GUI, dotazy na knihovnu, stránky).

Hlavní okno se ukáže až celé hotové, nic se dodatečně nepřekresluje; úvodní
okno jen říká, že se aplikace spouští.
"""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QSplashScreen

from ..i18n import tr
from . import theme

WIDTH, HEIGHT = 460, 230


def make_splash(version: str) -> QSplashScreen:
    """Bílá karta s logem, „Načítám…“ a verzí; v pixelech displeje, aby nebyla rozmazaná."""
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap(int(WIDTH * ratio), int(HEIGHT * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    card = QPainterPath()
    card.addRoundedRect(QRectF(0.5, 0.5, WIDTH - 1, HEIGHT - 1), 12, 12)
    p.fillPath(card, QColor(theme.CARD))
    p.setPen(QColor(theme.BORDER))
    p.drawPath(card)

    wordmark = QPixmap(str(resources.files("speechscope_app") / "assets" / "wordmark.png"))
    if not wordmark.isNull():
        w = 300.0
        h = w * wordmark.height() / wordmark.width()
        p.drawPixmap(QRectF((WIDTH - w) / 2, 52, w, h), wordmark, QRectF(wordmark.rect()))

    font = QFont(p.font())
    font.setPointSizeF(11)
    p.setFont(font)
    p.setPen(QColor(theme.TEXT))
    p.drawText(QRectF(0, 150, WIDTH, 24), Qt.AlignmentFlag.AlignHCenter, tr("Načítám…"))
    font.setPointSizeF(9)
    p.setFont(font)
    p.setPen(QColor(theme.MUTED))
    p.drawText(
        QRectF(0, 190, WIDTH, 20),
        Qt.AlignmentFlag.AlignHCenter,
        tr("aplikace {version}").format(version=version),
    )
    p.end()

    splash = QSplashScreen(pixmap)
    splash.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    return splash
