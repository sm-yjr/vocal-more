"""Tests for filesystem path helpers."""

from pathlib import Path

from vocal_more.paths import bundled_resource_path


def test_bundled_resource_path_prefers_resource_root_override(tmp_path, monkeypatch):
    """Resource lookup should support macOS bundle resource roots."""
    resource = tmp_path / "resources" / "settings" / "settings.html"
    resource.parent.mkdir(parents=True)
    resource.write_text("<html></html>")

    monkeypatch.setenv("VOCAL_MORE_RESOURCE_ROOT", str(tmp_path))

    assert bundled_resource_path("resources", "settings", "settings.html") == resource


def test_bundled_resource_path_falls_back_to_source_tree():
    """Development runs should still resolve resources from the checkout."""
    path = bundled_resource_path("resources", "floating_capsule", "capsule.html")

    assert path == Path(__file__).resolve().parents[1] / "resources" / "floating_capsule" / "capsule.html"
