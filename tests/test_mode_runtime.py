from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


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
        input_device="USB Mic",
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

    mode._recorder.set_device.assert_called_once_with("USB Mic")
    mode._recorder.set_gain.assert_called_once_with(6.0)
    mode._recorder.set_highpass_filter.assert_called_once_with(True)
    mode._recorder.set_highpass_freq.assert_called_once_with(180)
    mode._recorder.set_soft_limiter.assert_called_once_with(True)
    mode._asr.refresh_runtime_config.assert_called_once_with(drop_idle_session=True)

    mode._state = ModeState.RECORDING
    assert mode.runtime_is_idle is False
