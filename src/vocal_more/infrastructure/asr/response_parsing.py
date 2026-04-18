"""Pure helpers for extracting text from realtime ASR responses."""

from __future__ import annotations

from typing import Optional


def prefer_longer_text(current: str, candidate: Optional[str]) -> str:
    candidate_text = (candidate or "").strip()
    current_text = (current or "").strip()
    if not candidate_text:
        return current_text
    if not current_text or len(candidate_text) >= len(current_text):
        return candidate_text
    return current_text


def extract_text_from_content_part(part: Optional[dict]) -> str:
    if not isinstance(part, dict):
        return ""
    for field_name in ("text", "transcript"):
        value = part.get(field_name)
        if value:
            return str(value).strip()
    return ""


def extract_text_from_realtime_item(item: Optional[dict]) -> str:
    if not isinstance(item, dict):
        return ""

    texts: list[str] = []
    for content_part in item.get("content") or []:
        if isinstance(content_part, str):
            content_text = content_part.strip()
        else:
            content_text = extract_text_from_content_part(content_part)
        if content_text:
            texts.append(content_text)
    return "".join(texts).strip()


def extract_realtime_response_text(response_obj: Optional[dict]) -> str:
    if not isinstance(response_obj, dict):
        return ""

    texts: list[str] = []
    for item in response_obj.get("output") or []:
        item_text = extract_text_from_realtime_item(item)
        if item_text:
            texts.append(item_text)
    return "".join(texts).strip()


__all__ = [
    "extract_realtime_response_text",
    "extract_text_from_content_part",
    "extract_text_from_realtime_item",
    "prefer_longer_text",
]

