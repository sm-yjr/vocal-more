"""Tests for ASR engine backends."""

import importlib
from types import SimpleNamespace

import yaml


def test_realtime_ws_batch_uses_manual_commit_and_corpus(tmp_path, monkeypatch):
    """The websocket backend should disable turn detection and commit manually."""
    from vocal_more.config import Config, reload_config
    from vocal_more.dictionary import reload_dictionary
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    dict_path = tmp_path / "dictionary.yaml"

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "asr": {
                    "backend": "realtime_ws",
                    "language": "zh",
                    "use_dictionary_corpus": True,
                    "extra_corpus_terms": ["DashScope"],
                }
            },
            f,
        )

    with open(dict_path, "w") as f:
        yaml.dump(
            {"entries": [{"term": "阿里云百炼", "aliases": ["阿里云白练"]}]},
            f,
            allow_unicode=True,
        )

    reload_config()
    reload_dictionary()

    captured = {}

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["model"] = model
            captured["url"] = url
            captured["callback"] = callback
            captured["append_count"] = 0
            captured["committed"] = False
            captured["ended"] = False
            captured["closed"] = False

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["update_kwargs"] = kwargs
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            captured["append_count"] += 1

        def commit(self):
            captured["committed"] = True
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "阿里云百炼",
                }
            )

        def end_session(self, timeout=5):
            captured["ended"] = True
            captured["callback"].on_event({"type": "session.finished"})

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    transcription_params = captured["update_kwargs"]["transcription_params"]
    assert captured["update_kwargs"]["enable_turn_detection"] is False
    assert captured["committed"] is True
    assert captured["ended"] is True
    assert captured["closed"] is True
    assert transcription_params.language == "zh"
    assert transcription_params.corpus_text == "阿里云百炼\n\nDashScope"
    assert text == "阿里云百炼"


def test_short_file_backend_passes_audio_and_asr_options(tmp_path, monkeypatch):
    """Short-file backend should call qwen3-asr-flash with ITN enabled."""
    from vocal_more.config import Config, reload_config
    from vocal_more.dictionary import reload_dictionary
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    dict_path = tmp_path / "dictionary.yaml"

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"backend": "short_file", "model": "qwen3-asr-flash", "language": "zh"}}, f)

    with open(dict_path, "w") as f:
        yaml.dump(
            {"entries": [{"term": "Vocal More", "aliases": ["vocal mall"]}]},
            f,
            allow_unicode=True,
        )

    reload_config()
    reload_dictionary()

    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            output=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=[{"text": "Vocal More"}])
                    )
                ]
            ),
        )

    monkeypatch.setattr(asr_engine.MultiModalConversation, "call", fake_call)

    engine = asr_engine.BatchASREngine()
    monkeypatch.setattr(engine, "_supports_short_file", lambda _audio: True)

    text = engine.transcribe(b"\x01\x00" * 4000)

    assert captured["model"] == "qwen3-asr-flash"
    assert captured["asr_options"] == {"language": "zh", "enable_itn": True}
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["content"][0]["audio"].endswith(".wav")
    assert text == "Vocal More"


def test_short_file_backend_falls_back_when_audio_exceeds_limits(
    tmp_path, monkeypatch
):
    """Oversized audio should fall back to realtime websocket mode."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"backend": "short_file", "model": "qwen3-asr-flash"}}, f)

    reload_config()

    engine = asr_engine.BatchASREngine()
    monkeypatch.setattr(engine, "_supports_short_file", lambda _audio: False)
    monkeypatch.setattr(engine, "_transcribe_realtime_ws", lambda _audio, **kw: "fallback")
    monkeypatch.setattr(engine, "_transcribe_short_file", lambda _audio, **kw: "short")

    assert engine.transcribe(b"\x01\x00" * 5000000) == "fallback"


def test_omni_realtime_uses_transcription_model(tmp_path, monkeypatch):
    """Omni model should use input_audio_transcription_model instead of transcription_params."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    dict_path = tmp_path / "dictionary.yaml"

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {"asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"}},
            f,
        )

    with open(dict_path, "w") as f:
        yaml.dump({"entries": []}, f)

    reload_config()

    captured = {}

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["model"] = model
            captured["callback"] = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["update_kwargs"] = kwargs
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            pass

        def commit(self):
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "你好世界",
                }
            )

        def end_session(self, timeout=5):
            captured["callback"].on_event({"type": "session.finished"})

        def close(self):
            pass

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert captured["model"] == "qwen3.5-omni-plus-realtime"
    assert captured["update_kwargs"]["input_audio_transcription_model"] == "gummy-realtime-v1"
    assert "transcription_params" not in captured["update_kwargs"]
    assert captured["update_kwargs"]["enable_turn_detection"] is False
    assert text == "你好世界"


