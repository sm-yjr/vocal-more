"""Tests for the JSON-RPC request dispatcher."""

import os
import importlib
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DASHSCOPE_API_KEY", "test-api-key")


@pytest.fixture
def handler(tmp_path, monkeypatch):
    """Create an RPCHandler with mocked heavy dependencies."""
    from vocal_more.config import Config

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: tmp_path / "config.yaml"))

    # Reset the global config singleton so each test starts fresh
    import vocal_more.config as config_mod
    monkeypatch.setattr(config_mod, "_config", None)

    # Reset dictionary singleton
    import vocal_more.dictionary as dict_mod
    monkeypatch.setattr(dict_mod, "_dictionary", None)

    rpc_module = importlib.import_module("vocal_more.rpc_handler")

    # Mock heavy I/O components
    with (
        patch.object(rpc_module, "TextPolisher", return_value=MagicMock()),
        patch("vocal_more.modes.walkie_talkie.ASREngine", return_value=MagicMock()),
        patch("vocal_more.modes.walkie_talkie.AudioRecorder", return_value=MagicMock()),
        patch("vocal_more.modes.walkie_talkie.KeyboardSimulator", return_value=MagicMock()),
        patch("vocal_more.modes.realtime_long.ASREngine", return_value=MagicMock()),
        patch("vocal_more.modes.realtime_long.AudioRecorder", return_value=MagicMock()),
        patch("vocal_more.modes.realtime_long.KeyboardSimulator", return_value=MagicMock()),
    ):
        from vocal_more.rpc_handler import RPCHandler
        notifications = []
        rpc = RPCHandler(send_notification=lambda m, p: notifications.append((m, p)))
        rpc._notifications = notifications
        yield rpc
        rpc.close()


def test_dispatch_initialize(handler):
    result = handler.dispatch("initialize", {})
    assert "version" in result
    assert result["state"] == "idle"
    assert result["current_mode"] == "realtime_long"
    assert "config" in result
    assert "audio" in result["config"]


def test_initialize_does_not_expose_api_key(handler):
    handler.config.api_key = "sk-secret-123"

    result = handler.dispatch("initialize", {})

    assert result["config"]["api_key"] == ""


def test_initialize_includes_model_catalogs(handler):
    """Verify initialize response includes llm_models and asr_models."""
    result = handler.dispatch("initialize", {})
    assert "llm_models" in result
    assert "asr_models" in result

    # Verify structure of LLM catalog entries
    llm_models = result["llm_models"]
    assert isinstance(llm_models, list)
    assert len(llm_models) >= 1
    for entry in llm_models:
        assert "id" in entry
        assert "display_name" in entry
        assert "api" in entry
        assert "supports_thinking" in entry

    # Verify structure of ASR catalog entries
    asr_models = result["asr_models"]
    assert isinstance(asr_models, list)
    assert len(asr_models) >= 1
    for entry in asr_models:
        if entry.get("separator"):
            continue
        assert "id" in entry
        assert "display_name" in entry
        assert "transport" in entry
        assert "supports_transcription_params" in entry
        assert "input_audio_transcription_model" in entry
        assert "handles_inline_polish" in entry


def test_dispatch_unknown_method(handler):
    from vocal_more.rpc_handler import RPCError
    with pytest.raises(RPCError) as exc_info:
        handler.dispatch("nonexistent_method", {})
    assert exc_info.value.code == -32601


def test_dispatch_get_config(handler):
    handler.config.api_key = "sk-secret-123"

    result = handler.dispatch("get_config", {})

    assert result["api_key"] == ""
    assert result["audio"]["sample_rate"] == 16000
    assert result["llm"]["model"] == "qwen3.5-plus"
    assert result["enable_polish"] is True


def test_dispatch_set_config(handler):
    result = handler.dispatch("set_config", {"key": "enable_polish", "value": False})
    assert result["ok"] is True
    assert handler.config.enable_polish is False


def test_dispatch_set_config_nested(handler):
    result = handler.dispatch("set_config", {"key": "audio.gain", "value": 3.0})
    assert result["ok"] is True
    assert handler.config.audio.gain == 3.0


def test_dispatch_set_config_persona(handler):
    result = handler.dispatch(
        "set_config", {"key": "llm.persona", "value": "professional"}
    )
    assert result["ok"] is True
    assert handler.config.llm.persona == "professional"


