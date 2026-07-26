"""Tests for settings window shell behavior."""

from types import SimpleNamespace

from vocal_more.ui.settings_window import SettingsWindow


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
