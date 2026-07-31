"""Isolated recording-transcription retry use case and bounded runtime lane."""

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time
from typing import Callable, Protocol

from ..localization import t


RETRY_ASR_MODEL = "qwen3.5-omni-plus"
_STOP = object()


class RecordingRepositoryPort(Protocol):
    """Data operations required by retry transcription."""

    def get_pcm_data(self, recording_id: str) -> bytes | None: ...

    def get_language(self, recording_id: str) -> str: ...

    def update(
        self,
        recording_id: str,
        status: str,
        transcript=None,
        **kwargs,
    ) -> bool: ...


class BatchTranscriberPort(Protocol):
    """Provider operations required by retry transcription."""

    def transcribe(self, pcm_data: bytes, **kwargs) -> str: ...

    def get_last_metering(self) -> dict | None: ...


@dataclass(frozen=True)
class RecordingRetryOutcome:
    recording_id: str
    status: str
    transcript: str | None = None
    error: str | None = None
    billing: dict | None = None


@dataclass(frozen=True)
class RecordingRetryEvent:
    kind: str
    recording_id: str
    transcript: str | None = None
    error: str | None = None
    billing: dict | None = None


@dataclass(frozen=True)
class RecordingRetrySubmission:
    status: str

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True)
class RecordingRetryShutdown:
    drained: bool
    worker_alive: bool
    active_count: int


class RecordingRetryService:
    """Synchronously coordinate repository state around one ASR retry."""

    def __init__(
        self,
        *,
        config: object,
        recording_repository: RecordingRepositoryPort,
        transcriber_factory: Callable[[], BatchTranscriberPort],
        billing_merger: Callable[[dict | None], dict | None],
    ) -> None:
        self._config = config
        self._repository = recording_repository
        self._transcriber_factory = transcriber_factory
        self._billing_merger = billing_merger

    def retry(
        self,
        recording_id: str,
        *,
        should_commit: Callable[[], bool] = lambda: True,
        commit_if_active: Callable[[Callable[[], bool]], bool] | None = None,
    ) -> RecordingRetryOutcome:
        try:
            pcm_data = self._repository.get_pcm_data(recording_id)
        except Exception as exc:
            return self._commit_failure(
                recording_id,
                str(exc),
                should_commit=should_commit,
                commit_if_active=commit_if_active,
            )
        if pcm_data is None:
            return self._commit_failure(
                recording_id,
                self._message("settings_recording_not_found"),
                should_commit=should_commit,
                commit_if_active=commit_if_active,
            )

        transcriber: BatchTranscriberPort | None = None
        billing: dict | None = None
        try:
            transcriber = self._transcriber_factory()
            language = self._repository.get_language(recording_id)
            transcript = transcriber.transcribe(
                pcm_data,
                model_override=RETRY_ASR_MODEL,
                language_override=language,
            )
            billing = self._read_billing(transcriber)
        except Exception as exc:
            if transcriber is not None:
                billing = self._read_billing(transcriber)
            return self._commit_failure(
                recording_id,
                str(exc),
                billing=billing,
                should_commit=should_commit,
                commit_if_active=commit_if_active,
            )

        normalized = str(transcript or "").strip()
        if not normalized:
            return self._commit_failure(
                recording_id,
                self._message("settings_empty_transcription"),
                billing=billing,
                should_commit=should_commit,
                commit_if_active=commit_if_active,
            )
        committed = self._commit(
            lambda: self._repository.update(
                recording_id,
                "success",
                normalized,
                error=None,
                billing=billing,
            ),
            should_commit=should_commit,
            commit_if_active=commit_if_active,
        )
        if not committed:
            return RecordingRetryOutcome(recording_id, "canceled")
        return RecordingRetryOutcome(
            recording_id=recording_id,
            status="completed",
            transcript=normalized,
            billing=billing,
        )

    def _commit_failure(
        self,
        recording_id: str,
        error: str,
        *,
        billing: dict | None = None,
        should_commit: Callable[[], bool],
        commit_if_active: Callable[[Callable[[], bool]], bool] | None = None,
    ) -> RecordingRetryOutcome:
        update_kwargs = {"error": error}
        if billing is not None:
            update_kwargs["billing"] = billing
        committed = self._commit(
            lambda: self._repository.update(
                recording_id,
                "failed",
                **update_kwargs,
            ),
            should_commit=should_commit,
            commit_if_active=commit_if_active,
        )
        if not committed:
            return RecordingRetryOutcome(recording_id, "canceled")
        return RecordingRetryOutcome(
            recording_id=recording_id,
            status="failed",
            error=error,
            billing=billing,
        )

    @staticmethod
    def _commit(
        action: Callable[[], bool],
        *,
        should_commit: Callable[[], bool],
        commit_if_active: Callable[[Callable[[], bool]], bool] | None,
    ) -> bool:
        if commit_if_active is not None:
            return commit_if_active(action)
        if not should_commit():
            return False
        return bool(action())

    def _read_billing(self, transcriber: BatchTranscriberPort) -> dict | None:
        try:
            return self._billing_merger(transcriber.get_last_metering())
        except Exception:
            return None

    def _message(self, key: str) -> str:
        ui = getattr(self._config, "ui", None)
        language = getattr(ui, "language", "zh")
        return t(language, key)


