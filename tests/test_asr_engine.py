"""Tests for ASR engine backends."""

import importlib
import json
import queue
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml


def _make_pcm_segment(duration_sec: float, sample_value: int = 0) -> bytes:
    sample_count = int(16000 * duration_sec)
    return int(sample_value).to_bytes(2, "little", signed=True) * sample_count


def test_streaming_callback_dispatches_partials_on_inbound_worker():
    """Realtime partials should be delivered by the inbound event worker, not the SDK thread."""
    import vocal_more.core.asr_engine as asr_engine

    partial_threads = []
    received = threading.Event()
    callback = asr_engine.StreamingASRCallback(
        on_partial=lambda result: (
            partial_threads.append(threading.current_thread().name),
            received.set(),
        ),
    )

    callback.on_event(
        {
            "type": "conversation.item.input_audio_transcription.text",
            "text": "hello",
        }
    )

    assert received.wait(timeout=1.0) is True
    assert partial_threads == ["vocal-more-asr-inbound"]

    callback.close()


def test_streaming_queue_helpers_scale_with_blocksize():
    """Queue sizing and drain timeout should reflect recorder chunk duration."""
    import vocal_more.core.asr_engine as asr_engine

    fast_chunks = SimpleNamespace(audio=SimpleNamespace(sample_rate=16000, blocksize=800, channels=1))
    slow_chunks = SimpleNamespace(audio=SimpleNamespace(sample_rate=16000, blocksize=3200, channels=1))

    assert asr_engine._streaming_audio_queue_max_chunks(fast_chunks) > asr_engine._streaming_audio_queue_max_chunks(slow_chunks)
    assert asr_engine._audio_queue_drain_timeout_seconds(0, fast_chunks) == asr_engine.MIN_AUDIO_QUEUE_DRAIN_TIMEOUT_SECONDS
    assert asr_engine._audio_queue_drain_timeout_seconds(16, slow_chunks) > asr_engine.MIN_AUDIO_QUEUE_DRAIN_TIMEOUT_SECONDS


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
                    "model": "qwen3-asr-flash-realtime-2026-02-10",
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


def test_short_file_backend_omits_language_for_mixed_mode(tmp_path, monkeypatch):
    """Mixed-language mode should let the short-file backend auto-detect language."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"backend": "short_file", "model": "qwen3-asr-flash", "language": "auto"}}, f)

    reload_config()

    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            output=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=[{"text": "hello 世界"}])
                    )
                ]
            ),
        )

    monkeypatch.setattr(asr_engine.MultiModalConversation, "call", fake_call)

    engine = asr_engine.BatchASREngine()
    monkeypatch.setattr(engine, "_supports_short_file", lambda _audio: True)

    text = engine.transcribe(b"\x01\x00" * 4000)

    assert text == "hello 世界"
    assert captured["asr_options"] == {"enable_itn": True}


def test_language_override_is_local_to_batch_transcribe(tmp_path, monkeypatch):
    """language_override should affect the request without mutating global config."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {"asr": {"backend": "short_file", "model": "qwen3-asr-flash", "language": "zh"}},
            f,
        )

    reload_config()

    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            output=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=[{"text": "hello world"}])
                    )
                ]
            ),
        )

    monkeypatch.setattr(asr_engine.MultiModalConversation, "call", fake_call)

    engine = asr_engine.BatchASREngine()
    monkeypatch.setattr(engine, "_supports_short_file", lambda _audio: True)

    text = engine.transcribe(
        b"\x01\x00" * 4000,
        language_override="en",
    )

    assert text == "hello world"
    assert captured["asr_options"] == {"language": "en", "enable_itn": True}
    assert engine.config.asr.language == "zh"


