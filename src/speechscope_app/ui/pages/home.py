"""Domů: první stránka nabídky a rozcestník podle výstupu (30. 10. 2026).

Nahrazuje dřívější Uvítání mimo nabídku. Nahoře dvě velké karty: Hodnocení
pacienta (nahrávky jednoho pacienta → pavouk proti normě, doporučená) a
Analýza nahrávek (složka → tabulka feature). Pod nimi pruh „právě běží“
s postupem dávky a frontou, který hlavní okno plní z Výpočtu a který zmizí,
když nic neběží. Dole pod čarou tlačítko Nová norma… (dělá se jednou za čas)
a dva řádky stavu: knihovna a modely, případně norma, které se od spočítání
změnila feature. Stránka sama nic nepočítá, jen volá to, co existuje.

Dokud chybí modely, je pod kartami blok „První nastavení“: složka modelů
(návrh vedle aplikace) a složka výsledků, obojí jde změnit, a tlačítka na
instalaci modelů z balíku nebo stažení. Po instalaci blok zmizí.

Nové hodnocení… nepřeskakuje na Pacienty: stránka má dva kroky jako Analýza.
Krok 2 (`show_builder`) je průvodce `AssessmentBuildWidget` se záhlavím
„‹ Domů · Nové hodnocení z nahrávek“ a odkazem „…nebo z hotových běhů“
(`runs_requested`); po Spustit nebo Zpět se vrátí karty (`show_cards`).
"""

from __future__ import annotations

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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...backend.cache import format_size, free_bytes
from ...i18n import tr
from .. import theme
from ..widgets.elided_label import ElidedLabel

LOW_SPACE = 6_000_000_000  # balík modelů má přes 5 GB


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


class ActionCard(QFrame):
    """Velká karta výstupu: název, popis, nápověda a modré tlačítko; dál vede jen tlačítko."""

    activated = Signal()

    def __init__(
        self, title: str, text: str, hint: str, button: str, *, primary: bool = False
    ) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setProperty("role", "option")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("headline")
        layout.addWidget(self.title)
        self.text = QLabel(text)
        self.text.setWordWrap(True)
        layout.addWidget(self.text, 1)
        self.hint = QLabel(hint)
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.button = QPushButton(button)
        theme.set_role(self.button, "primary")
        self.button.clicked.connect(self.activated)
        row = QHBoxLayout()
        row.addWidget(self.button)
        row.addStretch(1)
        layout.addLayout(row)


