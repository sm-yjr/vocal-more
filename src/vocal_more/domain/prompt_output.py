"""Deterministic cleanup for Prompt Mode model output.

Prompt Mode must never expose internal ASR, dictionary, foreground-app,
or polish-control instructions.  The model prompt prevents those leaks;
this module is the final output boundary when a model nevertheless echoes
an internal section.
"""

from __future__ import annotations

import re


_HEADING_RE = re.compile(r"^\s*#{1,6}\s*(.*?)\s*$")
_FORBIDDEN_SECTION_NAMES = {
    "open question",
    "open questions",
    "question",
    "questions",
    "clarifying question",
    "clarifying questions",
    "missing information",
    "information needed",
    "stop rule",
    "stop rules",
    "待确认",
    "待确认事项",
    "待确认信息",
    "待补充",
    "待补充信息",
    "澄清问题",
    "需要确认的信息",
    "需要用户确认",
}
_INTERNAL_CONTROL_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:"
    r"当前场景|格式保护|专有名词修正|"
    r"润色强度要求|语气要求|表达人格要求|结构化格式要求"
    r")\s*[：:]",
    re.IGNORECASE,
)
_INTERNAL_CONTROL_TEXT_RE = re.compile(
    r"(?:用户定义的专有名词词典|"
    r"当前使用场景（仅为本地映射出的抽象类别）|"
    r"严格执行以下映射修正)",
    re.IGNORECASE,
)


def _normalized_heading(value: str) -> str:
    value = value.strip().rstrip(":：?？")
    value = re.sub(r"[\s_-]+", " ", value)
    return value.casefold()


def _is_forbidden_heading(value: str) -> bool:
    normalized = _normalized_heading(value)
    if normalized in _FORBIDDEN_SECTION_NAMES:
        return True
    return normalized.startswith("open question")


def _drop_empty_sections(lines: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(lines):
        heading = _HEADING_RE.match(lines[index])
        if heading is None:
            result.append(lines[index])
            index += 1
            continue

        end = index + 1
        while end < len(lines) and _HEADING_RE.match(lines[end]) is None:
            end += 1
        body = lines[index + 1 : end]
        if any(line.strip() for line in body):
            result.extend(lines[index:end])
        index = end
    return result


def sanitize_prompt_output(text: str) -> str:
    """Remove internal control text and unresolved-question sections."""
    if not str(text or "").strip():
        return ""

    output: list[str] = []
    skipping_section = False
    for raw_line in str(text).splitlines():
        line = raw_line.rstrip()
        heading = _HEADING_RE.match(line)
        if heading is not None:
            skipping_section = _is_forbidden_heading(heading.group(1))
            if skipping_section:
                continue
        elif skipping_section:
            continue

        if _INTERNAL_CONTROL_LINE_RE.search(line):
            continue
        if _INTERNAL_CONTROL_TEXT_RE.search(line):
            continue
        output.append(line)

    output = _drop_empty_sections(output)

    compact: list[str] = []
    blank = False
    for line in output:
        if not line.strip():
            if compact and not blank:
                compact.append("")
            blank = True
            continue
        compact.append(line)
        blank = False
    return "\n".join(compact).strip()


__all__ = ["sanitize_prompt_output"]
