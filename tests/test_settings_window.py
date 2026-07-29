"""Tests for settings window shell behavior."""

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

from vocal_more.ui.settings_window import SettingsWindow


def test_settings_window_preserves_menu_bar_activation_policy():
    module = importlib.import_module("vocal_more.ui.settings_window")
    app = MagicMock()
    module.NSApp = app

    module._activate_menu_bar_app()

    app.setActivationPolicy_.assert_called_once_with(
        module.NSApplicationActivationPolicyAccessory
    )
    app.activateIgnoringOtherApps_.assert_called_once_with(True)


def test_js_messages_are_parsed_and_dispatched_through_shell():
    """SettingsWindow should delegate JS bodies through bridge + dispatcher."""
    captured = {}

    window = SettingsWindow.__new__(SettingsWindow)
    window._bridge = SimpleNamespace(
        parse=lambda body: {
            "action": "sync_form_state",
            "payload": body["state"],
        }
    )
    window._dispatcher = SimpleNamespace(
        dispatch=lambda message: captured.update(message)
    )

    window._on_js_message(
        {
            "action": "syncFormState",
            "state": {"asr": {"model": "qwen3-asr-flash"}, "enable_polish": True},
        }
    )

    assert captured == {
        "action": "sync_form_state",
        "payload": {"asr": {"model": "qwen3-asr-flash"}, "enable_polish": True},
    }
    assert window._last_synced_state is not None


def test_js_messages_with_unknown_action_are_ignored():
    """Unknown JS messages should not reach the dispatcher."""
    called = {"value": False}

    window = SettingsWindow.__new__(SettingsWindow)
    window._bridge = SimpleNamespace(parse=lambda body: None)
    window._dispatcher = SimpleNamespace(
        dispatch=lambda message: called.__setitem__("value", True)
    )

    window._on_js_message({"action": "unknownAction"})

    assert called["value"] is False


def test_update_devices_can_sync_selected_device_to_frontend():
    """Backend refreshes should also correct the frontend's selected mic state."""
    calls = []

    window = SettingsWindow.__new__(SettingsWindow)
    window._eval_js = lambda script: calls.append(script)

    window.update_devices([{"name": "Built-in Mic"}], None)

    assert calls == ['loadDevices([{"name": "Built-in Mic"}], null)']


def test_update_environment_checks_syncs_onboarding_readiness():
    calls = []

    window = SettingsWindow.__new__(SettingsWindow)
    window._eval_js = lambda script: calls.append(script)

    window.update_environment_checks(
        [
            {"key": "accessibility", "status": "ok", "details": "trusted"},
            {"key": "hotkey_listener", "status": "ok", "details": "running"},
        ]
    )

    assert calls == [
        'loadEnvironmentChecks([{"key": "accessibility", "status": "ok", '
        '"details": "trusted"}, {"key": "hotkey_listener", "status": "ok", '
        '"details": "running"}])'
    ]


def test_background_js_uses_one_shot_event_driven_queue_drain(monkeypatch):
    module = importlib.import_module("vocal_more.ui.settings_window")
    scheduled = []

    class FakeTimer:
        def __init__(self, callback):
            self.callback = callback

        def invalidate(self):
            return None

    class FakeRunLoop:
        def addTimer_forMode_(self, timer, mode):
            scheduled.append((timer, mode))

    timer_calls = []

    def make_timer(interval, repeats, callback):
        timer_calls.append((interval, repeats))
        return FakeTimer(callback)

    monkeypatch.setattr(
        module.NSTimer,
        "timerWithTimeInterval_repeats_block_",
        make_timer,
        raising=False,
    )
    monkeypatch.setattr(
        module.NSRunLoop,
        "mainRunLoop",
        lambda: FakeRunLoop(),
        raising=False,
    )
    monkeypatch.setattr(module.threading, "current_thread", lambda: object())
    main_thread = object()
    monkeypatch.setattr(module.threading, "main_thread", lambda: main_thread)

    window = SettingsWindow.__new__(SettingsWindow)
    window._webview = MagicMock()
    window._js_queue = module.queue.Queue()
    window._js_drain_timer = None
    window._js_drain_lock = module.threading.Lock()

    window._eval_js("loadRecordings([])")

    assert timer_calls == [(0, False)]
    window._webview.evaluateJavaScript_completionHandler_.assert_not_called()

    scheduled[0][0].callback(None)

    window._webview.evaluateJavaScript_completionHandler_.assert_called_once_with(
        "loadRecordings([])",
        None,
    )