def test_dispatch_set_config_structured(handler):
    assert handler.config.llm.structured is False
    result = handler.dispatch(
        "set_config", {"key": "llm.structured", "value": True}
    )
    assert result["ok"] is True
    assert handler.config.llm.structured is True


def test_dispatch_set_config_invalid_key(handler):
    from vocal_more.rpc_handler import RPCError
    with pytest.raises(RPCError) as exc_info:
        handler.dispatch("set_config", {"key": "nonexistent", "value": True})
    assert exc_info.value.code == -32602


def test_dispatch_list_devices(handler):
    with patch(
        "vocal_more.rpc_handler.AudioRecorder.list_input_devices",
        return_value=[{"index": 0, "name": "Built-in", "is_default": True}],
    ):
        result = handler.dispatch("list_devices", {})
    assert len(result) == 1
    assert result[0]["name"] == "Built-in"


def test_dispatch_set_mode(handler):
    result = handler.dispatch("set_mode", {"mode": "realtime_long"})
    assert result["ok"] is True
    assert result["mode"] == "realtime_long"

    result2 = handler.dispatch("initialize", {})
    assert result2["current_mode"] == "realtime_long"


def test_dispatch_set_mode_invalid(handler):
    from vocal_more.rpc_handler import RPCError
    with pytest.raises(RPCError):
        handler.dispatch("set_mode", {"mode": "bogus"})


def test_dispatch_hotkey_pressed_released(handler):
    handler.dispatch("set_mode", {"mode": "walkie_talkie"})
    handler.dispatch("hotkey_pressed", {})
    # In walkie_talkie mode, pressing starts recording.
    assert handler._notifications[-1] == ("state_changed", {"state": "recording"})

    handler.dispatch("hotkey_released", {})
    # Releasing with too little data returns to idle.
    assert handler._notifications[-1] == ("state_changed", {"state": "idle"})


def test_dispatch_hotkey_commands_use_serial_command_coordinator(handler):
    handler._command_coordinator = MagicMock()

    result = handler.dispatch("hotkey_pressed", {})

    assert result["ok"] is True
    handler._command_coordinator.call.assert_called_once()


def test_dispatch_dictionary_crud(handler):
    # Initially empty
    result = handler.dispatch("get_dictionary", {})
    assert result == []

    # Add entry
    handler.dispatch("add_dict_entry", {"term": "Claude", "aliases": ["可劳德"]})
    result = handler.dispatch("get_dictionary", {})
    assert len(result) == 1
    assert result[0]["term"] == "Claude"
    assert result[0]["aliases"] == ["可劳德"]

    # Remove entry
    handler.dispatch("remove_dict_entry", {"term": "Claude"})
    result = handler.dispatch("get_dictionary", {})
    assert result == []


def test_dispatch_add_dict_entry_empty_term(handler):
    from vocal_more.rpc_handler import RPCError
    with pytest.raises(RPCError) as exc_info:
        handler.dispatch("add_dict_entry", {"term": ""})
    assert exc_info.value.code == -32602


def test_dispatch_set_active_hotkeys(handler):
    result = handler.dispatch("set_active_hotkeys", {"hotkeys": ["fn"]})
    assert result["ok"] is True
    assert handler.config.hotkey.active_hotkeys == ["fn"]


def test_dispatch_shutdown(handler):
    result = handler.dispatch("shutdown", {})
    assert result["ok"] is True


def test_dispatch_cancel(handler):
    result = handler.dispatch("cancel", {})
    assert result["ok"] is True


def test_notification_on_state_change(handler):
    """Verify state_changed notification fires when mode state changes."""
    handler.dispatch("hotkey_pressed", {})
    assert any(m == "state_changed" for m, _ in handler._notifications)


def test_notification_on_processing_stage(handler):
    """Processing stage updates should be exposed to the frontend."""
    handler._on_processing_stage("polishing")
    assert handler._notifications[-1] == ("processing_stage", {"stage": "polishing"})


def test_to_dict_added_to_config(handler):
    """Verify Config.to_dict() works and roundtrips correctly."""
    d = handler.config.to_dict()
    assert isinstance(d, dict)
    assert d["audio"]["sample_rate"] == 16000
    assert d["default_mode"] == "realtime_long"


