"""Split one final user edit into bounded dictionary-learning candidates."""

from __future__ import annotations

from dataclasses import replace
from difflib import SequenceMatcher

from ..domain.dictionary_learning_models import DictionaryLearningEvidence


MAX_CANDIDATES_PER_OBSERVATION = 5
_MERGE_EQUAL_GAP = 3
_CONTEXT_CHARACTERS = 16


def _group_edit_opcodes(
    before: str,
    after: str,
) -> list[tuple[int, int, int, int]]:
    """Return changed ranges, merging hunks separated by tiny equal spans."""
    groups: list[list[int]] = []
    for tag, before_start, before_end, after_start, after_end in SequenceMatcher(
        None,
        before,
        after,
        autojunk=False,
    ).get_opcodes():
        if tag == "equal":
            continue
        if groups:
            previous = groups[-1]
            before_gap = before_start - previous[1]
            after_gap = after_start - previous[3]
            if (
                before_gap <= _MERGE_EQUAL_GAP
                and after_gap <= _MERGE_EQUAL_GAP
            ):
                previous[1] = before_end
                previous[3] = after_end
                continue
        groups.append([before_start, before_end, after_start, after_end])
    return [tuple(group) for group in groups]


def edit_statistics(before: str, after: str) -> tuple[int, int, int]:
    """Return actual removed/added character counts and merged hunk count."""
    groups = _group_edit_opcodes(before, after)
    removed = sum(before_end - before_start for before_start, before_end, _, _ in groups)
    added = sum(after_end - after_start for _, _, after_start, after_end in groups)
    return removed, added, len(groups)


def split_dictionary_learning_evidence(
    evidence: DictionaryLearningEvidence,
    *,
    observation_id: str,
) -> list[DictionaryLearningEvidence]:
    """Create one independently classifiable evidence object per edit hunk."""
    before = evidence.baseline_text
    after = evidence.edited_text
    if not before or not after or before == after:
        return []

    candidates: list[tuple[int, int, int, int]] = []
    seen_mappings: set[tuple[str, str]] = set()
    for before_start, before_end, after_start, after_end in _group_edit_opcodes(
        before,
        after,
    ):
        removed = before[before_start:before_end]
        added = after[after_start:after_end]
        if not any(character.isalnum() for character in f"{removed}{added}"):
            continue
        mapping_key = (removed.casefold(), added.casefold())
        if mapping_key in seen_mappings:
            continue
        seen_mappings.add(mapping_key)
        candidates.append((before_start, before_end, after_start, after_end))

    if not candidates or len(candidates) > MAX_CANDIDATES_PER_OBSERVATION:
        return []

    candidate_count = len(candidates)
    split: list[DictionaryLearningEvidence] = []
    for index, (before_start, before_end, after_start, after_end) in enumerate(
        candidates
    ):
        before_context_start = max(0, before_start - _CONTEXT_CHARACTERS)
        before_context_end = min(
            len(before),
            before_end + _CONTEXT_CHARACTERS,
        )
        after_context_start = max(0, after_start - _CONTEXT_CHARACTERS)
        after_context_end = min(
            len(after),
            after_end + _CONTEXT_CHARACTERS,
        )
        split.append(
            replace(
                evidence,
                observation_id=observation_id,
                candidate_index=index,
                candidate_count=candidate_count,
                candidate_before_text=before[
                    before_context_start:before_context_end
                ],
                candidate_after_text=after[
                    after_context_start:after_context_end
                ],
                candidate_before_change_start=(
                    before_start - before_context_start
                ),
                candidate_before_change_end=before_end - before_context_start,
                candidate_after_change_start=after_start - after_context_start,
                candidate_after_change_end=after_end - after_context_start,
            )
        )
    return split


__all__ = [
    "MAX_CANDIDATES_PER_OBSERVATION",
    "edit_statistics",
    "split_dictionary_learning_evidence",
]
