from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_meeting_mode_marks_saved_record_failed_when_processing_is_canceled(tmp_path):
    from vocal_more.core.recording_store import RecordingStore
    from vocal_more.modes.meeting import MeetingMode

    recorder = MagicMock()
    recorder.stop.return_value = b"\x00\x00" * 16000
    store = RecordingStore(str(tmp_path))
    mode_holder = {}

    class CancelingService:
        def generate(self, _pcm_data, *, on_stage=None):
            if on_stage is not None:
                on_stage("meeting_transcribing")
            mode_holder["mode"].cancel(reason="test_cancel")
            return SimpleNamespace(
                notes={
                    "status": "success",
                    "speaker_count": 1,
                    "speakers": [{"id": "speaker_1", "label": "Speaker 1"}],
                    "segments": [
                        {
                            "speaker": "speaker_1",
                            "speaker_label": "Speaker 1",
                            "text": "Should not publish",
                        }
                    ],
                    "transcript": "Speaker 1: Should not publish",
                },
                billing={"total_cost_cny": 0.001},
            )

    mode = MeetingMode(
        recording_store=store,
        recorder_factory=lambda **kwargs: recorder,
        service_factory=lambda: CancelingService(),
    )
    mode_holder["mode"] = mode
    mode._processing_executor.submit = lambda callback, *args, **kwargs: callback(*args, **kwargs)

    mode.on_hotkey_pressed()
    mode.on_hotkey_pressed()

    rec = store.list_recordings()[0]
    assert rec["status"] == "failed"
    assert rec["meeting"]["status"] == "failed"
    assert rec["meeting"]["reason"] == "canceled"
    assert rec["transcript"] is None


def test_meeting_mode_records_generates_two_stage_notes_and_opens_result(tmp_path):
    from vocal_more.core.recording_store import RecordingStore
    from vocal_more.modes.base_mode import ModeState
    from vocal_more.modes.meeting import MeetingMode

    recorder = MagicMock()
    recorder.stop.return_value = b"\x00\x00" * 16000
    recording_store = RecordingStore(str(tmp_path))
    service = MagicMock()
    service.generate.return_value = SimpleNamespace(
        notes={
            "status": "success",
            "speaker_count": 2,
            "speakers": [
                {"id": "speaker_1", "label": "Speaker 1"},
                {"id": "speaker_2", "label": "Speaker 2"},
            ],
            "segments": [
                {
                    "speaker": "speaker_1",
                    "speaker_label": "Speaker 1",
                    "text": "Hi",
                },
                {
                    "speaker": "speaker_2",
                    "speaker_label": "Speaker 2",
                    "text": "Hello",
                },
            ],
            "transcript": "Speaker 1: Hi\nSpeaker 2: Hello",
            "minutes": {
                "status": "success",
                "summary": "Greeting",
                "key_points": [],
                "action_items": [],
                "error": None,
            },
            "billing": {"transcript": {}, "minutes": {}},
        },
        billing={"total_cost_cny": 0.003},
    )

    def generate_notes(_pcm_data, *, on_stage=None):
        if on_stage is not None:
            on_stage("meeting_transcribing")
            on_stage("meeting_summarizing")
        return service.generate.return_value

    service.generate.side_effect = generate_notes
    stages: list[str] = []
    ready_ids: list[str] = []

    mode = MeetingMode(
        on_processing_stage=stages.append,
        on_meeting_result=ready_ids.append,
        recording_store=recording_store,
        recorder_factory=lambda **kwargs: recorder,
        service_factory=lambda: service,
    )
    mode._processing_executor.submit = lambda callback, *args, **kwargs: callback(*args, **kwargs)

    mode.on_hotkey_pressed()
    assert mode.state == ModeState.RECORDING
    mode.on_hotkey_pressed()

    recorder.start.assert_not_called()
    recorder.start_capture_session.assert_called_once()
    capture_config = recorder.start_capture_session.call_args.args[0]
    assert capture_config is not mode.config.audio
    assert capture_config.gain_mode == mode.config.audio.gain_mode
    recorder.stop.assert_called_once_with()
    service.generate.assert_called_once()
    rec = recording_store.list_recordings()[0]
    assert rec["mode"] == "meeting"
    assert rec["asr_model"] == "qwen3.5-omni-plus"
    assert rec["status"] == "success"
    assert rec["transcript"] == "Speaker 1: Hi\nSpeaker 2: Hello"
    assert rec["meeting"]["speaker_count"] == 2
    assert rec["billing"] == {"total_cost_cny": 0.003}
    assert "meeting_transcribing" in stages
    assert "meeting_summarizing" in stages
    assert ready_ids == [rec["id"]]
    assert mode.state == ModeState.IDLE


def test_meeting_mode_does_not_use_keyboard_paste_or_dictation_workflow():
    from vocal_more.modes.meeting import MeetingMode

    mode = MeetingMode(
        recording_store=MagicMock(),
        recorder_factory=lambda **kwargs: MagicMock(),
        service_factory=lambda: MagicMock(),
    )

    assert not hasattr(mode, "_keyboard")
    assert not hasattr(mode, "_workflow")
