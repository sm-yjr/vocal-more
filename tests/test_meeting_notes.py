from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_parse_meeting_transcript_response_normalizes_dual_speaker_json():
    from vocal_more.application.meeting_notes import parse_meeting_transcript_response

    notes = parse_meeting_transcript_response(
        """
        ```json
        {
          "title": "Roadmap sync",
          "summary": "Confirmed the beta scope.",
          "speakers": [
            {"id": "speaker_1", "label": "Alex"},
            {"id": "speaker_2", "label": "Jamie"}
          ],
          "segments": [
            {"speaker": "speaker_1", "start_seconds": 1.2, "end_seconds": 4.5, "text": "We should keep the beta small."},
            {"speaker": "speaker_2", "start_seconds": "00:05", "end_seconds": "00:08", "text": "Agreed, ship the recorder first."}
          ],
          "action_items": ["Ship the recorder first"]
        }
        ```
        """
    )

    assert notes["speaker_count"] == 2
    assert notes["speakers"] == [
        {"id": "speaker_1", "label": "Alex"},
        {"id": "speaker_2", "label": "Jamie"},
    ]
    assert notes["segments"] == [
        {
            "speaker": "speaker_1",
            "speaker_label": "Alex",
            "start_seconds": 1.2,
            "end_seconds": 4.5,
            "timestamp": "00:01",
            "text": "We should keep the beta small.",
        },
        {
            "speaker": "speaker_2",
            "speaker_label": "Jamie",
            "start_seconds": 5.0,
            "end_seconds": 8.0,
            "timestamp": "00:05",
            "text": "Agreed, ship the recorder first.",
        },
    ]
    assert notes["transcript"] == (
        "[00:01] Alex: We should keep the beta small.\n"
        "[00:05] Jamie: Agreed, ship the recorder first."
    )
    assert "summary" not in notes
    assert "action_items" not in notes


def test_parse_meeting_transcript_response_preserves_timeline_turn_order():
    from vocal_more.application.meeting_notes import parse_meeting_transcript_response

    notes = parse_meeting_transcript_response(
        """
        {
          "speakers": [
            {"id": "speaker_1", "label": "Speaker 1"},
            {"id": "speaker_2", "label": "Speaker 2"}
          ],
          "segments": [
            {"speaker": "speaker_1", "start_seconds": 0, "end_seconds": 2, "text": "第一句"},
            {"speaker": "speaker_2", "start_seconds": 2, "end_seconds": 4, "text": "第二句"},
            {"speaker": "speaker_1", "start_seconds": 4, "end_seconds": 6, "text": "第三句"}
          ]
        }
        """
    )

    assert [segment["speaker"] for segment in notes["segments"]] == [
        "speaker_1",
        "speaker_2",
        "speaker_1",
    ]
    assert notes["transcript"].splitlines() == [
        "[00:00] Speaker 1: 第一句",
        "[00:02] Speaker 2: 第二句",
        "[00:04] Speaker 1: 第三句",
    ]


def test_parse_meeting_transcript_response_parses_plain_timeline_text():
    from vocal_more.application.meeting_notes import parse_meeting_transcript_response

    notes = parse_meeting_transcript_response(
        """
        [00:00.48 - 00:02.08] Speaker 1：第一句
        [00:02.80 - 00:03.84] Speaker 2: 第二句
        [00:05.60] speaker_1：第三句
        """
    )

    assert notes["speaker_count"] == 2
    assert notes["segments"] == [
        {
            "speaker": "speaker_1",
            "speaker_label": "Speaker 1",
            "start_seconds": 0.48,
            "end_seconds": 2.08,
            "timestamp": "00:00",
            "text": "第一句",
        },
        {
            "speaker": "speaker_2",
            "speaker_label": "Speaker 2",
            "start_seconds": 2.8,
            "end_seconds": 3.84,
            "timestamp": "00:02",
            "text": "第二句",
        },
        {
            "speaker": "speaker_1",
            "speaker_label": "Speaker 1",
            "start_seconds": 5.6,
            "timestamp": "00:05",
            "text": "第三句",
        },
    ]
    assert notes["transcript"].splitlines() == [
        "[00:00] Speaker 1: 第一句",
        "[00:02] Speaker 2: 第二句",
        "[00:05] Speaker 1: 第三句",
    ]


def test_parse_meeting_transcript_response_repairs_bare_segment_text():
    from vocal_more.application.meeting_notes import parse_meeting_transcript_response

    notes = parse_meeting_transcript_response(
        """
        {
          "speakers": [
            {"id": "speaker_1", "label": "Speaker 1"},
            {"id": "speaker_2", "label": "Speaker 2"}
          ],
          "segments": [
            {"speaker": "speaker_1", "start_seconds": 0.48, "end_seconds": 2.08, "text": "第一句"},
            {"speaker": "speaker_2", "start_seconds": 30.96, "我怎么这么焦虑呢？"},
            {"speaker": "speaker_1", "start_seconds": 32.72, "end_seconds": 35.04, "text": "第三句"}
          ]
        }
        """
    )

    assert notes["speaker_count"] == 2
    assert [segment["text"] for segment in notes["segments"]] == [
        "第一句",
        "我怎么这么焦虑呢？",
        "第三句",
    ]
    assert notes["transcript"].splitlines()[1] == "[00:30] Speaker 2: 我怎么这么焦虑呢？"


