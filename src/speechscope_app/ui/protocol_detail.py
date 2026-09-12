"""Okno „Co protokol počítá“: feature a sloupce protokolu s popisem, jen ke čtení.

Klinik v základním režimu jinak nevidí, co se za protokolem skrývá.
Dvojklik na kartu (Analýza, Protokoly) nebo odkaz na kartě ukáže strom
skupina → feature → sloupec s popisem z `list --json`, potřebné modely
se stavem z `doctor` a parametry, které protokol nastavuje.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend.library import FeatureInfo, FeatureParams
from ..backend.protocol import Protocol, summarize
from ..i18n import tr
from . import theme
from .recording_detail import column_index, filter_tree


class ProtocolDetailDialog(QDialog):
    def __init__(
        self,
        proto: Protocol,
        catalog: list[FeatureInfo],
        providers: list[FeatureParams] | None = None,
        doctor: dict[str, Any] | None = None,
        hint: str = "",
        parent: QWidget | None = None,
        language: str = "",
    ) -> None:
        """`language` je jazyk nahrávek z lišty na Analýze; při běhu přebije
        `transcript.language` i `nlp.language` z protokolu, tak se ukáže on."""
        super().__init__(parent)
        self._language = language
        self.setWindowTitle(tr("Co počítá {name}").format(name=proto.display_name))
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel(proto.display_name)
        title.setObjectName("headline")
        layout.addWidget(title)
        if proto.display_description:
            desc = QLabel(proto.display_description)
            desc.setObjectName("muted")
            desc.setWordWrap(True)
            layout.addWidget(desc)

        pool = [f for f in catalog if proto.task in f.tasks]
        chosen = proto.select([f.name for f in pool])
        self.summary = summarize(chosen, pool, providers)
        info = QHBoxLayout()
        info.setSpacing(6)
        task = QLabel(
            tr("Úloha: {task}").format(task=contract.TASK_LABELS.get(proto.task, proto.task))
        )
        info.addWidget(task)
        info.addWidget(QLabel("·"))
        state = (doctor or {}).get("providers", {})
        self.provider_pills: list[QLabel] = []
        if self.summary.providers:
            for name in self.summary.providers:
                pill = QLabel(contract.PROVIDER_SHORT.get(name, name))
                pill.setObjectName("pill")
                ready = state.get(name, {}).get("ready") if name in state else None
                theme.set_role(
                    pill, "ok" if ready else ("missing" if ready is False else "neutral")
                )
                pill.setToolTip(
                    contract.PROVIDER_LABELS.get(name, name)
                    + (
                        tr(" · připraveno")
                        if ready
                        else (tr(" · není připraveno") if ready is False else "")
                    )
                )
                info.addWidget(pill)
                self.provider_pills.append(pill)
        else:
            pill = QLabel(tr("bez modelů"))
            pill.setObjectName("pill")
            theme.set_role(pill, "ok")
            info.addWidget(pill)
        info.addWidget(QLabel("·"))
        self.counts = QLabel(
            tr("{n} feature, {columns} sloupců").format(n=len(chosen), columns=self.summary.columns)
        )
        info.addWidget(self.counts)
        if hint:
            info.addWidget(QLabel("·"))
            hint_label = QLabel(hint)
            hint_label.setObjectName("muted")
            info.addWidget(hint_label)
        info.addStretch(1)
        layout.addLayout(info)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("hledat feature nebo sloupec"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search)
        layout.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels([tr("feature / sloupec"), tr("popis")])
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree, 1)
        self._fill(proto, pool, chosen)
        # skupiny rozbalené, feature s více sloupci sbalené: seznam zůstane přehledný
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            group.setExpanded(group is not self.params_item)
            for f in range(group.childCount()):
                group.child(f).setExpanded(False)
        self.tree.resizeColumnToContents(0)

        if not catalog:
            note = QLabel(tr("Bez knihovny nejde seznam feature ukázat; nastavte ji v Prostředí."))
            note.setObjectName("muted")
            layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _search(self, text: str) -> None:
        """Při hledání se sloupce rozbalí, ať je nález vidět; bez textu zase sbalí."""
        filter_tree(self.tree, text)
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            for f in range(group.childCount()):
                group.child(f).setExpanded(bool(text.strip()))

    def _fill(self, proto: Protocol, pool: list[FeatureInfo], chosen: list[str]) -> None:
        index = column_index(pool)
        by_name = {f.name: f for f in pool}
        groups: dict[str, QTreeWidgetItem] = {}
        for name in chosen:
            feature = by_name.get(name)
            if feature is None:
                continue
            group = feature.group
            if group not in groups:
                groups[group] = QTreeWidgetItem([contract.GROUP_LABELS.get(group, group), group])
                groups[group].setForeground(1, QColor(theme.MUTED))
                self.tree.addTopLevelItem(groups[group])
            item = QTreeWidgetItem([name.removeprefix(group + "."), feature.description or ""])
            item.setToolTip(0, name)
            item.setToolTip(1, feature.description or "")
            groups[group].addChild(item)
            if feature.columns == [name]:
                continue  # jediný sloupec = feature sama (lingvistika): list bez podřádku
            for column in feature.columns:
                info = index.get(column)
                short = info.short if info else column
                desc = info.description if info else ""
                child = QTreeWidgetItem([short, desc])
                child.setToolTip(1, desc)
                item.addChild(child)
        self.params_item: QTreeWidgetItem | None = None
        config = {k: dict(v) for k, v in proto.config.items()}
        overridden: set[str] = set()
        if self._language:
            for provider in ("transcript", "nlp"):
                if provider in self.summary.providers:
                    config.setdefault(provider, {})["language"] = self._language
                    overridden.add(f"{provider}.language")
        items = [f"{n}.{k}={v}" for n, params in config.items() for k, v in params.items()]
        if items:
            self.params_item = QTreeWidgetItem(
                [tr("Parametry nastavené protokolem ({n})").format(n=len(items)), ""]
            )
            for entry in items:
                name, _, value = entry.partition("=")
                shown = value
                if name.endswith(".language"):
                    label = contract.LANGUAGE_LABELS.get(value)
                    shown = f"{value} ({label})" if label else value
                    if name in overridden:
                        shown += tr(" · jazyk nahrávek z lišty na Analýze")
                child = QTreeWidgetItem([name, shown])
                child.setToolTip(
                    1,
                    tr("Jazyk nahrávek (Whisper, Stanza), ne jazyk aplikace.")
                    if name.endswith(".language")
                    else tr("Ostatní parametry mají výchozí hodnoty z knihovny."),
                )
                self.params_item.addChild(child)
            self.tree.addTopLevelItem(self.params_item)
