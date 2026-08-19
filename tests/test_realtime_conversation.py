from __future__ import annotations

import base64


def test_buffered_realtime_conversation_coalesces_and_flushes_tail_before_commit():
    from vocal_more.infrastructure.asr.realtime_conversation import (
        BufferedRealtimeConversation,
    )

    events = []

    class Conversation:
        def append_audio(self, audio_b64):
            events.append(("audio", base64.b64decode(audio_b64)))

        def commit(self):
            events.append(("commit", b""))

    buffered = BufferedRealtimeConversation(Conversation(), frame_bytes=3200)
    buffered.send_audio_frame(b"a" * 1280)
    buffered.send_audio_frame(b"b" * 1280)
    assert events == []

    buffered.send_audio_frame(b"c" * 1280)
    assert events == [("audio", b"a" * 1280 + b"b" * 1280 + b"c" * 640)]

    buffered.commit()
    assert events[1] == ("audio", b"c" * 640)
    assert events[2] == ("commit", b"")
