"""Bounded observation of user edits made after Vocal More pastes text."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import threading
import time
from typing import Callable

from ..core.accessibility_text import FocusedTextSnapshot
from ..domain.dictionary_learning_models import (
    DictionaryLearningEvidence,
    MAX_EVIDENCE_TEXT_LENGTH,
)


_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")


@dataclass(frozen=True)
class PasteObservation:
    """Focused-field context captured immediately before Cmd+V."""

    original: FocusedTextSnapshot
    raw_text: str
    pasted_text: str
    recording_id: str | None
    mode_name: str


def _normalize_ax_text(value: str) -> str:
    return _ZERO_WIDTH_RE.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))


def _is_plausible_edit(baseline: str, edited: str) -> bool:
    if not baseline or not edited or baseline == edited:
        return False
    if len(edited) < len(baseline) * 0.20:
        return False

    start, baseline_end, edited_end = _change_bounds(baseline, edited)
    removed = baseline_end - start
    added = edited_end - start
    changed = removed + added

    if removed > len(baseline) * 0.50:
        return False
    if changed > max(4, len(baseline) * 2):
        return False
    if max(removed, added) > MAX_EVIDENCE_TEXT_LENGTH:
        return False
    return True


def _change_bounds(before: str, after: str) -> tuple[int, int, int]:
    prefix = 0
    common_length = min(len(before), len(after))
    while prefix < common_length and before[prefix] == after[prefix]:
        prefix += 1

    suffix = 0
    before_remaining = len(before) - prefix
    after_remaining = len(after) - prefix
    while (
        suffix < before_remaining
        and suffix < after_remaining
        and before[len(before) - suffix - 1] == after[len(after) - suffix - 1]
    ):
        suffix += 1

    return prefix, len(before) - suffix, len(after) - suffix


def _bounded_edit_context(before: str, after: str) -> tuple[str, str]:
    """Keep the changed span plus nearby context instead of a document prefix."""
    if len(before) <= MAX_EVIDENCE_TEXT_LENGTH and len(after) <= MAX_EVIDENCE_TEXT_LENGTH:
        return before, after

    start, before_end, after_end = _change_bounds(before, after)
    changed_length = max(before_end - start, after_end - start)
    context_budget = max(0, MAX_EVIDENCE_TEXT_LENGTH - changed_length)
    context_before = context_budget // 2
    context_after = context_budget - context_before

    before_start = max(0, start - context_before)
    after_start = max(0, start - context_before)
    return (
        before[before_start : min(len(before), before_end + context_after)],
        after[after_start : min(len(after), after_end + context_after)],
    )


def _locate_pasted_span(
    original: str,
    baseline: str,
    pasted_text: str,
) -> tuple[str, int, int] | None:
    """Locate the occurrence introduced by this paste, even if text repeats."""
    if not pasted_text:
        return None

    change_start, original_end, _baseline_end = _change_bounds(
        original,
        baseline,
    )
    occurrences: list[int] = []
    cursor = baseline.find(pasted_text)
    while cursor >= 0:
        occurrences.append(cursor)
        cursor = baseline.find(pasted_text, cursor + 1)
    if not occurrences:
        return None

    pasted_start = min(occurrences, key=lambda offset: abs(offset - change_start))
    pasted_end = pasted_start + len(pasted_text)
    original_replaced = original[change_start:original_end]
    return original_replaced, pasted_start, pasted_end


def _project_span_after_edit(
    baseline: str,
    edited: str,
    span_start: int,
    span_end: int,
) -> str:
    """Map one baseline span into edited text while excluding outside edits."""
    projected: list[str] = []
    matcher = SequenceMatcher(None, baseline, edited, autojunk=False)
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag == "insert":
            if span_start <= before_start <= span_end:
                projected.append(edited[after_start:after_end])
            continue

        overlap_start = max(span_start, before_start)
        overlap_end = min(span_end, before_end)
        if overlap_start >= overlap_end:
            continue

        if tag == "equal":
            relative_start = overlap_start - before_start
            relative_end = overlap_end - before_start
            projected.append(
                edited[after_start + relative_start : after_start + relative_end]
            )
        elif tag == "replace":
            projected.append(edited[after_start:after_end])
        # A delete contributes no edited text.

    return "".join(projected)


class DictionaryEditObserver:
    """Poll one exact editable AX element for a short correction window."""

    def __init__(
        self,
        *,
        provider,
        excluded_bundle_ids: set[str] | None = None,
        observation_seconds: float = 15.0,
        poll_interval: float = 0.10,
        post_paste_timeout: float = 1.0,
        settle_seconds: float = 0.50,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._excluded_bundle_ids = set(excluded_bundle_ids or ())
        self._observation_seconds = max(0.0, observation_seconds)
        self._poll_interval = max(0.01, poll_interval)
        self._post_paste_timeout = max(0.0, post_paste_timeout)
        self._settle_seconds = max(0.0, settle_seconds)
        self._clock = clock
        self._sleep = sleep

    def prepare(
        self,
        *,
        raw_text: str,
        pasted_text: str,
        recording_id: str | None,
        mode_name: str,
    ) -> PasteObservation | None:
        if not pasted_text:
            return None
        original = self._provider.capture_focused()
        if original is None or original.is_secure:
            return None
        if original.app_bundle_id in self._excluded_bundle_ids:
            return None
        return PasteObservation(
            original=original,
            raw_text=raw_text[:MAX_EVIDENCE_TEXT_LENGTH],
            pasted_text=pasted_text[:MAX_EVIDENCE_TEXT_LENGTH],
            recording_id=recording_id,
            mode_name=mode_name,
        )

    def observe(
        self,
        ticket: PasteObservation | None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> DictionaryLearningEvidence | None:
        if ticket is None:
            return None
        cancel_event = cancel_event or threading.Event()
        baseline = self._wait_for_pasted_baseline(ticket, cancel_event)
        if baseline is None:
            return None

        original_text = _normalize_ax_text(ticket.original.value)
        baseline_text = _normalize_ax_text(baseline.value)
        pasted_span = _locate_pasted_span(
            original_text,
            baseline_text,
            _normalize_ax_text(ticket.pasted_text),
        )
        if pasted_span is None:
            return None
        original_replaced, pasted_start, pasted_end = pasted_span
        pasted_baseline = baseline_text[pasted_start:pasted_end]
        latest_pasted = pasted_baseline
        last_relevant_change_at: float | None = None
        focus_committed = False
        deadline = self._clock() + self._observation_seconds
        while not cancel_event.is_set():
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._sleep(min(self._poll_interval, remaining))
            observed_at = self._clock()
            if cancel_event.is_set():
                return None
            if observed_at >= deadline:
                break

            current = self._provider.capture_focused()
            if current is None or not ticket.original.is_same_target(current):
                focus_committed = True
                break
            if current.is_secure:
                return None
            normalized = _normalize_ax_text(current.value)
            if not normalized:
                break
            current_pasted = _project_span_after_edit(
                baseline_text,
                normalized,
                pasted_start,
                pasted_end,
            )
            if current_pasted != latest_pasted:
                latest_pasted = current_pasted
                last_relevant_change_at = observed_at

        if cancel_event.is_set():
            return None
        if (
            not focus_committed
            and last_relevant_change_at is not None
            and self._clock() - last_relevant_change_at < self._settle_seconds
        ):
            return None
        if not _is_plausible_edit(pasted_baseline, latest_pasted):
            return None
        bounded_baseline, bounded_edited = _bounded_edit_context(
            pasted_baseline,
            latest_pasted,
        )

        return DictionaryLearningEvidence(
            raw_text=ticket.raw_text,
            pasted_text=ticket.pasted_text,
            original_text=original_replaced[:MAX_EVIDENCE_TEXT_LENGTH],
            baseline_text=bounded_baseline,
            edited_text=bounded_edited,
            app_bundle_id=ticket.original.app_bundle_id,
            app_name=ticket.original.app_name,
            mode_name=ticket.mode_name,
            recording_id=ticket.recording_id,
        )

    def _wait_for_pasted_baseline(
        self,
        ticket: PasteObservation,
        cancel_event: threading.Event,
    ) -> FocusedTextSnapshot | None:
        deadline = self._clock() + self._post_paste_timeout
        while not cancel_event.is_set():
            current = self._provider.capture_focused()
            if current is None or not ticket.original.is_same_target(current):
                return None
            normalized = _normalize_ax_text(current.value)
            if ticket.pasted_text in normalized:
                return FocusedTextSnapshot(
                    target_id=current.target_id,
                    pid=current.pid,
                    value=normalized,
                    role=current.role,
                    subrole=current.subrole,
                    app_bundle_id=current.app_bundle_id,
                    app_name=current.app_name,
                    is_secure=current.is_secure,
                )
            if self._clock() >= deadline:
                return None
            self._sleep(self._poll_interval)
        return None


__all__ = [
    "DictionaryEditObserver",
    "PasteObservation",
]
