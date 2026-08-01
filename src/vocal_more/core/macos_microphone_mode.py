"""Privacy-safe access to macOS Control Center microphone modes.

This leaf module deliberately has no Vocal More runtime dependencies. Both
capture backends and the static capability probe may therefore use it without
creating an import cycle or opening microphone input.
"""

from __future__ import annotations

import importlib
import platform


def _microphone_mode_label(av_module, value: object) -> str | None:
    labels = (
        ("AVCaptureMicrophoneModeStandard", "standard"),
        ("AVCaptureMicrophoneModeWideSpectrum", "wide_spectrum"),
        ("AVCaptureMicrophoneModeVoiceIsolation", "voice_isolation"),
    )
    for constant_name, label in labels:
        constant = getattr(av_module, constant_name, None)
        if constant is not None and value == constant:
            return label
    return None


def microphone_mode_observation(
    av_module,
    *,
    system: str,
) -> dict[str, object]:
    """Read preferred/active mode without device enumeration or audio I/O."""
    if system != "Darwin":
        return {
            "status": "unknown",
            "preferred": None,
            "active": None,
            "preferred_differs_from_active": None,
            "reason_code": "not_macos",
            "detail": None,
        }
    capture_device = getattr(av_module, "AVCaptureDevice", None)
    if capture_device is None:
        return {
            "status": "unknown",
            "preferred": None,
            "active": None,
            "preferred_differs_from_active": None,
            "reason_code": "avcapture_device_unavailable",
            "detail": None,
        }
    preferred_reader = getattr(capture_device, "preferredMicrophoneMode", None)
    active_reader = getattr(capture_device, "activeMicrophoneMode", None)
    if not callable(preferred_reader) or not callable(active_reader):
        return {
            "status": "unavailable",
            "preferred": None,
            "active": None,
            "preferred_differs_from_active": None,
            "reason_code": "microphone_mode_api_unavailable",
            "detail": None,
        }
    try:
        preferred = _microphone_mode_label(av_module, preferred_reader())
        active = _microphone_mode_label(av_module, active_reader())
    except Exception as exc:
        return {
            "status": "unknown",
            "preferred": None,
            "active": None,
            "preferred_differs_from_active": None,
            "reason_code": "microphone_mode_query_failed",
            "detail": f"{type(exc).__name__}: {exc}"[:500],
        }
    if preferred is None or active is None:
        return {
            "status": "unknown",
            "preferred": preferred,
            "active": active,
            "preferred_differs_from_active": None,
            "reason_code": "microphone_mode_value_unknown",
            "detail": None,
        }
    return {
        "status": "available",
        "preferred": preferred,
        "active": active,
        "preferred_differs_from_active": preferred != active,
        "reason_code": None,
        "detail": None,
    }


def macos_microphone_modes(
    *,
    av_module=None,
    system: str | None = None,
) -> tuple[str | None, str | None]:
    """Return preferred/active labels without touching microphone input."""
    observed_system = str(system or platform.system() or "").strip()
    if av_module is None and observed_system == "Darwin":
        try:
            av_module = importlib.import_module("AVFoundation")
        except Exception:
            return None, None
    observation = microphone_mode_observation(
        av_module,
        system=observed_system,
    )
    return (
        observation.get("preferred"),
        observation.get("active"),
    )
