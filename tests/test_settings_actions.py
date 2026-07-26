from __future__ import annotations

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
        {"action": "setConfig", "key": "llm.polish_mode", "value": "prompt"}
    ) == {"action": "set_config", "key": "llm.polish_mode", "value": "prompt"}
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

    recorder.start.assert_called_once_with()
    recorder.stop.assert_called_once_with()
    assert factory_kwargs == [{"on_audio_level": controller._handle_audio_level}]
    timer.cancel.assert_called_once_with()
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
