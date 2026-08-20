from types import SimpleNamespace


def _custom_key(code: int = 105) -> dict:
    return {
        "key_code": code,
        "display_name": "F13",
        "is_modifier": False,
        "flag_mask": 0,
    }


def test_command_shortcut_round_trips_through_config():
    from vocal_more.domain.config_models import AppConfig

    config = AppConfig.from_dict({"hotkey": {"command_key": _custom_key()}})

    assert config.hotkey.command_key == _custom_key()
    assert config.to_dict()["hotkey"]["command_key"] == _custom_key()


def test_command_shortcut_wins_over_duplicate_dictation_binding():
    from vocal_more.domain.config_models import AppConfig

    config = AppConfig.from_dict(
        {
            "hotkey": {
                "custom_keys": [_custom_key(), _custom_key(122)],
                "command_key": _custom_key(),
            }
        }
    )

    assert [key["key_code"] for key in config.hotkey.custom_keys] == [122]
    assert config.hotkey.custom_key["key_code"] == 122


def test_command_mode_support_is_limited_to_qwen35_omni_models():
    from vocal_more.domain.model_catalog import supports_command_mode

    assert supports_command_mode("qwen3.5-omni-flash-realtime") is True
    assert supports_command_mode("qwen3.5-omni-plus") is True
    assert supports_command_mode("qwen-audio-3.0-realtime-plus") is False
    assert supports_command_mode("fun-asr-realtime") is False


def test_ghostty_is_classified_as_terminal_without_identity_in_instruction():
    from vocal_more.domain.app_context import (
        classify_app_context,
        instruction_for_context,
    )

    context = classify_app_context("com.mitchellh.ghostty")
    instruction = instruction_for_context(context)

    assert context.category == "terminal"
    assert "终端场景" in instruction
    assert "ghostty" not in instruction.lower()


def test_command_prompt_adapts_terminal_output_and_keeps_dictionary(monkeypatch):
    from vocal_more.core import text_polisher

    monkeypatch.setattr(
        text_polisher,
        "_dictionary_prompt_block",
        lambda: "\n\n用户词典：Ghostty, kubectl",
    )

    prompt = text_polisher.build_omni_command_instructions(
        context_category="terminal"
    )

    assert "只输出可执行命令本身" in prompt
    assert "每行以 # 开头" in prompt
    assert "联网搜索" in prompt
    assert "Ghostty, kubectl" in prompt
    assert "不要附加回车" in prompt


def test_realtime_command_session_enables_search_and_dictation_resets_it(monkeypatch):
    from vocal_more.core import asr_engine

    config = SimpleNamespace(
        enable_polish=True,
        llm=SimpleNamespace(),
    )
    model_info = {
        "input_audio_transcription_model": "gummy-realtime-v1",
        "handles_inline_polish": True,
    }
    monkeypatch.setattr(
        asr_engine,
        "build_omni_inline_polish_instructions",
        lambda *_args, **_kwargs: "dictation prompt",
    )
    monkeypatch.setattr(
        asr_engine,
        "build_omni_command_instructions",
        lambda **_kwargs: "command prompt",
    )

    command = asr_engine._build_session_kwargs(
        model_info,
        config=config,
        context_instruction="terminal",
        command_mode=True,
    )
    dictation = asr_engine._build_session_kwargs(
        model_info,
        config=config,
        command_mode=False,
    )

    assert command["instructions"] == "command prompt"
    assert command["enable_search"] is True
    assert command["search_options"] == {"enable_source": True}
    assert dictation["enable_search"] is False
    assert dictation["instructions"] == "dictation prompt"


def test_command_workflow_never_pastes_when_generation_is_empty():
    from vocal_more.application.command_workflow import CommandWorkflow

    pasted: list[str] = []
    engine = SimpleNamespace(
        stop=lambda **_kwargs: "",
        get_last_metering=lambda: None,
    )
    workflow = CommandWorkflow(
        config=SimpleNamespace(
            auto_paste=True,
            asr=SimpleNamespace(language="zh"),
        ),
        asr_engine=engine,
        keyboard=SimpleNamespace(paste_text=pasted.append),
    )

    result = workflow.finish_recording(
        b"pcm",
        asr_model="qwen3.5-omni-plus",
        empty_message="empty",
        error_message=lambda details: details,
    )

    assert result.error_message == "empty"
    assert pasted == []


def test_command_workflow_pastes_generated_answer_without_normalization():
    from vocal_more.application.command_workflow import CommandWorkflow

    pasted: list[str] = []
    workflow = CommandWorkflow(
        config=SimpleNamespace(
            auto_paste=True,
            asr=SimpleNamespace(language="zh"),
        ),
        asr_engine=SimpleNamespace(
            stop=lambda **_kwargs: "git status --short\n",
            get_last_metering=lambda: None,
        ),
        keyboard=SimpleNamespace(paste_text=pasted.append),
    )

    result = workflow.finish_recording(
        b"pcm",
        asr_model="qwen3.5-omni-plus",
        empty_message="empty",
        error_message=lambda details: details,
    )

    assert result.final_text == "git status --short"
    assert pasted == ["git status --short"]
