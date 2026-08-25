from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_settings_bridge_normalizes_sync_form_state_message():
    from vocal_more.ui.settings_bridge import SettingsBridge

    bridge = SettingsBridge()

    message = bridge.parse(
        {
            "action": "syncFormState",
            "state": {"audio": {"gain": 3.0}},
        }
    )

    assert message == {
        "action": "sync_form_state",
        "payload": {"audio": {"gain": 3.0}},
    }


def test_settings_bridge_rejects_unknown_config_keys():
    from vocal_more.ui.settings_bridge import SettingsBridge

    bridge = SettingsBridge()

    assert bridge.parse(
        {"action": "setConfig", "key": "debug.dump_everything", "value": True}
    ) is None
    assert bridge.parse(
        {"action": "setConfig", "key": "audio.gain", "value": 3.0}
    ) == {"action": "set_config", "key": "audio.gain", "value": 3.0}
    assert bridge.parse(
        {"action": "setConfig", "key": "native_fast_paste", "value": False}
    ) == {
        "action": "set_config",
        "key": "native_fast_paste",
        "value": False,
    }
    assert bridge.parse(
        {"action": "setConfig", "key": "restore_clipboard", "value": False}
    ) == {
        "action": "set_config",
        "key": "restore_clipboard",
        "value": False,
    }
    assert bridge.parse(
        {"action": "setConfig", "key": "streaming_paste", "value": True}
    ) == {
        "action": "set_config",
        "key": "streaming_paste",
        "value": True,
    }
    endpoint = "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
    assert bridge.parse(
        {"action": "setConfig", "key": "asr.realtime_url", "value": endpoint}
    ) == {
        "action": "set_config",
        "key": "asr.realtime_url",
        "value": endpoint,
    }
    assert bridge.parse(
        {"action": "setConfig", "key": "audio.gain_mode", "value": "automatic"}
    ) == {
        "action": "set_config",
        "key": "audio.gain_mode",
        "value": "automatic",
    }
    assert bridge.parse(
        {"action": "previewConfig", "key": "audio.gain", "value": 3.0}
    ) == {"action": "preview_config", "key": "audio.gain", "value": 3.0}
    assert bridge.parse(
        {"action": "previewConfig", "key": "llm.level", "value": "strong"}
    ) is None
    assert bridge.parse(
        {
            "action": "setConfig",
            "key": "audio.waveform_ceiling_dbfs",
            "value": -6,
        }
    ) == {
        "action": "set_config",
        "key": "audio.waveform_ceiling_dbfs",
        "value": -6,
    }
    assert bridge.parse(
        {"action": "setConfig", "key": "llm.polish_mode", "value": "prompt"}
    ) == {"action": "set_config", "key": "llm.polish_mode", "value": "prompt"}
    assert bridge.parse(
        {"action": "setConfig", "key": "llm.output_language", "value": "en"}
    ) == {"action": "set_config", "key": "llm.output_language", "value": "en"}
    overrides = {"tone": {"enabled": True, "prompt": "Keep it warm"}}
    assert bridge.parse(
        {"action": "setConfig", "key": "llm.prompt_overrides", "value": overrides}
    ) == {"action": "set_config", "key": "llm.prompt_overrides", "value": overrides}
    assert bridge.parse(
        {
            "action": "setConfig",
            "key": "dictionary_learning.enabled",
            "value": True,
        }
    ) == {
        "action": "set_config",
        "key": "dictionary_learning.enabled",
        "value": True,
    }
    assert bridge.parse(
        {
            "action": "setConfig",
            "key": "ui.onboarding_completed",
            "value": True,
        }
    ) == {
        "action": "set_config",
        "key": "ui.onboarding_completed",
        "value": True,
    }
    assert bridge.parse(
        {
            "action": "setConfig",
            "key": "ui.advanced_settings",
            "value": True,
        }
    ) == {
        "action": "set_config",
        "key": "ui.advanced_settings",
        "value": True,
    }


