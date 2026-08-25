from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


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


def test_dictation_workflow_formats_final_text_on_normalize_path():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    asr = MagicMock()
    asr.stop.return_value = "今天用Python写了2个脚本"
    asr.get_last_metering.return_value = None
    keyboard = MagicMock()
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=True,
        asr=SimpleNamespace(language="zh"),
    )
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=keyboard,
        normalize_text=lambda text: text,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="walkie_talkie",
        asr_model="qwen3-asr-flash",
        text_polisher=None,
        messages=_messages(),
    )

    assert result.final_text == "今天用 Python 写了 2 个脚本"
    keyboard.paste_text.assert_called_once_with("今天用 Python 写了 2 个脚本")


def test_dictation_workflow_formats_final_text_on_polish_path():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    asr = MagicMock()
    asr.stop.return_value = "防御赛模型已经接好了"
    polisher = MagicMock()
    # Polisher output still has CJK/Latin glue: formatting must apply
    # after the polished text replaces the normalized text.
    polisher.polish.return_value = SimpleNamespace(
        polished_text="Fun-ASR Realtime已接好。。",
        billing=None,
    )
    config = SimpleNamespace(
        enable_polish=True,
        auto_paste=True,
        asr=SimpleNamespace(language="zh"),
    )
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=MagicMock(),
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="walkie_talkie",
        asr_model="qwen3-asr-flash",
        text_polisher=polisher,
        messages=_messages(),
    )

    assert result.final_text == "Fun-ASR Realtime 已接好。"


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


@pytest.mark.parametrize(
    "asr_model",
    [
        "qwen3-asr-flash",
        "qwen3-asr-flash-realtime-2026-02-10",
        "fun-asr-realtime",
    ],
)
def test_legacy_and_fun_asr_models_use_second_stage_polisher(asr_model):
    from vocal_more.application.dictation_workflow import DictationWorkflow

    asr = MagicMock()
    asr.stop.return_value = "防御赛模型已经接好了"
    polisher = MagicMock()
    polisher.polish.return_value = SimpleNamespace(
        polished_text="Fun-ASR Realtime 已经接好了。",
        billing=None,
    )
    config = SimpleNamespace(
        enable_polish=True,
        auto_paste=False,
        asr=SimpleNamespace(language="zh"),
    )
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=MagicMock(),
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="walkie_talkie",
        asr_model=asr_model,
        text_polisher=polisher,
        messages=_messages(),
    )

    polisher.polish.assert_called_once_with("防御赛模型已经接好了")
    assert result.final_text == "Fun-ASR Realtime 已经接好了。"


def test_dictation_workflow_wraps_paste_with_best_effort_dictionary_observation():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    calls: list[str] = []
    asr = MagicMock()
    asr.stop.return_value = "阿里云白练"
    keyboard = MagicMock()
    keyboard.paste_text.side_effect = lambda _text: calls.append("paste")
    learner = MagicMock()
    learner.prepare_paste.side_effect = lambda **_: (
        calls.append("prepare") or "ticket"
    )
    learner.observe_after_paste.side_effect = lambda _ticket: calls.append("observe")
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=True,
        asr=SimpleNamespace(language="zh"),
    )
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=keyboard,
        normalize_text=lambda text: text,
        dictionary_learning=learner,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="walkie_talkie",
        asr_model="qwen3-asr-flash",
        text_polisher=None,
        messages=_messages(),
    )

    assert calls == ["prepare", "paste", "observe"]
    learner.prepare_paste.assert_called_once_with(
        raw_text="阿里云白练",
        pasted_text="阿里云白练",
        recording_id=None,
        mode_name="walkie_talkie",
    )
    assert result.pasted is True


def test_dictation_workflow_saves_recording_concurrently_with_asr_stop():
    """recording_store.save() must overlap with asr_engine.stop().

    The fake store blocks inside save() until asr.stop() has been entered.
    With the old serial flow (save before stop) this would deadlock; the
    overlapped flow lets stop() run while save() is still in flight.
    """
    from vocal_more.application.dictation_workflow import DictationWorkflow

    stop_entered = threading.Event()
    events: list[str] = []
    events_lock = threading.Lock()

    def _record(name: str) -> None:
        with events_lock:
            events.append(name)

    class BlockingStore:
        def save(self, pcm_data, mode, asr_model, language="zh"):
            _record("save_started")
            assert stop_entered.wait(timeout=5.0), (
                "save() never observed asr.stop() running: "
                "persistence did not overlap ASR finalization"
            )
            _record("save_finished")
            return "rec-1"

        def update(self, recording_id, status, *args, **kwargs):
            _record(f"update:{recording_id}")
            return True

    def _stop(pcm_data=None):
        _record("stop_entered")
        stop_entered.set()
        return "hello world"

    asr = MagicMock()
    asr.stop.side_effect = _stop
    asr.get_last_metering.return_value = None
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=False,
        asr=SimpleNamespace(language="zh"),
    )
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=MagicMock(),
        recording_store=BlockingStore(),
        normalize_text=lambda text: text,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="walkie_talkie",
        asr_model="qwen3-asr-flash",
        text_polisher=None,
        messages=_messages(),
    )

    assert result.recording_id == "rec-1"
    assert "save_finished" in events
    # save completed only after stop was entered (overlap), and the store
    # update happened strictly after save finished (file already on disk).
    assert events.index("stop_entered") < events.index("save_finished")
    assert events.index("save_finished") < events.index("update:rec-1")


