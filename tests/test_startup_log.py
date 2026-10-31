"""Časy startu do startup.log: řádek s fázemi a ořez na posledních N startů."""

from __future__ import annotations

import re
from pathlib import Path

from speechscope_app.backend import startup_log


def test_line_has_phases_and_extra() -> None:
    timer = startup_log.StartupTimer()
    timer.mark("splash")
    timer.mark("window")
    line = timer.line(extra={"lib_list": 1.234}, info="app=0.4.0 exe=0")
    assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d app=0\.4\.0 exe=0 ", line)
    assert re.search(r" splash=\d+\.\d\d window=\d+\.\d\d lib_list=1\.23$", line)
    assert ("total=" in line and "python=" in line) or "total_main=" in line


def test_append_keeps_last_lines(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "startup.log"
    for n in range(5):
        startup_log.append(path, f"start {n}", max_lines=3)
    assert path.read_text(encoding="utf-8").splitlines() == ["start 2", "start 3", "start 4"]


def test_append_never_raises(tmp_path: Path) -> None:
    folder = tmp_path / "is-a-dir"
    folder.mkdir()
    startup_log.append(folder, "x")  # zápis do složky selže potichu