def test_settings_bridge_sanitizes_sync_form_state_payload():
    from vocal_more.ui.settings_bridge import SettingsBridge

    bridge = SettingsBridge()

    message = bridge.parse(
        {
            "action": "syncFormState",
            "state": {
                "api_key": "sk-test",
                "legacy_mode": True,
                "audio": {"gain": 3.0, "debug_path": "/tmp/private"},
                "llm": {
                    "level": "strong",
                    "polish_mode": "prompt",
                    "output_language": "en",
                    "prompt_overrides": {"tone": {"enabled": True, "prompt": "warm"}},
                    "unknown": True,
                },
                "hotkey": "not-a-dict",
                "dictionary_learning": {
                    "enabled": True,
                    "excluded_bundle_ids": ["com.1password.1password"],
                    "unknown": "ignored",
                },
            },
        }
    )

    assert message == {
        "action": "sync_form_state",
        "payload": {
            "api_key": "sk-test",
            "audio": {"gain": 3.0},
            "llm": {
                "level": "strong",
                "polish_mode": "prompt",
                "output_language": "en",
                "prompt_overrides": {"tone": {"enabled": True, "prompt": "warm"}},
            },
            "dictionary_learning": {
                "enabled": True,
                "excluded_bundle_ids": ["com.1password.1password"],
            },
        },
    }


def test_settings_bridge_rejects_malformed_payloads():
    from vocal_more.ui.settings_bridge import SettingsBridge

    bridge = SettingsBridge()

    assert bridge.parse({"action": "setActiveHotkeys", "hotkeys": "fn"}) is None
    assert bridge.parse({"action": "retryTranscription", "id": ["rec-1"]}) is None
    assert bridge.parse({"action": "generateMeetingNotes", "id": ["rec-1"]}) is None
    assert bridge.parse({"action": "setAsrModel", "model": "", "backend": "realtime_ws"}) is None


def test_settings_bridge_allows_only_expected_external_urls():
    from vocal_more.ui.settings_bridge import SettingsBridge

    bridge = SettingsBridge()

    assert bridge.parse(
        {
            "action": "openExternal",
            "url": "https://dashscope.console.aliyun.com/apiKey",
        }
    ) == {
        "action": "open_external",
        "url": "https://dashscope.console.aliyun.com/apiKey",
    }
    assert bridge.parse({"action": "openExternal", "url": "file:///etc/passwd"}) is None
    assert bridge.parse({"action": "openExternal", "url": "https://example.com"}) is None


def test_settings_action_dispatcher_routes_sync_form_state():
    from vocal_more.ui.settings_actions import SettingsActionDispatcher

    calls: list[tuple[str, dict]] = []
    dispatcher = SettingsActionDispatcher(
        on_sync_form_state=lambda payload: calls.append(("sync", payload))
    )

    dispatcher.dispatch(
        {
            "action": "sync_form_state",
            "payload": {"audio": {"gain": 3.0}},
        }
    )

    assert calls == [("sync", {"audio": {"gain": 3.0}})]


def test_settings_action_dispatcher_routes_audio_preview_without_persistence():
    from vocal_more.ui.settings_actions import SettingsActionDispatcher

    previews = []
    persisted = []
    mic_test_controller = MagicMock()
    dispatcher = SettingsActionDispatcher(
        on_preview_config=lambda key, value: previews.append((key, value)),
        on_set_config=lambda key, value: persisted.append((key, value)),
        mic_test_controller=mic_test_controller,
    )

    dispatcher.dispatch(
        {"action": "preview_config", "key": "audio.gain", "value": 4.0}
    )

    assert previews == [("audio.gain", 4.0)]
    assert persisted == []
    mic_test_controller.apply_audio_setting.assert_called_once_with(
        "audio.gain",
        4.0,
    )

