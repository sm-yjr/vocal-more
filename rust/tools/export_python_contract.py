#!/usr/bin/env python3
"""Export public constants and synthetic parity fixtures; never load user data.

Run with the development environment from the repository root. The generated
assets are embedded by Cargo; the Rust backend never invokes this script.
"""
from __future__ import annotations

import json
from pathlib import Path

from vocal_more import __version__
from vocal_more.domain.config_models import AppConfig
from vocal_more.domain.hotkey_catalog import CUSTOM_HOTKEY_KEYS
from vocal_more.domain.model_catalog import ALL_ASR_MODELS, ASR_MODEL_CATALOG, LLM_MODEL_CATALOG
from vocal_more.core import text_polisher as polish
from vocal_more.domain import dictionary_models as dictionary
from vocal_more.domain.bilingual_formatting import format_bilingual_text
from vocal_more.domain.prompt_output import sanitize_prompt_output
from unittest.mock import patch
from vocal_more.infrastructure import pricing
from vocal_more.core.dictionary_learning_model import SYSTEM_PROMPT as LEARNING_PROMPT
from vocal_more.domain import dictionary_learning_models as learning_models
from vocal_more.application.dictionary_learning_candidates import split_dictionary_learning_evidence, edit_statistics
from vocal_more.domain import prompt_coach
from dataclasses import asdict
import runpy
import random

ROOT = Path(__file__).resolve().parents[1] / "crates/backend"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def differences(before, after, prefix=""):
    result = {}
    for key, value in after.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and isinstance(before.get(key), dict):
            result.update(differences(before[key], value, full))
        elif value != before.get(key):
            result[full] = value
    return result


