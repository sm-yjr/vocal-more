"""Tests for mode behavior when ASR handles polish inline."""

import importlib
from types import SimpleNamespace
import yaml


def test_walkie_talkie_skips_second_stage_polisher_for_omni(tmp_path, monkeypatch):
    """Omni inline polish should bypass the separate TextPolisher call in the mode."""
    from vocal_more.config import Config, reload_config
    ModeState = importlib.import_module("vocal_more.modes.base_mode").ModeState
    WalkieTalkieMode = importlib.import_module("vocal_more.modes.walkie_talkie").WalkieTalkieMode

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "auto_paste": True,
                "asr": {"model": "qwen3.5-omni-plus-realtime"},
            },
            f,
        )

    reload_config()

    class FakeASREngine:
        def __init__(self, on_partial_result=None, on_final_result=None, on_error=None):
            self.on_partial_result = on_partial_result
            self.on_error = on_error

        def start(self):
            return None

        def stop(self, pcm_data=None):
            return "这个方案已经确认了，可以开始执行。"

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_level = on_audio_level
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            return None

        def stop(self):
            return b"\x01\x00" * 4000

    pasted = []

    class FakeKeyboard:
        def paste_text(self, text):
            pasted.append(text)

    monkeypatch.setattr("vocal_more.modes.walkie_talkie.ASREngine", FakeASREngine)
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.KeyboardSimulator", lambda: FakeKeyboard())

    class FailIfCalledPolisher:
        def polish(self, text):
            raise AssertionError(f"polish() should not be called for inline Omni path: {text}")

    results = []
    errors = []
    mode = WalkieTalkieMode(
        on_result=results.append,
        on_error=errors.append,
        text_polisher=FailIfCalledPolisher(),
    )

    mode.on_hotkey_pressed()
    mode.on_hotkey_released()
    mode._processing_thread.join(timeout=2)

    assert errors == []
    assert pasted == ["这个方案已经确认了，可以开始执行。"]
    assert results == ["这个方案已经确认了，可以开始执行。"]
    assert mode.state == ModeState.IDLE


def test_walkie_talkie_uses_session_model_for_polish_decision(tmp_path, monkeypatch):
    """A model change after recording starts should not change the finish-path polish decision."""
    from vocal_more.config import Config, get_config, reload_config
    WalkieTalkieMode = importlib.import_module("vocal_more.modes.walkie_talkie").WalkieTalkieMode

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "auto_paste": True,
                "asr": {"model": "qwen3-asr-flash-realtime-2026-02-10"},
            },
            f,
        )

    reload_config()

    class FakeASREngine:
        def __init__(self, on_partial_result=None, on_final_result=None, on_error=None):
            self.on_partial_result = on_partial_result
            self.on_error = on_error

        def start(self):
            return None

        def stop(self, pcm_data=None):
            return "嗯 这个方案已经确认了"

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_level = on_audio_level
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            return None

        def stop(self):
            return b"\x01\x00" * 4000

    pasted = []

    class FakeKeyboard:
        def paste_text(self, text):
            pasted.append(text)

    monkeypatch.setattr("vocal_more.modes.walkie_talkie.ASREngine", FakeASREngine)
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.KeyboardSimulator", lambda: FakeKeyboard())

    class TrackingPolisher:
        def __init__(self):
            self.calls = []

        def polish(self, text):
            self.calls.append(text)
            return SimpleNamespace(polished_text="这个方案已经确认了，可以开始执行。")

    polisher = TrackingPolisher()
    results = []
    errors = []
    mode = WalkieTalkieMode(
        on_result=results.append,
        on_error=errors.append,
        text_polisher=polisher,
    )

    mode.on_hotkey_pressed()
    mode.on_hotkey_released()

    get_config().asr.model = "qwen3.5-omni-plus-realtime"
    mode._processing_thread.join(timeout=2)

    assert errors == []
    assert polisher.calls == ["嗯 这个方案已经确认了"]
    assert pasted == ["这个方案已经确认了，可以开始执行。"]
    assert results == ["这个方案已经确认了，可以开始执行。"]


def test_walkie_talkie_streams_live_partials_without_reemitting_final_text(
    tmp_path, monkeypatch
):
    """Live partials should be forwarded during recording, without a duplicate stop-time partial."""
    from vocal_more.config import Config, reload_config

    WalkieTalkieMode = importlib.import_module("vocal_more.modes.walkie_talkie").WalkieTalkieMode

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"enable_polish": False}, f)

    reload_config()

    partials = []

    class FakeASREngine:
        def __init__(self, on_partial_result=None, on_final_result=None, on_error=None):
            self.on_partial_result = on_partial_result

        def start(self):
            return None

        def stop(self, pcm_data=None):
            return "最终文本"

        def send_audio(self, _chunk):
            if self.on_partial_result:
                self.on_partial_result(SimpleNamespace(text="实时片段", is_final=False))

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            return None

        def stop(self):
            return b"\x01\x00" * 4000

    monkeypatch.setattr("vocal_more.modes.walkie_talkie.ASREngine", FakeASREngine)
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.KeyboardSimulator", lambda: SimpleNamespace(paste_text=lambda text: None))

    mode = WalkieTalkieMode(on_partial_result=partials.append)
    mode.on_hotkey_pressed()
    mode._on_audio_chunk(b"\x01\x00")
    mode.on_hotkey_released()
    mode._processing_thread.join(timeout=2)

    assert partials == ["实时片段"]
