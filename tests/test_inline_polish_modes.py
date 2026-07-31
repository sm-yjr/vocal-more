"""Tests for mode behavior when ASR handles polish inline."""

import importlib
import threading
from types import SimpleNamespace
import yaml


def _build_realtime_long_context_harness(
    tmp_path,
    monkeypatch,
    *,
    context_personalization,
    config_payload=None,
    recorder_start_error=None,
):
    from vocal_more.config import Config, reload_config

    RealtimeLongMode = importlib.import_module(
        "vocal_more.modes.realtime_long"
    ).RealtimeLongMode

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    with open(config_path, "w") as f:
        yaml.dump(config_payload or {}, f)
    reload_config()

    observed = {
        "asr_start_kwargs": [],
        "asr_stop_calls": 0,
        "errors": [],
        "pasted": [],
        "polisher_contexts": [],
        "results": [],
    }

    class FakeASREngine:
        def start(self, **kwargs):
            observed["asr_start_kwargs"].append(kwargs)

        def stop(self, pcm_data=None):
            observed["asr_stop_calls"] += 1
            return "使用 get_config 读取配置"

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            if recorder_start_error is not None:
                raise recorder_start_error

        def stop(self):
            return b"\x01\x00" * 4000

    class FakePolisher:
        def set_context_instruction(self, instruction):
            observed["polisher_contexts"].append(instruction)

        def polish(self, text):
            return SimpleNamespace(polished_text=text)

    monkeypatch.setattr(
        "vocal_more.modes.realtime_long.ASREngine",
        lambda **kwargs: FakeASREngine(),
    )
    monkeypatch.setattr("vocal_more.modes.realtime_long.AudioRecorder", FakeRecorder)
    monkeypatch.setattr(
        "vocal_more.modes.realtime_long.KeyboardSimulator",
        lambda: SimpleNamespace(
            paste_text=lambda text: observed["pasted"].append(text)
        ),
    )

    mode = RealtimeLongMode(
        on_result=observed["results"].append,
        on_error=observed["errors"].append,
        text_polisher=FakePolisher(),
        context_personalization=context_personalization,
    )
    return mode, observed


def test_walkie_talkie_emits_explicit_lifecycle_states(tmp_path, monkeypatch):
    """Walkie-talkie should surface the richer lifecycle state sequence."""
    from vocal_more.config import Config, reload_config

    WalkieTalkieMode = importlib.import_module("vocal_more.modes.walkie_talkie").WalkieTalkieMode
    ModeState = importlib.import_module("vocal_more.modes.base_mode").ModeState

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"enable_polish": False, "auto_paste": False}, f)

    reload_config()

    class FakeASREngine:
        def start(self):
            return None

        def stop(self, pcm_data=None):
            return "明确的状态转换"

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            return None

        def stop(self):
            return b"\x01\x00" * 4000

    monkeypatch.setattr("vocal_more.modes.walkie_talkie.ASREngine", lambda **kwargs: FakeASREngine())
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr(
        "vocal_more.modes.walkie_talkie.KeyboardSimulator",
        lambda: SimpleNamespace(paste_text=lambda text: None),
    )

    states = []
    mode = WalkieTalkieMode(on_state_change=states.append)
    mode.on_hotkey_pressed()
    mode.on_hotkey_released()
    mode._processing_thread.join(timeout=2)

    assert states == [
        ModeState.STARTING,
        ModeState.RECORDING,
        ModeState.STOPPING,
        ModeState.PROCESSING,
        ModeState.IDLE,
    ]


def test_walkie_talkie_applies_and_records_private_app_context(tmp_path, monkeypatch):
    from vocal_more.config import Config, reload_config
    from vocal_more.domain.app_context import AppContext

    WalkieTalkieMode = importlib.import_module(
        "vocal_more.modes.walkie_talkie"
    ).WalkieTalkieMode

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))
    with open(config_path, "w") as f:
        yaml.dump({"enable_polish": True, "auto_paste": True}, f)
    reload_config()

    captured = {"asr_context": None, "polisher_contexts": [], "recorded": []}
    context = AppContext(
        category="development",
        bundle_id="com.microsoft.VSCode",
    )

    class FakeContextService:
        def capture(self):
            return context

        def instruction(self, _context):
            return "当前是开发场景。保护代码、命令、API 名、路径和英文标识符。"

        def record_success(self, used_context):
            captured["recorded"].append(used_context)

    class FakeASREngine:
        def start(self, *, context_instruction=""):
            captured["asr_context"] = context_instruction

        def stop(self, pcm_data=None):
            return "使用 get_config 读取配置"

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            pass

        def start(self):
            return None

        def stop(self):
            return b"\x01\x00" * 4000

    class FakePolisher:
        def set_context_instruction(self, instruction):
            captured["polisher_contexts"].append(instruction)

    monkeypatch.setattr(
        "vocal_more.modes.walkie_talkie.ASREngine",
        lambda **kwargs: FakeASREngine(),
    )
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr(
        "vocal_more.modes.walkie_talkie.KeyboardSimulator",
        lambda: SimpleNamespace(paste_text=lambda _text: None),
    )

    mode = WalkieTalkieMode(
        text_polisher=FakePolisher(),
        context_personalization=FakeContextService(),
    )
    mode.on_hotkey_pressed()
    mode.on_hotkey_released()
    mode._processing_thread.join(timeout=2)

    assert "当前是开发场景" in captured["asr_context"]
    assert captured["polisher_contexts"][0].startswith("当前是开发场景")
    assert captured["polisher_contexts"][-1] == ""
    assert captured["recorded"] == [context]


