"""Tests for RecordingStore."""

import base64
import json
import os
import subprocess
import threading
import wave
import zlib
from pathlib import Path

import pytest

from vocal_more.core.recording_store import MAX_RECORDINGS, RecordingStore


def _make_pcm(duration_sec: float = 1.0) -> bytes:
    """Generate silent PCM data for a given duration."""
    num_samples = int(16000 * duration_sec)
    return b"\x00\x00" * num_samples


class _FakeLosslessCodec:
    def encode_wav_to_flac(self, source: Path, destination: Path) -> None:
        destination.write_bytes(zlib.compress(source.read_bytes(), level=9))

    def decode_flac_to_wav(self, source: Path, destination: Path) -> None:
        destination.write_bytes(zlib.decompress(source.read_bytes()))


class _CorruptingCodec(_FakeLosslessCodec):
    def decode_flac_to_wav(self, source: Path, destination: Path) -> None:
        super().decode_flac_to_wav(source, destination)
        with wave.open(str(destination), "rb") as wav_file:
            params = wav_file.getparams()
            pcm = bytearray(wav_file.readframes(wav_file.getnframes()))
        pcm[0:2] = b"\x01\x00"
        with wave.open(str(destination), "wb") as wav_file:
            wav_file.setparams(params)
            wav_file.writeframes(bytes(pcm))


class _FailingCodec(_FakeLosslessCodec):
    def __init__(self, failure_stage: str) -> None:
        self._failure_stage = failure_stage

    def encode_wav_to_flac(self, source: Path, destination: Path) -> None:
        if self._failure_stage == "encode":
            raise RuntimeError("injected encode failure")
        super().encode_wav_to_flac(source, destination)

    def decode_flac_to_wav(self, source: Path, destination: Path) -> None:
        if self._failure_stage == "decode":
            raise RuntimeError("injected decode failure")
        super().decode_flac_to_wav(source, destination)


class _NonShrinkingCodec:
    def __init__(self) -> None:
        self.decode_calls = 0

    def encode_wav_to_flac(self, source: Path, destination: Path) -> None:
        destination.write_bytes(source.read_bytes() + b"not-smaller")

    def decode_flac_to_wav(self, source: Path, destination: Path) -> None:
        self.decode_calls += 1
        raise AssertionError("a non-shrinking archive must not be decoded")


class _BlockingCodec(_FakeLosslessCodec):
    def __init__(self) -> None:
        self.encode_started = threading.Event()
        self.allow_encode = threading.Event()
        self.worker_name: str | None = None
        self.encode_calls = 0

    def encode_wav_to_flac(self, source: Path, destination: Path) -> None:
        self.encode_calls += 1
        self.worker_name = threading.current_thread().name
        self.encode_started.set()
        if not self.allow_encode.wait(timeout=2):
            raise RuntimeError("test did not release blocked encoder")
        super().encode_wav_to_flac(source, destination)


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
        assert rec["error"] is None
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
        assert store.update(rec_id, "success", "hello world") is True

        rec = store.list_recordings()[0]
        assert rec["status"] == "success"
        assert rec["transcript"] == "hello world"

    def test_update_status_only(self, store):
        rec_id = store.save(_make_pcm(), "walkie_talkie", "m")
        store.update(rec_id, "failed")

        rec = store.list_recordings()[0]
        assert rec["status"] == "failed"
        assert rec["transcript"] is None
        assert rec["error"] is None

    def test_update_persists_error_and_clears_on_success(self, store):
        rec_id = store.save(_make_pcm(), "walkie_talkie", "m")
        store.update(rec_id, "failed", error="boom")

        rec = store.list_recordings()[0]
        assert rec["status"] == "failed"
        assert rec["error"] == "boom"

        store.update(rec_id, "success", "hello world", error=None)
        rec = store.list_recordings()[0]
        assert rec["status"] == "success"
        assert rec["transcript"] == "hello world"
        assert rec["error"] is None

    def test_update_persists_billing(self, store):
        rec_id = store.save(_make_pcm(), "walkie_talkie", "m")
        billing = {
            "currency": "CNY",
            "total_cost_cny": 0.00123,
            "asr_cost_cny": 0.00123,
            "polish_cost_cny": 0.0,
            "estimated": False,
        }

        store.update(rec_id, "success", "hello world", billing=billing)

        rec = store.list_recordings()[0]
        assert rec["billing"] == billing

    def test_update_persists_meeting_notes(self, store):
        rec_id = store.save(_make_pcm(), "realtime_long", "m")
        meeting_notes = {
            "speaker_count": 2,
            "speakers": [
                {"id": "speaker_1", "label": "Speaker 1"},
                {"id": "speaker_2", "label": "Speaker 2"},
            ],
            "segments": [
                {
                    "speaker": "speaker_1",
                    "speaker_label": "Speaker 1",
                    "text": "We should ship this first.",
                },
                {
                    "speaker": "speaker_2",
                    "speaker_label": "Speaker 2",
                    "text": "Agreed.",
                },
            ],
            "transcript": "Speaker 1: We should ship this first.\nSpeaker 2: Agreed.",
        }

        store.update(rec_id, "success", meeting_notes["transcript"], meeting=meeting_notes)

        rec = store.list_recordings()[0]
        assert rec["meeting"] == meeting_notes

    def test_update_nonexistent_id_is_noop(self, store):
        store.save(_make_pcm(), "walkie_talkie", "m")
        assert store.update("nonexistent", "success", "text") is False
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