def test_settings_bridge_and_dispatcher_route_onboarding_environment_actions():
    from vocal_more.ui.settings_actions import SettingsActionDispatcher
    from vocal_more.ui.settings_bridge import SettingsBridge

    bridge = SettingsBridge()
    calls: list[str] = []
    dispatcher = SettingsActionDispatcher(
        on_refresh_environment=lambda: calls.append("refresh"),
        on_open_accessibility_settings=lambda: calls.append("accessibility"),
    )

    for browser_action, normalized_action in (
        ("refreshEnvironment", "refresh_environment"),
        ("openAccessibilitySettings", "open_accessibility_settings"),
    ):
        message = bridge.parse({"action": browser_action})
        assert message == {"action": normalized_action}
        dispatcher.dispatch(message)

    assert calls == ["refresh", "accessibility"]


def test_settings_bridge_and_dispatcher_route_dashscope_model_check():
    from vocal_more.ui.settings_actions import SettingsActionDispatcher
    from vocal_more.ui.settings_bridge import SettingsBridge

    calls = []
    dispatcher = SettingsActionDispatcher(
        on_check_dashscope_models=lambda: calls.append("check")
    )

    message = SettingsBridge().parse({"action": "checkDashScopeModels"})
    dispatcher.dispatch(message)

    assert message == {"action": "check_dashscope_models"}
    assert calls == ["check"]


def test_settings_bridge_and_dispatcher_route_dictionary_learning_actions():
    from vocal_more.ui.settings_actions import SettingsActionDispatcher
    from vocal_more.ui.settings_bridge import SettingsBridge

    bridge = SettingsBridge()
    calls: list[tuple[str, str]] = []
    dispatcher = SettingsActionDispatcher(
        on_approve_dictionary_learning=lambda job_id: calls.append(
            ("approve", job_id)
        ),
        on_reject_dictionary_learning=lambda job_id: calls.append(
            ("reject", job_id)
        ),
        on_undo_dictionary_learning=lambda job_id: calls.append(
            ("undo", job_id)
        ),
    )

    for browser_action, normalized_action in (
        ("approveDictionaryLearning", "approve_dictionary_learning"),
        ("rejectDictionaryLearning", "reject_dictionary_learning"),
        ("undoDictionaryLearning", "undo_dictionary_learning"),
    ):
        message = bridge.parse({"action": browser_action, "id": "job-1"})
        assert message == {"action": normalized_action, "id": "job-1"}
        dispatcher.dispatch(message)

    assert calls == [
        ("approve", "job-1"),
        ("reject", "job-1"),
        ("undo", "job-1"),
    ]


def test_settings_action_dispatcher_routes_mic_test_actions():
    from vocal_more.ui.settings_actions import SettingsActionDispatcher

    mic_test_controller = MagicMock()
    dispatcher = SettingsActionDispatcher(mic_test_controller=mic_test_controller)

    dispatcher.dispatch({"action": "start_mic_test"})
    dispatcher.dispatch({"action": "stop_mic_test"})
    dispatcher.dispatch({"action": "play_mic_test"})

    mic_test_controller.start.assert_called_once_with()
    mic_test_controller.stop.assert_called_once_with()
    mic_test_controller.play.assert_called_once_with()


def test_mic_test_controller_cleans_up_recorder_after_stop():
    from vocal_more.ui.mic_test_controller import MicTestController

    recorder = MagicMock()
    recorder.stop.return_value = b"pcm"
    timer = MagicMock()
    factory_kwargs = []

    controller = MicTestController(
        config_provider=lambda: SimpleNamespace(
            audio=SimpleNamespace(
                input_device="Built-in Mic",
                gain_mode="automatic",
                gain=4.0,
                highpass_filter=True,
                highpass_freq=150,
                soft_limiter=True,
            )
        ),
        recorder_factory=lambda **kwargs: factory_kwargs.append(kwargs) or recorder,
        timer_factory=lambda interval, callback: timer,
    )

    controller.start()
    controller.stop()

    recorder.start.assert_not_called()
    recorder.start_capture_session.assert_called_once()
    capture_config = recorder.start_capture_session.call_args.args[0]
    assert capture_config is not controller._config_provider().audio
    assert capture_config.gain_mode == "automatic"
    recorder.set_gain_mode.assert_not_called()
    recorder.stop.assert_called_once_with()
    assert factory_kwargs == [{"on_audio_level": controller._handle_audio_level}]
    timer.cancel.assert_called_once_with()
    assert controller.is_running is False


