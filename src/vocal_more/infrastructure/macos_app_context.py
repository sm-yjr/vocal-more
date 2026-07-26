"""Read only the frontmost macOS application's bundle identifier."""

from __future__ import annotations


def frontmost_bundle_id() -> str:
    """Return the active app bundle ID without inspecting windows or focused text."""
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return ""
        return str(app.bundleIdentifier() or "")
    except Exception:
        return ""


__all__ = ["frontmost_bundle_id"]
