"""Tests for startup-time lazy resource ownership."""

import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from vocal_more.application.lazy_resource import LazyResource, initialized_resource
from vocal_more.ui.lazy_settings_window import LazySettingsWindow


def test_package_facades_do_not_eagerly_import_heavy_runtime_modules():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import vocal_more.core; import vocal_more.modes; "
                "assert 'vocal_more.core.asr_engine' not in sys.modules; "
                "assert 'vocal_more.modes.meeting' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS menu bar app only")
def test_menu_app_construction_defers_heavy_runtime_until_status_item_exists():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from vocal_more.app import VocalMoreApp; "
                "app = VocalMoreApp(); "
                "assert not app._dependencies_ready; "
                "assert 'dashscope' not in sys.modules; "
                "assert 'vocal_more.core.audio_recorder' not in sys.modules; "
                "assert 'vocal_more.modes.walkie_talkie' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr


def test_lazy_resource_creates_once_and_inspection_does_not_initialize():
    resource = MagicMock()
    factory = MagicMock(return_value=resource)
    lazy = LazyResource(factory)

    assert initialized_resource(lazy) is None
    assert lazy.is_initialized is False
    factory.assert_not_called()

    lazy.start()
    lazy.stop()

    factory.assert_called_once_with()
    resource.start.assert_called_once_with()
    resource.stop.assert_called_once_with()


def test_lazy_resource_close_does_not_create_unused_resource():
    factory = MagicMock()
    lazy = LazyResource(factory)

    lazy.close()

    factory.assert_not_called()


def test_settings_window_is_created_only_when_first_shown():
    window = MagicMock()
    factory = MagicMock(return_value=window)
    lazy = LazySettingsWindow(factory, on_set_config=MagicMock())

    lazy.set_interface_language("zh", update_frontend=False)
    lazy.update_environment_checks([{"key": "api_key"}])
    lazy.close()

    factory.assert_not_called()
    assert lazy.is_visible() is False

    lazy.show(config={"ui": {"language": "zh"}}, asr_models=[], llm_models=[],
              devices=[], dictionary=[])

    factory.assert_called_once()
    window.set_interface_language.assert_called_once_with(
        "zh",
        update_frontend=False,
    )
    window.show.assert_called_once()


def test_realtime_modes_leave_asr_resources_uninitialized_until_first_use(
    monkeypatch,
):
    created = []

    class FakeASR:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def start(self):
            return None

        def close(self):
            return None

    class FakeRecorder:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    modes = []
    for module_name, class_name in (
        ("vocal_more.modes.walkie_talkie", "WalkieTalkieMode"),
        ("vocal_more.modes.realtime_long", "RealtimeLongMode"),
    ):
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, "ASREngine", FakeASR)
        monkeypatch.setattr(module, "AudioRecorder", FakeRecorder)
        monkeypatch.setattr(
            module,
            "KeyboardSimulator",
            lambda: SimpleNamespace(),
        )
        modes.append(getattr(module, class_name)())

    assert created == []
    assert all(not mode._asr.is_initialized for mode in modes)

    modes[0]._asr.start()

    assert len(created) == 1
    assert modes[0]._asr.is_initialized is True
    assert modes[1]._asr.is_initialized is False

    for mode in modes:
        mode.close()