class TestLosslessHistoryCompression:
    @pytest.mark.skipif(
        not os.path.exists("/usr/bin/afconvert"),
        reason="macOS AudioToolbox is required",
    )
    def test_macos_flac_codec_round_trips_pcm_exactly(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            auto_compact=False,
        )
        pcm = _make_pcm(0.5)
        rec_id = store.save(pcm, "walkie_talkie", "m")
        store.update(rec_id, "success", "text")
        source_path = store.get_recording_path(rec_id)
        assert source_path is not None

        # Merely finding afconvert is insufficient: restricted macOS
        # environments may expose the binary while denying its FLAC encoder.
        # Probe the platform independently so a product-code regression still
        # fails whenever the system capability is actually available.
        probe_path = tmp_path / "afconvert-capability-probe.flac"
        probe = subprocess.run(
            [
                "/usr/bin/afconvert",
                str(source_path),
                str(probe_path),
                "-f",
                "flac",
                "-d",
                "flac",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0 or not probe_path.exists():
            store.close()
            details = (probe.stderr or probe.stdout or "FLAC encoder unavailable").strip()
            pytest.skip(f"afconvert FLAC encoder is unavailable: {details}")
        probe_path.unlink()

        result = store.compact_history(keep_recent=0)

        assert result["compressed_count"] == 1
        assert store.get_recording_path(rec_id).suffix == ".flac"
        assert store.get_pcm_data(rec_id) == pcm
        store.close()

    def test_compacts_only_terminal_recordings_older_than_recent_window(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        ids = []
        pcm_by_id = {}
        for index in range(5):
            pcm = _make_pcm(0.2 + index * 0.05)
            rec_id = store.save(pcm, "walkie_talkie", "m")
            store.update(rec_id, "success", f"text {index}")
            ids.append(rec_id)
            pcm_by_id[rec_id] = pcm

        result = store.compact_history(keep_recent=2)

        assert result["compressed_count"] == 3
        assert result["error_count"] == 0
        assert result["bytes_saved"] > 0
        recordings = {
            recording["id"]: recording
            for recording in store.list_recordings()
        }
        for rec_id in ids[:3]:
            assert recordings[rec_id]["filename"].endswith(".flac")
            assert recordings[rec_id]["storage_format"] == "flac"
            assert store.get_pcm_data(rec_id) == pcm_by_id[rec_id]
            decoded = base64.b64decode(store.get_wav_base64(rec_id))
            assert decoded.startswith(b"RIFF")
        for rec_id in ids[-2:]:
            assert recordings[rec_id]["filename"].endswith(".wav")
            assert recordings[rec_id]["storage_format"] == "wav"

        assert not list((tmp_path / "recordings").glob("*.tmp"))

    def test_compacted_recordings_reload_play_retry_and_delete(self, tmp_path):
        recordings_dir = tmp_path / "recordings"
        store = RecordingStore(
            recordings_dir=str(recordings_dir),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        rec_id = store.save(_make_pcm(0.5), "walkie_talkie", "m")
        store.update(rec_id, "failed", error="network")

        assert store.compact_history(keep_recent=0)["compressed_count"] == 1

        reloaded = RecordingStore(
            recordings_dir=str(recordings_dir),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        path = reloaded.get_recording_path(rec_id)
        assert path is not None and path.suffix == ".flac"
        assert reloaded.get_pcm_data(rec_id) == _make_pcm(0.5)
        assert reloaded.delete(rec_id) is True
        assert not path.exists()

    def test_pending_recordings_are_not_compacted(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        rec_id = store.save(_make_pcm(0.5), "walkie_talkie", "m")

        result = store.compact_history(keep_recent=0)

        assert result["compressed_count"] == 0
        assert store.get_recording_path(rec_id).suffix == ".wav"

    def test_failed_pcm_verification_keeps_original_wav(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_CorruptingCodec(),
            auto_compact=False,
        )
        rec_id = store.save(_make_pcm(0.5), "walkie_talkie", "m")
        store.update(rec_id, "success", "text")

        result = store.compact_history(keep_recent=0)

        assert result["compressed_count"] == 0
        assert result["error_count"] == 1
        assert store.get_recording_path(rec_id).suffix == ".wav"
        assert store.get_pcm_data(rec_id) == _make_pcm(0.5)

    @pytest.mark.parametrize("failure_stage", ["encode", "decode"])
    def test_codec_failure_keeps_original_wav_and_index(
        self,
        tmp_path,
        failure_stage,
    ):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FailingCodec(failure_stage),
            auto_compact=False,
        )
        pcm = _make_pcm(0.5)
        rec_id = store.save(pcm, "walkie_talkie", "m")
        store.update(rec_id, "success", "text")
        original_path = store.get_recording_path(rec_id)
        persisted_before = store._index_path.read_bytes()

        result = store.compact_history(keep_recent=0)

        assert result["compressed_count"] == 0
        assert result["error_count"] == 1
        assert store.get_recording_path(rec_id) == original_path
        assert original_path is not None and original_path.exists()
        assert store.get_pcm_data(rec_id) == pcm
        assert store._index_path.read_bytes() == persisted_before
        assert not list(store._dir.glob("*.flac"))
        store.close()

    def test_non_shrinking_archive_is_skipped_without_replacing_wav(self, tmp_path):
        codec = _NonShrinkingCodec()
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=codec,
            auto_compact=False,
        )
        pcm = _make_pcm(0.5)
        rec_id = store.save(pcm, "walkie_talkie", "m")
        store.update(rec_id, "success", "text")
        original_path = store.get_recording_path(rec_id)

        result = store.compact_history(keep_recent=0)

        assert result["compressed_count"] == 0
        assert result["error_count"] == 0
        assert result["skipped_count"] == 1
        assert codec.decode_calls == 0
        assert store.get_recording_path(rec_id) == original_path
        assert original_path is not None and original_path.exists()
        assert store.get_pcm_data(rec_id) == pcm
        assert not list(store._dir.glob("*.flac"))
        store.close()

    def test_index_save_failure_rolls_back_archive_commit(self, tmp_path, monkeypatch):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        pcm = _make_pcm(0.5)
        rec_id = store.save(pcm, "walkie_talkie", "m")
        store.update(rec_id, "success", "text")
        original_path = store.get_recording_path(rec_id)
        persisted_before = store._index_path.read_bytes()
        monkeypatch.setattr(store, "_save_index", lambda: False)

        result = store.compact_history(keep_recent=0)

        assert result["compressed_count"] == 0
        assert result["error_count"] == 1
        assert store.get_recording_path(rec_id) == original_path
        assert original_path is not None and original_path.exists()
        assert store.get_pcm_data(rec_id) == pcm
        assert store._index_path.read_bytes() == persisted_before
        assert not list(store._dir.glob("*.flac"))
        store.close()

    def test_unsafe_recording_id_cannot_escape_archive_directory(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        rec_id = store.save(_make_pcm(0.5), "walkie_talkie", "m")
        store.update(rec_id, "success", "text")
        source_path = store.get_recording_path(rec_id)
        escaped_path = tmp_path / "escaped.flac"
        with store._lock:
            store._recordings[0]["id"] = "../escaped"
            store._save_index()

        result = store.compact_history(keep_recent=0)

        assert result["compressed_count"] == 0
        assert result["skipped_count"] == 1
        assert source_path is not None and source_path.exists()
        assert not escaped_path.exists()
        store.close()

    def test_archive_rejects_id_and_filename_stem_mismatch(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        rec_id = store.save(_make_pcm(0.5), "walkie_talkie", "m")
        store.update(rec_id, "success", "text")
        source_path = store.get_recording_path(rec_id)
        with store._lock:
            store._recordings[0]["id"] = "different-safe-id"
            store._save_index()

        result = store.compact_history(keep_recent=0)

        assert result["compressed_count"] == 0
        assert result["skipped_count"] == 1
        assert source_path is not None and source_path.exists()
        assert not (store._dir / "different-safe-id.flac").exists()
        store.close()

    def test_pcm_read_retries_after_compaction_replaces_resolved_wav(
        self,
        tmp_path,
        monkeypatch,
    ):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        pcm = _make_pcm(0.5)
        rec_id = store.save(pcm, "walkie_talkie", "m")
        store.update(rec_id, "success", "text")
        original_read = store._read_wav_bytes
        read_started = threading.Event()
        allow_read = threading.Event()

        def read_after_compaction(audio_path: Path) -> bytes:
            if audio_path.suffix.lower() == ".wav" and not read_started.is_set():
                read_started.set()
                if not allow_read.wait(timeout=2):
                    raise RuntimeError("test did not release blocked reader")
            return original_read(audio_path)

        monkeypatch.setattr(store, "_read_wav_bytes", read_after_compaction)
        result: list[bytes | None] = []
        reader = threading.Thread(
            target=lambda: result.append(store.get_pcm_data(rec_id)),
            name="test-recording-read",
        )
        reader.start()
        assert read_started.wait(timeout=1)

        compacted = store.compact_history(keep_recent=0)
        allow_read.set()
        reader.join(timeout=2)

        assert compacted["compressed_count"] == 1
        assert not reader.is_alive()
        assert result == [pcm]
        store.close()

    def test_storage_summary_reports_archives_and_saved_bytes(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        rec_id = store.save(_make_pcm(1.0), "walkie_talkie", "m")
        store.update(rec_id, "success", "text")
        store.compact_history(keep_recent=0)

        summary = store.storage_summary()

        assert summary["recording_count"] == 1
        assert summary["compressed_count"] == 1
        assert summary["bytes_saved"] > 0
        assert summary["stored_bytes"] < summary["original_bytes"]

    def test_scheduled_compaction_is_owned_and_joinable(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        for index in range(4):
            rec_id = store.save(_make_pcm(0.2), "walkie_talkie", "m")
            store.update(rec_id, "success", f"text {index}")

        task = store.schedule_history_compaction()
        task.join(timeout=2)

        assert store.storage_summary()["compressed_count"] == 1
        store.close()

    def test_scheduling_without_candidates_does_not_start_work(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
            auto_compact=False,
        )
        rec_id = store.save(_make_pcm(0.2), "walkie_talkie", "m")
        store.update(rec_id, "success", "text")

        assert store.schedule_history_compaction(keep_recent=3) is None
        assert store.storage_summary()["compressed_count"] == 0
        store.close()

    def test_fourth_terminal_recording_triggers_background_compaction(self, tmp_path):
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=_FakeLosslessCodec(),
        )
        for index in range(4):
            rec_id = store.save(_make_pcm(0.2), "walkie_talkie", "m")
            store.update(rec_id, "success", f"text {index}")

        store.close()

        assert store.storage_summary()["compressed_count"] == 1

    def test_close_waits_for_owned_worker_and_rejects_later_scheduling(
        self,
        tmp_path,
    ):
        codec = _BlockingCodec()
        store = RecordingStore(
            recordings_dir=str(tmp_path / "recordings"),
            audio_codec=codec,
            auto_compact=False,
        )
        rec_id = store.save(_make_pcm(0.5), "walkie_talkie", "m")
        store.update(rec_id, "success", "text")
        task = store.schedule_history_compaction(keep_recent=0)
        assert task is not None
        assert codec.encode_started.wait(timeout=1)

        close_started = threading.Event()
        close_finished = threading.Event()

        def close_store() -> None:
            close_started.set()
            store.close()
            close_finished.set()

        close_thread = threading.Thread(
            target=close_store,
            name="test-recording-store-close",
        )
        close_thread.start()
        assert close_started.wait(timeout=1)
        assert not close_finished.wait(timeout=0.05)

        codec.allow_encode.set()
        close_thread.join(timeout=2)

        assert close_finished.is_set()
        assert task.done()
        assert codec.worker_name is not None
        assert codec.worker_name.startswith("vocal-more-recording-archive")

        later_id = store.save(_make_pcm(0.5), "walkie_talkie", "m")
        store.update(later_id, "success", "later")
        assert store.schedule_history_compaction(keep_recent=0) is None
        assert codec.encode_calls == 1


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
        store1.update(rec_id, "failed", error="temporary failure")
        store1.update(rec_id, "success", "test text", error=None)

        store2 = RecordingStore(recordings_dir=str(tmp_path / "recs"))
        recs = store2.list_recordings()
        assert len(recs) == 1
        assert recs[0]["transcript"] == "test text"
        assert recs[0]["error"] is None

    def test_reloads_legacy_entries_without_error_field(self, tmp_path):
        recs_dir = tmp_path / "recs"
        recs_dir.mkdir(parents=True)
        legacy_entry = [{
            "id": "legacy-1",
            "filename": "legacy-1.wav",
            "timestamp": "2026-04-16T12:00:00",
            "duration_seconds": 1.2,
            "mode": "walkie_talkie",
            "asr_model": "model-a",
            "language": "zh",
            "status": "failed",
            "transcript": None,
        }]
        (recs_dir / "recordings.json").write_text(
            json.dumps(legacy_entry),
            encoding="utf-8",
        )
        with wave.open(str(recs_dir / "legacy-1.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(_make_pcm(0.1))

        store = RecordingStore(recordings_dir=str(recs_dir))
        rec = store.list_recordings()[0]
        assert rec["error"] is None
        assert rec["billing"] is None
        assert rec["meeting"] is None

    def test_handles_corrupt_index(self, tmp_path):
        recs_dir = tmp_path / "recs"
        recs_dir.mkdir(parents=True)
        (recs_dir / "recordings.json").write_text("not json!!!")

        store = RecordingStore(recordings_dir=str(recs_dir))
        assert len(store.list_recordings()) == 0
        assert (recs_dir / "recordings.json.bak").exists()

    def test_rejects_absolute_filename_from_index(self, tmp_path):
        recs_dir = tmp_path / "recs"
        recs_dir.mkdir(parents=True)
        external_wav = tmp_path / "outside.wav"
        with wave.open(str(external_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(_make_pcm(0.1))

        (recs_dir / "recordings.json").write_text(
            json.dumps(
                [
                    {
                        "id": "evil-abs",
                        "filename": str(external_wav),
                        "timestamp": "2026-04-16T12:00:00",
                        "duration_seconds": 0.1,
                        "mode": "walkie_talkie",
                        "asr_model": "model-a",
                        "language": "zh",
                        "status": "success",
                        "transcript": "secret",
                        "error": None,
                    }
                ]
            ),
            encoding="utf-8",
        )

        store = RecordingStore(recordings_dir=str(recs_dir))

        assert store.list_recordings() == []
        assert store.get_recording_path("evil-abs") is None
        assert external_wav.exists()

    def test_rejects_parent_relative_filename_from_index(self, tmp_path):
        recs_dir = tmp_path / "recs"
        recs_dir.mkdir(parents=True)
        external_wav = tmp_path / "outside-parent.wav"
        with wave.open(str(external_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(_make_pcm(0.1))

        (recs_dir / "recordings.json").write_text(
            json.dumps(
                [
                    {
                        "id": "evil-parent",
                        "filename": "../outside-parent.wav",
                        "timestamp": "2026-04-16T12:00:00",
                        "duration_seconds": 0.1,
                        "mode": "walkie_talkie",
                        "asr_model": "model-a",
                        "language": "zh",
                        "status": "success",
                        "transcript": "secret",
                        "error": None,
                    }
                ]
            ),
            encoding="utf-8",
        )

        store = RecordingStore(recordings_dir=str(recs_dir))

        assert store.list_recordings() == []
        assert store.get_recording_path("evil-parent") is None
        assert external_wav.exists()
