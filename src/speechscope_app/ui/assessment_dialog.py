"""Nové hodnocení a jeho úprava: pacient, věk, pohlaví, datum a nahrávky po úlohách.

Jednoduchý dialog podle mockupu (docs/mockups/hodnoceni): nahoře údaje
pacienta, pod tím pro každou úlohu vybrané nahrávky s Přidat… a Odebrat.
Přidat… nabídne nahrávky dané úlohy z hotových běhů (běhy norem ne):
nahoře ty, které k pacientovi přiřadil manifest, pak ty s jeho ID v názvu
souboru, pak ostatní. První přidaná nahrávka předvyplní prázdné údaje
pacienta z manifestu. Víc nahrávek jedné úlohy se ve výpočtu zprůměruje.
"""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend import meta
from ..backend.assessments import (
    Assessment,
    RecordingRef,
    candidates_for,
    new_id,
    norm_match,
    prefill,
)
from ..backend.visits import Recording
from ..i18n import tr
from . import theme

SEX_CHOICES = (("", "–"), (meta.MALE, "muž"), (meta.FEMALE, "žena"))
ROLE_REC = Qt.ItemDataRole.UserRole


def recording_text(rec: Recording) -> str:
    started = f" · {rec.run.started:%d. %m. %Y %H:%M}" if rec.run.started else ""
    return tr("{file} · běh {run}{started}").format(
        file=rec.file, run=rec.run.display_name, started=started
    )


def match_text(match: bool | None) -> str:
    if match is None:
        return ""
    return tr("✓ sedí s normou") if match else tr("jiný protokol než norma")


