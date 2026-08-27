"""Regression tests for Prompt Mode output and capsule layout boundaries."""

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from vocal_more.config import LLMConfig
from vocal_more.core import text_polisher
from vocal_more.domain.prompt_output import sanitize_prompt_output


def test_prompt_mode_does_not_receive_dictionary_context_or_polish_overrides(
    monkeypatch,
):
    monkeypatch.setattr(
        text_polisher,
        "_dictionary_prompt_block",
        lambda: "\n\nDICTIONARY CALIBRATION SECRET",
    )
    config = LLMConfig(
        polish_mode="prompt",
        structured=True,
        prompt_overrides={
            "output_type": {
                "enabled": True,
                "prompt": "CUSTOM OUTPUT FORMAT SECRET",
            },
            "level": {
                "enabled": True,
                "prompt": "CUSTOM POLISH LEVEL SECRET",
            },
            "tone": {
                "enabled": True,
                "prompt": "CUSTOM TONE SECRET",
            },
            "persona": {
                "enabled": True,
                "prompt": "CUSTOM PERSONA SECRET",
            },
        },
    )

    prompts = (
        text_polisher.build_polish_system_prompt(
            config,
            context_instruction="INTERNAL TERMINAL CONTEXT",
        ),
        text_polisher.build_omni_inline_polish_instructions(
            config,
            context_instruction="INTERNAL TERMINAL CONTEXT",
        ),
    )

    for prompt in prompts:
        assert "DICTIONARY CALIBRATION SECRET" not in prompt
        assert "INTERNAL TERMINAL CONTEXT" not in prompt
        assert "CUSTOM OUTPUT FORMAT SECRET" not in prompt
        assert "CUSTOM POLISH LEVEL SECRET" not in prompt
        assert "CUSTOM TONE SECRET" not in prompt
        assert "CUSTOM PERSONA SECRET" not in prompt
        assert "不输出 Open questions" in prompt


def test_prompt_output_removes_questions_and_internal_control_lines():
    leaked = """# Goal
优化现有项目，交付一个新版本意图库。

# Context
- 当前场景：终端环境。
- 核心依赖：必须基于“轻量应用服务器”的意图库。

# Boundaries
- 关键约束：核心组件必须是“轻量应用服务器”的意图库。
- 格式保护：严格保留终端命令和参数。
- 专有名词修正：严格执行以下映射修正。

# Open questions
- 现有项目的代码库位置是什么？
- 其余构成部分是什么？
"""

    cleaned = sanitize_prompt_output(leaked)

    assert "# Open questions" not in cleaned
    assert "代码库位置是什么" not in cleaned
    assert "当前场景：终端环境" not in cleaned
    assert "格式保护" not in cleaned
    assert "专有名词修正" not in cleaned
    assert "轻量应用服务器" in cleaned
    assert "# Goal" in cleaned


def test_prompt_output_drops_empty_sections_after_cleanup():
    cleaned = sanitize_prompt_output(
        "# Context\n- 当前场景：终端环境。\n\n# Goal\n完成任务。"
    )
    assert "# Context" not in cleaned
    assert cleaned == "# Goal\n完成任务。"


def test_capsule_hint_layout_wraps_without_ellipsis():
    root = Path(__file__).resolve().parents[1]
    html = (root / "resources/floating_capsule/capsule.html").read_text(
        encoding="utf-8"
    )
    python_source = (
        root / "src/vocal_more/ui/floating_capsule.py"
    ).read_text(encoding="utf-8")

    assert "white-space: pre-wrap" in html
    assert "overflow-wrap: anywhere" in html
    assert "text-overflow: ellipsis" not in html
    assert "if (currentState === 'hidden') return;" in html
    assert "HINT_CAPSULE_HEIGHT = 200" in python_source
    assert "json.dumps(hint, ensure_ascii=False)" in python_source
    assert ".capsule.has-prompt-hint.state-recording" in html
    assert "max-height: calc(100% - 12px)" in html
    assert "overflow-y: auto" in html
    assert "scrollbar-width: none" in html
    assert ".streaming-text::-webkit-scrollbar" in html
    assert "streamingText.scrollTop = streamingText.scrollHeight" in html


