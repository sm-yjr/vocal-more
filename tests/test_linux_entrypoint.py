from __future__ import annotations

import sys
from types import ModuleType

from vocal_more import entrypoint


def test_linux_entrypoint_loads_linux_host_lazily(monkeypatch):
    calls = []
    module = ModuleType("vocal_more.linux_app")
    module.main = lambda: calls.append("linux")
    monkeypatch.setitem(sys.modules, "vocal_more.linux_app", module)
    monkeypatch.setattr(entrypoint.sys, "platform", "linux")
    monkeypatch.setattr(entrypoint.sys, "argv", ["vocal-more"])

    entrypoint.main()

    assert calls == ["linux"]


def test_linux_version_does_not_import_gtk_host(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.sys, "platform", "linux")
    monkeypatch.setattr(entrypoint.sys, "argv", ["vocal-more", "--version"])
    sys.modules.pop("vocal_more.linux_app", None)

    entrypoint.main()

    assert capsys.readouterr().out.strip() == entrypoint.__version__
    assert "vocal_more.linux_app" not in sys.modules
