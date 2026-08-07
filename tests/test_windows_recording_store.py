"""Tests for the Windows recording repository composition boundary."""

from vocal_more import windows_rpc_handler


def test_windows_recording_store_uses_appdata_and_disables_macos_codec(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(windows_rpc_handler, "default_data_dir", lambda: tmp_path)

    store = windows_rpc_handler.build_windows_recording_store()
    try:
        assert store._dir == tmp_path / "recordings"
        assert store._index_path == tmp_path / "recordings" / "recordings.json"
        assert store._auto_compact is False
    finally:
        store.close()
