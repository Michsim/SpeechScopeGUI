"""Editor obsahu protokolu: výběr feature a parametry feature i providerů.

Používá ho stránka Data (úprava pro tento běh v rozšířeném režimu)
i stránka Protokoly (úprava uloženého protokolu). Widget drží kopii
`config` protokolu jako přepsané parametry a umí z ní a z výběru
sestavit `Protocol` (`result`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.library import FeatureInfo, Library, LibraryError
from ...backend.protocol import Protocol, provider_order, summarize
from .feature_picker import FeaturePicker
from .param_form import ParamForm


class ParamsPanel(QWidget):
    """Parametry jedné feature nebo providera, s návratem na výchozí."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = ""
        self._form: ParamForm | None = None
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(4)
        head = QHBoxLayout()
        self.title = QLabel("Parametry")
        self.title.setObjectName("card_title")
        self.reset_btn = QPushButton("Výchozí vše")
        self.reset_btn.setToolTip("Vrátit všechny parametry na hodnoty z knihovny")
        self.reset_btn.setEnabled(False)
        self.reset_btn.clicked.connect(self.reset)
        head.addWidget(self.title, 1)
        head.addWidget(self.reset_btn)
        layout.addLayout(head)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.hint = QLabel("Klikni na feature nebo provider v tabulce.")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        self.scroll.setWidget(self.hint)
        layout.addWidget(self.scroll, 1)

    def show_params(self, name: str, form: ParamForm | None, error: str = "") -> None:
        self._name = name
        if self._form is not None:
            self._form.changed.disconnect(self._refresh_head)
        self._form = form
        if form is not None:
            self.scroll.takeWidget()
            self.scroll.setWidget(form)
            form.show()
            form.changed.connect(self._refresh_head)
            self._refresh_head()
        else:
            self.title.setText(name or "Parametry")
            self.subtitle.setText(name if name else "")
            label = QLabel(error or "Klikni na feature nebo provider v tabulce.")
            label.setObjectName("muted")
            label.setWordWrap(True)
            self.scroll.takeWidget()
            self.scroll.setWidget(label)
            self.reset_btn.setEnabled(False)

    def _refresh_head(self) -> None:
        if self._form is None:
            return
        n_changed = len(self._form.overrides())
        n_all = len(self._form._params)
        label = contract.PROVIDER_LABELS.get(self._name)
        self.title.setText(label or self._name)
        parts = [self._name] if label else []
        if n_all == 0:
            parts.append("bez parametrů")
        elif n_changed:
            parts.append(f"{n_changed} z {n_all} změněno")
        else:
            parts.append(f"{n_all} parametrů, vše výchozí")
        self.subtitle.setText(" · ".join(parts))
        self.reset_btn.setEnabled(n_changed > 0)

    def reset(self) -> None:
        if self._form is not None:
            self._form.reset()


