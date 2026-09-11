"""Stránka Běh: průběh dávky z událostí `--progress-json`.

Tabulka má řádek pro každou nahrávku už od startu a sloupec pro každý
provider, který se v dávce spouští (ze `start.providers`). Události
`begin` a `stage` (smlouva 2) plní buňky průběžně; se starou knihovnou
(smlouva 1) se plní jen sloupec s výsledkem.

Odhad zbývajícího času: dokud není hotová žádná nahrávka, bere se doba
na nahrávku z minulého běhu téhož protokolu (`expected_seconds`). Potom
medián hotových nahrávek; od tří hotových bez první, protože v ní je
načtení modelů.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.runner import Runner
from .. import theme

TICK_MS = 1000

PROVIDER_SHORT = contract.PROVIDER_SHORT

STAGE_COLORS: dict[str, str] = {
    "done": theme.OK,
    "cached": theme.NEUTRAL,
    "error": theme.MISSING,
    "running": theme.ACCENT,
}


def format_seconds(seconds: float) -> str:
    """41 s, 6 min 20 s, 1 h 05 min."""
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def estimate_per_file(durations: list[float]) -> float | None:
    """Medián dob na nahrávku; od tří hotových bez první (načtení modelů)."""
    if not durations:
        return None
    sample = durations[1:] if len(durations) >= 3 else durations
    return statistics.median(sample)


class RunPage(QWidget):
    finished = Signal(object, int, bool)  # BatchState, návratový kód, zrušeno
    progress_changed = Signal(str)  # krátký text do nabídky, "" když nic neběží

    COL_FILE = 0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner = Runner(self)
        self.runner.event.connect(self._on_event)
        self.runner.log.connect(self._on_log)
        self.runner.contract_error.connect(self._on_contract_error)
        self.runner.finished.connect(self._on_finished)

        self._started_at = 0.0
        self._finished_at: float | None = None
        self._file_started_at: float | None = None
        self._stage_started_at: float | None = None
        self._durations: list[float] = []
        self._expected: float | None = None
        self._providers: list[str] = []
        self._paths: list[Path] = []
        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._on_tick)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(8)

        self.headline = QLabel("Žádný běh.")
        self.headline.setObjectName("headline")
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.bar = QProgressBar()
        layout.addWidget(self.bar)
        self.timing = QLabel("")
        layout.addWidget(self.timing)
        self.current = QLabel("")
        self.current.setWordWrap(True)
        layout.addWidget(self.current)

        self.files = QTableWidget()
        self.files.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.files.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.files.verticalHeader().setVisible(False)
        self.files.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._set_columns([])
        layout.addWidget(self.files, 2)

        self.log_toggle = QToolButton()
        self.log_toggle.setText("Log knihovny")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.log_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.log_toggle.setAutoRaise(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        layout.addWidget(self.log_toggle)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setVisible(False)
        layout.addWidget(self.log, 1)

        bottom = QHBoxLayout()
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("muted")
        self.cancel_btn = QPushButton("Zrušit")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.confirm_cancel)
        bottom.addWidget(self.elapsed, 1)
        bottom.addWidget(self.cancel_btn)
        layout.addLayout(bottom)

    # --- start -------------------------------------------------------------------

    def start(
        self,
        argv: list[str],
        *,
        title: str,
        log_file: Path | None = None,
        inputs: list[Path] | None = None,
        expected_seconds: float | None = None,
    ) -> None:
        """Spustí knihovnu. `inputs` předplní tabulku, `expected_seconds` je
        doba na nahrávku z minulého běhu téhož protokolu pro první odhad."""
        self._paths = list(inputs or [])
        self._expected = expected_seconds
        self._durations = []
        self._providers = []
        self._file_started_at = None
        self._stage_started_at = None
        self._finished_at = None
        self._started_at = time.monotonic()

        self.headline.setText(title)
        self.summary.setText(f"{len(self._paths)} nahrávek" if self._paths else "")
        self.bar.setRange(0, 0)
        self.timing.setText(
            f"podle minulého běhu asi {format_seconds(expected_seconds * len(self._paths))}"
            if expected_seconds and self._paths
            else ""
        )
        self.current.setText("Spouštím knihovnu…")
        self.elapsed.setText("")
        self.log.clear()
        self.log_toggle.setChecked(False)
        self.log.appendPlainText("$ " + " ".join(argv))
        self._set_columns([])
        self._fill_rows([p.name for p in self._paths])
        self.cancel_btn.setEnabled(True)
        self._tick.start()
        self.runner.start(argv, log_file=log_file)
        self.progress_changed.emit(f"0/{len(self._paths)}" if self._paths else "…")

    # --- tabulka -----------------------------------------------------------------

    def _set_columns(self, providers: list[str]) -> None:
        self._providers = list(providers)
        labels = [
            "nahrávka",
            *(PROVIDER_SHORT.get(p, p) for p in providers),
            "výsledek",
            "poznámka",
        ]
        self.files.setColumnCount(len(labels))
        self.files.setHorizontalHeaderLabels(labels)
        header = self.files.horizontalHeader()
        for col in range(len(labels) - 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)

    @property
    def col_status(self) -> int:
        return 1 + len(self._providers)

    @property
    def col_note(self) -> int:
        return 2 + len(self._providers)

    def _fill_rows(self, names: list[str]) -> None:
        self.files.setRowCount(len(names))
        for row, name in enumerate(names):
            self._set_cell(row, self.COL_FILE, name)
            self._set_cell(row, self.col_status, "čeká", color=theme.MUTED)
            self._set_cell(row, self.col_note, "")
        self.files.resizeColumnsToContents()

    def _ensure_row(self, index: int, path: str) -> None:
        """Knihovna může najít jiné soubory než GUI; řádek se doplní."""
        while self.files.rowCount() <= index:
            row = self.files.rowCount()
            self.files.insertRow(row)
            self._set_cell(row, self.col_status, "čeká", color=theme.MUTED)
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        item = self.files.item(index, self.COL_FILE)
        if item is None or item.text() != name:
            self._set_cell(index, self.COL_FILE, name, tooltip=path)

    def _set_cell(
        self,
        row: int,
        col: int,
        text: str,
        *,
        color: str | None = None,
        tooltip: str | None = None,
    ) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if color:
            item.setForeground(QColor(color))
        if tooltip:
            item.setToolTip(tooltip)
        self.files.setItem(row, col, item)
        return item

    def _stage_cell(self, stage: contract.StageEvent, elapsed: float | None = None) -> None:
        if stage.provider not in self._providers:
            return
        col = 1 + self._providers.index(stage.provider)
        color = STAGE_COLORS.get(stage.status, theme.TEXT)
        match stage.status:
            case "running":
                text = "… " + (format_seconds(elapsed) if elapsed is not None else "")
            case "done":
                text = "✓ " + (format_seconds(stage.seconds) if stage.seconds is not None else "")
            case "cached":
                text = "z cache"
            case _:
                text = "✗"
        self._set_cell(stage.index, col, text.strip(), color=color, tooltip=stage.msg)

    # --- události ------------------------------------------------------------------

    def _on_event(self, event: contract.Event) -> None:
        state = self.runner.state
        now = time.monotonic()
        match event:
            case contract.StartEvent():
                self.bar.setRange(0, max(1, event.total))
                self.bar.setValue(0)
                self._set_columns(event.providers)
                names = [p.name for p in self._paths]
                if event.total != len(names):
                    names = [""] * event.total
                self._fill_rows(names)
                providers = ", ".join(contract.PROVIDER_LABELS.get(p, p) for p in event.providers)
                self.summary.setText(
                    f"{event.total} nahrávek, {len(event.features)} feature"
                    + (f" · spouští se: {providers}" if providers else " · bez modelů")
                )
                if not state.detailed:
                    self.current.setText("Knihovna hlásí jen dokončené nahrávky.")
            case contract.BeginEvent():
                self._file_started_at = now
                self._stage_started_at = None
                self._ensure_row(event.index, event.path)
                self._set_cell(event.index, self.col_status, "běží", color=theme.ACCENT)
                self.files.scrollToItem(self.files.item(event.index, self.COL_FILE))
                self._update_current()
            case contract.StageEvent():
                if event.running:
                    self._stage_started_at = now
                    self._stage_cell(event, 0.0)
                else:
                    self._stage_started_at = None
                    self._stage_cell(event)
                self._update_current()
            case contract.FileEvent():
                self._ensure_row(event.index, event.path)
                if self._file_started_at is not None:
                    self._durations.append(now - self._file_started_at)
                    self._file_started_at = None
                self._set_cell(
                    event.index,
                    self.col_status,
                    "ok" if event.ok else "chyba",
                    color=theme.OK if event.ok else theme.MISSING,
                )
                self._set_cell(event.index, self.col_note, event.msg or "", tooltip=event.msg)
                self.bar.setValue(state.processed)
                self.progress_changed.emit(f"{state.processed}/{state.total}")
                self._update_timing()
                self._update_current()
            case contract.DoneEvent():
                self.bar.setValue(self.bar.maximum())
            case contract.SavedEvent():
                pass

    def _update_current(self) -> None:
        state = self.runner.state
        if state.current is None:
            self.current.setText("")
            return
        name = state.current.path.replace("\\", "/").rsplit("/", 1)[-1]
        parts: list[str] = []
        for provider in self._providers:
            stage = state.stages.get(state.current.index, {}).get(provider)
            label = PROVIDER_SHORT.get(provider, provider)
            if stage is None:
                parts.append(label)
            elif stage.running:
                spent = time.monotonic() - (self._stage_started_at or time.monotonic())
                parts.append(f"{label} … {format_seconds(spent)}")
            elif stage.status == "cached":
                parts.append(f"{label} z cache")
            elif stage.status == "error":
                parts.append(f"{label} ✗")
            else:
                parts.append(f"{label} ✓ {format_seconds(stage.seconds or 0)}")
        text = f"Právě: {name} ({state.current.index + 1}/{state.total})"
        if parts:
            text += " · " + " · ".join(parts)
        self.current.setText(text)

    def _update_timing(self) -> None:
        state = self.runner.state
        end = self._finished_at if self._finished_at is not None else time.monotonic()
        spent = end - self._started_at
        self.elapsed.setText(f"uplynulo {format_seconds(spent)}")
        if self._finished_at is not None or not state.total:
            self.timing.setText("")
            return
        per_file = estimate_per_file(self._durations)
        source = "podle hotových nahrávek"
        if per_file is None:
            per_file = self._expected
            source = "podle minulého běhu"
        remaining_files = state.total - state.processed
        if per_file is None or remaining_files <= 0:
            self.timing.setText(f"{state.processed} z {state.total} hotovo")
            return
        remaining = per_file * remaining_files
        if self._file_started_at is not None:
            remaining -= min(per_file, time.monotonic() - self._file_started_at)
        self.timing.setText(
            f"{state.processed} z {state.total} hotovo · zbývá asi {format_seconds(remaining)} "
            f"({source}, {format_seconds(per_file)} na nahrávku)"
        )

    def _on_tick(self) -> None:
        self._update_timing()
        state = self.runner.state
        running = state.running_stage()
        if running is not None and self._stage_started_at is not None:
            self._stage_cell(running, time.monotonic() - self._stage_started_at)
        self._update_current()

    def seconds_per_file(self) -> float | None:
        """Střední doba na nahrávku z tohoto běhu; pro statistiku protokolu."""
        return estimate_per_file(self._durations)

    # --- log a konec ----------------------------------------------------------------

    def _toggle_log(self, shown: bool) -> None:
        self.log.setVisible(shown)
        self.log_toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)

    def _on_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _on_contract_error(self, msg: str) -> None:
        self.log.appendPlainText(f"!! {msg}")
        self.log_toggle.setChecked(True)

    def confirm_cancel(self) -> None:
        """Zrušit až po potvrzení; výpočet mezitím běží dál."""
        if not self.runner.running:
            return
        state = self.runner.state
        done = f"{state.processed} z {state.total}" if state.total else "0"
        answer = QMessageBox.question(
            self,
            "Zrušit výpočet",
            f"Opravdu zrušit výpočet? Hotovo je {done} nahrávek.\n"
            "Hotové řádky zůstanou ve výsledcích, mezivýsledky ve složce work.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes and self.runner.running:
            self.runner.cancel()

    def _on_finished(self, code: int, cancelled: bool) -> None:
        self._tick.stop()
        self._finished_at = time.monotonic()
        self.cancel_btn.setEnabled(False)
        self.bar.setRange(0, max(1, self.bar.maximum()))
        state = self.runner.state
        spent = format_seconds(self._finished_at - self._started_at)
        if cancelled:
            self.headline.setText(
                f"Zrušeno uživatelem po {state.processed} z {state.total} nahrávek."
                if state.total
                else "Zrušeno uživatelem."
            )
            self.bar.setValue(state.processed)
        elif code != 0:
            self.headline.setText(f"Knihovna skončila chybou (kód {code}), viz log.")
            self.log_toggle.setChecked(True)
        else:
            n_err = len(state.errors)
            self.headline.setText(
                f"Hotovo za {spent}: {state.processed - n_err} z {state.total} ok"
                + (f", {n_err} s chybou" if n_err else "")
            )
        if state.out:
            self.summary.setText(self.summary.text() + f" · výstup: {state.out}")
        self.current.setText("")
        self._update_timing()
        self.progress_changed.emit("")
        self.finished.emit(state, code, cancelled)
