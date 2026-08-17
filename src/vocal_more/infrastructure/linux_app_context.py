"""GNOME-reported foreground application identity for Linux.

GNOME Shell is the component that knows which application currently owns the
focused window on Wayland.  The Python process therefore deliberately does
not try to inspect window titles or talk to X11.  The Shell extension updates
an instance of :class:`LinuxAppContextProvider` over the desktop D-Bus
boundary, and consumers read the last safe value through a callable provider.

The provider is intentionally small and dependency free so it can be shared by
the D-Bus adapter and application services without importing GTK or GI in
worker threads.
"""

from __future__ import annotations

from threading import RLock

_MAX_APP_ID_LENGTH = 512


def normalize_desktop_app_id(value: object) -> str:
    """Return a safe desktop application identifier or ``""``.

    GNOME normally reports a desktop file ID (for example
    ``org.gnome.Terminal``).  This boundary accepts the identifier as an
    opaque value; classification is handled by the domain layer.  Rejecting
    NULs, control characters and very large values prevents malformed D-Bus
    payloads from leaking into persisted context or logs.
    """

    if value is None:
        return ""
    try:
        candidate = str(value).strip()
    except Exception:
        return ""
    if not candidate or len(candidate) > _MAX_APP_ID_LENGTH:
        return ""
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in candidate):
        return ""
    return candidate


class LinuxAppContextProvider:
    """Thread-safe, externally updatable current desktop app ID provider."""

    def __init__(self, initial_app_id: object = "") -> None:
        self._lock = RLock()
        self._app_id = normalize_desktop_app_id(initial_app_id)

    def update(self, app_id: object) -> str:
        """Replace the current ID and return the normalized value.

        An empty update means that the Shell has no focused application (or
        that focus was lost).  It is a valid state and intentionally clears
        the previous value rather than retaining stale context.
        """

        normalized = normalize_desktop_app_id(app_id)
        with self._lock:
            self._app_id = normalized
        return normalized

    # These names make the provider convenient to use from a D-Bus handler
    # while keeping ``update`` as the canonical API.
    set_app_id = update
    set_current_app_id = update
    set_current_desktop_app_id = update
    update_current_app_id = update

    def clear(self) -> None:
        """Forget the current app ID after focus/session changes."""

        self.update("")

    def current(self) -> str:
        """Return the current ID, or an empty string when unavailable."""

        with self._lock:
            return self._app_id

    @property
    def current_app_id(self) -> str:
        return self.current()

    def __call__(self) -> str:
        return self.current()


_default_provider = LinuxAppContextProvider()


def get_linux_app_context_provider() -> LinuxAppContextProvider:
    """Return the process-wide provider used by Linux composition roots."""

    return _default_provider


def current_desktop_app_id() -> str:
    """Read the process-wide GNOME-reported application identifier."""

    return _default_provider.current()


def set_current_desktop_app_id(app_id: object) -> str:
    """Update the process-wide provider from a trusted D-Bus adapter."""

    return _default_provider.update(app_id)


# Friendly aliases for callers that use ``get_current_*`` terminology.
get_current_desktop_app_id = current_desktop_app_id
update_current_desktop_app_id = set_current_desktop_app_id
get_current_app_id = current_desktop_app_id
set_current_app_id = set_current_desktop_app_id
get_focused_app_id = current_desktop_app_id
set_focused_app_id = set_current_desktop_app_id
current_focused_app_id = current_desktop_app_id


__all__ = [
    "LinuxAppContextProvider",
    "current_desktop_app_id",
    "current_focused_app_id",
    "get_current_app_id",
    "get_current_desktop_app_id",
    "get_focused_app_id",
    "get_linux_app_context_provider",
    "normalize_desktop_app_id",
    "set_current_app_id",
    "set_current_desktop_app_id",
    "set_focused_app_id",
    "update_current_desktop_app_id",
]