def test_realtime_ws_batch_omits_language_for_mixed_mode(tmp_path, monkeypatch):
    """Mixed-language mode should omit language hints for realtime transcription."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "asr": {
                    "backend": "realtime_ws",
                    "model": "qwen3-asr-flash-realtime-2026-02-10",
                    "language": "auto",
                }
            },
            f,
        )

    reload_config()

    captured = {}

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["model"] = model
            captured["url"] = url
            captured["callback"] = callback

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
                    "transcript": "hello 世界",
                }
            )

        def end_session(self, timeout=5):
            captured["callback"].on_event({"type": "session.finished"})

        def close(self):
            return None

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert text == "hello 世界"
    assert captured["update_kwargs"]["transcription_params"].language is None


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


def test_adaptive_response_timeouts_scale_with_audio_duration():
    """Realtime response waits should grow for long audio and stay bounded."""
    import vocal_more.core.asr_engine as asr_engine

    assert asr_engine._adaptive_response_start_timeout(30.0) == pytest.approx(3.0)
    assert asr_engine._adaptive_response_complete_timeout(30.0) == pytest.approx(30.0)

    assert asr_engine._adaptive_response_start_timeout(180.0) > 3.0
    assert asr_engine._adaptive_response_complete_timeout(180.0) > 30.0

    assert (
        asr_engine._adaptive_response_start_timeout(14 * 60)
        <= asr_engine.MAX_ADAPTIVE_RESPONSE_START_TIMEOUT_SECONDS
    )
    assert (
        asr_engine._adaptive_response_complete_timeout(14 * 60)
        <= asr_engine.MAX_ADAPTIVE_RESPONSE_COMPLETE_TIMEOUT_SECONDS
    )


@pytest.mark.parametrize(
    ("realtime_model", "offline_model"),
    [
        ("qwen3.5-omni-flash-realtime", "qwen3.5-omni-flash"),
        ("qwen3.5-omni-plus-realtime", "qwen3.5-omni-plus"),
    ],
)
def test_long_realtime_batch_audio_bypasses_realtime_and_uses_offline_chunking(
    tmp_path, monkeypatch, realtime_model, offline_model
):
    """Very long batch retry audio should skip realtime and route to offline chunking."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"model": realtime_model}}, f)

    reload_config()

    engine = asr_engine.BatchASREngine()
    captured = {}

    def fake_realtime(*_args, **_kwargs):
        raise AssertionError("long batch audio should bypass realtime_ws")

    def fake_chunked(audio_data, *, model, language_override):
        captured["audio_size"] = len(audio_data)
        captured["model"] = model
        captured["language_override"] = language_override
        return "chunked offline result"

    monkeypatch.setattr(engine, "_transcribe_realtime_ws", fake_realtime)
    monkeypatch.setattr(engine, "_transcribe_chunked_audio", fake_chunked)

    total_seconds = int(asr_engine.OMNI_REALTIME_DIRECT_OFFLINE_THRESHOLD_SECONDS + 30)
    audio_data = b"\x01\x00" * (16000 * total_seconds)
    text = engine.transcribe(audio_data, language_override="en")

    assert text == "chunked offline result"
    assert captured == {
        "audio_size": len(audio_data),
        "model": offline_model,
        "language_override": "en",
    }


def test_short_realtime_batch_audio_still_uses_realtime_path(tmp_path, monkeypatch):
    """Short batch audio should still use realtime instead of the direct-offline path."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"model": "qwen3.5-omni-plus-realtime"}}, f)

    reload_config()

    engine = asr_engine.BatchASREngine()
    calls = {"realtime": 0}

    def fake_realtime(audio_data, **kwargs):
        calls["realtime"] += 1
        calls["audio_size"] = len(audio_data)
        calls["kwargs"] = kwargs
        return "realtime result"

    def fake_offline(*_args, **_kwargs):
        raise AssertionError("short batch audio should not bypass to omni_offline")

    monkeypatch.setattr(engine, "_transcribe_realtime_ws", fake_realtime)
    monkeypatch.setattr(engine, "_transcribe_omni_offline", fake_offline)
    monkeypatch.setattr(engine, "_transcribe_chunked_audio", fake_offline)

    short_seconds = 210
    audio_data = b"\x01\x00" * (16000 * short_seconds)
    text = engine.transcribe(audio_data, language_override="zh")

    assert text == "realtime result"
    assert calls == {
        "realtime": 1,
        "audio_size": len(audio_data),
        "kwargs": {
            "model_override": "qwen3.5-omni-plus-realtime",
            "language_override": "zh",
        },
    }


def test_batch_realtime_uses_adaptive_response_start_timeout_for_long_audio(
    tmp_path, monkeypatch
):
    """Long realtime audio should wait longer than the short-utterance default."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"model": "qwen3.5-omni-plus-realtime"}}, f)

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
                    "transcript": "长音频 transcript",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            captured["create_response_called"] = True

        def close(self):
            return None

    def fake_wait_for_response_started(self, timeout=30.0):
        captured.setdefault("response_start_timeouts", []).append(timeout)
        return False

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)
    monkeypatch.setattr(
        asr_engine.BatchASRCallback,
        "wait_for_response_started",
        fake_wait_for_response_started,
    )
    monkeypatch.setattr(
        asr_engine.BatchASREngine,
        "_recover_failed_omni_response",
        lambda self, audio_data, model, transcript_text, reason, trace=None: (
            transcript_text,
            "transcript",
        ),
    )

    engine = asr_engine.BatchASREngine()
    long_audio_seconds = 210
    long_audio = b"\x01\x00" * (16000 * long_audio_seconds)
    text = engine.transcribe(long_audio)

    assert captured["create_response_called"] is True
    assert (
        captured["response_start_timeouts"][0]
        > asr_engine.INLINE_RESPONSE_START_TIMEOUT_SECONDS
    )
    assert text == "长音频 transcript"


