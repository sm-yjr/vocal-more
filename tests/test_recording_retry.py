from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock


def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not met before timeout")


def _service(
    *,
    pcm_data: bytes | None = b"pcm",
    transcript: str = "转写结果",
    error: Exception | None = None,
):
    from vocal_more.application.recording_retry import RecordingRetryService

    repository = MagicMock()
    repository.get_pcm_data.return_value = pcm_data
    repository.get_language.return_value = "zh"
    repository.update.return_value = True
    engine = MagicMock()
    if error is not None:
        engine.transcribe.side_effect = error
    else:
        engine.transcribe.return_value = transcript
    engine.get_last_metering.return_value = {"cost_cny": 0.12}
    service = RecordingRetryService(
        config=SimpleNamespace(ui=SimpleNamespace(language="zh")),
        recording_repository=repository,
        transcriber_factory=lambda: engine,
        billing_merger=lambda value: {"total": value["cost_cny"]},
    )
    return service, repository, engine


def test_retry_service_commits_transcript_language_and_billing():
    service, repository, engine = _service()

    outcome = service.retry("rec-1")

    assert outcome.status == "completed"
    assert outcome.transcript == "转写结果"
    assert outcome.billing == {"total": 0.12}
    engine.transcribe.assert_called_once_with(
        b"pcm",
        model_override="qwen3.5-omni-plus",
        language_override="zh",
    )
    repository.update.assert_called_once_with(
        "rec-1",
        "success",
        "转写结果",
        error=None,
        billing={"total": 0.12},
    )


def test_retry_service_marks_missing_and_empty_recordings_failed():
    missing, missing_repository, _ = _service(pcm_data=None)
    empty, empty_repository, _ = _service(transcript="  ")

    missing_outcome = missing.retry("missing")
    empty_outcome = empty.retry("empty")

    assert missing_outcome.status == "failed"
    assert missing_outcome.error
    missing_repository.update.assert_called_once_with(
        "missing",
        "failed",
        error=missing_outcome.error,
    )
    assert empty_outcome.status == "failed"
    assert empty_outcome.error
    empty_repository.update.assert_called_once_with(
        "empty",
        "failed",
        error=empty_outcome.error,
        billing={"total": 0.12},
    )


def test_retry_service_persists_provider_failure_with_available_billing():
    service, repository, _ = _service(error=RuntimeError("network down"))

    outcome = service.retry("rec-1")

    assert outcome.status == "failed"
    assert outcome.error == "network down"
    repository.update.assert_called_once_with(
        "rec-1",
        "failed",
        error="network down",
        billing={"total": 0.12},
    )


def test_retry_service_turns_repository_read_failure_into_failed_outcome():
    service, repository, _ = _service()
    repository.get_pcm_data.side_effect = RuntimeError("database unavailable")

    outcome = service.retry("rec-1")

    assert outcome.status == "failed"
    assert outcome.error == "database unavailable"
    repository.update.assert_called_once_with(
        "rec-1",
        "failed",
        error="database unavailable",
    )


def test_retry_service_drops_late_commit_after_runtime_invalidation():
    service, repository, _ = _service()

    outcome = service.retry("rec-1", should_commit=lambda: False)

    assert outcome.status == "canceled"
    repository.update.assert_not_called()


def test_retry_service_uses_commit_barrier_for_repository_write():
    service, repository, _ = _service()
    attempted = []

    outcome = service.retry(
        "rec-1",
        commit_if_active=lambda action: attempted.append(action) or False,
    )

    assert outcome.status == "canceled"
    assert len(attempted) == 1
    repository.update.assert_not_called()


def test_retry_service_cancels_when_recording_disappears_before_commit():
    service, repository, _ = _service()
    repository.update.return_value = False

    outcome = service.retry("rec-1")

    assert outcome.status == "canceled"


