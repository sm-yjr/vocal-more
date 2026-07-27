"""Pure models and validation rules for automatic dictionary learning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Iterable, Literal

from .dictionary_models import DictEntry, normalize_term


LearningDecisionName = Literal["add", "ignore", "review"]
ValidatedAction = Literal["add", "ignore", "review"]
LearningJobStatus = Literal[
    "pending",
    "processing",
    "applying",
    "retry",
    "applied",
    "review",
    "ignored",
    "failed",
    "reverted",
]

AUTO_ADD_CONFIDENCE = 0.90
REVIEW_CONFIDENCE = 0.0
MAX_LEARNED_TERM_LENGTH = 60
MAX_EVIDENCE_TEXT_LENGTH = 8_000
CURRENT_PROMPT_VERSION = 3

_NUMERIC_OR_DATE_RE = re.compile(
    r"^[零〇一二两三四五六七八九十百千万亿\d年月日号点时分秒"
    r"周星期礼拜上下午夜凌晨早晚:：./\-\s]+$"
)


@dataclass(frozen=True)
class DictionaryLearningEvidence:
    """The minimum text evidence needed to classify one user correction."""

    raw_text: str
    pasted_text: str
    original_text: str
    baseline_text: str
    edited_text: str
    app_bundle_id: str = ""
    app_name: str = ""
    mode_name: str = ""
    recording_id: str | None = None
    observation_id: str = ""
    candidate_index: int = 0
    candidate_count: int = 1
    candidate_before_text: str = ""
    candidate_after_text: str = ""
    candidate_before_change_start: int | None = None
    candidate_before_change_end: int | None = None
    candidate_after_change_start: int | None = None
    candidate_after_change_end: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "DictionaryLearningEvidence":
        if not isinstance(payload, dict):
            raise ValueError("learning evidence must be an object")
        return cls(
            raw_text=str(payload.get("raw_text", ""))[:MAX_EVIDENCE_TEXT_LENGTH],
            pasted_text=str(payload.get("pasted_text", ""))[:MAX_EVIDENCE_TEXT_LENGTH],
            original_text=str(payload.get("original_text", ""))[:MAX_EVIDENCE_TEXT_LENGTH],
            baseline_text=str(payload.get("baseline_text", ""))[:MAX_EVIDENCE_TEXT_LENGTH],
            edited_text=str(payload.get("edited_text", ""))[:MAX_EVIDENCE_TEXT_LENGTH],
            app_bundle_id=str(payload.get("app_bundle_id", ""))[:512],
            app_name=str(payload.get("app_name", ""))[:512],
            mode_name=str(payload.get("mode_name", ""))[:128],
            recording_id=(
                str(payload["recording_id"])[:512]
                if payload.get("recording_id") is not None
                else None
            ),
            observation_id=str(payload.get("observation_id", ""))[:128],
            candidate_index=max(0, int(payload.get("candidate_index", 0))),
            candidate_count=max(1, int(payload.get("candidate_count", 1))),
            candidate_before_text=str(
                payload.get("candidate_before_text", "")
            )[:MAX_EVIDENCE_TEXT_LENGTH],
            candidate_after_text=str(
                payload.get("candidate_after_text", "")
            )[:MAX_EVIDENCE_TEXT_LENGTH],
            candidate_before_change_start=_optional_nonnegative_int(
                payload.get("candidate_before_change_start")
            ),
            candidate_before_change_end=_optional_nonnegative_int(
                payload.get("candidate_before_change_end")
            ),
            candidate_after_change_start=_optional_nonnegative_int(
                payload.get("candidate_after_change_start")
            ),
            candidate_after_change_end=_optional_nonnegative_int(
                payload.get("candidate_after_change_end")
            ),
        )


@dataclass(frozen=True)
class DictionaryLearningDecision:
    """Structured decision returned by the language model."""

    decision: LearningDecisionName
    term: str = ""
    aliases: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason_code: str = ""

    @classmethod
    def from_payload(cls, payload: object) -> "DictionaryLearningDecision":
        if not isinstance(payload, dict):
            raise ValueError("model response must be a JSON object")

        decision = payload.get("decision")
        if decision not in ("add", "ignore", "review"):
            raise ValueError("model response has an invalid decision")

        raw_aliases = payload.get("aliases", [])
        if not isinstance(raw_aliases, list) or not all(
            isinstance(alias, str) for alias in raw_aliases
        ):
            raise ValueError("model response aliases must be a string array")

        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("model response confidence must be numeric") from exc
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("model response confidence must be between 0 and 1")

        return cls(
            decision=decision,
            term=str(payload.get("term", "")),
            aliases=list(raw_aliases),
            confidence=confidence,
            reason_code=str(payload.get("reason_code", ""))[:128],
        )


@dataclass(frozen=True)
class ValidatedDictionaryDecision:
    """A model decision after deterministic local safety checks."""

    action: ValidatedAction
    term: str = ""
    aliases: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason_code: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DictionaryLearningJob:
    """Persisted background classification job."""

    id: str
    evidence: DictionaryLearningEvidence
    status: LearningJobStatus
    created_at: float
    updated_at: float
    attempt_count: int = 0
    next_retry_at: float = 0.0
    error: str = ""
    result: ValidatedDictionaryDecision | None = None
    term_created: bool = False
    aliases_added: list[str] = field(default_factory=list)
    observation_id: str = ""
    candidate_index: int = 0
    candidate_count: int = 1
    notification_emitted: bool = False
    model: str = "qwen3.7-plus"
    prompt_version: int = CURRENT_PROMPT_VERSION


def _ignore(reason_code: str) -> ValidatedDictionaryDecision:
    return ValidatedDictionaryDecision(action="ignore", reason_code=reason_code)


def _contains_lexical_content(value: str) -> bool:
    return any(char.isalnum() for char in value)


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    return max(0, int(value))


def _occurrence_overlaps_change(
    value: str,
    scope: str,
    change_start: int | None,
    change_end: int | None,
) -> bool:
    if value not in scope:
        return False
    if change_start is None or change_end is None:
        return True
    cursor = scope.find(value)
    while cursor >= 0:
        occurrence_end = cursor + len(value)
        if change_start == change_end:
            if cursor <= change_start <= occurrence_end:
                return True
        elif cursor < change_end and occurrence_end > change_start:
            return True
        cursor = scope.find(value, cursor + 1)
    return False


def _is_numeric_or_date_change(term: str, aliases: Iterable[str]) -> bool:
    values = [term, *aliases]
    return bool(values) and all(_NUMERIC_OR_DATE_RE.fullmatch(value) for value in values)


def _has_dictionary_conflict(
    term: str,
    aliases: Iterable[str],
    existing_entries: Iterable[DictEntry],
) -> bool:
    alias_to_term: dict[str, str] = {}
    for entry in existing_entries:
        alias_to_term.setdefault(entry.term.casefold(), entry.term.casefold())
        for alias in entry.aliases:
            alias_to_term[alias.casefold()] = entry.term.casefold()

    canonical = term.casefold()
    existing_target = alias_to_term.get(canonical)
    if existing_target is not None and existing_target != canonical:
        return True
    return any(
        alias.casefold() in alias_to_term
        and alias_to_term[alias.casefold()] != canonical
        for alias in aliases
    )


def validate_decision(
    decision: DictionaryLearningDecision,
    evidence: DictionaryLearningEvidence,
    existing_entries: Iterable[DictEntry] = (),
) -> ValidatedDictionaryDecision:
    """Apply local invariants before a model result can mutate the dictionary."""

    if decision.decision == "ignore":
        return _ignore(decision.reason_code or "model_ignored")

    term = normalize_term(decision.term)
    aliases: list[str] = []
    seen: set[str] = set()
    for raw_alias in decision.aliases:
        alias = normalize_term(raw_alias)
        folded = alias.casefold()
        if not alias or folded == term.casefold() or folded in seen:
            continue
        seen.add(folded)
        aliases.append(alias)

    if not term:
        return _ignore("empty_term")
    if len(term) > MAX_LEARNED_TERM_LENGTH:
        return _ignore("term_too_long")
    if not aliases:
        return _ignore("empty_aliases")
    if not _contains_lexical_content(term) or any(
        not _contains_lexical_content(alias) for alias in aliases
    ):
        return _ignore("non_lexical_mapping")
    if any(mark in term for mark in ("\n", "\r", "。", "！", "？", "!", "?")):
        return _ignore("sentence_sized_term")
    if (
        evidence.candidate_after_text
        and not _occurrence_overlaps_change(
            term,
            evidence.candidate_after_text,
            evidence.candidate_after_change_start,
            evidence.candidate_after_change_end,
        )
    ):
        return _ignore("term_not_in_candidate")
    if term not in evidence.edited_text:
        return _ignore("term_not_in_edited_text")
    if evidence.candidate_before_text and any(
        not _occurrence_overlaps_change(
            alias,
            evidence.candidate_before_text,
            evidence.candidate_before_change_start,
            evidence.candidate_before_change_end,
        )
        for alias in aliases
    ):
        return _ignore("alias_not_in_candidate")
    if any(
        alias not in evidence.pasted_text and alias not in evidence.raw_text
        for alias in aliases
    ):
        return _ignore("alias_not_in_pasted_text")
    if _is_numeric_or_date_change(term, aliases):
        return _ignore("numeric_or_date_change")
    if _has_dictionary_conflict(term, aliases, existing_entries):
        return _ignore("dictionary_conflict")

    action: ValidatedAction
    action = (
        "add"
        if decision.decision == "add"
        and decision.confidence >= AUTO_ADD_CONFIDENCE
        else "review"
    )

    reason_code = decision.reason_code or "model_classification"
    return ValidatedDictionaryDecision(
        action=action,
        term=term if action != "ignore" else "",
        aliases=aliases if action != "ignore" else [],
        confidence=decision.confidence,
        reason_code=reason_code,
    )


__all__ = [
    "AUTO_ADD_CONFIDENCE",
    "CURRENT_PROMPT_VERSION",
    "DictionaryLearningDecision",
    "DictionaryLearningEvidence",
    "DictionaryLearningJob",
    "LearningJobStatus",
    "MAX_EVIDENCE_TEXT_LENGTH",
    "REVIEW_CONFIDENCE",
    "ValidatedDictionaryDecision",
    "validate_decision",
]
