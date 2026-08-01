from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_microphone_permission_request_has_an_explicit_localized_retry_message():
    from vocal_more.core.audio_recorder import AudioRecorderStartError
    from vocal_more.localization import format_microphone_start_error

    error = AudioRecorderStartError(
        "permission requested",
        code="microphone_permission_requested",
        stage="permission",
    )

    assert format_microphone_start_error("zh", error) == (
        "已请求麦克风权限。请在系统提示中允许访问，然后再次开始录音。"
    )
    assert format_microphone_start_error("en", error) == (
        "Microphone access was requested. Allow it in the system prompt, then start again."
    )


def _mode(*, idle: bool = True):
    return SimpleNamespace(
        runtime_is_idle=idle,
        apply_audio_runtime_config=MagicMock(),
        refresh_asr_runtime=MagicMock(),
    )


def test_mode_runtime_exposes_current_mode_without_leaking_mode_instances():
    from vocal_more.application.mode_runtime import ModeRuntimeService

    walkie = _mode()
    realtime = _mode()
    current = {"value": realtime}
    runtime = ModeRuntimeService(
        modes={"walkie_talkie": walkie, "realtime_long": realtime},
        get_current_mode=lambda: current["value"],
        set_current_mode=lambda mode: current.__setitem__("value", mode),
    )

    assert runtime.current_mode_name == "realtime_long"


def test_mode_runtime_switches_only_when_the_current_mode_is_idle():
    from vocal_more.application.mode_runtime import ModeRuntimeService

    walkie = _mode()
    realtime = _mode(idle=False)
    current = {"value": realtime}
    runtime = ModeRuntimeService(
        modes={"walkie_talkie": walkie, "realtime_long": realtime},
        get_current_mode=lambda: current["value"],
        set_current_mode=lambda mode: current.__setitem__("value", mode),
    )

    assert runtime.select_mode_when_idle("walkie_talkie") is False
    assert current["value"] is realtime

    realtime.runtime_is_idle = True
    assert runtime.select_mode_when_idle("walkie_talkie") is True
    assert current["value"] is walkie


def test_mode_runtime_broadcasts_runtime_updates_through_public_mode_ports():
    from vocal_more.application.mode_runtime import ModeRuntimeService

    walkie = _mode()
    realtime = _mode()
    runtime = ModeRuntimeService(
        modes={"walkie_talkie": walkie, "realtime_long": realtime},
        get_current_mode=lambda: realtime,
        set_current_mode=lambda _mode: None,
    )
    audio_config = SimpleNamespace(gain=4.0)

    runtime.apply_audio_config(audio_config)
    runtime.refresh_asr_runtime()

    walkie.apply_audio_runtime_config.assert_called_once_with(audio_config)
    realtime.apply_audio_runtime_config.assert_called_once_with(audio_config)
    walkie.refresh_asr_runtime.assert_called_once_with()
    realtime.refresh_asr_runtime.assert_called_once_with()


def test_base_mode_runtime_port_keeps_private_resources_inside_the_mode():
    from vocal_more.modes.base_mode import BaseMode, ModeState

    class RuntimeMode(BaseMode):
        @property
        def name(self):
            return "runtime-test"

        @property
        def description(self):
            return "runtime-test"

        def on_hotkey_pressed(self):
            return None

        def on_hotkey_released(self):
            return None

        def cancel(self, reason="user_cancel"):
            return None

    mode = RuntimeMode()
    mode._recorder = MagicMock()
    mode._recorder.input_status = {
        "processing_mode": "macos_voice_processing",
        "echo_cancellation": "active",
    }
    mode._asr = MagicMock()
    audio = SimpleNamespace(
        sample_rate=24000,
        blocksize=960,
        capture_channels=2,
        input_device="USB Mic",
        gain_mode="automatic",
        gain=6.0,
        highpass_filter=True,
        highpass_freq=180,
        soft_limiter=True,
    )

    assert mode.runtime_is_idle is True
    assert mode.audio_input_status == {
        "processing_mode": "macos_voice_processing",
        "echo_cancellation": "active",
    }
    mode.apply_audio_runtime_config(audio)
    mode.refresh_asr_runtime()

    mode._recorder.apply_capture_config.assert_called_once_with(audio)
    mode._recorder.set_sample_rate.assert_not_called()
    mode._recorder.set_blocksize.assert_not_called()
    mode._recorder.set_capture_channels.assert_not_called()
    mode._recorder.set_device.assert_not_called()
    mode._recorder.set_gain_mode.assert_not_called()
    mode._recorder.set_gain.assert_not_called()
    mode._recorder.set_highpass_filter.assert_not_called()
    mode._recorder.set_highpass_freq.assert_not_called()
    mode._recorder.set_soft_limiter.assert_not_called()
    mode._recorder.refresh_planned_input_status.assert_not_called()
    mode._asr.refresh_runtime_config.assert_called_once_with(drop_idle_session=True)

    mode._state = ModeState.RECORDING
    assert mode.runtime_is_idle is False
