"""Runtime environment checks surfaced in the menu bar app and diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Optional

from .config import Config
from .core.audio_recorder import AudioRecorder

EnvironmentStatus = Literal["ok", "error", "unknown"]


@dataclass
class EnvironmentCheckResult:
    """Status of one runtime prerequisite."""

    key: str
    status: EnvironmentStatus
    details: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict:
        return asdict(self)


def _check_api_key(config: Config) -> EnvironmentCheckResult:
    error = config.ensure_api_key()
    if error:
        return EnvironmentCheckResult("api_key", "error", error)
    return EnvironmentCheckResult("api_key", "ok", "configured")


def is_accessibility_trusted() -> Optional[bool]:
    """Return the current macOS Accessibility trust state when available."""
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def _check_accessibility() -> EnvironmentCheckResult:
    try:
        from ApplicationServices import AXIsProcessTrusted

        trusted = bool(AXIsProcessTrusted())
    except Exception as exc:
        return EnvironmentCheckResult("accessibility", "unknown", str(exc))

    if trusted:
        return EnvironmentCheckResult("accessibility", "ok", "trusted")
    return EnvironmentCheckResult("accessibility", "error", "missing")


def _check_input_device() -> EnvironmentCheckResult:
    try:
        devices = AudioRecorder.list_input_devices()
    except Exception as exc:
        return EnvironmentCheckResult("input_device", "error", str(exc))

    if not devices:
        return EnvironmentCheckResult("input_device", "error", "no input devices")
    return EnvironmentCheckResult(
        "input_device",
        "ok",
        f"{len(devices)} available",
    )


def _check_hotkey_listener(hotkey_listener_ready: Optional[bool]) -> EnvironmentCheckResult:
    if hotkey_listener_ready is True:
        return EnvironmentCheckResult("hotkey_listener", "ok", "running")
    if hotkey_listener_ready is False:
        return EnvironmentCheckResult("hotkey_listener", "error", "failed to start")
    return EnvironmentCheckResult("hotkey_listener", "unknown", "not started")


def run_environment_checks(
    config: Config,
    hotkey_listener_ready: Optional[bool] = None,
) -> list[EnvironmentCheckResult]:
    """Run the user-visible environment checks."""
    return [
        _check_api_key(config),
        _check_accessibility(),
        _check_input_device(),
        _check_hotkey_listener(hotkey_listener_ready),
    ]
