"""Nové hodnocení z nahrávek: volba zdroje a průvodce (nahrávky po úlohách, Spustit).

`NewAssessmentChoiceDialog` se ptá, odkud nahrávky vzít: z nahrávek (tento
průvodce, spočítá se) nebo z hotových běhů (`AssessmentDialog`).
`AssessmentBuildWidget` má dva kroky podle mockupu
(docs/mockups/hodnoceni-z-nahravek): 1 pacient, věk, pohlaví, datum, jazyk
a pro každou úlohu složka s nahrávkami a protokol (přibalený, v rozšířeném
režimu jiný); složka s `manifest.csv` se sloupcem `task` vyplní i ostatní
úlohy a údaje pacienta; 2 souhrn dávek s odhadem. Výsledek je
`AssessmentPlan`, dávky spouští hlavní okno. Widget žije na dvou místech
(30. 10. 2026): v kroku 2 stránky Domů (Nové hodnocení… z karty, bez
přeskakování na Pacienty) a v tenkém `AssessmentFromRecordingsDialog`
(Nové hodnocení… na Pacientech); hlásí `accepted` (Spustit) a `rejected`.

Víc pacientů nebo dat najednou (30. 10. 2026, mockup
docs/mockups/domu-pacienti, artboard 4): jediný zdroj je `manifest.csv`
(sloupce pacient, datum, úloha, věk, pohlaví). Když manifest dá víc než
jednoho pacienta nebo víc dat, pole pacienta nahoře zmizí a místo nich je
krok 2 Pacienti (tabulka vyšetření, prázdné datum a věk jdou dopsat jen pro
tento výpočet), dávka je na úlohu a datum (`TaskPlan.visit`) a po skončení
vznikne vyšetření na každou dvojici pacient a datum. Ze struktury složek se
nic neodvozuje (rozhodnuto 30. 10.); kdo manifest nemá, dostane
„Uložit šablonu manifestu…“ se seznamem nahrávek k vyplnění v Excelu.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTableWidgetSelectionRange,
    QVBoxLayout,
    QWidget,
)

from .. import contract
from ..backend import meta, tasks
from ..backend.assessment_build import (
    AssessmentPlan,
    Patient,
    PersonRow,
    new_plan,
    patient_from_scan,
    patients_text,
    people_from_scans,
    split_by_task,
    split_by_visit,
    write_manifest_template,
)
from ..backend.assessments import patient_key
from ..backend.audio import format_duration as audio_duration
from ..backend.norm_build import FolderScan, TaskPlan, norm_protocols, with_language
from ..backend.norms import (
    RECOMMENDED_CONTROLS,  # noqa: F401  (stejná sada textů)
    Norm,
)
from ..backend.protocol import Protocol
from ..i18n import N_, tr
from . import theme
from .assessment_series import SeriesEditor, SeriesPatient
from .new_norm_dialog import SEX_CHOICES, TaskRow, _scrolled, format_duration
from .widgets.clean_table import card, set_text, style_table
from .widgets.inputs import Segmented
from .widgets.status_header import Pill
from .widgets.task_rows import SummaryBar


class ChoiceCard(QFrame):
    """Karta s přepínačem a popisem; klik kamkoli do karty ji vybere, dvojklik potvrdí.

    Bez kolečka přepínače, výběr ukazuje jen modré zvýraznění karty (`indicator=True` ho vrátí)."""

    activated = Signal()  # dvojklik

    def __init__(
        self, title: str, hint: str, parent: QWidget | None = None, *, indicator: bool = False
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setProperty("role", "option")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(2)
        self.radio = QRadioButton(title)
        self.radio.setObjectName("card_title")
        if not indicator:
            self.radio.setStyleSheet(
                "QRadioButton::indicator { width: 0; height: 0; } QRadioButton { spacing: 0; }"
            )
        self.radio.toggled.connect(self._toggled)
        layout.addWidget(self.radio)
        desc = QLabel(hint)
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        desc.setContentsMargins(24 if indicator else 0, 0, 0, 0)
        layout.addWidget(desc)

    def _toggled(self, checked: bool) -> None:
        theme.set_role(self, "chosen" if checked else "option")

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        self.radio.setChecked(True)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        self.radio.setChecked(True)
        self.activated.emit()
        super().mouseDoubleClickEvent(event)


class NewAssessmentChoiceDialog(QDialog):
    """Z nahrávek (spočítá se), nebo z hotových běhů."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Nové hodnocení"))
        self.resize(560, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(12)
        layout.addWidget(QLabel(tr("Odkud vzít nahrávky pacienta?")))
        card1 = ChoiceCard(
            tr("Z nahrávek"),
            tr("Vyberu složky s nahrávkami, spočítá se a hodnocení vznikne samo."),
        )
        card2 = ChoiceCard(tr("Z hotových běhů"), tr("Vyberu nahrávky, které už jsou spočítané."))
        self.recordings = card1.radio
        self.runs = card2.radio
        group = QButtonGroup(self)
        group.addButton(self.recordings)
        group.addButton(self.runs)
        for choice in (card1, card2):
            choice.activated.connect(self.accept)
            layout.addWidget(choice)
        self.recordings.setChecked(True)
        layout.addStretch(1)
        footer = QHBoxLayout()
        cancel = QPushButton(tr("Storno"))
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        footer.addStretch(1)
        ok = QPushButton(tr("Pokračovat ›"))
        theme.set_role(ok, "primary")
        ok.clicked.connect(self.accept)
        footer.addWidget(ok)
        layout.addLayout(footer)

    def choice(self) -> str:
        return "recordings" if self.recordings.isChecked() else "runs"


CHOOSE, SINGLE, SERIES, MULTI = "choose", "single", "series", "multi"
NO_NORM = "none"  # volba „žádná norma“ (stejný klíč jako na stránce Pacienti)
MODE_LABELS = {SINGLE: N_("Jedno vyšetření"), SERIES: N_("Vývoj v čase"), MULTI: N_("Víc pacientů")}


def _visit_text(visit: str) -> str:
    """ISO datum jako „14. 10. 2026“; jiný text beze změny."""
    try:
        day = dt.date.fromisoformat(visit)
    except ValueError:
        return visit
    return f"{day.day}. {day.month}. {day.year}"


def _sex_text(sex: str) -> str:
    return {"f": tr("žena"), "m": tr("muž")}.get(sex, sex)


class AssessmentBuildWidget(QWidget):
    """Průvodce ve dvou krocích: 1 Nahrávky (složky, protokoly), 2 Pacienti (kdo a kdy).

    Mockup https://claude.ai/artifact/LqdUbs3QgkT4YPuHdsKN6z (31. 10. 2026): krok 2 je vždy,
    u víc pacientů tabulka po vyšetřeních (pacient × datum) s úlohami ve sloupcích, věk
    a pohlaví se píšou jednou za vyšetření, nepřiřazené nahrávky se přiřadí přímo tam.
    Úpravy platí jen pro tento výpočet (`_overrides`), do složky se nic nezapisuje."""

    accepted = Signal()  # Spustit: `plan()` je hotový
    rejected = Signal()  # Storno v prvním kroku

    def __init__(
        self,
        protocols: list[Protocol],
        assessments_dir: Path,
        *,
        advanced: bool = False,
        languages: list[str] | None = None,
        language: str = "cs",
        rate_lookup: Callable[[str], float | None] = lambda _slug: None,
        patients_lookup: Callable[[], list[SeriesPatient]] = list,
        run_protocol: Callable[[str], Protocol | None] = lambda _key: None,
        norms_lookup: Callable[[], list[Norm]] = list,
        norm_key_for: Callable[[str], str] = lambda _subject: "",
        mode: str = CHOOSE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dir = assessments_dir
        # hodnocení podle normy (1. 11. 2026, mockup https://claude.ai/artifact/PA7sSRD3cTtK13oDKcM4id,
        # `docs/mockups/hodnoceni-podle-normy/` mimo git): protokoly úloh a jazyk z normy
        self._protocols = list(protocols)
        self._advanced = advanced
        self._norms_lookup = norms_lookup
        self._norm_key_for = norm_key_for
        self._norms: list[Norm] | None = None
        self._norm_touched = False  # uživatel normu vybral sám: předvolba pacienta ji nemění
        self._norm_found: dict[str, Protocol] = {}
        self._norm_missing: dict[str, str] = {}
        self._norm_cache: dict[str, tuple[dict[str, Protocol], dict[str, str]]] = {}
        self._mode = CHOOSE
        self._rate = rate_lookup
        self._people: dict[Path, PersonRow] = {}  # nahrávka → pacient, datum… z manifestu
        self._overrides: dict[Path, dict[str, str]] = {}  # ruční opravy v kroku 2
        # údaje nahrávek se skládají jednou za změnu (dřív u každého vyšetření znovu)
        self._people_version = 0
        self._people_cache: tuple[int, dict[Path, PersonRow]] | None = None
        # předvyplněné hodnoty: nahrávka → sloupec → zdroj (z katalogu, z manifestu…)
        self._suggested: dict[Path, dict[str, str]] = {}
        self._patients_lookup = patients_lookup
        self._catalog: dict[str, SeriesPatient] | None = None
        # nepřiřazené: seznam se při doplňování nepřeskládává, přiřazené zůstanou s ✓
        self._loose_frozen: list[Path] | None = None
        self._loose_rows: list[Path] = []
        self._multi = False
        self._visit_filter = "all"  # all | missing | incomplete
        self._frozen_visits: set[tuple[str, str]] | None = None  # seznam k doplnění
        self._visit_rows: list[tuple[str, str, list[Path]]] = []
        self._filling = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        steps = QHBoxLayout()
        steps.setSpacing(8)
        self.step_pills = [Pill(tr("1 Nahrávky"), "neutral"), Pill(tr("2 Pacienti"), "neutral")]
        steps.addWidget(self.step_pills[0])
        arrow = QLabel("›")
        arrow.setObjectName("muted")
        steps.addWidget(arrow)
        steps.addWidget(self.step_pills[1])
        self._step_arrow = arrow
        steps.addStretch(1)
        # Typ hodnocení (1. 11. 2026, mockup https://claude.ai/artifact/MDbiZZGRgG5Wnyzk4GPVfv):
        # na začátku tři karty, pak přepínač tady nahoře.
        self.mode_switch = Segmented([tr(MODE_LABELS[m]) for m in (SINGLE, SERIES, MULTI)])
        self.mode_switch.currentChanged.connect(
            lambda label: self.set_mode(
                next(m for m in (SINGLE, SERIES, MULTI) if tr(MODE_LABELS[m]) == label)
            )
        )
        steps.addWidget(self.mode_switch)
        outer.addLayout(steps)
        outer.addWidget(self._build_norm_bar())
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        # Kroky v posuvné ploše (31. 10. 2026): obsah kroku nesmí zvětšovat minimální výšku
        # okna (260 nahrávek bez data dalo přes 10 000 px a Windows hlásily setGeometry).
        self.stack.addWidget(
            _scrolled(self._build_sources(protocols, advanced, languages or [], language))
        )
        self.stack.addWidget(_scrolled(self._build_patients()))
        # jeden pacient: formulář nad úlohami a kontrola úloh pod nimi (jedna obrazovka)
        self._sources_layout.insertWidget(0, self.person_box)
        self._sources_layout.insertWidget(self._sources_layout.count() - 1, self.task_checks)
        self.norm_warning = QLabel("")
        self.norm_warning.setWordWrap(True)
        self.norm_warning.setTextFormat(Qt.TextFormat.RichText)
        self.norm_warning.setStyleSheet(
            "QLabel { background: #fff4e5; border: 1px solid #fbd38d; color: #c2410c;"
            " border-radius: 8px; padding: 6px 10px; }"
        )
        self.norm_warning.linkActivated.connect(lambda _l: self._apply_norm())
        self.norm_warning.hide()
        self._sources_layout.insertWidget(self._sources_layout.count() - 1, self.norm_warning)
        self.series = SeriesEditor(
            list(self.rows),
            patients_lookup,
            recursive=lambda: self.recursive.isChecked(),
            protocols=protocols,
            advanced=advanced,
            run_protocol=run_protocol,
        )
        self.series.changed.connect(self._update_buttons)
        self.series.previous_loaded.connect(self._previous_language)
        self.series.subject.currentTextChanged.connect(lambda _t: self._preselect_norm())
        # výchozí protokoly řádků (bez normy) a jejich nabídka; norma je přepíše a vrátí
        self._row_defaults: dict[str, tuple[list[Protocol], str]] = {}
        for task, row in self.rows.items():
            choices = [row.protocol.itemData(i) for i in range(row.protocol.count())]
            current = row.current_protocol()
            self._row_defaults[task] = (
                [c for c in choices if isinstance(c, Protocol)],
                current.name if current is not None else "",
            )
            row.protocol.currentIndexChanged.connect(lambda _i: self._update_norm_warning())
        # Spustit s jiným protokolem než minule se zeptá (testy podstrčí odpověď)
        self.confirm: Callable[[str], bool] = self._ask
        series_page = QWidget()
        series_layout = QVBoxLayout(series_page)
        series_layout.setContentsMargins(0, 0, 0, 0)
        series_layout.addWidget(self.series)
        self.stack.addWidget(series_page)  # 2
        self.stack.addWidget(self._build_chooser())  # 3

        # Patička: Storno, souhrn (úlohy, nahrávky, zvuk, odhad), stav a Spustit hodnocení.
        foot = QFrame()
        foot.setObjectName("card")
        footer = QHBoxLayout(foot)
        footer.setContentsMargins(10, 8, 10, 8)
        footer.setSpacing(12)
        self.back_btn = QPushButton(tr("Storno"))
        self.back_btn.clicked.connect(self.go_back)
        footer.addWidget(self.back_btn)
        self.summary = SummaryBar()
        self.summary.setStyleSheet("QFrame#summarybar { background: transparent; border: none; }")
        footer.addWidget(self.summary, 1)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        footer.addWidget(self.hint)
        self.next_btn = QPushButton(tr("Spustit hodnocení"))
        theme.set_role(self.next_btn, "primary")
        self.next_btn.clicked.connect(self.go_next)
        footer.addWidget(self.next_btn)
        self._foot = foot
        outer.addWidget(foot)
        self.set_mode(mode)

    # --- krok 1 -------------------------------------------------------------------

    def _build_sources(
        self, protocols: list[Protocol], advanced: bool, languages: list[str], language: str
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._sources_layout = layout
        self.language = QComboBox()
        for code in languages or [language]:
            self.language.addItem(contract.language_label(code), code)
        self.language.setCurrentIndex(max(self.language.findData(language), 0))

        tasks_head = QHBoxLayout()
        tasks_head.setSpacing(10)
        tasks_title = QLabel(tr("Úlohy"))
        tasks_title.setObjectName("card_title")
        tasks_head.addWidget(tasks_title)
        tasks_hint = QLabel(tr("složka s nahrávkami pro každou úlohu"))
        tasks_hint.setObjectName("muted")
        tasks_head.addWidget(tasks_hint)
        info = QLabel("ⓘ")
        info.setObjectName("muted")
        info.setToolTip(
            tr(
                "Když vybraná složka má manifest.csv se sloupcem task, nahrávky se rozdělí "
                "podle něj a vyplní se i ostatní úlohy; s pacientem a datem v manifestu jde "
                "spočítat víc pacientů nebo vyšetření najednou. Protokol je přibalený pro "
                "danou úlohu, jiný jde vybrat jen v rozšířeném režimu. Do složek "
                "s nahrávkami se nic nezapisuje. Po výpočtu se hodnocení založí samo."
            )
        )
        tasks_head.addWidget(info)
        tasks_head.addStretch(1)
        self.language_label = QLabel(tr("Jazyk"))
        self.language_label.setObjectName("muted")
        tasks_head.addWidget(self.language_label)
        tasks_head.addWidget(self.language)
        self._tasks_head = tasks_head
        layout.addLayout(tasks_head)
        # řádky úloh jako na Analýze (widgets/task_rows.py), pod sebou
        rows_box = QWidget()
        rows_layout = QVBoxLayout(rows_box)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(6)
        self.rows: dict[str, TaskRow] = {}
        # úlohy knihovny a za nimi vlastní úlohy z registru (z norem a nastavení)
        customs = {t.key: t for t in tasks.registry().custom}
        for task in tasks.registry().keys():
            row = TaskRow(task, protocols, advanced, custom=customs.get(task))
            row.remove.hide()
            row.folder.editingFinished.connect(lambda row=row: self._rescan(row))
            row.browse.clicked.connect(lambda _=False, row=row: self._browse(row))
            rows_layout.addWidget(row.widget)
            self.rows[task] = row
        layout.addWidget(rows_box)
        options = QHBoxLayout()
        self.recursive = QCheckBox(tr("i podsložky"))
        self.recursive.toggled.connect(self._rescan_all)
        options.addWidget(self.recursive)
        options.addStretch(1)
        self.template_btn = QPushButton(tr("Uložit šablonu manifestu…"))
        self.template_btn.setFlat(True)
        self.template_btn.setToolTip(
            tr(
                "CSV se seznamem nalezených nahrávek a prázdnými sloupci pacient, datum, "
                "věk, pohlaví k vyplnění v Excelu; pro víc pacientů nebo dat najednou"
            )
        )
        self.template_btn.setEnabled(False)
        self.template_btn.clicked.connect(lambda: self.save_template())
        options.addWidget(self.template_btn)
        layout.addLayout(options)
        layout.addStretch(1)
        return page

    def _build_patients(self) -> QWidget:
        """Krok 2: jeden pacient formulářem, víc pacientů tabulkou vyšetření."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # jeden pacient
        self.person_box = QFrame()
        self.person_box.setObjectName("card")
        person_layout = QVBoxLayout(self.person_box)
        person_layout.setContentsMargins(14, 12, 14, 12)
        person_layout.setSpacing(8)
        person = QGridLayout()
        person.setHorizontalSpacing(10)
        self.subject = QLineEdit()
        self.subject.setPlaceholderText(tr("ID pacienta"))
        self.subject.textChanged.connect(self._update_buttons)
        self.subject.textChanged.connect(lambda _t: self._preselect_norm())
        self.age = QLineEdit()
        self.age.setPlaceholderText(tr("roky"))
        self.age.setMaximumWidth(80)
        self.age.textChanged.connect(self._update_person_marks)
        self.sex = QComboBox()
        for code, label in SEX_CHOICES:
            self.sex.addItem(tr(label), code)
        self.sex.currentIndexChanged.connect(lambda _i: self._update_person_marks())
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
        person_layout.addLayout(person)
        self.prefill_note = QLabel("")
        self.prefill_note.setObjectName("muted")
        self.prefill_note.setWordWrap(True)
        self.prefill_note.hide()
        person_layout.addWidget(self.prefill_note)
        self.task_checks = QLabel("")
        self.task_checks.setTextFormat(Qt.TextFormat.RichText)
        self.task_checks.setWordWrap(True)

        # víc pacientů: vyšetření (pacient × datum), úlohy ve sloupcích
        self.people_box = QWidget()
        people_layout = QVBoxLayout(self.people_box)
        people_layout.setContentsMargins(0, 0, 0, 0)
        people_layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel(tr("Vyšetření"))
        title.setObjectName("card_title")
        head.addWidget(title)
        self.people_summary = QLabel("")
        self.people_summary.setObjectName("muted")
        self.people_summary.setToolTip(
            tr(
                "Pacient, datum vyšetření, úloha, věk a pohlaví jsou z manifest.csv ve složce. "
                "Co chybí, dopište tady (platí jen pro tento výpočet). Nahrávka bez pacienta "
                "nebo data se spočítá, ale do žádného vyšetření se nezaloží."
            )
        )
        head.addWidget(self.people_summary)
        head.addStretch(1)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self.filter_buttons: dict[str, QPushButton] = {}
        for key in ("all", "missing", "incomplete"):
            btn = QPushButton("")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, k=key: self.set_visit_filter(k))
            self.filter_group.addButton(btn)
            head.addWidget(btn)
            self.filter_buttons[key] = btn
        self.filter_buttons["all"].setChecked(True)
        self.filter_buttons["missing"].setToolTip(
            tr("Vyšetření bez věku nebo pohlaví; po doplnění v seznamu zůstanou s fajfkou.")
        )
        self.filter_buttons["incomplete"].setToolTip(
            tr("Vyšetření, kterým chybí některá úloha; dimenze, které ji potřebují, nebudou.")
        )
        people_layout.addLayout(head)
        self.visits_table = QTableWidget()
        self.visits_table.itemChanged.connect(self._visit_edited)
        self.visits_table.setMinimumHeight(230)  # aspoň čtyři vyšetření i vedle žlutého oddílu
        people_layout.addWidget(card(self.visits_table), 1)
        self.totals = QLabel("")
        self.totals.setObjectName("muted")
        people_layout.addWidget(self.totals)
        # Nepřiřazené nahrávky (1. 11. 2026, mockup https://claude.ai/artifact/RXmri6zs9jpW7EhVp89Pfo,
        # `docs/mockups/neprirazene-tabulka/` mimo git): jedna tabulka (úloha, nahrávka,
        # pacient, datum, věk, pohlaví) s úpravou dvojklikem a výběrem více řádků pro
        # Vyplnit…; dřív widgety na řádek a přestavba po každém poli (u stovek pomalé).
        self.unassigned_box = QFrame()
        self.unassigned_box.setObjectName("unassigned")
        self.unassigned_box.setStyleSheet(
            "QFrame#unassigned { background: #fffbeb; border: 1px solid #fcd34d;"
            " border-radius: 10px; }"
            "QFrame#unassigned QLabel { background: transparent; border: none; }"
        )
        box_layout = QVBoxLayout(self.unassigned_box)
        box_layout.setContentsMargins(12, 10, 12, 10)
        box_layout.setSpacing(8)
        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        self.unassigned_head = QLabel("")
        head_row.addWidget(self.unassigned_head)
        self.unassigned_hint = QLabel(tr("vyberte řádky a Vyplnit…, nebo dvojklik na buňku"))
        self.unassigned_hint.setObjectName("muted")
        head_row.addWidget(self.unassigned_hint)
        head_row.addStretch(1)
        self.selection_label = QLabel("")
        self.selection_label.setStyleSheet("color: #1d4ed8; font-weight: 600;")
        head_row.addWidget(self.selection_label)
        self.fill_btn = QPushButton(tr("Vyplnit…"))
        theme.set_role(self.fill_btn, "primary")
        self.fill_btn.clicked.connect(lambda: self.fill_selected())
        head_row.addWidget(self.fill_btn)
        self.clear_selection_btn = QPushButton(tr("✕ zrušit výběr"))
        self.clear_selection_btn.setFlat(True)
        self.clear_selection_btn.clicked.connect(lambda: self.unassigned_table.clearSelection())
        head_row.addWidget(self.clear_selection_btn)
        self.done_btn = QPushButton(tr("Hotovo · skrýt přiřazené"))
        self.done_btn.clicked.connect(lambda: self.end_unassigned())
        head_row.addWidget(self.done_btn)
        box_layout.addLayout(head_row)
        self.unassigned_table = QTableWidget()
        self.unassigned_table.setItemDelegate(_UnassignedDelegate(self))
        self.unassigned_table.itemSelectionChanged.connect(self._unassigned_selection)
        box_layout.addWidget(self.unassigned_table)
        people_layout.addWidget(self.unassigned_box)
        layout.addWidget(self.people_box, 1)
        layout.addStretch(0)
        return page

    def _build_chooser(self) -> QWidget:
        """Tři karty typu hodnocení; každá vede do svého formuláře."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        question = QLabel(tr("Co chcete hodnotit?"))
        question.setObjectName("muted")
        layout.addWidget(question)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.mode_cards: dict[str, QPushButton] = {}
        texts = {
            SINGLE: tr("Jeden pacient, jedno datum. Složka s nahrávkami pro každou úlohu."),
            SERIES: tr(
                "Jeden pacient, víc vyšetření. Pro každé datum složky po úlohách; věk se "
                "dopočítá z roku narození."
            ),
            MULTI: tr("Studie nebo skupina najednou. Složka s manifestem (pacient, datum, úloha)."),
        }
        for mode in (SINGLE, SERIES, MULTI):
            box = QFrame()
            box.setObjectName("card")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(16, 14, 16, 14)
            box_layout.setSpacing(8)
            title = QLabel(tr(MODE_LABELS[mode]))
            title.setObjectName("card_title")
            box_layout.addWidget(title)
            text = QLabel(texts[mode])
            text.setObjectName("muted")
            text.setWordWrap(True)
            box_layout.addWidget(text, 1)
            btn = QPushButton(tr("Začít"))
            theme.set_role(btn, "primary")
            btn.clicked.connect(lambda _c=False, m=mode: self.set_mode(m))
            box_layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
            row.addWidget(box, 1)
            self.mode_cards[mode] = btn
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Typ hodnocení: volba (karty), jedno vyšetření, vývoj v čase, víc pacientů."""
        self._mode = mode
        choosing = mode == CHOOSE
        self.mode_switch.setVisible(not choosing)
        if not choosing:
            self.mode_switch.blockSignals(True)
            self.mode_switch.set_current(tr(MODE_LABELS[mode]))
            self.mode_switch.blockSignals(False)
        for widget in (*self.step_pills, self._step_arrow):
            widget.setVisible(mode == MULTI)
        self.person_box.setVisible(mode == SINGLE)
        self.task_checks.setVisible(mode == SINGLE)
        self.template_btn.setVisible(mode == MULTI)
        self._foot.setVisible(not choosing)
        self._multi = mode == MULTI
        if mode == MULTI:
            self._refresh_people()
        if choosing:
            self.stack.setCurrentIndex(3)
        elif mode == SERIES:
            self.series.reload_patients()
            self.stack.setCurrentIndex(2)
        else:
            self.stack.setCurrentIndex(0)
        self._place_language(mode == SERIES)
        self.norm_bar.setVisible(not choosing and self._has_norms())
        if not choosing:
            self._preselect_norm()
        self.back_btn.setText(tr("Storno"))
        if mode == SINGLE:
            self._fill_task_checks()
            self._update_person_marks()
        self._update_buttons()

    def _place_language(self, series: bool) -> None:
        """Výběr jazyka je jeden: u Vývoje v čase nad mřížkou, jinak u úloh."""
        target = self.series.head_extra if series else self._tasks_head
        if self.language.parentWidget() is not None and target.indexOf(self.language) >= 0:
            return
        for widget in (self.language_label, self.language):
            for layout in (self.series.head_extra, self._tasks_head):
                layout.removeWidget(widget)
            target.addWidget(widget)
        if not series:
            self.language_label.show()
            self.language.show()

    def _previous_language(self, code: str) -> None:
        """Jazyk z minulého vyšetření pacienta (Vývoj v čase); norma má přednost."""
        norm = self.selected_norm()
        if norm is not None and norm.info.language:
            return
        index = self.language.findData(code) if code else -1
        if index >= 0:
            self.language.setCurrentIndex(index)

    def _ask(self, text: str) -> bool:
        answer = QMessageBox.question(
            self,
            tr("Jiný protokol než minule"),
            text + "\n\n" + tr("Přesto spustit?"),
        )
        return answer == QMessageBox.StandardButton.Yes

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
        if not text:
            row.scan = FolderScan([])
            row.count.setText("–")
            row.refresh_look()
            self._update_buttons()
            return
        split = split_by_task(Path(text), row.task, recursive=self.recursive.isChecked())
        row.scan = split.get(row.task, FolderScan([]))
        # manifest se sloupcem task: tatáž složka vyplní i ostatní úlohy
        for task, scan in split.items():
            other = self.rows.get(task)
            if other is None or other is row or not scan.recordings:
                continue
            if not other.folder.text().strip() or other.folder.text().strip() == text:
                other.folder.setText(text)
                other.scan = scan
                self._show_count(other)
        self._show_count(row)
        self._refresh_people()
        if not self._multi:
            self._prefill(row.scan)
        self._update_buttons()

    def _show_count(self, row: TaskRow) -> None:
        n = len(row.scan.recordings)
        row.count.setText(f"{n} ✓" if n else tr("žádné"))
        row.refresh_look()

    def _rescan_all(self) -> None:
        for row in self.rows.values():
            if row.folder.text().strip():
                self._rescan(row)

    def _prefill(self, scan: FolderScan) -> None:
        """Prázdné údaje pacienta z manifestu složky; vyplněné se nepřepisují."""
        patient = patient_from_scan(scan)
        filled = []
        if patient.subject and not self.subject.text().strip():
            self.subject.setText(patient.subject)
            filled.append(tr("pacient"))
        if patient.age is not None and not self.age.text().strip():
            self.age.setText(f"{patient.age:g}")
            filled.append(tr("věk"))
        if patient.sex and not self.sex.currentData():
            self.sex.setCurrentIndex(max(0, self.sex.findData(patient.sex)))
            filled.append(tr("pohlaví"))
        if patient.date and not getattr(self, "_date_set", False):
            try:
                day = dt.date.fromisoformat(patient.date)
                self.date.setDate(QDate(day.year, day.month, day.day))
                self._date_set = True
                filled.append(tr("datum"))
            except ValueError:
                pass
        if filled and scan.manifest is not None:
            self.prefill_note.setText(
                tr("Z {file}: {what}. Jde přepsat.").format(
                    file=scan.manifest.path.name, what=", ".join(filled)
                )
            )
            self.prefill_note.show()

    def prefill(self, subject: str, age: float | None, sex: str) -> None:
        """Další vyšetření téhož pacienta: Vývoj v čase s pacientem (1. 11. 2026); pole
        jednoho vyšetření se vyplní taky, kdyby uživatel typ přepnul."""
        self.set_mode(SERIES)
        self.series.set_patient(subject, age, sex)
        self.subject.setText(subject)
        self.age.setText("" if age is None else f"{age:g}")
        self.sex.setCurrentIndex(max(0, self.sex.findData(sex)))
        self._update_buttons()

    def active_rows(self) -> list[TaskRow]:
        return [
            r for r in self.rows.values() if r.scan.recordings and r.current_protocol() is not None
        ]

    # --- víc pacientů a dat --------------------------------------------------------

    def _recording_tasks(self) -> dict[Path, str]:
        """Nahrávka → úloha podle řádku, ve kterém se našla (drží se, dokud se nezmění
        výsledky prohledání složek; `resolve` u stovek nahrávek stálo sekundy)."""
        stamp = tuple(id(row.scan) for row in self.rows.values())
        cached = getattr(self, "_rec_tasks_cache", None)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        out: dict[Path, str] = {}
        for row in self.rows.values():
            for rec in row.scan.recordings:
                try:
                    key = rec.resolve()  # stejný klíč jako `people_from_scans`
                except OSError:
                    key = rec
                out.setdefault(key, row.task)
        self._rec_tasks_cache = (stamp, out)
        return out

    def _refresh_people(self) -> None:
        scans = {task: row.scan for task, row in self.rows.items() if row.scan.recordings}
        self._people = people_from_scans(scans)
        self._overrides = {k: v for k, v in self._overrides.items() if k in self._people}
        self._loose_frozen = None  # jiné nahrávky: seznam nepřiřazených znovu
        self._catalog = None
        self._people_changed()
        # Typ si uživatel vybral sám (1. 11. 2026): manifest u jednoho vyšetření jen
        # předvyplní, víc pacientů je jen v režimu Víc pacientů.
        self._multi = self._mode == MULTI
        self.template_btn.setEnabled(bool(scans))
        if self._multi:
            self._fill_patients()

    def _people_changed(self) -> None:
        self._people_version += 1

    def _catalog_patients(self) -> dict[str, SeriesPatient]:
        if self._catalog is None:
            try:
                self._catalog = {patient_key(p.subject): p for p in self._patients_lookup()}
            except Exception:  # noqa: BLE001  katalog je jen pomůcka k předvyplnění
                self._catalog = {}
        return self._catalog

    def _merged_people(self) -> dict[Path, PersonRow]:
        """Údaje z manifestu přepsané ručními opravami z kroku 2, doplněné o předvyplnění.

        Předvyplní se jen to, co je bezpečné (rozhodnuto 1. 11. 2026): pohlaví z jiné
        nahrávky téhož pacienta nebo z katalogu, věk z téhož vyšetření (pacient a datum)
        nebo z roku narození v katalogu. Ruční hodnota vždy vyhraje. Výsledek se drží,
        dokud se údaje nezmění (`_people_changed`)."""
        if self._people_cache is not None and self._people_cache[0] == self._people_version:
            return self._people_cache[1]
        out: dict[Path, PersonRow] = {}
        for path, person in self._people.items():
            row = self._overrides.get(path, {})
            out[path] = PersonRow(
                subject=row.get(meta.SUBJECT, person.subject),
                visit=row.get(meta.VISIT, person.visit),
                age=meta.parse_age(row[meta.AGE]) if meta.AGE in row else person.age,
                sex=meta.parse_sex(row[meta.SEX]) if meta.SEX in row else person.sex,
            )
        self._suggested = {}
        sexes: dict[str, str] = {}
        ages: dict[tuple[str, str], float] = {}
        for person in out.values():
            key = patient_key(person.subject)
            if key and person.sex:
                sexes.setdefault(key, person.sex)
            if key and person.visit and person.age is not None:
                ages.setdefault((key, person.visit), person.age)
        lacking = any(not p.sex or p.age is None for p in out.values())
        catalog = self._catalog_patients() if lacking else {}
        for path, person in out.items():
            key = patient_key(person.subject)
            if not key:
                continue
            row = self._overrides.get(path, {})
            known = catalog.get(key)
            if not person.sex and meta.SEX not in row:
                if key in sexes:
                    person.sex = sexes[key]
                    self._suggested.setdefault(path, {})[meta.SEX] = tr("z manifestu")
                elif known is not None and known.sex:
                    person.sex = known.sex
                    self._suggested.setdefault(path, {})[meta.SEX] = tr("z katalogu")
            if person.age is None and meta.AGE not in row and person.visit:
                if (key, person.visit) in ages:
                    person.age = ages[(key, person.visit)]
                    self._suggested.setdefault(path, {})[meta.AGE] = tr("z téhož vyšetření")
                elif known is not None and known.birth_year and person.visit[:4].isdigit():
                    person.age = float(int(person.visit[:4]) - known.birth_year)
                    self._suggested.setdefault(path, {})[meta.AGE] = tr(
                        "z roku narození {year} v katalogu"
                    ).format(year=known.birth_year)
        self._people_cache = (self._people_version, out)
        return out

    def people(self) -> dict[Path, PersonRow]:
        """Údaje nahrávek: víc pacientů z manifestu a oprav, vývoj v čase z mřížky."""
        if self._mode == SERIES:
            return self.series.people()
        return self._merged_people() if self._multi else {}

    def is_multi(self) -> bool:
        return self._multi

    def people_counts(self) -> tuple[int, int, int, int]:
        """(pacientů, vyšetření, nahrávek, bez pacienta nebo data)."""
        people = self._merged_people()
        complete = {(p.subject, p.visit) for p in people.values() if p.subject and p.visit}
        missing = sum(1 for p in people.values() if not (p.subject and p.visit))
        return len({s for s, _v in complete}), len(complete), len(people), missing

    def set_person(self, path: Path, **values: object) -> None:
        """Ruční oprava údajů nahrávky (pacient, visit, věk, pohlaví) jen pro tento výpočet."""
        row = self._overrides.setdefault(path, {})
        for name, value in values.items():
            text = "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)
            if name == meta.VISIT:
                text = meta.normalize_visit(text) or text
            row[name] = text.strip()
        self._people_changed()
        self._after_people_edit()

    def _after_people_edit(self) -> None:
        if self._multi:
            self._fill_patients()
        self._update_buttons()

    # --- krok 2: vyšetření ---------------------------------------------------------

    def _visits(self) -> dict[tuple[str, str], list[Path]]:
        """(pacient, datum) → nahrávky; jen s pacientem i datem, v pořadí pacienta a data."""
        people = self._merged_people()
        out: dict[tuple[str, str], list[Path]] = {}
        for path, person in sorted(people.items(), key=lambda kv: (kv[1].subject, kv[1].visit)):
            if person.subject and person.visit:
                out.setdefault((person.subject, person.visit), []).append(path)
        return out

    def _active_tasks(self) -> list[str]:
        return [row.task for row in self.active_rows()]

    def _visit_info(self, paths: list[Path]) -> tuple[float | None, str]:
        people = self._merged_people()
        ages = [people[p].age for p in paths if people[p].age is not None]
        sexes = [people[p].sex for p in paths if people[p].sex]
        return (ages[0] if ages else None), (sexes[0] if sexes else "")

    def _visit_suggested(self, paths: list[Path], column: str) -> str:
        """Zdroj předvyplněné hodnoty vyšetření (když ji mají předvyplněnou všechny nahrávky)."""
        sources = [self._suggested.get(p, {}).get(column, "") for p in paths]
        return sources[0] if sources and all(sources) else ""

    def _visit_missing(self, paths: list[Path]) -> bool:
        age, sex = self._visit_info(paths)
        return age is None or not sex

    def _visit_incomplete(self, paths: list[Path]) -> bool:
        rec_tasks = self._recording_tasks()
        have = {rec_tasks.get(p, "") for p in paths}
        return any(t not in have for t in self._active_tasks())

    def set_visit_filter(self, key: str) -> None:
        """Všechna · chybí údaje (zamrazený seznam k doplnění) · neúplná sada úloh."""
        self._visit_filter = key
        visits = self._visits()
        self._frozen_visits = (
            {k for k, paths in visits.items() if self._visit_missing(paths)}
            if key == "missing"
            else None
        )
        self.filter_buttons[key].setChecked(True)
        self._fill_patients()

    def _fill_patients(self) -> None:
        """Tabulka vyšetření, souhrn, filtr, celkem po úlohách a nepřiřazené nahrávky."""
        visits = self._visits()
        task_keys = self._active_tasks()
        rec_tasks = self._recording_tasks()
        missing = [k for k, paths in visits.items() if self._visit_missing(paths)]
        incomplete = [k for k, paths in visits.items() if self._visit_incomplete(paths)]
        self.filter_buttons["all"].setText(tr("všechna {n}").format(n=len(visits)))
        self.filter_buttons["missing"].setText(tr("chybí údaje {n}").format(n=len(missing)))
        self.filter_buttons["incomplete"].setText(
            tr("neúplná sada úloh {n}").format(n=len(incomplete))
        )
        if self._visit_filter == "missing" and self._frozen_visits is not None:
            shown = [k for k in visits if k in self._frozen_visits]
        elif self._visit_filter == "incomplete":
            shown = incomplete
        else:
            shown = list(visits)
        headers = [tr("pacient"), tr("datum vyšetření"), tr("věk"), tr("pohlaví")]
        headers += [tasks.label(t) for t in task_keys]
        table = self.visits_table
        self._filling = True
        table.blockSignals(True)
        style_table(table, headers)
        for c in range(len(headers)):  # šířky jednou na konci (resizeColumnsToContents)
            table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        table.setRowCount(len(shown))
        self._visit_rows = []
        for r, key in enumerate(shown):
            subject, visit = key
            paths = visits[key]
            self._visit_rows.append((subject, visit, paths))
            age, sex = self._visit_info(paths)
            done = self._frozen_visits is not None and not self._visit_missing(paths)
            set_text(table, r, 0, subject + ("  ✓" if done else ""), bold=True)
            set_text(table, r, 1, _visit_text(visit))
            for c, text, column in (
                (2, "" if age is None else f"{age:g}", meta.AGE),
                (3, _sex_text(sex), meta.SEX),
            ):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                source = self._visit_suggested(paths, column) if text else ""
                if not text:
                    item.setBackground(QColor(theme.WARN_SOFT))
                    item.setToolTip(tr("chybí, dopište (platí pro celé vyšetření)"))
                elif source:
                    _mark_suggested(item, source)
                table.setItem(r, c, item)
            for c, task in enumerate(task_keys, start=4):
                names = [p.name for p in paths if rec_tasks.get(p) == task]
                item = QTableWidgetItem(str(len(names)) if names else "–")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if names:
                    item.setForeground(QColor("#1d4ed8"))
                    item.setToolTip("\n".join(names))
                else:
                    item.setForeground(QColor("#b45309"))
                    item.setToolTip(
                        tr("Úloha chybí; dimenze, které ji potřebují, v pavouku nebudou.")
                    )
                table.setItem(r, c, item)
        table.resizeColumnsToContents()
        # sloupce podle obsahu, volné místo až za posledním (roztažený poslední sloupec
        # odsouval čísla úlohy daleko od nadpisu)
        table.horizontalHeader().setStretchLastSection(False)
        table.blockSignals(False)
        self._filling = False
        patients, n_visits, n, unassigned = self.people_counts()
        self.people_summary.setText(
            tr("{p} · {v} vyšetření").format(p=patients_text(patients), v=n_visits)
        )
        counts = {t: 0 for t in task_keys}
        for paths in visits.values():
            for path in paths:
                if rec_tasks.get(path) in counts:
                    counts[rec_tasks[path]] += 1
        self.totals.setText(
            tr("celkem: {parts}").format(
                parts=" · ".join(f"{tasks.label(t)} {k}" for t, k in counts.items())
            )
        )
        self._fill_unassigned()

    def _loose(self, people: dict[Path, PersonRow]) -> list[Path]:
        rec_tasks = self._recording_tasks()
        return sorted(
            (p for p, person in people.items() if not (person.subject and person.visit)),
            key=lambda p: (tasks.task_order(rec_tasks.get(p, "")), p.name),
        )

    def unassigned(self) -> list[Path]:
        """Nahrávky v žlutém oddílu (i ty už přiřazené, dokud nepadne Hotovo)."""
        return list(self._loose_rows)

    def _fill_unassigned(self) -> None:
        """Žlutý oddíl: tabulka nahrávek bez pacienta nebo data; přiřazené zůstanou s ✓."""
        people = self._merged_people()
        if self._loose_frozen is None:
            self._loose_frozen = self._loose(people) or None
        rows = [p for p in self._loose_frozen or [] if p in people]
        self._loose_rows = rows
        self.unassigned_box.setVisible(bool(rows))
        if not rows:
            return
        table = self.unassigned_table
        selected = set(self.selected_unassigned())
        scroll = table.verticalScrollBar().value()
        rec_tasks = self._recording_tasks()
        headers = [
            tr("úloha"),
            tr("nahrávka"),
            tr("pacient"),
            tr("datum"),
            tr("věk"),
            tr("pohlaví"),
        ]
        self._filling = True
        table.blockSignals(True)
        table.setUpdatesEnabled(False)
        style_table(table, headers, selectable=True)
        # šířky se měří jednou na konci: s ResizeToContents stál každý setItem 11 ms
        # (400 nahrávek = 30 s na jednu změnu)
        for c in range(len(headers)):
            table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed
        )
        table.setRowCount(len(rows))
        done = 0
        for r, path in enumerate(rows):
            person = people[path]
            assigned = bool(person.subject and person.visit)
            done += assigned
            task = QTableWidgetItem(tasks.label(rec_tasks.get(path, "")))
            task.setForeground(QColor("#1d4ed8"))
            table.setItem(r, 0, task)
            name = QTableWidgetItem(path.name + ("  ✓" if assigned else ""))
            name.setToolTip(str(path))
            font = name.font()
            font.setBold(True)
            name.setFont(font)
            if assigned:
                name.setForeground(QColor(theme.OK))
            table.setItem(r, 1, name)
            values = (
                (2, meta.SUBJECT, person.subject, True),
                (3, meta.VISIT, person.visit, True),
                (4, meta.AGE, "" if person.age is None else f"{person.age:g}", False),
                (5, meta.SEX, person.sex, False),
            )
            for c, column, value, required in values:
                shown = _visit_text(value) if column == meta.VISIT else value
                if column == meta.SEX:
                    shown = _sex_text(value)
                item = QTableWidgetItem(shown or (tr("doplnit") if required else "–"))
                item.setData(VALUE_ROLE, shown)
                source = self._suggested.get(path, {}).get(column, "")
                if not value and required:
                    item.setBackground(QColor(theme.WARN_SOFT))
                    item.setForeground(QColor("#a16207"))
                elif not value:
                    item.setForeground(QColor(theme.MUTED))
                elif source:
                    _mark_suggested(item, source)
                table.setItem(r, c, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        for r, path in enumerate(rows):
            if path in selected:
                table.setRangeSelected(
                    QTableWidgetSelectionRange(r, 0, r, table.columnCount() - 1), True
                )
        table.setUpdatesEnabled(True)
        table.blockSignals(False)
        self._filling = False
        # nejvýš šest řádků, zbytek posuvníkem (seznam nesmí roztahovat stránku)
        shown_rows = min(len(rows), 6)
        header = table.horizontalHeader().sizeHint().height()
        table.setFixedHeight(header + shown_rows * table.verticalHeader().defaultSectionSize() + 4)
        table.verticalScrollBar().setValue(scroll)
        if done:
            self.unassigned_head.setText(
                tr("přiřazeno {done} z {n}").format(done=done, n=len(rows))
            )
        else:
            self.unassigned_head.setText(
                tr(
                    "⚠ {n} nahrávek není přiřazených k vyšetření · bez pacienta nebo data se "
                    "jen spočítají"
                ).format(n=len(rows))
            )
        self.unassigned_head.setStyleSheet(
            f"color: {theme.OK if done else '#92400e'}; font-weight: 600;"
        )
        self.done_btn.setVisible(done > 0)
        self._unassigned_selection()

    def selected_unassigned(self) -> list[Path]:
        rows = sorted({i.row() for i in self.unassigned_table.selectedIndexes()})
        return [self._loose_rows[r] for r in rows if 0 <= r < len(self._loose_rows)]

    def _unassigned_selection(self) -> None:
        n = len(self.selected_unassigned())
        self.selection_label.setText(tr("vybráno {n}").format(n=n) if n else "")
        for widget in (self.selection_label, self.fill_btn, self.clear_selection_btn):
            widget.setVisible(bool(n))
        self.unassigned_hint.setVisible(not n)

    def end_unassigned(self) -> None:
        """Hotovo: přiřazené zmizí, v oddílu zůstanou jen nepřiřazené."""
        self._loose_frozen = None
        self._fill_unassigned()

    def fill_unassigned(
        self,
        paths: list[Path],
        *,
        subject: str | None = None,
        visit: str | None = None,
        age: str | None = None,
        sex: str | None = None,
    ) -> int:
        """Jedna hodnota pro vybrané nahrávky (None = nechat); jedna přestavba na konci.
        Vrátí počet změněných hodnot."""
        values = {meta.SUBJECT: subject, meta.VISIT: visit, meta.AGE: age, meta.SEX: sex}
        changed = 0
        for path in paths:
            row = self._overrides.setdefault(path, {})
            for column, value in values.items():
                if value is None:
                    continue
                text = " ".join(value.split())
                if column == meta.VISIT:
                    text = meta.normalize_visit(text) or text
                elif column in (meta.AGE, meta.SEX) and text:
                    text = meta.normalize_value(column, text)
                if row.get(column) != text:
                    row[column] = text
                    changed += 1
        if changed:
            self._people_changed()
            self._after_people_edit()
        return changed

    def fill_selected(self) -> None:
        """Vyplnit…: malé okno, prázdné pole nic nezmění."""
        paths = self.selected_unassigned()
        if not paths:
            return
        dialog = FillPeopleDialog(
            len(paths), self._subject_choices(), self._catalog_patients(), self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.fill_unassigned(paths, **dialog.values())

    def _subject_choices(self) -> list[str]:
        people = self._merged_people()
        return sorted(
            {p.subject for p in people.values() if p.subject}
            | {p.subject for p in self._catalog_patients().values()}
        )

    def _commit_cell(self, row: int, column: int, text: str) -> None:
        """Úprava buňky v žlutém oddílu (dvojklik) pro ten jeden řádek."""
        name = {2: "subject", 3: "visit", 4: "age", 5: "sex"}.get(column)
        if name is None or not (0 <= row < len(self._loose_rows)):
            return
        path = self._loose_rows[row]
        # přestavba až po zavření editoru (jinak se maže editor, ze kterého signál přišel)
        QTimer.singleShot(0, lambda: self.fill_unassigned([path], **{name: text}))

    def _visit_edited(self, item: QTableWidgetItem) -> None:
        """Věk nebo pohlaví v řádku vyšetření platí pro všechny jeho nahrávky."""
        if self._filling or item.column() not in (2, 3):
            return
        r = item.row()
        if not (0 <= r < len(self._visit_rows)):
            return
        _subject, _visit, paths = self._visit_rows[r]
        column = meta.AGE if item.column() == 2 else meta.SEX
        text = item.text().strip()
        value = meta.normalize_value(column, text) if text else ""
        for path in paths:
            self._overrides.setdefault(path, {})[column] = value
        self._people_changed()
        QTimer.singleShot(0, self._after_people_edit)

    def set_visit_value(self, subject: str, visit: str, column: str, value: str) -> None:
        """Věk nebo pohlaví jednoho vyšetření (jako úprava v tabulce); pro testy a skripty."""
        paths = self._visits().get((subject, visit), [])
        for path in paths:
            self._overrides.setdefault(path, {})[column] = meta.normalize_value(column, value)
        self._people_changed()
        self._after_people_edit()

    def _update_person_marks(self) -> None:
        """Jeden pacient: prázdný věk a pohlaví žlutě (Spustit jde i bez nich)."""
        for widget, empty in (
            (self.age, not self.age.text().strip()),
            (self.sex, not self.sex.currentData()),
        ):
            widget.setStyleSheet(
                f"background: {theme.WARN_SOFT}; border-color: #f59e0b;" if empty else ""
            )

    def _fill_task_checks(self) -> None:
        """Jeden pacient: úlohy vyšetření jedním řádkem, chybějící oranžově."""
        parts = []
        for task, row in self.rows.items():
            n = len(row.scan.recordings)
            if n:
                parts.append(f"{tasks.label(task)} <b style='color:#1d4ed8'>{n}</b>")
            elif not tasks.is_custom(task):
                parts.append(f"<span style='color:#b45309'>{tasks.label(task)} –</span>")
        self.task_checks.setText(
            f"<b>{tr('Úlohy vyšetření')}</b>&nbsp;&nbsp;" + " &nbsp;·&nbsp; ".join(parts)
        )
        self.task_checks.setToolTip(
            tr("Chybějící úloha: dimenze, které ji potřebují, v pavouku nebudou.")
        )

    def save_template(self, target: Path | None = None) -> Path | None:
        """Šablona manifestu se seznamem nalezených nahrávek, kam uživatel ukáže."""
        rec_tasks = self._recording_tasks()
        if not rec_tasks:
            return None
        folders = [
            Path(r.folder.text().strip()) for r in self.rows.values() if r.folder.text().strip()
        ]
        base = folders[0] if folders else next(iter(rec_tasks)).parent
        if target is None:
            text, _filter = QFileDialog.getSaveFileName(
                self, tr("Uložit šablonu manifestu"), str(base / "manifest.csv"), "CSV (*.csv)"
            )
            if not text:
                return None
            target = Path(text)
        return write_manifest_template(target, rec_tasks, base)

    def _task_plans(self) -> list[TaskPlan]:
        language = str(self.language.currentData() or "")
        plans: list[TaskPlan] = []
        if self._mode == SERIES:
            # vývoj v čase: dávka na úlohu a datum, protokol z mřížky (jako minule)
            for task, by_date in self.series.task_inputs().items():
                proto = self.series.protocol_for(task)
                if proto is None:
                    continue
                protocol = with_language(proto, language).to_dict()
                for visit, recs in sorted(by_date.items()):
                    plans.append(TaskPlan(task, protocol, list(recs), visit=visit))
            return plans
        for row in self.active_rows():
            proto = row.current_protocol()
            assert proto is not None
            protocol = with_language(proto, language).to_dict()
            if self._multi:
                people = self._merged_people()
                for visit, recs in split_by_visit(list(row.scan.recordings), people).items():
                    plans.append(TaskPlan(row.task, protocol, recs, visit=visit))
            else:
                plans.append(TaskPlan(row.task, protocol, list(row.scan.recordings)))
        return plans

    # --- krok 2 -------------------------------------------------------------------

    # --- souhrn a Spustit ---------------------------------------------------------

    def step(self) -> int:
        """Krok u Víc pacientů (0 nahrávky, 1 pacienti); ostatní typy mají jednu obrazovku."""
        return 1 if self.stack.currentIndex() == 1 else 0

    def _show_step(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, pill in enumerate(self.step_pills):
            theme.set_role(pill, "accent" if i == index else ("ok" if i < index else "neutral"))
        self.back_btn.setText(tr("Storno") if index == 0 else tr("‹ Zpět"))
        if index == 1:
            self._fill_patients()
        self._update_buttons()

    def estimate(self) -> tuple[float, bool]:
        """Odhad všech dávek v sekundách a zda je známý pro všechny (podle minut zvuku)."""
        rows = self.active_rows()
        durations = {rec: sec for row in rows for rec, sec in row.scan.durations.items()}
        if self._mode == SERIES:
            durations = self.series.durations()
        total, known = 0.0, True
        for plan in self._task_plans():
            rate = self._rate(plan.proto().slug())
            audio = sum(durations.get(rec) or 0.0 for rec in plan.inputs)
            if rate and audio:
                total += rate / 60 * audio
            else:
                known = False
        return total, known

    def _update_summary(self) -> None:
        if self._mode == SERIES:
            n = self.series.new_visit_count()
            figures = [
                (
                    tr("{n} vyšetření").format(n=n),
                    tr("{k} nahrávek").format(k=self.series.recording_count()),
                )
            ]
            total, known = self.estimate()
            if total:
                figures.append(
                    ("~" + format_duration(total), tr("odhad") if known else tr("odhad (část)"))
                )
            self.summary.set_figures(figures)
            self.summary.setVisible(True)
            return
        rows = self.active_rows()
        n = sum(len(r.scan.recordings) for r in rows)
        audio = sum(r.scan.audio_seconds for r in rows)
        total, known = self.estimate()
        if self._multi:
            patients, visits, _n, _missing = self.people_counts()
            first = (patients_text(patients), tr("{v} vyšetření").format(v=visits))
            second = (
                tr("{b} dávek").format(b=len(self._task_plans())),
                tr("{n} nahrávek").format(n=n),
            )
        else:
            first = (tr("{k} úlohy").format(k=len(rows)), tr("{n} nahrávek").format(n=n))
            second = (audio_duration(audio) if audio else "–", tr("zvuku"))
        figures = [first, second]
        if total:
            figures.append(
                ("~" + format_duration(total), tr("odhad") if known else tr("odhad (část)"))
            )
        self.summary.set_figures(figures if rows else [])
        self.summary.setVisible(True)  # i prázdný drží místo, jinak se roztáhne Storno

    def _update_buttons(self) -> None:
        if not hasattr(self, "next_btn"):  # při stavbě stránky ještě patička není
            return
        self._update_norm_warning()  # úlohy s nahrávkami se mění se složkami
        hint, ok = "", True
        rows = self.active_rows()
        if self._mode == CHOOSE:
            self.next_btn.setEnabled(False)
            return
        if self._mode == SERIES:
            problems = self.series.problems()
            ok = not problems
            hint = problems[0] if problems else ""
            needed = self.series.needs_language()
            self.language_label.setVisible(needed)
            self.language.setVisible(needed)
            n = self.series.new_visit_count()
            self.next_btn.setText(
                tr("Spustit · {v} hodnocení").format(v=n) if n > 1 else tr("Spustit hodnocení")
            )
        elif self._mode == SINGLE:
            if not self.subject.text().strip():
                ok, hint = False, tr("Vyplňte pacienta.")
            elif not rows:
                ok, hint = False, tr("Vyberte složku aspoň pro jednu úlohu.")
            elif not self.age.text().strip() or not self.sex.currentData():
                hint = tr("bez věku nebo pohlaví se nevybere věkové okno a pohlaví")
            self.next_btn.setText(tr("Spustit hodnocení"))
        elif self.step() == 0:
            # Krok 1: jen složky. Patička napoví, co čeká v kroku 2.
            self.next_btn.setText(tr("Další › Pacienti"))
            if not rows:
                ok, hint = False, tr("Vyberte složku aspoň pro jednu úlohu.")
            elif self._multi:
                patients, _v, _n, unassigned = self.people_counts()
                missing = sum(1 for paths in self._visits().values() if self._visit_missing(paths))
                hint = tr("manifest: {p}").format(p=patients_text(patients))
                if missing or unassigned:
                    hint += " · " + tr("{k} údajů chybí").format(k=missing + unassigned)
        elif self._multi:
            _patients, visits, _n, unassigned = self.people_counts()
            missing = sum(1 for paths in self._visits().values() if self._visit_missing(paths))
            if not visits:
                ok, hint = False, tr("Žádná nahrávka nemá pacienta i datum vyšetření.")
            else:
                parts = []
                if missing:
                    parts.append(tr("{k} vyšetření bez věku nebo pohlaví").format(k=missing))
                if unassigned:
                    parts.append(tr("{k} nahrávek se jen spočítá").format(k=unassigned))
                hint = " · ".join(parts)
            self.next_btn.setText(
                tr("Spustit · {v} hodnocení").format(v=visits) if visits else tr("Spustit")
            )
        else:
            if not self.subject.text().strip():
                ok, hint = False, tr("Vyplňte pacienta.")
            elif not self.age.text().strip() or not self.sex.currentData():
                hint = tr("bez věku nebo pohlaví se nevybere věkové okno a pohlaví")
            self.next_btn.setText(tr("Spustit hodnocení"))
        self.next_btn.setEnabled(ok)
        self.hint.setText(hint)
        self._update_summary()

    def go_next(self) -> None:
        """Krok 1 → Pacienti, krok 2 → Spustit."""
        if not self.next_btn.isEnabled():
            return
        if self._mode == MULTI and self.step() == 0:
            self._show_step(1)
            return
        if self._mode == SERIES and self.series.changed_tasks():
            if not self.confirm(self.series.protocol_warning_text()):
                return
        elif self._mode != SERIES and self.norm_changed_tasks():
            if not self.confirm(self._norm_changed_text()):
                return
        self.accepted.emit()

    def go_back(self) -> None:
        if self.step() == 0:
            self.rejected.emit()
        else:
            self._show_step(0)

    # --- výsledek ----------------------------------------------------------------

    def birth_year(self) -> int | None:
        """Rok narození z Vývoje v čase (hlavní okno ho uloží k pacientovi)."""
        return self.series.birth_year_value() if self._mode == SERIES else None

    def patient(self) -> Patient:
        if self._mode == SERIES:  # první vyšetření řady (plán s víc daty má `people`)
            dated = sorted((v for v in self.series.visits() if v.date), key=lambda v: v.date)
            first = dated[0] if dated else None
            return Patient(
                subject=self.series.subject_text(),
                age=self.series.age_for(first) if first is not None else None,
                sex=self.series.sex_code(),
                date=first.date if first is not None else "",
            )
        day = self.date.date()
        return Patient(
            subject=self.subject.text().strip(),
            age=meta.parse_age(self.age.text()),
            sex=str(self.sex.currentData() or ""),
            date=dt.date(day.year(), day.month(), day.day()).isoformat(),
        )

    def plan(self) -> AssessmentPlan:
        language = str(self.language.currentData() or "")
        plan = new_plan(self._dir, self.patient(), language, self._task_plans(), self.people())
        norm = self.selected_norm()
        plan.norm = norm.info.name if norm is not None else ""
        return plan

    # --- norma -------------------------------------------------------------------------

    def _build_norm_bar(self) -> QWidget:
        """Pruh „Srovnávat s normou“: protokoly úloh a jazyk se vezmou z vybrané normy."""
        bar = QFrame()
        bar.setObjectName("normbar")
        bar.setStyleSheet(
            f"QFrame#normbar {{ background: {theme.CARD}; border: 1px solid {theme.BORDER};"
            f" border-left: 4px solid {theme.ACCENT}; border-radius: 10px; }}"
            "QFrame#normbar QLabel { background: transparent; border: none; }"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(10)
        title = QLabel(tr("Srovnávat s normou"))
        title.setStyleSheet("font-weight: 600;")
        row.addWidget(title)
        self.norm_combo = QComboBox()
        self.norm_combo.setMinimumWidth(240)
        self.norm_combo.currentIndexChanged.connect(lambda _i: self._norm_chosen())
        row.addWidget(self.norm_combo)
        self.norm_info = QLabel("")
        self.norm_info.setObjectName("muted")
        row.addWidget(self.norm_info, 1)
        self.norm_pill = Pill(tr("protokoly a jazyk podle normy"), "accent")
        row.addWidget(self.norm_pill)
        self.norm_bar = bar
        bar.hide()
        return bar

    def _has_norms(self) -> bool:
        return bool(self._load_norms())

    def _load_norms(self) -> list[Norm]:
        if self._norms is None:
            try:
                self._norms = list(self._norms_lookup())
            except Exception:  # noqa: BLE001  normy jsou jen pomůcka k volbě protokolů
                self._norms = []
            self.norm_combo.blockSignals(True)
            self.norm_combo.clear()
            for norm in self._norms:
                self.norm_combo.addItem(norm.info.display_name, str(norm.path))
            self.norm_combo.addItem(tr("žádná norma (jen hodnoty)"), NO_NORM)
            self.norm_combo.blockSignals(False)
        return self._norms

    def _norm_subject(self) -> str:
        if self._mode == SINGLE:
            return self.subject.text().strip()
        if self._mode == SERIES:
            return self.series.subject_text()
        return ""

    def _preselect_norm(self) -> None:
        """Norma pacienta (nebo výchozí), dokud ji uživatel nezměnil sám."""
        if not self._load_norms():
            self._apply_norm()
            return
        if not self._norm_touched:
            key = self._norm_key_for(self._norm_subject())
            index = self.norm_combo.findData(key) if key else 0
            self.norm_combo.blockSignals(True)
            self.norm_combo.setCurrentIndex(max(index, 0))
            self.norm_combo.blockSignals(False)
        self._apply_norm()

    def _norm_chosen(self) -> None:
        self._norm_touched = True
        self._apply_norm()

    def select_norm(self, key: str) -> bool:
        """Norma podle cesty (nebo NO_NORM); pro testy a předvolbu."""
        self._load_norms()
        index = self.norm_combo.findData(key)
        if index < 0:
            return False
        self.norm_combo.setCurrentIndex(index)
        self._norm_touched = True
        self._apply_norm()
        return True

    def norm_key(self) -> str:
        """Vybraná norma jako klíč (cesta nebo NO_NORM); bez norem prázdné."""
        if not self._load_norms():
            return ""
        return str(self.norm_combo.currentData() or "")

    def selected_norm(self) -> Norm | None:
        key = self.norm_key()
        return next((n for n in self._norms or [] if str(n.path) == key), None)

    def _apply_norm(self) -> None:
        """Protokoly řádků a jazyk podle vybrané normy; bez normy výchozí jako dřív."""
        norm = self.selected_norm()
        if norm is None:
            found, missing = {}, {}
        else:
            key = str(norm.path)
            if key not in self._norm_cache:
                self._norm_cache[key] = norm_protocols(norm, self._protocols)
            found, missing = self._norm_cache[key]
        self._norm_found, self._norm_missing = found, missing
        for task, row in self.rows.items():
            choices, default = self._row_defaults.get(task, ([], ""))
            subs = {c.name: tr("přibalený") if c.builtin else tr("vlastní") for c in choices}
            if task in found:
                proto = found[task]
                options = list(choices)
                if all(c.name != proto.name for c in options):
                    options.append(proto)
                subs[proto.name] = tr("podle normy")
                row.protocol.set_protocols(options, current=proto.name, subs=subs)
                row.protocol.setEnabled(self._advanced and len(options) > 1)
            else:
                if task in missing:
                    subs[default] = tr("protokol normy „{name}“ tu není").format(name=missing[task])
                elif norm is not None:
                    subs[default] = tr("výchozí · norma tuto úlohu nemá")
                row.protocol.set_protocols(choices, current=default, subs=subs)
                row.protocol.setEnabled(self._advanced and len(choices) > 1)
        language = norm.info.language if norm is not None else ""
        index = self.language.findData(language) if language else -1
        if index >= 0:
            self.language.setCurrentIndex(index)
        self.language.setEnabled(index < 0)
        self.language.setToolTip(tr("Jazyk nahrávek kontrol normy.") if index >= 0 else "")
        if norm is not None:
            parts = [tr("{n} osob").format(n=norm.subjects)] if norm.subjects else []
            if norm.info.language:
                parts.append(contract.language_label(norm.info.language))
            names = [tasks.label(t) for t in sorted(found, key=tasks.task_order)]
            if names:
                parts.append(tr("úlohy {tasks}").format(tasks=", ".join(names)))
            self.norm_info.setText(" · ".join(parts))
        else:
            self.norm_info.setText(
                tr("výsledky v jednotkách feature, pavouk a z-skóre až s normou")
                if self._norms
                else ""
            )
        self.norm_pill.setVisible(norm is not None)
        self.norm_bar.setStyleSheet(
            self.norm_bar.styleSheet().replace(
                theme.ACCENT if norm is None else "#9ca3af",
                "#9ca3af" if norm is None else theme.ACCENT,
            )
        )
        self.series.set_norm(found, norm.info.display_name if norm is not None else "")
        self._update_norm_warning()
        self._update_buttons()

    def norm_changed_tasks(self) -> list[str]:
        """Úlohy s nahrávkami, kde je (v rozšířeném režimu) jiný protokol než norma."""
        out = []
        for task, row in self.rows.items():
            proto = row.current_protocol()
            if task in self._norm_found and row.scan.recordings and proto is not None:
                if proto.name != self._norm_found[task].name:
                    out.append(task)
        return out

    def _norm_changed_text(self) -> str:
        return tr("{tasks}: jiný protokol než norma, srovnání s kontrolami nebude přesné.").format(
            tasks=", ".join(tasks.label(t) for t in self.norm_changed_tasks())
        )

    def _update_norm_warning(self) -> None:
        """Pod úlohami: jiný protokol než norma, chybějící protokol normy, úloha mimo normu."""
        if not hasattr(self, "norm_warning"):
            return
        norm = self.selected_norm()
        parts: list[str] = []
        if norm is not None and self._mode in (SINGLE, MULTI):
            changed = self.norm_changed_tasks()
            if changed:
                parts.append(
                    self._norm_changed_text()
                    + f" <a href='norm' style='color:#c2410c'>{tr('vrátit podle normy')}</a>"
                )
            used = [t for t, r in self.rows.items() if r.scan.recordings]
            missing = [t for t in used if t in self._norm_missing]
            if missing:
                parts.append(
                    tr(
                        "Protokol kontrol pro {tasks} tu není nainstalovaný, počítá se výchozím "
                        "(srovnání nebude přesné); importovat jde na stránce Protokoly."
                    ).format(tasks=", ".join(tasks.label(t) for t in missing))
                )
            outside = [t for t in used if t not in self._norm_found and t not in missing]
            if outside:
                parts.append(
                    tr(
                        "{tasks}: norma tuto úlohu nemá, spočítá se, ale s normou se nesrovná."
                    ).format(tasks=", ".join(tasks.label(t) for t in outside))
                )
        self.norm_warning.setText("<br>".join(parts))
        self.norm_warning.setVisible(bool(parts))


class AssessmentFromRecordingsDialog(QDialog):
    """Průvodce v okně (Nové hodnocení… na Pacientech); vše ostatní dělá widget."""

    def __init__(
        self,
        protocols: list[Protocol],
        assessments_dir: Path,
        *,
        advanced: bool = False,
        languages: list[str] | None = None,
        language: str = "cs",
        rate_lookup: Callable[[str], float | None] = lambda _slug: None,
        patients_lookup: Callable[[], list[SeriesPatient]] = list,
        run_protocol: Callable[[str], Protocol | None] = lambda _key: None,
        norms_lookup: Callable[[], list[Norm]] = list,
        norm_key_for: Callable[[str], str] = lambda _subject: "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Nové hodnocení"))
        self.resize(1040, 720)
        self.widget = AssessmentBuildWidget(
            protocols,
            assessments_dir,
            advanced=advanced,
            languages=languages,
            language=language,
            rate_lookup=rate_lookup,
            patients_lookup=patients_lookup,
            run_protocol=run_protocol,
            norms_lookup=norms_lookup,
            norm_key_for=norm_key_for,
            parent=self,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.addWidget(self.widget)
        self.widget.accepted.connect(self.accept)
        self.widget.rejected.connect(self.reject)

    def __getattr__(self, name: str):  # noqa: ANN204
        """Pole a metody průvodce (`plan`, `rows`, `set_folder`…) jako dřív přímo na dialogu."""
        if name == "widget":
            raise AttributeError(name)
        return getattr(self.widget, name)


__all__ = ["NewAssessmentChoiceDialog", "AssessmentBuildWidget", "AssessmentFromRecordingsDialog"]


VALUE_ROLE = Qt.ItemDataRole.UserRole + 41  # skutečná hodnota (zobrazený text může být „doplnit“)


def _mark_suggested(item: QTableWidgetItem, source: str) -> None:
    """Předvyplněná hodnota: šedě kurzívou, zdroj v tooltipu."""
    font = item.font()
    font.setItalic(True)
    item.setFont(font)
    item.setForeground(QColor(theme.MUTED))
    item.setToolTip(tr("předvyplněno {source}; přepsat jde vždy").format(source=source))


class _UnassignedDelegate(QStyledItemDelegate):
    """Editor buněk žlutého oddílu: pacient s nabídkou známých, pohlaví ze seznamu."""

    def __init__(self, owner: AssessmentBuildWidget) -> None:
        super().__init__(owner)
        self._owner = owner

    def createEditor(self, parent, option, index):  # noqa: ANN001, ANN201, N802
        column = index.column()
        if column == 2:
            combo = QComboBox(parent)
            combo.setEditable(True)
            combo.addItems(self._owner._subject_choices())
            return combo
        if column == 5:
            combo = QComboBox(parent)
            for code, label in SEX_CHOICES:
                combo.addItem(tr(label), code)
            return combo
        if column in (3, 4):
            edit = QLineEdit(parent)
            edit.setPlaceholderText(tr("třeba 14. 10. 2026") if column == 3 else tr("roky"))
            return edit
        return None

    def setEditorData(self, editor, index):  # noqa: ANN001, N802
        value = str(index.data(VALUE_ROLE) or "")
        if isinstance(editor, QComboBox) and index.column() == 5:
            code = meta.parse_sex(value) if value else ""
            editor.setCurrentIndex(max(0, editor.findData(code)))
        elif isinstance(editor, QComboBox):
            editor.setCurrentText(value)
        elif isinstance(editor, QLineEdit):
            editor.setText(value)

    def setModelData(self, editor, model, index):  # noqa: ANN001, ARG002, N802
        if isinstance(editor, QComboBox) and index.column() == 5:
            text = str(editor.currentData() or "")
            old = meta.parse_sex(str(index.data(VALUE_ROLE) or ""))
        else:
            text = editor.currentText() if isinstance(editor, QComboBox) else editor.text()
            old = str(index.data(VALUE_ROLE) or "")
        if text.strip() != old:
            self._owner._commit_cell(index.row(), index.column(), text)


class FillPeopleDialog(QDialog):
    """Vyplnit…: pacient, datum, věk, pohlaví pro vybrané nahrávky; prázdné nic nemění."""

    def __init__(
        self,
        count: int,
        subjects: list[str],
        catalog: dict[str, SeriesPatient],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Vyplnit {n} nahrávek").format(n=count))
        self._catalog = catalog
        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.subject = QComboBox()
        self.subject.setEditable(True)
        self.subject.addItems(subjects)
        self.subject.setCurrentText("")
        self.subject.lineEdit().setPlaceholderText(tr("nechat"))
        self.subject.currentTextChanged.connect(lambda _t: self._suggest())
        self.visit = QLineEdit()
        self.visit.setPlaceholderText(tr("nechat · třeba 14. 10. 2026"))
        self.visit.textChanged.connect(lambda _t: self._suggest())
        self.age = QLineEdit()
        self.age.setPlaceholderText(tr("nechat"))
        self.sex = QComboBox()
        self.sex.addItem(tr("nechat"), None)
        for code, label in SEX_CHOICES:
            if code:
                self.sex.addItem(tr(label), code)
        for r, (text, widget) in enumerate(
            (
                (tr("Pacient"), self.subject),
                (tr("Datum"), self.visit),
                (tr("Věk"), self.age),
                (tr("Pohlaví"), self.sex),
            )
        ):
            form.addWidget(QLabel(text), r, 0)
            form.addWidget(widget, r, 1)
        layout.addLayout(form)
        self.note = QLabel(
            tr(
                "Prázdné pole nic nezmění. U pacienta z katalogu se pohlaví a věk z roku "
                "narození doplní samy (šedě, přepsat jde vždy). Platí jen pro tento výpočet."
            )
        )
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("Storno"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("Vyplnit"))
        theme.set_role(ok, "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        self.resize(460, self.sizeHint().height())

    def _suggest(self) -> None:
        """Pacient z katalogu: věk z roku narození jako nápověda v poli věku."""
        known = self._catalog.get(patient_key(self.subject.currentText()))
        visit = meta.normalize_visit(self.visit.text())
        hint = tr("nechat")
        if known is not None and known.birth_year and visit[:4].isdigit():
            hint = tr("{age} · z roku narození {year}").format(
                age=int(visit[:4]) - known.birth_year, year=known.birth_year
            )
        self.age.setPlaceholderText(hint)

    def values(self) -> dict[str, str | None]:
        subject = self.subject.currentText().strip()
        visit = self.visit.text().strip()
        age = self.age.text().strip()
        sex = self.sex.currentData()
        return {
            "subject": subject or None,
            "visit": visit or None,
            "age": age or None,
            "sex": str(sex) if sex else None,
        }
