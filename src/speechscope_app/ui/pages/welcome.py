"""Uvítání po startu: velké logo, dvě věty a doporučení, kam jít dál.

Ukazuje se při každém spuštění, dokud uživatel neklikne v nabídce. Není
to záložka, hlavní okno ji jen vloží do zásobníku stránek mimo nabídku.
Doporučení (Analýza, nebo napřed Prostředí) dává hlavní okno podle toho,
zda je nastavená knihovna a jsou nainstalované modely; po kontrole
prostředí se řádek stavu zpřesní z výsledku `doctor`.

Dokud modely nejsou, je pod textem blok „První nastavení“: složka modelů
(návrh vedle aplikace) a složka výsledků, obojí jde změnit, a tlačítka
na instalaci modelů z balíku nebo stažení. Po instalaci blok zmizí.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...backend.cache import format_size, free_bytes
from ...i18n import tr
from .. import theme

LOW_SPACE = 6_000_000_000  # balík modelů má přes 5 GB


def _asset(name: str, width: int, dpr: float) -> QPixmap | None:
    """Obrázek z assets škálovaný na fyzické pixely, ať není na HiDPI rozmazaný."""
    pixmap = QPixmap(str(resources.files("speechscope_app") / "assets" / name))
    if pixmap.isNull():
        return None
    scaled = pixmap.scaledToWidth(round(width * dpr), Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(dpr)
    return scaled


class FolderRow:
    """Řádek „Modely   C:\\…\\models   [Změnit…]“ v mřížce."""

    def __init__(self, grid: QGridLayout, row: int, title: str, hint: str) -> None:
        self.title = QLabel(title)
        self.title.setObjectName("card_title")
        self.title.setToolTip(hint)
        self.path = QLabel("")
        self.path.setObjectName("muted")
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.button = QPushButton(tr("Změnit…"))
        grid.addWidget(self.title, row, 0)
        grid.addWidget(self.path, row, 1)
        grid.addWidget(self.button, row, 2)

    def set_path(self, path: Path) -> None:
        # dlouhá cesta se zkrátí uprostřed; celá je v tooltipu
        metrics = self.path.fontMetrics()
        self.path.setText(metrics.elidedText(str(path), Qt.TextElideMode.ElideMiddle, 360))
        self.path.setToolTip(str(path))


class WelcomePage(QWidget):
    analysis_requested = Signal()
    environment_requested = Signal()
    models_dir_requested = Signal()  # uživatel chce změnit složku modelů
    work_root_requested = Signal()  # … a složku výsledků
    install_requested = Signal()  # modely ze souboru (balík)
    download_requested = Signal()  # stažení z internetu

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
        card.setMaximumWidth(640)
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

        # --- první nastavení (jen dokud chybí modely) -----------------------------------
        self.setup = QFrame()
        self.setup.setObjectName("card")
        theme.set_role(self.setup, "warn")
        setup_layout = QVBoxLayout(self.setup)
        setup_layout.setContentsMargins(16, 12, 16, 12)
        setup_layout.setSpacing(8)
        setup_title = QLabel(tr("První nastavení"))
        setup_title.setObjectName("card_title")
        setup_layout.addWidget(setup_title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self.models_row = FolderRow(
            grid,
            0,
            tr("Modely"),
            tr("Kam se nainstalují modely (asi 5 GB). Návrh je složka vedle aplikace."),
        )
        self.work_row = FolderRow(
            grid,
            1,
            tr("Výsledky"),
            tr("Kam se ukládají tabulky výsledků a mezivýsledky, do složky na každý běh."),
        )
        self.models_row.button.clicked.connect(self.models_dir_requested)
        self.work_row.button.clicked.connect(self.work_root_requested)
        setup_layout.addLayout(grid)
        self.space = QLabel("")
        self.space.setObjectName("muted")
        self.space.setWordWrap(True)
        setup_layout.addWidget(self.space)
        self.setup_note = QLabel(
            tr(
                "Modely zatím nejsou nainstalované. Nainstalujte je z balíku "
                "(zip z USB nebo sdíleného disku), nebo je stáhněte z internetu."
            )
        )
        self.setup_note.setWordWrap(True)
        setup_layout.addWidget(self.setup_note)
        setup_buttons = QHBoxLayout()
        setup_buttons.addStretch(1)
        self.install_btn = QPushButton(tr("Modely ze souboru…"))
        theme.set_role(self.install_btn, "primary")
        self.install_btn.clicked.connect(self.install_requested)
        self.download_btn = QPushButton(tr("Stáhnout modely…"))
        self.download_btn.clicked.connect(self.download_requested)
        setup_buttons.addWidget(self.install_btn)
        setup_buttons.addWidget(self.download_btn)
        setup_layout.addLayout(setup_buttons)
        layout.addSpacing(6)
        layout.addWidget(self.setup)
        self.setup.hide()

        self.version = QLabel(tr("aplikace {version}").format(version=__version__))
        self.version.setObjectName("muted")
        self.version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.version)

        self._recommend_env = False
        self._library_ok = False
        self.set_state(library_ok=False, models_ok=False)

    # --- stav ------------------------------------------------------------------------

    def set_folders(self, models_dir: Path, work_root: Path) -> None:
        self.models_row.set_path(models_dir)
        self.work_row.set_path(work_root)
        free = free_bytes(models_dir)
        if free is None:
            self.space.setText("")
        elif free < LOW_SPACE:
            self.space.setText(
                tr(
                    "Na disku složky modelů je volných jen {size}; balík modelů potřebuje asi 6 GB."
                ).format(size=format_size(free))
            )
            theme.set_role(self.space, "missing")
        else:
            self.space.setText(tr("Volné místo na disku: {size}.").format(size=format_size(free)))
            theme.set_role(self.space, "")

    def set_state(self, *, library_ok: bool, models_ok: bool) -> None:
        """Laciný stav před kontrolou: je nastavená knihovna a je co v modelech."""
        self._library_ok = library_ok
        if not library_ok:
            self._recommend(True)
            self._set_status("warn", tr("Knihovna SpeechScope není nastavená."))
        elif not models_ok:
            self._recommend(True)
            self._set_status("warn", tr("Modely zatím nejsou nainstalované."))
        else:
            self._recommend(False)
            self._set_status("ok", tr("Modely jsou nainstalované, můžete začít."))
        self._show_setup(not models_ok)

    def set_report(self, report: dict[str, Any] | None) -> None:
        """Zpřesnění z `doctor --json` po kontrole na stránce Prostředí."""
        if report is None:
            self._recommend(True)
            self._set_status("missing", tr("Knihovnu se nepodařilo spustit."))
            self._show_setup(True)
        elif report.get("all_ready"):
            self._recommend(False)
            self._set_status("ok", tr("Prostředí zkontrolováno, vše je připravené."))
            self._show_setup(False)
        else:
            self._recommend(True)
            self._set_status("warn", tr("V prostředí něco chybí, podívejte se na Prostředí."))
            models = report.get("models") or {}
            missing = any(not m.get("present") for m in models.values()) if models else True
            self._show_setup(missing)

    def recommends_environment(self) -> bool:
        return self._recommend_env

    def _show_setup(self, shown: bool) -> None:
        self.setup.setVisible(shown)
        self.status.setVisible(not shown)  # blok říká totéž podrobněji
        self.install_btn.setEnabled(self._library_ok)
        self.download_btn.setEnabled(self._library_ok)
        self.setup_note.setText(
            tr(
                "Modely zatím nejsou nainstalované. Nainstalujte je z balíku "
                "(zip z USB nebo sdíleného disku), nebo je stáhněte z internetu."
            )
            if self._library_ok
            else tr("Napřed je třeba v Nastavení ukázat na knihovnu SpeechScope.")
        )

    def _recommend(self, env: bool) -> None:
        """Modré je doporučené tlačítko, druhé zůstane obyčejné."""
        self._recommend_env = env
        theme.set_role(self.env_btn, "primary" if env else "")
        theme.set_role(self.start_btn, "" if env else "primary")

    def _set_status(self, role: str, text: str) -> None:
        self.status.setText(text)
        theme.set_role(self.status, role)