def test_default_realtime_long_applies_private_context_and_records_success(
    tmp_path, monkeypatch
):
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    recorded = []

    class TrackingRepository:
        def increment(self, context):
            recorded.append(context)

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=lambda: "com.microsoft.VSCode",
        repository=TrackingRepository(),
    )
    mode, observed = _build_realtime_long_context_harness(
        tmp_path,
        monkeypatch,
        context_personalization=service,
    )

    assert mode.config.default_mode == "realtime_long"

    mode.on_hotkey_pressed()
    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    assert observed["errors"] == []
    assert observed["pasted"] == ["使用 get_config 读取配置"]
    assert observed["results"] == ["使用 get_config 读取配置"]
    assert len(observed["asr_start_kwargs"]) == 1
    context_instruction = observed["asr_start_kwargs"][0]["context_instruction"]
    assert "当前是开发场景" in context_instruction
    assert "com.microsoft" not in context_instruction
    assert "VSCode" not in context_instruction
    assert observed["polisher_contexts"] == [context_instruction, ""]
    assert [context.category for context in recorded] == ["development"]
    assert mode._active_app_context is None
    mode.close()


def test_realtime_long_does_not_record_context_without_auto_paste(
    tmp_path, monkeypatch
):
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    recorded = []

    class TrackingRepository:
        def increment(self, context):
            recorded.append(context)

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=lambda: "com.apple.Notes",
        repository=TrackingRepository(),
    )
    mode, observed = _build_realtime_long_context_harness(
        tmp_path,
        monkeypatch,
        context_personalization=service,
        config_payload={"auto_paste": False, "enable_polish": False},
    )

    mode.on_hotkey_pressed()
    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    assert observed["results"] == ["使用 get_config 读取配置"]
    assert observed["pasted"] == []
    assert recorded == []
    assert observed["polisher_contexts"][-1] == ""
    assert mode._active_app_context is None
    mode.close()


def test_realtime_long_clears_context_when_microphone_start_fails(
    tmp_path, monkeypatch
):
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=lambda: "com.microsoft.VSCode",
        repository=None,
    )
    mode, observed = _build_realtime_long_context_harness(
        tmp_path,
        monkeypatch,
        context_personalization=service,
        recorder_start_error=RuntimeError("device busy"),
    )

    mode.on_hotkey_pressed()

    assert observed["asr_start_kwargs"] == []
    assert observed["polisher_contexts"][0].startswith("当前是开发场景")
    assert observed["polisher_contexts"][-1] == ""
    assert mode._active_app_context is None
    mode.close()


def test_realtime_long_cancel_clears_context_without_recording(
    tmp_path, monkeypatch
):
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    recorded = []

    class TrackingRepository:
        def increment(self, context):
            recorded.append(context)

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=lambda: "com.microsoft.VSCode",
        repository=TrackingRepository(),
    )
    mode, observed = _build_realtime_long_context_harness(
        tmp_path,
        monkeypatch,
        context_personalization=service,
    )

    mode.on_hotkey_pressed()
    mode.cancel()

    assert observed["pasted"] == []
    assert recorded == []
    assert observed["polisher_contexts"][-1] == ""
    assert mode._active_app_context is None
    mode.close()


