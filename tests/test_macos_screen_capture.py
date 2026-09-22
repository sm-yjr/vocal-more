"""Screen context frames stay in memory and meet provider limits."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="CoreGraphics capture")


def test_capture_requests_permission_without_reading_a_frame(monkeypatch):
    import Quartz

    from vocal_more.core.macos_screen_capture import (
        MAX_JPEG_BYTES,
        ScreenCaptureError,
        capture_main_display_jpeg,
    )

    requested = []
    monkeypatch.setattr(Quartz, "CGPreflightScreenCaptureAccess", lambda: False, raising=False)
    monkeypatch.setattr(
        Quartz,
        "CGRequestScreenCaptureAccess",
        lambda: requested.append(True),
        raising=False,
    )
    monkeypatch.setattr(
        Quartz,
        "CGDisplayCreateImage",
        lambda _display: pytest.fail("capture must not run before permission"),
        raising=False,
    )
    monkeypatch.setattr(Quartz, "CGMainDisplayID", lambda: 1, raising=False)

    with pytest.raises(ScreenCaptureError, match="屏幕录制权限"):
        capture_main_display_jpeg(request_permission=True)

    assert requested == [True]
    assert MAX_JPEG_BYTES == 190 * 1024