def test_set_asr_model_syncs_backend(handler):
    """Setting asr.model should auto-sync asr.backend to match model transport."""
    # Default model is realtime_ws
    assert handler.config.asr.backend == "realtime_ws"

    # Switch to short_file model
    handler.dispatch("set_config", {"key": "asr.model", "value": "qwen3-asr-flash"})
    assert handler.config.asr.model == "qwen3-asr-flash"
    assert handler.config.asr.backend == "short_file"

    # Switch to omni realtime model
    handler.dispatch("set_config", {"key": "asr.model", "value": "qwen3.5-omni-plus-realtime"})
    assert handler.config.asr.model == "qwen3.5-omni-plus-realtime"
    assert handler.config.asr.backend == "realtime_ws"

    # Switch to omni offline model
    handler.dispatch("set_config", {"key": "asr.model", "value": "qwen3.5-omni-plus"})
    assert handler.config.asr.model == "qwen3.5-omni-plus"
    assert handler.config.asr.backend == "omni_offline"

    # Switch back to the default realtime model
    handler.dispatch("set_config", {"key": "asr.model", "value": "qwen3.5-omni-flash-realtime"})
    assert handler.config.asr.model == "qwen3.5-omni-flash-realtime"
    assert handler.config.asr.backend == "realtime_ws"


def test_dispatch_set_config_updates_audio_recorders(handler):
    """Audio config changes should be pushed into existing mode recorders."""
    result = handler.dispatch("set_config", {"key": "audio.highpass_freq", "value": 380})

    assert result["ok"] is True
    assert handler.config.audio.highpass_freq == 380
    handler._walkie_talkie._recorder.set_highpass_freq.assert_called_with(380)
    handler._realtime_long._recorder.set_highpass_freq.assert_called_with(380)


def test_dispatch_set_config_refreshes_idle_asr_runtime_for_asr_and_llm_changes(handler):
    """Session-sensitive config changes should invalidate idle ASR runtime state."""
    result = handler.dispatch("set_config", {"key": "asr.language", "value": "en"})

    assert result["ok"] is True
    handler._walkie_talkie._asr.refresh_runtime_config.assert_called_once_with(
        drop_idle_session=True
    )
    handler._realtime_long._asr.refresh_runtime_config.assert_called_once_with(
        drop_idle_session=True
    )

    handler._walkie_talkie._asr.refresh_runtime_config.reset_mock()
    handler._realtime_long._asr.refresh_runtime_config.reset_mock()

    result = handler.dispatch("set_config", {"key": "llm.level", "value": "strong"})

    assert result["ok"] is True
    handler._walkie_talkie._asr.refresh_runtime_config.assert_called_once_with(
        drop_idle_session=True
    )
    handler._realtime_long._asr.refresh_runtime_config.assert_called_once_with(
        drop_idle_session=True
    )


def test_dispatch_set_config_default_mode_switches_current_mode(handler):
    """Updating default_mode through set_config should switch the active mode."""
    result = handler.dispatch(
        "set_config", {"key": "default_mode", "value": "realtime_long"}
    )

    assert result["ok"] is True
    init_result = handler.dispatch("initialize", {})
    assert init_result["current_mode"] == "realtime_long"


def test_dispatch_set_config_default_mode_waits_until_idle(handler):
    """Changing default_mode should not interrupt an in-flight recording mode."""
    handler._current_mode = handler._walkie_talkie
    handler._walkie_talkie._state = handler._walkie_talkie.state.__class__.RECORDING

    result = handler.dispatch(
        "set_config", {"key": "default_mode", "value": "realtime_long"}
    )

    assert result["ok"] is True
    assert handler._current_mode is handler._walkie_talkie

    handler._walkie_talkie._state = handler._walkie_talkie.state.__class__.IDLE
    handler._select_default_mode_when_safe()

    assert handler._current_mode is handler._realtime_long


def test_dispatch_set_config_empty_api_key_clears_polisher(handler):
    """Clearing api_key should remove the shared text polisher from all modes."""
    assert handler._text_polisher is not None

    result = handler.dispatch("set_config", {"key": "api_key", "value": ""})

    assert result["ok"] is True
    assert handler._text_polisher is None
    assert handler._walkie_talkie.text_polisher is None
    assert handler._realtime_long.text_polisher is None
    handler._walkie_talkie._asr.refresh_api_key.assert_called_once()
    handler._realtime_long._asr.refresh_api_key.assert_called_once()


def test_dispatch_set_active_hotkeys_normalizes_values(handler):
    """Hotkey updates should be normalized before saving."""
    result = handler.dispatch(
        "set_active_hotkeys", {"hotkeys": ["printscreen", "bogus"]}
    )

    assert result["ok"] is True
    assert handler.config.hotkey.active_hotkeys == ["f13"]