class PickRecordingDialog(QDialog):
    """Výběr nahrávky jedné úlohy; hledání podle souboru nebo pacienta."""

    def __init__(
        self,
        task: str,
        subject: str,
        recordings: list[Recording],
        taken: set[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            tr("Přidat nahrávku: {task}").format(task=contract.TASK_SHORT.get(task, task))
        )
        self.resize(720, 480)
        self._task = task
        self._recordings = [r for r in recordings if f"{r.run.dir.name}#{r.row}" not in taken]
        layout = QVBoxLayout(self)
        self.search = QLineEdit(subject)
        self.search.setPlaceholderText(tr("pacient nebo název souboru"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        layout.addWidget(self.list, 1)
        self.empty = QLabel(tr("Žádná nahrávka této úlohy v hotových bězích."))
        self.empty.setObjectName("muted")
        layout.addWidget(self.empty)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Přidat"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._fill()

    def _fill(self) -> None:
        needle = self.search.text().strip()
        manifest, by_name, others = candidates_for(self._task, needle, self._recordings)
        self.list.clear()
        groups = (
            (tr("podle manifestu patří k {s}").format(s=needle), manifest),
            (tr("název souboru obsahuje „{s}“").format(s=needle), by_name),
            (tr("ostatní nahrávky úlohy"), [] if needle else others),
        )
        first: QListWidgetItem | None = None
        for header, recs in groups:
            if not recs:
                continue
            head = QListWidgetItem(header)
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(head)
            for rec in recs:
                item = QListWidgetItem("  " + recording_text(rec))
                item.setData(ROLE_REC, rec)
                item.setToolTip(str(rec.path) if rec.path else "")
                self.list.addItem(item)
                first = first or item
        self.list.setCurrentItem(first)
        self.empty.setVisible(first is None)

    def chosen(self) -> Recording | None:
        item = self.list.currentItem()
        data = item.data(ROLE_REC) if item is not None else None
        return data if isinstance(data, Recording) else None


class TaskBox:
    """Úloha v dialogu: vybrané nahrávky, Přidat… a Odebrat."""

    def __init__(self, task: str) -> None:
        self.task = task
        self.label = QLabel(contract.TASK_SHORT.get(task, task))
        self.label.setObjectName("card_title")
        self.list = QListWidget()
        self.list.setMaximumHeight(72)
        self.add_btn = QPushButton(tr("Přidat…"))
        self.remove_btn = QPushButton(tr("Odebrat"))
        self.note = QLabel("")
        self.note.setObjectName("muted")

    def recordings(self) -> list[Recording]:
        return [self.list.item(i).data(ROLE_REC) for i in range(self.list.count())]


class AssessmentDialog(QDialog):
    def __init__(
        self,
        recordings: list[Recording],
        *,
        assessment: Assessment | None = None,
        initial: Recording | None = None,
        norm_protocols: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Upravit hodnocení") if assessment else tr("Nové hodnocení"))
        self.resize(900, 640)
        self._recordings = recordings
        self._norm = norm_protocols or {}
        self._id = assessment.id if assessment else ""
        self._created = assessment.created if assessment else ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)
        person = QGridLayout()
        person.setHorizontalSpacing(10)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText(tr("ID pacienta"))
        self.subject.textChanged.connect(self._update_buttons)
        self.age = QLineEdit()
        self.age.setPlaceholderText(tr("roky"))
        self.age.setMaximumWidth(80)
        self.sex = QComboBox()
        for code, label in SEX_CHOICES:
            self.sex.addItem(tr(label), code)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("d. M. yyyy")
        self.date.setDate(QDate.currentDate())
        for col, (text, widget) in enumerate(
            (
                (tr("Pacient"), self.subject),
                (tr("Věk"), self.age),
                (tr("Pohlaví"), self.sex),
                (tr("Datum vyšetření"), self.date),
            )
        ):
            person.addWidget(QLabel(text), 0, 2 * col)
            person.addWidget(widget, 0, 2 * col + 1)
        person.setColumnStretch(1, 1)
        outer.addLayout(person)
        self.prefill_note = QLabel("")
        self.prefill_note.setObjectName("muted")
        self.prefill_note.setWordWrap(True)
        self.prefill_note.hide()  # jen když něco přišlo z manifestu
        outer.addWidget(self.prefill_note)

        title = QLabel(tr("Nahrávky po úlohách"))
        title.setObjectName("section")
        outer.addWidget(title)
        box = QFrame()
        box.setObjectName("card")
        grid = QGridLayout(box)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setVerticalSpacing(10)
        self.boxes: dict[str, TaskBox] = {}
        for row, task in enumerate(contract.TASKS):
            tb = TaskBox(task)
            tb.add_btn.clicked.connect(lambda _=False, t=task: self.pick(t))
            tb.remove_btn.clicked.connect(lambda _=False, t=task: self.remove_selected(t))
            tb.list.currentRowChanged.connect(lambda _r, t=task: self._update_task(t))
            buttons = QVBoxLayout()
            buttons.addWidget(tb.add_btn)
            buttons.addWidget(tb.remove_btn)
            buttons.addStretch(1)
            grid.addWidget(tb.label, row * 2, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(tb.list, row * 2, 1)
            grid.addLayout(buttons, row * 2, 2)
            grid.addWidget(tb.note, row * 2 + 1, 1)
            self.boxes[task] = tb
        grid.setColumnStretch(1, 1)
        outer.addWidget(box, 1)
        hint = QLabel(
            tr(
                "Nabízejí se nahrávky z hotových běhů dané úlohy, ne z běhů norem. "
                "Víc nahrávek jedné úlohy se zprůměruje po jednotlivých feature. "
                "Úloha bez nahrávky se v pavouku neukáže."
            )
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        footer = QHBoxLayout()
        cancel = QPushButton(tr("Storno"))
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        footer.addStretch(1)
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        footer.addWidget(self.summary)
        self.ok_btn = QPushButton(tr("Uložit") if assessment else tr("Vytvořit hodnocení"))
        theme.set_role(self.ok_btn, "primary")
        self.ok_btn.clicked.connect(self.accept)
        footer.addWidget(self.ok_btn)
        outer.addLayout(footer)

        if assessment is not None:
            self._load(assessment)
        elif initial is not None:
            self.add_recording(initial)
        for task in self.boxes:
            self._update_task(task)
        self._update_buttons()

    # --- údaje --------------------------------------------------------------------

    def _load(self, assessment: Assessment) -> None:
        self.subject.setText(assessment.subject)
        self.age.setText("" if assessment.age is None else f"{assessment.age:g}")
        self.sex.setCurrentIndex(max(0, self.sex.findData(assessment.sex)))
        self._set_date(assessment.date)
        for ref in assessment.recordings:
            rec = next((r for r in self._recordings if ref.matches(r) and r.file == ref.file), None)
            if rec is not None:
                self._append(rec)

    def _set_date(self, iso: str) -> None:
        try:
            day = dt.date.fromisoformat(iso[:10])
        except ValueError:
            return
        self.date.setDate(QDate(day.year, day.month, day.day))

    def _prefill(self, rec: Recording) -> None:
        """Prázdné údaje pacienta z manifestu nahrávky; vyplněné se nepřepisují."""
        subject, age, sex, date = prefill(rec)
        filled = []
        if subject and not self.subject.text().strip():
            self.subject.setText(subject)
            filled.append(tr("pacient"))
        if age is not None and not self.age.text().strip():
            self.age.setText(f"{age:g}")
            filled.append(tr("věk"))
        if sex and not self.sex.currentData():
            self.sex.setCurrentIndex(max(0, self.sex.findData(sex)))
            filled.append(tr("pohlaví"))
        if date and not self._has_recordings(exclude=rec):
            self._set_date(date)
        if filled:
            self.prefill_note.setText(
                tr("Z manifestu nahrávky {file}: {what}. Jde přepsat.").format(
                    file=rec.file, what=", ".join(filled)
                )
            )
            self.prefill_note.show()

    def _has_recordings(self, exclude: Recording | None = None) -> bool:
        return any(r is not exclude for tb in self.boxes.values() for r in tb.recordings())

    # --- nahrávky -----------------------------------------------------------------

    def taken(self) -> set[str]:
        return {f"{r.run.dir.name}#{r.row}" for tb in self.boxes.values() for r in tb.recordings()}

    def pick_dialog(self, task: str) -> PickRecordingDialog:
        return PickRecordingDialog(
            task, self.subject.text().strip(), self._recordings, self.taken(), self
        )

    def pick(self, task: str) -> None:
        dialog = self.pick_dialog(task)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rec = dialog.chosen()
            if rec is not None:
                self.add_recording(rec)

    def add_recording(self, rec: Recording) -> bool:
        if rec.task not in self.boxes or f"{rec.run.dir.name}#{rec.row}" in self.taken():
            return False
        self._append(rec)
        self._prefill(rec)
        self._update_task(rec.task)
        self._update_buttons()
        return True

    def _append(self, rec: Recording) -> None:
        tb = self.boxes[rec.task]
        text = recording_text(rec)
        match = match_text(norm_match(rec, self._norm))
        item = QListWidgetItem(f"{text}   {match}" if match else text)
        item.setData(ROLE_REC, rec)
        item.setToolTip(str(rec.path) if rec.path else "")
        tb.list.addItem(item)

    def remove_selected(self, task: str) -> None:
        tb = self.boxes[task]
        row = tb.list.currentRow()
        if row >= 0:
            tb.list.takeItem(row)
        self._update_task(task)
        self._update_buttons()

    def _update_task(self, task: str) -> None:
        tb = self.boxes[task]
        n = tb.list.count()
        tb.list.setVisible(n > 0)
        tb.remove_btn.setEnabled(tb.list.currentRow() >= 0)
        tb.remove_btn.setVisible(n > 0)
        tb.add_btn.setText(tr("Přidat…") if n else tr("+ Přidat nahrávku"))
        tb.note.setText(tr("{n} nahrávky: feature se zprůměrují").format(n=n) if n > 1 else "")
        tb.note.setVisible(n > 1)

    def _update_buttons(self) -> None:
        recs = [r for tb in self.boxes.values() for r in tb.recordings()]
        tasks = {r.task for r in recs}
        self.summary.setText(
            tr("úlohy: {k} · nahrávek: {n}").format(k=len(tasks), n=len(recs)) if recs else ""
        )
        self.ok_btn.setEnabled(bool(recs) and bool(self.subject.text().strip()))

    # --- výsledek -----------------------------------------------------------------

    def assessment(self) -> Assessment:
        day = self.date.date()
        return Assessment(
            id=self._id or new_id(),
            subject=self.subject.text().strip(),
            age=meta.parse_age(self.age.text()),
            sex=str(self.sex.currentData() or ""),
            date=dt.date(day.year(), day.month(), day.day()).isoformat(),
            recordings=[RecordingRef.of(r) for tb in self.boxes.values() for r in tb.recordings()],
            created=self._created,
        )


__all__ = ["AssessmentDialog", "PickRecordingDialog", "recording_text"]