def test_dictation_workflow_joins_save_before_update_when_asr_stop_raises():
    """After asr.stop() raises, save must already be complete and the
    failure update must use the final recording_id."""
    from vocal_more.application.dictation_workflow import DictationWorkflow

    release_save = threading.Event()
    save_finished = threading.Event()
    update_calls: list[tuple[str, str, bool]] = []

    class SlowStore:
        def save(self, pcm_data, mode, asr_model, language="zh"):
            # Keep save in flight until stop() has raised, forcing the
            # workflow to actually wait on the join before update().
            assert release_save.wait(timeout=5.0)
            save_finished.set()
            return "rec-err"

        def update(self, recording_id, status, *args, **kwargs):
            update_calls.append(
                (recording_id, status, save_finished.is_set())
            )
            return True

    def _stop(pcm_data=None):
        release_save.set()
        raise RuntimeError("network down")

    asr = MagicMock()
    asr.stop.side_effect = _stop
    asr.get_last_metering.return_value = None
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=True,
        asr=SimpleNamespace(language="zh"),
    )
    keyboard = MagicMock()
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=keyboard,
        recording_store=SlowStore(),
        normalize_text=lambda text: text,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="walkie_talkie",
        asr_model="qwen3-asr-flash",
        text_polisher=None,
        messages=_messages(),
    )

    assert result.error_code == "processing_error"
    assert result.recording_id == "rec-err"
    assert update_calls == [("rec-err", "failed", True)]
    keyboard.paste_text.assert_not_called()


def test_dictation_workflow_waits_for_save_even_when_aborted():
    """An aborted session still joins the save thread (no orphan thread)
    and reports the final recording_id, without pasting."""
    from vocal_more.application.dictation_workflow import DictationWorkflow

    release_save = threading.Event()
    save_finished = threading.Event()

    class SlowStore:
        def save(self, pcm_data, mode, asr_model, language="zh"):
            assert release_save.wait(timeout=5.0)
            save_finished.set()
            return "rec-abort"

        def update(self, recording_id, status, *args, **kwargs):
            return True

    def _stop(pcm_data=None):
        release_save.set()
        return "some text"

    asr = MagicMock()
    asr.stop.side_effect = _stop
    asr.get_last_metering.return_value = None
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=True,
        asr=SimpleNamespace(language="zh"),
    )
    keyboard = MagicMock()
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=keyboard,
        recording_store=SlowStore(),
        normalize_text=lambda text: text,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="walkie_talkie",
        asr_model="qwen3-asr-flash",
        text_polisher=None,
        messages=_messages(),
        should_abort=lambda: True,
    )

    assert save_finished.is_set()
    assert result.recording_id == "rec-abort"
    assert result.pasted is False
    keyboard.paste_text.assert_not_called()


def test_streamed_prefix_tail_alignment_rules():
    from vocal_more.application.dictation_workflow import streamed_prefix_tail

    # Empty prefix: nothing was streamed, the whole text remains.
    assert streamed_prefix_tail("abc", "") == "abc"
    assert streamed_prefix_tail("", "") == ""
    # Exact prefix.
    assert streamed_prefix_tail("abcdef", "abc") == "def"
    assert streamed_prefix_tail("abc", "abc") == ""
    # Whitespace differences between the engine's separator-free
    # accumulation and the paste-time separators are ignored.
    assert streamed_prefix_tail("abcdef", "ab cd") == "ef"
    assert streamed_prefix_tail("ab def", "abcdef") is None
    assert streamed_prefix_tail("  abc def", "abc") == " def"
    # Misalignment or a streamed prefix longer than the final text must
    # return None so callers paste nothing rather than duplicate.
    assert streamed_prefix_tail("xyz", "abc") is None
    assert streamed_prefix_tail("ab", "abc") is None
    assert streamed_prefix_tail("abd", "abc") is None


