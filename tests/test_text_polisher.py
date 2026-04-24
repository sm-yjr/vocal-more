"""Tests for smart text polishing."""

from types import SimpleNamespace

import yaml


def _mock_generation_response(text: str):
    return SimpleNamespace(
        status_code=200,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        output=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        ),
    )


def _mock_multimodal_response(text: str):
    return SimpleNamespace(
        status_code=200,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        output=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=[{"text": text}])
                )
            ]
        ),
    )


def test_polish_uses_llm_for_short_text_when_enabled(tmp_path, monkeypatch):
    """With only an on/off switch, short utterances should still use the LLM."""
    from vocal_more.config import Config, reload_config
    from vocal_more.core.text_polisher import TextPolisher

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({}, f)

    reload_config()

    called = False

    def fake_mm_call(**_kwargs):
        nonlocal called
        called = True
        return _mock_multimodal_response("好的。")

    def fake_gen_call(**_kwargs):
        nonlocal called
        called = True
        return _mock_generation_response("好的。")

    monkeypatch.setattr("vocal_more.core.text_polisher.MultiModalConversation.call", fake_mm_call)
    monkeypatch.setattr("vocal_more.core.text_polisher.Generation.call", fake_gen_call)

    polisher = TextPolisher()
    result = polisher.polish("好的")

    assert called is True
    assert result.polished_text == "好的。"
    assert result.used_llm is True


def test_polish_uses_llm_with_no_thinking(tmp_path, monkeypatch):
    """Qwen3.5 models should use multimodal API with thinking disabled."""
    from vocal_more.config import Config, reload_config
    from vocal_more.dictionary import reload_dictionary
    from vocal_more.core.text_polisher import TextPolisher

    config_path = tmp_path / "config.yaml"
    dict_path = tmp_path / "dictionary.yaml"

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump(
            {
                "llm": {
                    "enable_thinking": False,
                    "temperature": 0.0,
                    "max_tokens": 65536,
                }
            },
            f,
        )

    with open(dict_path, "w") as f:
        yaml.dump(
            {"entries": [{"term": "Vocal More", "aliases": ["vocal mall"]}]},
            f,
            allow_unicode=True,
        )

    reload_config()
    reload_dictionary()

    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _mock_multimodal_response("Vocal More 已经接好了。")

    monkeypatch.setattr(
        "vocal_more.core.text_polisher.MultiModalConversation.call", fake_call
    )

    polisher = TextPolisher()
    result = polisher.polish("嗯 vocal mall 已经接好了")

    assert captured["enable_thinking"] is False
    assert captured["temperature"] == 0.0
    assert captured["max_tokens"] == 65536
    assert "Vocal More 已经接好了" in captured["messages"][1]["content"][0]["text"]
    assert result.polished_text == "Vocal More 已经接好了。"
    assert result.used_llm is True
    assert result.billing["cost_cny"] > 0


def test_polish_non_catalog_model_uses_generation_api(tmp_path, monkeypatch):
    """Models not in the catalog should fall back to Generation.call."""
    from vocal_more.config import Config, reload_config
    from vocal_more.core.text_polisher import TextPolisher

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    # Use a model id not in LLM_MODEL_CATALOG — _parse_llm_model falls back
    # to default, so we monkeypatch _parse_llm_model to pass it through.
    monkeypatch.setattr("vocal_more.config._parse_llm_model", lambda raw: raw)
    monkeypatch.setattr("vocal_more.domain.config_models._parse_llm_model", lambda raw: raw)

    with open(config_path, "w") as f:
        yaml.dump({"llm": {"model": "qwen-legacy"}}, f)

    reload_config()

    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _mock_generation_response("整理后的文本。")

    monkeypatch.setattr("vocal_more.core.text_polisher.Generation.call", fake_call)

    polisher = TextPolisher()
    result = polisher.polish("嗯 这是测试")

    assert captured["model"] == "qwen-legacy"
    assert captured["temperature"] == 0.0
    assert captured["max_tokens"] == 1024
    assert captured["enable_thinking"] is False
    assert result.polished_text == "整理后的文本。"


