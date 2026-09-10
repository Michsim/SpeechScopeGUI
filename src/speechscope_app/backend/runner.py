"""Řízení dlouhého běhu knihovny přes QProcess.

Stdout se čte po řádcích a překládá na události ze `contract`, stderr
jde ven jako log. Zrušení je `kill()`; knihovna nemá měkké přerušení
a mezivýsledky z pracovní složky zůstávají použitelné.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from ..contract import BatchState, ContractError, Event, parse_event
from .library import subprocess_env


class Runner(QObject):
    started = Signal()
    event = Signal(object)  # contract.Event
    log = Signal(str)  # jeden řádek stderr
    contract_error = Signal(str)
    finished = Signal(int, bool)  # návratový kód, zrušeno uživatelem

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.state = BatchState()
        self._proc: QProcess | None = None
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._cancelled = False
        self.log_lines: list[str] = []

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def start(self, argv: list[str], *, cwd: str | None = None) -> None:
        if self.running:
            raise RuntimeError("běh už probíhá")
        self.state = BatchState()
        self.log_lines = []
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._cancelled = False

        proc = QProcess(self)
        env = QProcessEnvironment()
        for key, value in subprocess_env().items():
            env.insert(key, value)
        proc.setProcessEnvironment(env)
        if cwd:
            proc.setWorkingDirectory(cwd)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc

        program, *args = argv
        proc.start(program, args)
        self.started.emit()

    def cancel(self) -> None:
        if self._proc is None or not self.running:
            return
        self._cancelled = True
        self._proc.kill()

    # --- čtení ----------------------------------------------------------------

    def _on_stdout(self) -> None:
        assert self._proc is not None
        self._stdout_buf += bytes(self._proc.readAllStandardOutput().data()).decode(
            "utf-8", errors="replace"
        )
        *lines, self._stdout_buf = self._stdout_buf.split("\n")
        for line in lines:
            self._handle_line(line)

    def _on_stderr(self) -> None:
        assert self._proc is not None
        self._stderr_buf += bytes(self._proc.readAllStandardError().data()).decode(
            "utf-8", errors="replace"
        )
        *lines, self._stderr_buf = self._stderr_buf.split("\n")
        for line in lines:
            self._emit_log(line)

    def _handle_line(self, line: str) -> None:
        try:
            event: Event | None = parse_event(line)
        except ContractError as exc:
            self.contract_error.emit(str(exc))
            return
        if event is None:
            return
        self.state.apply(event)
        self.event.emit(event)

    def _emit_log(self, line: str) -> None:
        text = line.rstrip("\r")
        if not text:
            return
        self.log_lines.append(text)
        self.log.emit(text)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._emit_log("knihovnu se nepodařilo spustit")
            self.finished.emit(-1, False)
            self._proc = None

    def _on_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        # zbytek bufferů bez závěrečného \n
        if self._stdout_buf.strip():
            self._handle_line(self._stdout_buf)
            self._stdout_buf = ""
        if self._stderr_buf.strip():
            self._emit_log(self._stderr_buf)
            self._stderr_buf = ""
        self.finished.emit(code, self._cancelled)
        self._proc = None
