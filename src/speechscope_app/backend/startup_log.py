"""Časy startu aplikace do `startup.log` v datech aplikace.

Jeden řádek na start, drží se posledních `MAX_LINES`. Jde do diagnostického
zipu, takže je z kliniky vidět, kolik start trvá na tamním počítači a co z toho
jsou dotazy na knihovnu. Klíče jsou anglicky (jako log knihovny), časy v sekundách:

- `total`: od vzniku procesu (dvojklik, u exe včetně spouštěče) po zobrazení okna,
- `python`: start Pythonu a importy před `main()` (= `total` − čas od `main()`),
- `splash`, `import`, `window`, `shown`: fáze od začátku `main()` (vždy přírůstek),
- `lib_list`, `lib_providers`, `lib_models`: dotazy na knihovnu puštěné napřed.

Zápis nikdy neshodí aplikaci.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

MAX_LINES = 200


def process_age() -> float | None:
    """Sekundy od vzniku tohoto procesu; jen na Windows (`GetProcessTimes`)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        filetime_p = ctypes.POINTER(wintypes.FILETIME)
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, *[filetime_p] * 4]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        created, exited, kernel, user, now = (wintypes.FILETIME() for _ in range(5))
        ok = kernel32.GetProcessTimes(
            kernel32.GetCurrentProcess(),
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        kernel32.GetSystemTimePreciseAsFileTime(ctypes.byref(now))

        def seconds(ft: wintypes.FILETIME) -> float:
            return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7

        return seconds(now) - seconds(created)
    except (AttributeError, OSError, ctypes.ArgumentError):
        return None


class StartupTimer:
    """Značky fází startu; čas se měří od vytvoření (začátek `main()`)."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._last = self._start
        self.phases: list[tuple[str, float]] = []

    def mark(self, name: str) -> None:
        """Konec fáze `name`: přírůstek od předchozí značky."""
        now = time.perf_counter()
        self.phases.append((name, now - self._last))
        self._last = now

    def since_start(self) -> float:
        return time.perf_counter() - self._start

    def line(self, *, extra: dict[str, float] | None = None, info: str = "") -> str:
        """Řádek logu: datum, `info`, celkem, fáze a `extra` (dotazy na knihovnu)."""
        since = self.since_start()
        age = process_age()
        parts = [f"{datetime.now():%Y-%m-%d %H:%M:%S}"]
        if info:
            parts.append(info)
        if age is not None:
            parts.append(f"total={age:.2f}")
            parts.append(f"python={max(age - since, 0.0):.2f}")
        else:
            parts.append(f"total_main={since:.2f}")
        parts += [f"{name}={seconds:.2f}" for name, seconds in self.phases]
        parts += [f"{name}={seconds:.2f}" for name, seconds in (extra or {}).items()]
        return " ".join(parts)


def append(path: Path, line: str, *, max_lines: int = MAX_LINES) -> None:
    """Přidá řádek na konec a nechá jen posledních `max_lines`; chyby zápisu spolkne."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        lines = [*old, line][-max_lines:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return
