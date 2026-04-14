"""Tests for RecordingStore."""

import json
import wave
from pathlib import Path

import pytest

from vocal_more.core.recording_store import MAX_RECORDINGS, RecordingStore


def _make_pcm(duration_sec: float = 1.0) -> bytes:
    """Generate silent PCM data for a given duration."""
    num_samples = int(16000 * duration_sec)
    return b"\x00\x00" * num_samples


@pytest.fixture
def store(tmp_path):
    return RecordingStore(recordings_dir=str(tmp_path / "recordings"))


class TestSave:
    def test_save_creates_wav_and_index(self, store):
        pcm = _make_pcm(2.0)
        rec_id = store.save(pcm, "walkie_talkie", "qwen-omni-turbo")

        assert rec_id
        recordings = store.list_recordings()
        assert len(recordings) == 1
        rec = recordings[0]
        assert rec["id"] == rec_id
        assert rec["mode"] == "walkie_talkie"
        assert rec["asr_model"] == "qwen-omni-turbo"
        assert rec["status"] == "pending"
        assert rec["transcript"] is None
        assert rec["duration_seconds"] == pytest.approx(2.0, abs=0.2)

        wav_path = Path(store._dir) / rec["filename"]
        assert wav_path.exists()

        with wave.open(str(wav_path), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 32000

    def test_save_persists_index_to_disk(self, store):
        store.save(_make_pcm(), "realtime_long", "model-a")

        raw = json.loads(store._index_path.read_text(encoding="utf-8"))
        assert len(raw) == 1
        assert raw[0]["mode"] == "realtime_long"

    def test_multiple_saves_same_second_get_unique_ids(self, store):
        ids = [store.save(_make_pcm(0.1), "walkie_talkie", "m") for _ in range(5)]
        assert len(set(ids)) == 5


class TestUpdate:
    def test_update_status_and_transcript(self, store):
        rec_id = store.save(_make_pcm(), "walkie_talkie", "m")
        store.update(rec_id, "success", "hello world")

        rec = store.list_recordings()[0]
        assert rec["status"] == "success"
        assert rec["transcript"] == "hello world"

    def test_update_status_only(self, store):
        rec_id = store.save(_make_pcm(), "walkie_talkie", "m")
        store.update(rec_id, "failed")

        rec = store.list_recordings()[0]
        assert rec["status"] == "failed"
        assert rec["transcript"] is None

    def test_update_nonexistent_id_is_noop(self, store):
        store.save(_make_pcm(), "walkie_talkie", "m")
        store.update("nonexistent", "success", "text")
        rec = store.list_recordings()[0]
        assert rec["status"] == "pending"


class TestListRecordings:
    def test_list_returns_newest_first(self, store):
        store.save(_make_pcm(), "walkie_talkie", "m")
        store.save(_make_pcm(), "realtime_long", "m")

        recs = store.list_recordings()
        assert len(recs) == 2
        assert recs[0]["mode"] == "realtime_long"
        assert recs[1]["mode"] == "walkie_talkie"


class TestGetData:
    def test_get_wav_base64(self, store):
        import base64

        pcm = _make_pcm(0.5)
        rec_id = store.save(pcm, "walkie_talkie", "m")
        b64 = store.get_wav_base64(rec_id)
        assert b64 is not None
        decoded = base64.b64decode(b64)
        assert decoded[:4] == b"RIFF"

    def test_get_pcm_data(self, store):
        pcm = _make_pcm(0.5)
        rec_id = store.save(pcm, "walkie_talkie", "m")
        result = store.get_pcm_data(rec_id)
        assert result == pcm

    def test_get_nonexistent_returns_none(self, store):
        assert store.get_wav_base64("nope") is None
        assert store.get_pcm_data("nope") is None


class TestDelete:
    def test_delete_removes_file_and_entry(self, store):
        rec_id = store.save(_make_pcm(), "walkie_talkie", "m")
        recs = store.list_recordings()
        wav_path = Path(store._dir) / recs[0]["filename"]
        assert wav_path.exists()

        assert store.delete(rec_id) is True
        assert not wav_path.exists()
        assert len(store.list_recordings()) == 0

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete("nope") is False


class TestRotation:
    def test_enforces_max_recordings(self, store):
        ids = []
        for i in range(MAX_RECORDINGS + 3):
            ids.append(store.save(_make_pcm(0.1), "walkie_talkie", "m"))

        recs = store.list_recordings()
        assert len(recs) == MAX_RECORDINGS

        rec_ids = [r["id"] for r in recs]
        for old_id in ids[:3]:
            assert old_id not in rec_ids

    def test_oldest_wav_files_deleted(self, store):
        first_id = store.save(_make_pcm(0.1), "walkie_talkie", "m")
        first_file = store.list_recordings()[-1]["filename"]
        first_path = Path(store._dir) / first_file

        for _ in range(MAX_RECORDINGS):
            store.save(_make_pcm(0.1), "walkie_talkie", "m")

        assert not first_path.exists()


class TestPersistence:
    def test_reloads_from_disk(self, tmp_path):
        store1 = RecordingStore(recordings_dir=str(tmp_path / "recs"))
        rec_id = store1.save(_make_pcm(), "walkie_talkie", "m")
        store1.update(rec_id, "success", "test text")

        store2 = RecordingStore(recordings_dir=str(tmp_path / "recs"))
        recs = store2.list_recordings()
        assert len(recs) == 1
        assert recs[0]["transcript"] == "test text"

    def test_handles_corrupt_index(self, tmp_path):
        recs_dir = tmp_path / "recs"
        recs_dir.mkdir(parents=True)
        (recs_dir / "recordings.json").write_text("not json!!!")

        store = RecordingStore(recordings_dir=str(recs_dir))
        assert len(store.list_recordings()) == 0
        assert (recs_dir / "recordings.json.bak").exists()