def test_realtime_long_transcribes_when_app_provider_raises(tmp_path, monkeypatch):
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    def failing_provider():
        raise RuntimeError("workspace unavailable")

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=failing_provider,
        repository=None,
    )
    mode, observed = _build_realtime_long_context_harness(
        tmp_path,
        monkeypatch,
        context_personalization=service,
        config_payload={"enable_polish": False},
    )

    mode.on_hotkey_pressed()
    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    assert observed["asr_start_kwargs"] == [{}]
    assert observed["pasted"] == ["使用 get_config 读取配置"]
    assert observed["results"] == ["使用 get_config 读取配置"]
    assert observed["errors"] == []
    mode.close()


def test_realtime_long_transcribes_when_context_repository_raises(
    tmp_path, monkeypatch
):
    from vocal_more.application.context_personalization import (
        ContextPersonalizationService,
    )
    from vocal_more.domain.config_models import ContextPersonalizationConfig

    class FailingRepository:
        def increment(self, _context):
            raise OSError("profile is read-only")

    service = ContextPersonalizationService(
        config=ContextPersonalizationConfig(enabled=True),
        app_provider=lambda: "com.microsoft.VSCode",
        repository=FailingRepository(),
    )
    mode, observed = _build_realtime_long_context_harness(
        tmp_path,
        monkeypatch,
        context_personalization=service,
        config_payload={"enable_polish": False},
    )

    mode.on_hotkey_pressed()
    mode.on_hotkey_pressed()
    mode._processing_thread.join(timeout=2)

    assert observed["pasted"] == ["使用 get_config 读取配置"]
    assert observed["results"] == ["使用 get_config 读取配置"]
    assert observed["errors"] == []
    assert observed["polisher_contexts"][-1] == ""
    assert mode._active_app_context is None
    mode.close()


def test_walkie_talkie_start_failure_marks_failed_then_idle(tmp_path, monkeypatch):
    """Microphone startup failures should pass through FAILED before returning idle."""
    from vocal_more.config import Config, reload_config

    WalkieTalkieMode = importlib.import_module("vocal_more.modes.walkie_talkie").WalkieTalkieMode
    ModeState = importlib.import_module("vocal_more.modes.base_mode").ModeState

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"ui": {"language": "zh"}}, f)

    reload_config()

    class FakeASREngine:
        def start(self):
            return None

        def stop(self, pcm_data=None):
            return ""

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            raise RuntimeError("mic missing")

    monkeypatch.setattr("vocal_more.modes.walkie_talkie.ASREngine", lambda **kwargs: FakeASREngine())
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr(
        "vocal_more.modes.walkie_talkie.KeyboardSimulator",
        lambda: SimpleNamespace(paste_text=lambda text: None),
    )

    states = []
    errors = []
    mode = WalkieTalkieMode(on_state_change=states.append, on_error=errors.append)
    mode.on_hotkey_pressed()

    assert states == [
        ModeState.STARTING,
        ModeState.FAILED,
        ModeState.IDLE,
    ]
    assert errors == ["无法启动麦克风：mic missing"]


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


def test_walkie_talkie_reports_short_recordings_to_user(tmp_path, monkeypatch):
    """Too-short recordings should surface a user-facing error instead of failing silently."""
    from vocal_more.config import Config, reload_config

    WalkieTalkieMode = importlib.import_module("vocal_more.modes.walkie_talkie").WalkieTalkieMode
    ModeState = importlib.import_module("vocal_more.modes.base_mode").ModeState

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"ui": {"language": "zh"}}, f)

    reload_config()

    class FakeASREngine:
        def start(self):
            return None

        def stop(self, pcm_data=None):
            return ""

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            return None

        def stop(self):
            return b"\x01\x00" * 1000

    monkeypatch.setattr("vocal_more.modes.walkie_talkie.ASREngine", lambda **kwargs: FakeASREngine())
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr(
        "vocal_more.modes.walkie_talkie.KeyboardSimulator",
        lambda: SimpleNamespace(paste_text=lambda text: None),
    )

    errors = []
    mode = WalkieTalkieMode(on_error=errors.append)
    mode.on_hotkey_pressed()
    mode.on_hotkey_released()

    assert errors == ["录音太短了，请稍微多按一会儿热键。"]
    assert mode.state == ModeState.IDLE
    assert mode._processing_thread is None


