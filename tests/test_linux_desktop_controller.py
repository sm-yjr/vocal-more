from __future__ import annotations

import time
from types import SimpleNamespace

from vocal_more.linux_desktop_controller import LinuxDesktopController


class _Handler:
    def __init__(self):
        self.calls = []

    def dispatch(self, method, params):
        self.calls.append((method, params))
        if method == "initialize":
            return {"state": "idle", "current_mode": "realtime_long"}
        return {"ok": True, "mode": params.get("mode")}

    def close(self):
        self.calls.append(("close", {}))


def _config():
    return SimpleNamespace(
        api_key="key",
        default_mode="realtime_long",
        auto_paste=True,
        ui=SimpleNamespace(language="en"),
        hotkey=SimpleNamespace(linux_accelerator="F9"),
    )


def test_controller_snapshot_is_privacy_safe_and_tracks_runtime_state():
    snapshots = []
    handler = _Handler()
    controller = LinuxDesktopController(
        config=_config(),
        handler=handler,
        on_snapshot=snapshots.append,
        on_show_settings=lambda: None,
        on_quit=lambda: None,
    )

    controller.handle_runtime_notification("state_changed", {"state": "recording"})
    controller.handle_runtime_notification("audio_level", {"rms": 0.4})

    snapshot = snapshots[-1]
    assert snapshot.state == "recording"
    assert snapshot.audio_level == 0.4
    assert snapshot.trigger_label == "F9"
    assert "transcript" not in snapshot.to_json()
    controller.close()


def test_controller_forwards_walkie_press_and_release_in_order():
    handler = _Handler()
    config = _config()
    config.default_mode = "walkie_talkie"
    controller = LinuxDesktopController(
        config=config,
        handler=handler,
        on_snapshot=lambda _snapshot: None,
        on_show_settings=lambda: None,
        on_quit=lambda: None,
    )
    controller.handle_runtime_notification("state_changed", {"state": "idle"})
    controller._mode = "walkie_talkie"

    pressed = controller.submit_trigger_pressed()
    pressed.result(timeout=1)
    controller.handle_runtime_notification("state_changed", {"state": "recording"})
    released = controller.submit_trigger_released()
    released.result(timeout=1)

    assert ("hotkey_pressed", {}) in handler.calls
    assert ("hotkey_released", {}) in handler.calls
    controller.close()


def test_controller_rejects_mode_change_while_recording():
    notices = []
    handler = _Handler()
    controller = LinuxDesktopController(
        config=_config(),
        handler=handler,
        on_snapshot=lambda _snapshot: None,
        on_show_settings=lambda: None,
        on_quit=lambda: None,
        on_notice=lambda title, body: notices.append((title, body)),
    )
    controller.handle_runtime_notification("state_changed", {"state": "recording"})

    future = controller.submit_set_mode("meeting")
    try:
        future.result(timeout=1)
    except RuntimeError:
        pass
    deadline = time.monotonic() + 1
    while not notices and time.monotonic() < deadline:
        time.sleep(0.01)

    assert notices
    assert ("set_mode", {"mode": "meeting"}) not in handler.calls
    controller.close()