def test_qwen36_plus_routes_to_multimodal_api(tmp_path, monkeypatch):
    """qwen3.6-plus is in catalog with api=multimodal_conversation, should use MultiModalConversation.call."""
    from vocal_more.config import Config, reload_config
    from vocal_more.core.text_polisher import TextPolisher

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"llm": {"model": "qwen3.6-plus"}}, f)

    reload_config()

    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _mock_multimodal_response("整理后的文本。")

    # Generation.call should NOT be called
    gen_called = False

    def fake_gen_call(**kwargs):
        nonlocal gen_called
        gen_called = True
        return _mock_generation_response("should not be used")

    monkeypatch.setattr(
        "vocal_more.core.text_polisher.MultiModalConversation.call", fake_call
    )
    monkeypatch.setattr(
        "vocal_more.core.text_polisher.Generation.call", fake_gen_call
    )

    polisher = TextPolisher()
    result = polisher.polish("嗯 这是测试")

    assert gen_called is False
    assert captured["model"] == "qwen3.6-plus"
    assert captured["enable_thinking"] is False
    assert captured["temperature"] == 0.0
    assert captured["max_tokens"] == 1024
    assert result.polished_text == "整理后的文本。"
    assert result.used_llm is True


def test_polish_logs_selected_model(tmp_path, monkeypatch, capsys):
    """CLI logs should show which LLM model is used for polishing."""
    from vocal_more.config import Config, reload_config
    from vocal_more.core.text_polisher import TextPolisher

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({"llm": {"model": "qwen3.6-plus"}}, f)

    reload_config()

    def fake_call(**kwargs):
        return _mock_multimodal_response("整理后的文本。")

    monkeypatch.setattr(
        "vocal_more.core.text_polisher.MultiModalConversation.call", fake_call
    )

    polisher = TextPolisher()
    polisher.polish("嗯 这是测试")

    out = capsys.readouterr().out
    assert "[Polisher] Calling model=qwen3.6-plus" in out


def test_structured_flag_adds_instructions_to_prompt(tmp_path, monkeypatch):
    """When structured=True, the system prompt should include structured formatting instructions."""
    from vocal_more.config import Config, LLMConfig, reload_config
    from vocal_more.core.text_polisher import build_polish_system_prompt

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({}, f)

    reload_config()

    prompt_off = build_polish_system_prompt(LLMConfig(structured=False))
    assert "结构化格式要求" not in prompt_off

    prompt_on = build_polish_system_prompt(LLMConfig(structured=True))
    assert "结构化格式要求" in prompt_on
    assert "序号" in prompt_on
    assert "换行" in prompt_on


def test_structured_is_independent_of_level(tmp_path, monkeypatch):
    """Structured can be combined with any level."""
    from vocal_more.config import Config, LLMConfig, reload_config
    from vocal_more.core.text_polisher import build_polish_system_prompt

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({}, f)

    reload_config()

    for level in ("minimal", "balanced", "strong"):
        prompt = build_polish_system_prompt(LLMConfig(level=level, structured=True))
        assert "结构化格式要求" in prompt
        assert level_label(level) in prompt


def level_label(level: str) -> str:
    from vocal_more.core.text_polisher import LEVEL_INSTRUCTIONS
    return LEVEL_INSTRUCTIONS[level][:10]


def test_minimal_prompt_preserves_spoken_texture_by_default(tmp_path, monkeypatch):
    """Minimal polish should stay close to the original spoken phrasing."""
    from vocal_more.config import Config, LLMConfig, reload_config
    from vocal_more.core.text_polisher import build_polish_system_prompt

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_path))

    with open(config_path, "w") as f:
        yaml.dump({}, f)

    reload_config()

    prompt = build_polish_system_prompt(LLMConfig(level="minimal"))

    assert "口语转文本基线" in prompt
    assert "尽量保留原句、原词和口语感" in prompt
    assert "不要主动删除语气词" in prompt
    assert "即使是 minimal" not in prompt


def test_stronger_levels_do_more_cleanup_than_minimal():
    """Balanced/strong should handle filler cleanup more aggressively than minimal."""
    from vocal_more.core.text_polisher import LEVEL_INSTRUCTIONS

    assert "不要主动删除语气词" in LEVEL_INSTRUCTIONS["minimal"]
    assert "在 minimal 基线之上" in LEVEL_INSTRUCTIONS["balanced"]
    assert "口头填充词" in LEVEL_INSTRUCTIONS["balanced"]
    assert "在 balanced 基线之上" in LEVEL_INSTRUCTIONS["strong"]
