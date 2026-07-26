"""Runtime ownership for edit observation and deferred model classification."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from .background_executor import BackgroundExecutor
from .dictionary_edit_observer import PasteObservation


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
        self._lock = threading.Lock()
        self._active_cancel: threading.Event | None = None
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
        return _PreparedObservation(observer=observer, ticket=ticket)

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
                self._repository.enqueue(evidence)
                self._queue_worker.wake()
        except Exception as exc:
            print(f"[DictionaryLearning] Edit observation failed: {exc}")
        finally:
            with self._lock:
                if self._active_cancel is cancel_event:
                    self._active_cancel = None

    def wake(self) -> None:
        self._queue_worker.wake()

    def set_on_change(self, callback) -> None:
        if self._processor is not None:
            self._processor.set_on_change(callback)

    def list_recent(self, *, limit: int = 100) -> list[dict]:
        jobs = self._repository.list_jobs(
            statuses=("applied", "review", "reverted"),
            limit=limit,
        )
        records: list[dict] = []
        for job in jobs:
            if job.result is None:
                continue
            records.append(
                {
                    "id": job.id,
                    "status": job.status,
                    "term": job.result.term,
                    "aliases": list(job.result.aliases),
                    "confidence": job.result.confidence,
                    "reason_code": job.result.reason_code,
                    "created_at": job.created_at,
                }
            )
        return records

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
