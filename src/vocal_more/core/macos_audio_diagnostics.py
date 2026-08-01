"""Privacy-safe macOS audio capability queries.

These functions never request microphone access and never construct an input
engine.  They are safe for settings snapshots and environment checks.  TCC
permission requests remain tied to an explicit user recording action.
"""

from __future__ import annotations

import ctypes
import importlib
import platform
from pathlib import Path
import re
import threading
from typing import Callable, Literal

from .macos_microphone_mode import (
    macos_microphone_modes,
    microphone_mode_observation as _microphone_mode_observation,
)


AUDIO_CAPABILITY_SCHEMA = "vocal_more.macos_audio_capabilities"
AUDIO_CAPABILITY_SCHEMA_VERSION = 1

_AUTO = object()
_VOICE_PROCESSING_SELECTORS = (
    "setVoiceProcessingEnabled_error_",
    "isVoiceProcessingEnabled",
)
_AGC_SELECTORS = (
    "setVoiceProcessingAGCEnabled_",
    "isVoiceProcessingAGCEnabled",
)
_MICROPHONE_REQUEST_LOCK = threading.Lock()
_microphone_request_in_flight = False


MicrophonePermission = Literal[
    "not_determined",
    "authorized",
    "denied",
    "restricted",
    "unknown",
]


def _query_microphone_permission(
    av_module,
) -> tuple[MicrophonePermission, BaseException | None]:
    try:
        observed = av_module.AVCaptureDevice.authorizationStatusForMediaType_(
            av_module.AVMediaTypeAudio
        )
    except Exception as exc:
        return "unknown", exc

    values = {
        getattr(av_module, "AVAuthorizationStatusNotDetermined", 0): (
            "not_determined"
        ),
        getattr(av_module, "AVAuthorizationStatusRestricted", 1): "restricted",
        getattr(av_module, "AVAuthorizationStatusDenied", 2): "denied",
        getattr(av_module, "AVAuthorizationStatusAuthorized", 3): "authorized",
    }
    return values.get(observed, "unknown"), None


def _microphone_permission_status(av_module) -> MicrophonePermission:
    status, _error = _query_microphone_permission(av_module)
    return status


def microphone_permission_status(
    *,
    system: str | None = None,
) -> MicrophonePermission:
    if (system or platform.system()) != "Darwin":
        return "unknown"
    try:
        import AVFoundation
    except Exception:
        return "unknown"
    return _microphone_permission_status(AVFoundation)


def request_microphone_access(
    *,
    system: str | None = None,
    av_module=_AUTO,
) -> bool:
    """Begin the user-authorized TCC prompt without waiting for its result.

    This is intentionally separate from every static capability query. Callers
    may invoke it only in response to an explicit recording action after they
    have observed ``not_determined``. Apple's completion handler can arrive
    much later; a no-op block keeps this admission operation non-blocking and
    prevents it from being counted as CoreAudio device-start time.
    """
    if (system or platform.system()) != "Darwin":
        return False
    if av_module is _AUTO:
        try:
            import AVFoundation as av_module
        except Exception:
            return False
    global _microphone_request_in_flight
    with _MICROPHONE_REQUEST_LOCK:
        if _microphone_request_in_flight:
            return True
        _microphone_request_in_flight = True

    def completed(_granted) -> None:
        global _microphone_request_in_flight
        with _MICROPHONE_REQUEST_LOCK:
            _microphone_request_in_flight = False

    try:
        av_module.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            av_module.AVMediaTypeAudio,
            completed,
        )
    except Exception:
        completed(False)
        raise
    return True


def _exception_detail(
    exc: BaseException,
    *,
    private_values: tuple[str, ...] = (),
) -> str:
    """Return a bounded diagnostic message suitable for local JSON output."""
    detail = f"{type(exc).__name__}: {exc}"
    for value in private_values:
        if value:
            detail = detail.replace(value, Path(value).name)
    detail = re.sub(r"/Users/[^/\s:]+", "/Users/<redacted>", detail)
    return detail[:500]