def test_capsule_expands_before_initial_prompt_hint_is_injected(monkeypatch):
    import AppKit

    monkeypatch.setattr(AppKit, "NSEvent", type("NSEvent", (), {}), raising=False)
    monkeypatch.setattr(AppKit, "NSPanel", type("NSPanel", (), {}), raising=False)
    monkeypatch.setattr(AppKit, "NSPointInRect", lambda *_args: False, raising=False)
    monkeypatch.setattr(AppKit, "NSScreen", type("NSScreen", (), {}), raising=False)
    monkeypatch.setattr(AppKit, "NSWindowStyleMaskBorderless", 0, raising=False)
    monkeypatch.setattr(
        AppKit,
        "NSWindowStyleMaskNonactivatingPanel",
        0,
        raising=False,
    )
    capsule_module = importlib.import_module("vocal_more.ui.floating_capsule")
    capsule_module = importlib.reload(capsule_module)

    panel = MagicMock()
    capsule = capsule_module.FloatingCapsule.__new__(
        capsule_module.FloatingCapsule
    )
    capsule._panel = panel
    capsule._current_mode = None
    capsule._current_state = "hidden"
    capsule._interface_language = "zh"
    capsule._latest_prompt_text = ""
    capsule._html_loaded = False
    capsule._hide_timer = None
    capsule._ensure_setup = MagicMock()
    events = []
    capsule._set_capsule_size_on_main_thread = MagicMock(
        side_effect=lambda expanded: events.append(("resize", expanded))
    )
    capsule._display_mode = MagicMock(return_value="prompt")
    capsule._eval_js = MagicMock(
        side_effect=lambda javascript: events.append(("javascript", javascript))
    )
    capsule._start_push_timer = MagicMock()

    monkeypatch.setattr(
        capsule_module,
        "NSEvent",
        SimpleNamespace(mouseLocation=lambda: (0, 0)),
    )
    monkeypatch.setattr(
        capsule_module,
        "NSScreen",
        SimpleNamespace(screens=lambda: [], mainScreen=lambda: None),
    )
    monkeypatch.setattr(
        capsule_module,
        "prompt_coach_hint",
        lambda _text, _language: "第一行提示\n第二行提示",
    )

    capsule._show_on_main_thread("pushToTalk")

    capsule._set_capsule_size_on_main_thread.assert_has_calls(
        [call(False), call(True)]
    )
    javascript = capsule._eval_js.call_args.args[0]
    assert 'updatePromptHint("第一行提示\\n第二行提示")' in javascript
    assert events[0:2] == [("resize", False), ("resize", True)]
    assert events[2][0] == "javascript"


def test_capsule_expands_before_streaming_text_is_injected(monkeypatch):
    import AppKit

    monkeypatch.setattr(AppKit, "NSEvent", type("NSEvent", (), {}), raising=False)
    monkeypatch.setattr(AppKit, "NSPanel", type("NSPanel", (), {}), raising=False)
    monkeypatch.setattr(AppKit, "NSPointInRect", lambda *_args: False, raising=False)
    monkeypatch.setattr(AppKit, "NSScreen", type("NSScreen", (), {}), raising=False)
    monkeypatch.setattr(AppKit, "NSWindowStyleMaskBorderless", 0, raising=False)
    monkeypatch.setattr(
        AppKit,
        "NSWindowStyleMaskNonactivatingPanel",
        0,
        raising=False,
    )
    capsule_module = importlib.import_module("vocal_more.ui.floating_capsule")
    capsule_module = importlib.reload(capsule_module)

    capsule = capsule_module.FloatingCapsule.__new__(
        capsule_module.FloatingCapsule
    )
    capsule._current_state = "recording"
    capsule._current_mode = "handsFree"
    events = []
    capsule._set_capsule_size_on_main_thread = MagicMock(
        side_effect=lambda expanded: events.append(("resize", expanded))
    )
    capsule._eval_js = MagicMock(
        side_effect=lambda javascript: events.append(("javascript", javascript))
    )

    capsule._update_streaming_text_on_main_thread(
        "第一行\n第二行",
        '"第一行\\n第二行"',
    )

    assert events == [
        ("resize", True),
        ("javascript", 'updateStreamingText("第一行\\n第二行")'),
    ]

    events.clear()
    capsule._update_streaming_text_on_main_thread("", '""')

    assert events == [
        ("resize", False),
        ("javascript", 'updateStreamingText("")'),
    ]
