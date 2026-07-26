from __future__ import annotations


def test_audio_replay_preserves_duration_only_in_paced_mode():
    from scripts.run_dictation_benchmark import feed_audio

    class Engine:
        def __init__(self):
            self.chunks = []

        def send_audio(self, chunk):
            self.chunks.append(chunk)

    pcm = b"\x01\x00" * 2000
    sleeps = []
    paced_engine = Engine()

    feed_audio(
        paced_engine,
        pcm,
        chunk_bytes=3200,
        paced=True,
        sleep=sleeps.append,
    )

    assert b"".join(paced_engine.chunks) == pcm
    assert sum(sleeps) == 0.125

    protocol_engine = Engine()
    sleeps.clear()
    feed_audio(
        protocol_engine,
        pcm,
        chunk_bytes=3200,
        paced=False,
        sleep=sleeps.append,
    )
    assert b"".join(protocol_engine.chunks) == pcm
    assert sleeps == []
