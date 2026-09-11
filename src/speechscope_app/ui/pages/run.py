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
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ... import contract
from ...backend.history import RunInfo
from ...backend.protocol import provider_order
from ...backend.runner import Runner
from ...i18n import tr
from .. import theme
from ..file_actions import open_file, show_file_menu
from ..widgets.run_history import RunHistory

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


def estimate_rate(pairs: list[tuple[float, float]]) -> float | None:
    """Sekundy výpočtu na sekundu zvuku: medián poměrů (doba, délka zvuku),
    od tří hotových bez první nahrávky (načtení modelů)."""
    ratios = [spent / audio for spent, audio in pairs if audio > 0]
    if not ratios:
        return None
    sample = ratios[1:] if len(ratios) >= 3 else ratios
    return statistics.median(sample)


class RunPage(QWidget):
    finished = Signal(object, int, bool)  # BatchState, návratový kód, zrušeno
    notice = Signal(str)  # krátká hláška do stavového řádku hlavního okna
    progress_changed = Signal(str)  # krátký text do nabídky, "" když nic neběží
    file_done = Signal(int, int)  # hotových, celkem; po každé nahrávce živého běhu
    queue_remove = Signal(int)  # vyhodit položku fronty
    queue_clear = Signal()

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
        self._file_seconds: dict[int, float] = {}  # index nahrávky -> doba výpočtu
        self._audio: list[float | None] = []  # délky zvuku podle indexu nahrávky
        self._expected: float | None = None
        self._expected_total: float | None = None
        self._providers: list[str] = []
        self._paths: list[Path] = []
        self._events_file: Path | None = None
        self._viewing: RunInfo | None = None  # starý běh z historie místo živého
        self._live_dir: Path | None = None  # složka posledního živého běhu
        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._on_tick)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(self.splitter, 1)
        self.history = RunHistory(tr("Historie výpočtů"))
        self.history.selected.connect(self.show_recorded)
        self.splitter.addWidget(self.history)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)
        self.splitter.addWidget(body)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([380, 720])

        self.headline = QLabel(tr("Žádný běh."))
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
        self.files.cellDoubleClicked.connect(lambda row, _col: self.open_recording(row))
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._files_menu)
        self._row_paths: dict[int, Path] = {}
        self._set_columns([])
        layout.addWidget(self.files, 2)

        # fronta dalších dávek (jen když něco čeká)
        self.queue_box = QWidget()
        queue_layout = QVBoxLayout(self.queue_box)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        queue_layout.setSpacing(4)
        queue_head = QHBoxLayout()
        self.queue_title = QLabel("")
        self.queue_title.setObjectName("section")
        self.queue_remove_btn = QPushButton(tr("Odebrat vybranou"))
        self.queue_remove_btn.clicked.connect(self._remove_selected_queued)
        self.queue_clear_btn = QPushButton(tr("Vyprázdnit frontu"))
        self.queue_clear_btn.clicked.connect(self.queue_clear)
        queue_head.addWidget(self.queue_title, 1)
        queue_head.addWidget(self.queue_remove_btn)
        queue_head.addWidget(self.queue_clear_btn)
        queue_layout.addLayout(queue_head)
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(96)
        queue_layout.addWidget(self.queue_list)
        self.queue_box.hide()
        layout.addWidget(self.queue_box)

        self.log_toggle = QToolButton()
        self.log_toggle.setText(tr("Log knihovny"))
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
        self.cancel_btn = QPushButton(tr("Zrušit"))
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
        audio_seconds: list[float | None] | None = None,
        expected_total: float | None = None,
    ) -> None:
        """Spustí knihovnu. `inputs` předplní tabulku, `expected_seconds` je
        doba na nahrávku z minulého běhu téhož protokolu pro první odhad,
        `audio_seconds` délky nahrávek (None = neznámá) a `expected_total`
        odhad celé dávky podle minut zvuku, když ho hlavní okno spočítalo."""
        self._viewing = None
        self._paths = list(inputs or [])
        self._expected = expected_seconds
        self._expected_total = expected_total
        self._audio = list(audio_seconds or [])
        self._file_seconds = {}
        self._events_file = log_file.with_name("events.jsonl") if log_file else None
        self._live_dir = log_file.parent if log_file else None
        if self._events_file is not None:
            self._events_file.unlink(missing_ok=True)
        self._durations = []
        self._providers = []
        self._file_started_at = None
        self._stage_started_at = None
        self._finished_at = None
        self._started_at = time.monotonic()

        self.headline.setText(title)
        self.summary.setText(tr("{n} nahrávek").format(n=len(self._paths)) if self._paths else "")
        self.bar.setRange(0, 0)
        first_guess = expected_total or (
            expected_seconds * len(self._paths) if expected_seconds and self._paths else None
        )
        self.timing.setText(
            tr("podle minulého běhu asi {time}").format(time=format_seconds(first_guess))
            if first_guess
            else ""
        )
        self.current.setText(tr("Spouštím knihovnu…"))
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
            tr("nahrávka"),
            *(PROVIDER_SHORT.get(p, p) for p in providers),
            tr("výsledek"),
            tr("poznámka"),
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
        self.files.clearContents()  # buňky ze startu bez sloupců providerů by zůstaly
        self.files.setRowCount(len(names))
        self._row_paths = {
            i: p for i, p in enumerate(self._paths) if i < len(names) and p.name == names[i]
        }
        for row, name in enumerate(names):
            self._set_cell(row, self.COL_FILE, name, tooltip=str(self._row_paths.get(row, "")))
            self._set_cell(row, self.col_status, tr("čeká"), color=theme.MUTED)
            self._set_cell(row, self.col_note, "")
        self.files.resizeColumnsToContents()

    def _ensure_row(self, index: int, path: str) -> None:
        """Knihovna může najít jiné soubory než GUI; řádek se doplní."""
        while self.files.rowCount() <= index:
            row = self.files.rowCount()
            self.files.insertRow(row)
            self._set_cell(row, self.col_status, tr("čeká"), color=theme.MUTED)
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        self._row_paths[index] = Path(path)
        item = self.files.item(index, self.COL_FILE)
        if item is None or item.text() != name:
            self._set_cell(index, self.COL_FILE, name, tooltip=path)

    def path_for_row(self, row: int) -> Path | None:
        return self._row_paths.get(row)

    def open_recording(self, row: int) -> bool:
        path = self.path_for_row(row)
        return open_file(path, self.notice.emit) if path is not None else False

    def _files_menu(self, pos) -> None:  # noqa: ANN001
        item = self.files.itemAt(pos)
        path = self.path_for_row(item.row()) if item is not None else None
        if path is not None:
            show_file_menu(self, path, self.files.viewport().mapToGlobal(pos), self.notice.emit)

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

    def _record(self, event: contract.Event) -> None:
        """Událost do events.jsonl ve složce běhu, aby šel průběh přehrát z historie."""
        if self._events_file is None:
            return
        try:
            with self._events_file.open("a", encoding="utf-8") as fh:
                fh.write(contract.dump_event(event) + "\n")
        except OSError:
            self._events_file = None

    def _on_event(self, event: contract.Event) -> None:
        self._record(event)
        self._render_event(event, self.runner.state)

    def _render_event(self, event: contract.Event, state: contract.BatchState) -> None:
        now = time.monotonic()
        match event:
            case contract.StartEvent():
                self.bar.setRange(0, max(1, event.total))
                self.bar.setValue(0)
                self._set_columns(sorted(event.providers, key=provider_order))
                names = [p.name for p in self._paths]
                if event.total != len(names):
                    names = [""] * event.total
                self._fill_rows(names)
                providers = ", ".join(contract.PROVIDER_LABELS.get(p, p) for p in event.providers)
                self.summary.setText(
                    tr("{n} nahrávek, {k} feature").format(n=event.total, k=len(event.features))
                    + (
                        tr(" · spouští se: {providers}").format(providers=providers)
                        if providers
                        else tr(" · bez modelů")
                    )
                )
                if not state.detailed:
                    self.current.setText(tr("Knihovna hlásí jen dokončené nahrávky."))
            case contract.BeginEvent():
                self._file_started_at = now
                self._stage_started_at = None
                self._ensure_row(event.index, event.path)
                self._set_cell(event.index, self.col_status, tr("běží"), color=theme.ACCENT)
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
                    self._file_seconds[event.index] = now - self._file_started_at
                    self._file_started_at = None
                self._set_cell(
                    event.index,
                    self.col_status,
                    tr("ok") if event.ok else tr("chyba"),
                    color=theme.OK if event.ok else theme.MISSING,
                )
                self._set_cell(event.index, self.col_note, event.msg or "", tooltip=event.msg)
                self.bar.setValue(state.processed)
                if self._viewing is None:
                    self.progress_changed.emit(f"{state.processed}/{state.total}")
                    self.file_done.emit(state.processed, state.total)
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
                parts.append(tr("{label} z cache").format(label=label))
            elif stage.status == "error":
                parts.append(f"{label} ✗")
            else:
                parts.append(f"{label} ✓ {format_seconds(stage.seconds or 0)}")
        text = tr("Právě: {name} ({i}/{total})").format(
            name=name, i=state.current.index + 1, total=state.total
        )
        if parts:
            text += " · " + " · ".join(parts)
        self.current.setText(text)

    def _update_timing(self) -> None:
        state = self.runner.state
        end = self._finished_at if self._finished_at is not None else time.monotonic()
        spent = end - self._started_at
        self.elapsed.setText(tr("uplynulo {time}").format(time=format_seconds(spent)))
        if self._finished_at is not None or not state.total:
            self.timing.setText("")
            return
        per_file = estimate_per_file(self._durations)
        source = tr("podle hotových nahrávek")
        if per_file is None:
            per_file = self._expected
            source = tr("podle minulého běhu")
        remaining_files = state.total - state.processed
        if remaining_files <= 0:
            self.timing.setText(
                tr("{n} z {total} hotovo").format(n=state.processed, total=state.total)
            )
            return
        remaining = self._remaining_by_audio(per_file)
        if remaining is None:
            if per_file is None:
                self.timing.setText(
                    tr("{n} z {total} hotovo").format(n=state.processed, total=state.total)
                )
                return
            remaining = per_file * remaining_files
        else:
            source = tr("podle minut zvuku")
        if self._file_started_at is not None:
            current_guess = per_file if per_file is not None else remaining
            remaining -= min(current_guess, time.monotonic() - self._file_started_at)
        self.timing.setText(
            tr(
                "{n} z {total} hotovo · zbývá asi {remaining} ({source}, {per_file} na nahrávku)"
            ).format(
                n=state.processed,
                total=state.total,
                remaining=format_seconds(remaining),
                source=source,
                per_file=format_seconds(per_file),
            )
        )

    def _on_tick(self) -> None:
        self._update_timing()
        state = self.runner.state
        running = state.running_stage()
        if running is not None and self._stage_started_at is not None:
            self._stage_cell(running, time.monotonic() - self._stage_started_at)
        self._update_current()

    def set_queue(self, titles: list[str]) -> None:
        self.queue_list.clear()
        for i, title in enumerate(titles, start=1):
            self.queue_list.addItem(f"{i}. {title}")
        self.queue_title.setText(tr("Ve frontě ({n})").format(n=len(titles)))
        self.queue_box.setVisible(bool(titles))

    def _remove_selected_queued(self) -> None:
        row = self.queue_list.currentRow()
        if row >= 0:
            self.queue_remove.emit(row)

    def rate(self) -> float | None:
        """Sekundy výpočtu na sekundu zvuku z tohoto běhu; None bez známých délek."""
        pairs = [
            (spent, self._audio[i])
            for i, spent in sorted(self._file_seconds.items())
            if i < len(self._audio) and self._audio[i]
        ]
        return estimate_rate(pairs)  # type: ignore[arg-type]

    def _remaining_by_audio(self, per_file: float | None) -> float | None:
        """Zbývající čas z délek nezpracovaných nahrávek; None, když délky
        nebo tempo nejsou známé (a nejde je doplnit dobou na nahrávku)."""
        total = self.runner.state.total
        if not self._audio or total <= 0:
            return None
        rate = self.rate()
        if rate is None:
            stat = self._expected_total
            if stat is None or not self._audio:
                return None
            known = [a for a in self._audio if a]
            if not known:
                return None
            rate = stat / sum(known)  # odhad tempa z minulého běhu
        remaining = 0.0
        for i in range(total):
            if i in self._file_seconds:
                continue
            audio = self._audio[i] if i < len(self._audio) else None
            if audio:
                remaining += rate * audio
            elif per_file is not None:
                remaining += per_file
            else:
                return None
        return remaining

    def elapsed_seconds(self) -> float:
        end = self._finished_at if self._finished_at is not None else time.monotonic()
        return round(end - self._started_at, 1)

    def seconds_per_file(self) -> float | None:
        """Střední doba na nahrávku z tohoto běhu; pro statistiku protokolu."""
        return estimate_per_file(self._durations)

    def seconds_per_audio_minute(self) -> float | None:
        rate = self.rate()
        return rate * 60 if rate else None

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
            tr("Zrušit výpočet"),
            tr(
                "Opravdu zrušit výpočet? Hotovo je {done} nahrávek.\n"
                "Hotové řádky zůstanou ve výsledcích, mezivýsledky ve složce work."
            ).format(done=done),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes and self.runner.running:
            self.runner.cancel()

    # --- historie -------------------------------------------------------------------

    def set_work_root(self, root: Path) -> None:
        self.history.set_work_root(root)

    def refresh_history(self, select: Path | None = None) -> None:
        self.history.refresh(select=select)

    def show_recorded(self, info: RunInfo) -> None:
        """Průběh starého běhu z events.jsonl a jeho log; při živém běhu se nic nemění."""
        if self.runner.running:
            return
        if self._viewing is None and self._live_dir == info.dir:
            return  # právě dokončený běh už na stránce je, živý pohled se nepřepisuje
        self._viewing = info
        self._tick.stop()
        self._paths = []
        self._providers = []
        self._durations = []
        self._file_started_at = None
        self._stage_started_at = None
        self._started_at = time.monotonic()
        self._finished_at = self._started_at
        self.cancel_btn.setEnabled(False)
        self.log.clear()
        self.log_toggle.setChecked(False)
        self.current.setText("")
        self.timing.setText("")
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self._set_columns([])
        self._fill_rows([])
        self.headline.setText(info.protocol_name)
        when = info.started.strftime("%d.%m.%Y %H:%M") if info.started else ""
        parts = [when, info.status_label]
        if info.seconds:
            parts.append(tr("trvalo {time}").format(time=format_seconds(info.seconds)))
        events_file = info.dir / "events.jsonl"
        state = contract.BatchState()
        if events_file.is_file():
            for line in events_file.read_text(encoding="utf-8").splitlines():
                try:
                    event = contract.parse_event(line)
                except contract.ContractError:
                    continue
                if event is None:
                    continue
                state.apply(event)
                self._render_event(event, state)
            self.bar.setRange(0, max(1, state.total))
            self.bar.setValue(state.processed)
            self.current.setText("")
            self.timing.setText(
                tr("{n} z {total} hotovo").format(n=state.processed, total=state.total)
                if state.total
                else ""
            )
        else:
            self.current.setText(tr("Průběh tohoto běhu není zaznamenaný (starší verze)."))
        self.summary.setText(" · ".join(p for p in parts if p))  # až po přehrání událostí
        log_file = info.dir / "speechscope.log"
        if log_file.is_file():
            try:
                self.log.setPlainText(log_file.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        self.elapsed.setText(
            tr("uplynulo {time}").format(time=format_seconds(info.seconds)) if info.seconds else ""
        )

    def _on_finished(self, code: int, cancelled: bool) -> None:
        self._tick.stop()
        self._finished_at = time.monotonic()
        self.cancel_btn.setEnabled(False)
        self.bar.setRange(0, max(1, self.bar.maximum()))
        state = self.runner.state
        spent = format_seconds(self._finished_at - self._started_at)
        if cancelled:
            self.headline.setText(
                tr("Zrušeno uživatelem po {n} z {total} nahrávek.").format(
                    n=state.processed, total=state.total
                )
                if state.total
                else tr("Zrušeno uživatelem.")
            )
            self.bar.setValue(state.processed)
        elif code != 0:
            self.headline.setText(
                tr("Knihovna skončila chybou (kód {code}), viz log.").format(code=code)
            )
            self.log_toggle.setChecked(True)
        else:
            n_err = len(state.errors)
            self.headline.setText(
                tr("Hotovo za {time}: {ok} z {total} ok").format(
                    time=spent, ok=state.processed - n_err, total=state.total
                )
                + (tr(", {n} s chybou").format(n=n_err) if n_err else "")
            )
        if state.out:
            self.summary.setText(
                self.summary.text() + tr(" · výstup: {path}").format(path=state.out)
            )
        self.current.setText("")
        self._update_timing()
        self.progress_changed.emit("")
        self.finished.emit(state, code, cancelled)
