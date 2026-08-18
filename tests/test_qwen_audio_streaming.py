"""Tests for the dedicated Qwen Audio Recognition transport."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest


def test_recognition_adapter_sends_binary_pcm_and_maps_sentence_snapshots(monkeypatch):
    import vocal_more.infrastructure.asr.qwen_audio_streaming as streaming

    captured = {}

    class FakeResult:
        message = ""

        def __init__(self, text, *, final=False):
            self._sentence = {"text": text, "end_time": 100 if final else None}

        def get_sentence(self):
            return self._sentence

        def get_usage(self, _sentence):
            return {"duration": 1} if self._sentence["end_time"] else None

    class FakeRecognitionResult:
        @staticmethod
        def is_sentence_end(sentence):
            return sentence.get("end_time") is not None

    class FakeRecognition:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.callback = kwargs["callback"]
            self.frames = []

        def start(self):
            self.callback.on_open()

        def send_audio_frame(self, frame):
            self.frames.append(frame)
            captured["frames"] = self.frames

        def stop(self):
            self.callback.on_event(FakeResult("hello "))
            self.callback.on_event(FakeResult("hello world"))
            self.callback.on_event(FakeResult("hello world.", final=True))
            self.callback.on_complete()
            self.callback.on_close()

    monkeypatch.setattr(streaming, "Recognition", FakeRecognition)
    monkeypatch.setattr(streaming, "RecognitionResult", FakeRecognitionResult)

    events = []
    conversation = streaming.QwenAudioStreamingConversation(
        model="qwen-audio-3.0-asr-flash-streaming",
        sample_rate=16000,
        language="en",
        context_instruction="Prefer product names.",
        on_ready=lambda: events.append(("ready", "")),
        on_partial=lambda text: events.append(("partial", text)),
        on_final=lambda text: events.append(("final", text)),
        on_complete=lambda: events.append(("complete", "")),
        on_error=lambda text: events.append(("error", text)),
        on_close=lambda: events.append(("close", "")),
        on_usage=lambda usage: events.append(("usage", usage)),
    )
    conversation.connect()
    conversation.send_audio_frame(b"\x01\x02")
    conversation.commit()

    assert captured["model"] == "qwen-audio-3.0-asr-flash-streaming"
    assert captured["format"] == "pcm"
    assert captured["sample_rate"] == 16000
    assert captured["language_hints"] == ["en"]
    assert captured["raw_input"] == {
        "context": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Prefer product names."}
                ],
            }
        ]
    }
    assert len(captured["frames"]) == 1
    assert captured["frames"][0].startswith(b"\x01\x02")
    assert len(captured["frames"][0]) == 1280
    assert ("partial", "hello world") in events
    assert ("final", "hello world.") in events
    assert ("usage", {"duration": 1}) in events
    assert events[-2:] == [("complete", ""), ("close", "")]


def test_recognition_adapter_coalesces_macos_blocks_and_flushes_tail(monkeypatch):
    import vocal_more.infrastructure.asr.qwen_audio_streaming as streaming

    frames = []

    class FakeRecognition:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

        def send_audio_frame(self, frame):
            frames.append(frame)

        def stop(self):
            pass

    monkeypatch.setattr(streaming, "Recognition", FakeRecognition)
    conversation = streaming.QwenAudioStreamingConversation(
        model="qwen-audio-3.0-asr-flash-streaming",
        sample_rate=16000,
        language=None,
        context_instruction="",
        on_ready=lambda: None,
        on_partial=lambda _text: None,
        on_final=lambda _text: None,
        on_complete=lambda: None,
        on_error=lambda _text: None,
        on_close=lambda: None,
    )

    conversation.send_audio_frame(b"a" * 1280)
    conversation.send_audio_frame(b"b" * 1280)
    assert frames == []
    conversation.send_audio_frame(b"c" * 1280)
    assert [len(frame) for frame in frames] == [3200]
    conversation.commit()
    assert [len(frame) for frame in frames] == [3200, 1280]
    assert frames[-1][:640] == b"c" * 640
    assert frames[-1][640:] == b"\0" * 640


def test_recognition_adapter_retries_cleanup_after_stop_failure(monkeypatch):
    import vocal_more.infrastructure.asr.qwen_audio_streaming as streaming

    captured = {"frames": [], "stop_calls": 0}

    class FakeRecognition:
        def __init__(self, **_kwargs):
            pass

        def send_audio_frame(self, frame):
            captured["frames"].append(frame)

        def stop(self):
            captured["stop_calls"] += 1
            if captured["stop_calls"] == 1:
                raise RuntimeError("temporary stop failure")

    monkeypatch.setattr(streaming, "Recognition", FakeRecognition)
    conversation = streaming.QwenAudioStreamingConversation(
        model="qwen-audio-3.0-asr-flash-streaming",
        sample_rate=16000,
        language=None,
        context_instruction="",
        on_ready=lambda: None,
        on_partial=lambda _text: None,
        on_final=lambda _text: None,
        on_complete=lambda: None,
        on_error=lambda _text: None,
        on_close=lambda: None,
    )
    conversation.send_audio_frame(b"tail")

    with pytest.raises(RuntimeError, match="temporary stop failure"):
        conversation.commit()
    conversation.close()

    assert captured["stop_calls"] == 2
    assert captured["frames"][0].startswith(b"tail")
    assert len(captured["frames"][0]) == 1280


def test_asr_engine_builds_recognition_protocol_instead_of_omni(monkeypatch):
    import vocal_more.core.asr_engine as asr_engine

    captured = {}

    class FakeConversation:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def connect(self):
            captured["on_ready"]()

        def close(self):
            pass

    monkeypatch.setattr(asr_engine, "QwenAudioStreamingConversation", FakeConversation)
    monkeypatch.setattr(
        asr_engine,
        "OmniRealtimeConversation",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Omni protocol used")),
    )

    engine = object.__new__(asr_engine.ASREngine)
    engine._session_config = SimpleNamespace(
        audio=SimpleNamespace(sample_rate=16000),
        asr=SimpleNamespace(language="auto"),
    )
    engine._close_conversation = lambda _conversation: None
    callback = asr_engine.StreamingASRCallback()
    conversation = engine._establish_conversation(
        "qwen-audio-3.0-asr-flash-streaming",
        {"protocol": "audio_recognition"},
        callback,
        context_instruction="context",
        session_config=engine._session_config,
    )

    assert isinstance(conversation, FakeConversation)
    assert captured["model"] == "qwen-audio-3.0-asr-flash-streaming"
    assert captured["context_instruction"] == "context"
    callback.close()


def test_batch_engine_routes_qwen_audio_to_recognition_protocol():
    from vocal_more.core import asr_engine

    engine = object.__new__(asr_engine.BatchASREngine)
    engine.config = SimpleNamespace(
        api_key="test-key",
        asr=SimpleNamespace(
            model="qwen-audio-3.0-asr-flash-streaming",
            backend="realtime_ws",
        )
    )
    engine._last_metering = None
    engine._audio_duration_seconds = lambda _audio: 1.0
    captured = {}

    def transcribe_recognition(audio, **kwargs):
        captured["audio"] = audio
        captured.update(kwargs)
        return "recognized"

    engine._transcribe_audio_recognition = transcribe_recognition
    engine._transcribe_realtime_ws = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("Omni realtime route used")
    )

    result = engine.transcribe(b"pcm", context_instruction="context")

    assert result == "recognized"
    assert captured["audio"] == b"pcm"
    assert captured["model_override"] == "qwen-audio-3.0-asr-flash-streaming"
    assert captured["context_instruction"] == "context"


def test_streaming_fallback_switches_new_model_to_legacy_short_file():
    from vocal_more.core import asr_engine

    engine = object.__new__(asr_engine.ASREngine)
    engine._session_model_id = "qwen-audio-3.0-asr-flash-streaming"
    engine._context_instruction = "Prefer Vocal More."
    captured = {}

    class FakeBatchEngine:
        def _supports_short_file(self, _pcm_data):
            return True

        def transcribe(self, pcm_data, **kwargs):
            captured["pcm_data"] = pcm_data
            captured.update(kwargs)
            return "fallback result"

    engine._batch_fallback = FakeBatchEngine()

    result = engine._transcribe_batch_fallback(b"pcm")

    assert result == "fallback result"
    assert captured == {
        "pcm_data": b"pcm",
        "model_override": "qwen3-asr-flash",
        "context_instruction": "Prefer Vocal More.",
    }


def test_streaming_fallback_chunks_long_audio_into_legacy_short_file_calls():
    from vocal_more.core import asr_engine

    engine = object.__new__(asr_engine.ASREngine)
    engine._session_model_id = "qwen-audio-3.0-asr-flash-streaming"
    engine._context_instruction = ""
    calls = []

    class FakeBatchEngine:
        def _supports_short_file(self, _pcm_data):
            return False

        def _split_audio_for_batch(self, pcm_data, *, max_duration_seconds):
            assert pcm_data == b"long pcm"
            assert max_duration_seconds == asr_engine.SHORT_FILE_MAX_DURATION_SECONDS
            return [b"chunk one", b"chunk two"]

        def transcribe(self, pcm_data, **kwargs):
            calls.append((pcm_data, kwargs))
            return "first" if pcm_data == b"chunk one" else "second"

    engine._batch_fallback = FakeBatchEngine()

    result = engine._transcribe_batch_fallback(b"long pcm")

    assert result == "first\nsecond"
    assert calls == [
        (
            b"chunk one",
            {"model_override": "qwen3-asr-flash", "allow_chunking": False},
        ),
        (
            b"chunk two",
            {"model_override": "qwen3-asr-flash", "allow_chunking": False},
        ),
    ]


def test_batch_recognition_replay_is_paced_and_keeps_partial_only_result(monkeypatch):
    import vocal_more.core.asr_engine as asr_engine

    sleeps = []

    class FakeConversation:
        def __init__(self, **kwargs):
            self.on_partial = kwargs["on_partial"]
            self.on_complete = kwargs["on_complete"]

        def connect(self):
            pass

        def send_audio_frame(self, _frame):
            self.on_partial("latest partial")

        def commit(self):
            self.on_complete()

        def close(self):
            pass

    monkeypatch.setattr(asr_engine, "QwenAudioStreamingConversation", FakeConversation)
    monkeypatch.setattr(asr_engine.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        asr_engine,
        "_finalize_trace",
        lambda trace, source: setattr(trace, "result_source", source),
    )

    engine = object.__new__(asr_engine.BatchASREngine)
    engine.config = SimpleNamespace(
        enable_polish=False,
        audio=SimpleNamespace(sample_rate=16000),
        asr=SimpleNamespace(model="qwen-audio-3.0-asr-flash-streaming", language="auto"),
    )
    engine._build_debug_trace = lambda **_kwargs: SimpleNamespace(
        partial_texts=[],
        final_transcripts=[],
        error="",
        usage={},
        recognition_timed_out=False,
        result_text="",
        result_source="",
    )
    engine._finalize_trace_billing = lambda _trace: None
    engine._dump_debug_artifacts = lambda _audio, _trace: None

    audio = b"x" * (asr_engine.REALTIME_CHUNK_SIZE + 2)
    result = engine._transcribe_audio_recognition(audio)

    assert result == "latest partial"
    assert sleeps == [
        asr_engine.REALTIME_CHUNK_SIZE / (16000 * asr_engine.PCM_SAMPLE_WIDTH_BYTES)
    ]


def test_streaming_stop_empty_recognition_uses_legacy_fallback():
    from vocal_more.core import asr_engine

    engine = asr_engine.ASREngine()
    model = "qwen-audio-3.0-asr-flash-streaming"
    engine._session_config = deepcopy(engine.config)
    engine._session_config.asr.model = model
    engine._session_model_id = model
    engine._context_instruction = ""
    engine._batch_fallback.config = engine._session_config
    engine._is_running = True
    engine._accepting_audio = True
    engine._session_ready = True
    engine._connect_failed = False
    engine._connect_done = asr_engine.threading.Event()
    engine._connect_done.set()
    engine._conversation_model_id = model
    engine._conversation_is_clean = True
    engine._active_trace = engine._batch_fallback._build_debug_trace(
        backend="realtime_ws",
        model=model,
        audio_data=None,
        corpus_text=None,
        request_mode="streaming",
    )
    callback = asr_engine.StreamingASRCallback()
    callback.set_debug_trace(engine._active_trace)
    engine._callback = callback

    class EmptyConversation:
        def commit(self):
            callback.recognition_complete()

        def create_response(self):
            raise AssertionError("Recognition protocol has no response channel")

        def end_session(self, timeout=5):
            del timeout

        def close(self):
            pass

    engine._conversation = EmptyConversation()
    calls = []

    def fake_transcribe(pcm_data, **kwargs):
        calls.append((pcm_data, kwargs))
        return "legacy fallback result"

    engine._batch_fallback.transcribe = fake_transcribe
    engine._batch_fallback.get_last_metering = lambda: None
    pcm_data = b"\x01\x00" * 4000

    try:
        result = engine.stop(pcm_data=pcm_data)
    finally:
        engine.close()

    assert result == "legacy fallback result"
    assert calls == [
        (
            pcm_data,
            {"model_override": "qwen3-asr-flash"},
        )
    ]