def test_standard_asr_still_uses_transcription_params(tmp_path, monkeypatch):
    """Default ASR model should use transcription_params, not input_audio_transcription_model."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    dict_path = tmp_path / "dictionary.yaml"

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"language": "zh"}}, f)

    with open(dict_path, "w") as f:
        yaml.dump({"entries": []}, f)

    reload_config()

    captured = {}

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["model"] = model
            captured["callback"] = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["update_kwargs"] = kwargs
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            pass

        def commit(self):
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "hello",
                }
            )

        def end_session(self, timeout=5):
            captured["callback"].on_event({"type": "session.finished"})

        def close(self):
            pass

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert captured["model"] == "qwen3-asr-flash-realtime-2026-02-10"
    assert "transcription_params" in captured["update_kwargs"]
    assert "input_audio_transcription_model" not in captured["update_kwargs"]
    assert captured["update_kwargs"]["transcription_params"].language == "zh"
    assert text == "hello"


def test_omni_inline_polish_uses_response_text_output(tmp_path, monkeypatch):
    """Omni inline polish should create a text response instead of returning raw transcription."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
                "llm": {
                    "polish_mode": "always",
                    "level": "balanced",
                    "tone": "direct",
                    "persona": "professional",
                },
            },
            f,
            allow_unicode=True,
        )

    reload_config()

    captured = {}

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["model"] = model
            captured["callback"] = callback
            captured["create_response_called"] = False

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["update_kwargs"] = kwargs
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "嗯 这个方案已经确认了",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            captured["create_response_called"] = True
            captured["create_response_instructions"] = instructions
            captured["create_response_modalities"] = output_modalities
            captured["callback"].on_event({"type": "response.text.delta", "delta": "这个方案已经确认了，"})
            captured["callback"].on_event({"type": "response.text.delta", "delta": "可以开始执行。"})
            captured["callback"].on_event({"type": "response.done"})

        def close(self):
            return None

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert captured["model"] == "qwen3.5-omni-plus-realtime"
    assert captured["update_kwargs"]["enable_turn_detection"] is False
    assert captured["update_kwargs"]["instructions"]
    assert "润色强度" in captured["update_kwargs"]["instructions"]
    assert captured["create_response_called"] is True
    assert text == "这个方案已经确认了，可以开始执行。"


def test_omni_smart_mode_skips_inline_polish_for_short_text(tmp_path, monkeypatch):
    """Short texts in smart mode should return transcription directly without response generation."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
                "llm": {"polish_mode": "smart"},
            },
            f,
        )

    reload_config()

    captured = {}

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["callback"] = callback
            captured["create_response_called"] = False

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["update_kwargs"] = kwargs
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "好的",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            captured["create_response_called"] = True

        def close(self):
            return None

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert captured["update_kwargs"]["instructions"]
    assert captured["create_response_called"] is False
    assert text == "好的"


def test_omni_inline_polish_falls_back_to_transcript_when_response_incomplete(
    tmp_path, monkeypatch
):
    """Timed-out Omni responses should not return partial polished text."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
                "llm": {"polish_mode": "always"},
            },
            f,
        )

    reload_config()

    captured = {}

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["callback"] = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "这个方案已经确认了",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            captured["callback"].on_event({"type": "response.text.delta", "delta": "这个方案"})

        def close(self):
            return None

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)
    monkeypatch.setattr(
        asr_engine.BatchASRCallback,
        "wait_for_response_complete",
        lambda self, timeout=30.0: False,
    )

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert text == "这个方案已经确认了"


