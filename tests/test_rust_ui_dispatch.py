"""The thin UI must wake on events, retain ordering and stop after close."""
import importlib.util
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="AppKit thin UI")


def dispatcher(monkeypatch):
    import Foundation
    scheduled = []
    monkeypatch.setitem(sys.modules, "rumps", SimpleNamespace(App=object))
    monkeypatch.setattr(Foundation, "NSOperationQueue", SimpleNamespace(
        mainQueue=lambda: SimpleNamespace(addOperationWithBlock_=scheduled.append)), raising=False)
    for name in ("NSRunLoop", "NSRunLoopCommonModes", "NSTimer"):
        if not hasattr(Foundation, name):
            monkeypatch.setattr(Foundation, name, Mock(), raising=False)
    spec = importlib.util.spec_from_file_location("vocal_more._rust_ui_dispatch_test",
        Path(__file__).resolve().parents[1] / "src/vocal_more/rust_ui.py")
    rust_ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rust_ui)
    app = rust_ui.RustVocalMoreApp.__new__(rust_ui.RustVocalMoreApp)
    app._closing = False
    app._drain_lock = threading.Lock()
    app._drain_scheduled = False
    app._events = queue.Queue(maxsize=512)
    app._hotkeys = None
    app._event = Mock()
    app._notify = Mock()
    return app, scheduled


def test_burst_coalesces_main_thread_wakeups_and_preserves_terminal_order(monkeypatch):
    app, scheduled = dispatcher(monkeypatch)
    for index in range(150):
        app._enqueue({"method": "audio_level", "params": {"index": index}})
    app._enqueue({"method": "final_result", "params": {"text": "done"}})
    assert len(scheduled) == 1
    scheduled.pop(0)()
    assert app._event.call_count == 128
    assert len(scheduled) == 1
    scheduled.pop(0)()
    assert [call.args for call in app._event.call_args_list] == [
        ("audio_level", {"index": index}) for index in range(150)
    ] + [("final_result", {"text": "done"})]
    assert app._events.empty() and not scheduled


def test_queued_wakeup_cannot_touch_closed_ui(monkeypatch):
    app, scheduled = dispatcher(monkeypatch)
    app._enqueue({"method": "final_result", "params": {"text": "late"}})
    app._closing = True
    scheduled.pop(0)()
    app._enqueue({"method": "state_changed", "params": {"state": "idle"}})
    app._event.assert_not_called()
    assert not scheduled


def test_config_event_switches_sparkle_update_channel(monkeypatch):
    app, _scheduled = dispatcher(monkeypatch)
    app.snapshot = {"config": {}}
    app.capsule = SimpleNamespace(set_interface_language=Mock())
    app._updater = Mock()
    app._hotkeys = None
    app._js = Mock()
    app._build_menu = Mock()
    config = {
        "api_key": "",
        "update_channel": "nightly",
        "ui": {"language": "zh"},
        "hotkey": {"custom_key": None, "custom_keys": []},
    }

    type(app)._event(
        app,
        "config_changed",
        {"config": config, "api_key_set": False},
    )

    app._updater.set_update_channel.assert_called_once_with("nightly")


def test_watchdog_starts_sparkle_with_configured_channel(monkeypatch):
    from vocal_more.infrastructure import sparkle_updater

    app, _scheduled = dispatcher(monkeypatch)
    updater = Mock()
    updater_factory = Mock(return_value=updater)
    monkeypatch.setattr(sparkle_updater, "SparkleUpdater", updater_factory)
    app._updater = None
    app.config = SimpleNamespace(update_channel="nightly")
    app._hotkeys = None
    app._schedule_drain = Mock()

    type(app)._watchdog(app)

    assert app._updater is updater
    updater_factory.assert_called_once_with(update_channel="nightly")


def test_rust_paste_constructs_keyboard_on_main_thread(monkeypatch):
    app, _scheduled = dispatcher(monkeypatch)
    app._retained = {}
    app._text_provider = SimpleNamespace(capture_focused=lambda: None)
    app.client = SimpleNamespace(
        call=lambda method, _params: {
            "claim_paste": {
                "token": "paste-token",
                "text": "hello",
                "cancelled": False,
                "observe_correction": False,
                "native_fast_paste": True,
                "restore_clipboard": True,
            },
            "prepare_paste_observation": {
                "token": "paste-token",
                "cancelled": False,
                "observation_id": None,
            },
        }[method]
    )
    app.request = Mock()

    from vocal_more.core import keyboard_sim, macos_native_paste

    constructed_on = []
    pasted = []

    class FakeKeyboardSimulator:
        def __init__(self, **_kwargs):
            constructed_on.append(threading.current_thread())

        def paste_text(self, text):
            pasted.append(text)

    monkeypatch.setattr(keyboard_sim, "KeyboardSimulator", FakeKeyboardSimulator)
    monkeypatch.setattr(
        macos_native_paste,
        "MacOSNativePaste",
        lambda **_kwargs: object(),
    )

    worker = threading.Thread(
        target=app._paste,
        args=({"token": "paste-token"},),
    )
    worker.start()
    worker.join()

    queued = app._events.get_nowait()
    assert queued["method"] == "_deliver_paste"
    assert constructed_on == []

    type(app)._event(app, queued["method"], queued["params"])

    assert constructed_on == [threading.main_thread()]
    assert pasted == ["hello"]


def test_screen_frame_starts_explicit_screen_context_session(monkeypatch):
    app, _scheduled = dispatcher(monkeypatch)
    app.request = Mock()
    app._screen_capture_inflight = True
    app._screen_context_pending = True
    app._screen_context_active = False
    app._screen_context_generation = None

    type(app)._event(
        app,
        "_screen_frame",
        {
            "initial": True,
            "generation": None,
            "jpeg_base64": "/9j/2Q==",
        },
    )

    assert app._screen_capture_inflight is False
    assert app._screen_context_pending is False
    assert app._screen_context_active is True
    app.request.assert_called_once()
    method, params = app.request.call_args.args[:2]
    assert method == "start"
    assert params == {
        "screen_context": True,
        "screen_frame_base64": "/9j/2Q==",
    }