def test_retry_runtime_deduplicates_ids_and_bounds_total_jobs():
    from vocal_more.application.recording_retry import (
        RecordingRetryOutcome,
        RecordingRetryRuntime,
    )

    started = threading.Event()
    release = threading.Event()
    service = MagicMock()

    def retry(recording_id, *, should_commit, commit_if_active):
        started.set()
        release.wait(1)
        return RecordingRetryOutcome(
            recording_id=recording_id,
            status="completed",
            transcript="done",
        )

    service.retry.side_effect = retry
    runtime = RecordingRetryRuntime(service=service, capacity=1)
    events = []
    try:
        first = runtime.submit("rec-1", events.append)
        assert first.status == "accepted"
        assert started.wait(1)

        duplicate = runtime.submit("rec-1", events.append)
        busy = runtime.submit("rec-2", events.append)

        assert duplicate.status == "duplicate"
        assert busy.status == "busy"
        assert service.retry.call_count == 1
    finally:
        release.set()
        runtime.close(timeout=1)


def test_retry_runtime_emits_lifecycle_events_and_rejects_after_close():
    from vocal_more.application.recording_retry import (
        RecordingRetryOutcome,
        RecordingRetryRuntime,
    )

    service = MagicMock()
    service.retry.return_value = RecordingRetryOutcome(
        recording_id="rec-1",
        status="completed",
        transcript="done",
    )
    runtime = RecordingRetryRuntime(service=service, capacity=2)
    events = []

    submission = runtime.submit("rec-1", events.append)
    _wait_until(lambda: len(events) == 2)
    runtime.close(timeout=1)
    closed = runtime.submit("rec-2", events.append)

    assert submission.status == "accepted"
    assert [event.kind for event in events] == ["started", "completed"]
    assert events[-1].transcript == "done"
    assert closed.status == "closed"


def test_retry_runtime_suppresses_late_events_when_closed():
    from vocal_more.application.recording_retry import (
        RecordingRetryOutcome,
        RecordingRetryRuntime,
    )

    started = threading.Event()
    release = threading.Event()

    class BlockingService:
        def retry(self, recording_id, *, should_commit, commit_if_active):
            started.set()
            release.wait(1)
            assert should_commit() is False
            return RecordingRetryOutcome(recording_id, "canceled")

    runtime = RecordingRetryRuntime(service=BlockingService(), capacity=1)
    events = []
    runtime.submit("rec-1", events.append)
    assert started.wait(1)

    runtime.close(timeout=0)
    release.set()
    _wait_until(lambda: runtime.active_count == 0)

    assert [event.kind for event in events] == ["started"]


def test_retry_runtime_reports_unexpected_failure_and_keeps_worker_alive():
    from vocal_more.application.recording_retry import (
        RecordingRetryOutcome,
        RecordingRetryRuntime,
    )

    class FlakyService:
        def retry(self, recording_id, *, should_commit, commit_if_active):
            if recording_id == "broken":
                raise RuntimeError("unexpected failure")
            return RecordingRetryOutcome(recording_id, "completed", transcript="done")

    runtime = RecordingRetryRuntime(service=FlakyService(), capacity=1)
    events = []
    try:
        assert runtime.submit("broken", events.append).accepted
        _wait_until(lambda: runtime.active_count == 0)
        assert runtime.submit("healthy", events.append).accepted
        _wait_until(lambda: runtime.active_count == 0)
    finally:
        runtime.close(timeout=1)

    assert [(event.kind, event.recording_id) for event in events] == [
        ("started", "broken"),
        ("failed", "broken"),
        ("started", "healthy"),
        ("completed", "healthy"),
    ]
    assert events[1].error == "unexpected failure"