def test_realtime_long_reports_microphone_start_failure(tmp_path, monkeypatch):
    """Audio device open failures should reach the user with context."""
    from vocal_more.config import Config, reload_config

    RealtimeLongMode = importlib.import_module("vocal_more.modes.realtime_long").RealtimeLongMode
    ModeState = importlib.import_module("vocal_more.modes.base_mode").ModeState

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"ui": {"language": "zh"}}, f)

    reload_config()

    class FakeASREngine:
        def __init__(self):
            self.start_calls = 0

        def start(self):
            self.start_calls += 1
            return None

        def stop(self, pcm_data=None):
            return ""

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            raise RuntimeError("device busy")

        def stop(self):
            return b""

    monkeypatch.setattr("vocal_more.modes.realtime_long.ASREngine", lambda **kwargs: FakeASREngine())
    monkeypatch.setattr("vocal_more.modes.realtime_long.AudioRecorder", FakeRecorder)
    monkeypatch.setattr(
        "vocal_more.modes.realtime_long.KeyboardSimulator",
        lambda: SimpleNamespace(paste_text=lambda text: None),
    )

    errors = []
    mode = RealtimeLongMode(on_error=errors.append)
    mode.on_hotkey_pressed()

    assert errors == ["无法启动麦克风：device busy"]
    assert mode.state == ModeState.IDLE
    assert mode._asr.start_calls == 0


def test_realtime_long_reports_device_change_after_recorder_recovery_fails(
    tmp_path, monkeypatch
):
    """Recoverable PortAudio failures should collapse into one device-change message."""
    from vocal_more.config import Config, reload_config
    from vocal_more.core.audio_recorder import AudioRecorderStartError

    RealtimeLongMode = importlib.import_module("vocal_more.modes.realtime_long").RealtimeLongMode
    ModeState = importlib.import_module("vocal_more.modes.base_mode").ModeState

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"ui": {"language": "zh"}}, f)

    reload_config()

    class FakeASREngine:
        def __init__(self):
            self.start_calls = 0

        def start(self):
            self.start_calls += 1
            return None

        def stop(self, pcm_data=None):
            return ""

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            raise AudioRecorderStartError(
                "PortAudio internal error -9986",
                device_change_detected=True,
            )

        def stop(self):
            return b""

    monkeypatch.setattr("vocal_more.modes.realtime_long.ASREngine", lambda **kwargs: FakeASREngine())
    monkeypatch.setattr("vocal_more.modes.realtime_long.AudioRecorder", FakeRecorder)
    monkeypatch.setattr(
        "vocal_more.modes.realtime_long.KeyboardSimulator",
        lambda: SimpleNamespace(paste_text=lambda text: None),
    )

    errors = []
    mode = RealtimeLongMode(on_error=errors.append)
    mode.on_hotkey_pressed()

    assert errors == ["麦克风设备似乎已变更，请重新选择输入设备或重新连接麦克风后再试。"]
    assert mode.state == ModeState.IDLE
    assert mode._asr.start_calls == 0


def test_walkie_talkie_reports_microphone_start_failure_without_starting_asr(
    tmp_path, monkeypatch
):
    """Hold-to-talk should not burn an ASR session when the microphone never opens."""
    from vocal_more.config import Config, reload_config

    WalkieTalkieMode = importlib.import_module("vocal_more.modes.walkie_talkie").WalkieTalkieMode
    ModeState = importlib.import_module("vocal_more.modes.base_mode").ModeState

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"ui": {"language": "zh"}}, f)

    reload_config()

    class FakeASREngine:
        def __init__(self):
            self.start_calls = 0

        def start(self):
            self.start_calls += 1
            return None

        def stop(self, pcm_data=None):
            return ""

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            raise RuntimeError("device busy")

        def stop(self):
            return b""

    monkeypatch.setattr("vocal_more.modes.walkie_talkie.ASREngine", lambda **kwargs: FakeASREngine())
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr(
        "vocal_more.modes.walkie_talkie.KeyboardSimulator",
        lambda: SimpleNamespace(paste_text=lambda text: None),
    )

    errors = []
    mode = WalkieTalkieMode(on_error=errors.append)
    mode.on_hotkey_pressed()

    assert errors == ["无法启动麦克风：device busy"]
    assert mode.state == ModeState.IDLE
    assert mode._asr.start_calls == 0


def test_walkie_talkie_emits_processing_stages_for_transcribe_and_polish(
    tmp_path, monkeypatch
):
    """The app should be able to reflect transcribing vs polishing in the capsule."""
    from vocal_more.config import Config, reload_config

    WalkieTalkieMode = importlib.import_module("vocal_more.modes.walkie_talkie").WalkieTalkieMode

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3-asr-flash-realtime-2026-02-10"},
            },
            f,
        )

    reload_config()

    class FakeASREngine:
        def start(self):
            return None

        def stop(self, pcm_data=None):
            return "嗯 这个方案已经确认了"

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            return None

        def stop(self):
            return b"\x01\x00" * 4000

    class FakePolisher:
        def polish(self, text):
            return SimpleNamespace(polished_text="这个方案已经确认了。")

    monkeypatch.setattr("vocal_more.modes.walkie_talkie.ASREngine", lambda **kwargs: FakeASREngine())
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr(
        "vocal_more.modes.walkie_talkie.KeyboardSimulator",
        lambda: SimpleNamespace(paste_text=lambda text: None),
    )

    stages = []
    mode = WalkieTalkieMode(
        on_processing_stage=stages.append,
        text_polisher=FakePolisher(),
    )
    mode.on_hotkey_pressed()
    mode.on_hotkey_released()
    mode._processing_thread.join(timeout=2)

    assert stages == ["transcribing", "polishing"]


