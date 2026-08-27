from vocal_more.domain.prompt_coach import (
    PromptFacet,
    assess_prompt,
    prompt_coach_hint,
)


def test_empty_prompt_prioritizes_goal():
    assessment = assess_prompt("")

    assert assessment.suggested is PromptFacet.GOAL
    assert assessment.ready is False
    assert "Agent 完成什么" in prompt_coach_hint("", "zh")


def test_technical_goal_then_requests_output_and_boundaries():
    goal_only = assess_prompt("帮我开发 Vocal More 0.4.3")
    assert PromptFacet.GOAL in goal_only.present
    assert PromptFacet.CONTEXT in goal_only.present
    assert goal_only.suggested is PromptFacet.OUTPUT

    with_output = assess_prompt(
        "帮我开发 Vocal More 0.4.3，提交 PR 并通过 pytest"
    )
    assert PromptFacet.OUTPUT in with_output.present
    assert with_output.suggested is PromptFacet.BOUNDARIES

    complete = assess_prompt(
        "帮我开发 Vocal More 0.4.3，提交 PR 并通过 pytest。"
        "必须兼容 Windows 和 macOS，不新增在线模型调用。"
    )
    assert PromptFacet.BOUNDARIES in complete.present
    assert complete.suggested is None
    assert complete.ready is True


def test_nontechnical_prompt_does_not_force_irrelevant_sections():
    assessment = assess_prompt("帮我写一封请假邮件，输出 100 字以内，语气自然")

    assert PromptFacet.GOAL in assessment.present
    assert PromptFacet.OUTPUT in assessment.present
    assert PromptFacet.BOUNDARIES in assessment.present
    assert assessment.ready is True


def test_deictic_request_asks_for_context_after_goal_and_output():
    assessment = assess_prompt("分析这个问题，输出一份报告")

    assert assessment.suggested is PromptFacet.CONTEXT


def test_english_hints_are_localized():
    hint = prompt_coach_hint(
        "Build version 0.4.3 and return a pull request",
        "en",
    )

    assert "boundary" in hint.lower()
