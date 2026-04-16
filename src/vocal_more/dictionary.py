"""Dictionary management for Vocal-More.

Maintains a list of proper nouns / specialized terms so the AI polisher
can correct common ASR mis-recognitions.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

import yaml

from .config import Config, get_config


@dataclass
class DictEntry:
    """A single dictionary entry."""

    term: str  # Correct spelling
    aliases: List[str] = field(default_factory=list)  # Common mis-recognitions


class Dictionary:
    """Manages the user dictionary stored in YAML."""

    def __init__(self) -> None:
        self.entries: List[DictEntry] = []
        self.load()

    # -- persistence ----------------------------------------------------------

    @staticmethod
    def get_path() -> Path:
        return Config.get_config_dir() / "dictionary.yaml"

    def load(self) -> None:
        path = self.get_path()
        if not path.exists():
            self.entries = []
            return

        from .compatibility import run_compatibility_check_and_repair

        run_compatibility_check_and_repair("dictionary")

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        self.entries = []
        for raw_entry in data.get("entries", []):
            if not isinstance(raw_entry, dict):
                continue
            term = _normalize_term(raw_entry.get("term"))
            if not term:
                continue
            aliases = _merge_aliases([], list(_iter_alias_values(raw_entry.get("aliases", []))), term)
            self.entries.append(DictEntry(term=term, aliases=aliases))

    def save(self) -> None:
        path = self.get_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "entries": [
                {"term": e.term, "aliases": e.aliases} if e.aliases else {"term": e.term}
                for e in self.entries
            ]
        }

        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    # -- mutations ------------------------------------------------------------

    def add_entry(self, term: str, aliases: Optional[List[str]] = None) -> None:
        term = _normalize_term(term)
        if not term:
            return
        normalized_aliases = _normalize_aliases(aliases or [])

        # Avoid duplicates (case-sensitive on purpose)
        for e in self.entries:
            if e.term == term:
                if normalized_aliases:
                    e.aliases = _merge_aliases(e.aliases, normalized_aliases, term)
                self.save()
                return

        self.entries.append(DictEntry(term=term, aliases=normalized_aliases))
        self.save()

    def remove_entry(self, term: str) -> None:
        self.entries = [e for e in self.entries if e.term != term]
        self.save()

    # -- prompt helpers -------------------------------------------------------

    def format_for_prompt(self) -> str:
        """Return a formatted string suitable for injection into the polish prompt.

        Returns empty string when the dictionary is empty.
        """
        if not self.entries:
            return ""

        lines: List[str] = []
        for e in self.entries:
            if e.aliases:
                aliases_str = "、".join(e.aliases)
                lines.append(f"   - {e.term}（可能被误识别为：{aliases_str}）")
            else:
                lines.append(f"   - {e.term}")

        entries_block = "\n".join(lines)
        return (
            "5. 专有名词修正：以下是用户定义的专有名词词典，"
            "语音转写中可能出现这些词的错误识别，请修正为正确的写法：\n"
            f"{entries_block}"
        )

    def build_asr_corpus_text(self) -> str:
        """Build the corpus text used for ASR context biasing."""
        config = get_config()

        canonical_terms = []
        seen = set()

        for entry in self.entries:
            term = entry.term.strip()
            if term and term not in seen:
                seen.add(term)
                canonical_terms.append(term)

        extra_terms = []
        for term in config.asr.extra_corpus_terms:
            cleaned = str(term).strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                extra_terms.append(cleaned)

        sections: List[str] = []
        if canonical_terms:
            sections.append("\n".join(canonical_terms))
        if extra_terms:
            sections.append("\n".join(extra_terms))

        return "\n\n".join(sections)

    def normalize_terms(self, text: str) -> str:
        """Normalize known aliases back to canonical terms."""
        normalized = text
        replacements: List[tuple[str, str]] = []

        for entry in self.entries:
            for alias in entry.aliases:
                cleaned_alias = _normalize_term(alias)
                if cleaned_alias and cleaned_alias != entry.term:
                    replacements.append((cleaned_alias, entry.term))

        replacements.sort(key=lambda item: len(item[0]), reverse=True)

        for alias, canonical in replacements:
            pattern = _build_alias_pattern(alias)
            flags = re.IGNORECASE if _is_ascii_word(alias) else 0
            normalized = re.sub(pattern, canonical, normalized, flags=flags)

        return normalized


def _is_ascii_word(value: str) -> bool:
    return bool(value) and all(ch.isascii() for ch in value)


def _needs_boundary(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _build_alias_pattern(alias: str) -> str:
    prefix = r"(?<![A-Za-z0-9])" if _needs_boundary(alias[0]) else ""
    suffix = r"(?![A-Za-z0-9])" if _needs_boundary(alias[-1]) else ""
    return f"{prefix}{re.escape(alias)}{suffix}"


def _normalize_term(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _iter_alias_values(raw: object) -> Iterable[object]:
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
                yield from _iter_alias_values(parsed)
                return

        for piece in re.split(r"[,，\n]+", text):
            cleaned = piece.strip()
            if cleaned:
                yield cleaned
        return

    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            yield from _iter_alias_values(item)
        return

    if isinstance(raw, dict):
        return

    yield raw


def _merge_aliases(
    existing_aliases: Iterable[object],
    new_aliases: Iterable[object],
    term: str,
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw_alias in list(existing_aliases) + list(new_aliases):
        alias = _normalize_term(raw_alias)
        if not alias or alias == term or alias in seen:
            continue
        seen.add(alias)
        merged.append(alias)
    return merged


def _normalize_aliases(raw: object) -> list[str]:
    return _merge_aliases([], list(_iter_alias_values(raw)), "")


# -- singleton ----------------------------------------------------------------

_dictionary: Optional[Dictionary] = None


def get_dictionary() -> Dictionary:
    global _dictionary
    if _dictionary is None:
        _dictionary = Dictionary()
    return _dictionary


def reload_dictionary() -> Dictionary:
    global _dictionary
    _dictionary = Dictionary()
    return _dictionary


def build_asr_corpus_text() -> str:
    return get_dictionary().build_asr_corpus_text()


def normalize_terms(text: str) -> str:
    return get_dictionary().normalize_terms(text)
