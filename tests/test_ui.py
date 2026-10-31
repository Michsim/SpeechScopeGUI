"""Kouřový test celého okna nad falešnou knihovnou."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import wait_doctor
from PySide6.QtCore import QItemSelectionModel
from pytestqt.qtbot import QtBot

from speechscope_app import contract
from speechscope_app.backend.history import read_run
from speechscope_app.backend.library import ParamInfo
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import (
    PAGE_ASSESSMENT,
    PAGE_BATCH,
    PAGE_ENV,
    PAGE_HOME,
    PAGE_RESULTS,
    PAGE_RUN,
    MainWindow,
)
from speechscope_app.ui.widgets.meta_table import GROUP_FOLDER
from speechscope_app.ui.widgets.param_form import ParamForm


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    # uživatelské protokoly do tmp, ne do AppData
    monkeypatch.setattr(
        "speechscope_app.backend.settings.app_data_dir", lambda: tmp_path / "appdata"
    )
    s = AppSettings(tmp_path / "settings.ini")
    s.use_fake_library = True
    s.models_dir = tmp_path / "models"
    s.work_root = tmp_path / "work"
    s.advanced = True
    return s


def test_window_runs_batch_end_to_end(
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPEECHSCOPE_FAKE_DELAY", "0.05")  # ať má statistika co měřit
    window = MainWindow(settings)
    qtbot.addWidget(window)
    wait_doctor(qtbot, window)
    assert window.env_page.report and window.env_page.report["all_ready"]

    window.batch_page.set_folder(recordings)
    assert window.batch_page.files.rowCount() == 4
    assert window.batch_page.run_btn.isEnabled()

    assert window.batch_page.select_protocol("Fonace, základní")
    assert window.batch_page.protocols.task_buttons["phonation"].isChecked()
    assert window.batch_page.editor.has_catalog()
    assert window.batch_page.new_btn.isEnabled()
    assert not window.batch_page.editor.picker.warning.text()  # doctor: všechno připravené
    assert "Bez modelů" in window.batch_page.editor_summary.text()

    runs = window.runs_page
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        window.batch_page.run_btn.click()
        run_dir = window._running_dir
        assert read_run(run_dir).status == "running"  # záznam hned při startu
        # Výsledky = přehled + detail: běžící běh otevřený na Průběhu, Hodnoty zavřené
        assert window.nav.currentRow() == PAGE_RESULTS and runs.in_detail()
        assert runs.tab() == "progress" and not runs.values_tab.isEnabled()
        assert not runs.running_card.isHidden() and runs.live_dir() == run_dir
    state = window.run_page.runner.state
    assert state.finished and state.total == 4
    assert window.nav.currentRow() == PAGE_RESULTS
    assert runs.in_detail() and runs.tab() == "values"  # po dávce hodnoty
    assert runs.values_tab.isEnabled() and runs.running_card.isHidden()
    assert runs.detail_stack.currentWidget() is window.results_page
    runs.set_tab("progress")
    assert runs.detail_stack.currentWidget() is window.run_page
    runs.show_overview()
    assert not runs.in_detail() and runs.table.rowCount() == 1
    assert runs.table.item(0, 0).text() == "Fonace, základní"
    assert runs.table.item(0, 3).text() == "4" and runs.cell_text(0, 4)
    assert runs.table.item(0, 4).text() == ""  # stav kreslí štítek, text pod ním není
    runs.search.setText("nic takového")
    assert runs.visible_rows() == []
    runs.search.clear()
    runs.open_row(0)
    qtbot.waitUntil(lambda: not runs.loading())  # tabulka se načte v dalším kroku
    assert runs.in_detail() and runs.tab() == "values" and runs.crumb.text() == "Fonace, základní"
    assert not runs.prev_btn.isEnabled() and not runs.next_btn.isEnabled()
    assert read_run(run_dir).status == "ok"
    # tabulka běhu: řádek na nahrávku, výsledek, nabídka bez postupu po konci
    run = window.run_page
    assert run.files.rowCount() == 4
    statuses = [run.files.item(r, run.col_status).text() for r in range(4)]
    assert sorted(statuses) == ["chyba", "hotovo", "hotovo", "hotovo"]
    assert window.nav.item(PAGE_RUN).badge() == ""  # po dávce bez štítku
    assert run.headline.text().startswith("Hotovo za")
    assert settings.seconds_per_file("fonace-zakladni") is not None
    assert window.results_page.table.model().rowCount() == 4
    # kontrola kvality: faleška dá „short“ nahrávce 3 s, pod žlutým prahem fonace
    model = window.results_page.table.model()
    statuses = {model.frame["file"].iloc[r]: model._status(r)[0] for r in range(4)}
    assert statuses["p03_short.wav"] == "kvalita" and statuses["p01.wav"] == "hotovo"
    assert "délka nahrávky 3 s < 5 s" in model._status(list(statuses).index("p03_short.wav"))[2]
    pills = [p.text() for p in window.results_page.header._pill_widgets]
    assert "1 podezřelých" in pills and "1 s chybou" in pills

    run_dirs = list((settings.work_root).glob("*_fonace-zakladni"))
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "features.csv").is_file()
    assert (run_dirs[0] / "protocol.yaml").is_file()
    assert (run_dirs[0] / "speechscope.log").is_file()
    run_file = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
    assert run_file["status"] == "ok" and run_file["total"] == 4 and run_file["processed"] == 4
    # historie na Výsledcích: jeden běh, vybraný, se stavem hotovo
    results = window.results_page
    assert results.history.count() == 1
    # bez názvu se běh jmenuje podle protokolu (pole zůstává prázdné, jen nápověda)
    assert window.batch_page.run_label() == ""
    assert results.history.card(0).title.text() == "Fonace, základní"
    assert results.history.card(0).pill.text() == "hotovo"
    assert results.history.current_row() == 0
    assert results.title_label.text() == "Fonace, základní"
    # přejmenování z historie
    info = results.history.current()
    assert window.rename_run(info, "Pacienti září")
    assert results.history.card(0).title.text() == "Pacienti září"
    assert "Fonace, základní" in results.history.card(0).detail.text()
    assert results.title_label.text() == "Pacienti září"
    # složka na disku se přejmenuje podle názvu, datum a čas zůstávají
    renamed = settings.work_root / (run_dirs[0].name[:20] + "pacienti-zari")
    assert renamed.is_dir() and not run_dirs[0].exists()
    assert results.current_dir() == renamed and results.history.current().dir == renamed
    assert json.loads((renamed / "run.json").read_text(encoding="utf-8"))["label"] == (
        "Pacienti září"
    )
    assert window.rename_run(results.history.current(), "")  # prázdný = podle protokolu
    assert results.history.card(0).title.text() == "Fonace, základní"
    assert run_dirs[0].is_dir() and not renamed.exists()  # i složka zpět na protokol
    # záznam průběhu a jeho přehrání na Výpočtu
    events = (run_dirs[0] / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert events[0].startswith('{"event": "inputs"')  # řádek GUI se seznamem nahrávek
    assert events[1].startswith('{"event": "start"') and events[-1].startswith('{"event": "saved"')
    assert run.history.count() == 1
    run._live_dir = None  # jako po novém startu aplikace: běh je jen v historii
    run.show_recorded(run.history.current())
    assert run.files.rowCount() == 4
    replayed = sorted(run.files.item(r, run.col_status).text() for r in range(4))
    assert replayed == ["chyba", "hotovo", "hotovo", "hotovo"]
    assert "hotovo" in run.summary.text() and run.log.toPlainText()


def test_edit_builtin_saves_copy_and_edit_own_overwrites(
    qtbot: QtBot, settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upravit… na kartě: přibalený → kopie, vlastní → přepis souboru; jazyk z lišty jde do běhu."""
    from PySide6.QtWidgets import QDialog

    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    assert page.select_protocol("Fonace, základní")
    card = page.protocols._cards["Fonace, základní"]
    assert card.edit_btn.isVisibleTo(card) and page.new_btn.isEnabled()
    dialog = page.editor_dialog

    def edit_and_accept(self):  # uživatel odškrtne první feature a uloží
        first = page.editor.selected()[0]
        page.editor.picker.set_checked(first, False)
        self._removed = first
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(type(dialog), "exec", edit_and_accept)
    result = page.edit_protocol(page.current_protocol())
    assert result is not None and result.name == "Fonace, základní (kopie)" and not result.builtin
    assert dialog._removed not in result.features
    saved = page.current_protocol()
    assert saved.name == "Fonace, základní (kopie)" and saved.path is not None
    assert saved.path.parent == settings.protocols_dir() and dialog._removed not in saved.features
    assert page.protocols.task_buttons["phonation"].isChecked()

    # vlastní: úprava přepíše stejný soubor, i s novým jménem
    def rename_and_accept(self):
        self.name.setText("Moje fonace")
        self.description.setText("jen test")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(type(dialog), "exec", rename_and_accept)
    path_before = saved.path
    result = page.edit_protocol(saved)
    assert result is not None and result.path == path_before
    renamed = page.current_protocol()
    assert renamed.name == "Moje fonace" and renamed.description == "jen test"
    assert renamed.path == path_before
    assert len(list(settings.protocols_dir().glob("*.yaml"))) == 1
    # jazyk z lišty se do uloženého protokolu nepíše, do běhu ano
    page.set_language("en")
    assert "transcript" not in page.current_protocol().config
    assert page.effective_protocol().config["transcript"] == {"language": "en"}

    # Zrušit nic neuloží ani nezmění
    monkeypatch.setattr(type(dialog), "exec", lambda self: QDialog.DialogCode.Rejected)
    assert page.edit_protocol(page.current_protocol()) is None
    assert page.current_protocol().name == "Moje fonace"
    assert len(list(settings.protocols_dir().glob("*.yaml"))) == 1


