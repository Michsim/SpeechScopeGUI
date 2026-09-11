"""Uvítání po startu: velké logo, dvě věty a doporučení, kam jít dál.

Ukazuje se při každém spuštění, dokud uživatel neklikne v nabídce. Není
to záložka, hlavní okno ji jen vloží do zásobníku stránek mimo nabídku.
Doporučení (Analýza, nebo napřed Prostředí) dává hlavní okno podle toho,
zda je nastavená knihovna a jsou nainstalované modely; po kontrole
prostředí se řádek stavu zpřesní z výsledku `doctor`.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...i18n import tr
from .. import theme


def _asset(name: str, width: int, dpr: float) -> QPixmap | None:
    """Obrázek z assets škálovaný na fyzické pixely, ať není na HiDPI rozmazaný."""
    pixmap = QPixmap(str(resources.files("speechscope_app") / "assets" / name))
    if pixmap.isNull():
        return None
    scaled = pixmap.scaledToWidth(round(width * dpr), Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(dpr)
    return scaled


class WelcomePage(QWidget):
    analysis_requested = Signal()
    environment_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.addStretch(2)
        row = QHBoxLayout()
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)

        card = QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(480)
        card.setMaximumWidth(560)
        row.addWidget(card)
        row.addStretch(1)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(40, 36, 40, 32)
        layout.setSpacing(10)

        # Jen wordmark: má znak, název i slogan; znak navíc by stetoskop zdvojil.
        self.wordmark = QLabel()
        self.wordmark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wordmark = _asset("wordmark.png", 420, self.devicePixelRatioF())
        if wordmark is not None:
            self.wordmark.setPixmap(wordmark)
        else:
            self.wordmark.setObjectName("brand")
            self.wordmark.setText(tr("SpeechScope"))
        layout.addWidget(self.wordmark)
        layout.addSpacing(16)

        self.headline = QLabel(tr("Vítejte v aplikaci SpeechScope"))
        self.headline.setObjectName("headline")
        self.headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.headline)
        self.intro = QLabel(
            tr(
                "Řečové biomarkery z klinických nahrávek. Vyberte složku s nahrávkami, "
                "úlohu a protokol, zbytek spočítá aplikace."
            )
        )
        self.intro.setObjectName("muted")
        self.intro.setWordWrap(True)
        self.intro.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.intro)
        layout.addSpacing(10)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        self.start_btn = QPushButton(tr("Začít analýzu"))
        self.start_btn.clicked.connect(self.analysis_requested)
        self.env_btn = QPushButton(tr("Zkontrolovat prostředí"))
        self.env_btn.clicked.connect(self.environment_requested)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.env_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addSpacing(6)

        self.status = QLabel("")
        self.status.setObjectName("pill")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_row = QHBoxLayout()
        status_row.addStretch(1)
        status_row.addWidget(self.status)
        status_row.addStretch(1)
        layout.addLayout(status_row)
        self.version = QLabel(tr("aplikace {version}").format(version=__version__))
        self.version.setObjectName("muted")
        self.version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.version)

        self._recommend_env = False
        self.set_state(library_ok=False, models_ok=False)

    # --- stav ------------------------------------------------------------------------

    def set_state(self, *, library_ok: bool, models_ok: bool) -> None:
        """Laciný stav před kontrolou: je nastavená knihovna a je co v modelech."""
        if not library_ok:
            self._recommend(True)
            self._set_status("warn", tr("Knihovna SpeechScope není nastavená."))
        elif not models_ok:
            self._recommend(True)
            self._set_status("warn", tr("Modely zatím nejsou nainstalované."))
        else:
            self._recommend(False)
            self._set_status("ok", tr("Modely jsou nainstalované, můžete začít."))

    def set_report(self, report: dict[str, Any] | None) -> None:
        """Zpřesnění z `doctor --json` po kontrole na stránce Prostředí."""
        if report is None:
            self._recommend(True)
            self._set_status("missing", tr("Knihovnu se nepodařilo spustit."))
        elif report.get("all_ready"):
            self._recommend(False)
            self._set_status("ok", tr("Prostředí zkontrolováno, vše je připravené."))
        else:
            self._recommend(True)
            self._set_status("warn", tr("V prostředí něco chybí, podívejte se na Prostředí."))

    def recommends_environment(self) -> bool:
        return self._recommend_env

    def _recommend(self, env: bool) -> None:
        """Modré je doporučené tlačítko, druhé zůstane obyčejné."""
        self._recommend_env = env
        theme.set_role(self.env_btn, "primary" if env else "")
        theme.set_role(self.start_btn, "" if env else "primary")

    def _set_status(self, role: str, text: str) -> None:
        self.status.setText(text)
        theme.set_role(self.status, role)
