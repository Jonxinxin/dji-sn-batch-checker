"""Separate bundled read-only resources from persistent user data."""
from __future__ import annotations

import ctypes
import os
import sys
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))


def default_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "DJI-SN-Checker"
    return PROJECT_ROOT / ".python-data"


@contextmanager
def external_browser_dlls():
    """Do not inject PyInstaller's DLL directory into an installed browser."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetDllDirectoryW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
    api.GetDllDirectoryW.restype = ctypes.c_uint
    api.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    api.SetDllDirectoryW.restype = ctypes.c_int
    previous = ctypes.create_unicode_buffer(32768)
    api.GetDllDirectoryW(len(previous), previous)
    if not api.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not api.SetDllDirectoryW(previous.value or None):
            raise ctypes.WinError(ctypes.get_last_error())
