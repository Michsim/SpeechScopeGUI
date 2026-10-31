"""Průvodce Nová norma: složky po úlohách, tabulka kontrol, souhrn a Spustit.

Tři kroky podle mockupu (docs/mockups/normy): 1 jméno, popis a pro každou
úlohu složka s nahrávkami a protokol (přibalený; vlastní jen v rozšířeném
režimu), 2 jedna tabulka kontrol pro všechny úlohy (kontrola z názvu
souboru nebo z manifest.csv ve složce, věk a pohlaví ručně nebo z CSV),
3 souhrn dávek s odhadem času. Výsledek je `NormPlan`; dávky spouští
hlavní okno. Do složek s nahrávkami se nic nezapisuje.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend import meta
from ..backend import tasks as tasks_mod
from ..backend.norm_build import (
    SUBJECT_RULES,
    Control,
    FolderScan,
    NormPlan,
    TaskPlan,
    default_protocol,
    guess_controls,
    read_controls_csv,
    scan_folder,
    with_language,
)
from ..backend.norms import RECOMMENDED_CONTROLS, NormInfo, unique_norm_dir
from ..backend.protocol import Protocol
from ..i18n import tr
from . import theme
from .custom_task_dialog import CustomTaskDialog
from .widgets.clean_table import card, set_text, style_table
from .widgets.status_header import Pill

SEX_CHOICES = (("", "–"), (meta.FEMALE, "žena"), (meta.MALE, "muž"))
CSV_FILTER = "CSV (*.csv)"


def format_duration(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return tr("{m} min").format(m=minutes)
    return tr("{h} h {m} min").format(h=minutes // 60, m=minutes % 60)


class TaskRow:
    """Řádek kroku 1: úloha, složka, protokol, počet nahrávek.

    Vlastní úloha (`custom`) má pod názvem, jako která úloha knihovny se počítá,
    protokoly základní úlohy a výchozí protokol z vlastní úlohy; jde ji odebrat.
    """

    def __init__(
        self,
        task: str,
        protocols: list[Protocol],
        advanced: bool,
        custom: tasks_mod.CustomTask | None = None,
    ) -> None:
        self.task = task
        self.custom = custom
        base = custom.base if custom is not None else task
        self.label = QLabel(tasks_mod.label(task) if custom is None else custom.name)
        self.name_box = QWidget()
        name_layout = QVBoxLayout(self.name_box)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(0)
        name_layout.addWidget(self.label)
        self.sub = QLabel(
            tr("vlastní · jako {task}").format(task=contract.TASK_SHORT.get(base, base))
            if custom is not None
            else ""
        )
        self.sub.setObjectName("muted")
        self.sub.setVisible(custom is not None)
        name_layout.addWidget(self.sub)
        self.folder = QLineEdit()
        self.folder.setPlaceholderText(tr("nevybráno, úloha se vynechá"))
        self.browse = QPushButton(tr("Vybrat…"))
        self.remove = QPushButton("✕")
        self.remove.setFixedSize(36, 34)
        self.remove.setToolTip(tr("Odebrat vlastní úlohu z normy"))
        self.remove.setVisible(custom is not None)
        self.protocol = QComboBox()
        choices = [p for p in protocols if p.task == base and (p.builtin or advanced)]
        default = default_protocol(base, protocols)
        if custom is not None and custom.protocol:
            default = next((p for p in protocols if p.name == custom.protocol), default)
        for proto in sorted(choices, key=lambda p: (not p.builtin, p.display_name)):
            suffix = tr(" (přibalený)") if proto.builtin else tr(" (vlastní)")
            self.protocol.addItem(proto.display_name + suffix, proto)
            if default is not None and proto.name == default.name:
                self.protocol.setCurrentIndex(self.protocol.count() - 1)
        self.protocol.setEnabled(advanced and self.protocol.count() > 1)
        self.count = QLabel("–")
        self.count.setObjectName("muted")
        self.scan = FolderScan([])

    def current_protocol(self) -> Protocol | None:
        data = self.protocol.currentData()
        return data if isinstance(data, Protocol) else None


class NewNormDialog(QDialog):
    def __init__(
        self,
        protocols: list[Protocol],
        norms_dir: Path,
        *,
        advanced: bool = False,
        languages: list[str] | None = None,
        language: str = "cs",
        rate_lookup: Callable[[str], float | None] = lambda _slug: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Nová norma"))
        self.resize(980, 700)
        self._norms_dir = norms_dir
        self._rate = rate_lookup
        self._files: dict[Path, str] = {}
        self._controls: dict[str, Control] = {}
        self._filling = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)
        steps = QHBoxLayout()
        steps.setSpacing(8)
        self.step_pills = [
            Pill(tr("1 Nahrávky"), "neutral"),
            Pill(tr("2 Kontroly"), "neutral"),
            Pill(tr("3 Spustit"), "neutral"),
        ]
        for i, pill in enumerate(self.step_pills):
            if i:
                arrow = QLabel("›")
                arrow.setObjectName("muted")
                steps.addWidget(arrow)
            steps.addWidget(pill)
        steps.addStretch(1)
        outer.addLayout(steps)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_sources(protocols, advanced, languages or [], language))
        self.stack.addWidget(self._build_controls())
        self.stack.addWidget(self._build_summary())

        footer = QHBoxLayout()
        self.back_btn = QPushButton("")
        self.back_btn.clicked.connect(self.go_back)
        footer.addWidget(self.back_btn)
        footer.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        footer.addWidget(self.hint)
        self.next_btn = QPushButton("")
        theme.set_role(self.next_btn, "primary")
        self.next_btn.clicked.connect(self.go_next)
        footer.addWidget(self.next_btn)
        outer.addLayout(footer)
        self._show_step(0)

    # --- krok 1: nahrávky ---------------------------------------------------------

    def _build_sources(
        self, protocols: list[Protocol], advanced: bool, languages: list[str], language: str
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("třeba Zdravé kontroly SAMI 2026"))
        self.name.textChanged.connect(self._update_buttons)
        form.addRow(tr("Název normy"), self.name)
        self.source = QLineEdit()
        self.source.setPlaceholderText(tr("pracoviště nebo studie"))
        form.addRow(tr("Zdroj"), self.source)
        self.conditions = QLineEdit()
        self.conditions.setPlaceholderText(
            tr("místnost, mikrofon (pomůže posoudit, k čemu normu použít)")
        )
        form.addRow(tr("Podmínky"), self.conditions)
        self.language = QComboBox()
        for code in languages or [language]:
            self.language.addItem(contract.language_label(code), code)
        idx = self.language.findData(language)
        self.language.setCurrentIndex(max(idx, 0))
        form.addRow(tr("Jazyk nahrávek"), self.language)
        layout.addLayout(form)

        grid_box = QFrame()
        grid_box.setObjectName("card")
        grid = QGridLayout(grid_box)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        for col, text in enumerate(
            [tr("úloha"), tr("složka s nahrávkami"), "", tr("protokol"), tr("nahrávek")]
        ):
            head = QLabel(text)
            head.setObjectName("muted")
            grid.addWidget(head, 0, col)
        self.task_grid = grid
        self._protocols = protocols
        self._advanced = advanced
        self.rows: dict[str, TaskRow] = {}
        for task in contract.TASKS:
            self._add_row(TaskRow(task, protocols, advanced))
        grid.setColumnStretch(1, 1)
        layout.addWidget(grid_box)
        options = QHBoxLayout()
        self.recursive = QCheckBox(tr("včetně podsložek"))
        self.recursive.toggled.connect(self._rescan_all)
        options.addWidget(self.recursive)
        self.add_task_btn = QPushButton(tr("+ Přidat vlastní úlohu…"))
        self.add_task_btn.setToolTip(
            tr("Další úloha pod vlastním jménem, počítá se jako jedna z úloh knihovny.")
        )
        self.add_task_btn.clicked.connect(lambda: self._ask_custom_task())
        options.addWidget(self.add_task_btn)
        options.addStretch(1)
        layout.addLayout(options)
        note = QLabel(
            tr(
                "Protokol je přibalený pro danou úlohu; jiný jde vybrat jen v rozšířeném "
                "režimu. Vlastní úloha má svůj výchozí protokol a počítá se jako jedna z úloh "
                "knihovny; norma si ji zapíše, takže ji Hodnocení nabídne pacientům. Norma si "
                "zapíše protokol i verzi knihovny. Do složek s nahrávkami se nic nezapisuje."
            )
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _add_row(self, row: TaskRow) -> None:
        r = len(self.rows) + 1
        row.folder.editingFinished.connect(lambda row=row: self._rescan(row))
        row.browse.clicked.connect(lambda _=False, row=row: self._browse(row))
        row.remove.clicked.connect(lambda _=False, row=row: self.remove_custom_task(row.task))
        grid = self.task_grid
        grid.addWidget(row.name_box, r, 0)
        grid.addWidget(row.folder, r, 1)
        grid.addWidget(row.browse, r, 2)
        grid.addWidget(row.protocol, r, 3)
        grid.addWidget(row.count, r, 4)
        grid.addWidget(row.remove, r, 5)
        self.rows[row.task] = row

    def _ask_custom_task(self) -> None:
        dialog = CustomTaskDialog(self._protocols, advanced=self._advanced, parent=self)
        if dialog.exec():
            self.add_custom_task(dialog.task())

    def add_custom_task(self, task: tasks_mod.CustomTask) -> TaskRow:
        """Řádek vlastní úlohy v kroku 1 (klíč musí být volný); vrátí ho kvůli složce."""
        if task.key in self.rows:
            return self.rows[task.key]
        row = TaskRow(task.key, self._protocols, self._advanced, custom=task)
        self._add_row(row)
        self._update_buttons()
        return row

    def remove_custom_task(self, key: str) -> None:
        row = self.rows.get(key)
        if row is None or row.custom is None:
            return
        for widget in (row.name_box, row.folder, row.browse, row.protocol, row.count, row.remove):
            self.task_grid.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        del self.rows[key]
        self._update_buttons()

    def custom_tasks(self) -> list[tasks_mod.CustomTask]:
        return [r.custom for r in self.rows.values() if r.custom is not None]

    def _browse(self, row: TaskRow) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, tr("Složka s nahrávkami: {task}").format(task=row.label.text()), row.folder.text()
        )
        if folder:
            self.set_folder(row.task, Path(folder))

    def set_folder(self, task: str, folder: Path | None) -> None:
        row = self.rows[task]
        row.folder.setText(str(folder) if folder else "")
        self._rescan(row)

    def set_protocol(self, task: str, name: str) -> bool:
        row = self.rows[task]
        for i in range(row.protocol.count()):
            proto = row.protocol.itemData(i)
            if isinstance(proto, Protocol) and proto.name == name:
                row.protocol.setCurrentIndex(i)
                return True
        return False

    def _rescan(self, row: TaskRow) -> None:
        text = row.folder.text().strip()
        row.scan = (
            scan_folder(Path(text), recursive=self.recursive.isChecked())
            if text
            else FolderScan([])
        )
        n = len(row.scan.recordings)
        if not text:
            row.count.setText("–")
        elif n:
            row.count.setText(f"{n} ✓")
        else:
            row.count.setText(tr("žádné"))
        self._update_buttons()

    def _rescan_all(self) -> None:
        for row in self.rows.values():
            self._rescan(row)

    def active_rows(self) -> list[TaskRow]:
        return [
            r for r in self.rows.values() if r.scan.recordings and r.current_protocol() is not None
        ]

    # --- krok 2: kontroly ---------------------------------------------------------

    def _build_controls(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Kontrolu poznám z názvu souboru:")))
        self.rule = QComboBox()
        for key, label in SUBJECT_RULES.items():
            self.rule.addItem(label, key)
        self.rule.currentIndexChanged.connect(self._regroup)
        top.addWidget(self.rule)
        self.example = QLabel("")
        self.example.setObjectName("muted")
        top.addWidget(self.example, 1)
        self.csv_btn = QPushButton(tr("Načíst věk a pohlaví z CSV…"))
        self.csv_btn.clicked.connect(self._load_csv)
        top.addWidget(self.csv_btn)
        layout.addLayout(top)
        self.counts = QLabel("")
        self.counts.setObjectName("muted")
        layout.addWidget(self.counts)
        self.table = QTableWidget()
        style_table(
            self.table,
            [tr("kontrola"), tr("věk"), tr("pohlaví"), tr("nahrávky"), tr("stav")],
            row_height=44,  # místo na výběr pohlaví
        )
        # věk se píše přímo do buňky (style_table úpravy vypíná)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(1, 80)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(2, 110)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._age_edited)
        layout.addWidget(card(self.table), 1)
        note = QLabel(
            tr(
                "Kontrolu bez věku nebo pohlaví jde použít, jen se nevybere podle věkového "
                "okna nebo pohlaví. Když ve složce je manifest.csv, pacient, věk a pohlaví "
                "se vezmou z něj."
            )
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _regroup(self) -> None:
        scans = {r.task: r.scan for r in self.active_rows()}
        rule = str(self.rule.currentData() or "prefix")
        self._files, self._controls = guess_controls(scans, rule, self._controls)
        first = next((p for s in scans.values() for p in s.recordings), None)
        if first is not None:
            self.example.setText(f"{first.name} → {self._files.get(first, '')}")
        self._fill_table()

    def _fill_table(self) -> None:
        self._filling = True
        per_subject: dict[str, dict[str, int]] = {}
        for row in self.active_rows():
            for rec in row.scan.recordings:
                tasks = per_subject.setdefault(self._files.get(rec, ""), {})
                tasks[row.task] = tasks.get(row.task, 0) + 1
        all_tasks = [r.task for r in self.active_rows()]
        for r in range(self.table.rowCount()):  # staré výběry pohlaví hned pryč, ne až později
            old = self.table.cellWidget(r, 2)
            if old is not None:
                old.hide()
        self.table.setRowCount(len(self._controls))
        for r, (subject, control) in enumerate(self._controls.items()):
            name = QTableWidgetItem(subject)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            bold = name.font()
            bold.setBold(True)
            name.setFont(bold)
            self.table.setItem(r, 0, name)
            age = QTableWidgetItem("" if control.age is None else f"{control.age:g}")
            age.setData(Qt.ItemDataRole.UserRole, subject)
            self.table.setItem(r, 1, age)
            sex = QComboBox()
            for code, label in SEX_CHOICES:
                sex.addItem(tr(label), code)
            sex.setCurrentIndex(max(0, sex.findData(control.sex)))
            sex.currentIndexChanged.connect(
                lambda _i, s=subject, box=sex: self.set_sex(s, str(box.currentData()))
            )
            self.table.setCellWidget(r, 2, sex)
            tasks = per_subject.get(subject, {})
            recs = QTableWidgetItem(
                " · ".join(f"{contract.TASK_SHORT.get(t, t)} {n}" for t, n in tasks.items())
            )
            recs.setFlags(recs.flags() & ~Qt.ItemFlag.ItemIsEditable)
            recs.setForeground(QColor(theme.MUTED))
            self.table.setItem(r, 3, recs)
            state = QTableWidgetItem(self._state_text(control, tasks, all_tasks))
            state.setFlags(state.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, 4, state)
        self._filling = False
        self._update_counts()

    def _state_text(self, control: Control, tasks: dict[str, int], all_tasks: list[str]) -> str:
        missing = []
        if control.age is None:
            missing.append(tr("chybí věk"))
        if not control.sex:
            missing.append(tr("chybí pohlaví"))
        lacking = [self.rows[t].label.text() for t in all_tasks if t not in tasks]
        if lacking:
            missing.append(tr("bez {tasks}").format(tasks=", ".join(lacking)))
        return " · ".join(missing) or "✓"

    def _age_edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 1:
            return
        subject = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self.set_age(subject, meta.parse_age(item.text()))

    def set_age(self, subject: str, age: float | None) -> None:
        control = self._controls.get(subject)
        if control is None:
            return
        control.age = age
        self._refresh_state(subject)

    def set_sex(self, subject: str, sex: str) -> None:
        control = self._controls.get(subject)
        if control is None:
            return
        control.sex = sex
        self._refresh_state(subject)

    def _refresh_state(self, subject: str) -> None:
        """Jen sloupec stav a počty; tabulka se nepřekresluje (smazala by právě měněný výběr)."""
        tasks: dict[str, int] = {}
        for row in self.active_rows():
            for rec in row.scan.recordings:
                if self._files.get(rec) == subject:
                    tasks[row.task] = tasks.get(row.task, 0) + 1
        all_tasks = [r.task for r in self.active_rows()]
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.text() == subject:
                state = self.table.item(r, 4)
                if state is not None:
                    state.setText(self._state_text(self._controls[subject], tasks, all_tasks))
                break
        self._update_counts()

    def _update_counts(self) -> None:
        controls = list(self._controls.values())
        complete = sum(1 for c in controls if c.age is not None and c.sex)
        no_age = sum(1 for c in controls if c.age is None)
        parts = [
            tr("{n} kontrol").format(n=len(controls)),
            tr("{n} kompletních").format(n=complete),
        ]
        if no_age:
            parts.append(tr("{n} bez věku").format(n=no_age))
        self.counts.setText(" · ".join(parts))

    def _load_csv(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, tr("Věk a pohlaví kontrol"), "", CSV_FILTER)
        if source:
            self.load_controls_csv(Path(source))

    def load_controls_csv(self, path: Path) -> int:
        """Doplní věk a pohlaví z CSV; vrátí, kolika kontrolám."""
        try:
            loaded = read_controls_csv(path)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("SpeechScope"), str(exc))
            return 0
        n = 0
        for subject, row in loaded.items():
            control = self._controls.get(subject)
            if control is None:
                continue
            if row.age is not None:
                control.age = row.age
            if row.sex:
                control.sex = row.sex
            n += 1
        self._fill_table()
        return n

    def controls(self) -> dict[str, Control]:
        return dict(self._controls)

    # --- krok 3: souhrn -----------------------------------------------------------

    def _build_summary(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.summary_title = QLabel("")
        self.summary_title.setObjectName("headline")
        layout.addWidget(self.summary_title)
        self.summary_text = QLabel("")
        self.summary_text.setWordWrap(True)
        layout.addWidget(self.summary_text)
        self.batches = QTableWidget()
        style_table(self.batches, [tr("úloha"), tr("dávka"), tr("odhad")])
        header = self.batches.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        layout.addWidget(card(self.batches))
        self.total = QLabel("")
        layout.addWidget(self.total)
        self.warnings = QLabel("")
        self.warnings.setObjectName("card")
        theme.set_role(self.warnings, "warn")
        self.warnings.setWordWrap(True)
        self.warnings.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(self.warnings)
        info = QLabel(
            tr(
                "Dávky se zařadí do fronty na stránce Výpočet jako běžná analýza. Po poslední "
                "se norma sestaví sama a objeví se na stránce Normy. Když některá dávka "
                "spadne, jde normu sestavit z hotových, nebo chybné dopočítat."
            )
        )
        info.setObjectName("muted")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch(1)
        return page

    def _fill_summary(self) -> None:
        rows = self.active_rows()
        self.summary_title.setText(self.name.text().strip())
        controls = list(self._controls.values())
        complete = sum(1 for c in controls if c.age is not None and c.sex)
        self.summary_text.setText(
            tr(
                "{n} kontrol, {k} s věkem i pohlavím. Spočítá se dávek: {b}, každá na stránce "
                "Výpočet jako běžná analýza."
            ).format(n=len(controls), k=complete, b=len(rows))
        )
        self.batches.setRowCount(len(rows))
        total = 0.0
        known = True
        for r, row in enumerate(rows):
            proto = row.current_protocol()
            assert proto is not None
            set_text(self.batches, r, 0, row.label.text(), bold=True)
            self.batches.setItem(
                r,
                1,
                QTableWidgetItem(
                    tr("{n} nahrávek · {proto}").format(
                        n=len(row.scan.recordings), proto=proto.display_name
                    )
                ),
            )
            rate = self._rate(proto.slug())
            if rate and row.scan.audio_seconds:
                seconds = rate / 60 * row.scan.audio_seconds
                total += seconds
                estimate = format_duration(seconds)
            else:
                known = False
                estimate = tr("po prvním běhu")
            set_text(self.batches, r, 2, estimate, muted=True)
        height = self.batches.horizontalHeader().height() + 4
        height += sum(self.batches.rowHeight(i) for i in range(len(rows)))
        self.batches.setFixedHeight(height)
        if total:
            text = tr("Celkem odhad asi {t}").format(t=format_duration(total))
            self.total.setText(
                text if known else text + tr(" (bez úloh, které se ještě nepočítaly)")
            )
        else:
            self.total.setText(tr("Odhad času bude po prvním běhu protokolu."))
        problems = []
        for row in self.rows.values():
            if not row.scan.recordings:
                problems.append(
                    tr(
                        "{task} se vynechá (bez nahrávek). Dimenze, které ji potřebují, s touto "
                        "normou porovnat nepůjdou."
                    ).format(task=row.label.text())
                )
        subjects = {r.task: set() for r in rows}
        for row in rows:
            for rec in row.scan.recordings:
                subjects[row.task].add(self._files.get(rec, ""))
        for row in rows:
            n = len(subjects[row.task])
            if n < RECOMMENDED_CONTROLS:
                problems.append(
                    tr("{task}: jen {n} kontrol, doporučeno aspoň {min}.").format(
                        task=row.label.text(), n=n, min=RECOMMENDED_CONTROLS
                    )
                )
        self.warnings.setText("\n".join(problems))
        self.warnings.setVisible(bool(problems))

    # --- kroky ---------------------------------------------------------------------

    def step(self) -> int:
        return self.stack.currentIndex()

    def _show_step(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, pill in enumerate(self.step_pills):
            theme.set_role(pill, "accent" if i == index else ("ok" if i < index else "neutral"))
        self.back_btn.setText(tr("Storno") if index == 0 else tr("‹ Zpět"))
        self.next_btn.setText(tr("Spustit") if index == 2 else tr("Další ›"))
        if index == 1:
            self._regroup()
        elif index == 2:
            self._fill_summary()
        self._update_buttons()

    def _update_buttons(self) -> None:
        hint = ""
        ok = True
        if self.step() == 0:
            rows = self.active_rows()
            if not self.name.text().strip():
                ok, hint = False, tr("Vyplňte název normy.")
            elif not rows:
                ok, hint = False, tr("Vyberte složku aspoň pro jednu úlohu.")
            else:
                n = sum(len(r.scan.recordings) for r in rows)
                hint = tr("úloh: {k} · nahrávek: {n}").format(k=len(rows), n=n)
        elif self.step() == 1:
            ok = bool(self._controls)
            hint = "" if ok else tr("Žádná kontrola.")
        self.next_btn.setEnabled(ok)
        self.hint.setText(hint)

    def go_next(self) -> None:
        if not self.next_btn.isEnabled():
            return
        if self.step() < 2:
            self._show_step(self.step() + 1)
        else:
            self.accept()

    def go_back(self) -> None:
        if self.step() == 0:
            self.reject()
        else:
            self._show_step(self.step() - 1)

    # --- výsledek ----------------------------------------------------------------

    def plan(self) -> NormPlan:
        """Plán normy z vyplněného průvodce (složka v norms_dir je volná, ještě nevznikla)."""
        if not self._controls:
            self._regroup()
        language = str(self.language.currentData() or "")
        name = self.name.text().strip()
        info = NormInfo(
            name=name,
            source=self.source.text().strip(),
            conditions=self.conditions.text().strip(),
            language=language,
        )
        tasks = []
        for row in self.active_rows():
            proto = row.current_protocol()
            assert proto is not None
            tasks.append(
                TaskPlan(
                    task=row.task,
                    protocol=with_language(proto, language).to_dict(),
                    inputs=list(row.scan.recordings),
                )
            )
        files = {p: s for p, s in self._files.items() if any(p in t.inputs for t in tasks)}
        used = {t.task for t in tasks}
        info.custom_tasks = [c for c in self.custom_tasks() if c.key in used]
        for custom in info.custom_tasks:
            tasks_mod.registry().register(custom, save=True)  # příště se nabídne znovu
        return NormPlan(
            info=info,
            folder=unique_norm_dir(self._norms_dir, name),
            tasks=tasks,
            files=files,
            controls=dict(self._controls),
        )


__all__ = ["NewNormDialog", "format_duration"]