def main():
    defaults = AppConfig().to_dict()
    constants = {name: getattr(polish, name) for name in (
        "COMMON_POLISH_RULES", "SPOKEN_TEXT_BASELINE", "POLISH_EXAMPLES",
        "COMMAND_CONTEXT_INSTRUCTIONS", "PROMPT_OUTPUT_INSTRUCTIONS",
    )}
    with patch.object(polish, "_dictionary_prompt_block", return_value="@DICTIONARY@"), patch.object(polish, "_context_prompt_block", return_value="@CONTEXT@"), patch.object(polish, "_build_polish_rule_block", return_value="@RULES@"):
        templates = {"native": polish.build_native_dictation_instructions()}
        for mode in ["dictation", "prompt"]:
            config = AppConfig().llm
            config.polish_mode = mode
            templates[mode + "_system"] = polish.build_polish_system_prompt(config)
            templates[mode + "_inline"] = polish.build_omni_inline_polish_instructions(config)
    write(ROOT / "assets/product-contract.json", {
        "source_version": __version__, "defaults": defaults,
        "all_asr_models": ALL_ASR_MODELS, "asr_models": ASR_MODEL_CATALOG,
        "llm_models": LLM_MODEL_CATALOG,
        "hotkey_keys": [key.to_config() for key in CUSTOM_HOTKEY_KEYS],
        "prompt_presets": polish.build_polish_prompt_presets(), "prompt_constants": constants,
        "prompt_templates": templates,
        "pricing": {"asr_seconds": pricing._ASR_AUDIO_SECONDS_PRICING,
                    "omni": pricing._OMNI_PRICING, "text": pricing._TEXT_TIERED_PRICING},
        "learning_prompt": LEARNING_PROMPT,
        "prompt_coach": {
            "patterns": {name: getattr(prompt_coach, name).pattern for name in
                         ("_ACTION_RE", "_CONTEXT_RE", "_OUTPUT_RE", "_BOUNDARY_RE", "_TECHNICAL_RE", "_CONTEXT_NEEDED_RE")},
            "hints": {locale: {facet.value if facet else "ready": hint for facet, hint in hints.items()}
                      for locale, hints in prompt_coach._HINTS.items()},
        },
    })
    billing = []
    usages = [None, {}, {"input_tokens": 100, "output_tokens": 50,
                        "input_tokens_details": {"audio_tokens": 80, "text_tokens": 20},
                        "output_tokens_details": {"text_tokens": 49, "audio_tokens": 1}},
              {"prompt_tokens": 130000, "completion_tokens": 120},
              {"prompt_tokens": 900000, "completion_tokens": 1500},
              {"input_tokens": "7", "output_tokens": -4}]
    for model in [*pricing._ASR_AUDIO_SECONDS_PRICING, *pricing._OMNI_PRICING, "unknown"]:
        for usage in usages:
            billing.append({"stage": "asr", "model": model, "seconds": 8.275, "usage": usage,
                            "expected": pricing.build_asr_billing(model=model, audio_seconds=8.275, usage=usage)})
    for model in [*pricing._TEXT_TIERED_PRICING, "unknown"]:
        for usage in usages:
            for thinking in (True, False):
                billing.append({"stage": "polish", "model": model, "thinking": thinking, "usage": usage,
                                "expected": pricing.build_polish_billing(model=model, enable_thinking=thinking, usage=usage)})
    write(ROOT / "tests/fixtures/billing.json", billing)
    # Reuse existing model-validation tests as an independent Python oracle.
    learning_cases = []
    real_validate = learning_models.validate_decision
    def capture_validation(decision, evidence, existing_entries=()):
        existing_entries = list(existing_entries)
        result = real_validate(decision, evidence, existing_entries)
        learning_cases.append({"decision": asdict(decision), "evidence": evidence.to_dict(),
                               "entries": [asdict(e) for e in existing_entries], "expected": result.to_dict()})
        return result
    test_module = runpy.run_path(str(ROOT.parents[2] / "tests/test_dictionary_learning.py"))
    with patch.object(learning_models, "validate_decision", capture_validation):
        for name, function in test_module.items():
            if name.startswith("test_decision_validation_"):
                function()
        for name in ["test_short_name_and_case_corrections_can_be_learned", "test_confident_model_cannot_bypass_lexical_and_exact_edit_guards"]:
            function = test_module[name]
            for mark in function.pytestmark:
                if mark.name == "parametrize":
                    for args in mark.args[1]:
                        function(*args)
    write(ROOT / "tests/fixtures/learning-validation.json", learning_cases)
    pairs = [("范UI是一定要符合Shadcn UI的。", "FanUI是一定要符合Shadcn/ui的。"),
             ("Cloud Code----Cloud Code", "Claude Code----Claude Code"), ("你好。", "你好！"),
             ("abXcdYef", "ab1cd2ef"), ("a----b----c----d----e----f", "A----B----C----D----E----F"),
             ("github", "GitHub"), ("露那", "Luna"), ("", "text"), ("text", "")]
    generator = random.Random(42)
    alphabet = "abcABC甲乙丙四１２🦀， --"
    for _ in range(160):
        before = "".join(generator.choices(alphabet, k=generator.randint(1, 65)))
        cut = generator.randrange(len(before))
        after = before[:cut] + "".join(generator.choices(alphabet, k=generator.randint(0, 12))) + before[cut+generator.randint(0, 12):]
        pairs.append((before, after))
    split_cases = []
    for before, after in pairs:
        evidence = learning_models.DictionaryLearningEvidence(raw_text=before, pasted_text=before, original_text="", baseline_text=before, edited_text=after)
        split_cases.append({"evidence": evidence.to_dict(), "statistics": edit_statistics(before, after),
                            "expected": [e.to_dict() for e in split_dictionary_learning_evidence(evidence, observation_id="fixture")]})
    write(ROOT / "tests/fixtures/learning-candidates.json", split_cases)
    cases = []
    values = [None, True, False, 0, 1, -10, 999999, 0.25, "", "invalid", "false", "yes", "42.5", [], {}]
    for key, initial in defaults.items():
        fields = [(f"{key}.{k}", v) for k, v in initial.items()] if isinstance(initial, dict) else [(key, initial)]
        for field, current in fields:
            for value in [current, *values]:
                config = AppConfig()
                try:
                    config.apply_update(field, value)
                except (ValueError, TypeError, AttributeError):
                    cases.append({"key": field, "input": value, "error": True})
                else:
                    cases.append({"key": field, "input": value, "changes": differences(defaults, config.to_dict())})
    for field, value in [
        ("asr.backend", "short_file"), ("asr.backend", "omni_offline"),
        *[("asr.model", model["id"]) for model in ALL_ASR_MODELS],
        ("llm.level", "structured"), ("audio.blocksize", 640), ("audio.blocksize", "1600"),
        ("hotkey.custom_keys", [CUSTOM_HOTKEY_KEYS[0].to_config()] * 2),
        ("hotkey.active_hotkeys", ["right_cmd", "f13"]),
        ("asr.realtime_url", "wss://example.maas.aliyuncs.com:443/api-ws/v1/realtime/"),
        ("llm.prompt_overrides", {"tone": {"enabled": "yes", "prompt": "测试"}}),
    ]:
        config = AppConfig()
        config.apply_update(field, value)
        cases.append({"key": field, "input": value, "changes": differences(defaults, config.to_dict())})
    write(ROOT / "tests/fixtures/config-updates.json", cases)
    migrations = [{}, {"api_key": "synthetic-test-key"}, {"audio": {"channels": 2, "gain": 4}},
                  {"asr": {"model": "fun-asr-realtime", "backend": "omni_offline"}},
                  {"ui": {"onboarding_completed": False}}, {"unknown": 1},
                  {"audio": {"sample_rate": 48000, "unknown": 1, "gain_mode": "automatic"}},
                  {"hotkey": {"custom_keys": [], "custom_key": CUSTOM_HOTKEY_KEYS[0].to_config()}}]
    write(ROOT / "tests/fixtures/config-migrations.json", [
        {"input": raw, "expected": AppConfig.from_dict(raw).to_dict()} for raw in migrations
    ])
    entries = [dictionary.DictEntry("Rust", ["拉斯特", "rustlang"]), dictionary.DictEntry("Vocal More", ["vocalmore"])]
    dictionary_cases = []
    texts = ["", "使用rustlang和拉斯特", "RUSTLANG rustlangx xrustlang _rustlang_", "vocalmore和Vocal More", "拉斯特/vocalmore", "中文ＡＰＩ和3个词。。", "使用`ＡＰＩ中文`和Python脚本", "打开https://example.com/中文3和www.测试中文3", "路径C:\\中文3完整保留", "# Goal\n做一个工具\n# Open questions\n私有控制信息\n# Output\n文件\n当前场景：终端\n", "# Context\n\n# Goal\n完成任务\n", "事项：1. 检查 2. 发布 3. 复盘", "版本 1.2.3 与 Python 3.12", "要点 - 开始 - 结束", "中文！！？？。。，，、、"]
    for text in texts:
        dictionary_cases.append({"input":text,"entries":[vars(e) for e in entries],
            "normalized":dictionary.normalize_text_entries(text, entries), "bilingual":format_bilingual_text(text),
            "prompt":sanitize_prompt_output(text), "structured":polish.normalize_structured_list_spacing(text, polish.LLMConfig(structured=True))})
    write(ROOT / "tests/fixtures/text-output.json", dictionary_cases)
    prompt_cases = []
    for index in range(36):
        config = AppConfig()
        config.llm.polish_mode = ["dictation", "prompt"][index % 2]
        config.llm.level = ["minimal", "balanced", "strong"][index % 3]
        config.llm.tone = ["neutral", "gentle", "direct"][(index // 3) % 3]
        config.llm.persona = ["default", "technical", "bilingual", "professional", "chat"][index % 5]
        config.llm.output_language = ["auto", "zh", "en"][(index // 2) % 3]
        config.llm.structured = index % 4 == 0
        if index % 6 == 0:
            config.llm.prompt_overrides = {"level":{"enabled":True,"prompt":"保留重要数字"}, "tone":{"enabled":False,"prompt":"禁用草稿"}}
        context = "开发工具，保护代码" if index % 3 else ""
        block = dictionary.format_entries_for_prompt(entries) if index % 2 else ""
        with patch.object(polish, "_dictionary_prompt_block", return_value="\n\n" + block if block else ""):
            prompt_cases.append({"config":config.to_dict(),"dictionary":block,"context":context,
                "system":polish.build_polish_system_prompt(config.llm, context_instruction=context),
                "inline":polish.build_omni_inline_polish_instructions(config.llm, context_instruction=context),
                "native":polish.build_native_dictation_instructions(context_instruction=context)})
    write(ROOT / "tests/fixtures/prompts.json", prompt_cases)
    from vocal_more.core.asr_engine import _build_session_kwargs
    from vocal_more.core import asr_engine
    from dashscope.audio.qwen_omni import OmniRealtimeConversation
    wire_cases = []
    for model in ALL_ASR_MODELS:
        if model["transport"] != "realtime_ws" or model["protocol"] == "audio_recognition":
            continue
        for enable in [True, False]:
            config = AppConfig()
            config.apply_update("asr.model", model["id"])
            config.enable_polish = enable
            config.asr.language = "zh"
            wire = []
            conversation = OmniRealtimeConversation.__new__(OmniRealtimeConversation)
            conversation._OmniRealtimeConversation__send_str = lambda raw: wire.append(json.loads(raw))
            with patch.object(polish, "_dictionary_prompt_block", return_value=""), patch.object(asr_engine, "_get_corpus_text", return_value=None):
                conversation.update_session(**_build_session_kwargs(model, config=config))
            wire_cases.append({"config":config.to_dict(), "session":wire[0]["session"]})
    write(ROOT / "tests/fixtures/realtime-session.json", wire_cases)
    print(f"Exported public constants and {len(cases)} synthetic configuration cases")


if __name__ == "__main__":
    main()
