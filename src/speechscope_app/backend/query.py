"""Krátký dotaz knihovny (`doctor --json`, `version`) na pozadí přes QProcess.

`Library` v `library.py` volá tytéž příkazy synchronně; to je v pořádku
při startu pro laciné dotazy (`models list`), ale `doctor` trvá i v exe
několik sekund a v hlavním vlákně Qt by zamrzlo okno („Neodpovídá“).
Stránky proto dlouhé dotazy pouští tudy a výsledek dostanou signálem.
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .library import Library, LibraryError, subprocess_env


class AsyncQuery(QObject):
    """Jeden běh knihovny; `finished(kód, stdout, stderr)` po skončení.

    `cancel()` proces zabije a signál už nepřijde, takže volající, který
    mezitím změnil nastavení, nedostane zastaralou odpověď.
    """

    finished = Signal(int, str, str)

    def __init__(self, library: Library, args: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._argv = library.argv(args)
        self._proc: QProcess | None = None
        self._cancelled = False

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def start(self) -> None:
        proc = QProcess(self)
        env = QProcessEnvironment()
        for key, value in subprocess_env().items():
            env.insert(key, value)
        proc.setProcessEnvironment(env)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        program, *args = self._argv
        proc.start(program, args)

    def cancel(self) -> None:
        if self._proc is None:
            return
        self._cancelled = True
        if self.running:
            self._proc.kill()
            self._proc.waitForFinished(1000)

    def _on_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        if self._cancelled or self._proc is None:
            return
        out = bytes(self._proc.readAllStandardOutput().data()).decode("utf-8", errors="replace")
        err = bytes(self._proc.readAllStandardError().data()).decode("utf-8", errors="replace")
        self.finished.emit(code, out, err)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and not self._cancelled:
            self.finished.emit(-1, "", f"knihovnu nejde spustit: {self._argv[0]}")


def parse_json(args: list[str], code: int, out: str, err: str, ok_codes: tuple[int, ...]) -> Any:
    """Totéž co `Library.run_json`, jen nad hotovým výstupem; chyba je `LibraryError`."""
    if code not in ok_codes:
        msg = err.strip().splitlines()[-1] if err.strip() else "bez hlášky"
        raise LibraryError(f"`{' '.join(args)}` skončil kódem {code}: {msg}", returncode=code)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise LibraryError(f"`{' '.join(args)}` nevrátil JSON", returncode=code) from exc
