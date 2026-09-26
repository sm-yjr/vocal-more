"""The thin UI must wake on events, retain ordering and stop after close."""
import importlib.util
import queue
import sys
import threading
from concurrent.futures import Future
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
    ] + [("final_result", {"text": "done", "_ui_epoch": 0})]
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


@pytest.mark.parametrize("interruption", [None, "cancel", "start", "new_generation"])
def test_rust_paste_constructs_keyboard_on_main_thread_only_while_current(monkeypatch, interruption):
    app, _scheduled = dispatcher(monkeypatch)
    app._retained = {}
    app.snapshot = {"state": "idle", "generation": 7}
    app.config = SimpleNamespace(screen_context_enabled=False)
    app._screen_context_active = False
    app._screen_context_pending = False
    app._text_provider = SimpleNamespace(capture_focused=lambda: None)
    completed = Future()
    completed.set_result({"ok": True})
    app.client = SimpleNamespace(
        request=Mock(return_value=completed),
        call=lambda method, _params: {
            "claim_paste": {
                "token": "paste-token",
                "generation": 7,
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

    if interruption == "new_generation":
        app.snapshot["generation"] = 8
    elif interruption is not None:
        type(app).request(app, interruption)
    type(app)._event(app, queued["method"], queued["params"])

    assert constructed_on == ([threading.main_thread()] if interruption is None else [])
    assert pasted == (["hello"] if interruption is None else [])


def test_next_hotkey_can_paste_after_cancel_when_ui_snapshot_lags_backend(monkeypatch):
    from vocal_more.core import keyboard_sim, macos_native_paste

    app, _scheduled = dispatcher(monkeypatch)
    app.snapshot = {"state": "cancelling", "generation": 7}
    app._paste_epoch = 1
    app._paste_cancelled = True
    app._retained = {}
    app._state_item = SimpleNamespace(title="")
    app.capsule = Mock(_current_state="hidden")
    app._t = lambda key: key
    completed = Future()
    completed.set_result({"generation": 8})
    paste = {
        "token": "new-token", "generation": 8, "text": "new session",
        "observe_correction": False, "native_fast_paste": True,
        "restore_clipboard": False,
    }
    app.client = SimpleNamespace(
        request=Mock(return_value=completed),
        call=lambda method, _params: paste if method == "claim_paste" else {"cancelled": False},
    )
    keyboard = Mock()
    monkeypatch.setattr(keyboard_sim, "KeyboardSimulator", Mock(return_value=keyboard))
    monkeypatch.setattr(macos_native_paste, "MacOSNativePaste", Mock())

    type(app).request(app, "hotkey_pressed")

    assert app._paste_epoch == 2
    assert app._paste_cancelled is False
    type(app)._event(app, "state_changed", {
        "state": "starting", "generation": 8, "current_mode": "walkie_talkie",
    })
    app._paste({"token": "new-token"})
    type(app)._event(app, "state_changed", {"state": "idle", "generation": 8})
    queued = app._events.get_nowait()
    assert queued["method"] == "_deliver_paste"
    type(app)._event(app, queued["method"], queued["params"])
    keyboard.paste_text.assert_called_once_with("new session")


@pytest.mark.parametrize("interruption", [None, "cancel", "start", "new_generation", "cancel_restart"])
def test_queued_clipboard_result_requires_current_session(monkeypatch, interruption):
    app, scheduled = dispatcher(monkeypatch)
    app._event = type(app)._event.__get__(app)
    app.snapshot = {"state": "idle", "generation": 7}
    app.config = SimpleNamespace(auto_paste=False, screen_context_enabled=False)
    app._screen_context_active = False
    app._screen_context_pending = False
    app._copy = Mock()
    app._js = Mock()
    app._t = lambda key: key
    completed = Future()
    completed.set_result({"ok": True})
    app.client = SimpleNamespace(request=Mock(return_value=completed))
    app._enqueue({"method": "final_result", "params": {"text": "old result", "generation": 7}})

    if interruption == "new_generation":
        app.snapshot["generation"] = 8
    elif interruption == "cancel_restart":
        app.request("cancel")
        app.request("start")
    elif interruption is not None:
        app.request(interruption)
    scheduled.pop(0)()

    assert app._last_text == "old result"
    if interruption is None:
        app._copy.assert_called_once_with("old result")
        app._notify.assert_called_once()
    else:
        app._copy.assert_not_called()
        app._notify.assert_not_called()

    # Explicit history copying remains available even after cancellation.
    app._copy.reset_mock()
    app._event("copy_transcript", {"id": "history-1", "text": "manual history"})
    app._copy.assert_called_once_with("manual history")
    app._js.assert_called_once_with("copiedFeedback", "history-1")

    # A subsequent accepted generation can still automatically copy its result.
    app._copy.reset_mock()
    app.request("start")
    app.snapshot["generation"] = 9
    app._enqueue({"method": "final_result", "params": {"text": "new result", "generation": 9}})
    scheduled.pop(0)()
    app._copy.assert_called_once_with("new result")


def screen_dispatcher(monkeypatch):
    app, _scheduled = dispatcher(monkeypatch)
    app.request = Mock()
    app._screen_capture_inflight = True
    app._screen_context_pending = True
    app._screen_context_active = True
    app._screen_context_generation = 2
    app._pending_screen_start = {
        "generation": 2, "jpeg_base64": None, "cancel_requested": False,
    }
    app.snapshot = {"state": "recording", "generation": 2}
    app.config = SimpleNamespace(screen_context_enabled=True)
    app._capture_screen = Mock()
    return app


def test_initial_screen_frame_is_delivered_only_to_its_own_start(monkeypatch):
    app = screen_dispatcher(monkeypatch)
    type(app)._event(
        app,
        "_screen_frame",
        {
            "initial": True,
            "generation": None,
            "screen_start": app._pending_screen_start,
            "jpeg_base64": "/9j/2Q==",
        },
    )

    assert app._screen_capture_inflight is False
    assert app._screen_context_pending is False
    assert app._screen_context_active is True
    app.request.assert_called_once()
    method, params = app.request.call_args.args[:2]
    assert method == "append_screen_frame"
    assert params == {
        "generation": 2,
        "jpeg_base64": "/9j/2Q==",
    }


@pytest.mark.parametrize("method", ["_screen_frame", "_screen_capture_failed"])
@pytest.mark.parametrize("initial", [True, False])
def test_previous_session_capture_cannot_affect_new_screen_session(monkeypatch, method, initial):
    app = screen_dispatcher(monkeypatch)
    current = app._pending_screen_start
    type(app)._event(app, method, {
        "initial": initial,
        "generation": 1,
        "screen_start": {"generation": 1},
        "jpeg_base64": "OLD_CANCELLED_FRAME",
        "message": "Old capture failed",
    })

    app.request.assert_not_called()
    app._notify.assert_not_called()
    assert app._screen_capture_inflight is False
    assert app._pending_screen_start is current
    assert current["jpeg_base64"] is None
    assert app._screen_context_active is True
    assert app._screen_context_generation == 2
    app._capture_screen.assert_called_once_with(initial=True, request_permission=True)


def test_unbound_initial_frame_cannot_start_recording(monkeypatch):
    app = screen_dispatcher(monkeypatch)
    app._pending_screen_start = None
    type(app)._event(app, "_screen_frame", {
        "initial": True, "generation": None, "jpeg_base64": "OLD_FRAME",
    })
    app.request.assert_not_called()
    app._capture_screen.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_capture_result_keeps_original_start_identity(monkeypatch, fails):
    from vocal_more.core import macos_screen_capture

    app = screen_dispatcher(monkeypatch)
    app._screen_capture_inflight = False
    pending = app._pending_screen_start
    app._submit_os = Mock(return_value=True)
    app._enqueue = Mock()
    capture = Mock(return_value=b"mock-jpeg")
    if fails:
        capture.side_effect = RuntimeError("capture failed")
    monkeypatch.setattr(macos_screen_capture, "capture_main_display_jpeg", capture)

    type(app)._capture_screen(app, initial=True, request_permission=True)
    worker, data = app._submit_os.call_args.args
    app._pending_screen_start = {"generation": 3}
    worker(data)

    result = app._enqueue.call_args.args[0]
    assert result["method"] == ("_screen_capture_failed" if fails else "_screen_frame")
    assert result["params"]["screen_start"] is pending
    assert result["params"]["generation"] == 2
