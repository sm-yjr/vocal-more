"""Local completeness coaching for spoken Agent prompts.

The coach is deliberately deterministic: it never sends partial transcripts to
another model and never stores them. It only chooses one high-value reminder
for the floating capsule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class PromptFacet(str, Enum):
    """Prompt information that can materially change an Agent result."""

    GOAL = "goal"
    CONTEXT = "context"
    OUTPUT = "output"
    BOUNDARIES = "boundaries"


@dataclass(frozen=True)
class PromptAssessment:
    """Detected prompt facets and the next optional improvement."""

    present: frozenset[PromptFacet]
    suggested: PromptFacet | None
    ready: bool


_ACTION_RE = re.compile(
    r"(?:"
    r"帮我|请(?:你)?|需要|我想(?:要)?|目标是|任务是|希望|"
    r"生成|创建|写|撰写|开发|实现|修改|修复|分析|比较|总结|解释|"
    r"调研|查找|搜索|设计|规划|评估|优化|转换|翻译|提取|验证|"
    r"部署|测试|审查|推荐|整理|制作|输出|给出|做|"
    r"\b(?:create|build|write|develop|implement|modify|fix|analy[sz]e|"
    r"compare|summari[sz]e|explain|research|find|search|design|plan|"
    r"evaluate|optimi[sz]e|convert|translate|extract|verify|deploy|"
    r"test|review|recommend|organize|generate|produce|help|need|want)\b"
    r")",
    re.IGNORECASE,
)
_CONTEXT_RE = re.compile(
    r"(?:"
    r"背景|上下文|当前|现在|现有|已经|因为|基于|用于|面向|项目|"
    r"代码库|仓库|系统|环境|数据|用户|版本|输入|文件|接口|业务|"
    r"\b(?:context|background|current|currently|existing|because|"
    r"based on|used for|project|repo(?:sitory)?|system|environment|"
    r"data|users?|version|input|files?|api|codebase)\b|"
    r"\bv?\d+\.\d+(?:\.\d+)?\b"
    r")",
    re.IGNORECASE,
)
_OUTPUT_RE = re.compile(
    r"(?:"
    r"输出|返回|交付|格式|表格|列表|代码|补丁|文档|报告|方案|步骤|"
    r"摘要|清单|字数|篇幅|安装包|链接|提交|验收|通过|"
    r"\b(?:output|return|deliver(?:able)?|format|table|list|code|patch|"
    r"document|report|plan|steps?|summary|checklist|json|yaml|markdown|"
    r"words?|pull request|pr|installer|link|acceptance|pytest)\b"
    r")",
    re.IGNORECASE,
)
_BOUNDARY_RE = re.compile(
    r"(?:"
    r"不要|不能|避免|必须|只能|至少|最多|不超过|以内|限制|约束|边界|"
    r"预算|截止|兼容|权限|范围|保留|禁止|安全|隐私|成本|不新增|"
    r"\b(?:must|must not|only|at least|at most|no more than|limit|"
    r"constraint|boundary|avoid|preserve|cannot|can't|budget|deadline|"
    r"compatible|permission|scope|safety|privacy|cost|without adding)\b"
    r")",
    re.IGNORECASE,
)
_TECHNICAL_RE = re.compile(
    r"(?:"
    r"代码|开发|实现|修复|调试|部署|测试|仓库|项目|接口|模型|Agent|"
    r"Windows|macOS|Linux|Python|pytest|API|PR|版本|"
    r"\b(?:code|develop|implement|fix|debug|deploy|test|repo|project|"
    r"api|model|agent|windows|macos|linux|python|pytest|pull request|"
    r"version)\b"
    r")",
    re.IGNORECASE,
)
_CONTEXT_NEEDED_RE = re.compile(
    r"(?:这个|这些|上述|前面|当前问题|现有代码|继续|基于它|"
    r"\b(?:this|these|above|previous|existing code|continue|based on it)\b)",
    re.IGNORECASE,
)

_HINTS = {
    "zh": {
        PromptFacet.GOAL: "先说清希望 Agent 完成什么。",
        PromptFacet.OUTPUT: "可补充交付形式或验收标准，例如代码、PR、表格或篇幅。",
        PromptFacet.BOUNDARIES: "可补充最关键的边界，例如版本、兼容性、预算或禁用项。",
        PromptFacet.CONTEXT: "可补充会改变答案的必要背景，例如现状、输入或目标用户。",
        None: "信息已较完整，可以结束录音。",
    },
    "en": {
        PromptFacet.GOAL: "State the result you want the Agent to produce.",
        PromptFacet.OUTPUT: "Optionally add the deliverable or acceptance criteria.",
        PromptFacet.BOUNDARIES: "Optionally add the key boundary: version, compatibility, budget, or exclusions.",
        PromptFacet.CONTEXT: "Optionally add only the context that would change the answer.",
        None: "The prompt is sufficiently complete; you can finish recording.",
    },
}


def _meaningful_length(text: str) -> int:
    return len(re.sub(r"[\W_]+", "", text, flags=re.UNICODE))


def assess_prompt(text: str) -> PromptAssessment:
    """Assess a partial spoken prompt without inferring unstated facts."""

    normalized = str(text or "").strip()
    meaningful_length = _meaningful_length(normalized)
    present: set[PromptFacet] = set()

    if _ACTION_RE.search(normalized):
        present.add(PromptFacet.GOAL)
    if _CONTEXT_RE.search(normalized):
        present.add(PromptFacet.CONTEXT)
    if _OUTPUT_RE.search(normalized):
        present.add(PromptFacet.OUTPUT)
    if _BOUNDARY_RE.search(normalized):
        present.add(PromptFacet.BOUNDARIES)

    suggested: PromptFacet | None
    if meaningful_length < 6 or PromptFacet.GOAL not in present:
        suggested = PromptFacet.GOAL
    elif PromptFacet.OUTPUT not in present:
        suggested = PromptFacet.OUTPUT
    elif (
        PromptFacet.BOUNDARIES not in present
        and _TECHNICAL_RE.search(normalized)
    ):
        suggested = PromptFacet.BOUNDARIES
    elif (
        PromptFacet.CONTEXT not in present
        and _CONTEXT_NEEDED_RE.search(normalized)
    ):
        suggested = PromptFacet.CONTEXT
    else:
        suggested = None

    return PromptAssessment(
        present=frozenset(present),
        suggested=suggested,
        ready=suggested is None,
    )


def prompt_coach_hint(text: str, language: str = "en") -> str:
    """Return one localized, non-blocking prompt-completeness reminder."""

    locale = "zh" if str(language or "").lower().startswith("zh") else "en"
    assessment = assess_prompt(text)
    return _HINTS[locale][assessment.suggested]


__all__ = [
    "PromptAssessment",
    "PromptFacet",
    "assess_prompt",
    "prompt_coach_hint",
]
