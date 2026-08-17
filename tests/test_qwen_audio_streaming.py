"""Tests for the dedicated Qwen Audio Recognition transport."""

from __future__ import annotations

from types import SimpleNamespace


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
    assert captured["raw_input"] == "Prefer product names."
    assert captured["frames"] == [b"\x01\x02"]
    assert ("partial", "hello world") in events
    assert ("final", "hello world.") in events
    assert ("usage", {"duration": 1}) in events
    assert events[-2:] == [("complete", ""), ("close", "")]


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