def test_new_protocol_from_analysis(
    qtbot: QtBot, settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QDialog

    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    assert page.select_protocol("Spontánní řeč, akustika")
    dialog = page.editor_dialog

    def fill_and_accept(self):
        assert self.task.isVisibleTo(self) and self.task.currentData() == "spontaneous"
        self.task.setCurrentIndex(self.task.findData("reading"))
        assert page.editor.protocol().task == "reading"
        self.name.setText("Moje čtení")
        self.description.setText("popis")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(type(dialog), "exec", fill_and_accept)
    result = page.new_protocol()
    assert result is not None and result.task == "reading" and result.features
    created = page.current_protocol()
    assert created.name == "Moje čtení" and created.task == "reading"
    assert page.protocols.task_buttons["reading"].isChecked()
    assert created.path is not None and created.path.parent == settings.protocols_dir()

    # jméno, které už existuje, okno nepustí
    dialog._mode = "new"
    dialog._taken = page.protocol_names()
    dialog.name.setText("Moje čtení")
    dialog._validate()
    assert not dialog.apply_btn.isEnabled() and "existuje" in dialog.note.text()
    dialog.name.setText("Jiný")
    assert dialog.apply_btn.isEnabled()


def test_start_page_depends_on_models(settings: AppSettings, qtbot: QtBot) -> None:
    """Start je vždy Domů; bez modelů doporučí Prostředí a zkontroluje ho."""
    from speechscope_app.backend.library import fake_command

    settings.use_fake_library = False
    settings.library_command = fake_command()  # chová se jako skutečná, ale bez modelů
    window = MainWindow(settings)
    qtbot.addWidget(window)
    assert window.nav.currentRow() == PAGE_HOME
    assert window.pages.currentIndex() == PAGE_HOME
    home = window.home_page
    assert window.env_page.checking  # kontrola se po startu pustila sama, na pozadí
    wait_doctor(qtbot, window)
    assert window.env_page.report is not None
    # falešný doctor hlásí vše připravené, doporučení se řídí jeho zprávou
    assert home.recommends_environment() == (not window.env_page.report["all_ready"])
    home.set_state(library_ok=True, models_ok=False)  # laciný stav před kontrolou
    assert home.recommends_environment() and home.env_btn.property("role") == "primary"
    home.set_report({"all_ready": False})
    assert "chybí" in home.status.text()
    home.env_btn.click()
    assert window.nav.currentRow() == PAGE_ENV and window.pages.currentIndex() == PAGE_ENV

    settings.models_dir.mkdir(parents=True)
    (settings.models_dir / "whisper-large-v3-ct2").mkdir()
    window2 = MainWindow(settings)
    qtbot.addWidget(window2)
    assert window2.pages.currentIndex() == PAGE_HOME
    home2 = window2.home_page
    assert not home2.recommends_environment() and home2.env_btn.property("role") != "primary"
    assert home2.assess_btn.property("role") == "primary"  # doporučená karta
    assert window2.env_page.checking  # doctor běží na pozadí i s modely (kvůli Spustit)
    home2.start_btn.click()
    assert window2.nav.currentRow() == PAGE_BATCH


def test_models_dialog_downloads_missing(
    qtbot: QtBot, settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from speechscope_app.ui.models_dialog import ModelsDownloadDialog

    monkeypatch.setenv("SPEECHSCOPE_FAKE_DOCTOR", "missing")
    library = settings.make_library()
    assert library is not None
    dialog = ModelsDownloadDialog(library, settings.models_dir)
    qtbot.addWidget(dialog)
    assert set(dialog.selected()) == {"whisper", "stanza"}
    assert dialog.hf_token.isHidden() and dialog.gh_token.isHidden()  # pyannote ani onnx nechybí
    assert "phnrec" not in dialog.note.text()  # phnrec je na místě

    with qtbot.waitSignal(dialog.runner.finished, timeout=15000):
        dialog.start()
    assert dialog.downloaded
    text = dialog.log.toPlainText()
    assert "stahuji whisper" in text and "stahuji stanza" in text and "wavlm" not in text


def test_param_form_overrides(qtbot: QtBot) -> None:
    params = [
        ParamInfo("f_min", 60.0, "float", "", bounds={"gt": 0}),
        ParamInfo("mode", "fmin", "str", "", choices=["fmin", "fixed"]),
        ParamInfo("order", 5, "int", "", bounds={"ge": 1}),
        ParamInfo("flag", False, "bool", ""),
        ParamInfo("note", "", "str", ""),
    ]
    form = ParamForm(params)
    qtbot.addWidget(form)
    assert form.overrides() == {}
    form.set_values({"f_min": 75, "mode": "fixed", "flag": True})
    assert form.overrides() == {"f_min": 75.0, "mode": "fixed", "flag": True}
    form.set_value("f_min", 60.0)
    assert "f_min" not in form.overrides()


def test_param_form_rows_use_inputs_of_form_b(qtbot: QtBot) -> None:
    """Parametry jako řádky: číslo s jednotkou, 2–3 možnosti přepínač, víc seznam, vypínač."""
    from PySide6.QtWidgets import QComboBox

    from speechscope_app.ui.widgets.inputs import NumberField, Segmented, ToggleSwitch

    params = [
        ParamInfo("f_min", 60.0, "float", "Nejnižší povolená hodnota F0 (Hz)", bounds={"gt": 0}),
        ParamInfo(
            "mode", "fmin", "str", "Reference půltónů: fmin, fixed, median",
            choices=["fmin", "fixed", "median"],
        ),
        ParamInfo("model", "a", "str", "Model", choices=["a", "b", "c", "d"]),
        ParamInfo("order", 5, "int", "Řád (-)", bounds={"ge": 1, "le": 10}),
        ParamInfo("flag", False, "bool", "Vyhodit meze"),
    ]  # fmt: skip
    form = ParamForm(params)
    qtbot.addWidget(form)
    f_min = form._widgets["f_min"]
    assert isinstance(f_min, NumberField) and f_min.unit.text() == "Hz" and f_min.text() == "60"
    assert isinstance(form._widgets["mode"], Segmented)
    assert isinstance(form._widgets["model"], QComboBox)
    assert isinstance(form._widgets["flag"], ToggleSwitch)
    row = form._rows["mode"]
    assert row.name.text() == "Reference půltónů" and row.desc.text() == "mode · výchozí fmin"
    assert "fmin, fixed, median" in row.toolTip()  # celý popis v tooltipu řádku
    assert form._rows["f_min"].name.text() == "Nejnižší povolená hodnota F0"
    f_min.set_text("75.5")
    assert form.overrides() == {"f_min": 75.5} and form._rows["f_min"].reset_btn.isVisibleTo(form)
    order = form._widgets["order"]
    order.set_text("12")  # nad mezí: nepřijme se
    assert form.value("order") == 5
    order.step_by(2)
    assert form.value("order") == 7
    form._widgets["mode"].buttons[2].click()
    assert form.value("mode") == "median"
    form.reset()
    assert form.overrides() == {}


def _bundle(path: Path, *, valid: bool = True) -> Path:
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        if valid:
            manifest = {"format": "speechscope-models", "version": 1, "models": {"whisper": {}}}
            zf.writestr("manifest.json", json.dumps(manifest))
        else:
            zf.writestr("neco.txt", "x")
    return path


def test_models_install_dialog_unpacks_bundle(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from speechscope_app.ui import models_install_dialog as mid
    from speechscope_app.ui.models_install_dialog import ModelsInstallDialog

    library = settings.make_library()
    assert library is not None
    dialog = ModelsInstallDialog(library, settings.models_dir, archive=_bundle(tmp_path / "m.zip"))
    qtbot.addWidget(dialog)
    assert dialog.start_btn.isEnabled()
    assert dialog.bundle_keys(tmp_path / "m.zip") == ["whisper"]
    assert "whisper" in dialog.installed_keys()  # falešný doctor: všechno na místě
    # všechno z balíku už je: dotaz, Ne = nic se nespustí
    monkeypatch.setattr(
        mid.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )
    dialog.start()
    assert not dialog.runner.running and "nic se nedělalo" in dialog.status.text()
    monkeypatch.setattr(
        mid.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    with qtbot.waitSignal(dialog.runner.finished, timeout=15000):
        dialog.start()
    assert dialog.installed
    assert "whisper: rozbaleno 100 %" in dialog.log.toPlainText()

    bad = ModelsInstallDialog(
        library, settings.models_dir, archive=_bundle(tmp_path / "cizi.zip", valid=False)
    )
    qtbot.addWidget(bad)
    with qtbot.waitSignal(bad.runner.finished, timeout=15000):
        bad.start()
    assert not bad.installed
    assert "není balík" in bad.status.text()

    empty = ModelsInstallDialog(library, settings.models_dir)
    qtbot.addWidget(empty)
    assert not empty.start_btn.isEnabled()


def test_language_from_doctor_goes_to_run(qtbot: QtBot, settings: AppSettings) -> None:
    settings.last_language = "en"
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    # jazyky jsou k dispozici hned po startu (models list), bez kontroly prostředí
    assert [page.language.itemData(i) for i in range(page.language.count())][:3] == [
        "cs",
        "en",
        "de",
    ]
    wait_doctor(qtbot, window)  # doctor: totéž
    codes = [page.language.itemData(i) for i in range(page.language.count())]
    assert codes == ["cs", "en", "de", "it", "es", "fr"]  # Stanza z doctor, v pořadí knihovny
    labels = [page.language.itemText(i) for i in range(page.language.count())]
    assert labels[2:] == ["němčina", "italština", "španělština", "francouzština"]
    assert page.language_code() == "en"  # z nastavení, zachováno po načtení nabídky
    assert page.select_protocol("Spontánní řeč, lingvistika")
    proto = page.effective_protocol()
    assert proto is not None
    assert proto.config["transcript"]["language"] == "en"
    assert proto.config["nlp"]["language"] == "en"
    assert "angličtina" in page.step2_hint.text()
    page.set_language("cs")
    assert page.effective_protocol().config["transcript"]["language"] == "cs"


def test_cancel_shows_partial_results(
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPEECHSCOPE_FAKE_DELAY", "0.4")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.batch_page.set_folder(recordings)
    assert window.batch_page.select_protocol("Fonace, základní")
    run = window.run_page

    def cancel_after_first(event: object) -> None:
        if isinstance(event, contract.FileEvent):
            run.runner.cancel()

    run.runner.event.connect(cancel_after_first)
    with qtbot.waitSignal(run.finished, timeout=15000):
        window.batch_page.run_btn.click()
    state = run.runner.state
    assert 1 <= state.processed < state.total
    assert run.headline.text().startswith("Zrušeno uživatelem po")
    statuses = [run.files.item(r, run.col_status).text() for r in range(run.files.rowCount())]
    assert "běží" not in statuses and "čeká" not in statuses  # po zrušení nic „neběží“
    assert "zrušeno" in statuses or "neproběhlo" in statuses
    run._live_dir = None
    run.show_recorded(run.history.current())  # přehrání ze záznamu totéž
    replayed = [run.files.item(r, run.col_status).text() for r in range(run.files.rowCount())]
    assert "běží" not in replayed and "čeká" not in replayed
    names = [run.files.item(r, run.COL_FILE).text() for r in range(run.files.rowCount())]
    assert all(names) and "p03_short.wav" in names  # i nezačaté nahrávky mají jméno
    assert window.nav.currentRow() == PAGE_RESULTS
    assert "Částečný výsledek po zrušení" in window.results_page.summary.text()
    assert window.results_page.table.model().rowCount() == state.processed
    assert settings.seconds_per_file("fonace-zakladni") is None


def test_transcribe_only_writes_work_dir(
    qtbot: QtBot, settings: AppSettings, recordings: Path
) -> None:
    """Jen přepis / Jen segmentace jako režim vedle úlohy, volby přímo v kroku 3."""
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    page.set_advanced(False)
    assert page.mode_row.isHidden()  # základní režim: jen výpočet feature
    page.set_advanced(True)
    assert not page.mode_row.isHidden() and page.mode == "extract"
    assert page.select_protocol("Spontánní řeč, lingvistika")
    page.set_language("en")
    assert page.mode_buttons["transcript"].isEnabled()

    page.set_mode("transcript")
    assert page.mode == "transcript" and page.mode_buttons["transcript"].isChecked()
    assert page.protocols.isHidden() and not page.prepare_box.isHidden()
    assert page.advanced_box.isHidden() and page.step3_title.text() == "Přepis (Whisper)"
    assert page.transcribe_model.text() == "large-v3"
    assert "Jen přepis · angličtina · large-v3" in page.status.text()
    page.run_label_edit.setText("Pacienti říjen")  # název běhu platí i pro přepis
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    assert page.run_label() == ""  # po spuštění se pole vyprázdní
    state = window.run_page.runner.state
    assert state.finished and state.providers == ["transcript"] and state.n_ok == 3
    work = Path(state.out)
    assert work == settings.work_root / "work" / "spontaneous"  # pracovní složka podle úlohy
    assert (work / "transcript" / "p01.txt").is_file()
    assert window.nav.currentRow() == PAGE_RUN  # bez tabulky se na Výsledky nepřepíná
    assert window.run_page.headline.text().startswith("Hotovo")
    argv = window.run_page.log.toPlainText().splitlines()[0]
    assert "transcribe" in argv and "--language en" in argv
    assert settings.seconds_per_file("spontanni-rec-lingvistika") is None
    run_dirs = list(settings.work_root.glob("*_pacienti-rijen-prepis"))
    assert len(run_dirs) == 1 and (run_dirs[0] / "speechscope.log").is_file()
    assert not list(settings.work_root.glob("*_spontanni-rec-lingvistika-prepis"))
    # kopie přepisů ve složce běhu (bez otisku .json), živé zůstávají ve work
    copies = sorted(p.name for p in (run_dirs[0] / "transcript").iterdir())
    assert copies == [
        "p01.txt",
        "p01.words.json",
        "p03_short.txt",
        "p03_short.words.json",
        "p04.txt",
        "p04.words.json",
    ]
    run_file = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
    assert run_file["prepare"] == "transcript" and run_file["copied"] == 6
    assert run_file["label"] == "Pacienti říjen"
    assert run_file["work_dir"] == str(work)
    results = window.results_page
    results.refresh()  # bez tabulky se Výsledky samy neotvírají
    assert results.history.select_dir(run_dirs[0])
    results.show_run(results.history.current())
    assert results.title_label.text() == "Pacienti říjen"  # název, ne protokol
    assert results.history.card(results.history.current_row()).title.text() == "Pacienti říjen"
    assert not results.work_btn.isHidden() and results._work_dir == work / "transcript"
    assert "Kopie ve složce běhu (3): p01.txt, p03_short.txt, p04.txt." in results.summary.text()
    assert str(work / "transcript") in results.summary.text()

    page.set_mode("segments")
    assert page.step3_title.text() == "Segmentace" and not page.segment_opts.isHidden()
    assert page.segment_model.currentData() == "conformer"  # protokol má auto → conformer
    assert page.segment_model.findData("auto") == -1
    page.cut_audio.setChecked(True)
    assert "Jen segmentace · conformer" in page.status.text()
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    assert window.run_page.runner.state.providers == ["segments"]
    assert (work / "segments" / "p01.txt").is_file()
    first = window.run_page.log.toPlainText().splitlines()[0]
    assert "--model conformer" in first and "--cut-audio" in first
    # vyříznutý zvuk se přesune do složky běhu, ve work nezůstane
    seg_dir = next(settings.work_root.glob("*_spontanni-rec-lingvistika-segmentace"))
    assert sorted(p.name for p in (seg_dir / "cut").iterdir()) == [
        "p01.wav",
        "p03_short.wav",
        "p04.wav",
    ]
    assert not (work / "segments" / "cut").exists()
    assert (seg_dir / "segments" / "p01.txt").is_file()
    results.refresh()
    assert results.history.select_dir(seg_dir)
    results.show_run(results.history.current())
    assert "Vyříznutý zvuk (3) ve složce cut." in results.summary.text()
    assert results._work_dir == work / "segments"

    # fonace a DDK: přepis nedává smysl, zůstává jen segmentace
    page.set_mode("transcript")
    assert page.select_protocol("Fonace, základní")
    assert not page.mode_buttons["transcript"].isEnabled() and page.mode == "segments"
    page.set_mode("transcript")
    assert page.mode == "extract"  # nepovolený režim spadne na výpočet feature
    assert not page.protocols.isHidden() and page.step3_title.text() == "Protokol"
    page.set_mode("segments")
    # DDK: slabiky hledá ddk_events, segmentace ani přepis nedávají smysl
    assert page.select_protocol("DDK, základní")
    assert page.mode == "extract"
    assert not page.mode_buttons["segments"].isEnabled()
    assert not page.mode_buttons["transcript"].isEnabled()
    page.set_mode("segments")
    assert page.mode == "extract"
    assert page.select_protocol("Fonace, základní")
    assert page.mode_buttons["segments"].isEnabled()
    page.set_mode("segments")
    page.set_advanced(False)  # základní režim vrátí výpočet feature a řadu schová
    assert page.mode == "extract" and page.mode_row.isHidden()


def test_run_label_names_folder(
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPEECHSCOPE_FAKE_DELAY", "0.05")  # ať má statistika co měřit
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    assert page.select_protocol("Fonace, základní")
    page.run_label_edit.setText("  Kontrola po 3 měsících ")
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    run_dir = next(settings.work_root.glob("*_kontrola-po-3-mesicich"))
    assert not list(settings.work_root.glob("*_fonace-zakladni"))
    info = read_run(run_dir)
    assert info.label == "Kontrola po 3 měsících" and info.protocol_name == "Fonace, základní"
    assert page.run_label() == ""  # název platí pro jeden běh
    assert settings.seconds_per_file("fonace-zakladni") is not None  # statistika podle protokolu


def test_remove_recordings_from_list(qtbot: QtBot, settings: AppSettings, recordings: Path) -> None:
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    assert page.files.rowCount() == 4 and not page.remove_btn.isEnabled()
    assert page.restore_btn.isHidden()
    names = [page.files.item(r, 0).text() for r in range(4)]
    page.files.selectRow(1)
    page.files.selectionModel().select(
        page.files.model().index(2, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert page.remove_btn.isEnabled() and page.selected_rows() == [1, 2]
    page.remove_selected()
    assert [page.files.item(r, 0).text() for r in range(page.files.rowCount())] == [
        names[0],
        names[3],
    ]
    assert "2 nahrávek (2 odebráno)" in page.step1_hint.text()
    assert "(2 odebráno)" in page.status1.text()
    assert not page.restore_btn.isHidden() and page.restore_btn.text() == "Vrátit odebrané (2)"
    # přeskenování téže složky odebrané nevrátí, jiná složka ano
    page.rescan()
    assert page.files.rowCount() == 2
    assert page.select_protocol("Fonace, základní")
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    assert window.run_page.runner.state.total == 2
    page.restore_excluded()
    assert page.files.rowCount() == 4 and page.restore_btn.isHidden()
    page.files.selectRow(0)
    page.remove_selected()
    assert page.files.rowCount() == 3 and page.selected_rows() == [0]  # Delete jde opakovat
    page.set_folder(recordings.parent)
    page.set_folder(recordings)
    assert page.files.rowCount() == 4 and not page._excluded


def test_manifest_metadata_reach_results(
    qtbot: QtBot, settings: AppSettings, recordings: Path
) -> None:
    (recordings / "manifest.csv").write_text(
        "\ufeffpath;pacient;skupina\np01.wav;PD001;PD\nsub/p04.flac;HC002;HC\n", encoding="utf-8"
    )
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    assert page.manifest() is not None
    headers = [page.files.horizontalHeaderItem(c).text() for c in range(page.files.columnCount())]
    assert page.files.columns == ["subject", "group"]  # kanonické názvy sloupců
    assert headers[-2:] == ["PACIENT", "SKUPINA"]  # v hlavičce popisky velkými písmeny
    assert "2 z 4" in page.manifest_label.text()
    assert page.select_protocol("Fonace, základní")
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    frame = window.results_page._frame
    assert frame is not None and "subject" in frame.columns
    by_file = dict(zip(frame["file"], frame["subject"].fillna(""), strict=True))
    assert by_file["p01.wav"] == "PD001" and by_file["p02_bad.wav"] == ""
    run_dir = next(settings.work_root.glob("*_fonace-zakladni"))
    assert (run_dir / "manifest.csv").is_file()

    page.clear_manifest()
    assert page.manifest() is None and page.files.columnCount() == 3  # + délka, ruční vstupy
    assert page.files.item(0, 2).text() == "labely, přepis"  # p01 má labely i přepis
    page.set_advanced(False)
    assert page.files.columnCount() == 2


def test_metadata_edits_and_custom_columns_reach_run(
    qtbot: QtBot, settings: AppSettings, recordings: Path
) -> None:
    """Krok 1 Analýzy: sloupce metadat jdou přidat a upravit; do běhu jde upravený manifest."""
    (recordings / "manifest.csv").write_text(
        "\ufeffpath;pacient\np01.wav;PD001\n", encoding="utf-8"
    )
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    assert page.meta_edits() == 0 and page.edits_btn.isHidden()
    assert page.add_meta_column("vek") == "age" and page.add_meta_column("path") == ""
    headers = [""] * page.files.meta_from + page.files.columns  # klíče podle pozice
    assert headers[-2:] == ["subject", "age"]
    assert page.set_meta(recordings / "p01.wav", "age", "64,5") == "64.5"
    assert page.set_meta(recordings / "p03_short.wav", "subject", "HC003") == "HC003"
    assert page.set_meta(recordings / "p03_short.wav", "pohlavi", "žena") == "f"
    assert page.set_meta(recordings / "p03_short.wav", "datum", "14. 10. 2026") == "2026-10-14"
    manifest = page.manifest()
    assert manifest is not None and manifest.columns == ["subject", "age", "sex", "visit"]
    assert manifest.meta_for(recordings / "p01.wav") == {
        "subject": "PD001",
        "age": "64.5",
        "sex": "",
        "visit": "",
    }
    assert manifest.meta_for(recordings / "p03_short.wav")["sex"] == "f"
    assert "upraveno: 4" in page.manifest_label.text() and not page.edits_btn.isHidden()
    # úprava přímo v buňce tabulky (dvojklik): věk p01 na 70, shoda s manifestem úpravu ruší
    age_col = headers.index("age")
    page.files.item(0, age_col).setText("70")
    assert page.manifest().meta_for(recordings / "p01.wav")["age"] == "70"
    page.files.item(0, headers.index("subject")).setText("PD001")  # stejné jako manifest
    assert page.meta_edits() == 4 and not page.open_recording(0, age_col)

    assert page.select_protocol("Fonace, základní")
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    frame = window.results_page._frame
    assert frame is not None and {"subject", "age", "sex"} <= set(frame.columns)
    by_file = frame.set_index("file")
    assert str(by_file.loc["p01.wav", "age"]) in ("70", "70.0")
    assert (
        by_file.loc["p03_short.wav", "subject"] == "HC003"
        and by_file.loc["p03_short.wav", "sex"] == "f"
    )
    assert (recordings / "manifest.csv").read_text(
        encoding="utf-8-sig"
    ) == "path;pacient\np01.wav;PD001\n"
    assert not (settings.work_root / "export").exists()

    # Vyplnit vybrané: jedna hodnota pro označené řádky; Uložit manifest… mimo složku
    page.files.clearSelection()
    page.files.selectRow(1)
    page.files.selectionModel().select(
        page.files.model().index(2, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert page.fill_btn.isEnabled() and page.fill_selected("skupina", "HC") == 2
    assert page.manifest().meta_for(recordings / "p02_bad.wav")["group"] == "HC"
    assert page.manifest().meta_for(recordings / "p01.wav")["group"] == ""
    saved = page.save_manifest(settings.work_root / "export" / "manifest.csv")
    assert saved is not None
    text = saved.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "path;subject;age;sex;visit;group"
    # mimo složku nahrávek jsou cesty absolutní; nahrávka bez metadat má prázdné sloupce
    assert any("p04.flac" in line and line.endswith(";;;;;") for line in text.splitlines())
    assert page.meta_edits() == 6  # uložení jinam úpravy nemění
    # uložení do složky nahrávek: načte se jako manifest, úpravy se stanou základem
    saved = page.save_manifest(recordings / "manifest.csv")
    assert saved == recordings / "manifest.csv" and page.meta_edits() == 0
    assert page.base_manifest() is not None and page.base_manifest().columns == [
        "subject",
        "age",
        "sex",
        "visit",
        "group",
    ]
    assert page.manifest().meta_for(recordings / "p03_short.wav")["sex"] == "f"
    (recordings / "manifest.csv").write_text(
        "\ufeffpath;pacient\np01.wav;PD001\n", encoding="utf-8"
    )
    page.set_manifest(None)
    page.set_meta(recordings / "p01.wav", "age", "64.5")

    page.discard_meta_edits()
    assert page.meta_edits() == 0 and page.manifest().columns == ["subject"]
    page.set_meta(recordings / "p01.wav", "skupina", "PD")
    assert page.manifest().columns == ["subject", "group"]
    page.clear_manifest()  # bez souboru zůstanou vlastní sloupce a úpravy
    assert page.manifest().columns == ["group"] and "Vlastní metadata" in page.manifest_label.text()
    page.set_folder(recordings / "sub")  # jiná složka = úpravy pryč
    assert page.manifest() is None and page.meta_edits() == 0


def test_run_asks_when_provider_not_ready(
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setenv("SPEECHSCOPE_FAKE_DOCTOR", "missing")  # transcript a nlp nepřipravené
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    assert page.select_protocol("Spontánní řeč, lingvistika")
    assert page._doctor is None  # start na Datech, doctor ještě neběžel
    asked: list[str] = []

    def say_no(parent, title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(say_no))
    # Spustit před zprávou doctor: čeká na kontrolu z pozadí (dialog se Storno), neblokuje
    page.run_btn.click()
    assert page._run_after_doctor is not None and page._doctor_wait is not None
    assert not asked
    page._doctor_wait.canceled.emit()  # Storno = nespouštět
    assert page._run_after_doctor is None and page._doctor_wait is None
    page.run_btn.click()
    with qtbot.waitSignal(window.env_page.report_changed, timeout=15000):
        pass
    assert page._doctor_wait is None
    assert len(asked) == 1 and "Přepis (Whisper)" in asked[0] and "Stanza" in asked[0]
    assert not window.run_page.runner.running
    assert page._doctor is not None  # zpráva dorazila z pozadí

    # akustický protokol bez modelů se nikoho neptá
    assert page.select_protocol("Fonace, základní")
    assert page.missing_providers(page.effective_protocol()) == []
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    assert len(asked) == 1


def test_runs_queue_up_and_continue(
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPEECHSCOPE_FAKE_DELAY", "0.3")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    finished: list[str] = []
    window.run_page.finished.connect(
        lambda state, code, cancelled: finished.append(state.out or "")
    )

    from PySide6.QtWidgets import QMessageBox

    asked: list[str] = []
    answers = [
        QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
        QMessageBox.StandardButton.Yes,
    ]

    def ask(parent, title, text, *args, **kwargs):
        asked.append(text)
        return answers.pop(0)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(ask))

    assert page.select_protocol("Fonace, základní")
    wait_doctor(qtbot, window)  # jinak Spustit čeká na kontrolu prostředí
    page.run_btn.click()
    assert window.run_page.runner.running
    assert page.select_protocol("DDK, základní")
    page.run_btn.click()  # běží jiná dávka: dotaz, odpověď Ne
    assert not window.queued_jobs() and "Fonace, základní" in asked[0]
    page.run_btn.click()  # Ano
    assert page.select_protocol("Spontánní řeč, akustika")
    page.run_btn.click()
    assert [j.proto.name for j in window.queued_jobs()] == [
        "DDK, základní",
        "Spontánní řeč, akustika",
    ]
    assert "2. v pořadí" in asked[2]
    assert not window.run_page.queue_box.isHidden()
    assert window.run_page.queue_list.count() == 2
    assert window.nav.item(PAGE_RUN).badge().endswith("+2")

    window.remove_queued(1)  # pohádku vyhodit
    assert [j.proto.name for j in window.queued_jobs()] == ["DDK, základní"]

    qtbot.waitUntil(lambda: len(finished) == 2, timeout=30000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=5000)
    assert not window.queued_jobs() and window.run_page.queue_box.isHidden()
    assert window.nav.item(PAGE_RUN).badge() == ""  # po dávce bez štítku
    assert window.nav.currentRow() == PAGE_RESULTS  # až po poslední dávce
    run_dirs = sorted(p.name for p in settings.work_root.iterdir() if p.name != "work")
    assert any(n.endswith("fonace-zakladni") for n in run_dirs)
    assert any(n.endswith("ddk-zakladni") for n in run_dirs)
    assert len(run_dirs) == 2


def test_environment_warns_about_remote_desktop(qtbot: QtBot, settings: AppSettings) -> None:
    window = MainWindow(settings)
    qtbot.addWidget(window)
    env = window.env_page
    wait_doctor(qtbot, window)
    report = dict(env.report)
    assert env.gpu_cards["onnx"].pill.text() == "DirectML"
    gpu = dict(report["gpu"])
    gpu.update({"remote_session": True, "onnxruntime_gpu_usable": False})
    report["gpu"] = gpu
    env._show("0.4.0", report)
    card = env.gpu_cards["onnx"]
    assert card.pill.text() == "CPU" and "vzdálená plocha" in card.detail.text()


def test_rerun_failed_merges_into_original_table(
    qtbot: QtBot, settings: AppSettings, recordings: Path
) -> None:
    """Řádek s chybou se po „Spočítat znovu chybné“ nahradí; ostatní zůstanou."""
    import pandas as pd

    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.batch_page.set_folder(recordings)
    assert window.batch_page.select_protocol("Fonace, základní")
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        window.batch_page.run_btn.click()
    run_dir = next(settings.work_root.glob("*_fonace-zakladni"))
    csv = run_dir / "features.csv"
    frame = pd.read_csv(csv)
    # p01 uměle označit jako chybný: při opakování projde a řádek se doplní
    frame.loc[frame["file"] == "p01.wav", "error"] = "výpadek"
    value_col = [c for c in frame.columns if c.startswith("acoustic.")][0]
    frame.loc[frame["file"] == "p01.wav", value_col] = float("nan")
    frame.to_csv(csv, index=False)
    results = window.results_page
    results.load(csv)
    assert results.rerun_btn.isVisibleTo(results)

    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        results.rerun_btn.click()
    merged = pd.read_csv(csv)
    assert len(merged) == 4
    p01 = merged[merged["file"] == "p01.wav"].iloc[0]
    assert pd.isna(p01.get("error")) and pd.notna(p01[value_col])
    bad = merged[merged["file"] == "p02_bad.wav"].iloc[0]
    assert bad["error"]  # opravdová chyba zůstala
    assert "nahrazeno" in results.summary.text() and results._path == csv
    assert window.nav.currentRow() == PAGE_RESULTS
    assert any(d.name.endswith("-znovu") for d in settings.work_root.iterdir())


def test_environment_cache_cleanup(qtbot: QtBot, settings: AppSettings) -> None:
    import os
    import time

    from speechscope_app.ui.cache_dialog import CacheDialog

    work = settings.work_root / "work"
    (work / "segments").mkdir(parents=True)
    old = work / "segments" / "p01.txt"
    old.write_bytes(b"x" * 10)
    stamp = time.time() - 60 * 86400
    os.utime(old, (stamp, stamp))
    (work / "segments" / "p02.txt").write_bytes(b"y" * 10)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    env = window.env_page
    env.refresh_cache()
    assert "2 souborů, 20 B" in env.cache_detail.text() and env.cache_btn.isEnabled()
    assert "Po úlohách: segments 20 B." in env.cache_detail.text()  # staré rozvržení jménem
    (work / "ddk" / "segments").mkdir(parents=True)
    (work / "ddk" / "segments" / "p09.txt").write_bytes(b"z" * 5)
    env.refresh_cache()
    assert "Po úlohách: DDK 5 B · segments 20 B." in env.cache_detail.text()
    (work / "ddk" / "segments" / "p09.txt").unlink()
    env.refresh_cache()

    dialog = CacheDialog(work)
    qtbot.addWidget(dialog)
    assert dialog.older_than_days() == 30 and "1 souborů" in dialog.preview.text()
    dialog.everything.setChecked(True)
    assert dialog.older_than_days() is None and "2 souborů" in dialog.preview.text()
    dialog.older.setChecked(True)
    dialog.delete_btn.click()
    assert dialog.removed is not None and dialog.removed.files == 1
    env.refresh_cache()
    assert "1 souborů, 10 B" in env.cache_detail.text() and not old.exists()


def test_home_first_setup_block(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bez modelů: blok se složkami a instalací; složky jdou změnit; po modelech zmizí."""
    from speechscope_app.backend.library import fake_command

    settings.use_fake_library = False
    settings.library_command = fake_command()
    monkeypatch.setenv("SPEECHSCOPE_FAKE_DOCTOR", "missing")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    home = window.home_page
    assert home.setup.isVisibleTo(home) and home.status.isVisibleTo(home)
    assert home.models_row.path.toolTip() == str(settings.models_dir)
    assert home.work_row.path.toolTip() == str(settings.work_root)
    assert home.install_btn.isEnabled() and "Volné místo" in home.space.text()
    assert window.env_page.models_path.text() == str(settings.models_dir)

    new_work = tmp_path / "vysledky"
    window.change_work_root(new_work)
    assert settings.work_root == new_work and home.work_row.path.toolTip() == str(new_work)
    assert str(new_work) in window.batch_page.subtitle.text()  # podtitul Analýzy sedí
    assert window.env_page.work_path.text() == str(new_work)
    new_models = tmp_path / "modely"
    new_models.mkdir()
    (new_models / "whisper-large-v3-ct2").mkdir()
    monkeypatch.delenv("SPEECHSCOPE_FAKE_DOCTOR")
    window.change_models_dir(new_models)
    assert settings.models_dir == new_models and window.library is not None
    assert str(new_models) in " ".join(window.library.argv([]) + [str(window.library.models_dir)])
    assert not home.setup.isVisibleTo(home) and "nainstalované" in home.status.text()
    assert not home.recommends_environment()

    # doctor: providery připravené, i když některý model chybí (balík kliniky) → blok pryč
    home.set_report(
        {
            "all_ready": False,
            "providers": {"transcript": {"ready": True}, "segments": {"ready": True}},
            "models": {"wavlm": {"present": False}},
        }
    )
    assert not home.setup.isVisibleTo(home)
    # provider bez modelu → blok zpět; Hotovo ho schová do dalšího startu
    home.set_report(
        {"all_ready": False, "providers": {"transcript": {"ready": False}}, "models": {}}
    )
    assert home.setup.isVisibleTo(home) and "nejsou nainstalované" in home.setup_note.text()
    home.done_btn.click()
    assert not home.setup.isVisibleTo(home)
    home.set_state(library_ok=True, models_ok=False)
    assert not home.setup.isVisibleTo(home)  # zůstává schovaný


def test_default_models_dir_next_to_installed_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from speechscope_app.backend import settings as settings_mod

    monkeypatch.setattr(settings_mod, "app_data_dir", lambda: tmp_path / "appdata")
    s = AppSettings(tmp_path / "s.ini")
    assert s.models_dir == tmp_path / "appdata" / "models"  # vývoj: jako dřív
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "SpeechScope.exe"))
    assert s.models_dir == tmp_path / "app" / "models"  # zabaleno: vedle exe
    legacy = tmp_path / "appdata" / "models" / "stanza"
    legacy.mkdir(parents=True)
    assert s.models_dir == tmp_path / "appdata" / "models"  # starší instalace s modely
    s.models_dir = tmp_path / "jinde"
    assert s.models_dir == tmp_path / "jinde"


def test_double_click_opens_recording(
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dvojklik na Analýze otevře nahrávku (nebo ruční vstup), chybějící soubor jen ohlásí."""
    from speechscope_app.ui import file_actions

    opened: list[str] = []
    monkeypatch.setattr(
        file_actions.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    notices: list[str] = []
    page.notice.connect(notices.append)
    assert page.open_recording(0)
    assert Path(opened[-1]) == recordings / "p01.wav"
    assert page.open_recording(0, column=2)  # rozšířený režim: sloupec ruční vstupy
    assert Path(opened[-1]) == recordings / "p01.labels.txt"
    page.set_advanced(False)
    assert page.open_recording(0, column=2)
    assert Path(opened[-1]) == recordings / "p01.wav"
    (recordings / "p03_short.wav").unlink()
    assert not page.open_recording(2) and "neexistuje" in notices[-1]
    assert not page.open_recording(99)

    # Výpočet: cesty řádků po startu
    page.rescan()  # smazaná nahrávka pryč ze seznamu, jinak knihovna skončí chybou
    assert page.select_protocol("Fonace, základní")
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    run = window.run_page
    assert run.path_for_row(0) == recordings / "p01.wav"
    assert run.open_recording(0) and Path(opened[-1]) == recordings / "p01.wav"
    # Výsledky: cesta z tabulky
    assert window.results_page.path_for_row(0) == recordings / "p01.wav"


def test_language_change_offers_restart(
    qtbot: QtBot, settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from speechscope_app.ui import main_window as mw

    window = MainWindow(settings)
    qtbot.addWidget(window)
    started: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        mw.QProcess,
        "startDetached",
        staticmethod(lambda prog, args: started.append((prog, args)) or True),
    )
    monkeypatch.setattr(
        mw.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    monkeypatch.setattr(sys, "argv", ["speechscope-app", "--fake"])
    window._offer_restart()
    assert started and started[0] == (sys.executable, ["-m", "speechscope_app.main", "--fake"])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["SpeechScope.exe", "--smoke"])
    assert mw.restart_command() == (sys.executable, [])


def test_delete_runs_go_to_trash(
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smazat jeden běh i všechny: složky pryč, historie i stránky bez smazaného běhu."""
    import shutil

    from speechscope_app.backend import history

    trashed: list[Path] = []

    def fake_trash(path: Path) -> None:  # v testu bez Koše
        trashed.append(path)
        shutil.rmtree(path)

    monkeypatch.setattr(history, "send_to_trash", fake_trash)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.batch_page.set_folder(recordings)
    assert window.batch_page.select_protocol("Fonace, základní")
    for _ in range(2):
        with qtbot.waitSignal(window.run_page.finished, timeout=15000):
            window.batch_page.run_btn.click()
    results = window.results_page
    assert results.history.count() == 2 and results.current_dir() is not None
    shown = results.current_dir()
    info = next(r for r in results.history.all_runs() if r.dir == shown)
    assert results.history.can_delete(info) and results.history.delete_btn.isEnabled()
    assert window.delete_run(info, confirm=False)
    assert trashed == [shown] and not shown.exists()
    assert results.history.count() == 1 and results.current_dir() != shown
    assert window.run_page.history.count() == 1

    assert window.delete_all_runs(confirm=False) == 1
    assert results.history.count() == 0 and results.current_dir() is None
    assert "Zatím žádný výstup" in results.summary.text()
    assert window.run_page.headline.text() == "Žádný výpočet"
    assert window.delete_all_runs(confirm=False) == 0


def test_font_scale_setting_changes_stylesheet(qtbot: QtBot, settings: AppSettings) -> None:
    from PySide6.QtWidgets import QApplication

    from speechscope_app.ui import theme
    from speechscope_app.ui.settings_dialog import SettingsDialog

    assert settings.font_scale == "normal"
    assert "font-size: 11pt;" in theme.stylesheet(1.0)
    assert "font-size: 14.3pt;" in theme.stylesheet(1.3)
    assert "font-size: 10pt;" in theme.stylesheet(theme.FONT_SCALES["small"])  # původní velikost
    window = MainWindow(settings)
    qtbot.addWidget(window)
    dialog = SettingsDialog(settings, window)
    qtbot.addWidget(dialog)
    dialog.font_scale.setCurrentIndex(dialog.font_scale.findData("largest"))
    dialog.accept()
    assert settings.font_scale == "largest"
    app = QApplication.instance()
    theme.apply_scale(app, theme.FONT_SCALES[settings.font_scale])
    assert abs(app.font().pointSizeF() - theme.BASE_PT * 1.3) < 0.01
    theme.apply_scale(app, 1.0)  # ostatní testy zpět na normální
    settings.font_scale = "nesmysl"
    assert settings.font_scale == "normal"


def test_analysis_two_steps(qtbot: QtBot, settings: AppSettings, recordings: Path) -> None:
    """Krok 1 nahrávky, Pokračovat až s nahrávkami; krok 2 úloha a protokol, zpět vlevo dole."""
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    assert page.current_step() == 1 and not page.continue_btn.isEnabled()
    assert "krok 1" in page.step_label.text()
    page.set_folder(recordings)
    assert page.continue_btn.isEnabled() and page.current_step() == 1
    assert "4 nahrávek" in page.status1.text()
    page.continue_btn.click()
    assert page.current_step() == 2 and "krok 2" in page.step_label.text()
    assert page.select_protocol("Fonace, základní") and page.run_btn.isEnabled()
    page.back_btn.click()
    assert page.current_step() == 1
    page._continue_if_ready()
    assert page.current_step() == 2
    # po spuštění zůstane krok 2 pro další dávku
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    assert page.current_step() == 2
    assert len(page.protocols.task_buttons) == 4  # úlohy v jedné řadě


def test_home_cards_running_strip_and_norm_warning(
    qtbot: QtBot, settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Domů: karty vedou na stránky, pruh „právě běží“ sleduje dávku, stará norma varuje."""
    from PySide6.QtWidgets import QDialog
    from test_norms import controls_frame

    from speechscope_app.backend.norms import Norm, NormInfo

    Norm(
        NormInfo(
            name="Stará norma", feature_versions={"phonation": {"acoustic.quality.cpp": "1.2"}}
        ),
        controls_frame(),
    ).save(settings.norms_dir() / "stara")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    home = window.home_page
    assert window.nav.currentRow() == PAGE_HOME and window.nav_labels[PAGE_HOME] == "Domů"
    assert window.nav_labels[PAGE_BATCH] == "Analýza nahrávek"
    assert home.running.isHidden() and not home.norm_warning.isHidden()
    assert "Stará norma" in home.norm_warning.text()
    home.start_btn.click()
    assert window.nav.currentRow() == PAGE_BATCH
    window.nav.setCurrentRow(PAGE_HOME)
    home.assess_btn.click()  # průvodce v kroku 2, nabídka zůstává na Domů
    assert window.nav.currentRow() == PAGE_HOME and home.step() == 1
    assert home.builder is not None and home.builder.step() == 0
    home.home_btn.click()
    assert home.step() == 0 and home.builder is None
    # odkaz „…nebo z hotových běhů“ vede na Pacienty a rovnou do dialogu nad běhy
    home.assess_btn.click()
    monkeypatch.setattr(
        "speechscope_app.ui.pages.assessment.AssessmentDialog.exec",
        lambda self: QDialog.DialogCode.Rejected,
    )
    home.runs_link.linkActivated.emit("runs")
    assert window.nav.currentRow() == PAGE_ASSESSMENT and home.step() == 0
    home.env_btn.click()
    assert window.nav.currentRow() == PAGE_ENV
    window.nav.setCurrentRow(PAGE_HOME)

    # pruh právě běží: název dávky, postup, fronta; po konci zmizí
    window._running_label = "Pacienti září"
    window._queue.append(object())  # type: ignore[arg-type]
    window._show_progress("1/3")
    assert not home.running.isHidden() and home.running_title.text() == "Pacienti září"
    assert home.running_detail.text() == "1/3 nahrávek · ve frontě: 1"
    window._queue.clear()
    window._show_progress("")
    assert home.running.isHidden()
    home.run_btn.click()
    assert window.nav.currentRow() == PAGE_RUN


def test_recordings_table_grouping_search_and_missing(
    qtbot: QtBot, settings: AppSettings, recordings: Path
) -> None:
    """Krok 1 Analýzy: Seskupit podle sloupce i podsložky, hledání, dlaždice bez údajů."""
    (recordings / "manifest.csv").write_text(
        "﻿path;pacient;vek\np01.wav;PD001;64\np02_bad.wav;PD001;64\np03_short.wav;HC002;\n",
        encoding="utf-8",
    )
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    files = page.files
    assert files.rowCount() == 4 and files.group_by == ""
    # p03 nemá věk, p04 (bez řádku v manifestu) nemá nic: dlaždice a žluté „doplnit“
    assert not page.missing_tile.isHidden() and page.missing_tile.value.text() == "2"
    assert page.patients_tile.value.text() == "2"
    page.missing_tile.clicked.emit()
    assert [page._recordings[i].name for i in files.visible_indices()] == [
        "p03_short.wav",
        "p04.flac",
    ]
    page.missing_tile.clicked.emit()
    assert len(files.visible_indices()) == 4

    # seskupení podle pacienta: nadpisy skupin, prázdný pacient na konci
    assert page.set_group_by("subject")
    assert files.rowCount() == 4 + 3 and files.is_group_header(0)
    assert files.item(0, 0).text().startswith("HC002")
    assert "PD001" in files.item(2, 0).text() and "64" in files.item(2, 0).text()
    assert files.item(files.rowCount() - 2, 0).text().startswith("bez hodnoty")
    assert files.data_index(0) is None and not page.open_recording(0)
    # klik na nadpis označí skupinu, Vyplnit vybrané ji doplní
    files.cellClicked.emit(2, 0)
    pd = sorted(page._recordings[i].name for i in page.selected_rows())
    assert pd == ["p01.wav", "p02_bad.wav"] and page.fill_btn.isEnabled()
    assert page.fill_selected("skupina", "PD") == 2
    assert page.manifest().meta_for(recordings / "p01.wav")["group"] == "PD"
    # úprava buňky přesune řádek do jiné skupiny
    i = next(i for i, rec in enumerate(page._recordings) if rec.name == "p04.flac")
    subject_col = files.meta_from + files.columns.index("subject")
    files.item(files.table_row(i), subject_col).setText("HC002")
    assert files.rowCount() == 4 + 2
    # Odebrat ve seskupené tabulce odebírá nahrávky, ne nadpisy
    files.clearSelection()
    files.select_index(i)
    page.remove_selected()
    assert "p04.flac" not in [rec.name for rec in page._recordings]

    # podsložka jako volba jen se sken včetně podsložek; hledání schová i prázdné skupiny
    page.restore_excluded()
    assert page.set_group_by(GROUP_FOLDER)
    titles = [files.item(r, 0).text() for r in range(files.rowCount()) if files.is_group_header(r)]
    assert titles[0].startswith("sub") and titles[1].startswith("(hlavní složka)")
    page.search.setText("p04")
    assert [page._recordings[i].name for i in files.visible_indices()] == ["p04.flac"]
    main = next(r for r in range(files.rowCount()) if files.item(r, 0).text().startswith("(hl"))
    assert files.isRowHidden(main)  # skupina bez shody se schová celá
    page.search.clear()
    page.recursive.setChecked(False)
    assert page.group_combo.findData(GROUP_FOLDER) < 0 and files.group_by == ""


def test_sidebar_collapse_badge_and_environment(qtbot: QtBot, settings: AppSettings) -> None:
    """Lišta: Ctrl+číslo přepne stránku, sbalení se pamatuje, štítek s průběhem, stav prostředí."""
    from speechscope_app.ui.widgets.sidebar_nav import COLLAPSED_WIDTH, parse_progress

    window = MainWindow(settings)
    qtbot.addWidget(window)
    nav = window.nav
    wait_doctor(qtbot, window)
    assert nav.env.state() == "ok" and nav.env.label() == "Prostředí v pořádku"
    nav.environment_clicked.emit()
    assert nav.currentRow() == PAGE_ENV and window.pages.currentIndex() == PAGE_ENV
    nav.item(PAGE_BATCH).click()
    assert nav.currentRow() == PAGE_BATCH and nav.item(PAGE_BATCH).isChecked()
    assert not nav.item(PAGE_ENV).isChecked()

    assert not nav.collapsed() and nav.width() > COLLAPSED_WIDTH
    nav.toggle.click()
    assert nav.collapsed() and nav.width() == COLLAPSED_WIDTH and settings.sidebar_collapsed
    assert nav.brand_names.isHidden()
    nav.toggle.click()
    assert not nav.collapsed() and not settings.sidebar_collapsed

    window._show_progress("12/40")
    assert nav.item(PAGE_RUN).badge() == "12/40"
    assert nav.item(PAGE_RUN).progress() == pytest.approx(0.3)
    assert nav.item(PAGE_RUN).text() == "Výsledky"
    window._show_progress("")
    assert nav.item(PAGE_RUN).badge() == "" and nav.item(PAGE_RUN).progress() is None
    assert parse_progress("segmentace") is None and parse_progress("3/0") is None

    settings.sidebar_collapsed = True
    window2 = MainWindow(settings)
    qtbot.addWidget(window2)
    assert window2.nav.collapsed() and window2.nav.width() == COLLAPSED_WIDTH


def test_analysis_warns_about_short_recordings(
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Krátké nahrávky podle hlavičky: varování pod úlohou a dotaz před Spustit."""
    from dataclasses import replace

    from PySide6.QtWidgets import QMessageBox

    window = MainWindow(settings)
    qtbot.addWidget(window)
    wait_doctor(qtbot, window)
    page = window.batch_page
    page.set_folder(recordings)
    page._found = [replace(r, duration=3.0 if "short" in r.name else 30.0) for r in page._found]
    page._apply_exclusions()
    page._update_run_state()  # skenování složky to volá samo, tady se délky podstrčily
    assert page.select_protocol("Fonace, základní")
    assert not page.short_warning.isHidden() and "p03_short.wav" in page.short_warning.text()
    assert [r.name for r in page.short_recordings()] == ["p03_short.wav"]
    assert page.select_protocol("DDK, základní")  # DDK práh délky nemá
    assert page.short_warning.isHidden() and page.short_recordings() == []
    assert page.select_protocol("Fonace, základní")

    asked: list[str] = []
    answer = [QMessageBox.StandardButton.No]
    monkeypatch.setattr(
        "speechscope_app.ui.pages.batch.QMessageBox.question",
        lambda _parent, _title, text, *a, **k: (asked.append(text), answer[0])[1],
    )
    started: list = []
    page.run_requested.connect(lambda proto, paths: started.append(proto))
    page.run_btn.click()
    assert asked and "1 nahrávek je kratších než 5 s" in asked[0] and not started
    answer[0] = QMessageBox.StandardButton.Yes
    page.run_btn.click()
    assert len(started) == 1


def test_progress_replayed_only_on_progress_tab(
    qtbot: QtBot, settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otevření běhu na Hodnotách Průběh nepřehrává (zdržovalo kliknutí), záložka Průběh ano."""
    from test_visits import make_run

    settings.work_root.mkdir(parents=True)
    make_run(
        settings.work_root,
        "2026-10-14_10-12-00_fonace",
        [{"file": "a.wav", "path": "C:/d/a.wav", "task": "phonation"}],
    )
    window = MainWindow(settings)
    qtbot.addWidget(window)
    runs = window.runs_page
    replayed: list = []
    monkeypatch.setattr(window.run_page, "show_recorded", lambda info: replayed.append(info))
    runs.refresh_list()
    runs.open_row(0)
    qtbot.waitUntil(lambda: not runs.loading())
    assert runs.tab() == "values" and replayed == []
    runs.set_tab("progress")
    assert len(replayed) == 1
    runs.set_tab("values")
    runs.set_tab("progress")
    assert len(replayed) == 1  # podruhé se nepřehrává


def test_open_row_switches_at_once_and_last_click_wins(qtbot: QtBot, settings: AppSettings) -> None:
    """Klik v přehledu hned ukáže detail s „Načítám…“; rychlé proklikání ukáže poslední běh."""
    from test_visits import make_run

    settings.work_root.mkdir(parents=True)
    for name, file in (
        ("2026-10-14_10-12-00_fonace", "a.wav"),
        ("2026-10-15_09-00-00_cteni", "b.wav"),
    ):
        make_run(
            settings.work_root, name, [{"file": file, "path": f"C:/d/{file}", "task": "phonation"}]
        )
    window = MainWindow(settings)
    qtbot.addWidget(window)
    runs, results = window.runs_page, window.results_page
    runs.refresh_list()
    runs.open_row(0)
    assert runs.in_detail() and runs.loading() and results.empty.text() == "Načítám…"
    runs.open_row(1)  # druhý klik dřív, než se první načetl
    qtbot.waitUntil(lambda: not runs.loading())
    assert results.current_dir() == runs._rows[1].dir
    qtbot.wait(50)
    assert results.current_dir() == runs._rows[1].dir  # první klik se už nedokončil
    # zpět na přehled před načtením: detail se znovu neotevře
    runs.open_row(0)
    runs.show_overview()
    qtbot.wait(50)
    assert not runs.in_detail()
