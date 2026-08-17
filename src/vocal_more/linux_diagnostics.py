"""Read-only diagnostics for the Ubuntu GNOME/Wayland host."""

from __future__ import annotations

import ctypes.util
import os
import platform
import shutil
import subprocess
from pathlib import Path


def collect_linux_environment(
    *,
    dbus_ready: bool,
    extension_status: str,
    paste_status: str,
) -> dict[str, str]:
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/invalid"))
    pipewire_ready = (runtime_dir / "pipewire-0").exists()
    shell_version = _gnome_shell_version()
    return {
        "Wayland": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "unknown"),
        "GNOME Shell": shell_version,
        "Shell compatibility": _shell_compatibility(shell_version),
        "D-Bus": "ready" if dbus_ready else "unavailable",
        "extension": extension_status,
        "PipeWire": "ready" if pipewire_ready else "socket unavailable",
        "PipeWire default source": _pipewire_default_source(pipewire_ready),
        "PortAudio": "ready" if ctypes.util.find_library("portaudio") else "unavailable",
        "AT-SPI": _atspi_status(),
        "FLAC": "ready" if shutil.which("flac") else "unavailable",
        "auto-paste": paste_status,
        "session recovery": "D-Bus name-owner reconnect enabled",
    }


def _gnome_shell_version() -> str:
    executable = shutil.which("gnome-shell")
    if executable is None:
        return "unavailable"
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return (completed.stdout or completed.stderr).strip() or "unavailable"


def _atspi_status() -> str:
    if os.environ.get("NO_AT_BRIDGE") == "1":
        return "disabled"
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        del Atspi
        return "ready"
    except Exception:
        return "unavailable"


def _shell_compatibility(version: str) -> str:
    normalized = version.casefold()
    if "gnome shell 50" in normalized:
        return "compatible"
    if version == "unavailable":
        return "unavailable"
    return "unsupported (requires Shell 50)"


def _pipewire_default_source(pipewire_ready: bool) -> str:
    """Report the current PipeWire source without inspecting audio content."""
    if not pipewire_ready:
        return "unavailable"
    executable = shutil.which("wpctl")
    if executable is None:
        return "wpctl unavailable"
    try:
        completed = subprocess.run(
            [executable, "inspect", "@DEFAULT_AUDIO_SOURCE@"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "query failed"
    if completed.returncode != 0:
        return "query failed"
    for line in completed.stdout.splitlines():
        key, separator, value = line.strip().partition(" = ")
        if separator and key in {"node.description", "node.name", "device.description"}:
            return value.strip().strip('"')[:160] or "ready"
    return "ready"


def linux_platform_summary() -> str:
    return f"{platform.system()} {platform.machine()} / Python {platform.python_version()}"


__all__ = ["collect_linux_environment", "linux_platform_summary"]
