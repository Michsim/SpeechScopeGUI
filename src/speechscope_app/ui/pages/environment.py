"""Stránka Prostředí: výstup `doctor --json` jako přehled karet.

Nahoře stav celku a tlačítko kontroly, pod ním karty částí knihovny,
tabulka modelů a přehled akcelerace. Vše čte jen z JSON, který vrací
knihovna; nic o modelech se tu nehádá.
"""

from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.library import Library, LibraryError
from .. import theme

NOT_CHECKED = "Prostředí zatím nebylo zkontrolováno."
NOT_CHECKED_HINT = "Stiskni Zkontrolovat nebo F5."


# --- drobné stavební prvky ----------------------------------------------------


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(True)
    return label


def _pill(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("pill")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    theme.set_role(label, role)
    return label


class Card(QFrame):
    """Bílá karta s barevným proužkem vlevo podle role."""

    def __init__(self, role: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 12, 16, 12)
        self.body.setSpacing(4)
        theme.set_role(self, role)

    def set_role(self, role: str) -> None:
        theme.set_role(self, role)


class StatusCard(Card):
    """Karta jedné části: název, štítek stavu, doplňující řádek."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__("neutral", parent)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = _label(title, "card_title")
        self.pill = _pill("—", "neutral")
        head.addWidget(self.title, 1)
        head.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(head)
        self.detail = _label("", "muted")
        self.body.addWidget(self.detail)
        self.detail.hide()

    def set_state(self, role: str, pill: str, pill_role: str, detail: str = "") -> None:
        self.set_role(role)
        self.pill.setText(pill)
        theme.set_role(self.pill, pill_role)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))

    def clear(self) -> None:
        self.set_state("neutral", "—", "neutral")


# --- stránka ------------------------------------------------------------------


class EnvironmentPage(QWidget):
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self.report: dict[str, Any] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        # hlavička stránky
        layout.addWidget(_label("Prostředí", "page_title"))
        layout.addWidget(
            _label(
                "Kontrola knihovny SpeechScope, modelů a grafické karty. "
                "Dávku jde spustit, jen když je připravené to, co protokol potřebuje.",
                "page_subtitle",
            )
        )

        # stavová karta
        self.status_card = Card("neutral")
        row = QHBoxLayout()
        row.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.summary = _label(NOT_CHECKED, "headline")
        self.summary_detail = _label(NOT_CHECKED_HINT, "muted")
        text.addWidget(self.summary)
        text.addWidget(self.summary_detail)
        row.addLayout(text, 1)
        self.settings_btn = QPushButton("Nastavení…")
        self.settings_btn.clicked.connect(self.settings_requested)
        self.settings_btn.hide()
        row.addWidget(self.settings_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.check_btn = QPushButton("Zkontrolovat")
        theme.set_role(self.check_btn, "primary")
        self.check_btn.clicked.connect(self.refresh)
        row.addWidget(self.check_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.status_card.body.addLayout(row)
        layout.addWidget(self.status_card)

        # části knihovny
        layout.addWidget(_label("Části knihovny", "section"))
        self.providers = QWidget()
        grid = QGridLayout(self.providers)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.provider_cards: dict[str, StatusCard] = {}
        for i, (name, title) in enumerate(contract.PROVIDER_LABELS.items()):
            card = StatusCard(title)
            self.provider_cards[name] = card
            grid.addWidget(card, i // 2, i % 2)
        layout.addWidget(self.providers)

        # modely
        layout.addWidget(_label("Modely", "section"))
        self.models = QTableWidget()
        self.models.setColumnCount(4)
        self.models.setHorizontalHeaderLabels(["", "Model", "K čemu slouží", "Cesta"])
        self.models.verticalHeader().hide()
        self.models.setShowGrid(False)
        self.models.setAlternatingRowColors(True)
        self.models.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.models.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.models.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.models.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.models.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.models.setColumnWidth(0, 28)
        layout.addWidget(self.models)

        # akcelerace
        layout.addWidget(_label("Akcelerace", "section"))
        self.gpu = QWidget()
        gpu_row = QHBoxLayout(self.gpu)
        gpu_row.setContentsMargins(0, 0, 0, 0)
        gpu_row.setSpacing(12)
        self.gpu_cards: dict[str, StatusCard] = {}
        for key, title in (
            ("whisper", "Přepis (CTranslate2)"),
            ("onnx", "Segmentace (ONNX Runtime)"),
            ("torch", "Torch"),
        ):
            card = StatusCard(title)
            self.gpu_cards[key] = card
            gpu_row.addWidget(card, 1)
        layout.addWidget(self.gpu)
        layout.addStretch(1)

        self._clear_cards()

    # --- veřejné --------------------------------------------------------------

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self.check_btn.setEnabled(library is not None)
        self.check_btn.setText("Zkontrolovat")
        self.settings_btn.setVisible(library is None)
        self.report = None
        if library is None:
            self._set_status(
                "warn",
                "Knihovna SpeechScope není nastavená.",
                "V Nastavení ukaž na speechscope.exe a složku s modely.",
            )
        else:
            self._set_status("neutral", NOT_CHECKED, NOT_CHECKED_HINT)
        self._clear_cards()

    def refresh(self) -> None:
        if self._library is None:
            return
        self.check_btn.setEnabled(False)
        self.check_btn.setText("Kontroluji…")
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        QApplication.processEvents()
        try:
            version = self._library.version()
            report = self._library.doctor()
        except LibraryError as exc:
            self.report = None
            self._set_status("missing", "Knihovnu se nepodařilo spustit.", str(exc))
            self._clear_cards()
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.check_btn.setText("Zkontrolovat znovu")
            self.check_btn.setEnabled(True)
        self.report = report
        self._show(version, report)

    # --- vykreslení -----------------------------------------------------------

    def _set_status(self, role: str, headline: str, detail: str) -> None:
        self.status_card.set_role(role)
        self.summary.setText(headline)
        self.summary_detail.setText(detail)

    def _clear_cards(self) -> None:
        for card in (*self.provider_cards.values(), *self.gpu_cards.values()):
            card.clear()
        self.models.setRowCount(0)
        self.models.hide()

    def _show(self, version: str, report: dict[str, Any]) -> None:
        providers = report.get("providers", {})
        models = report.get("models", {})
        self._show_summary(version, report, providers, models)
        self._show_providers(providers)
        self._show_models(models)
        self._show_gpu(report.get("gpu", {}))

    def _show_summary(
        self,
        version: str,
        report: dict[str, Any],
        providers: dict[str, Any],
        models: dict[str, Any],
    ) -> None:
        detail = f"Knihovna {version}, modely v {report.get('models_dir')}."
        if version != contract.KNOWN_LIBRARY_VERSION:
            detail += f" Aplikace byla ověřená s verzí {contract.KNOWN_LIBRARY_VERSION}."
        if report.get("all_ready"):
            self._set_status("ok", "Všechno je připravené.", detail)
            return
        missing_providers = [
            contract.PROVIDER_LABELS.get(n, n) for n, p in providers.items() if not p.get("ready")
        ]
        missing_models = [m.get("name", k) for k, m in models.items() if not m.get("present")]
        parts = []
        if missing_providers:
            parts.append("nefunguje " + ", ".join(missing_providers))
        if missing_models:
            parts.append("chybí model " + ", ".join(missing_models))
        what = "; ".join(parts) or "knihovna hlásí, že něco chybí"
        self._set_status("missing", "Něco chybí.", what[0].upper() + what[1:] + ". " + detail)

    def _show_providers(self, providers: dict[str, Any]) -> None:
        for name, card in self.provider_cards.items():
            p = providers.get(name)
            if p is None:
                card.clear()
            elif p.get("ready"):
                card.set_state("ok", "Připraveno", "ok")
            else:
                card.set_state("missing", "Chybí", "missing", f"Potřebuje: {p.get('needs', '')}")

    def _show_models(self, models: dict[str, Any]) -> None:
        self.models.setRowCount(len(models))
        self.models.setVisible(bool(models))
        for r, (key, m) in enumerate(models.items()):
            present = bool(m.get("present"))
            path = str(m.get("path", ""))
            dot = QTableWidgetItem("●")
            dot.setForeground(QColor(theme.OK if present else theme.MISSING))
            dot.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setToolTip("je na místě" if present else "chybí")
            # jen poslední část: složka modelů je ve stavové kartě, celá cesta v nápovědě
            path_item = QTableWidgetItem(PureWindowsPath(path).name if path else "")
            path_item.setForeground(QColor(theme.MUTED))
            path_item.setToolTip(path)
            cells = (
                dot,
                QTableWidgetItem(str(m.get("name", key))),
                QTableWidgetItem(str(m.get("used_by", ""))),
                path_item,
            )
            for c, item in enumerate(cells):
                self.models.setItem(r, c, item)
        self._fit_models()

    def _show_gpu(self, gpu: dict[str, Any]) -> None:
        """Akcelerace není podmínka: CPU je v pořádku, jen pomalejší."""
        cuda = int(gpu.get("ctranslate2_cuda") or 0)
        self._set_accel(
            "whisper",
            cuda > 0,
            "CUDA" if cuda > 0 else "CPU",
            f"{cuda} zařízení CUDA." if cuda > 0 else "Bez grafické karty, přepis bude pomalejší.",
        )
        names = [
            str(p).removesuffix("ExecutionProvider") for p in gpu.get("onnxruntime_providers") or []
        ]
        dml = bool(gpu.get("onnxruntime_gpu"))
        self._set_accel(
            "onnx",
            dml,
            "DirectML" if dml else "CPU",
            "Providery: " + ", ".join(names) + "." if names else "onnxruntime není k dispozici.",
        )
        torch_cuda = bool(gpu.get("torch_cuda"))
        build = str(gpu.get("torch_build") or "")
        self._set_accel(
            "torch",
            torch_cuda,
            "CUDA" if torch_cuda else "CPU",
            f"Sestavení {build}." if build else "Torch není nainstalovaný.",
        )

    def _set_accel(self, key: str, gpu: bool, pill: str, detail: str) -> None:
        self.gpu_cards[key].set_state(
            "ok" if gpu else "neutral", pill, "accent" if gpu else "neutral", detail
        )

    def _fit_models(self) -> None:
        """Tabulka bez vlastního scrollu: výšku má přesně podle řádků."""
        height = self.models.horizontalHeader().height() + 4
        for r in range(self.models.rowCount()):
            height += self.models.rowHeight(r)
        self.models.setFixedHeight(max(height, 44))
