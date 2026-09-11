"""Kouřový test celého okna nad falešnou knihovnou."""

from __future__ import annotations

import json
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
    assert window.batch_page.edit_btn.isEnabled()
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
    assert results.runs.rowCount() == 1
    assert results.runs.item(0, 1).text() == "Fonace, základní"
    assert results.runs.item(0, 3).text() == "hotovo"
    assert results.runs.selectionModel().selectedRows()[0].row() == 0
    # záznam průběhu a jeho přehrání na Výpočtu
    events = (run_dirs[0] / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert events[0].startswith('{"event": "start"') and events[-1].startswith('{"event": "saved"')
    assert run.history.runs.rowCount() == 1
    run._live_dir = None  # jako po novém startu aplikace: běh je jen v historii
    run.show_recorded(run.history.current())
    assert run.files.rowCount() == 4
    replayed = sorted(run.files.item(r, run.col_status).text() for r in range(4))
    assert replayed == ["chyba", "ok", "ok", "ok"]
    assert "hotovo" in run.summary.text() and run.log.toPlainText()


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
    assert page.manifest() is None and page.files.columnCount() == 2  # nahrávka, ruční vstupy
    assert page.files.item(0, 1).text() == "labely, přepis"  # p01 má labely i přepis
    page.set_advanced(False)
    assert page.files.columnCount() == 1


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
