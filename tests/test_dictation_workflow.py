from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def _messages():
    return SimpleNamespace(
        empty_transcription="empty transcript",
        processing_error=lambda details: f"processing: {details}",
        polish_error=lambda details: f"polish: {details}",
    )


def test_dictation_workflow_persists_audio_normalizes_text_and_optionally_pastes():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    stages: list[str] = []
    asr = MagicMock()
    asr.stop.return_value = "open ai codex"
    asr.get_last_metering.return_value = {
        "stage": "asr",
        "cost_cny": 0.001,
    }
    keyboard = MagicMock()
    recording_store = MagicMock()
    text_polisher = None
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=True,
        asr=SimpleNamespace(language="en"),
    )

    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=keyboard,
        recording_store=recording_store,
        normalize_text=lambda text: "OpenAI codex",
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="walkie_talkie",
        asr_model="qwen3-asr-flash",
        text_polisher=text_polisher,
        messages=_messages(),
        on_processing_stage=stages.append,
    )

    assert recording_store.save.call_count == 1
    recording_store.update.assert_called_once_with(
        recording_store.save.return_value,
        "success",
        "open ai codex",
        error=None,
        billing={
            "currency": "CNY",
            "region": "cn-beijing",
            "total_cost_cny": 0.001,
            "asr_cost_cny": 0.001,
            "polish_cost_cny": 0.0,
            "estimated": False,
            "asr": {"stage": "asr", "cost_cny": 0.001},
        },
    )
    keyboard.paste_text.assert_called_once_with("OpenAI codex")
    assert stages == ["transcribing"]
    assert result.raw_text == "open ai codex"
    assert result.final_text == "OpenAI codex"
    assert result.pasted is True
    assert result.error_message is None


def test_dictation_workflow_reports_empty_transcript_as_failure():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    asr = MagicMock()
    asr.stop.return_value = ""
    asr.get_last_metering.return_value = {
        "stage": "asr",
        "cost_cny": 0.002,
        "estimated": True,
    }
    keyboard = MagicMock()
    recording_store = MagicMock()
    config = SimpleNamespace(
        enable_polish=True,
        auto_paste=True,
        asr=SimpleNamespace(language="zh"),
    )

    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=keyboard,
        recording_store=recording_store,
        normalize_text=lambda text: text,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="realtime_long",
        asr_model="qwen3.5-omni-plus-realtime",
        text_polisher=MagicMock(),
        messages=_messages(),
    )

    recording_store.update.assert_called_once_with(
        recording_store.save.return_value,
        "failed",
        error="empty transcript",
        billing={
            "currency": "CNY",
            "region": "cn-beijing",
            "total_cost_cny": 0.002,
            "asr_cost_cny": 0.002,
            "polish_cost_cny": 0.0,
            "estimated": True,
            "asr": {"stage": "asr", "cost_cny": 0.002, "estimated": True},
        },
    )
    keyboard.paste_text.assert_not_called()
    assert result.error_message == "empty transcript"
    assert result.error_code == "empty_transcription"