def test_dictation_workflow_streamed_session_skips_polish_and_pastes_tail():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    asr = MagicMock()
    # Engine accumulation concatenates segments without separators.
    asr.stop.return_value = "第一段。第二段。第三段。"
    asr.get_last_metering.return_value = None
    keyboard = MagicMock()
    recording_store = MagicMock()
    polisher = MagicMock()
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
        asr_model="qwen3-asr-flash-realtime-2026-02-10",
        text_polisher=polisher,
        messages=_messages(),
        streamed_raw_text="第一段。第二段。",
    )

    polisher.polish.assert_not_called()
    keyboard.paste_text.assert_called_once_with(" 第三段。")
    assert result.pasted is True
    assert result.warnings == []
    # Result and history keep the complete aggregated text.
    assert result.final_text == "第一段。第二段。第三段。"
    assert result.raw_text == "第一段。第二段。第三段。"
    recording_store.update.assert_called_once_with(
        recording_store.save.return_value,
        "success",
        "第一段。第二段。第三段。",
        error=None,
        billing=None,
    )


def test_dictation_workflow_streamed_tail_alignment_ignores_whitespace():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    asr = MagicMock()
    asr.stop.return_value = "Hello world.Next part."
    asr.get_last_metering.return_value = None
    keyboard = MagicMock()
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=True,
        asr=SimpleNamespace(language="en"),
    )
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=keyboard,
        normalize_text=lambda text: text,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="realtime_long",
        asr_model="qwen3-asr-flash-realtime-2026-02-10",
        text_polisher=None,
        messages=_messages(),
        streamed_raw_text="Hello world.",
    )

    keyboard.paste_text.assert_called_once_with(" Next part.")
    assert result.pasted is True
    assert result.warnings == []


def test_dictation_workflow_streamed_prefix_mismatch_pastes_nothing():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    asr = MagicMock()
    # A fallback transcription path rewrote the text: no prefix alignment.
    asr.stop.return_value = "完全不同的最终文本"
    asr.get_last_metering.return_value = None
    keyboard = MagicMock()
    recording_store = MagicMock()
    config = SimpleNamespace(
        enable_polish=False,
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
    messages = _messages()
    messages.streaming_paste_mismatch = "对齐失败，未粘贴剩余内容"

    result = workflow.finish_recording(
        b"pcm",
        mode_name="realtime_long",
        asr_model="qwen3-asr-flash-realtime-2026-02-10",
        text_polisher=None,
        messages=messages,
        streamed_raw_text="第一段。",
    )

    keyboard.paste_text.assert_not_called()
    assert result.pasted is False
    assert result.warnings == ["对齐失败，未粘贴剩余内容"]
    # History still records the complete final text.
    recording_store.update.assert_called_once_with(
        recording_store.save.return_value,
        "success",
        "完全不同的最终文本",
        error=None,
        billing=None,
    )


def test_dictation_workflow_streamed_full_prefix_leaves_no_tail():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    asr = MagicMock()
    asr.stop.return_value = "第一段。第二段。"
    asr.get_last_metering.return_value = None
    keyboard = MagicMock()
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=True,
        asr=SimpleNamespace(language="zh"),
    )
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=keyboard,
        normalize_text=lambda text: text,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="realtime_long",
        asr_model="qwen3-asr-flash-realtime-2026-02-10",
        text_polisher=None,
        messages=_messages(),
        streamed_raw_text="第一段。第二段。",
    )

    keyboard.paste_text.assert_not_called()
    # Text reached the app during recording even though nothing is left.
    assert result.pasted is True
    assert result.warnings == []


def test_dictionary_observer_failure_does_not_block_paste():
    from vocal_more.application.dictation_workflow import DictationWorkflow

    asr = MagicMock()
    asr.stop.return_value = "正常听写"
    keyboard = MagicMock()
    learner = MagicMock()
    learner.prepare_paste.side_effect = RuntimeError("AX unavailable")
    config = SimpleNamespace(
        enable_polish=False,
        auto_paste=True,
        asr=SimpleNamespace(language="zh"),
    )
    workflow = DictationWorkflow(
        config=config,
        asr_engine=asr,
        keyboard=keyboard,
        normalize_text=lambda text: text,
        dictionary_learning=learner,
    )

    result = workflow.finish_recording(
        b"pcm",
        mode_name="walkie_talkie",
        asr_model="qwen3-asr-flash",
        text_polisher=None,
        messages=_messages(),
    )

    keyboard.paste_text.assert_called_once_with("正常听写")
    learner.observe_after_paste.assert_not_called()
    assert result.pasted is True
