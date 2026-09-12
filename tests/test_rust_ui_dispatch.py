"""The thin UI must wake on events, retain ordering and stop after close."""
import queue
import importlib.util
from pathlib import Path
import sys
import threading
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
