"""Výběr feature ve dvou sloupcích: skupiny vlevo, karty feature uprostřed.

Skupina je doména a druhá část jména (`acoustic.timing.*` → „Akustika ·
časování“). Vlevo je seznam skupin s počtem vybraných a pod nimi
providery (klik ukáže jejich parametry v panelu vpravo, který drží
`ProtocolEditor`). Uprostřed jsou karty feature vybrané skupiny:
zaškrtávátko, jméno, popis, počet sloupců a co feature potřebuje.
Hledání prochází všechny skupiny najednou.

Widget nezná knihovnu ani protokol; dostane seznam `FeatureInfo` a výběr,
vrací vybraná jména. `current_changed` nese jméno feature nebo providera
pro panel parametrů.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.library import FeatureInfo
from .. import theme

ROLE_KIND = Qt.ItemDataRole.UserRole  # "group" | "provider"
ROLE_KEY = Qt.ItemDataRole.UserRole + 1  # klíč skupiny nebo jméno providera


def group_label(group: str) -> str:
    """`acoustic.timing` → „Akustika · časování“; neznámé skupiny surově."""
    if group in contract.GROUP_LABELS:
        return contract.GROUP_LABELS[group]
    domain, _, rest = group.partition(".")
    return f"{contract.DOMAIN_LABELS.get(domain, domain)} · {rest or '?'}"


def group_short(group: str) -> str:
    """Jen druhá část popisku: „časování“."""
    return group_label(group).split(" · ", 1)[-1]


def requires_label(requires: list[str]) -> str:
    return ", ".join(contract.PROVIDER_SHORT.get(r, r) for r in requires)


def feature_short(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def columns_label(n: int) -> str:
    if n == 1:
        return "1 sloupec"
    if n < 5:
        return f"{n} sloupce"
    return f"{n} sloupců"


class FeatureCard(QFrame):
    """Jedna feature: zaškrtávátko, jméno, popis, sloupce, providery."""

    toggled = Signal(str, bool)
    clicked = Signal(str)

    def __init__(self, info: FeatureInfo, checked: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("card")
        self.setProperty("role", "neutral")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        head = QHBoxLayout()
        self.check = QCheckBox(feature_short(info.name))
        font = self.check.font()
        font.setBold(True)
        self.check.setFont(font)
        self.check.setChecked(checked)
        self.check.toggled.connect(lambda on: self.toggled.emit(info.name, on))
        head.addWidget(self.check, 1)
        cols = QLabel(columns_label(len(info.columns) or len(info.outputs) or 1))
        cols.setObjectName("muted")
        cols.setToolTip("\n".join(f"{k}: {v}" for k, v in info.outputs.items()))
        head.addWidget(cols)
        layout.addLayout(head)
        desc = info.description or "; ".join(v for v in info.outputs.values() if v)
        if desc:
            text = QLabel(desc)
            text.setObjectName("muted")
            text.setWordWrap(True)
            layout.addWidget(text)
        foot = QHBoxLayout()
        req = QLabel(
            "potřebuje: " + requires_label(info.requires) if info.requires else "bez modelů"
        )
        color = theme.MUTED if info.requires else theme.OK
        req.setStyleSheet(f"color: {color}; font-size: 8.5pt;")
        foot.addWidget(req, 1)
        self.modified = QLabel("")
        self.modified.setStyleSheet(f"color: {theme.ACCENT}; font-size: 8.5pt;")
        foot.addWidget(self.modified)
        layout.addLayout(foot)

    def set_modified(self, modified: bool) -> None:
        self.modified.setText("● změněné parametry" if modified else "")

    def set_current(self, current: bool) -> None:
        theme.set_role(self, "selected" if current else "neutral")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self.info.name)
        super().mousePressEvent(event)


class FeaturePicker(QWidget):
    selection_changed = Signal()
    current_changed = Signal(str)  # jméno feature nebo providera, "" = nic

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._features: list[FeatureInfo] = []
        self._by_name: dict[str, FeatureInfo] = {}
        self._checked: dict[str, bool] = {}
        self._groups: list[str] = []
        self._providers: list[str] = []
        self._overridden: set[str] = set()
        self._current = ""
        self._cards: dict[str, FeatureCard] = {}
        self._extras: list[QWidget] = []  # texty místo karet (prázdno, provider)
        self._summary_args: tuple[list[str], int, str] = ([], 0, "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Hledat ve všech skupinách…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_changed)
        top.addWidget(self.search, 1)
        self.quick: dict[str, QPushButton] = {}
        for key, label, tip in (
            ("all", "Vše", "Vybrat všechny feature pro úlohu"),
            ("none", "Nic", "Zrušit výběr"),
            ("nomodels", "Jen bez modelů", "Jen feature, které nepotřebují žádný provider"),
            (
                "notranscript",
                "Bez přepisu",
                "Vynechat feature, které potřebují Whisper nebo Stanzu",
            ),
        ):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, k=key: self.quick_pick(k))
            top.addWidget(btn)
            self.quick[key] = btn
        layout.addLayout(top)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)

        self.groups = QListWidget()
        self.groups.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.groups.currentItemChanged.connect(self._group_changed)
        self.splitter.addWidget(self.groups)

        middle = QWidget()
        mid = QVBoxLayout(middle)
        mid.setContentsMargins(8, 0, 8, 0)
        mid.setSpacing(6)
        head = QHBoxLayout()
        self.group_check = QCheckBox("")
        self.group_check.setToolTip("Vybrat nebo zrušit všechny zobrazené")
        self.group_check.clicked.connect(self._group_check_clicked)
        self.group_title = QLabel("")
        self.group_title.setObjectName("card_title")
        self.group_count = QLabel("")
        self.group_count.setObjectName("muted")
        head.addWidget(self.group_check)
        head.addWidget(self.group_title, 1)
        head.addWidget(self.group_count)
        mid.addLayout(head)
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 4, 0)
        self.cards_layout.setSpacing(6)
        self.cards_layout.addStretch(1)
        self.cards_scroll.setWidget(self.cards_host)
        mid.addWidget(self.cards_scroll, 1)
        self.splitter.addWidget(middle)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([210, 520])

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.linkActivated.connect(self._link_activated)
        layout.addWidget(self.summary)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color: {theme.WARN};")
        self.warning.setVisible(False)
        layout.addWidget(self.warning)

    # --- naplnění ---------------------------------------------------------------

    def set_features(self, features: list[FeatureInfo], selected: set[str]) -> None:
        self._features = sorted(features, key=lambda f: f.name)
        self._by_name = {f.name: f for f in self._features}
        self._checked = {f.name: f.name in selected for f in self._features}
        self._groups = []
        for f in self._features:
            if f.group not in self._groups:
                self._groups.append(f.group)
        self._current = ""
        self.groups.blockSignals(True)
        self.groups.clear()
        self.groups.blockSignals(False)
        self._rebuild_groups()
        self._rebuild_cards()

    def set_providers(self, providers: list[str]) -> None:
        """Providery do levého seznamu, kvůli přístupu k jejich parametrům."""
        if providers == self._providers:
            return
        self._providers = list(providers)
        self._rebuild_groups()

    def count(self) -> int:
        return len(self._features)

    def set_overridden(self, names: set[str]) -> None:
        """Feature a providery se změněnými parametry: tečka v seznamu i na kartě."""
        if names == self._overridden:
            return
        self._overridden = set(names)
        self._rebuild_groups()
        for name, card in self._cards.items():
            card.set_modified(name in names)
        self.set_summary(*self._summary_args)

    # --- levý sloupec ---------------------------------------------------------------

    def current_group(self) -> str:
        item = self.groups.currentItem()
        if item is None or item.data(ROLE_KIND) != "group":
            return ""
        return str(item.data(ROLE_KEY))

    def current_provider(self) -> str:
        item = self.groups.currentItem()
        if item is None or item.data(ROLE_KIND) != "provider":
            return ""
        return str(item.data(ROLE_KEY))

    def _rebuild_groups(self) -> None:
        wanted_group = self.current_group()
        wanted_provider = self.current_provider()
        if not wanted_group and not wanted_provider and self._groups:
            wanted_group = self._groups[0]
        self.groups.blockSignals(True)
        self.groups.clear()
        chosen: QListWidgetItem | None = None
        domain_seen: set[str] = set()
        for group in self._groups:
            domain = group.split(".", 1)[0]
            if domain not in domain_seen:
                domain_seen.add(domain)
                self._add_header(contract.DOMAIN_LABELS.get(domain, domain))
            members = [f.name for f in self._features if f.group == group]
            n = sum(self._checked[m] for m in members)
            mark = " ●" if any(m in self._overridden for m in members) else ""
            item = QListWidgetItem(f"    {group_short(group)}{mark}    {n}/{len(members)}")
            item.setData(ROLE_KIND, "group")
            item.setData(ROLE_KEY, group)
            item.setToolTip(f"{group_label(group)}: {n} z {len(members)} vybráno")
            if n == 0:
                item.setForeground(QColor(theme.MUTED))
            self.groups.addItem(item)
            if group == wanted_group:
                chosen = item
        if self._providers:
            self._add_header("Providery")
            for provider in self._providers:
                mark = " ●" if provider in self._overridden else ""
                label = contract.PROVIDER_SHORT.get(provider, provider)
                item = QListWidgetItem(f"    {label}{mark}")
                item.setData(ROLE_KIND, "provider")
                item.setData(ROLE_KEY, provider)
                item.setToolTip(contract.PROVIDER_LABELS.get(provider, provider))
                self.groups.addItem(item)
                if provider == wanted_provider:
                    chosen = item
        if chosen is not None:
            self.groups.setCurrentItem(chosen)
        self.groups.blockSignals(False)

    def _add_header(self, text: str) -> None:
        head = QListWidgetItem(text)
        head.setFlags(Qt.ItemFlag.NoItemFlags)
        font = head.font()
        font.setBold(True)
        head.setFont(font)
        self.groups.addItem(head)

    def _group_changed(self, item: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if item is None:
            return
        if item.data(ROLE_KIND) == "provider":
            provider = str(item.data(ROLE_KEY))
            self._show_provider(provider)
            self._set_current(provider)
            return
        if self.search.text().strip():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        self._rebuild_cards()

    # --- prostřední sloupec -------------------------------------------------------------

    def _clear_cards(self) -> None:
        for w in [*self._cards.values(), *self._extras]:
            self.cards_layout.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        self._cards.clear()
        self._extras.clear()

    def _add_middle(self, widget: QWidget) -> None:
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, widget)

    def visible_features(self) -> list[FeatureInfo]:
        """Feature v prostředním sloupci: hledání přes všechno, jinak skupina."""
        needle = self.search.text().strip().lower()
        if needle:
            out = []
            for f in self._features:
                outputs = " ".join(f"{k} {v}" for k, v in f.outputs.items())
                hay = f"{f.name} {f.description or ''} {outputs}".lower()
                if needle in hay:
                    out.append(f)
            return out
        group = self.current_group()
        return [f for f in self._features if f.group == group]

    def _rebuild_cards(self) -> None:
        self._clear_cards()
        features = self.visible_features()
        searching = bool(self.search.text().strip())
        if searching:
            self.group_title.setText(f"Hledání „{self.search.text().strip()}“")
        else:
            group = self.current_group()
            self.group_title.setText(group_label(group) if group else "")
        n = sum(self._checked[f.name] for f in features)
        self.group_count.setText(f"{n} z {len(features)} vybráno" if features else "")
        self.group_check.setVisible(bool(features))
        self.group_check.blockSignals(True)
        self.group_check.setChecked(bool(features) and n == len(features))
        self.group_check.blockSignals(False)
        for f in features:
            card = FeatureCard(f, self._checked[f.name])
            card.set_modified(f.name in self._overridden)
            card.toggled.connect(self._card_toggled)
            card.clicked.connect(self._set_current)
            card.set_current(f.name == self._current)
            self._add_middle(card)
            self._cards[f.name] = card
        if not features:
            empty = QLabel("Nic nenalezeno." if searching else "Skupina je prázdná.")
            empty.setObjectName("muted")
            self._add_middle(empty)
            self._extras.append(empty)

    def _show_provider(self, provider: str) -> None:
        """Místo karet feature: kdo provider potřebuje."""
        self._clear_cards()
        self.group_title.setText(contract.PROVIDER_LABELS.get(provider, provider))
        users = [f for f in self._features if provider in f.requires]
        chosen = [f for f in users if self._checked[f.name]]
        self.group_count.setText(f"potřebuje ho {len(chosen)} z {len(users)} vybraných")
        self.group_check.setVisible(False)
        names = ", ".join(feature_short(f.name) for f in users) or "žádná"
        text = QLabel(f"Parametry providera jsou vpravo. Feature, které ho potřebují: {names}.")
        text.setObjectName("muted")
        text.setWordWrap(True)
        self._add_middle(text)
        self._extras.append(text)

    def _card_toggled(self, name: str, on: bool) -> None:
        self._checked[name] = on
        self._after_change()

    def _group_check_clicked(self, on: bool) -> None:
        for f in self.visible_features():
            self._checked[f.name] = on
        self._after_change()

    def _after_change(self) -> None:
        self._rebuild_groups()
        if self.current_provider():
            self._show_provider(self.current_provider())
        else:
            self._rebuild_cards()
        self.selection_changed.emit()

    def _search_changed(self, _text: str) -> None:
        if self.current_provider() and self._groups:
            self.groups.blockSignals(True)
            self.groups.setCurrentItem(None)
            self.groups.blockSignals(False)
        self._rebuild_cards()

    def _set_current(self, name: str) -> None:
        self._current = name
        for n, card in self._cards.items():
            card.set_current(n == name)
        self.current_changed.emit(name)

    # --- výběr --------------------------------------------------------------------

    def selected(self) -> list[str]:
        return [f.name for f in self._features if self._checked[f.name]]

    def set_checked(self, name: str, checked: bool) -> None:
        self._checked[name] = checked
        self._after_change()

    def set_selected(self, names: set[str]) -> None:
        for f in self._features:
            self._checked[f.name] = f.name in names
        self._after_change()

    def quick_pick(self, key: str) -> None:
        match key:
            case "all":
                names = {f.name for f in self._features}
            case "none":
                names = set()
            case "nomodels":
                names = {f.name for f in self._features if not f.requires}
            case "notranscript":
                names = {
                    f.name for f in self._features if not ({"transcript", "nlp"} & set(f.requires))
                }
            case _:
                return
        self.set_selected(names)

    def show_group_of(self, name: str) -> None:
        """Přepne levý sloupec na skupinu feature, nebo na provider."""
        for row in range(self.groups.count()):
            item = self.groups.item(row)
            kind, key = item.data(ROLE_KIND), item.data(ROLE_KEY)
            if (kind == "provider" and key == name) or (
                kind == "group" and name in self._by_name and self._by_name[name].group == key
            ):
                self.groups.setCurrentItem(item)
                return

    def _link_activated(self, link: str) -> None:
        if link.startswith("provider:"):
            self.show_group_of(link.split(":", 1)[1])

    # --- souhrn ---------------------------------------------------------------------

    def set_summary(self, providers: list[str], columns: int, hint: str = "") -> None:
        """Řádek pod sloupci: providery jako odkazy na parametry, počty, cena."""
        self._summary_args = (list(providers), columns, hint)
        n = len(self.selected())
        if providers:
            links = ", ".join(
                f'<a href="provider:{p}">{contract.PROVIDER_LABELS.get(p, p)}</a>'
                + (" <b>(upraveno)</b>" if p in self._overridden else "")
                for p in providers
            )
            runs = f"Spustí se: {links}"
        else:
            runs = "Bez modelů"
        parts = [runs, f"{n} feature, {columns} sloupců"]
        if hint:
            parts.append(hint)
        self.summary.setText(" · ".join(parts))

    def set_warning(self, text: str) -> None:
        self.warning.setText(text)
        self.warning.setVisible(bool(text))