def test_mic_test_concurrent_stop_claims_the_native_recorder_once():
    from vocal_more.ui.mic_test_controller import MicTestController

    entered = threading.Event()
    release = threading.Event()
    stop_calls: list[int] = []
    calls_lock = threading.Lock()

    class BlockingRecorder:
        input_status = {"phase": "active"}

        def start_capture_session(self, _audio_config):
            return None

        def stop(self):
            with calls_lock:
                stop_calls.append(len(stop_calls) + 1)
                call_number = stop_calls[-1]
            entered.set()
            assert release.wait(timeout=1.0)
            return b"preserved-pcm" if call_number == 1 else b""

    timer = MagicMock()
    completed: list[str] = []
    controller = MicTestController(
        config_provider=lambda: SimpleNamespace(
            audio=SimpleNamespace(
                input_device="Built-in Mic",
                gain_mode="automatic",
                gain=4.0,
                blocksize=640,
                capture_channels=1,
                highpass_filter=True,
                highpass_freq=150,
                soft_limiter=True,
            )
        ),
        recorder_factory=lambda **_kwargs: BlockingRecorder(),
        timer_factory=lambda _interval, _callback: timer,
        on_complete=lambda: completed.append("complete"),
    )
    controller.start()

    first = threading.Thread(target=controller.stop)
    second = threading.Thread(target=controller.stop)
    first.start()
    assert entered.wait(timeout=1.0)
    second.start()
    second.join(timeout=0.2)
    release.set()
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert stop_calls == [1]
    assert controller._pcm_data == b"preserved-pcm"
    assert completed == ["complete"]
    assert controller.is_running is False


def test_mic_test_stale_auto_stop_cannot_stop_a_new_session():
    from vocal_more.ui.mic_test_controller import MicTestController

    class FakeTimer:
        def __init__(self, callback):
            self.callback = callback
            self.daemon = False
            self.cancelled = False

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

    recorders = [MagicMock(), MagicMock()]
    timers: list[FakeTimer] = []
    completed: list[str] = []
    controller = MicTestController(
        config_provider=lambda: SimpleNamespace(
            audio=SimpleNamespace(
                input_device="Built-in Mic",
                gain_mode="automatic",
                gain=4.0,
                blocksize=640,
                capture_channels=1,
                highpass_filter=True,
                highpass_freq=150,
                soft_limiter=True,
            )
        ),
        recorder_factory=lambda **_kwargs: recorders.pop(0),
        timer_factory=lambda _interval, callback: timers.append(
            FakeTimer(callback)
        )
        or timers[-1],
        on_complete=lambda: completed.append("complete"),
    )

    controller.start()
    first_recorder = controller._recorder
    first_timer = timers[-1]
    controller.start()
    second_recorder = controller._recorder
    second_timer = timers[-1]

    first_timer.callback()

    assert first_timer.cancelled is True
    first_recorder.stop.assert_called_once_with()
    second_recorder.stop.assert_not_called()
    assert controller.is_running is True

    second_timer.callback()

    second_recorder.stop.assert_called_once_with()
    assert completed == ["complete"]
    assert controller.is_running is False


