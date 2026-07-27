"""Runtime ownership for edit observation and deferred model classification."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
import uuid

from .background_executor import BackgroundExecutor
from .dictionary_edit_observer import PasteObservation
from .dictionary_learning_candidates import split_dictionary_learning_evidence


class DictionaryLearningQueueWorker:
    """Own one long-lived worker that drains due SQLite jobs."""

    def __init__(
        self,
        *,
        config,
        processor,
        repository,
        executor=None,
        auto_start: bool = True,
    ) -> None:
        self._config = config
        self._processor = processor
        self._repository = repository
        self._executor = executor or BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-dictionary-learning",
        )
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._start_lock = threading.Lock()
        self._task = None
        self._auto_start = auto_start
        if auto_start and self._can_process():
            self._ensure_started()

    def _can_process(self) -> bool:
        learning_config = getattr(self._config, "dictionary_learning", None)
        return bool(
            learning_config is not None
            and learning_config.enabled
            and str(getattr(self._config, "api_key", "")).strip()
        )

    def _ensure_started(self) -> None:
        if not self._auto_start or self._stop_event.is_set():
            return
        with self._start_lock:
            if self._task is None:
                self._task = self._executor.submit(self._run)

    def process_available_once(self) -> bool:
        if not self._can_process():
            return False
        return bool(self._processor.process_next())

    def wake(self) -> None:
        if self._can_process():
            self._ensure_started()
        self._wake_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self._can_process():
                self._wake_event.wait(60.0)
                self._wake_event.clear()
                continue
            try:
                if self.process_available_once():
                    continue
            except Exception as exc:
                print(f"[DictionaryLearning] Queue processing failed: {exc}")

            due_at = self._repository.next_due_at()
            if due_at is None:
                wait_seconds = 60.0
            else:
                wait_seconds = max(0.05, min(60.0, due_at - time.time()))
            self._wake_event.wait(wait_seconds)
            self._wake_event.clear()

    def close(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self._executor.close(wait=True, cancel_futures=True)


@dataclass(frozen=True)
class _PreparedObservation:
    observer: object
    ticket: PasteObservation
    observation_id: str
    created_at: float


def _candidate_change_text(evidence, *, after: bool) -> str:
    text = (
        evidence.candidate_after_text
        if after
        else evidence.candidate_before_text
    )
    start = (
        evidence.candidate_after_change_start
        if after
        else evidence.candidate_before_change_start
    )
    end = (
        evidence.candidate_after_change_end
        if after
        else evidence.candidate_before_change_end
    )
    if start is None or end is None:
        return ""
    return text[start:end].strip()


class AutomaticDictionaryLearningCoordinator:
    """Bridge the synchronous paste workflow to two owned background workers."""

    def __init__(
        self,
        *,
        config,
        observer_factory,
        repository,
        queue_worker,
        processor=None,
        executor=None,
        observation_id_factory=None,
    ) -> None:
        self._config = config
        self._observer_factory = observer_factory
        self._repository = repository
        self._queue_worker = queue_worker
        self._processor = processor
        self._executor = executor or BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-dictionary-observer",
        )
        self._observation_id_factory = observation_id_factory or (
            lambda: str(uuid.uuid4())
        )
        self._lock = threading.Lock()
        self._active_cancel: threading.Event | None = None
        self._on_change = None
        self._last_observation: dict | None = None
        self._closed = False

    def prepare_paste(
        self,
        *,
        raw_text: str,
        pasted_text: str,
        recording_id: str | None,
        mode_name: str,
    ) -> _PreparedObservation | None:
        learning_config = getattr(self._config, "dictionary_learning", None)
        if (
            self._closed
            or learning_config is None
            or not learning_config.enabled
            or not str(getattr(self._config, "api_key", "")).strip()
        ):
            return None

        observer = self._observer_factory(
            excluded_bundle_ids=set(learning_config.excluded_bundle_ids)
        )
        ticket = observer.prepare(
            raw_text=raw_text,
            pasted_text=pasted_text,
            recording_id=recording_id,
            mode_name=mode_name,
        )
        if ticket is None:
            return None
        prepared = _PreparedObservation(
            observer=observer,
            ticket=ticket,
            observation_id=self._observation_id_factory(),
            created_at=time.time(),
        )
        with self._lock:
            original = getattr(ticket, "original", None)
            self._last_observation = {
                "id": f"observation:{prepared.observation_id}",
                "status": "monitoring",
                "app_name": str(getattr(original, "app_name", "")),
                "created_at": prepared.created_at,
            }
        self._emit_change(
            {
                "id": prepared.observation_id,
                "status": "monitoring",
                "source": "observation",
                "dictionary_changed": False,
            }
        )
        return prepared

    def observe_after_paste(
        self,
        prepared: _PreparedObservation | None,
    ) -> None:
        if prepared is None or self._closed:
            return
        with self._lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
            cancel_event = threading.Event()
            self._active_cancel = cancel_event
        self._executor.submit(
            self._observe_and_enqueue,
            prepared,
            cancel_event,
        )

    def _observe_and_enqueue(
        self,
        prepared: _PreparedObservation,
        cancel_event: threading.Event,
    ) -> None:
        try:
            evidence = prepared.observer.observe(
                prepared.ticket,
                cancel_event=cancel_event,
            )
            if evidence is not None:
                candidates = split_dictionary_learning_evidence(
                    evidence,
                    observation_id=prepared.observation_id,
                )
                if candidates:
                    jobs = self._repository.enqueue_many(candidates)
                    self._complete_observation(prepared, status=None)
                    self._emit_change(
                        {
                            "id": prepared.observation_id,
                            "status": "pending",
                            "job_ids": [job.id for job in jobs],
                            "source": "observation",
                            "dictionary_changed": False,
                        }
                    )
                    self._queue_worker.wake()
                    return
            self._complete_observation(prepared, status="no_change")
            self._emit_change(
                {
                    "id": prepared.observation_id,
                    "status": "no_change",
                    "source": "observation",
                    "dictionary_changed": False,
                }
            )
        except Exception as exc:
            print(f"[DictionaryLearning] Edit observation failed: {exc}")
            self._complete_observation(prepared, status="observation_failed")
            self._emit_change(
                {
                    "id": prepared.observation_id,
                    "status": "observation_failed",
                    "source": "observation",
                    "dictionary_changed": False,
                }
            )
        finally:
            with self._lock:
                if self._active_cancel is cancel_event:
                    self._active_cancel = None

    def wake(self) -> None:
        self._queue_worker.wake()

    def set_on_change(self, callback) -> None:
        with self._lock:
            self._on_change = callback
        if self._processor is not None:
            self._processor.set_on_change(callback)

    def list_recent(self, *, limit: int = 100) -> list[dict]:
        jobs = self._repository.list_jobs(
            statuses=(
                "pending",
                "processing",
                "applying",
                "retry",
                "applied",
                "review",
                "ignored",
                "failed",
                "reverted",
            ),
            limit=limit,
        )
        records: list[dict] = []
        for job in jobs:
            result = job.result
            before_text = _candidate_change_text(job.evidence, after=False)
            after_text = _candidate_change_text(job.evidence, after=True)
            record = {
                "id": job.id,
                "status": job.status,
                "term": result.term if result is not None else after_text,
                "aliases": (
                    list(result.aliases)
                    if result is not None
                    else ([before_text] if before_text else [])
                ),
                "confidence": (
                    result.confidence if result is not None else None
                ),
                "reason_code": (
                    result.reason_code if result is not None else ""
                ),
                "before_text": before_text,
                "after_text": after_text,
                "created_at": job.created_at,
            }
            records.append(record)
        with self._lock:
            latest = (
                dict(self._last_observation)
                if self._last_observation is not None
                else None
            )
        if latest is not None:
            records.insert(0, latest)
        return records

    def _complete_observation(
        self,
        prepared: _PreparedObservation,
        *,
        status: str | None,
    ) -> None:
        with self._lock:
            current = self._last_observation
            expected_id = f"observation:{prepared.observation_id}"
            if current is None or current.get("id") != expected_id:
                return
            if status is None:
                self._last_observation = None
                return
            self._last_observation = {
                **current,
                "status": status,
            }

    def _emit_change(self, change: dict) -> None:
        with self._lock:
            callback = self._on_change
        if callback is None:
            return
        try:
            callback(change)
        except Exception as exc:
            print(f"[DictionaryLearning] Change callback failed: {exc}")

    def approve(self, job_id: str) -> bool:
        return bool(self._processor and self._processor.approve(job_id))

    def reject(self, job_id: str) -> bool:
        return bool(self._processor and self._processor.reject(job_id))

    def undo(self, job_id: str) -> bool:
        return bool(self._processor and self._processor.undo(job_id))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._active_cancel is not None:
                self._active_cancel.set()
        self._executor.close(wait=True, cancel_futures=True)
        self._queue_worker.close()


__all__ = [
    "AutomaticDictionaryLearningCoordinator",
    "DictionaryLearningQueueWorker",
]
