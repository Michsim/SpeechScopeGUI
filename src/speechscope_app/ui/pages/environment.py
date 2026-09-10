"""Stránka Prostředí: výstup `doctor --json` v tabulkách."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.library import Library, LibraryError


def _fill(table: QTableWidget, rows: list[list[str]], headers: list[str]) -> None:
    table.clear()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, c, item)
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)


class EnvironmentPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Library | None = None
        self.report: dict[str, Any] | None = None

        layout = QVBoxLayout(self)
        head = QHBoxLayout()
        self.summary = QLabel("Prostředí zatím nebylo zkontrolováno.")
        self.summary.setWordWrap(True)
        self.check_btn = QPushButton("Zkontrolovat")
        self.check_btn.clicked.connect(self.refresh)
        head.addWidget(self.summary, 1)
        head.addWidget(self.check_btn)
        layout.addLayout(head)

        layout.addWidget(QLabel("<b>Části knihovny</b>"))
        self.providers = QTableWidget()
        layout.addWidget(self.providers, 2)
        layout.addWidget(QLabel("<b>Modely</b>"))
        self.models = QTableWidget()
        layout.addWidget(self.models, 3)
        layout.addWidget(QLabel("<b>Grafická karta</b>"))
        self.gpu = QLabel("")
        self.gpu.setWordWrap(True)
        layout.addWidget(self.gpu)

    def set_library(self, library: Library | None) -> None:
        self._library = library
        self.check_btn.setEnabled(library is not None)
        if library is None:
            self.summary.setText("Knihovna SpeechScope není nastavená. Otevři Nastavení.")

    def refresh(self) -> None:
        if self._library is None:
            return
        try:
            version = self._library.version()
            report = self._library.doctor()
        except LibraryError as exc:
            self.summary.setText(f"Knihovnu se nepodařilo spustit: {exc}")
            self.report = None
            return
        self.report = report
        self._show(version, report)

    def _show(self, version: str, report: dict[str, Any]) -> None:
        ready = report.get("all_ready", False)
        version_note = ""
        if version != contract.KNOWN_LIBRARY_VERSION:
            version_note = (
                f" Knihovna má verzi {version}, aplikace byla ověřená s "
                f"{contract.KNOWN_LIBRARY_VERSION}."
            )
        self.summary.setText(
            ("Všechno je připravené." if ready else "Něco chybí, viz tabulky.")
            + f" Modely: {report.get('models_dir')}."
            + version_note
        )
        _fill(
            self.providers,
            [
                [
                    contract.PROVIDER_LABELS.get(name, name),
                    "připraven" if p.get("ready") else "chybí",
                    "" if p.get("ready") else str(p.get("needs", "")),
                ]
                for name, p in report.get("providers", {}).items()
            ],
            ["část", "stav", "co chybí"],
        )
        _fill(
            self.models,
            [
                [m.get("name", key), "je" if m.get("present") else "chybí", str(m.get("path", ""))]
                for key, m in report.get("models", {}).items()
            ],
            ["model", "stav", "cesta"],
        )
        gpu = report.get("gpu", {})
        providers = ", ".join(gpu.get("onnxruntime_providers", []) or [])
        self.gpu.setText(
            f"Whisper (ctranslate2 CUDA): {gpu.get('ctranslate2_cuda')}, "
            f"torch: {gpu.get('torch_build')}, onnxruntime: {providers or '-'}"
        )
