"""Application use case for generating meeting notes for saved recordings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..localization import t


MEETING_RUNNING_STATUSES = {"transcribing", "summarizing"}
_TERMINAL_FAILURE_STATUSES = {"pending", "failed"}


@dataclass(frozen=True)
class MeetingNotesRecordingResult:
    """Outcome of generating meeting notes for one recording."""

    recording_id: str
    status: str
    meeting: dict[str, Any] | None = None
    transcript: str | None = None
    billing: dict | None = None
    error: str | None = None


class MeetingNotesRecordingRunner:
    """Coordinate recording-store state around the two-stage meeting service."""

    def __init__(
        self,
        *,
        config,
        recording_store,
        service_factory: Callable[[], object] | None = None,
    ) -> None:
        self._config = config
        self._recording_store = recording_store
        self._service_factory = service_factory or (
            lambda: self._build_default_service()
        )

    def _build_default_service(self):
        from .meeting_notes import MeetingNotesService

        return MeetingNotesService(config=self._config)

    def generate_for_recording(
        self,
        recording_id: str,
        *,
        pcm_data: bytes | None = None,
        on_stage: Callable[[str], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> MeetingNotesRecordingResult:
        begin = self._recording_store.begin_meeting_generation(recording_id)
        recording_status = str(begin.get("recording_status") or "pending")
        if not begin.get("started"):
            reason = str(begin.get("reason") or "failed")
            return MeetingNotesRecordingResult(
                recording_id=recording_id,
                status=reason,
                meeting=begin.get("meeting"),
                error=self._missing_recording_error() if reason == "not_found" else None,
            )

        if self._should_abort(should_abort):
            return self._mark_failed(
                recording_id,
                recording_status,
                self._canceled_error(),
                status="canceled",
            )

        try:
            audio = pcm_data if pcm_data is not None else self._recording_store.get_pcm_data(recording_id)
            if audio is None:
                return self._mark_failed(
                    recording_id,
                    recording_status,
                    self._missing_recording_error(),
                )

            result = self._service_factory().generate(
                audio,
                on_stage=lambda stage: self._handle_stage(
                    recording_id,
                    recording_status,
                    stage,
                    on_stage,
                ),
            )

            if self._should_abort(should_abort):
                return self._mark_failed(
                    recording_id,
                    recording_status,
                    self._canceled_error(),
                    status="canceled",
                )

            meeting = dict(result.notes)
            if result.billing:
                meeting["billing"] = result.billing
            transcript = meeting.get("transcript") or None
            update_kwargs: dict[str, Any] = {"error": None, "meeting": meeting}
            if result.billing:
                update_kwargs["billing"] = result.billing
            self._recording_store.update(
                recording_id,
                "success",
                transcript,
                **update_kwargs,
            )
            return MeetingNotesRecordingResult(
                recording_id=recording_id,
                status=str(meeting.get("status") or "success"),
                meeting=meeting,
                transcript=transcript,
                billing=result.billing,
            )
        except Exception as exc:
            return self._mark_failed(recording_id, recording_status, str(exc))

    def _handle_stage(
        self,
        recording_id: str,
        recording_status: str,
        stage: str,
        on_stage: Callable[[str], None] | None,
    ) -> None:
        meeting_status = "summarizing" if stage == "meeting_summarizing" else "transcribing"
        self._recording_store.update(
            recording_id,
            recording_status,
            error=None,
            meeting={"status": meeting_status},
        )
        if on_stage is not None:
            on_stage(stage)

    def _mark_failed(
        self,
        recording_id: str,
        recording_status: str,
        error: str,
        *,
        status: str = "failed",
    ) -> MeetingNotesRecordingResult:
        meeting = {
            "status": "failed",
            "error": error,
            "minutes": {
                "status": "failed",
                "summary": "",
                "key_points": [],
                "action_items": [],
                "error": error,
            },
        }
        if status == "canceled":
            meeting["reason"] = "canceled"
        self._recording_store.update(
            recording_id,
            self._failure_recording_status(recording_status),
            error=error,
            meeting=meeting,
        )
        return MeetingNotesRecordingResult(
            recording_id=recording_id,
            status=status,
            meeting=meeting,
            error=error,
        )

    @staticmethod
    def _should_abort(should_abort: Callable[[], bool] | None) -> bool:
        return bool(should_abort and should_abort())

    @staticmethod
    def _failure_recording_status(recording_status: str) -> str:
        return "failed" if recording_status in _TERMINAL_FAILURE_STATUSES else recording_status

    def _missing_recording_error(self) -> str:
        return t(self._ui_language(), "settings_recording_not_found")

    def _canceled_error(self) -> str:
        return t(self._ui_language(), "meeting_generation_canceled")

    def _ui_language(self) -> str:
        ui = getattr(self._config, "ui", None)
        return getattr(ui, "language", "zh")