def _known_value(value: object) -> dict[str, object]:
    return {
        "status": "known",
        "value": value,
        "reason_code": None,
        "detail": None,
    }


def _unknown_value(reason_code: str, detail: str | None = None) -> dict[str, object]:
    return {
        "status": "unknown",
        "value": None,
        "reason_code": reason_code,
        "detail": detail,
    }


def _platform_value(value: object, *, reason_code: str) -> dict[str, object]:
    text = str(value or "").strip()
    return _known_value(text) if text else _unknown_value(reason_code)


def _permission_observation(
    av_module,
    *,
    system: str,
) -> dict[str, object]:
    if system != "Darwin":
        return {
            "status": "unknown",
            "source": None,
            "request_attempted": False,
            "reason_code": "not_macos",
            "detail": None,
        }
    if av_module is None:
        return {
            "status": "unknown",
            "source": "AVCaptureDevice.authorizationStatusForMediaType",
            "request_attempted": False,
            "reason_code": "avfoundation_unavailable",
            "detail": None,
        }
    observed, error = _query_microphone_permission(av_module)
    if error is not None:
        return {
            "status": "unknown",
            "source": "AVCaptureDevice.authorizationStatusForMediaType",
            "request_attempted": False,
            "reason_code": "microphone_permission_query_failed",
            "detail": _exception_detail(error),
        }

    return {
        "status": observed,
        "source": "AVCaptureDevice.authorizationStatusForMediaType",
        "request_attempted": False,
        "reason_code": (
            "microphone_permission_value_unknown"
            if observed == "unknown"
            else None
        ),
        "detail": None,
    }


def _api_capability(
    av_module,
    *,
    required_selectors: tuple[str, ...],
) -> dict[str, object]:
    if av_module is None:
        return {
            "status": "unknown",
            "required_selectors": list(required_selectors),
            "missing_selectors": [],
            "reason_code": "avfoundation_unavailable",
            "detail": None,
        }

    input_node_class = getattr(av_module, "AVAudioInputNode", None)
    if input_node_class is None:
        return {
            "status": "unavailable",
            "required_selectors": list(required_selectors),
            "missing_selectors": list(required_selectors),
            "reason_code": "avaudio_input_node_unavailable",
            "detail": None,
        }

    missing = [
        selector
        for selector in required_selectors
        if not hasattr(input_node_class, selector)
    ]
    return {
        "status": "unavailable" if missing else "available",
        "required_selectors": list(required_selectors),
        "missing_selectors": missing,
        "reason_code": "required_selectors_missing" if missing else None,
        "detail": None,
    }


def _not_attempted(reason_code: str) -> dict[str, object]:
    return {
        "status": "not_attempted",
        "reason_code": reason_code,
        "detail": None,
    }


def _unknown_abi(
    expected: int,
    reason_code: str,
    detail: str | None = None,
) -> dict[str, object]:
    return {
        "status": "unknown",
        "expected": int(expected),
        "observed": None,
        "reason_code": reason_code,
        "detail": detail,
    }


