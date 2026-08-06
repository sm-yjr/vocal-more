"""Windows filesystem convention tests."""

from pathlib import Path

import vocal_more.paths as paths


def test_default_windows_data_dir_uses_roaming_appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    assert paths.default_data_dir() == tmp_path / "Roaming" / "Vocal More"


def test_pyinstaller_resource_root_is_considered(tmp_path, monkeypatch):
    resource = tmp_path / "resources" / "example.txt"
    resource.parent.mkdir(parents=True)
    resource.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert paths.bundled_resource_path("resources", "example.txt") == resource