def test_streaming_long_audio_uses_realtime_transcript_without_inline_response(
    tmp_path, monkeypatch
):
    """Realtime-long stop should use the already streamed transcript for long audio."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"}}, f)

    reload_config()

    captured = {"commit_called": False, "create_response_called": False, "closed": False}

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

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
            captured["commit_called"] = True
            captured["callback"].on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "long realtime result",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            captured["create_response_called"] = True

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    engine._batch_fallback.transcribe = MagicMock(return_value="long offline result")
    engine._schedule_warm_close = MagicMock()

    engine.start()
    long_audio_seconds = 270
    pcm_data = b"\x01\x00" * (16000 * long_audio_seconds)
    text = engine.stop(pcm_data=pcm_data)

    assert text == "long realtime result"
    assert captured["commit_called"] is True
    assert captured["create_response_called"] is False
    assert captured["closed"] is False
    engine._schedule_warm_close.assert_called_once()
    engine._batch_fallback.transcribe.assert_not_called()


def test_omni_offline_long_audio_is_chunked_and_joined(tmp_path, monkeypatch):
    """Long offline audio should be retried chunk-by-chunk and stitched back together."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"model": "qwen3.5-omni-plus"}}, f)

    reload_config()

    engine = asr_engine.BatchASREngine()
    calls = []
    chunk_results = iter(["第一段。", "第二段。", "第三段。"])

    def fake_offline(audio_data, model_override=None):
        calls.append((len(audio_data), model_override))
        return next(chunk_results)

    monkeypatch.setattr(engine, "_transcribe_omni_offline", fake_offline)

    total_seconds = asr_engine.OMNI_OFFLINE_CHUNK_DURATION_SECONDS * 2 + 30
    audio_data = b"\x01\x00" * (16000 * total_seconds)
    text = engine.transcribe(audio_data)

    assert text == "第一段。第二段。第三段。"
    assert len(calls) == 3
    assert all(model == "qwen3.5-omni-plus" for _, model in calls)
    assert max(size for size, _ in calls) <= 16000 * 2 * asr_engine.OMNI_OFFLINE_CHUNK_DURATION_SECONDS


def test_omni_offline_chunking_prefers_silence_before_hard_boundary(tmp_path, monkeypatch):
    """Chunking should cut at a nearby quiet window instead of the exact duration limit."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"model": "qwen3.5-omni-plus"}}, f)

    reload_config()

    engine = asr_engine.BatchASREngine()
    audio_data = b"".join(
        [
            _make_pcm_segment(172, 9000),
            _make_pcm_segment(4, 0),
            _make_pcm_segment(20, 9000),
        ]
    )

    chunks = engine._split_audio_for_batch(audio_data)

    assert len(chunks) == 2
    first_chunk_seconds = len(chunks[0]) / (16000 * 2)
    assert 175.5 <= first_chunk_seconds <= 176.5
    assert first_chunk_seconds < asr_engine.OMNI_OFFLINE_CHUNK_DURATION_SECONDS


def test_omni_offline_chunk_failure_reports_chunk_index(tmp_path, monkeypatch):
    """Chunked retry errors should identify which chunk failed."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"model": "qwen3.5-omni-plus"}}, f)

    reload_config()

    engine = asr_engine.BatchASREngine()
    call_count = {"value": 0}

    def fake_offline(audio_data, model_override=None):
        call_count["value"] += 1
        if call_count["value"] == 2:
            raise RuntimeError("request too large")
        return "ok"

    monkeypatch.setattr(engine, "_transcribe_omni_offline", fake_offline)

    total_seconds = asr_engine.OMNI_OFFLINE_CHUNK_DURATION_SECONDS * 2 + 5
    audio_data = b"\x01\x00" * (16000 * total_seconds)

    with pytest.raises(RuntimeError, match=r"Chunk 2/3 transcription failed: request too large"):
        engine.transcribe(audio_data)


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


def test_legacy_realtime_asr_uses_transcription_params(tmp_path, monkeypatch):
    """Legacy realtime ASR should use transcription_params, not Omni transcription hints."""
    from vocal_more.config import Config, reload_config
    import vocal_more.core.asr_engine as asr_engine

    config_path = tmp_path / "config.yaml"
    dict_path = tmp_path / "dictionary.yaml"

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "asr": {
                    "model": "qwen3-asr-flash-realtime-2026-02-10",
                    "language": "zh",
                }
            },
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


def test_refresh_api_key_drops_idle_warm_session(monkeypatch):
    """Refreshing credentials should tear down any idle warm realtime session."""
    import vocal_more.core.asr_engine as asr_engine

    engine = asr_engine.ASREngine()
    conversation = MagicMock()
    timer = MagicMock()

    engine.config.api_key = "updated-key"
    engine._conversation = conversation
    engine._conversation_model_id = engine.config.asr.model
    engine._session_ready = True
    engine._warm_close_timer = timer
    engine._is_running = False

    engine.refresh_api_key()

    assert asr_engine.dashscope.api_key == "updated-key"
    timer.cancel.assert_called_once()
    conversation.close.assert_called_once()
    assert engine._conversation is None
    assert engine._conversation_model_id is None
    assert engine._session_ready is False


def test_refresh_runtime_config_drops_idle_warm_session(monkeypatch):
    """Session-sensitive config refresh should tear down any idle warm realtime session."""
    import vocal_more.core.asr_engine as asr_engine

    engine = asr_engine.ASREngine()
    conversation = MagicMock()
    timer = MagicMock()

    engine._conversation = conversation
    engine._conversation_model_id = engine.config.asr.model
    engine._session_ready = True
    engine._warm_close_timer = timer
    engine._is_running = False

    engine.refresh_runtime_config(drop_idle_session=True)

    timer.cancel.assert_called_once()
    conversation.close.assert_called_once()
    assert engine._conversation is None
    assert engine._conversation_model_id is None
    assert engine._session_ready is False


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


