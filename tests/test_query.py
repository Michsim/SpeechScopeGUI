"""Dotaz knihovny na pozadí: výsledek signálem, zrušení bez signálu."""

from __future__ import annotations

import json
from pathlib import Path

from pytestqt.qtbot import QtBot

from speechscope_app.backend import command
from speechscope_app.backend.library import Library, LibraryError, fake_command
from speechscope_app.backend.query import AsyncQuery, parse_json


def test_async_query_doctor(qtbot: QtBot, tmp_path: Path) -> None:
    library = Library(fake_command(), models_dir=tmp_path / "models")
    args = command.doctor_args(models_dir=library.models_dir)
    query = AsyncQuery(library, args)
    with qtbot.waitSignal(query.finished, timeout=15000) as blocker:
        query.start()
    code, out, err = blocker.args
    report = parse_json(args, code, out, err, ok_codes=(0, 1))
    assert report["providers"] and json.loads(out) == report
    assert not query.running


def test_async_query_cancel_and_failure(qtbot: QtBot, tmp_path: Path) -> None:
    library = Library(fake_command(), models_dir=tmp_path / "models")
    query = AsyncQuery(library, command.doctor_args(models_dir=library.models_dir))
    got: list[tuple] = []
    query.finished.connect(lambda *a: got.append(a))
    query.start()
    query.cancel()
    qtbot.wait(300)
    assert got == [] and not query.running  # zrušený dotaz nic nehlásí

    missing = AsyncQuery(Library(["neexistujici-program-xyz"]), ["version"])
    with qtbot.waitSignal(missing.finished, timeout=5000) as blocker:
        missing.start()
    assert blocker.args[0] == -1 and "nejde spustit" in blocker.args[2]
    try:
        parse_json(["version"], -1, "", blocker.args[2], ok_codes=(0,))
    except LibraryError as exc:
        assert "kódem -1" in str(exc)
    else:
        raise AssertionError("parse_json měl selhat")
