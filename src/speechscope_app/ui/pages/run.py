"""Stránka Běh: průběh dávky z událostí `--progress-json`."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.runner import Runner


class RunPage(QWidget):
    finished = Signal(object, int, bool)  # BatchState, návratový kód, zrušeno

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner = Runner(self)
        self.runner.event.connect(self._on_event)
        self.runner.log.connect(self._on_log)
        self.runner.contract_error.connect(self._on_contract_error)
        self.runner.finished.connect(self._on_finished)
        self._started_at = 0.0

        layout = QVBoxLayout(self)
        self.headline = QLabel("Žádný běh.")
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)
        self.bar = QProgressBar()
        layout.addWidget(self.bar)

        self.files = QTableWidget()
        self.files.setColumnCount(3)
        self.files.setHorizontalHeaderLabels(["nahrávka", "stav", "poznámka"])
        self.files.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.files, 2)

        layout.addWidget(QLabel("Log knihovny"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        layout.addWidget(self.log, 1)

        bottom = QHBoxLayout()
        self.elapsed = QLabel("")
        self.cancel_btn = QPushButton("Zrušit")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.runner.cancel)
        bottom.addWidget(self.elapsed, 1)
        bottom.addWidget(self.cancel_btn)
        layout.addLayout(bottom)

    def start(self, argv: list[str], *, title: str) -> None:
        self.files.setRowCount(0)
        self.log.clear()
        self.bar.setRange(0, 0)
        self.headline.setText(title)
        self.elapsed.setText("")
        self._started_at = time.monotonic()
        self.log.appendPlainText("$ " + " ".join(argv))
        self.cancel_btn.setEnabled(True)
        self.runner.start(argv)

    def _on_event(self, event: contract.Event) -> None:
        state = self.runner.state
        match event:
            case contract.StartEvent():
                self.bar.setRange(0, max(1, event.total))
                self.bar.setValue(0)
                providers = ", ".join(contract.PROVIDER_LABELS.get(p, p) for p in event.providers)
                self.headline.setText(
                    f"{event.total} nahrávek, {len(event.features)} feature"
                    + (f", spouští se: {providers}" if providers else "")
                )
            case contract.FileEvent():
                row = self.files.rowCount()
                self.files.insertRow(row)
                name = event.path.replace("\\", "/").rsplit("/", 1)[-1]
                for c, text in enumerate([name, "ok" if event.ok else "chyba", event.msg or ""]):
                    item = QTableWidgetItem(text)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.files.setItem(row, c, item)
                self.files.scrollToBottom()
                self.bar.setValue(state.processed)
                self._update_elapsed()
            case contract.DoneEvent():
                self.bar.setValue(self.bar.maximum())
            case contract.SavedEvent():
                self.headline.setText(f"Hotovo, výstup: {event.out}")

    def _update_elapsed(self) -> None:
        state = self.runner.state
        spent = time.monotonic() - self._started_at
        text = f"uplynulo {spent:.0f} s"
        if state.processed and state.total:
            remaining = spent / state.processed * (state.total - state.processed)
            text += f", zbývá asi {remaining:.0f} s"
        self.elapsed.setText(text)

    def _on_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _on_contract_error(self, msg: str) -> None:
        self.log.appendPlainText(f"!! {msg}")

    def _on_finished(self, code: int, cancelled: bool) -> None:
        self.cancel_btn.setEnabled(False)
        self.bar.setRange(0, max(1, self.bar.maximum()))
        if cancelled:
            self.headline.setText("Zrušeno uživatelem.")
        elif code != 0:
            self.headline.setText(f"Knihovna skončila chybou (kód {code}), viz log.")
        self._update_elapsed()
        self.finished.emit(self.runner.state, code, cancelled)