def test_omni_inline_polish_handles_short_text_when_enabled(tmp_path, monkeypatch):
    """With polish enabled, Omni should still request a final response for short text."""
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
            captured["callback"].on_event({"type": "response.text.delta", "delta": "好的。"})
            captured["callback"].on_event({"type": "response.done"})

        def close(self):
            return None

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert captured["update_kwargs"]["instructions"]
    assert captured["create_response_called"] is True
    assert text == "好的。"


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
    monkeypatch.setattr(
        asr_engine.BatchASREngine,
        "_recover_failed_omni_response",
        lambda self, audio_data, model, transcript_text, reason: (
            transcript_text,
            "transcript",
        ),
    )

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert text == "这个方案已经确认了"


def test_omni_inline_polish_accepts_text_done_and_output_item_done_without_response_done(
    tmp_path, monkeypatch
):
    """A completed text item should be accepted even if response.done never arrives."""
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
            captured["callback"].on_event(
                {
                    "type": "response.created",
                    "response_id": "resp-123",
                }
            )
            captured["callback"].on_event(
                {
                    "type": "response.text.delta",
                    "response_id": "resp-123",
                    "item_id": "item-assistant-123",
                    "delta": "这个方案已经确认了，可以开始执行。",
                }
            )
            captured["callback"].on_event(
                {
                    "type": "response.text.done",
                    "response_id": "resp-123",
                    "item_id": "item-assistant-123",
                    "text": "这个方案已经确认了，可以开始执行。",
                }
            )
            captured["callback"].on_event(
                {
                    "type": "response.output_item.done",
                    "response_id": "resp-123",
                    "output_index": 0,
                    "item": {
                        "id": "item-assistant-123",
                        "object": "realtime.item",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "这个方案已经确认了，可以开始执行。",
                            }
                        ],
                    },
                }
            )

        def close(self):
            return None

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)
    monkeypatch.setattr(
        asr_engine.BatchASREngine,
        "_recover_failed_omni_response",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not fall back when output item is completed")
        ),
    )

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert text == "这个方案已经确认了，可以开始执行。"


def test_omni_inline_polish_recovers_if_response_starts_during_transcript_wait(
    tmp_path, monkeypatch
):
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-flash-realtime", "language": "zh"},
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
            return None

        def create_response(self, instructions=None, output_modalities=None):
            return None

        def close(self):
            return None

    wait_started_calls = {"count": 0}

    def fake_wait_for_response_started(self, timeout=30.0):
        wait_started_calls["count"] += 1
        if wait_started_calls["count"] == 1:
            return False
        return True

    def fake_wait_for_transcription_complete(self, timeout=30.0):
        self.on_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "能听到吗？",
            }
        )
        self.on_event(
            {
                "type": "response.created",
                "response_id": "resp-late-1",
            }
        )
        self.on_event(
            {
                "type": "response.text.done",
                "response_id": "resp-late-1",
                "item_id": "item-assistant-late-1",
                "text": "能听到吗？",
            }
        )
        self.on_event(
            {
                "type": "response.output_item.done",
                "response_id": "resp-late-1",
                "output_index": 0,
                "item": {
                    "id": "item-assistant-late-1",
                    "object": "realtime.item",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "能听到吗？"}],
                },
            }
        )
        return True

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)
    monkeypatch.setattr(
        asr_engine.BatchASRCallback,
        "wait_for_response_started",
        fake_wait_for_response_started,
    )
    monkeypatch.setattr(
        asr_engine.BatchASRCallback,
        "wait_for_transcription_complete",
        fake_wait_for_transcription_complete,
    )
    monkeypatch.setattr(
        asr_engine.BatchASREngine,
        "_recover_failed_omni_response",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not fall back when response starts during transcript wait")
        ),
    )

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert wait_started_calls["count"] >= 2
    assert text == "能听到吗？"


def test_omni_batch_requests_response_immediately_after_commit(tmp_path, monkeypatch):
    """Omni batch path should request the final response without waiting for transcript."""
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
            },
            f,
        )

    reload_config()

    captured = {"committed": False, "create_response_called": False}

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
            captured["committed"] = True

        def create_response(self, instructions=None, output_modalities=None):
            captured["create_response_called"] = True
            assert captured["committed"] is True
            captured["callback"].on_event({"type": "response.text.delta", "delta": "最终整理后的文本"})
            captured["callback"].on_event({"type": "response.done"})

        def close(self):
            return None

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)
    monkeypatch.setattr(
        asr_engine.BatchASRCallback,
        "wait_for_transcription_complete",
        lambda self, timeout=30.0: False,
    )

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert captured["create_response_called"] is True
    assert text == "最终整理后的文本"


