"""Pure dictionary entry models and normalization helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class DictEntry:
    """A single dictionary entry."""

    term: str
    aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DictionaryMutation:
    """The exact dictionary changes made by one automatic-learning job."""

    term: str
    term_created: bool
    aliases_added: list[str] = field(default_factory=list)


def normalize_term(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def iter_alias_values(raw: object) -> Iterable[object]:
    if raw is None:
        return

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return

        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                yield from iter_alias_values(parsed)
                return

        for piece in re.split(r"[,，\n]+", text):
            cleaned = piece.strip()
            if cleaned:
                yield cleaned
        return

    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            yield from iter_alias_values(item)
        return

    if isinstance(raw, dict):
        return

    yield raw


def merge_aliases(
    existing_aliases: Iterable[object],
    new_aliases: Iterable[object],
    term: str,
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw_alias in list(existing_aliases) + list(new_aliases):
        alias = normalize_term(raw_alias)
        if not alias or alias == term or alias in seen:
            continue
        seen.add(alias)
        merged.append(alias)
    return merged


def normalize_aliases(raw: object) -> list[str]:
    return merge_aliases([], list(iter_alias_values(raw)), "")


def normalize_entries(raw_entries: object) -> list[DictEntry]:
    entries: list[DictEntry] = []
    if not isinstance(raw_entries, list):
        return entries

    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        term = normalize_term(raw_entry.get("term"))
        if not term:
            continue
        aliases = merge_aliases([], list(iter_alias_values(raw_entry.get("aliases", []))), term)
        entries.append(DictEntry(term=term, aliases=aliases))
    return entries


def normalize_dictionary_data(raw_data: object) -> dict[str, list[dict[str, object]]]:
    if isinstance(raw_data, dict):
        raw_entries = raw_data.get("entries", [])
    else:
        raw_entries = []
    return serialize_entries(normalize_entries(raw_entries))


def serialize_entries(entries: Iterable[DictEntry]) -> dict[str, list[dict[str, object]]]:
    return {
        "entries": [
            {"term": entry.term, "aliases": list(entry.aliases)}
            if entry.aliases
            else {"term": entry.term}
            for entry in entries
        ]
    }


def format_entries_for_prompt(entries: Iterable[DictEntry]) -> str:
    lines: list[str] = []
    for entry in entries:
        if entry.aliases:
            aliases_str = "、".join(entry.aliases)
            lines.append(f"   - {entry.term}（可能被误识别为：{aliases_str}）")
        else:
            lines.append(f"   - {entry.term}")

    if not lines:
        return ""

    return (
        "5. 专有名词修正：以下是用户定义的专有名词词典，"
        "语音转写中可能出现这些词的错误识别，请修正为正确的写法：\n"
        f"{chr(10).join(lines)}"
    )


def build_asr_corpus_text(entries: Iterable[DictEntry], extra_terms: Iterable[object]) -> str:
    canonical_terms: list[str] = []
    seen: set[str] = set()

    for entry in entries:
        term = entry.term.strip()
        if term and term not in seen:
            seen.add(term)
            canonical_terms.append(term)

    additional_terms: list[str] = []
    for term in extra_terms:
        cleaned = str(term).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            additional_terms.append(cleaned)

    sections: list[str] = []
    if canonical_terms:
        sections.append("\n".join(canonical_terms))
    if additional_terms:
        sections.append("\n".join(additional_terms))
    return "\n\n".join(sections)


def _is_ascii_word(value: str) -> bool:
    return bool(value) and all(ch.isascii() for ch in value)


def _needs_boundary(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def build_alias_pattern(alias: str) -> str:
    prefix = r"(?<![A-Za-z0-9])" if _needs_boundary(alias[0]) else ""
    suffix = r"(?![A-Za-z0-9])" if _needs_boundary(alias[-1]) else ""
    return f"{prefix}{re.escape(alias)}{suffix}"


def normalize_text_entries(text: str, entries: Iterable[DictEntry]) -> str:
    normalized = text
    replacements: list[tuple[str, str]] = []
    for entry in entries:
        for alias in entry.aliases:
            cleaned_alias = normalize_term(alias)
            if cleaned_alias and cleaned_alias != entry.term:
                replacements.append((cleaned_alias, entry.term))

    replacements.sort(key=lambda item: len(item[0]), reverse=True)

    for alias, canonical in replacements:
        flags = re.IGNORECASE if _is_ascii_word(alias) else 0
        normalized = re.sub(build_alias_pattern(alias), canonical, normalized, flags=flags)

    return normalized


__all__ = [
    "DictEntry",
    "DictionaryMutation",
    "build_asr_corpus_text",
    "format_entries_for_prompt",
    "iter_alias_values",
    "merge_aliases",
    "normalize_aliases",
    "normalize_dictionary_data",
    "normalize_entries",
    "normalize_term",
    "normalize_text_entries",
    "serialize_entries",
]
