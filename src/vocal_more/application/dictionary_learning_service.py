"""Application services for asynchronous automatic dictionary learning."""

from __future__ import annotations

import time

from ..core.dictionary_learning_model import (
    DictionaryLearningRequestError,
    DictionaryLearningResponseError,
)
from ..domain.dictionary_learning_models import validate_decision
from ..domain.dictionary_models import DictionaryMutation


class DictionaryLearningProcessor:
    """Process one durable learning job at a time."""

    def __init__(
        self,
        *,
        repository,
        dictionary,
        model_client,
        on_change=None,
    ) -> None:
        self._repository = repository
        self._dictionary = dictionary
        self._model_client = model_client
        self._on_change = on_change

    def set_on_change(self, callback) -> None:
        self._on_change = callback

    def _emit_change(
        self,
        job_id: str,
        status: str,
        decision=None,
        *,
        source: str,
        dictionary_changed: bool,
        job=None,
    ) -> None:
        if self._on_change is None:
            return
        try:
            change = {
                "id": job_id,
                "status": status,
                "term": getattr(decision, "term", ""),
                "aliases": list(getattr(decision, "aliases", ())),
                "confidence": getattr(decision, "confidence", None),
                "source": source,
                "dictionary_changed": dictionary_changed,
            }
            if (
                job is not None
                and status == "applied"
                and job.candidate_count > 1
            ):
                change["suppress_notification"] = True
            self._on_change(change)
        except Exception as exc:
            print(f"[DictionaryLearning] Change callback failed: {exc}")

    def _emit_observation_summary(self, job) -> None:
        try:
            terms = self._repository.claim_observation_notification(
                job.observation_id
            )
        except Exception as exc:
            print(f"[DictionaryLearning] Notification claim failed: {exc}")
            return
        if terms is None or job.candidate_count <= 1 or not terms:
            return
        if self._on_change is None:
            return
        try:
            self._on_change(
                {
                    "id": job.observation_id,
                    "status": "applied_group",
                    "terms": terms,
                    "source": "automatic",
                    "dictionary_changed": True,
                }
            )
        except Exception as exc:
            print(f"[DictionaryLearning] Change callback failed: {exc}")

    def process_next(self, *, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else now
        job = self._repository.claim_next(now=timestamp)
        if job is None:
            return False

        if job.result is not None:
            try:
                self._dictionary.add_entry(
                    job.result.term,
                    job.result.aliases,
                )
                self._repository.finish(
                    job.id,
                    status="applied",
                    result=job.result,
                    term_created=job.term_created,
                    aliases_added=job.aliases_added,
                    now=timestamp,
                )
                self._emit_change(
                    job.id,
                    "applied",
                    job.result,
                    source="automatic",
                    dictionary_changed=bool(
                        job.term_created or job.aliases_added
                    ),
                    job=job,
                )
            except Exception as exc:
                self._repository.schedule_retry(
                    job.id,
                    error=str(exc),
                    now=timestamp,
                )
                self._emit_change(
                    job.id,
                    "retry",
                    job.result,
                    source="automatic",
                    dictionary_changed=False,
                    job=job,
                )
            self._emit_observation_summary(job)
            return True

        try:
            model_decision = self._model_client.classify(job.evidence)
            decision = validate_decision(
                model_decision,
                job.evidence,
                self._dictionary.snapshot_entries(),
            )
        except DictionaryLearningRequestError as exc:
            if exc.retryable:
                self._repository.schedule_retry(
                    job.id,
                    error=str(exc),
                    now=timestamp,
                )
                status = "retry"
            else:
                self._repository.mark_failed(job.id, error=str(exc), now=timestamp)
                status = "failed"
            self._emit_change(
                job.id,
                status,
                source="automatic",
                dictionary_changed=False,
                job=job,
            )
            self._emit_observation_summary(job)
            return True
        except DictionaryLearningResponseError as exc:
            self._repository.mark_failed(job.id, error=str(exc), now=timestamp)
            self._emit_change(
                job.id,
                "failed",
                source="automatic",
                dictionary_changed=False,
                job=job,
            )
            self._emit_observation_summary(job)
            return True
        except Exception as exc:
            self._repository.mark_failed(job.id, error=str(exc), now=timestamp)
            self._emit_change(
                job.id,
                "failed",
                source="automatic",
                dictionary_changed=False,
                job=job,
            )
            self._emit_observation_summary(job)
            return True

        if decision.action == "add":
            try:
                mutation = self._dictionary.add_entry_with_result(
                    decision.term,
                    decision.aliases,
                    before_apply=lambda planned: self._repository.mark_applying(
                        job.id,
                        result=decision,
                        mutation=planned,
                        now=timestamp,
                    ),
                )
                self._repository.finish(
                    job.id,
                    status="applied",
                    result=decision,
                    term_created=mutation.term_created,
                    aliases_added=mutation.aliases_added,
                    now=timestamp,
                )
                self._emit_change(
                    job.id,
                    "applied",
                    decision,
                    source="automatic",
                    dictionary_changed=bool(
                        mutation.term_created or mutation.aliases_added
                    ),
                    job=job,
                )
            except Exception as exc:
                self._repository.schedule_retry(
                    job.id,
                    error=str(exc),
                    now=timestamp,
                )
                self._emit_change(
                    job.id,
                    "retry",
                    decision,
                    source="automatic",
                    dictionary_changed=False,
                    job=job,
                )
            self._emit_observation_summary(job)
        elif decision.action == "review":
            self._repository.finish(
                job.id,
                status="review",
                result=decision,
                now=timestamp,
            )
            self._emit_change(
                job.id,
                "review",
                decision,
                source="automatic",
                dictionary_changed=False,
                job=job,
            )
            self._emit_observation_summary(job)
        else:
            self._repository.finish(
                job.id,
                status="ignored",
                result=decision,
                now=timestamp,
            )
            self._emit_change(
                job.id,
                "ignored",
                decision,
                source="automatic",
                dictionary_changed=False,
                job=job,
            )
            self._emit_observation_summary(job)
        return True

    def undo(self, job_id: str) -> bool:
        job = self._repository.get(job_id)
        if job is None or job.status != "applied" or job.result is None:
            return False
        mutation = DictionaryMutation(
            term=job.result.term,
            term_created=job.term_created,
            aliases_added=job.aliases_added,
        )
        if not self._dictionary.undo_mutation(mutation):
            return False
        self._repository.mark_reverted(job_id)
        self._emit_change(
            job.id,
            "reverted",
            job.result,
            source="undo",
            dictionary_changed=True,
        )
        return True

    def approve(self, job_id: str) -> bool:
        job = self._repository.get(job_id)
        if job is None or job.status != "review" or job.result is None:
            return False
        mutation = self._dictionary.add_entry_with_result(
            job.result.term,
            job.result.aliases,
        )
        self._repository.finish(
            job.id,
            status="applied",
            result=job.result,
            term_created=mutation.term_created,
            aliases_added=mutation.aliases_added,
        )
        self._emit_change(
            job.id,
            "applied",
            job.result,
            source="review",
            dictionary_changed=bool(
                mutation.term_created or mutation.aliases_added
            ),
        )
        return True

    def reject(self, job_id: str) -> bool:
        job = self._repository.get(job_id)
        if job is None or job.status != "review" or job.result is None:
            return False
        self._repository.finish(
            job.id,
            status="ignored",
            result=job.result,
        )
        self._emit_change(
            job.id,
            "ignored",
            job.result,
            source="review",
            dictionary_changed=False,
        )
        return True


__all__ = ["DictionaryLearningProcessor"]