def test_batch_debug_trace_writes_stage_timings(tmp_path, monkeypatch):
    """Batch debug traces should include summarized stage timings and result source."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("VOCAL_MORE_DEBUG_DIR", str(debug_dir))
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
            },
            f,
        )

    reload_config()

    class FakeConversation:
        def __init__(self, model, url, callback):
            self.callback = callback

        def connect(self):
            self.callback.on_open()

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
            self.callback.on_close(1000, "closed")

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.BatchASREngine()
    assert engine.transcribe(b"\x01\x00" * 4000) == "这个方案已经确认了，可以开始执行。"

    json_files = sorted(debug_dir.glob("*.json"))
    assert len(json_files) == 1
    trace = json.loads(json_files[0].read_text())

    assert trace["request_mode"] == "batch"
    assert trace["result_source"] == "response"
    assert trace["response_requested"] is True
    assert trace["timings_ms"]["commit_ms"] is not None
    assert trace["timings_ms"]["response_done_ms"] is not None
    assert trace["timings_ms"]["total_result_ms"] == trace["timings_ms"]["result_selected_ms"]


def test_omni_offline_trace_records_service_request_id(tmp_path, monkeypatch):
    """Omni offline traces should include the provider request ID and completion ID."""
    from vocal_more.config import Config, reload_config
    import openai

    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("VOCAL_MORE_DEBUG_DIR", str(debug_dir))
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": False,
                "asr": {"model": "qwen3.5-omni-plus", "backend": "omni_offline"},
            },
            f,
        )

    reload_config()

    class FakeStream:
        def __init__(self):
            self.response = SimpleNamespace(headers={"x-request-id": "req-omni-123"})

        def __iter__(self):
            yield SimpleNamespace(
                id="chatcmpl-abc",
                choices=[SimpleNamespace(delta=SimpleNamespace(content="你好"))],
            )
            yield SimpleNamespace(
                id="chatcmpl-abc",
                choices=[SimpleNamespace(delta=SimpleNamespace(content="，世界"))],
            )

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeStream()

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    engine = asr_engine.BatchASREngine()
    assert engine._transcribe_omni_offline(b"\x01\x00" * 4000) == "你好，世界"

    json_files = sorted(debug_dir.glob("*.json"))
    trace = json.loads(json_files[0].read_text())

    assert trace["backend"] == "omni_offline"
    assert trace["service_request_id"] == "req-omni-123"
    assert trace["completion_id"] == "chatcmpl-abc"
    assert trace["result_text"] == "你好，世界"


def test_batch_omni_falls_back_to_offline_model_when_response_never_starts(
    tmp_path, monkeypatch
):
    """Batch Omni should use the offline sibling model when realtime response stalls."""
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
            captured["create_response_called"] = True

        def close(self):
            return None

    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)
    monkeypatch.setattr(
        asr_engine.BatchASRCallback,
        "wait_for_response_started",
        lambda self, timeout=30.0: False,
    )

    offline_calls = []

    original_transcribe = asr_engine.BatchASREngine.transcribe

    def fake_transcribe(self, audio_data, model_override=None, language_override=None):
        if model_override == "qwen3.5-omni-plus":
            offline_calls.append(model_override)
            return "离线兜底结果"
        return original_transcribe(
            self,
            audio_data,
            model_override=model_override,
            language_override=language_override,
        )

    monkeypatch.setattr(asr_engine.BatchASREngine, "transcribe", fake_transcribe)

    engine = asr_engine.BatchASREngine()
    text = engine.transcribe(b"\x01\x00" * 4000)

    assert captured["create_response_called"] is True
    assert offline_calls == ["qwen3.5-omni-plus"]
    assert text == "离线兜底结果"


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
                "llm": {"level": "balanced"},
            },
            f,
        )

    reload_config()

    captured = {"append_count": 0, "closed": False}
    timers = []

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeTimer:
        def __init__(self, interval, callback):
            self.interval = interval
            self.callback = callback
            self.daemon = False
            self.started = False
            self.canceled = False
            timers.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.canceled = True

        def fire(self):
            self.callback()

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
    monkeypatch.setattr(asr_engine.threading, "Timer", FakeTimer)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    text = engine.stop(pcm_data=b"\x01\x00" * 4000)

    assert captured["update_kwargs"]["instructions"]
    assert captured["append_count"] >= 1
    assert captured["closed"] is False
    assert timers[-1].started is True
    timers[-1].fire()
    assert captured["closed"] is True
    assert text == "这个方案已经确认了，可以开始执行。"


def test_streaming_queue_backpressure_falls_back_to_batch(tmp_path, monkeypatch):
    """A full outbound audio queue should trigger deterministic batch fallback."""
    from vocal_more.config import Config, reload_config

    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"}}, f)

    reload_config()

    captured = {"append_count": 0, "closed": False}

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["callback"] = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["callback"].on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            captured["append_count"] += 1

        def commit(self):
            raise AssertionError("queue overflow path should bypass realtime commit")

        def create_response(self, instructions=None, output_modalities=None):
            raise AssertionError("queue overflow path should bypass realtime response")

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    offline_calls = []

    def fake_transcribe(audio_data, model_override=None, language_override=None):
        offline_calls.append(
            {
                "audio_size": len(audio_data),
                "model_override": model_override,
                "language_override": language_override,
            }
        )
        return "batch fallback result"

    monkeypatch.setattr(
        engine._audio_queue,
        "put_nowait",
        lambda _chunk: (_ for _ in ()).throw(queue.Full()),
    )
    engine._batch_fallback.transcribe = fake_transcribe

    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    text = engine.stop(pcm_data=b"\x01\x00" * 4000)

    assert text == "batch fallback result"
    assert captured["append_count"] == 0
    assert captured["closed"] is True
    assert offline_calls == [
        {
            "audio_size": 8000,
            "model_override": None,
            "language_override": None,
        }
    ]


def test_streaming_debug_trace_records_full_realtime_protocol(tmp_path, monkeypatch):
    """Streaming traces should include commit/response lifecycle events from the server."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("VOCAL_MORE_DEBUG_DIR", str(debug_dir))
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
            },
            f,
        )

    reload_config()

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeTimer:
        def __init__(self, interval, callback):
            self.callback = callback
            self.daemon = False

        def start(self):
            return None

        def cancel(self):
            return None

    class FakeConversation:
        def __init__(self, model, url, callback):
            self.callback = callback

        def connect(self):
            self.callback.on_open()
            self.callback.on_event(
                {
                    "type": "session.created",
                    "event_id": "evt-session-created",
                    "session": {"id": "sess-123"},
                }
            )

        def update_session(self, **kwargs):
            self.callback.on_event(
                {
                    "type": "session.updated",
                    "event_id": "evt-session-updated",
                    "session": {"id": "sess-123"},
                }
            )

        def append_audio(self, _audio):
            return None

        def commit(self):
            self.callback.on_event(
                {
                    "type": "input_audio_buffer.committed",
                    "event_id": "evt-committed",
                }
            )
            self.callback.on_event(
                {
                    "type": "conversation.item.created",
                    "event_id": "evt-item-created",
                    "item": {"id": "item-user-123"},
                }
            )
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "event_id": "evt-transcript-done",
                    "item_id": "item-user-123",
                    "transcript": "这个方案已经确认了",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            response = {
                "id": "resp-456",
                "conversation_id": "conv-789",
                "output": [{"id": "item-assistant-456"}],
            }
            self.callback.on_event(
                {
                    "type": "response.created",
                    "event_id": "evt-response-created",
                    "response": response,
                }
            )
            self.callback.on_event(
                {
                    "type": "response.output_item.added",
                    "event_id": "evt-output-added",
                    "response": response,
                }
            )
            self.callback.on_event(
                {
                    "type": "response.text.delta",
                    "event_id": "evt-response-delta",
                    "response": response,
                    "delta": "这个方案已经确认了。",
                }
            )
            self.callback.on_event(
                {
                    "type": "response.text.done",
                    "event_id": "evt-response-text-done",
                    "response": response,
                }
            )
            self.callback.on_event(
                {
                    "type": "response.done",
                    "event_id": "evt-response-done",
                    "response": response,
                }
            )

        def close(self):
            self.callback.on_close(1000, "closed")

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine.threading, "Timer", FakeTimer)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    assert engine.stop(pcm_data=b"\x01\x00" * 4000) == "这个方案已经确认了。"

    json_files = sorted(debug_dir.glob("*.json"))
    trace = json.loads(json_files[0].read_text())
    event_types = [event["type"] for event in trace["events"]]

    assert "session.created" in event_types
    assert "input_audio_buffer.committed" in event_types
    assert "conversation.item.created" in event_types
    assert "response.created" in event_types
    assert "response.output_item.added" in event_types
    assert "response.text.done" in event_types
    assert "response.done" in event_types
    assert trace["session_id"] == "sess-123"
    assert trace["conversation_id"] == "conv-789"
    assert trace["input_item_id"] == "item-user-123"
    assert trace["response_id"] == "resp-456"
    assert trace["response_output_item_id"] == "item-assistant-456"
    assert trace["server_event_ids"]["conversation.item.input_audio_transcription.completed"] == "evt-transcript-done"
    assert trace["server_event_ids"]["response.created"] == "evt-response-created"
    assert trace["server_event_ids"]["response.done"] == "evt-response-done"