@dataclass(frozen=True)
class _LaneJob:
    run: Callable[[], None]
    cancel: Callable[[], None]


class _BoundedJobLane:
    """One daemon worker with a hard bound on running plus queued jobs."""

    def __init__(self, *, capacity: int, thread_name: str) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._queue: queue.Queue[_LaneJob | object] = queue.Queue(maxsize=capacity)
        self._slots = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._closed = False
        self._worker = threading.Thread(
            target=self._run,
            name=thread_name,
            daemon=True,
        )
        self._worker.start()

    def submit(self, job: _LaneJob) -> bool:
        if not self._slots.acquire(blocking=False):
            return False
        with self._lock:
            if self._closed:
                self._slots.release()
                return False
            self._queue.put_nowait(job)
        return True

    def close(self, *, timeout: float) -> bool:
        canceled_jobs: list[_LaneJob] = []
        with self._lock:
            if not self._closed:
                self._closed = True
                while True:
                    try:
                        queued = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if queued is not _STOP:
                        assert isinstance(queued, _LaneJob)
                        canceled_jobs.append(queued)
                    self._queue.task_done()
                self._queue.put_nowait(_STOP)

        for job in canceled_jobs:
            try:
                job.cancel()
            except Exception as exc:
                print(f"[RecordingRetry] Queued job cancellation failed: {exc}")
            finally:
                self._slots.release()

        if threading.current_thread() is not self._worker:
            self._worker.join(max(0.0, timeout))
        return not self._worker.is_alive()

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is _STOP:
                    return
                assert isinstance(job, _LaneJob)
                try:
                    job.run()
                except Exception as exc:
                    # A single malformed job must not permanently kill the lane.
                    print(f"[RecordingRetry] Worker job failed: {exc}")
            finally:
                if job is not _STOP:
                    self._slots.release()
                self._queue.task_done()


