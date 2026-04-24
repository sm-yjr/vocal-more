from __future__ import annotations

from types import SimpleNamespace


def _meeting_notes(text: str = "Speaker 1: Hi"):
    return SimpleNamespace(
        notes={
            "status": "success",
            "speaker_count": 1,
            "speakers": [{"id": "speaker_1", "label": "Speaker 1"}],
            "segments": [
                {
                    "speaker": "speaker_1",
                    "speaker_label": "Speaker 1",
                    "text": text.removeprefix("Speaker 1: "),
                }
            ],
            "transcript": text,
        },
        billing={"total_cost_cny": 0.001},
    )


def test_meeting_recording_job_dedupes_in_flight_recording(tmp_path):
    from vocal_more.application.meeting_jobs import MeetingNotesRecordingRunner
    from vocal_more.core.recording_store import RecordingStore

    store = RecordingStore(str(tmp_path))
    rec_id = store.save(b"\x00\x00" * 16000, "realtime_long", "m")
    nested_results = []

    class ReentrantService:
        calls = 0

        def generate(self, _pcm_data, *, on_stage=None):
            self.calls += 1
            nested_results.append(runner.generate_for_recording(rec_id))
            return _meeting_notes()

    service = ReentrantService()
    runner = MeetingNotesRecordingRunner(
        config=SimpleNamespace(asr=SimpleNamespace(language="auto")),
        recording_store=store,
        service_factory=lambda: service,
    )

    result = runner.generate_for_recording(rec_id)

    assert result.status == "success"
    assert nested_results[0].status == "already_running"
    assert service.calls == 1
    rec = store.list_recordings()[0]
    assert rec["meeting"]["status"] == "success"


def test_meeting_recording_job_marks_canceled_generation_failed(tmp_path):
    from vocal_more.application.meeting_jobs import MeetingNotesRecordingRunner
    from vocal_more.core.recording_store import RecordingStore

    store = RecordingStore(str(tmp_path))
    rec_id = store.save(b"\x00\x00" * 16000, "meeting", "m")
    should_abort = {"value": False}

    class CancelingService:
        def generate(self, _pcm_data, *, on_stage=None):
            should_abort["value"] = True
            return _meeting_notes("Speaker 1: Should not publish")

    runner = MeetingNotesRecordingRunner(
        config=SimpleNamespace(asr=SimpleNamespace(language="auto")),
        recording_store=store,
        service_factory=lambda: CancelingService(),
    )

    result = runner.generate_for_recording(
        rec_id,
        should_abort=lambda: should_abort["value"],
    )

    assert result.status == "canceled"
    rec = store.list_recordings()[0]
    assert rec["status"] == "failed"
    assert rec["meeting"]["status"] == "failed"
    assert rec["meeting"]["reason"] == "canceled"
    assert rec["transcript"] is None