def test_walkie_talkie_cancel_during_processing_suppresses_late_result_and_paste(
    tmp_path, monkeypatch
):
    """Cancelling while the finish workflow is running should drop late side effects."""
    from vocal_more.config import Config, reload_config

    WalkieTalkieMode = importlib.import_module("vocal_more.modes.walkie_talkie").WalkieTalkieMode
    ModeState = importlib.import_module("vocal_more.modes.base_mode").ModeState

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"enable_polish": False, "auto_paste": True}, f)

    reload_config()

    entered_stop = threading.Event()
    release_stop = threading.Event()
    pasted = []
    results = []
    states = []

    class FakeASREngine:
        def start(self):
            return None

        def stop(self, pcm_data=None):
            entered_stop.set()
            release_stop.wait(timeout=1.0)
            return "晚到的结果"

        def send_audio(self, _chunk):
            return None

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            return None

        def stop(self):
            return b"\x01\x00" * 4000

    class FakeKeyboard:
        def paste_text(self, text):
            pasted.append(text)

    monkeypatch.setattr("vocal_more.modes.walkie_talkie.ASREngine", lambda **kwargs: FakeASREngine())
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.AudioRecorder", FakeRecorder)
    monkeypatch.setattr("vocal_more.modes.walkie_talkie.KeyboardSimulator", lambda: FakeKeyboard())

    mode = WalkieTalkieMode(on_result=results.append, on_state_change=states.append)
    mode.on_hotkey_pressed()
    mode.on_hotkey_released()

    assert entered_stop.wait(timeout=1.0) is True
    mode.cancel()
    release_stop.set()
    mode._processing_thread.join(timeout=2)

    assert pasted == []
    assert results == []
    assert mode.state == ModeState.IDLE
    assert ModeState.CANCELLING in states


def test_realtime_long_cancel_during_microphone_start_cannot_revive_recording(
    tmp_path,
    monkeypatch,
):
    """A late native open must not enter RECORDING after quit/cancel wins."""
    from vocal_more.config import Config, reload_config

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config,
        "get_config_path",
        classmethod(lambda cls: config_path),
    )
    with open(config_path, "w") as output:
        yaml.dump({"ui": {"language": "zh"}}, output)
    reload_config()

    start_entered = threading.Event()
    allow_start = threading.Event()
    recorder_stopped = threading.Event()
    asr_instances = []

    class FakeRecorder:
        def __init__(self, on_audio_level=None, on_audio_chunk=None):
            self.on_audio_chunk = on_audio_chunk

        def start(self):
            start_entered.set()
            allow_start.wait(timeout=1.0)

        def stop(self):
            recorder_stopped.set()
            return b""

    class FakeASREngine:
        def __init__(self, **_kwargs):
            asr_instances.append(self)

        def start(self, **_kwargs):
            return None

        def stop(self, pcm_data=None):
            return ""

        def send_audio(self, _chunk):
            return None

    monkeypatch.setattr(
        "vocal_more.modes.realtime_long.AudioRecorder",
        FakeRecorder,
    )
    monkeypatch.setattr(
        "vocal_more.modes.realtime_long.ASREngine",
        FakeASREngine,
    )
    monkeypatch.setattr(
        "vocal_more.modes.realtime_long.KeyboardSimulator",
        lambda: SimpleNamespace(paste_text=lambda text: None),
    )

    module = importlib.import_module("vocal_more.modes.realtime_long")
    mode = module.RealtimeLongMode()
    start_thread = threading.Thread(target=mode.on_hotkey_pressed)
    start_thread.start()
    assert start_entered.wait(timeout=0.5)

    mode.cancel(reason="app_quit")
    allow_start.set()
    start_thread.join(timeout=0.5)

    assert recorder_stopped.is_set()
    assert start_thread.is_alive() is False
    assert asr_instances == []
    assert mode.state == importlib.import_module(
        "vocal_more.modes.base_mode"
    ).ModeState.IDLE
    mode.close()
