"""Read the foreground Windows process name without inspecting window content."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import PureWindowsPath
import sys


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_MAX_PATH_CHARS = 32768


def _configure_windows_api(user32, kernel32) -> None:
    """Attach pointer-safe Win32 signatures to real ctypes function objects."""
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def foreground_process_name(*, user32=None, kernel32=None) -> str:
    """Return the foreground executable basename, or an empty string on failure."""
    injected = user32 is not None or kernel32 is not None
    if sys.platform != "win32" and not injected:
        return ""
    if (user32 is None) != (kernel32 is None):
        raise ValueError("user32 and kernel32 must be provided together")

    try:
        if not injected:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            _configure_windows_api(user32, kernel32)

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""

        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return ""

        process_handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            process_id.value,
        )
        if not process_handle:
            return ""

        try:
            buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARS)
            size = wintypes.DWORD(len(buffer))
            ok = kernel32.QueryFullProcessImageNameW(
                process_handle,
                0,
                buffer,
                ctypes.byref(size),
            )
            if not ok:
                return ""
            return PureWindowsPath(buffer.value).name.casefold()
        finally:
            kernel32.CloseHandle(process_handle)
    except Exception:
        return ""


__all__ = ["foreground_process_name"]