def test_update_trace_ids_keeps_response_item_id_separate_from_input_item_id():
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    trace = asr_engine.ASRDebugTrace(
        backend="realtime_ws",
        request_mode="streaming",
        model="qwen3.5-omni-plus-realtime",
        sample_rate=16000,
        audio_bytes=0,
        audio_duration_ms=0.0,
        corpus_text=None,
    )

    asr_engine._update_trace_ids_from_response(
        trace,
        "conversation.item.created",
        {
            "event_id": "evt-user-item",
            "item": {"id": "item-user-123"},
        },
    )
    asr_engine._update_trace_ids_from_response(
        trace,
        "response.text.done",
        {
            "event_id": "evt-text-done",
            "response_id": "resp-456",
            "item_id": "item-assistant-789",
            "text": "好的。",
        },
    )

    assert trace.input_item_id == "item-user-123"
    assert trace.response_output_item_id == "item-assistant-789"
    assert trace.response_id == "resp-456"


def test_streaming_omni_falls_back_to_offline_model_when_response_never_starts(
    tmp_path, monkeypatch
):
    """Streaming Omni should use the offline sibling model if the response never starts."""
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
            },
            f,
        )

    reload_config()

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeTimer:
        def __init__(self, interval, callback):
            self.callback = callback
            self.daemon = False

        def start(self):
            return None

        def cancel(self):
            return None

    class FakeConversation:
        def __init__(self, model, url, callback):
            self.callback = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            self.callback.on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "这个方案已经确认了",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            return None

        def close(self):
            return None

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine.threading, "Timer", FakeTimer)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)
    monkeypatch.setattr(
        asr_engine.StreamingASRCallback,
        "wait_for_response_started",
        lambda self, timeout=30.0: False,
    )

    engine = asr_engine.ASREngine()
    offline_calls = []

    def fake_transcribe(audio_data, model_override=None, language_override=None):
        offline_calls.append(model_override)
        return "离线兜底结果"

    engine._batch_fallback.transcribe = fake_transcribe

    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    text = engine.stop(pcm_data=b"\x01\x00" * 4000)

    assert offline_calls == ["qwen3.5-omni-plus"]
    assert text == "离线兜底结果"