def test_parse_meeting_minutes_response_normalizes_json():
    from vocal_more.application.meeting_notes import parse_meeting_minutes_response

    minutes = parse_meeting_minutes_response(
        """
        {
          "summary": "Decided to ship a meeting mode.",
          "key_points": ["Use two-stage generation"],
          "action_items": ["Add meeting to the mode menu"]
        }
        """
    )

    assert minutes == {
        "status": "success",
        "summary": "Decided to ship a meeting mode.",
        "key_points": ["Use two-stage generation"],
        "action_items": ["Add meeting to the mode menu"],
        "error": None,
    }


def test_meeting_notes_service_runs_transcript_then_minutes():
    from vocal_more.application.meeting_notes import MeetingMinutesResult, MeetingNotesService

    class FakeEngine:
        def __init__(self):
            self.calls = []

        def transcribe_with_system_prompt(self, audio_data, *, system_prompt, model_override, language_override):
            self.calls.append(
                {
                    "audio_data": audio_data,
                    "system_prompt": system_prompt,
                    "model_override": model_override,
                    "language_override": language_override,
                }
            )
            return "[00:00] Speaker 1: Hi\n[00:02] Speaker 2: Hello"

        def get_last_metering(self):
            return {"stage": "asr", "cost_cny": 0.003}

    class FakeMinutesGenerator:
        def __init__(self):
            self.transcripts = []

        def generate(self, transcript):
            self.transcripts.append(transcript)
            return MeetingMinutesResult(
                minutes={
                    "status": "success",
                    "summary": "Two people greeted each other.",
                    "key_points": ["Speaker 1 said hi", "Speaker 2 replied"],
                    "action_items": [],
                    "error": None,
                },
                billing={"stage": "polish", "cost_cny": 0.002},
            )

    engine = FakeEngine()
    minutes_generator = FakeMinutesGenerator()
    config = SimpleNamespace(asr=SimpleNamespace(language="auto"))
    stages: list[str] = []
    service = MeetingNotesService(
        config=config,
        engine_factory=lambda: engine,
        minutes_generator_factory=lambda: minutes_generator,
    )

    result = service.generate(b"pcm", on_stage=stages.append)

    assert engine.calls
    call = engine.calls[0]
    assert "最多两位发言人" in call["system_prompt"]
    assert "summary" not in call["system_prompt"]
    assert "action_items" not in call["system_prompt"]
    assert "JSON" not in call["system_prompt"]
    assert call["model_override"] == "qwen3.5-omni-plus"
    assert call["language_override"] == "auto"
    assert result.notes["transcript"] == "[00:00] Speaker 1: Hi\n[00:02] Speaker 2: Hello"
    assert result.notes["status"] == "success"
    assert result.notes["minutes"]["summary"] == "Two people greeted each other."
    assert minutes_generator.transcripts == ["[00:00] Speaker 1: Hi\n[00:02] Speaker 2: Hello"]
    assert result.notes["billing"]["transcript"]["asr"]["cost_cny"] == 0.003
    assert result.notes["billing"]["minutes"]["polish"]["cost_cny"] == 0.002
    assert result.billing["total_cost_cny"] == 0.005
    assert stages == ["meeting_transcribing", "meeting_summarizing"]


def test_meeting_notes_service_keeps_transcript_when_minutes_fail():
    from vocal_more.application.meeting_notes import MeetingNotesService

    class FakeEngine:
        def transcribe_with_system_prompt(self, audio_data, *, system_prompt, model_override, language_override):
            return (
                '{"speakers":[{"id":"speaker_1","label":"Speaker 1"}],'
                '"segments":[{"speaker":"speaker_1","text":"Keep the transcript"}]}'
            )

        def get_last_metering(self):
            return {"stage": "asr", "cost_cny": 0.001}

    class FailingMinutesGenerator:
        def generate(self, transcript):
            raise RuntimeError("minutes offline")

    service = MeetingNotesService(
        config=SimpleNamespace(asr=SimpleNamespace(language="auto")),
        engine_factory=lambda: FakeEngine(),
        minutes_generator_factory=lambda: FailingMinutesGenerator(),
    )

    result = service.generate(b"pcm")

    assert result.notes["status"] == "partial"
    assert result.notes["transcript"] == "Speaker 1: Keep the transcript"
    assert result.notes["minutes"] == {
        "status": "failed",
        "summary": "",
        "key_points": [],
        "action_items": [],
        "error": "minutes offline",
    }


def test_meeting_notes_service_fails_when_transcript_is_empty():
    from vocal_more.application.meeting_notes import MeetingNotesService

    class EmptyEngine:
        def transcribe_with_system_prompt(self, audio_data, *, system_prompt, model_override, language_override):
            return ""

    service = MeetingNotesService(
        config=SimpleNamespace(asr=SimpleNamespace(language="auto")),
        engine_factory=lambda: EmptyEngine(),
        minutes_generator_factory=lambda: None,
    )

    with pytest.raises(RuntimeError, match="Empty meeting transcript"):
        service.generate(b"pcm")
