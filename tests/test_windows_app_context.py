"""Tests for privacy-bounded Windows foreground-process capture."""

from __future__ import annotations

import pytest

from vocal_more.infrastructure.windows_app_context import foreground_process_name


class _User32:
    def __init__(self, *, hwnd=101, process_id=4242) -> None:
        self.hwnd = hwnd
        self.process_id = process_id

    def GetForegroundWindow(self):
        return self.hwnd

    def GetWindowThreadProcessId(self, hwnd, process_id):
        assert hwnd == self.hwnd
        process_id._obj.value = self.process_id
        return 7


class _Kernel32:
    def __init__(self, *, path=r"C:\Program Files\Microsoft VS Code\Code.exe") -> None:
        self.path = path
        self.closed: list[int] = []

    def OpenProcess(self, access, inherit, process_id):
        assert access == 0x1000
        assert inherit is False
        assert process_id == 4242
        return 808

    def QueryFullProcessImageNameW(self, handle, flags, buffer, size):
        assert handle == 808
        assert flags == 0
        buffer.value = self.path
        size._obj.value = len(self.path)
        return True

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


def test_returns_only_lowercase_executable_basename():
    kernel32 = _Kernel32()

    result = foreground_process_name(user32=_User32(), kernel32=kernel32)

    assert result == "code.exe"
    assert kernel32.closed == [808]


def test_failure_to_open_process_fails_closed():
    kernel32 = _Kernel32()
    kernel32.OpenProcess = lambda *_args: 0

    assert foreground_process_name(user32=_User32(), kernel32=kernel32) == ""
    assert kernel32.closed == []


def test_both_win32_libraries_must_be_injected_together():
    with pytest.raises(ValueError):
        foreground_process_name(user32=_User32())
