"""Hodnocení pacienta: průvodce z nahrávek jako vlastní stránka nabídky (19. 11. 2026).

Dřív byl průvodce druhým krokem stránky Domů. Stránka drží jeden `AssessmentBuildWidget`
(`set_builder`); hlavní okno ho vyrobí při prvním zobrazení a rozdělaný nechává, dokud ho
uživatel nespustí nebo nezahodí (Storno v prvním kroku = čerstvý průvodce). Po Spustit
jednoho pacienta nabídne „Další vyšetření téhož pacienta…“ (`offer_next_visit`). Odkaz
„…nebo z hotových běhů“ vede do dialogu nad hotovými běhy na Pacientech (`runs_requested`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...i18n import tr
from .. import theme


class AssessPage(QWidget):
    runs_requested = Signal()  # hodnocení z hotových běhů (dialog na Pacientech)
    next_visit_requested = Signal()  # „Další vyšetření téhož pacienta…“ po Spustit

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.title = QLabel(tr("Hodnocení pacienta"))
        self.title.setObjectName("page_title")
        self.title.setToolTip(tr("Z nahrávek vznikne vyšetření pacienta se srovnáním s normou."))
        head.addWidget(self.title, 1)
        self.runs_link = QLabel(tr('<a href="runs">…nebo z hotových běhů</a>'))
        self.runs_link.setObjectName("muted")
        self.runs_link.setToolTip(tr("Nahrávky, které už jsou spočítané, bez dalšího výpočtu"))
        self.runs_link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.runs_link.linkActivated.connect(lambda _href: self.runs_requested.emit())
        head.addWidget(self.runs_link)
        layout.addLayout(head)

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

        self.empty = QLabel(tr("Knihovna není nastavená."))
        self.empty.setObjectName("muted")
        layout.addWidget(self.empty)
        self.builder_slot = QVBoxLayout()
        self.builder_slot.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.builder_slot, 1)
        self.builder: QWidget | None = None

    def set_builder(self, widget: QWidget | None) -> None:
        """Vymění průvodce za čerstvý; `None` = bez knihovny jen hláška."""
        if self.builder is not None:
            self.builder_slot.removeWidget(self.builder)
            self.builder.hide()  # deleteLater maže až z fronty událostí, napřed skrýt
            self.builder.setParent(None)
            self.builder.deleteLater()
        self.builder = widget
        self.empty.setVisible(widget is None)
        if widget is not None:
            self.builder_slot.addWidget(widget)

    def offer_next_visit(self, subject: str) -> None:
        """Po Spustit hodnocení jednoho pacienta: nabídka další návštěvy téhož pacienta."""
        self.next_visit_text.setText(
            tr(
                "Hodnocení pacienta {subject} se počítá. Máte další návštěvu téhož "
                "pacienta? Údaje zůstanou vyplněné, stačí datum a složky."
            ).format(subject=subject)
        )
        self.next_visit.show()