def test_streaming_omni_inline_polish_returns_response_text(tmp_path, monkeypatch):
    """The streaming ASR path should return Omni's final polished response text."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
                "llm": {"polish_mode": "always", "level": "balanced"},
            },
            f,
        )

    reload_config()

    captured = {"append_count": 0, "closed": False}

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["callback"] = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["update_kwargs"] = kwargs
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            captured["append_count"] += 1

        def commit(self):
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "嗯 这个方案已经确认了",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            captured["callback"].on_event(
                {"type": "response.text.delta", "delta": "这个方案已经确认了，可以开始执行。"}
            )
            captured["callback"].on_event({"type": "response.done"})

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    text = engine.stop(pcm_data=b"\x01\x00" * 4000)

    assert captured["update_kwargs"]["instructions"]
    assert captured["append_count"] >= 1
    assert captured["closed"] is True
    assert text == "这个方案已经确认了，可以开始执行。"


def test_omni_inline_polish_falls_back_to_transcript_when_socket_closes_early(
    tmp_path, monkeypatch
):
    """A closed socket before response.done should not surface partial response text."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
                "llm": {"polish_mode": "always"},
            },
            f,
        )

    reload_config()

    captured = {}

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["callback"] = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "这个方案已经确认了",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            captured["callback"].on_event({"type": "response.text.delta", "delta": "这个方案"})
            captured["callback"].on_close(1000, "closed")

        def close(self):
            return None

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert text == "这个方案已经确认了"


def test_streaming_engine_uses_session_model_snapshot_for_finish_path(
    tmp_path, monkeypatch
):
    """Streaming finish decisions should use the model active when the session started."""
    from vocal_more.config import Config, get_config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3-asr-flash-realtime-2026-02-10", "language": "zh"},
                "llm": {"polish_mode": "always"},
            },
            f,
        )

    reload_config()

    captured = {"create_response_called": False, "end_session_called": False}

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["callback"] = callback
            captured["session_model"] = model

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["update_kwargs"] = kwargs
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "嗯 这个方案已经确认了",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            captured["create_response_called"] = True

        def end_session(self, timeout=5):
            captured["end_session_called"] = True
            captured["callback"].on_event({"type": "session.finished"})

        def close(self):
            return None

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    engine.start()
    get_config().asr.model = "qwen3.5-omni-plus-realtime"
    text = engine.stop(pcm_data=b"\x01\x00" * 4000)

    assert captured["session_model"] == "qwen3-asr-flash-realtime-2026-02-10"
    assert captured["create_response_called"] is False
    assert captured["end_session_called"] is True
    assert text == "嗯 这个方案已经确认了"


def test_streaming_engine_emits_partial_updates_during_recording_and_inline_response(
    tmp_path, monkeypatch
):
    """Streaming ASR should emit partial callbacks before stop and during Omni response."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
                "llm": {"polish_mode": "always"},
            },
            f,
        )

    reload_config()

    partials = []

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeConversation:
        def __init__(self, model, url, callback):
            self.callback = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            self.callback.on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.text",
                    "text": "这个方案",
                }
            )

        def commit(self):
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "这个方案已经确认了",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            self.callback.on_event(
                {"type": "response.text.delta", "delta": "这个方案已经确认了，可以开始执行。"}
            )
            self.callback.on_event({"type": "response.done"})

        def close(self):
            return None

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine(
        on_partial_result=lambda result: partials.append(result.text),
    )
    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    text = engine.stop(pcm_data=b"\x01\x00" * 4000)

    assert partials == [
        "这个方案",
        "这个方案已经确认了",
        "这个方案已经确认了，可以开始执行。",
    ]
    assert text == "这个方案已经确认了，可以开始执行。"


def test_streaming_asr_logs_selected_model(tmp_path, monkeypatch, capsys):
    """Streaming ASR startup log should include the active model id."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {"asr": {"model": "qwen3-asr-flash-realtime-2026-02-10", "language": "zh"}},
            f,
        )

    reload_config()

    started = {"value": False}

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            started["value"] = True

    monkeypatch.setattr(asr_engine.threading, "Thread", FakeThread)

    engine = asr_engine.ASREngine()
    engine.start()

    out = capsys.readouterr().out
    assert started["value"] is True
    assert "[StreamingASR] Starting session:" in out
    assert "model=qwen3-asr-flash-realtime-2026-02-10" in out
