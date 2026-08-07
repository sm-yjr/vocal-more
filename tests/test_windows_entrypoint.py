"""Tests for desktop-host selection without importing native UI eagerly."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import vocal_more.entrypoint as entrypoint


def test_windows_entrypoint_loads_windows_host(monkeypatch):
    calls: list[str] = []
    module = ModuleType("vocal_more.windows_app")
    module.main = lambda: calls.append("windows")
    monkeypatch.setitem(sys.modules, "vocal_more.windows_app", module)
    monkeypatch.setattr(entrypoint.sys, "platform", "win32")
    monkeypatch.setattr(entrypoint.sys, "argv", ["vocal-more"])

    entrypoint.main()

    assert calls == ["windows"]


def test_version_path_does_not_import_native_host(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.sys, "platform", "win32")
    monkeypatch.setattr(entrypoint.sys, "argv", ["vocal-more", "--version"])
    sys.modules.pop("vocal_more.windows_app", None)

    entrypoint.main()

    assert capsys.readouterr().out.strip() == entrypoint.__version__
    assert "vocal_more.windows_app" not in sys.modules


def test_pyinstaller_enables_utf8_mode():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "windows" / "vocal_more.spec").read_text(
        encoding="utf-8"
    )

    assert '("X utf8", None, "OPTION")' in spec
