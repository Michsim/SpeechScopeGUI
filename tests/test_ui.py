"""Kouřový test celého okna nad falešnou knihovnou."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from speechscope_app import contract
from speechscope_app.backend.library import ParamInfo
from speechscope_app.backend.settings import AppSettings
from speechscope_app.ui.main_window import PAGE_BATCH, PAGE_ENV, PAGE_RESULTS, PAGE_RUN, MainWindow
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
    assert window.batch_page.edit_btn.isEnabled()
    assert not window.batch_page.editor.picker.warning.text()  # doctor: všechno připravené
    assert "Bez modelů" in window.batch_page.editor_summary.text()

    with qtbot.waitSignal(window.run_page.finished, timeout=15000):
        window.batch_page.run_btn.click()
    state = window.run_page.runner.state
    assert state.finished and state.total == 4
    assert window.nav.currentRow() == PAGE_RESULTS
    # tabulka běhu: řádek na nahrávku, výsledek, nabídka bez postupu po konci
    run = window.run_page
    assert run.files.rowCount() == 4
    statuses = [run.files.item(r, run.col_status).text() for r in range(4)]
    assert sorted(statuses) == ["chyba", "ok", "ok", "ok"]
    assert window.nav.item(PAGE_RUN).text() == "Běh"
    assert run.headline.text().startswith("Hotovo za")
    assert settings.seconds_per_file("fonace-zakladni") is not None
    assert window.results_page.table.model().rowCount() == 4

    run_dirs = list((settings.work_root).glob("*_fonace-zakladni"))
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "features.csv").is_file()
    assert (run_dirs[0] / "protocol.yaml").is_file()
    assert (run_dirs[0] / "speechscope.log").is_file()


def test_save_protocol_from_advanced_mode(qtbot: QtBot, settings: AppSettings) -> None:
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    assert page.select_protocol("Fonace, základní")
    assert page.save_btn.isEnabled()

    # odškrtnout první feature; jazyk z lišty se promítne i do parametrů providera
    first = page.editor.selected()[0]
    page.editor.picker.set_checked(first, False)
    page.editor.show_params("transcript")
    page.set_language("en")
    assert page.editor.params._form.value("language") == "en"
    assert page.editor.overrides()["transcript"] == {"language": "en"}
    proto = page.effective_protocol()
    assert proto is not None and first not in proto.features
    assert proto.config["transcript"] == {"language": "en"}
    proto.name = "Moje fonace"
    proto.description = "jen test"

    path = window.save_protocol(proto)
    assert path.is_file() and path.parent == settings.protocols_dir()
    assert page.current_protocol().name == "Moje fonace"
    assert not page.protocols._cards["Moje fonace"].modified.isVisible()
    assert page.protocols.task_buttons["phonation"].isChecked()
    saved = page.current_protocol()
    assert saved.features == proto.features
    assert saved.config == {"transcript": {"language": "en"}, "nlp": {"language": "en"}}

    # stejné jméno podruhé přepíše soubor, nevznikne druhý
    again = window.save_protocol(proto)
    assert again == path and len(list(settings.protocols_dir().glob("*.yaml"))) == 1


def test_start_page_depends_on_models(settings: AppSettings, qtbot: QtBot) -> None:
    """Skutečná knihovna bez modelů: začít na Prostředí a rovnou zkontrolovat."""
    from speechscope_app.backend.library import fake_command

    settings.use_fake_library = False
    settings.library_command = fake_command()  # chová se jako skutečná, ale bez modelů
    window = MainWindow(settings)
    qtbot.addWidget(window)
    assert window.nav.currentRow() == PAGE_ENV
    assert window.env_page.report is not None  # refresh proběhl sám

    settings.models_dir.mkdir(parents=True)
    (settings.models_dir / "whisper-large-v3-ct2").mkdir()
    window2 = MainWindow(settings)
    qtbot.addWidget(window2)
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
    qtbot: QtBot, settings: AppSettings, tmp_path: Path
) -> None:
    from speechscope_app.ui.models_install_dialog import ModelsInstallDialog

    library = settings.make_library()
    assert library is not None
    dialog = ModelsInstallDialog(library, settings.models_dir, archive=_bundle(tmp_path / "m.zip"))
    qtbot.addWidget(dialog)
    assert dialog.start_btn.isEnabled()
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


def test_editor_dialog_cancel_restores(qtbot: QtBot, settings: AppSettings, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    assert page.select_protocol("Pohádka, akustika")
    before = page.editor.selected()
    assert page.edit_btn.isEnabled()

    def fake_exec(self):  # uživatel v okně odškrtne feature a dá Zrušit
        page.editor.picker.set_checked(before[0], False)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(type(page.editor_dialog), "exec", fake_exec)
    page.open_editor()
    assert page.editor.selected() == before
    assert not page.protocols._cards["Pohádka, akustika"].modified.isVisible()

    def fake_exec_ok(self):
        page.editor.picker.set_checked(before[0], False)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(type(page.editor_dialog), "exec", fake_exec_ok)
    page.open_editor()
    assert before[0] not in page.editor.selected()
    assert before[0] not in page.effective_protocol().features


def test_language_from_doctor_goes_to_run(qtbot: QtBot, settings: AppSettings) -> None:
    settings.last_language = "en"
    window = MainWindow(settings)
    qtbot.addWidget(window)
    page = window.batch_page
    window.env_page.refresh()  # doctor: Stanza (cs, en)
    codes = [page.language.itemData(i) for i in range(page.language.count())]
    assert codes == ["cs", "en"]
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