def test_streaming_omni_accepts_completed_output_item_without_response_done(
    tmp_path, monkeypatch
):
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
            },
            f,
        )

    reload_config()

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    class FakeTimer:
        def __init__(self, interval, function):
            self.function = function

        def start(self):
            return None

        def cancel(self):
            return None

    class FakeConversation:
        def __init__(self, model, url, callback):
            self.callback = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            self.callback.on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "这个方案已经确认了",
                }
            )

        def create_response(self, instructions=None, output_modalities=None):
            self.callback.on_event(
                {
                    "type": "response.created",
                    "response_id": "resp-123",
                }
            )
            self.callback.on_event(
                {
                    "type": "response.text.delta",
                    "response_id": "resp-123",
                    "item_id": "item-assistant-123",
                    "delta": "这个方案已经确认了，可以开始执行。",
                }
            )
            self.callback.on_event(
                {
                    "type": "response.text.done",
                    "response_id": "resp-123",
                    "item_id": "item-assistant-123",
                    "text": "这个方案已经确认了，可以开始执行。",
                }
            )
            self.callback.on_event(
                {
                    "type": "response.output_item.done",
                    "response_id": "resp-123",
                    "output_index": 0,
                    "item": {
                        "id": "item-assistant-123",
                        "object": "realtime.item",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "这个方案已经确认了，可以开始执行。",
                            }
                        ],
                    },
                }
            )

        def close(self):
            return None

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine.threading, "Timer", FakeTimer)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    engine._batch_fallback.transcribe = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("should not fall back when output item is completed")
    )

    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    text = engine.stop(pcm_data=b"\x01\x00" * 4000)

    assert text == "这个方案已经确认了，可以开始执行。"


def test_streaming_omni_recovers_if_response_starts_during_transcript_wait(
    tmp_path, monkeypatch
):
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": True,
                "asr": {"model": "qwen3.5-omni-flash-realtime", "language": "zh"},
            },
            f,
        )

    reload_config()

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    class FakeTimer:
        def __init__(self, interval, function):
            self.function = function

        def start(self):
            return None

        def cancel(self):
            return None

    class FakeConversation:
        def __init__(self, model, url, callback):
            self.callback = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            self.callback.on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            return None

        def create_response(self, instructions=None, output_modalities=None):
            return None

        def close(self):
            return None

    wait_started_calls = {"count": 0}

    def fake_wait_for_response_started(self, timeout=30.0):
        wait_started_calls["count"] += 1
        if wait_started_calls["count"] == 1:
            return False
        return True

    def fake_wait_for_transcription_complete(self, timeout=30.0):
        self.on_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "能听到吗？",
            }
        )
        self.on_event(
            {
                "type": "response.created",
                "response_id": "resp-late-stream",
            }
        )
        self.on_event(
            {
                "type": "response.text.done",
                "response_id": "resp-late-stream",
                "item_id": "item-assistant-late-stream",
                "text": "能听到吗？",
            }
        )
        self.on_event(
            {
                "type": "response.output_item.done",
                "response_id": "resp-late-stream",
                "output_index": 0,
                "item": {
                    "id": "item-assistant-late-stream",
                    "object": "realtime.item",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "能听到吗？"}],
                },
            }
        )
        return True

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine.threading, "Timer", FakeTimer)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)
    monkeypatch.setattr(
        asr_engine.StreamingASRCallback,
        "wait_for_response_started",
        fake_wait_for_response_started,
    )
    monkeypatch.setattr(
        asr_engine.StreamingASRCallback,
        "wait_for_transcription_complete",
        fake_wait_for_transcription_complete,
    )

    engine = asr_engine.ASREngine()
    engine._batch_fallback.transcribe = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("should not fall back when response starts during transcript wait")
    )

    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    text = engine.stop(pcm_data=b"\x01\x00" * 4000)

    assert wait_started_calls["count"] >= 2
    assert text == "能听到吗？"


