from pathlib import Path
from types import SimpleNamespace

from vocal_more.linux_app import extension_guide_marker, should_show_extension_guide


def test_extension_guide_is_one_time_and_only_for_unavailable_extension(tmp_path):
    paths = SimpleNamespace(state_dir=tmp_path)

    assert should_show_extension_guide("disabled", paths) is True
    marker = extension_guide_marker(paths)
    assert marker == Path(tmp_path) / ".gnome-extension-guide-v1"
    marker.write_text("shown\n", encoding="utf-8")

    assert should_show_extension_guide("disabled", paths) is False
    marker.unlink()
    assert should_show_extension_guide("enabled", paths) is False