class ProtocolEditor(QWidget):
    changed = Signal()  # výběr nebo parametry se změnily

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self._proto: Protocol | None = None
        self._param_forms: dict[str, ParamForm] = {}
        self._overrides: dict[str, dict] = {}
        self._doctor: dict[str, Any] | None = None
        self._seconds_per_file: Callable[[str], float | None] = lambda _slug: None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter)
        self.picker = FeaturePicker()
        self.picker.selection_changed.connect(self._selection_changed)
        self.picker.current_changed.connect(self._show_params)
        self.splitter.addWidget(self.picker)
        self.params = ParamsPanel()
        self.splitter.addWidget(self.params)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([760, 340])

    # --- nastavení zvenku -----------------------------------------------------

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self._param_forms.clear()
        self._rebuild()
        providers: list[str] = []
        if library is not None:
            try:
                providers = [p.name for p in library.providers()]
            except LibraryError:
                providers = []
        self.picker.set_providers(sorted(providers, key=provider_order))

    def set_doctor(self, report: dict[str, Any] | None) -> None:
        """Stav providerů z `doctor --json`, kvůli varování o chybějících modelech."""
        self._doctor = report
        self._update_summary()

    def set_stats_lookup(self, lookup: Callable[[str], float | None]) -> None:
        """Doba na nahrávku z minulého běhu protokolu podle jeho slugu."""
        self._seconds_per_file = lookup
        self._update_summary()

    def refresh_summary(self) -> None:
        self._update_summary()

    def set_protocol(self, proto: Protocol | None) -> None:
        """Načte výběr a parametry protokolu; dosavadní úpravy zahodí."""
        self._proto = proto
        self._overrides = {k: dict(v) for k, v in (proto.config if proto else {}).items()}
        self._param_forms.clear()
        self.params.show_params("", None)
        self._rebuild()
        self.picker.set_overridden(self._overridden_names())

    def _overridden_names(self) -> set[str]:
        return {name for name, values in self._overrides.items() if values}

    def protocol(self) -> Protocol | None:
        return self._proto

    def has_catalog(self) -> bool:
        return self.picker.count() > 0

    def selected(self) -> list[str]:
        return self.picker.selected()

    def overrides(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self._overrides.items() if v}

    def result(self) -> Protocol | None:
        """Protokol tak, jak ho uživatel upravil: výběr feature a parametry."""
        base = self._proto
        if base is None:
            return None
        proto = Protocol(
            name=base.name,
            task=base.task,
            description=base.description,
            features=list(base.features),
            domain=base.domain,
            vad=base.vad,
            config=self.overrides(),
        )
        if self.has_catalog():
            proto.features = self.picker.selected()
            proto.domain = None
        return proto

    # --- vnitřek ------------------------------------------------------------------

    def _catalog(self) -> list[FeatureInfo]:
        if self._library is None or self._proto is None:
            return []
        try:
            return self._library.features(self._proto.task)
        except LibraryError as exc:
            self.picker.set_warning(f"Seznam feature nejde načíst: {exc}")
            return []

    def _rebuild(self) -> None:
        proto = self._proto
        catalog = self._catalog()
        if proto is None or not catalog:
            self.picker.set_features([], set())
            return
        self.picker.set_features(catalog, set(proto.select([f.name for f in catalog])))
        self._update_summary()

    def _selection_changed(self) -> None:
        self._update_summary()
        self.changed.emit()

    def _update_summary(self) -> None:
        proto = self._proto
        catalog = self._catalog()
        if proto is None or not catalog:
            return
        providers = None
        if self._library is not None:
            try:
                providers = self._library.providers()
            except LibraryError:
                providers = None
        summary = summarize(self.picker.selected(), catalog, providers)
        hint = summary.cost_hint()
        seconds = self._seconds_per_file(proto.slug())
        if seconds:
            hint = f"naposledy {seconds:.0f} s na nahrávku"
        self.picker.set_summary(summary.providers, summary.columns, hint)
        self.picker.set_warning(self._missing_models(summary.providers))
        self.picker.set_overridden(self._overridden_names())

    def _missing_models(self, providers: list[str]) -> str:
        if not self._doctor:
            return ""
        state = self._doctor.get("providers", {})
        missing = [
            contract.PROVIDER_LABELS.get(p, p)
            for p in providers
            if p in state and not state[p].get("ready")
        ]
        if not missing:
            return ""
        return (
            "Není připraveno: "
            + ", ".join(missing)
            + " (viz Prostředí). Dotčené sloupce zůstanou prázdné."
        )

    def show_params(self, name: str) -> None:
        self._show_params(name)

    def _show_params(self, name: str) -> None:
        if not name or self._library is None:
            self.params.show_params("", None)
            return
        form = self._param_forms.get(name)
        if form is None:
            try:
                params = self._library.params(name).params
            except LibraryError as exc:
                self.params.show_params(name, None, str(exc))
                return
            form = ParamForm(params)
            form.set_values(self._overrides.get(name, {}))
            # hodnota z protokolu shodná s výchozí není úprava
            self._overrides[name] = form.overrides()
            self.picker.set_overridden(self._overridden_names())
            form.changed.connect(lambda n=name, f=form: self._params_changed(n, f))
            self._param_forms[name] = form
        self.params.show_params(name, form)

    def _params_changed(self, name: str, form: ParamForm) -> None:
        self._overrides[name] = form.overrides()
        self.picker.set_overridden(self._overridden_names())
        self.changed.emit()


class ProtocolEditorDialog(QDialog):
    """Okno s editorem přes většinu obrazovky; Použít nebo Zrušit.

    Editor v něm žije trvale (drží načtené parametry), okno se jen ukazuje.
    Při Zrušit se editor vrátí na stav před otevřením.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Feature a parametry")
        self.setModal(True)
        self.editor = ProtocolEditor()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.heading = QLabel("")
        self.heading.setObjectName("headline")
        layout.addWidget(self.heading)
        layout.addWidget(self.editor, 1)
        self.buttons = QDialogButtonBox()
        self.apply_btn = self.buttons.addButton("Použít", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton("Zrušit", QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            size = screen.availableSize()
            self.resize(int(size.width() * 0.85), int(size.height() * 0.85))
        else:
            self.resize(1200, 760)

    def open_for(self, title: str) -> bool:
        """Ukáže okno; vrací True po Použít. Po Zrušit vrátí editor zpět."""
        snapshot = self.editor.result()
        self.heading.setText(title)
        if self.exec() == QDialog.DialogCode.Accepted:
            return True
        if snapshot is not None:
            self.editor.set_protocol(snapshot)
        return False
