"""Domů: první stránka nabídky, přehled výpočtů a stavu aplikace (19. 11. 2026).

Do 19. 11. 2026 byl Domů rozcestník se dvěma velkými kartami a průvodcem hodnocení
v druhém kroku. Hodnocení pacienta má teď vlastní stránku v nabídce (`pages/assess.py`)
vedle Analýzy nahrávek, takže Domů jen ukazuje: nahoře nadpis a dvě stejná modrá tlačítka
Nové hodnocení… a Nová analýza… (vedou na ty stránky), pod nimi pruh „právě běží“
s postupem dávky a frontou, který hlavní okno plní z Výpočtu (když nic neběží, karta
„Nic se nepočítá“), a dlaždice Stav a nastavení (`StatusTile`): knihovna a modely, normy
(i varování o změněné feature), protokoly, dimenze, složka výsledků a kontrola kvality,
každá s tlačítkem na svou stránku nebo dialog. Dole pod čarou Nová norma… a Nastavení….
Stránka sama nic nepočítá, jen volá to, co existuje.

Dokud chybí modely, je nad dlaždicemi blok „První nastavení“: složka modelů
(návrh vedle aplikace) a složka výsledků, obojí jde změnit, a tlačítka na
instalaci modelů z balíku nebo stažení. Po instalaci blok zmizí.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...backend.cache import format_size, free_bytes
from ...i18n import tr
from .. import theme
from ..widgets.clean_table import pill
from ..widgets.elided_label import ElidedLabel

LOW_SPACE = 6_000_000_000  # balík modelů má přes 5 GB
QUEUE_ROWS = 3  # řádků fronty na Domů; zbytek je v přehledu Výsledků


def rough_seconds(seconds: float) -> str:
    """Hrubý odhad do fronty: ~1 min, ~14 min, ~2 h 05 min."""
    minutes = max(1, int(round(seconds / 60)))
    if minutes < 60:
        return tr("~{m} min").format(m=minutes)
    if minutes % 60 == 0:
        return tr("~{h} h").format(h=minutes // 60)
    return tr("~{h} h {m:02d} min").format(h=minutes // 60, m=minutes % 60)


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """Řádek fronty na Domů: jedna dávka, nebo všechny dávky jednoho hodnocení či normy."""

    title: str
    chips: tuple[str, ...] = ()  # úlohy, u analýzy „analýza · úloha“
    seconds: float | None = None  # odhad; None = neznámý
    indices: tuple[int, ...] = ()  # pozice dávek ve frontě hlavního okna
    warn: bool = False  # norma: štítek žlutě, bývá dlouhá


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


DOT_COLORS = {"ok": theme.OK, "warn": theme.WARN, "missing": theme.MISSING}


class StatusTile(QFrame):
    """Dlaždice stavu: tečka, název, jeden řádek textu a tlačítko, které vede dál."""

    def __init__(self, title: str, button: str, *, wrap: bool = False) -> None:
        super().__init__()
        self.setObjectName("card")
        # název a tlačítko v řádku, text pod nimi přes celou šířku (v úzkém okně se
        # vedle tlačítka nevešel); volné místo zůstává dole
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(16, 12, 12, 12)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(2)
        self.grid.setColumnStretch(1, 1)
        self.dot = QLabel("")
        self.dot.setFixedSize(10, 10)
        self.grid.addWidget(self.dot, 0, 0)
        self.title = QLabel(title)
        self.title.setObjectName("card_title")
        self.grid.addWidget(self.title, 0, 1)
        self.button = QPushButton(button)
        self.grid.addWidget(self.button, 0, 2)
        # stav prostředí je věta a zalomí se; cesty a počty se zkrátí výpustkou
        self.text: QLabel = QLabel("") if wrap else ElidedLabel("")
        self.text.setWordWrap(wrap)
        self.text.setObjectName("muted")
        self._lines = 0
        self.add_line(self.text)
        self._role = ""
        self.set_role("")

    def set_role(self, role: str) -> None:
        """Barva tečky: ok, warn, missing; cokoli jiného šedá (jen informace)."""
        self._role = role
        color = DOT_COLORS.get(role, "#9ca3af")
        self.dot.setStyleSheet(f"background: {color}; border-radius: 5px;")

    def add_line(self, widget: QWidget) -> None:
        """Další řádek pod názvem přes celou šířku dlaždice."""
        self.grid.setRowStretch(self._lines + 1, 0)
        self._lines += 1
        self.grid.addWidget(widget, self._lines, 1, 1, 2)
        self.grid.setRowStretch(self._lines + 1, 1)

    def role(self) -> str:
        return self._role

    def set_text(self, text: str) -> None:
        self.text.setText(text)


class HomePage(QWidget):
    assessment_requested = Signal()  # Nové hodnocení… → stránka Hodnocení pacienta
    analysis_requested = Signal()  # Analýza nahrávek → stránka Analýza
    norm_requested = Signal()  # Nová norma… → průvodce Nová norma
    environment_requested = Signal()  # stránka Prostředí
    norms_requested = Signal()  # stránka Normy (varování o změněné feature)
    protocols_requested = Signal()  # stránka Protokoly
    dimensions_requested = Signal()  # stránka Dimenze
    quality_requested = Signal()  # dialog Prahy kontroly kvality
    settings_requested = Signal()  # dialog Nastavení
    run_requested = Signal()  # stránka Výpočet (pruh „právě běží“)
    queue_requested = Signal()  # „Zobrazit vše ve Výsledcích“: přehled s frontou
    queue_remove_requested = Signal(object)  # řádek fronty ✕: indexy dávek (tuple[int, ...])
    models_dir_requested = Signal()  # uživatel chce změnit složku modelů
    work_root_requested = Signal()  # … a složku výsledků
    install_requested = Signal()  # modely ze souboru (balík)
    download_requested = Signal()  # stažení z internetu

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.headline = QLabel(tr("Přehled"))
        self.headline.setObjectName("page_title")
        titles.addWidget(self.headline)
        self.intro = QLabel(tr("Co se právě počítá a v jakém stavu je aplikace."))
        self.intro.setObjectName("muted")
        titles.addWidget(self.intro)
        head.addLayout(titles, 1)
        # obě tlačítka stejně modrá (rozhodnutí uživatele 19. 11. 2026)
        self.assess_btn = QPushButton(tr("Nové hodnocení…"))
        self.assess_btn.setToolTip(
            tr(
                "Nahrávky pacienta po úlohách. Výsledkem je pavouk proti normě "
                "a strom hodnot, uložený k pacientovi jako vyšetření."
            )
        )
        theme.set_role(self.assess_btn, "primary")
        self.assess_btn.clicked.connect(self.assessment_requested)
        self.start_btn = QPushButton(tr("Nová analýza…"))
        self.start_btn.setToolTip(
            tr(
                "Složka s nahrávkami, protokol a tabulka feature pro každou nahrávku. "
                "Pro studie a export do statistiky."
            )
        )
        theme.set_role(self.start_btn, "primary")
        self.start_btn.clicked.connect(self.analysis_requested)
        head.addWidget(self.assess_btn, 0, Qt.AlignmentFlag.AlignBottom)
        head.addWidget(self.start_btn, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(head)

        # --- nic neběží ------------------------------------------------------------------
        self.idle = QFrame()
        self.idle.setObjectName("card")
        idle_layout = QVBoxLayout(self.idle)
        idle_layout.setContentsMargins(16, 12, 16, 12)
        idle_layout.setSpacing(2)
        idle_title = QLabel(tr("Nic se nepočítá"))
        idle_title.setObjectName("card_title")
        idle_layout.addWidget(idle_title)
        self.idle_text = QLabel(
            tr(
                "Začněte hodnocením pacienta nebo analýzou nahrávek. "
                'Hotové běhy jsou ve <a href="results">Výsledcích ›</a>'
            )
        )
        self.idle_text.setObjectName("muted")
        self.idle_text.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.idle_text.linkActivated.connect(lambda _href: self.queue_requested.emit())
        idle_layout.addWidget(self.idle_text)
        layout.addWidget(self.idle)

        # --- právě běží ------------------------------------------------------------------
        self.running = QFrame()
        self.running.setObjectName("card")
        theme.set_role(self.running, "accent")
        self.running.setCursor(Qt.CursorShape.PointingHandCursor)
        self.running.mousePressEvent = lambda event: self.run_requested.emit()  # type: ignore[method-assign]
        running_box = QVBoxLayout(self.running)
        running_box.setContentsMargins(16, 10, 16, 10)
        running_box.setSpacing(8)
        run_layout = QHBoxLayout()
        run_layout.setSpacing(14)
        running_box.addLayout(run_layout)
        self.running_pill = QLabel(tr("právě běží"))
        self.running_pill.setObjectName("pill")
        theme.set_role(self.running_pill, "accent")
        run_layout.addWidget(self.running_pill, 0, Qt.AlignmentFlag.AlignTop)
        run_text = QVBoxLayout()
        run_text.setSpacing(4)
        run_head = QHBoxLayout()
        self.running_title = ElidedLabel("")
        self.running_title.setObjectName("card_title")
        run_head.addWidget(self.running_title, 1)
        self.running_detail = QLabel("")
        self.running_detail.setObjectName("muted")
        run_head.addWidget(self.running_detail)
        run_text.addLayout(run_head)
        self.running_bar = QProgressBar()
        self.running_bar.setTextVisible(False)
        self.running_bar.setFixedHeight(8)
        self.running_bar.setRange(0, 0)
        run_text.addWidget(self.running_bar)
        run_layout.addLayout(run_text, 1)
        self.run_btn = QPushButton(tr("Výpočet"))
        self.run_btn.clicked.connect(self.run_requested)
        run_layout.addWidget(self.run_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        # fronta pod pruhem (3. 11. 2026, podoba A): nejvýš QUEUE_ROWS řádků, dávky
        # jednoho hodnocení či normy jako jeden řádek, ✕ vyndá z fronty
        self.queue_box = QWidget()
        queue_layout = QVBoxLayout(self.queue_box)
        queue_layout.setContentsMargins(0, 4, 0, 0)
        queue_layout.setSpacing(0)
        queue_head = QHBoxLayout()
        self.queue_title = QLabel("")
        self.queue_title.setObjectName("muted")
        caps = self.queue_title.font()
        caps.setBold(True)
        caps.setCapitalization(caps.Capitalization.AllUppercase)
        self.queue_title.setFont(caps)
        queue_head.addWidget(self.queue_title)
        queue_head.addStretch(1)
        self.queue_finish = QLabel("")
        self.queue_finish.setObjectName("muted")
        queue_head.addWidget(self.queue_finish)
        queue_layout.addLayout(queue_head)
        self.queue_rows = QVBoxLayout()
        self.queue_rows.setSpacing(0)
        queue_layout.addLayout(self.queue_rows)
        self.queue_more = QLabel("")
        self.queue_more.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.queue_more.linkActivated.connect(lambda _link: self.queue_requested.emit())
        queue_layout.addWidget(self.queue_more)
        running_box.addWidget(self.queue_box)
        self.queue_box.hide()
        self._queue_widgets: list[QWidget] = []
        self._queue_shown: list[QueueEntry] = []
        layout.addWidget(self.running)
        self.running.hide()

        # --- první nastavení (jen dokud chybí modely) ---------------------------------------
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
        self.setup_note = QLabel("")
        self.setup_note.setWordWrap(True)
        setup_layout.addWidget(self.setup_note)
        setup_buttons = QHBoxLayout()
        self.done_btn = QPushButton(tr("Hotovo"))
        self.done_btn.setToolTip(tr("Schová tento blok; složky i modely jdou změnit v Prostředí."))
        self.done_btn.clicked.connect(self.dismiss_setup)
        setup_buttons.addWidget(self.done_btn)
        setup_buttons.addStretch(1)
        self.install_btn = QPushButton(tr("Modely ze souboru…"))
        theme.set_role(self.install_btn, "primary")
        self.install_btn.clicked.connect(self.install_requested)
        self.download_btn = QPushButton(tr("Stáhnout modely…"))
        self.download_btn.clicked.connect(self.download_requested)
        setup_buttons.addWidget(self.install_btn)
        setup_buttons.addWidget(self.download_btn)
        setup_layout.addLayout(setup_buttons)
        layout.addWidget(self.setup)
        self.setup.hide()

        # --- stav a nastavení --------------------------------------------------------------
        self.tiles_title = QLabel(tr("Stav a nastavení"))
        self.tiles_title.setObjectName("muted")
        caps = self.tiles_title.font()
        caps.setBold(True)
        caps.setCapitalization(caps.Capitalization.AllUppercase)
        self.tiles_title.setFont(caps)
        layout.addWidget(self.tiles_title)
        tiles = QGridLayout()
        tiles.setSpacing(12)
        self.env_tile = StatusTile(tr("Knihovna a modely"), tr("Prostředí"), wrap=True)
        self.norms_tile = StatusTile(tr("Normy"), tr("Normy"))
        self.protocols_tile = StatusTile(tr("Protokoly"), tr("Protokoly"))
        self.dimensions_tile = StatusTile(tr("Dimenze"), tr("Dimenze"))
        self.work_tile = StatusTile(tr("Složka výsledků"), tr("Změnit…"))
        self.quality_tile = StatusTile(tr("Kontrola kvality"), tr("Prahy…"))
        self.tiles = (
            self.env_tile,
            self.norms_tile,
            self.protocols_tile,
            self.dimensions_tile,
            self.work_tile,
            self.quality_tile,
        )
        for index, tile in enumerate(self.tiles):
            tiles.addWidget(tile, index // 3, index % 3)
            tiles.setColumnStretch(index % 3, 1)
        layout.addLayout(tiles)
        self.status = self.env_tile.text
        self.env_btn = self.env_tile.button
        self.env_btn.clicked.connect(self.environment_requested)
        self.norms_tile.button.clicked.connect(self.norms_requested)
        self.protocols_tile.button.clicked.connect(self.protocols_requested)
        self.dimensions_tile.button.clicked.connect(self.dimensions_requested)
        self.work_tile.button.clicked.connect(self.work_root_requested)
        self.quality_tile.button.clicked.connect(self.quality_requested)
        # varování o normě se změněnou feature: druhý řádek dlaždice Normy
        self.norm_warning = QLabel("")
        self.norm_warning.setObjectName("muted")
        self.norm_warning.setWordWrap(True)
        self.norm_warning.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.norm_warning.linkActivated.connect(lambda _href: self.norms_requested.emit())
        self.norms_tile.add_line(self.norm_warning)
        self.norm_warning.hide()

        layout.addStretch(1)

        # --- dole: Nová norma a nastavení -----------------------------------------------------
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setObjectName("rule")
        layout.addWidget(rule)
        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        self.norm_btn = QPushButton(tr("Nová norma…"))
        self.norm_btn.setToolTip(
            tr(
                "Nahrávky zdravých kontrol s věkem a pohlavím. Dělá se jednou za čas, "
                "hotové normy jsou na stránce Normy."
            )
        )
        self.norm_btn.clicked.connect(self.norm_requested)
        bottom.addWidget(self.norm_btn)
        bottom.addStretch(1)
        self.version = QLabel(tr("aplikace {version}").format(version=__version__))
        self.version.setObjectName("muted")
        bottom.addWidget(self.version)
        self.settings_btn = QPushButton(tr("Nastavení…"))
        self.settings_btn.clicked.connect(self.settings_requested)
        bottom.addWidget(self.settings_btn)
        layout.addLayout(bottom)

        self._norms_role = ""
        self._recommend_env = False
        self._library_ok = False
        self._models_present = False
        self._dismissed = False  # Hotovo: blok se v tomto běhu už neukáže
        self.set_state(library_ok=False, models_ok=False)

    # --- stav ------------------------------------------------------------------------

    def set_folders(self, models_dir: Path, work_root: Path) -> None:
        self.models_row.set_path(models_dir)
        self.work_row.set_path(work_root)
        work_free = free_bytes(work_root)
        self.work_tile.set_text(
            tr("{path} · volných {size}").format(path=work_root, size=format_size(work_free))
            if work_free is not None
            else str(work_root)
        )
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
        self._models_present = models_ok
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
            providers = report.get("providers") or {}
            # blok má smysl, dokud není připravený některý provider; balík pro
            # kliniku nemá wavlm ani pyannote a přesto je všechno připravené
            self._models_present = all(p.get("ready") for p in providers.values())
            self._show_setup(not self._models_present)

    def recommends_environment(self) -> bool:
        return self._recommend_env

    def dismiss_setup(self) -> None:
        """Hotovo: blok pryč do dalšího startu; stav zůstane ve štítku."""
        self._dismissed = True
        self._show_setup(False)

    def set_running(self, title: str, detail: str) -> None:
        """Pruh „právě běží“: název dávky a postup; fronta je pod ním (`set_queue`)."""
        self.running_title.setText(title)
        self.running_detail.setText(detail)
        self.idle.hide()
        self.running.show()

    def set_queue(self, entries: list[QueueEntry], finish: str = "") -> None:
        """Fronta pod pruhem „právě běží“; prázdná = jen pruh jako dřív.

        `finish` je čas, kdy bude hotovo všechno (HH:MM), prázdné = neznámý. Řádky se
        přestaví jen při změně fronty, ne po každé nahrávce běžící dávky.
        """
        self.queue_finish.setText(
            tr("vše hotovo kolem <b>{time}</b>").format(time=finish) if finish else ""
        )
        if entries == self._queue_shown:
            return
        self._queue_shown = list(entries)
        for widget in self._queue_widgets:
            widget.hide()  # deleteLater maže až z fronty událostí, napřed skrýt
            widget.deleteLater()
        self._queue_widgets = []
        count = sum(len(e.indices) or 1 for e in entries)
        self.queue_box.setVisible(bool(entries))
        if not entries:
            return
        self.queue_title.setText(tr("Ve frontě · {n}").format(n=count))
        for number, entry in enumerate(entries[:QUEUE_ROWS], start=1):
            row = self._queue_row(number, entry)
            self.queue_rows.addWidget(row)
            self._queue_widgets.append(row)
        hidden = len(entries) - QUEUE_ROWS
        self.queue_more.setText(
            "<a href='all'>"
            + tr("+{n} další · Zobrazit vše ve Výsledcích ›").format(n=hidden)
            + "</a>"
            if hidden > 0
            else ""
        )
        self.queue_more.setVisible(hidden > 0)

    def queue_entries(self) -> list[QLabel]:
        """Názvy viditelných řádků fronty (pro testy)."""
        return [w.findChild(QLabel, "queue_name") for w in self._queue_widgets]

    def _queue_row(self, number: int, entry: QueueEntry) -> QWidget:
        row = QWidget()
        row.setMinimumHeight(40)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        no = QLabel(str(number))
        no.setObjectName("muted")
        no.setFixedWidth(16)
        no.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(no)
        name = QLabel(entry.title)
        name.setObjectName("queue_name")
        bold = name.font()
        bold.setBold(True)
        name.setFont(bold)
        layout.addWidget(name)
        for chip in entry.chips:
            layout.addWidget(
                pill(chip, "warn" if entry.warn else "neutral"), 0, Qt.AlignmentFlag.AlignVCenter
            )
        layout.addStretch(1)
        when = QLabel(rough_seconds(entry.seconds) if entry.seconds is not None else "")
        when.setObjectName("muted")
        layout.addWidget(when)
        remove = QPushButton("✕")
        remove.setFlat(True)
        remove.setFixedWidth(40)
        remove.setToolTip(tr("Vyndat z fronty"))
        remove.setAccessibleName(tr("Vyndat z fronty"))
        indices = entry.indices
        remove.clicked.connect(lambda: self.queue_remove_requested.emit(indices))
        layout.addWidget(remove)
        # klik na řádek = fronta v přehledu Výsledků (ne Průběh běžící dávky)
        row.mousePressEvent = lambda event: (event.accept(), self.queue_requested.emit())  # type: ignore[method-assign]
        return row

    def set_progress(self, processed: int, total: int) -> None:
        if total > 0:
            self.running_bar.setRange(0, total)
            self.running_bar.setValue(processed)
        else:
            self.running_bar.setRange(0, 0)

    def clear_running(self) -> None:
        self.running.hide()
        self.idle.show()
        self.running_bar.setRange(0, 0)

    def set_norm_warnings(self, names: list[str]) -> None:
        """Normy, kterým se od spočítání změnila feature (odkaz vede na Normy)."""
        self.norms_tile.set_role("warn" if names else self._norms_role)
        if not names:
            self.norm_warning.hide()
            return
        self.norm_warning.setText(
            tr(
                "Od spočítání normy {names} se v knihovně změnily feature, "
                '<a href="norms">přepočítejte ji na Normách</a>.'
            ).format(names=", ".join(f"„{n}“" for n in names))
        )
        self.norm_warning.show()

    def set_overview(
        self, *, norms: int, builtin: int, custom: int, dimensions: int, quality_custom: bool
    ) -> None:
        """Čísla do dlaždic: počet norem, protokolů a os pavouka, vlastní prahy kvality."""
        self._norms_role = "ok" if norms else ""
        if self.norm_warning.isHidden():
            self.norms_tile.set_role(self._norms_role)
        self.norms_tile.set_text(
            tr("počet norem: {n}").format(n=norms) if norms else tr("zatím žádná norma")
        )
        self.protocols_tile.set_text(
            tr("přibalené: {a} · vlastní: {b}").format(a=builtin, b=custom)
        )
        self.dimensions_tile.set_text(tr("osy pavouka: {n}").format(n=dimensions))
        self.quality_tile.set_text(tr("upravené prahy") if quality_custom else tr("výchozí prahy"))

    def _show_setup(self, shown: bool) -> None:
        shown = shown and not self._dismissed
        self.setup.setVisible(shown)
        self.install_btn.setEnabled(self._library_ok)
        self.download_btn.setEnabled(self._library_ok)
        if not self._library_ok:
            note = tr("Napřed je třeba v Nastavení ukázat na knihovnu SpeechScope.")
        elif self._models_present:
            note = tr("Modely jsou na místě. Blok zavřete tlačítkem Hotovo.")
        else:
            note = tr(
                "Modely zatím nejsou nainstalované. Nainstalujte je z balíku "
                "(zip z USB nebo sdíleného disku), nebo je stáhněte z internetu."
            )
        self.setup_note.setText(note)

    def _recommend(self, env: bool) -> None:
        """Bez knihovny nebo modelů je modré tlačítko Prostředí, karty počkají."""
        self._recommend_env = env
        theme.set_role(self.env_btn, "primary" if env else "")

    def _set_status(self, role: str, text: str) -> None:
        self.status.setText(text)
        self.env_tile.set_role(role)