def test_retry_runtime_close_is_bounded_while_repository_commit_is_running():
    from vocal_more.application.recording_retry import (
        RecordingRetryOutcome,
        RecordingRetryRuntime,
    )

    commit_started = threading.Event()
    release_commit = threading.Event()
    close_finished = threading.Event()
    shutdown_reports = []

    class CommitService:
        def retry(self, recording_id, *, should_commit, commit_if_active):
            def commit():
                commit_started.set()
                release_commit.wait(1)
                return True

            committed = commit_if_active(commit)
            status = "completed" if committed else "canceled"
            return RecordingRetryOutcome(recording_id, status, transcript="done")

    runtime = RecordingRetryRuntime(service=CommitService(), capacity=1)
    events = []
    runtime.submit("rec-1", events.append)
    assert commit_started.wait(1)

    closer = threading.Thread(
        target=lambda: (
            shutdown_reports.append(runtime.close(timeout=0)),
            close_finished.set(),
        ),
    )
    closer.start()
    closed_within_deadline = close_finished.wait(0.1)

    release_commit.set()
    closer.join(1)
    drained = runtime.close(timeout=1)

    assert closed_within_deadline is True
    assert shutdown_reports[0].drained is False
    assert shutdown_reports[0].worker_alive is True
    assert drained.drained is True
    assert [event.kind for event in events] == ["started"]


def test_retry_runtime_close_waits_for_leased_event_callback():
    from vocal_more.application.recording_retry import (
        RecordingRetryOutcome,
        RecordingRetryRuntime,
    )

    about_to_emit = threading.Event()
    release_emit = threading.Event()
    close_finished = threading.Event()

    class ImmediateService:
        def retry(self, recording_id, *, should_commit, commit_if_active):
            return RecordingRetryOutcome(recording_id, "completed", transcript="done")

    runtime = RecordingRetryRuntime(service=ImmediateService(), capacity=1)
    original_emit = runtime._emit

    def delayed_emit(callback, event):
        if event.kind == "completed":
            about_to_emit.set()
            release_emit.wait(1)
        original_emit(callback, event)

    runtime._emit = delayed_emit
    events = []
    runtime.submit("rec-1", events.append)
    assert about_to_emit.wait(1)

    closer = threading.Thread(
        target=lambda: (runtime.close(timeout=1), close_finished.set()),
    )
    closer.start()
    assert not close_finished.wait(0.05)

    release_emit.set()
    assert close_finished.wait(1)
    closer.join(1)
    assert [event.kind for event in events] == ["started", "completed"]


def test_retry_runtime_suppresses_started_event_if_close_wins_admission_race():
    from vocal_more.application.recording_retry import RecordingRetryRuntime

    admitted = threading.Event()
    release_submit = threading.Event()
    runtime = RecordingRetryRuntime(service=MagicMock(), capacity=1)
    original_submit = runtime._lane.submit

    def delayed_submit(job):
        accepted = original_submit(job)
        admitted.set()
        release_submit.wait(1)
        return accepted

    runtime._lane.submit = delayed_submit
    events = []
    submissions = []
    submitter = threading.Thread(
        target=lambda: submissions.append(runtime.submit("rec-1", events.append)),
    )
    submitter.start()
    assert admitted.wait(1)

    runtime.close(timeout=0)
    release_submit.set()
    submitter.join(1)
    runtime.close(timeout=1)

    assert submissions[0].status == "closed"
    assert events == []


def test_reentrant_event_close_does_not_report_drained_before_callback_returns():
    from vocal_more.application.recording_retry import (
        RecordingRetryEvent,
        RecordingRetryRuntime,
    )

    runtime = RecordingRetryRuntime(service=MagicMock(), capacity=1)
    with runtime._lock:
        runtime._active_ids.add("rec-1")
        generation = runtime._generation
    shutdown_reports = []

    def close_from_callback(_event):
        runtime._discard("rec-1")
        shutdown_reports.append(runtime.close(timeout=1))

    runtime._emit_if_active(
        "rec-1",
        generation,
        close_from_callback,
        RecordingRetryEvent("started", "rec-1"),
    )

    assert shutdown_reports[0].drained is False
    assert runtime.close(timeout=1).drained is True


def test_submit_returns_closed_when_started_callback_closes_runtime():
    from vocal_more.application.recording_retry import RecordingRetryRuntime

    runtime = RecordingRetryRuntime(service=MagicMock(), capacity=1)
    shutdown_reports = []

    submission = runtime.submit(
        "rec-1",
        lambda _event: shutdown_reports.append(runtime.close(timeout=0)),
    )
    runtime.close(timeout=1)

    assert submission.status == "closed"
    assert shutdown_reports[0].drained is False