def test_mic_test_manual_stop_during_startup_rejects_the_late_recorder():
    from vocal_more.ui.mic_test_controller import MicTestController

    start_entered = threading.Event()
    release_start = threading.Event()
    stop_calls: list[str] = []

    class BlockingStartRecorder:
        input_status = {"phase": "starting"}

        def start_capture_session(self, _audio_config):
            start_entered.set()
            assert release_start.wait(timeout=1.0)

        def stop(self):
            stop_calls.append("stop")
            return b"late-pcm"

    started: list[str] = []
    timer = MagicMock()
    controller = MicTestController(
        config_provider=lambda: SimpleNamespace(
            audio=SimpleNamespace(
                input_device="Built-in Mic",
                gain_mode="automatic",
                gain=4.0,
                blocksize=640,
                capture_channels=1,
                highpass_filter=True,
                highpass_freq=150,
                soft_limiter=True,
            )
        ),
        recorder_factory=lambda **_kwargs: BlockingStartRecorder(),
        timer_factory=lambda _interval, _callback: timer,
        on_started=lambda: started.append("started"),
    )
    errors: list[BaseException] = []

    def start_controller():
        try:
            controller.start()
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    start_thread = threading.Thread(target=start_controller)
    start_thread.start()
    assert start_entered.wait(timeout=1.0)

    controller.stop()
    release_start.set()
    start_thread.join(timeout=1.0)

    assert not start_thread.is_alive()
    assert errors == []
    assert stop_calls == ["stop"]
    assert started == []
    assert controller.is_running is False


def test_mic_test_permission_request_uses_the_same_localized_retry_message():
    from vocal_more.core.audio_recorder import AudioRecorderStartError
    from vocal_more.ui.mic_test_controller import MicTestController

    recorder = MagicMock()
    recorder.start_capture_session.side_effect = AudioRecorderStartError(
        "permission requested",
        code="microphone_permission_requested",
        stage="permission",
    )
    errors = []
    controller = MicTestController(
        config_provider=lambda: SimpleNamespace(
            ui=SimpleNamespace(language="zh"),
            audio=SimpleNamespace(
                gain_mode="automatic",
                gain=2.0,
                highpass_filter=True,
                highpass_freq=200,
                soft_limiter=True,
            ),
        ),
        recorder_factory=lambda **_kwargs: recorder,
        on_error=errors.append,
    )

    controller.start()

    assert errors == [
        "已请求麦克风权限。请在系统提示中允许访问，然后再次开始录音。"
    ]
    assert controller.is_running is False


def test_settings_bridge_and_dispatcher_route_meeting_notes_action():
    from vocal_more.ui.settings_actions import SettingsActionDispatcher
    from vocal_more.ui.settings_bridge import SettingsBridge

    bridge = SettingsBridge()
    message = bridge.parse({"action": "generateMeetingNotes", "id": "rec-1"})
    calls: list[str] = []
    dispatcher = SettingsActionDispatcher(
        on_generate_meeting_notes=lambda rec_id: calls.append(rec_id)
    )

    assert message == {"action": "generate_meeting_notes", "id": "rec-1"}

    dispatcher.dispatch(message)

    assert calls == ["rec-1"]


def test_settings_bridge_and_dispatcher_reset_context_profile():
    from vocal_more.ui.settings_actions import SettingsActionDispatcher
    from vocal_more.ui.settings_bridge import SettingsBridge

    calls = []
    dispatcher = SettingsActionDispatcher(
        on_reset_context_profile=lambda: calls.append("reset")
    )

    message = SettingsBridge().parse({"action": "resetContextProfile"})
    dispatcher.dispatch(message)

    assert message == {"action": "reset_context_profile"}
    assert calls == ["reset"]


def test_settings_bridge_and_dispatcher_compact_recording_history():
    from vocal_more.ui.settings_actions import SettingsActionDispatcher
    from vocal_more.ui.settings_bridge import SettingsBridge

    calls = []
    dispatcher = SettingsActionDispatcher(
        on_compact_recording_history=lambda: calls.append("compact")
    )

    message = SettingsBridge().parse({"action": "compactRecordingHistory"})
    dispatcher.dispatch(message)

    assert message == {"action": "compact_recording_history"}
    assert calls == ["compact"]