def _probe_native_audio_library(
    *,
    path_resolver: Callable[[], Path | None] | None = None,
    library_loader: Callable[[str], object] = ctypes.CDLL,
    expected_abi_version: int | None = None,
    include_absolute_paths: bool = False,
) -> dict[str, object]:
    """Inspect the optional dylib path and ABI without creating audio I/O."""
    if path_resolver is None or expected_abi_version is None:
        from .native_audio_capture import (
            NATIVE_AUDIO_ABI_VERSION,
            native_audio_library_path,
        )

        if path_resolver is None:
            path_resolver = native_audio_library_path
        if expected_abi_version is None:
            expected_abi_version = NATIVE_AUDIO_ABI_VERSION

    expected = int(expected_abi_version)
    try:
        resolved = path_resolver()
    except Exception as exc:
        detail = _exception_detail(exc)
        return {
            "path": _unknown_value("library_path_probe_failed", detail),
            "load": _not_attempted("library_path_unknown"),
            "abi": _unknown_abi(expected, "library_path_unknown"),
        }

    if resolved is None:
        return {
            "path": {
                "status": "missing",
                "value": None,
                "reason_code": "library_not_found",
                "detail": None,
            },
            "load": _not_attempted("library_not_found"),
            "abi": _unknown_abi(expected, "library_not_found"),
        }

    resolved_path = Path(resolved)
    path = str(resolved_path)
    path_parts = resolved_path.parts
    if "Contents" in path_parts and "Frameworks" in path_parts:
        origin = "app_bundle"
    elif ".build" in path_parts and "native" in path_parts:
        origin = "development_build"
    else:
        origin = "filesystem"
    path_result = {
        "status": "found",
        "value": path if include_absolute_paths else resolved_path.name,
        "origin": origin,
        "reason_code": None,
        "detail": None,
    }
    try:
        library = library_loader(path)
    except Exception as exc:
        detail = _exception_detail(
            exc,
            private_values=() if include_absolute_paths else (path,),
        )
        return {
            "path": path_result,
            "load": {
                "status": "failed",
                "reason_code": "library_load_failed",
                "detail": detail,
            },
            "abi": _unknown_abi(expected, "library_load_failed", detail),
        }

    load_result = {
        "status": "loadable",
        "reason_code": None,
        "detail": None,
    }
    symbol = getattr(library, "vm_audio_abi_version", None)
    if symbol is None or not callable(symbol):
        return {
            "path": path_result,
            "load": load_result,
            "abi": _unknown_abi(expected, "abi_symbol_missing"),
        }

    try:
        symbol.argtypes = []
        symbol.restype = ctypes.c_uint32
        observed = int(symbol())
    except Exception as exc:
        return {
            "path": path_result,
            "load": load_result,
            "abi": _unknown_abi(
                expected,
                "abi_probe_failed",
                _exception_detail(exc),
            ),
        }

    compatible = observed == expected
    if compatible:
        from .native_audio_capture import NATIVE_AUDIO_ABI_V1_REQUIRED_SYMBOLS

        missing_symbols = sorted(
            name
            for name in NATIVE_AUDIO_ABI_V1_REQUIRED_SYMBOLS
            if not callable(getattr(library, name, None))
        )
        if missing_symbols:
            return {
                "path": path_result,
                "load": load_result,
                "abi": {
                    "status": "incomplete",
                    "expected": expected,
                    "observed": observed,
                    "reason_code": "required_symbols_missing",
                    "missing_symbols": missing_symbols,
                    "detail": None,
                },
            }
    return {
        "path": path_result,
        "load": load_result,
        "abi": {
            "status": "compatible" if compatible else "mismatch",
            "expected": expected,
            "observed": observed,
            "reason_code": None if compatible else "abi_version_mismatch",
            "missing_symbols": [],
            "detail": None,
        },
    }


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _default_planned_status() -> dict:
    # Import lazily. In particular, denied/not-determined probes never import
    # sounddevice through AudioRecorder and therefore cannot enumerate devices.
    # Dynamic import also keeps diagnostics out of AudioRecorder's static
    # dependency graph: AudioRecorder may publish this snapshot, so a normal
    # module import here would create an architecture cycle.
    recorder_module = importlib.import_module(
        f"{__package__}.audio_recorder"
    )
    return recorder_module.AudioRecorder.inspect_input_status()


