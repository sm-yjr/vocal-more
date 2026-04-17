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
        recorder_factory=lambda **kwargs: recorder,
        timer_factory=lambda interval, callback: timer,
    )

    controller.start()
    controller.stop()

    recorder.start.assert_called_once_with()
    recorder.stop.assert_called_once_with()
    timer.cancel.assert_called_once_with()
    assert controller.is_running is False
