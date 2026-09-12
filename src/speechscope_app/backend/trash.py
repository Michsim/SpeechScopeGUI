"""Přesun souboru nebo složky do Koše Windows (jinde smazání).

Přes `SHFileOperationW` s `FOF_ALLOWUNDO`, bez dialogů shellu. Chyba
shellu se hlásí jako `OSError`, aby ji GUI mohlo ukázat ve stavovém řádku
(typicky soubor otevřený v Excelu).
"""

from __future__ import annotations

import ctypes
import shutil
import sys
from pathlib import Path

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


def send_to_trash(path: Path) -> None:
    """Do Koše; mimo Windows smaže natrvalo. `OSError`, když to nejde."""
    if not path.exists():
        raise FileNotFoundError(str(path))
    if sys.platform != "win32":
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return
    _shell_delete(path)


def _shell_delete(path: Path) -> None:
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = (
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        )

    # seznam cest je ukončený dvěma nulami
    source = ctypes.create_unicode_buffer(str(path.resolve()) + "\0")
    op = SHFILEOPSTRUCTW(
        None,
        FO_DELETE,
        ctypes.cast(source, wintypes.LPCWSTR),
        None,
        FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
        False,
        None,
        None,
    )
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    code = shell32.SHFileOperationW(ctypes.byref(op))
    if code != 0 or op.fAnyOperationsAborted:
        raise OSError(code, f"přesun do Koše selhal (kód {code}): {path}")
    if path.exists():
        raise OSError(code, f"přesun do Koše se neprovedl: {path}")
