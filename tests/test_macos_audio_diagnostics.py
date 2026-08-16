"""Tests for static, privacy-safe macOS audio diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("native_value", "expected"),
    [
        (0, "not_determined"),
        (1, "restricted"),
        (2, "denied"),
        (3, "authorized"),
        (99, "unknown"),
    ],
)
def test_microphone_permission_query_never_requests_access(native_value, expected):
    from vocal_more.core.macos_audio_diagnostics import (
        _microphone_permission_status,
    )

    calls = []

    class CaptureDevice:
        @staticmethod
        def authorizationStatusForMediaType_(media_type):
            calls.append(("status", media_type))
            return native_value

        @staticmethod
        def requestAccessForMediaType_completionHandler_(*_args):
            calls.append(("request",))

    av = type(
        "AV",
        (),
        {
            "AVCaptureDevice": CaptureDevice,
            "AVMediaTypeAudio": "audio",
            "AVAuthorizationStatusNotDetermined": 0,
            "AVAuthorizationStatusRestricted": 1,
            "AVAuthorizationStatusDenied": 2,
            "AVAuthorizationStatusAuthorized": 3,
        },
    )

    assert _microphone_permission_status(av) == expected
    assert calls == [("status", "audio")]


def test_non_macos_permission_is_not_misreported_as_denied():
    from vocal_more.core.macos_audio_diagnostics import (
        microphone_permission_status,
    )

    assert microphone_permission_status(system="Linux") == "unknown"


def test_explicit_microphone_request_is_non_blocking_and_only_requests_once():
    from vocal_more.core.macos_audio_diagnostics import (
        request_microphone_access,
    )

    callbacks = []
    calls = []

    class CaptureDevice:
        @staticmethod
        def requestAccessForMediaType_completionHandler_(media_type, callback):
            calls.append(media_type)
            callbacks.append(callback)

    av = SimpleNamespace(
        AVCaptureDevice=CaptureDevice,
        AVMediaTypeAudio="audio",
    )

    started = request_microphone_access(system="Darwin", av_module=av)
    coalesced = request_microphone_access(system="Darwin", av_module=av)

    assert started is True
    assert coalesced is True
    assert calls == ["audio"]
    assert len(callbacks) == 1
    # The wrapper must not wait for the user-facing TCC prompt to resolve.
    callbacks[0](True)


def test_non_macos_microphone_request_never_touches_avfoundation():
    from vocal_more.core.macos_audio_diagnostics import (
        request_microphone_access,
    )

    assert request_microphone_access(
        system="Linux",
        av_module=pytest.fail,
    ) is False


def test_permission_query_failure_is_a_structured_unknown_and_skips_enumeration():
    from vocal_more.core.macos_audio_diagnostics import (
        macos_audio_capability_snapshot,
    )

    class FailingCaptureDevice:
        @staticmethod
        def authorizationStatusForMediaType_(_media_type):
            raise RuntimeError("TCC bridge unavailable")

    av = SimpleNamespace(
        AVCaptureDevice=FailingCaptureDevice,
        AVAudioInputNode=type("InputNode", (), {}),
        AVMediaTypeAudio="audio",
    )
    snapshot = macos_audio_capability_snapshot(
        system="Darwin",
        av_module=av,
        native_path_resolver=lambda: None,
        planned_status_provider=lambda: pytest.fail("must not enumerate"),
    )

    permission = snapshot["microphone_permission"]
    assert permission["status"] == "unknown"
    assert permission["reason_code"] == "microphone_permission_query_failed"
    assert "RuntimeError" in permission["detail"]
    assert snapshot["probe"]["input_device_enumeration_attempted"] is False
    assert snapshot["planned_input"]["reason_code"] == (
        "microphone_permission_query_failed"
    )


def _fake_avfoundation(
    *,
    permission: int = 3,
    missing: set[str] | None = None,
    preferred_mode: int = 0,
    active_mode: int = 0,
):
    missing = missing or set()
    calls: list[tuple] = []

    class CaptureDevice:
        @staticmethod
        def authorizationStatusForMediaType_(media_type):
            calls.append(("authorization_status", media_type))
            return permission

        @staticmethod
        def requestAccessForMediaType_completionHandler_(*_args):
            raise AssertionError("a static capability probe must never request access")

        @staticmethod
        def preferredMicrophoneMode():
            calls.append(("preferred_microphone_mode",))
            return preferred_mode

        @staticmethod
        def activeMicrophoneMode():
            calls.append(("active_microphone_mode",))
            return active_mode

    selectors = {
        "setVoiceProcessingEnabled_error_": object(),
        "isVoiceProcessingEnabled": object(),
        "setVoiceProcessingAGCEnabled_": object(),
        "isVoiceProcessingAGCEnabled": object(),
    }
    input_node = type(
        "InputNode",
        (),
        {key: value for key, value in selectors.items() if key not in missing},
    )
    module = SimpleNamespace(
        AVCaptureDevice=CaptureDevice,
        AVAudioInputNode=input_node,
        AVMediaTypeAudio="audio",
        AVAuthorizationStatusNotDetermined=0,
        AVAuthorizationStatusRestricted=1,
        AVAuthorizationStatusDenied=2,
        AVAuthorizationStatusAuthorized=3,
        AVCaptureMicrophoneModeStandard=0,
        AVCaptureMicrophoneModeWideSpectrum=1,
        AVCaptureMicrophoneModeVoiceIsolation=2,
    )
    return module, calls


def test_denied_snapshot_is_static_and_does_not_enumerate_devices():
    from vocal_more.core.macos_audio_diagnostics import (
        AUDIO_CAPABILITY_SCHEMA,
        AUDIO_CAPABILITY_SCHEMA_VERSION,
        macos_audio_capability_snapshot,
    )

    av, calls = _fake_avfoundation(permission=2)

    def forbidden_planned_status():
        raise AssertionError("denied permission must prevent device enumeration")

    snapshot = macos_audio_capability_snapshot(
        system="Darwin",
        os_version="15.5",
        architecture="arm64",
        av_module=av,
        native_path_resolver=lambda: None,
        planned_status_provider=forbidden_planned_status,
    )

    assert snapshot["schema"] == AUDIO_CAPABILITY_SCHEMA
    assert snapshot["schema_version"] == AUDIO_CAPABILITY_SCHEMA_VERSION == 1
    assert snapshot["probe"] == {
        "kind": "static",
        "microphone_permission_request_attempted": False,
        "audio_input_opened": False,
        "input_device_enumeration_attempted": False,
    }
    assert snapshot["microphone_permission"]["status"] == "denied"
    assert snapshot["apple_audio"]["voice_processing"]["status"] == "available"
    assert snapshot["apple_audio"]["agc"]["status"] == "available"
    assert snapshot["apple_audio"]["microphone_mode"] == {
        "status": "available",
        "preferred": "standard",
        "active": "standard",
        "preferred_differs_from_active": False,
        "reason_code": None,
        "detail": None,
    }
    assert snapshot["planned_input"] == {
        "status": "unknown",
        "value": None,
        "reason_code": "microphone_permission_denied",
        "detail": None,
    }
    assert calls == [
        ("authorization_status", "audio"),
        ("preferred_microphone_mode",),
        ("active_microphone_mode",),
    ]


def test_authorized_snapshot_calls_only_the_injected_planned_status_probe():
    from vocal_more.core.macos_audio_diagnostics import (
        macos_audio_capability_snapshot,
    )

    av, calls = _fake_avfoundation(permission=3)
    planned_calls: list[str] = []
    planned = {
        "phase": "planned",
        "processing_mode": "macos_voice_processing",
        "gain_control": "apple_agc",
    }

    snapshot = macos_audio_capability_snapshot(
        system="Darwin",
        os_version="15.5",
        architecture="arm64",
        av_module=av,
        native_path_resolver=lambda: None,
        planned_status_provider=lambda: planned_calls.append("planned") or planned,
    )

    assert snapshot["probe"]["input_device_enumeration_attempted"] is True
    assert snapshot["probe"]["audio_input_opened"] is False
    assert snapshot["planned_input"] == {
        "status": "available",
        "value": planned,
        "reason_code": None,
        "detail": None,
    }
    assert planned_calls == ["planned"]
    assert calls == [
        ("authorization_status", "audio"),
        ("preferred_microphone_mode",),
        ("active_microphone_mode",),
    ]


def test_microphone_mode_reports_route_override_without_opening_audio():
    from vocal_more.core.macos_audio_diagnostics import (
        macos_microphone_modes,
    )

    av, calls = _fake_avfoundation(
        permission=2,
        preferred_mode=2,
        active_mode=0,
    )

    assert macos_microphone_modes(av_module=av, system="Darwin") == (
        "voice_isolation",
        "standard",
    )
    assert calls == [
        ("preferred_microphone_mode",),
        ("active_microphone_mode",),
    ]


def test_static_api_snapshot_reports_missing_selectors_without_building_an_engine():
    from vocal_more.core.macos_audio_diagnostics import (
        macos_audio_capability_snapshot,
    )

    av, _calls = _fake_avfoundation(
        permission=0,
        missing={"isVoiceProcessingEnabled", "isVoiceProcessingAGCEnabled"},
    )
    snapshot = macos_audio_capability_snapshot(
        system="Darwin",
        av_module=av,
        native_path_resolver=lambda: None,
        planned_status_provider=lambda: pytest.fail("must not enumerate"),
    )

    voice = snapshot["apple_audio"]["voice_processing"]
    agc = snapshot["apple_audio"]["agc"]
    assert voice["status"] == "unavailable"
    assert voice["reason_code"] == "required_selectors_missing"
    assert voice["missing_selectors"] == ["isVoiceProcessingEnabled"]
    assert agc["status"] == "unavailable"
    assert agc["missing_selectors"] == ["isVoiceProcessingAGCEnabled"]
    assert snapshot["planned_input"]["reason_code"] == (
        "microphone_permission_not_determined"
    )


class _ABISymbol:
    def __init__(self, value: int):
        self.value = value
        self.argtypes = object()
        self.restype = object()

    def __call__(self):
        return self.value


def _fake_native_library(observed: int = 1, *, missing: set[str] | None = None):
    from vocal_more.core.native_audio_capture import (
        NATIVE_AUDIO_ABI_V2_REQUIRED_SYMBOLS,
    )

    missing = missing or set()
    values = {
        name: (lambda: None)
        for name in NATIVE_AUDIO_ABI_V2_REQUIRED_SYMBOLS
        if name not in missing
    }
    values["vm_audio_abi_version"] = _ABISymbol(observed)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("observed", "expected_status", "reason_code"),
    [
        (1, "compatible", None),
        (7, "mismatch", "abi_version_mismatch"),
    ],
)
def test_native_library_probe_reports_path_load_and_abi(
    tmp_path,
    observed,
    expected_status,
    reason_code,
):
    from vocal_more.core.macos_audio_diagnostics import (
        _probe_native_audio_library,
    )

    path = tmp_path / "libvocal_more_audio.dylib"
    library = _fake_native_library(observed)

    result = _probe_native_audio_library(
        path_resolver=lambda: path,
        library_loader=lambda value: library
        if value == str(path)
        else pytest.fail("unexpected library path"),
        expected_abi_version=1,
    )

    assert result["path"] == {
        "status": "found",
        "value": "libvocal_more_audio.dylib",
        "origin": "filesystem",
        "reason_code": None,
        "detail": None,
    }
    assert result["load"]["status"] == "loadable"
    assert result["abi"]["status"] == expected_status
    assert result["abi"]["expected"] == 1
    assert result["abi"]["observed"] == observed
    assert result["abi"]["reason_code"] == reason_code


def test_native_library_probe_rejects_an_incomplete_matching_abi(tmp_path):
    from vocal_more.core.macos_audio_diagnostics import _probe_native_audio_library

    path = tmp_path / "libvocal_more_audio.dylib"
    result = _probe_native_audio_library(
        path_resolver=lambda: path,
        library_loader=lambda _value: _fake_native_library(
            missing={"vm_audio_read", "vm_audio_stop"}
        ),
        expected_abi_version=1,
    )

    assert result["abi"]["status"] == "incomplete"
    assert result["abi"]["reason_code"] == "required_symbols_missing"
    assert result["abi"]["missing_symbols"] == ["vm_audio_read", "vm_audio_stop"]


def test_privacy_safe_snapshot_redacts_the_native_library_absolute_path():
    from vocal_more.core.macos_audio_diagnostics import macos_audio_capability_snapshot

    av, _calls = _fake_avfoundation(permission=2)
    private_path = Path(
        "/Users/alice/Private Project/.build/native/libvocal_more_audio.dylib"
    )
    snapshot = macos_audio_capability_snapshot(
        system="Darwin",
        av_module=av,
        native_path_resolver=lambda: private_path,
        native_library_loader=lambda _value: _fake_native_library(),
    )

    encoded = json.dumps(snapshot)
    assert "/Users/alice" not in encoded
    assert snapshot["native_audio_library"]["path"]["origin"] == (
        "development_build"
    )


def test_verbose_native_probe_may_include_the_resolved_path(tmp_path):
    from vocal_more.core.macos_audio_diagnostics import _probe_native_audio_library

    path = tmp_path / "libvocal_more_audio.dylib"
    result = _probe_native_audio_library(
        path_resolver=lambda: path,
        library_loader=lambda _value: _fake_native_library(),
        expected_abi_version=1,
        include_absolute_paths=True,
    )

    assert result["path"]["value"] == str(path)


def test_native_library_missing_is_a_known_state_not_an_exception():
    from vocal_more.core.macos_audio_diagnostics import (
        _probe_native_audio_library,
    )

    result = _probe_native_audio_library(
        path_resolver=lambda: None,
        library_loader=lambda _path: pytest.fail("missing paths must not be loaded"),
        expected_abi_version=1,
    )

    assert result["path"]["status"] == "missing"
    assert result["load"]["status"] == "not_attempted"
    assert result["abi"] == {
        "status": "unknown",
        "expected": 1,
        "observed": None,
        "reason_code": "library_not_found",
        "detail": None,
    }
    json.dumps(result)


def test_json_cli_prints_exactly_one_snapshot(capsys):
    from scripts.probe_macos_audio_capabilities import main

    snapshot = {
        "schema": "vocal_more.macos_audio_capabilities",
        "schema_version": 1,
        "probe": {"kind": "static"},
    }

    assert main([], snapshot_provider=lambda: snapshot) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == snapshot
    assert captured.out.endswith("\n")
    assert captured.err == ""


def test_json_cli_compact_mode_is_single_line(capsys):
    from scripts.probe_macos_audio_capabilities import main

    snapshot = {"schema": "test", "schema_version": 1}

    assert main(["--compact"], snapshot_provider=lambda: snapshot) == 0
    output = capsys.readouterr().out
    assert output.count("\n") == 1
    assert json.loads(output) == snapshot


def test_json_cli_keeps_probe_messages_out_of_machine_readable_stdout(capsys):
    from scripts.probe_macos_audio_capabilities import main

    snapshot = {"schema": "test", "schema_version": 1}

    def noisy_provider():
        print("device probe note")
        return snapshot

    assert main([], snapshot_provider=noisy_provider) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == snapshot
    assert captured.err == "device probe note\n"
