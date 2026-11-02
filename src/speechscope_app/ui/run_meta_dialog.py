"""Údaje nahrávek hotového běhu: načíst manifest, upravit ručně, zapsat do výsledků.

Výsledky › Hodnoty › Údaje nahrávek… (1. 11. 2026, varianta A, `backend/run_meta.py`).
Tabulka je táž jako v kroku 1 Analýzy (`MetaTable`): dvojklik, Vyplnit vybrané,
+ Sloupec. Uložit zapíše změněné buňky do `features.csv` běhu, původní tabulka
zůstane jako záloha a jde vrátit.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..backend import meta
from ..backend.manifest import read_manifest
from ..backend.run_meta import (
    PROTECTED,
    apply_metadata,
    has_backup,
    match_manifest,
    read_table,
    restore_original,
    write_manifest,
)
from ..i18n import tr
from . import theme
from .widgets.menu import Menu
from .widgets.meta_table import COLUMN_PROP, MetaRow, MetaTable

CSV_FILTER = "CSV (*.csv)"


class RunMetaDialog(QDialog):
    """Okno údajů nahrávek jednoho běhu; `saved` = co se zapsalo (pro přenačtení)."""

    def __init__(self, run_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Údaje nahrávek"))
        self.resize(980, 640)
        self.run_dir = run_dir
        self.changed_cells = 0
        self.restored = False
        self._table = read_table(run_dir)
        self._keys = self._table.recording_keys()
        self._row_of = {k: i for i, k in enumerate(self._keys)}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        intro = QLabel(
            tr(
                "Pacient, datum, věk, pohlaví nebo skupina k nahrávkám tohoto běhu. Hodnoty "
                "feature se nepřepočítávají, údaje se jen připíšou do tabulky výsledků; "
                "původní tabulka zůstane jako záloha."
            )
        )
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.load_btn = QPushButton(tr("Načíst manifest…"))
        self.load_btn.setToolTip(
            tr(
                "CSV se sloupcem path (nebo názvem souboru) a údaji; páruje se podle "
                "cesty, pak podle názvu."
            )
        )
        self.load_btn.clicked.connect(lambda: self._load_manifest())
        bar.addWidget(self.load_btn)
        self.column_btn = QPushButton(tr("+ Sloupec"))
        column_menu = Menu(self.column_btn)
        self._add_column_items(column_menu)
        column_menu.addSeparator()
        # odebrat jeden sloupec (i pravým tlačítkem na záhlaví tabulky)
        self.remove_menu = column_menu.add_menu(tr("Odebrat sloupec"), icon="remove")
        self.remove_menu.aboutToShow.connect(self._fill_remove_menu)
        self.column_btn.setMenu(column_menu)
        bar.addWidget(self.column_btn)
        self.fill_btn = QPushButton(tr("Vyplnit vybrané…"))
        self.fill_btn.clicked.connect(lambda: self._fill_selected())
        bar.addWidget(self.fill_btn)
        bar.addStretch(1)
        self.save_manifest_btn = QPushButton(tr("Uložit manifest…"))
        self.save_manifest_btn.clicked.connect(lambda: self.save_manifest())
        bar.addWidget(self.save_manifest_btn)
        layout.addLayout(bar)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.meta = MetaTable([tr("nahrávka")], labels=True)
        files = self._table.column("file")
        columns = self._table.meta_columns()
        self.meta.set_rows(
            [
                MetaRow(
                    key=key,
                    leading=[files[i] or key.name],
                    base={c: v for c, v in zip(columns, self._values(i, columns), strict=True)},
                    tooltip=str(key),
                )
                for i, key in enumerate(self._keys)
            ],
            columns,
        )
        self.meta.itemChanged.connect(lambda _i: self._update_status())
        self.meta.edited.connect(self._update_status)  # i odebrání sloupce ze záhlaví
        add_menu = Menu(self, tr("Přidat sloupec"))  # „+“ v záhlaví tabulky (2. 11. 2026)
        self._add_column_items(add_menu)
        self.meta.set_add_menu(add_menu)
        layout.addWidget(self.meta, 1)

        foot = QHBoxLayout()
        self.restore_btn = QPushButton(tr("Vrátit původní tabulku"))
        self.restore_btn.setToolTip(
            tr("Tabulka výsledků, jak ji spočítala knihovna, bez doplněných údajů.")
        )
        self.restore_btn.setVisible(has_backup(run_dir))
        self.restore_btn.clicked.connect(lambda: self.restore())
        foot.addWidget(self.restore_btn)
        foot.addStretch(1)
        cancel = QPushButton(tr("Storno"))
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        self.save_btn = QPushButton(tr("Uložit do výsledků"))
        theme.set_role(self.save_btn, "primary")
        self.save_btn.clicked.connect(lambda: self.save())
        foot.addWidget(self.save_btn)
        layout.addLayout(foot)
        self._update_status()

    def _values(self, row: int, columns: list[str]) -> list[str]:
        return [self._table.column(c)[row] for c in columns]

    # --- úpravy ---------------------------------------------------------------------------

    def _add_column_items(self, menu: Menu) -> None:
        for column in meta.META_COLUMNS:
            action = menu.addAction(f"{meta.label(column)} ({column})")
            action.triggered.connect(lambda _c=False, col=column: self.add_column(col))
            action.setProperty(COLUMN_PROP, column)
        menu.addSeparator()
        menu.addAction(tr("Vlastní název…")).triggered.connect(lambda: self._ask_column())

    def add_column(self, name: str) -> str:
        column = meta.canonical_column(name.strip())
        if not column or column in PROTECTED or "." in column:
            return ""
        return self.meta.add_column(column)

    def _fill_remove_menu(self) -> None:
        self.remove_menu.clear()
        columns = self.meta.columns
        if not columns:
            self.remove_menu.addAction(tr("žádný sloupec")).setEnabled(False)
        for column in columns:
            action = self.remove_menu.addAction(self.meta.display_header(column))
            action.triggered.connect(lambda _c=False, col=column: self.remove_column(col))

    def remove_column(self, column: str) -> bool:
        """Sloupec zmizí z okna; z tabulky výsledků až po Uložit do výsledků."""
        removed = self.meta.remove_column(column)
        self._update_status()
        return removed

    def _ask_column(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Nový sloupec"), tr("Název sloupce:"))
        if ok and name.strip():
            self.add_column(name)

    def _fill_selected(self) -> None:
        columns = self.meta.columns
        if not columns or not self.meta.selected_indices():
            self.status.setText(tr("Vyberte řádky a pak Vyplnit vybrané…"))
            return
        labels = [f"{meta.label(c)} ({c})" if c in meta.META_COLUMNS else c for c in columns]
        label, ok = QInputDialog.getItem(
            self, tr("Vyplnit vybrané"), tr("Sloupec:"), labels, 0, False
        )
        if not ok:
            return
        column = columns[labels.index(label)]
        value, ok = QInputDialog.getText(
            self,
            tr("Vyplnit vybrané"),
            tr("Hodnota pro {n} vybraných řádků, sloupec {column}:").format(
                n=len(self.meta.selected_indices()), column=column
            ),
        )
        if ok:
            self.meta.fill_selected(column, value)
            self._update_status()

    def _load_manifest(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, tr("Načíst manifest"), "", CSV_FILTER)
        if chosen:
            self.load_manifest(Path(chosen))

    def load_manifest(self, path: Path) -> tuple[int, int]:
        """Údaje z manifestu do tabulky jako úpravy (zapíše je až Uložit);
        vrátí (spárováno, bez řádku)."""
        manifest = read_manifest(path, keep_missing=True)
        match = match_manifest(self._table, manifest)
        for column in match.columns:
            if column not in self.meta.columns:
                self.add_column(column)
        for row, values in match.values.items():
            for column, value in values.items():
                self.meta.set_value(self._keys[row], meta.canonical_column(column), value)
        text = tr("Manifest {name}: spárováno {m} z {n} nahrávek").format(
            name=path.name, m=match.matched, n=len(self._keys)
        )
        if match.unmatched:
            text += " · " + tr("bez řádku v manifestu: {files}").format(
                files=", ".join(match.unmatched[:8]) + (" …" if len(match.unmatched) > 8 else "")
            )
        if manifest.problems:
            text += " · " + "; ".join(manifest.problems[:3])
        self._manifest_note = text
        self._update_status()
        return match.matched, len(match.unmatched)

    def _update_status(self) -> None:
        note = getattr(self, "_manifest_note", "")
        edits = self.meta.edit_count()
        removed = self.meta.removed_columns()
        parts = [note] if note else []
        parts.append(tr("upraveno: {n}").format(n=edits) if edits else tr("beze změn"))
        if removed:
            parts.append(tr("odebrat: {columns}").format(columns=", ".join(removed)))
        self.status.setText(" · ".join(parts))
        self.save_btn.setEnabled(edits > 0 or bool(removed))

    # --- uložení ------------------------------------------------------------------------

    def changes(self) -> dict[int, dict[str, str]]:
        """Upravené buňky po řádcích tabulky výsledků."""
        out: dict[int, dict[str, str]] = {}
        for key, cells in self.meta.edits().items():
            row = self._row_of.get(key)
            if row is not None and cells:
                out[row] = dict(cells)
        return out

    def save(self) -> bool:
        try:
            self.changed_cells = apply_metadata(
                self.run_dir, self.changes(), remove=self.meta.removed_columns()
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr("Údaje nahrávek"),
                tr("Tabulku výsledků nejde zapsat (není otevřená v Excelu?): {error}").format(
                    error=exc
                ),
            )
            return False
        self.accept()
        return True

    def restore(self) -> bool:
        answer = QMessageBox.question(
            self,
            tr("Vrátit původní tabulku"),
            tr("Doplněné údaje se zahodí a tabulka bude jako po výpočtu. Pokračovat?"),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        try:
            self.restored = restore_original(self.run_dir)
        except OSError as exc:
            QMessageBox.warning(self, tr("Údaje nahrávek"), str(exc))
            return False
        self.accept()
        return self.restored

    def save_manifest(self, target: Path | None = None) -> Path | None:
        """Údaje běhu (včetně neuložených úprav) jako manifest CSV, kam uživatel ukáže."""
        if target is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self, tr("Uložit manifest"), str(self.run_dir / "manifest-udaje.csv"), CSV_FILTER
            )
            if not chosen:
                return None
            target = Path(chosen)
        columns = self.meta.columns
        merged = self.meta.rows_merged()
        table = read_table(self.run_dir)
        # úpravy z okna do kopie tabulky jen pro zápis manifestu
        for column in columns:
            if column not in table.header:
                table.header.append(column)
                for row in table.rows:
                    row.append("")
        for key, values in merged.items():
            row = self._row_of.get(key)
            if row is None:
                continue
            cells = table.rows[row]
            while len(cells) < len(table.header):
                cells.append("")
            for column, value in values.items():
                if column in table.header:
                    cells[table.header.index(column)] = value
        return write_manifest(table, columns, target)