def test_streaming_engine_reuses_warm_omni_session_within_ttl(tmp_path, monkeypatch):
    """A second short utterance should reuse the existing Omni realtime socket."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": False,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
            },
            f,
        )

    reload_config()

    timers = []
    captured = {"instances": 0, "update_calls": 0, "closed": 0}

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeTimer:
        def __init__(self, interval, callback):
            self.interval = interval
            self.callback = callback
            self.daemon = False
            self.started = False
            self.canceled = False
            timers.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.canceled = True

        def fire(self):
            self.callback()

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["instances"] += 1
            self.callback = callback

        def connect(self):
            return None

        def update_session(self, **kwargs):
            captured["update_calls"] += 1
            self.callback.on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "收到",
                }
            )

        def close(self):
            captured["closed"] += 1

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine.threading, "Timer", FakeTimer)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    assert engine.stop(pcm_data=b"\x01\x00" * 4000) == "收到"

    assert captured["instances"] == 1
    assert captured["update_calls"] == 1
    assert captured["closed"] == 0
    assert timers[0].started is True

    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    assert engine.stop(pcm_data=b"\x01\x00" * 4000) == "收到"

    assert captured["instances"] == 1
    assert captured["update_calls"] == 2
    assert timers[0].canceled is True
    assert captured["closed"] == 0

    timers[-1].fire()
    assert captured["closed"] == 1


def test_streaming_engine_reconnects_instead_of_reusing_stale_warm_socket(
    tmp_path, monkeypatch
):
    """Warm reuse should be skipped when the cached socket is no longer connected."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": False,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
            },
            f,
        )

    reload_config()

    timers = []
    captured = {"instances": 0}

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeTimer:
        def __init__(self, interval, callback):
            self.callback = callback
            self.daemon = False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            return None

    class FakeConversation:
        def __init__(self, model, url, callback):
            captured["instances"] += 1
            self.callback = callback
            self.ws = SimpleNamespace(sock=SimpleNamespace(connected=True))

        def connect(self):
            return None

        def update_session(self, **kwargs):
            self.callback.on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "收到",
                }
            )

        def close(self):
            self.ws.sock.connected = False

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine.threading, "Timer", FakeTimer)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    assert engine.stop(pcm_data=b"\x01\x00" * 4000) == "收到"

    engine._conversation.ws.sock.connected = False

    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    assert engine.stop(pcm_data=b"\x01\x00" * 4000) == "收到"

    assert captured["instances"] == 2


def test_streaming_debug_trace_marks_warm_reuse(tmp_path, monkeypatch):
    """Streaming debug traces should show whether a warm Omni session was reused."""
    from vocal_more.config import Config, reload_config
    asr_engine = importlib.import_module("vocal_more.core.asr_engine")

    config_path = tmp_path / "config.yaml"
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("VOCAL_MORE_DEBUG_DIR", str(debug_dir))
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "enable_polish": False,
                "asr": {"model": "qwen3.5-omni-plus-realtime", "language": "zh"},
            },
            f,
        )

    reload_config()

    timers = []

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeTimer:
        def __init__(self, interval, callback):
            self.interval = interval
            self.callback = callback
            self.daemon = False
            self.started = False
            self.canceled = False
            timers.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.canceled = True

    class FakeConversation:
        def __init__(self, model, url, callback):
            self.callback = callback

        def connect(self):
            self.callback.on_open()

        def update_session(self, **kwargs):
            self.callback.on_event({"type": "session.updated"})

        def append_audio(self, _audio):
            return None

        def commit(self):
            self.callback.on_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "收到",
                }
            )

        def close(self):
            self.callback.on_close(1000, "closed")

    monkeypatch.setattr(asr_engine.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(asr_engine.threading, "Timer", FakeTimer)
    monkeypatch.setattr(asr_engine, "OmniRealtimeConversation", FakeConversation)

    engine = asr_engine.ASREngine()
    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    assert engine.stop(pcm_data=b"\x01\x00" * 4000) == "收到"

    engine.start()
    engine.send_audio(b"\x01\x00" * 1600)
    assert engine.stop(pcm_data=b"\x01\x00" * 4000) == "收到"

    json_files = sorted(debug_dir.glob("*.json"))
    assert len(json_files) == 2
    first_trace = json.loads(json_files[0].read_text())
    second_trace = json.loads(json_files[1].read_text())

    assert first_trace["request_mode"] == "streaming"
    assert first_trace["warm_session_reused"] is False
    assert first_trace["result_source"] == "transcript"
    assert second_trace["request_mode"] == "streaming"
    assert second_trace["warm_session_reused"] is True
    assert second_trace["timings_ms"]["commit_ms"] is not None
    assert second_trace["timings_ms"]["result_selected_ms"] is not None


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
    monkeypatch.setattr(
        asr_engine.BatchASREngine,
        "_recover_failed_omni_response",
        lambda self, audio_data, model, transcript_text, reason: (
            transcript_text,
            "transcript",
        ),
    )

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
            },
            f,
        )

    reload_config()

    partials = []
    timers = []

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class FakeTimer:
        def __init__(self, interval, callback):
            self.interval = interval
            self.callback = callback
            self.daemon = False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            return None

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
    monkeypatch.setattr(asr_engine.threading, "Timer", FakeTimer)
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