class HomePage(QWidget):
    assessment_requested = Signal()  # Hodnocení pacienta → průvodce v kroku 2
    runs_requested = Signal()  # z kroku 2: hodnocení z hotových běhů (dialog na Pacientech)
    analysis_requested = Signal()  # Analýza nahrávek → stránka Analýza
    norm_requested = Signal()  # Nová norma… → průvodce Nová norma
    environment_requested = Signal()  # stránka Prostředí
    norms_requested = Signal()  # stránka Normy (varování o změněné feature)
    run_requested = Signal()  # stránka Výpočet (pruh „právě běží“)
    next_visit_requested = Signal()  # „Další vyšetření téhož pacienta…“ po Spustit
    models_dir_requested = Signal()  # uživatel chce změnit složku modelů
    work_root_requested = Signal()  # … a složku výsledků
    install_requested = Signal()  # modely ze souboru (balík)
    download_requested = Signal()  # stažení z internetu

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        page_layout.addWidget(self.stack)
        cards_page = QWidget()
        self.stack.addWidget(cards_page)
        self.stack.addWidget(self._build_builder_page())
        layout = QVBoxLayout(cards_page)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)

        self.headline = QLabel(tr("Co chcete udělat?"))
        self.headline.setObjectName("page_title")
        layout.addWidget(self.headline)
        self.intro = QLabel(
            tr("Výpočet nahrávek proběhne vždy stejně, liší se jen to, co z něj vznikne.")
        )
        self.intro.setObjectName("muted")
        layout.addWidget(self.intro)

        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.assessment_card = ActionCard(
            tr("Hodnocení pacienta"),
            tr(
                "Nahrávky jednoho pacienta po úlohách. Výsledkem je pavouk proti normě "
                "a strom hodnot, uložený k pacientovi jako vyšetření."
            ),
            tr("složka s nahrávkami · nebo z hotových běhů"),
            tr("Nové hodnocení…"),
            primary=True,
        )
        self.assessment_card.activated.connect(self.assessment_requested)
        self.analysis_card = ActionCard(
            tr("Analýza nahrávek"),
            tr(
                "Složka s nahrávkami, protokol a tabulka feature pro každou nahrávku. "
                "Pro studie a export do statistiky."
            ),
            tr("složka · protokol · tabulka CSV"),
            tr("Nová analýza…"),
        )
        self.analysis_card.activated.connect(self.analysis_requested)
        cards.addWidget(self.assessment_card, 1)
        cards.addWidget(self.analysis_card, 1)
        layout.addLayout(cards)
        # tlačítka karet pro testy a klávesnici pod jmény jako dřív na uvítání
        self.start_btn = self.analysis_card.button
        self.assess_btn = self.assessment_card.button

        # --- právě běží ------------------------------------------------------------------
        self.running = QFrame()
        self.running.setObjectName("card")
        theme.set_role(self.running, "accent")
        self.running.setCursor(Qt.CursorShape.PointingHandCursor)
        self.running.mousePressEvent = lambda event: self.run_requested.emit()  # type: ignore[method-assign]
        run_layout = QHBoxLayout(self.running)
        run_layout.setContentsMargins(16, 10, 16, 10)
        run_layout.setSpacing(14)
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
        layout.addWidget(self.running)
        self.running.hide()

        # po Spustit: další návštěva téhož pacienta bez psaní údajů znovu
        self.next_visit = QFrame()
        self.next_visit.setObjectName("card")
        theme.set_role(self.next_visit, "neutral")
        next_layout = QHBoxLayout(self.next_visit)
        next_layout.setContentsMargins(16, 10, 16, 10)
        next_layout.setSpacing(14)
        self.next_visit_text = QLabel("")
        self.next_visit_text.setWordWrap(True)
        next_layout.addWidget(self.next_visit_text, 1)
        self.next_visit_btn = QPushButton(tr("Další vyšetření téhož pacienta…"))
        self.next_visit_btn.clicked.connect(self.next_visit_requested)
        next_layout.addWidget(self.next_visit_btn)
        self.next_visit_close = QPushButton(tr("Zavřít"))
        self.next_visit_close.setFlat(True)
        self.next_visit_close.clicked.connect(self.next_visit.hide)
        next_layout.addWidget(self.next_visit_close)
        layout.addWidget(self.next_visit)
        self.next_visit.hide()

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

        layout.addStretch(1)

        # --- dole: Nová norma a stav ----------------------------------------------------------
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setObjectName("rule")
        layout.addWidget(rule)
        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        self.norm_btn = QPushButton(tr("Nová norma…"))
        self.norm_btn.clicked.connect(self.norm_requested)
        bottom.addWidget(self.norm_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.norm_hint = QLabel(
            tr(
                "Nahrávky zdravých kontrol s věkem a pohlavím. Dělá se jednou za čas, "
                "hotové normy jsou na stránce Normy."
            )
        )
        self.norm_hint.setObjectName("muted")
        self.norm_hint.setWordWrap(True)
        self.norm_hint.setMaximumWidth(560)
        bottom.addWidget(self.norm_hint, 0, Qt.AlignmentFlag.AlignTop)
        bottom.addStretch(1)
        status_block = QVBoxLayout()
        status_block.setSpacing(4)
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.status = QLabel("")
        self.status.setObjectName("pill")
        status_row.addWidget(self.status)
        self.env_btn = QPushButton(tr("Prostředí"))
        self.env_btn.clicked.connect(self.environment_requested)
        status_row.addWidget(self.env_btn)
        status_block.addLayout(status_row)
        self.norm_warning = QLabel("")
        self.norm_warning.setObjectName("muted")
        self.norm_warning.setWordWrap(True)
        self.norm_warning.setMaximumWidth(520)
        self.norm_warning.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.norm_warning.linkActivated.connect(lambda _href: self.norms_requested.emit())
        status_block.addWidget(self.norm_warning)
        self.norm_warning.hide()
        self.version = QLabel(tr("aplikace {version}").format(version=__version__))
        self.version.setObjectName("muted")
        status_block.addWidget(self.version)
        bottom.addLayout(status_block)
        layout.addLayout(bottom)

        self._recommend_env = False
        self._library_ok = False
        self._models_present = False
        self._dismissed = False  # Hotovo: blok se v tomto běhu už neukáže
        self.set_state(library_ok=False, models_ok=False)

    # --- krok 2: průvodce ------------------------------------------------------------

    def _build_builder_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.home_btn = QPushButton(tr("‹ Domů"))
        self.home_btn.clicked.connect(self.show_cards)
        head.addWidget(self.home_btn)
        title = QLabel(tr("Nové hodnocení z nahrávek"))
        title.setObjectName("page_title")
        head.addWidget(title, 1)
        self.runs_link = QLabel(tr('<a href="runs">…nebo z hotových běhů</a>'))
        self.runs_link.setObjectName("muted")
        self.runs_link.setToolTip(tr("Nahrávky, které už jsou spočítané, bez dalšího výpočtu"))
        self.runs_link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.runs_link.linkActivated.connect(lambda _href: self.runs_requested.emit())
        head.addWidget(self.runs_link)
        layout.addLayout(head)
        self.builder_slot = QVBoxLayout()
        self.builder_slot.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.builder_slot, 1)
        self.builder: QWidget | None = None
        return page

    def step(self) -> int:
        return self.stack.currentIndex()

    def show_builder(self, widget: QWidget) -> None:
        """Krok 2 s čerstvým průvodcem; předchozí se zahodí."""
        self.next_visit.hide()
        self._drop_builder()
        self.builder = widget
        self.builder_slot.addWidget(widget)
        self.stack.setCurrentIndex(1)

    def show_cards(self) -> None:
        """Zpět na karty; rozdělaný průvodce se bez ptaní zahodí."""
        self._drop_builder()
        self.stack.setCurrentIndex(0)

    def _drop_builder(self) -> None:
        if self.builder is not None:
            self.builder_slot.removeWidget(self.builder)
            self.builder.hide()
            self.builder.setParent(None)
            self.builder.deleteLater()
            self.builder = None

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

    def set_running(
        self, title: str, detail: str, queued: int = 0, names: list[str] | None = None
    ) -> None:
        """Pruh „právě běží“: název dávky, postup a co čeká ve frontě (první jménem)."""
        self.running_title.setText(title)
        parts = [detail] if detail else []
        if names:
            text = tr("ve frontě: {name}").format(name=names[0])
            if len(names) > 1:
                text += f" (+{len(names) - 1})"
            parts.append(text)
        elif queued:
            parts.append(tr("ve frontě: {n}").format(n=queued))
        self.running_detail.setText(" · ".join(parts))
        self.running.show()

    def offer_next_visit(self, subject: str) -> None:
        """Po Spustit hodnocení jednoho pacienta: nabídka další návštěvy téhož pacienta."""
        self.next_visit_text.setText(
            tr(
                "Hodnocení pacienta {subject} se počítá. Máte další návštěvu téhož "
                "pacienta? Údaje zůstanou vyplněné, stačí datum a složky."
            ).format(subject=subject)
        )
        self.next_visit.show()

    def set_progress(self, processed: int, total: int) -> None:
        if total > 0:
            self.running_bar.setRange(0, total)
            self.running_bar.setValue(processed)
        else:
            self.running_bar.setRange(0, 0)

    def clear_running(self) -> None:
        self.running.hide()
        self.running_bar.setRange(0, 0)

    def set_norm_warnings(self, names: list[str]) -> None:
        """Normy, kterým se od spočítání změnila feature (odkaz vede na Normy)."""
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
        theme.set_role(self.status, role)
