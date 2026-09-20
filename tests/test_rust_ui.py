"""UX events must reach the release's Rust-backed thin UI."""
import importlib.util
import sys
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setitem(sys.modules, "vocal_more.infrastructure.sparkle_updater",
                        SimpleNamespace(effective_update_channel=lambda value: value or "nightly"))
    monkeypatch.setitem(sys.modules, "rumps", SimpleNamespace(App=object))
    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(
        NSOperationQueue=Mock(), NSRunLoop=Mock(), NSRunLoopCommonModes=Mock(), NSTimer=Mock()))
    spec = importlib.util.spec_from_file_location(
        "vocal_more._rust_ui_ux_test", Path(__file__).resolve().parents[1] / "src/vocal_more/rust_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = module.RustVocalMoreApp.__new__(module.RustVocalMoreApp)
    instance._closing = False
    instance.snapshot = {"generation": 3, "state": "processing"}
    instance._state_item = SimpleNamespace(title="")
    instance.capsule = Mock(_current_state="processing")
    instance.capsule.show_failure.side_effect = lambda _: setattr(instance.capsule, "_current_state", "failure")
    instance.config = SimpleNamespace(auto_paste=True)
    instance._t = lambda key: key
    instance._notify = Mock()
    instance._js = Mock()
    instance.request = Mock()
    instance._copy = Mock()
    return instance


def test_microphone_privacy_action_opens_exact_system_pane(app, monkeypatch):
    popen = Mock()
    monkeypatch.setattr(sys.modules['subprocess'], 'Popen', popen)
    app._dispatch_settings_message({"action": "openMicrophoneSettings"})
    popen.assert_called_once_with([
        "/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"])
    app.request.assert_not_called()


def test_terminal_error_survives_immediate_idle_but_new_recording_replaces_it(app):
    app._event("error", {"generation": 3, "message": "No audio captured"})
    app._event("state_changed", {"state": "idle", "generation": 3})
    app.capsule.show_failure.assert_called_once_with("No audio captured")
    app.capsule.hide.assert_not_called()
    app._event("state_changed", {"state": "starting", "generation": 4, "current_mode": "realtime_long"})
    app.capsule.show.assert_called_once_with("handsFree")


def test_settings_write_failure_restores_authoritative_controls_and_reports_inline(app):
    app._event("request_failed", {"method": "ui_action", "action": "setConfig",
               "key": "network.proxy_url", "message": "invalid proxy"})
    app._js.assert_called_once_with("configError", "network.proxy_url", "invalid proxy")
    callback = app.request.call_args.kwargs["callback"]
    callback({"config": {"network": {"proxy_url": ""}, "api_key": ""}, "api_key_set": True})
    app._js.assert_any_call("updateConfig", "network", {"proxy_url": ""})
    app.capsule.hide.assert_not_called()
    app._notify.assert_not_called()


def test_request_failure_keeps_config_key_for_inline_error(app):
    app._enqueue = Mock()
    future = Future()
    app.client = SimpleNamespace(request=lambda *args: future)
    type(app).request(app, "ui_action", {"action": "setConfig", "key": "network.proxy_url", "value": "bad"})
    future.set_exception(ValueError("invalid proxy"))
    assert app._enqueue.call_args.args[0]["params"]["key"] == "network.proxy_url"


@pytest.mark.parametrize("auto_paste", [True, False])
def test_success_is_quiet_for_paste_and_copies_when_paste_disabled(app, auto_paste):
    app.config.auto_paste = auto_paste
    app._event("final_result", {"text": "transcript"})
    if auto_paste:
        app._copy.assert_not_called()
        app._notify.assert_not_called()
    else:
        app._copy.assert_called_once_with("transcript")
        app._notify.assert_called_once()


def test_config_error_snapshot_resolves_unset_update_channel(app):
    app._event("request_failed", {"method": "set_config", "key": "update_channel", "message": "invalid"})
    app.request.call_args.kwargs["callback"]({"config": {"update_channel": None}, "api_key_set": False})
    app._js.assert_any_call("updateConfig", "update_channel", "nightly")