class RecordingRetryRuntime:
    """Own retry admission, deduplication, invalidation, and worker lifecycle."""

    def __init__(self, *, service: RecordingRetryService, capacity: int = 2) -> None:
        self._service = service
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._closed = False
        self._generation = 0
        self._active_ids: set[str] = set()
        self._inflight_commits = 0
        self._inflight_callbacks = 0
        self._callback_threads: dict[int, int] = {}
        self._lane = _BoundedJobLane(
            capacity=capacity,
            thread_name="vocal-more-recording-retry",
        )

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active_ids)

    def submit(
        self,
        recording_id: str,
        on_event: Callable[[RecordingRetryEvent], None],
    ) -> RecordingRetrySubmission:
        with self._lock:
            if self._closed:
                return RecordingRetrySubmission("closed")
            if recording_id in self._active_ids:
                return RecordingRetrySubmission("duplicate")
            generation = self._generation
            self._active_ids.add(recording_id)

        start_gate = threading.Event()
        job = _LaneJob(
            run=lambda: self._run_job(
                recording_id,
                generation,
                start_gate,
                on_event,
            ),
            cancel=lambda: self._discard(recording_id),
        )
        if not self._lane.submit(job):
            with self._lock:
                status = "closed" if self._closed else "busy"
            self._discard(recording_id)
            return RecordingRetrySubmission(status)

        try:
            emitted = self._emit_if_active(
                recording_id,
                generation,
                on_event,
                RecordingRetryEvent("started", recording_id),
            )
        finally:
            start_gate.set()
        if not emitted or not self._is_generation_open(generation):
            self._discard(recording_id)
            return RecordingRetrySubmission("closed")
        return RecordingRetrySubmission("accepted")

    def close(self, *, timeout: float = 0.5) -> RecordingRetryShutdown:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            if not self._closed:
                self._closed = True
                self._generation += 1

        worker_drained = self._lane.close(
            timeout=max(0.0, deadline - time.monotonic()),
        )

        current_thread_id = threading.get_ident()
        with self._condition:
            while True:
                callbacks_owned_here = self._callback_threads.get(
                    current_thread_id,
                    0,
                )
                pending_leases = (
                    self._inflight_commits
                    + self._inflight_callbacks
                    - callbacks_owned_here
                )
                if pending_leases <= 0:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            active_count = len(self._active_ids)
            drained = (
                worker_drained
                and self._inflight_commits == 0
                and self._inflight_callbacks == 0
                and active_count == 0
            )
            return RecordingRetryShutdown(
                drained=drained,
                worker_alive=not worker_drained,
                active_count=active_count,
            )

    def _run_job(
        self,
        recording_id: str,
        generation: int,
        start_gate: threading.Event,
        on_event: Callable[[RecordingRetryEvent], None],
    ) -> None:
        start_gate.wait()
        try:
            if not self._is_active(recording_id, generation):
                return
            try:
                outcome = self._service.retry(
                    recording_id,
                    should_commit=lambda: self._is_active(recording_id, generation),
                    commit_if_active=lambda action: self._commit_if_active(
                        recording_id,
                        generation,
                        action,
                    ),
                )
            except Exception as exc:
                if self._is_active(recording_id, generation):
                    self._emit_if_active(
                        recording_id,
                        generation,
                        on_event,
                        RecordingRetryEvent(
                            "failed",
                            recording_id,
                            error=str(exc),
                        ),
                    )
                return
            if outcome.status in {"completed", "failed"}:
                self._emit_if_active(
                    recording_id,
                    generation,
                    on_event,
                    RecordingRetryEvent(
                        kind=outcome.status,
                        recording_id=recording_id,
                        transcript=outcome.transcript,
                        error=outcome.error,
                        billing=outcome.billing,
                    ),
                )
        finally:
            self._discard(recording_id)

    def _is_active(self, recording_id: str, generation: int) -> bool:
        with self._lock:
            return (
                not self._closed
                and generation == self._generation
                and recording_id in self._active_ids
            )

    def _is_generation_open(self, generation: int) -> bool:
        with self._lock:
            return not self._closed and generation == self._generation

    def _commit_if_active(
        self,
        recording_id: str,
        generation: int,
        action: Callable[[], bool],
    ) -> bool:
        with self._condition:
            if (
                self._closed
                or generation != self._generation
                or recording_id not in self._active_ids
            ):
                return False
            self._inflight_commits += 1
        try:
            return bool(action())
        finally:
            with self._condition:
                self._inflight_commits -= 1
                self._condition.notify_all()

    def _discard(self, recording_id: str) -> None:
        with self._condition:
            self._active_ids.discard(recording_id)
            self._condition.notify_all()

    def _emit_if_active(
        self,
        recording_id: str,
        generation: int,
        callback: Callable[[RecordingRetryEvent], None],
        event: RecordingRetryEvent,
    ) -> bool:
        thread_id = threading.get_ident()
        with self._condition:
            if (
                self._closed
                or generation != self._generation
                or recording_id not in self._active_ids
            ):
                return False
            self._inflight_callbacks += 1
            self._callback_threads[thread_id] = (
                self._callback_threads.get(thread_id, 0) + 1
            )
        try:
            self._emit(callback, event)
        finally:
            with self._condition:
                self._inflight_callbacks -= 1
                remaining = self._callback_threads[thread_id] - 1
                if remaining:
                    self._callback_threads[thread_id] = remaining
                else:
                    self._callback_threads.pop(thread_id, None)
                self._condition.notify_all()
        return True

    @staticmethod
    def _emit(
        callback: Callable[[RecordingRetryEvent], None],
        event: RecordingRetryEvent,
    ) -> None:
        try:
            callback(event)
        except Exception as exc:
            print(f"[RecordingRetry] Event callback failed: {exc}")


__all__ = [
    "BatchTranscriberPort",
    "RETRY_ASR_MODEL",
    "RecordingRepositoryPort",
    "RecordingRetryEvent",
    "RecordingRetryOutcome",
    "RecordingRetryRuntime",
    "RecordingRetryService",
    "RecordingRetryShutdown",
    "RecordingRetrySubmission",
]
