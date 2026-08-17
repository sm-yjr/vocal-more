from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace


class _FakeText:
    def __init__(self, value: str) -> None:
        self.value = value
        self.reads = 0

    def get_text(self, _start, _end):
        self.reads += 1
        return self.value

    def get_caret_offset(self):
        return 2


class _FakeElement:
    def __init__(self, *, role="ROLE_TEXT", path="/editor", value="hello"):
        self.role = role
        self.path = path
        self.text = _FakeText(value)
        self.defunct = False

    def queryText(self):
        return self.text

    def get_role(self):
        return self.role

    def get_name(self):
        return "Editor"

    def get_process_id(self):
        return 42

    def get_object_path(self):
        return self.path


class _FakeDesktop:
    def __init__(self, element):
        self.element = element

    def get_focus(self):
        return self.element


class _FakeRegistry:
    desktop = None

    @classmethod
    def getDesktop(cls, _index):
        return cls.desktop


class _FakeAtspi:
    Registry = _FakeRegistry


def test_linux_app_context_provider_clears_and_normalizes_ids():
    from vocal_more.infrastructure.linux_app_context import LinuxAppContextProvider

    provider = LinuxAppContextProvider()
    assert provider.update(" org.gnome.Terminal ") == "org.gnome.Terminal"
    assert provider.current() == "org.gnome.Terminal"
    assert provider.update("bad\x00id") == ""
    assert provider() == ""


def test_linux_context_personalization_uses_shell_reported_id(monkeypatch):
    from vocal_more.application import context_personalization
    from vocal_more.domain.config_models import ContextPersonalizationConfig
    from vocal_more.infrastructure.linux_app_context import (
        set_current_desktop_app_id,
    )

    monkeypatch.setattr(context_personalization.platform, "system", lambda: "Linux")
    set_current_desktop_app_id("org.gnome.Terminal")
    service = context_personalization.ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=context_personalization._platform_app_provider(),
        repository=None,
    )
    context = service.capture()
    assert context is not None
    assert context.category == "development"


def test_linux_atspi_provider_fails_closed_for_missing_text_and_password():
    from vocal_more.core.linux_accessibility_text import LinuxFocusedTextProvider

    missing = SimpleNamespace(
        get_role=lambda: "ROLE_TEXT",
        get_name=lambda: "Editor",
        get_process_id=lambda: 42,
    )
    _FakeRegistry.desktop = _FakeDesktop(missing)
    provider = LinuxFocusedTextProvider(backend=_FakeAtspi)
    assert provider.capture_focused() is None

    password = _FakeElement(role="ROLE_PASSWORD_TEXT", value="must-not-read")
    _FakeRegistry.desktop = _FakeDesktop(password)
    snapshot = provider.capture_focused()
    assert snapshot is not None
    assert snapshot.is_secure is True
    assert snapshot.value == ""
    assert password.text.reads == 0


def test_linux_atspi_provider_rejects_defunct_retained_target():
    from vocal_more.core.linux_accessibility_text import LinuxFocusedTextProvider

    element = _FakeElement()
    _FakeRegistry.desktop = _FakeDesktop(element)
    provider = LinuxFocusedTextProvider(backend=_FakeAtspi)
    original = provider.capture_focused()
    assert original is not None
    element.defunct = True
    assert provider.capture_target(original) is None


def test_system_flac_codec_verifies_round_trip(monkeypatch, tmp_path):
    from vocal_more.core import flac_codec

    source = tmp_path / "source.wav"
    encoded = tmp_path / "source.flac"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x01\x00" * 20)

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **_kwargs):
        destination = Path(command[command.index("--output-name") + 1])
        if "--decode" in command:
            destination.write_bytes(source.read_bytes())
        else:
            destination.write_bytes(source.read_bytes())
        return _Result()

    monkeypatch.setattr(flac_codec.subprocess, "run", fake_run)
    codec = flac_codec.SystemFlacAudioCodec(executable="flac")
    codec.encode_wav_to_flac(source, encoded)
    assert codec.verify_round_trip(source, encoded) is True


def test_linux_recording_store_factory_uses_linux_codec(tmp_path):
    from vocal_more.core.flac_codec import SystemFlacAudioCodec
    from vocal_more.infrastructure.linux_recording_store import (
        build_linux_recording_store,
    )

    store = build_linux_recording_store(tmp_path / "recordings", auto_compact=False)
    try:
        assert store._dir == tmp_path / "recordings"
        assert isinstance(store._audio_codec, SystemFlacAudioCodec)
    finally:
        store.close()
