"""Kouřový test celého okna nad falešnou knihovnou."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from speechscope_app import contract
from speechscope_app.backend.history import read_run
from speechscope_app.backend.library import ParamInfo
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import (
    PAGE_BATCH,
    PAGE_ENV,
    PAGE_RESULTS,
    PAGE_RUN,
    PAGE_WELCOME,
    MainWindow,
)
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
    window.env_page.refresh()
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

    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        window.batch_page.run_btn.click()
        run_dir = window._running_dir
        assert read_run(run_dir).status == "running"  # záznam hned při startu
    state = window.run_page.runner.state
    assert state.finished and state.total == 4
    assert window.nav.currentRow() == PAGE_RESULTS
    assert read_run(run_dir).status == "ok"
    # tabulka běhu: řádek na nahrávku, výsledek, nabídka bez postupu po konci
    run = window.run_page
    assert run.files.rowCount() == 4
    statuses = [run.files.item(r, run.col_status).text() for r in range(4)]
    assert sorted(statuses) == ["chyba", "ok", "ok", "ok"]
    assert window.nav.item(PAGE_RUN).text() == "Výpočet"
    assert run.headline.text().startswith("Hotovo za")
    assert settings.seconds_per_file("fonace-zakladni") is not None
    assert window.results_page.table.model().rowCount() == 4

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
    assert json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))["label"] == (
        "Pacienti září"
    )
    assert window.rename_run(info, "")  # prázdný = podle protokolu
    assert results.history.card(0).title.text() == "Fonace, základní"
    # záznam průběhu a jeho přehrání na Výpočtu
    events = (run_dirs[0] / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert events[0].startswith('{"event": "inputs"')  # řádek GUI se seznamem nahrávek
    assert events[1].startswith('{"event": "start"') and events[-1].startswith('{"event": "saved"')
    assert run.history.count() == 1
    run._live_dir = None  # jako po novém startu aplikace: běh je jen v historii
    run.show_recorded(run.history.current())
    assert run.files.rowCount() == 4
    replayed = sorted(run.files.item(r, run.col_status).text() for r in range(4))
    assert replayed == ["chyba", "ok", "ok", "ok"]
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
    assert page.select_protocol("Pohádka, akustika")
    dialog = page.editor_dialog

    def fill_and_accept(self):
        assert self.task.isVisibleTo(self) and self.task.currentData() == "story"
        self.task.setCurrentIndex(self.task.findData("monologue"))
        assert page.editor.protocol().task == "monologue"
        self.name.setText("Můj monolog")
        self.description.setText("popis")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(type(dialog), "exec", fill_and_accept)
    result = page.new_protocol()
    assert result is not None and result.task == "monologue" and result.features
    created = page.current_protocol()
    assert created.name == "Můj monolog" and created.task == "monologue"
    assert page.protocols.task_buttons["monologue"].isChecked()
    assert created.path is not None and created.path.parent == settings.protocols_dir()

    # jméno, které už existuje, okno nepustí
    dialog._mode = "new"
    dialog._taken = page.protocol_names()
    dialog.name.setText("Můj monolog")
    dialog._validate()
    assert not dialog.apply_btn.isEnabled() and "existuje" in dialog.note.text()
    dialog.name.setText("Jiný")
    assert dialog.apply_btn.isEnabled()


def test_start_page_depends_on_models(settings: AppSettings, qtbot: QtBot) -> None:
    """Start je vždy uvítání bez záložky; bez modelů doporučí Prostředí a zkontroluje ho."""
    from speechscope_app.backend.library import fake_command

    settings.use_fake_library = False
    settings.library_command = fake_command()  # chová se jako skutečná, ale bez modelů
    window = MainWindow(settings)
    qtbot.addWidget(window)
    assert window.nav.currentRow() == -1
    assert window.pages.currentIndex() == PAGE_WELCOME
    welcome = window.welcome_page
    assert window.env_page.report is not None  # refresh proběhl sám
    # falešný doctor hlásí vše připravené, doporučení se řídí jeho zprávou
    assert welcome.recommends_environment() == (not window.env_page.report["all_ready"])
    welcome.set_state(library_ok=True, models_ok=False)  # laciný stav před kontrolou
    assert welcome.recommends_environment() and welcome.env_btn.property("role") == "primary"
    welcome.set_report({"all_ready": False})
    assert "chybí" in welcome.status.text()
    welcome.env_btn.click()
    assert window.nav.currentRow() == PAGE_ENV and window.pages.currentIndex() == PAGE_ENV

    settings.models_dir.mkdir(parents=True)
    (settings.models_dir / "whisper-large-v3-ct2").mkdir()
    window2 = MainWindow(settings)
    qtbot.addWidget(window2)
    assert window2.pages.currentIndex() == PAGE_WELCOME
    welcome2 = window2.welcome_page
    assert not welcome2.recommends_environment()
    assert welcome2.start_btn.property("role") == "primary"
    assert window2.env_page.report is None  # bez modelů chybějících se doctor nevolá
    welcome2.start_btn.click()
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
    window.env_page.refresh()  # doctor: totéž
    codes = [page.language.itemData(i) for i in range(page.language.count())]
    assert codes == ["cs", "en", "de", "it", "es", "fr"]  # Stanza z doctor, v pořadí knihovny
    labels = [page.language.itemText(i) for i in range(page.language.count())]
    assert labels[2:] == ["němčina", "italština", "španělština", "francouzština"]
    assert page.language_code() == "en"  # z nastavení, zachováno po načtení nabídky
    assert page.select_protocol("Pohádka, lingvistika")
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
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from speechscope_app.ui.prepare_dialog import SegmentDialog, TranscribeDialog

    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    assert page.select_protocol("Pohádka, lingvistika")
    page.set_language("cs")
    assert page.transcribe_btn.isEnabled() and page.segment_btn.isEnabled()

    seen: list[object] = []

    def accept_transcribe(self):  # v dialogu přepnout jazyk na angličtinu
        seen.append(self)
        self.language.setCurrentIndex(self.language.findData("en"))
        return 1

    monkeypatch.setattr(TranscribeDialog, "exec", accept_transcribe)
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.transcribe_btn.click()
    assert len(seen) == 1 and "large-v3" in seen[0].form.itemAt(3).widget().text()
    state = window.run_page.runner.state
    assert state.finished and state.providers == ["transcript"] and state.n_ok == 3
    work = Path(state.out)
    assert work == settings.work_root / "work"
    assert (work / "transcript" / "p01.txt").is_file()
    assert window.nav.currentRow() == PAGE_RUN  # bez tabulky se na Výsledky nepřepíná
    assert window.run_page.headline.text().startswith("Hotovo")
    argv = window.run_page.log.toPlainText().splitlines()[0]
    assert "transcribe" in argv and "--language en" in argv
    assert settings.seconds_per_file("pohadka-lingvistika") is None
    run_dirs = list(settings.work_root.glob("*_pohadka-lingvistika-prepis"))
    assert len(run_dirs) == 1 and (run_dirs[0] / "speechscope.log").is_file()

    def accept_segment(self):  # výchozí conformer (protokol má auto), zapnout cut-audio
        assert self.model.currentData() == "conformer"
        assert self.model.findData("auto") == -1
        self.cut_audio.setChecked(True)
        return 1

    monkeypatch.setattr(SegmentDialog, "exec", accept_segment)
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.segment_btn.click()
    assert window.run_page.runner.state.providers == ["segments"]
    assert (work / "segments" / "p01.txt").is_file()
    first = window.run_page.log.toPlainText().splitlines()[0]
    assert "--model conformer" in first and "--cut-audio" in first

    # Zrušit v dialogu nic nespustí
    monkeypatch.setattr(SegmentDialog, "exec", lambda self: 0)
    page.segment_btn.click()
    assert not window.run_page.runner.running


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
    assert headers[-2:] == ["pacient", "skupina"]
    assert "2 z 4" in page.manifest_label.text()
    assert page.select_protocol("Fonace, základní")
    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        page.run_btn.click()
    frame = window.results_page._frame
    assert frame is not None and "pacient" in frame.columns
    by_file = dict(zip(frame["file"], frame["pacient"].fillna(""), strict=True))
    assert by_file["p01.wav"] == "PD001" and by_file["p02_bad.wav"] == ""
    run_dir = next(settings.work_root.glob("*_fonace-zakladni"))
    assert (run_dir / "manifest.csv").is_file()

    page.clear_manifest()
    assert page.manifest() is None and page.files.columnCount() == 3  # + délka, ruční vstupy
    assert page.files.item(0, 2).text() == "labely, přepis"  # p01 má labely i přepis
    page.set_advanced(False)
    assert page.files.columnCount() == 2


def test_run_asks_when_provider_not_ready(
    qtbot: QtBot, settings: AppSettings, recordings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setenv("SPEECHSCOPE_FAKE_DOCTOR", "missing")  # transcript a nlp nepřipravené
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    page.set_folder(recordings)
    assert page.select_protocol("Pohádka, lingvistika")
    assert page._doctor is None  # start na Datech, doctor ještě neběžel
    asked: list[str] = []

    def say_no(parent, title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(say_no))
    page.run_btn.click()
    assert len(asked) == 1 and "Přepis (Whisper)" in asked[0] and "Stanza" in asked[0]
    assert not window.run_page.runner.running
    assert page._doctor is not None  # doctor se doptal sám

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
    page.run_btn.click()
    assert window.run_page.runner.running
    assert page.select_protocol("DDK, základní")
    page.run_btn.click()  # běží jiná dávka: dotaz, odpověď Ne
    assert not window.queued_jobs() and "Fonace, základní" in asked[0]
    page.run_btn.click()  # Ano
    assert page.select_protocol("Pohádka, akustika")
    page.run_btn.click()
    assert [j.proto.name for j in window.queued_jobs()] == ["DDK, základní", "Pohádka, akustika"]
    assert "2. v pořadí" in asked[2]
    assert not window.run_page.queue_box.isHidden()
    assert window.run_page.queue_list.count() == 2
    assert "(+2)" in window.nav.item(PAGE_RUN).text()

    window.remove_queued(1)  # pohádku vyhodit
    assert [j.proto.name for j in window.queued_jobs()] == ["DDK, základní"]

    qtbot.waitUntil(lambda: len(finished) == 2, timeout=30000)
    qtbot.waitUntil(lambda: not window.run_page.runner.running, timeout=5000)
    assert not window.queued_jobs() and window.run_page.queue_box.isHidden()
    assert window.nav.item(PAGE_RUN).text() == "Výpočet"
    assert window.nav.currentRow() == PAGE_RESULTS  # až po poslední dávce
    run_dirs = sorted(p.name for p in settings.work_root.iterdir() if p.name != "work")
    assert any(n.endswith("fonace-zakladni") for n in run_dirs)
    assert any(n.endswith("ddk-zakladni") for n in run_dirs)
    assert len(run_dirs) == 2


def test_environment_warns_about_remote_desktop(qtbot: QtBot, settings: AppSettings) -> None:
    window = MainWindow(settings)
    qtbot.addWidget(window)
    env = window.env_page
    env.refresh()
    report = dict(env.report)
    assert env.gpu_cards["onnx"].pill.text() == "DirectML"
    gpu = dict(report["gpu"])
    gpu.update({"remote_session": True, "onnxruntime_gpu_usable": False})
    report["gpu"] = gpu
    env._show("0.2.0", report)
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


def test_welcome_first_setup_block(
    qtbot: QtBot, settings: AppSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bez modelů: blok se složkami a instalací; složky jdou změnit; po modelech zmizí."""
    from speechscope_app.backend.library import fake_command

    settings.use_fake_library = False
    settings.library_command = fake_command()
    monkeypatch.setenv("SPEECHSCOPE_FAKE_DOCTOR", "missing")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    welcome = window.welcome_page
    assert welcome.setup.isVisibleTo(welcome) and not welcome.status.isVisibleTo(welcome)
    assert welcome.models_row.path.toolTip() == str(settings.models_dir)
    assert welcome.work_row.path.toolTip() == str(settings.work_root)
    assert welcome.install_btn.isEnabled() and "Volné místo" in welcome.space.text()
    assert window.env_page.models_path.text() == str(settings.models_dir)

    new_work = tmp_path / "vysledky"
    window.change_work_root(new_work)
    assert settings.work_root == new_work and welcome.work_row.path.toolTip() == str(new_work)
    assert str(new_work) in window.batch_page.subtitle.text()  # podtitul Analýzy sedí
    assert window.env_page.work_path.text() == str(new_work)
    new_models = tmp_path / "modely"
    new_models.mkdir()
    (new_models / "whisper-large-v3-ct2").mkdir()
    monkeypatch.delenv("SPEECHSCOPE_FAKE_DOCTOR")
    window.change_models_dir(new_models)
    assert settings.models_dir == new_models and window.library is not None
    assert str(new_models) in " ".join(window.library.argv([]) + [str(window.library.models_dir)])
    assert not welcome.setup.isVisibleTo(welcome) and welcome.status.isVisibleTo(welcome)
    assert not welcome.recommends_environment()

    # doctor: providery připravené, i když některý model chybí (balík kliniky) → blok pryč
    welcome.set_report(
        {
            "all_ready": False,
            "providers": {"transcript": {"ready": True}, "segments": {"ready": True}},
            "models": {"wavlm": {"present": False}},
        }
    )
    assert not welcome.setup.isVisibleTo(welcome)
    # provider bez modelu → blok zpět; Hotovo ho schová do dalšího startu
    welcome.set_report(
        {"all_ready": False, "providers": {"transcript": {"ready": False}}, "models": {}}
    )
    assert (
        welcome.setup.isVisibleTo(welcome) and "nejsou nainstalované" in welcome.setup_note.text()
    )
    welcome.done_btn.click()
    assert not welcome.setup.isVisibleTo(welcome) and welcome.status.isVisibleTo(welcome)
    welcome.set_state(library_ok=True, models_ok=False)
    assert not welcome.setup.isVisibleTo(welcome)  # zůstává schovaný


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
    assert len(page.protocols.task_buttons) == 5  # úlohy v jedné řadě