def macos_audio_capability_snapshot(
    *,
    system: str | None = None,
    os_version: str | None = None,
    architecture: str | None = None,
    kernel_version: str | None = None,
    av_module: object = _AUTO,
    native_path_resolver: Callable[[], Path | None] | None = None,
    native_library_loader: Callable[[str], object] = ctypes.CDLL,
    planned_status_provider: Callable[[], dict] | None = None,
    include_absolute_paths: bool = False,
) -> dict[str, object]:
    """Return a stable, JSON-safe, zero-capture capability snapshot.

    The probe never calls ``requestAccess`` and never creates AVAudioEngine or
    a PortAudio stream. Device enumeration is attempted only after the current
    app identity is already authorized for microphone capture.
    """
    observed_system = str(system or platform.system() or "").strip()
    av_error: str | None = None
    resolved_av_module = av_module
    if av_module is _AUTO:
        if observed_system != "Darwin":
            resolved_av_module = None
        else:
            try:
                resolved_av_module = importlib.import_module("AVFoundation")
            except Exception as exc:
                resolved_av_module = None
                av_error = _exception_detail(exc)

    permission = _permission_observation(
        resolved_av_module,
        system=observed_system,
    )
    avfoundation = (
        {
            "status": "available",
            "reason_code": None,
            "detail": None,
        }
        if resolved_av_module is not None and observed_system == "Darwin"
        else {
            "status": "unknown",
            "reason_code": (
                "not_macos"
                if observed_system != "Darwin"
                else "avfoundation_import_failed"
            ),
            "detail": av_error,
        }
    )

    native = _probe_native_audio_library(
        path_resolver=native_path_resolver,
        library_loader=native_library_loader,
        include_absolute_paths=include_absolute_paths,
    )

    enumeration_attempted = False
    permission_status = str(permission["status"])
    if permission_status != "authorized":
        reason = (
            str(permission.get("reason_code"))
            if permission_status == "unknown" and permission.get("reason_code")
            else f"microphone_permission_{permission_status}"
        )
        planned_input = _unknown_value(reason)
    else:
        enumeration_attempted = True
        provider = planned_status_provider or _default_planned_status
        try:
            planned = provider()
            if not isinstance(planned, dict):
                raise TypeError("planned input status must be a mapping")
        except Exception as exc:
            planned_input = {
                "status": "error",
                "value": None,
                "reason_code": "planned_input_probe_failed",
                "detail": _exception_detail(exc),
            }
        else:
            planned_input = {
                "status": "available",
                "value": _json_safe(planned),
                "reason_code": None,
                "detail": None,
            }

    if os_version is None:
        os_version = platform.mac_ver()[0] if observed_system == "Darwin" else ""
    if architecture is None:
        architecture = platform.machine()
    if kernel_version is None:
        kernel_version = platform.version()

    return {
        "schema": AUDIO_CAPABILITY_SCHEMA,
        "schema_version": AUDIO_CAPABILITY_SCHEMA_VERSION,
        "probe": {
            "kind": "static",
            "microphone_permission_request_attempted": False,
            "audio_input_opened": False,
            "input_device_enumeration_attempted": enumeration_attempted,
        },
        "platform": {
            "system": _platform_value(
                observed_system,
                reason_code="system_name_unavailable",
            ),
            "os_version": _platform_value(
                os_version,
                reason_code=(
                    "not_macos"
                    if observed_system != "Darwin"
                    else "os_version_unavailable"
                ),
            ),
            "architecture": _platform_value(
                architecture,
                reason_code="architecture_unavailable",
            ),
            "kernel_version": _platform_value(
                kernel_version,
                reason_code="kernel_version_unavailable",
            ),
        },
        "microphone_permission": permission,
        "apple_audio": {
            "avfoundation": avfoundation,
            "voice_processing": _api_capability(
                resolved_av_module,
                required_selectors=_VOICE_PROCESSING_SELECTORS,
            ),
            "agc": _api_capability(
                resolved_av_module,
                required_selectors=_AGC_SELECTORS,
            ),
            "microphone_mode": _microphone_mode_observation(
                resolved_av_module,
                system=observed_system,
            ),
        },
        "native_audio_library": native,
        "planned_input": planned_input,
    }
