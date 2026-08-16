"""Meeting mode: toggle recording, then generate two-speaker notes."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Callable, Optional

from ..application.background_executor import BackgroundExecutor, TaskHandle
from ..application.meeting_jobs import MeetingNotesRecordingRunner
from ..application.meeting_notes import DEFAULT_MEETING_NOTES_MODEL, MeetingNotesService
from ..config import get_config
from ..core.audio_recorder import AudioRecorder
from ..localization import format_microphone_start_error, t
from .base_mode import BaseMode, ModeState


class MeetingMode(BaseMode):
    """Toggle-style meeting recording mode with two-stage note generation."""

    def __init__(
        self,
        on_state_change: Optional[Callable[[ModeState], None]] = None,
        on_result: Optional[Callable[[str], None]] = None,
        on_partial_result: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_processing_stage: Optional[Callable[[str], None]] = None,
        on_audio_level: Optional[Callable[[float], None]] = None,
        recording_store: Optional[object] = None,
        on_meeting_result: Optional[Callable[[str], None]] = None,
        recorder_factory: Optional[Callable[..., object]] = None,
        service_factory: Optional[Callable[[], object]] = None,
    ) -> None:
        super().__init__(
            on_state_change,
            on_result,
            on_partial_result,
            on_error,
            on_processing_stage,
            on_audio_level,
        )
        self.config = get_config()
        self._recording_store = recording_store
        self._on_meeting_result = on_meeting_result
        self._service_factory = service_factory or (
            lambda: MeetingNotesService(config=self.config)
        )
        self._recorder = (recorder_factory or AudioRecorder)(
            on_audio_level=on_audio_level
        )
        self._processing_executor = BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-meeting-finish",
        )
        self._processing_thread: Optional[TaskHandle[None]] = None
        self._active_session_token = 0
        self._active_recording_id: Optional[str] = None

    @property
    def name(self) -> str:
        return "Meeting"

    @property
    def description(self) -> str:
        return "Press Fn to start a meeting, press again to generate speaker notes"

    def on_hotkey_pressed(self) -> None:
        if self._state == ModeState.IDLE:
            self._start_recording()
        elif self._state == ModeState.RECORDING:
            self._stop_recording()

    def on_hotkey_released(self) -> None:
        return None

    def _start_recording(self) -> None:
        session_token = self._begin_session()
        self._active_session_token = session_token
        self._active_recording_id = None
        self._set_state(ModeState.STARTING)
        session_audio_config = deepcopy(self.config.audio)
        try:
            self._start_audio_capture(session_audio_config)
        except Exception as exc:
            if (
                not self._is_active_session(session_token)
                or self._state != ModeState.STARTING
            ):
                return
            self._report_startup_failure(exc)
            return
        if (
            not self._is_active_session(session_token)
            or self._state != ModeState.STARTING
        ):
            self._recorder.stop()
            return
        self._set_state(ModeState.RECORDING)

    def _report_startup_failure(self, exc: Exception) -> None:
        if self.on_error:
            self.on_error(
                format_microphone_start_error(self.config.ui.language, exc)
            )
        self._set_state(ModeState.FAILED)
        self._set_state(ModeState.IDLE)

    def _stop_recording(self) -> None:
        self._set_state(ModeState.STOPPING)
        pcm_data = self._recorder.stop()
        if len(pcm_data) < 3200:
            if self.on_error:
                self.on_error(t(self.config.ui.language, "mode_recording_too_short"))
            self._set_state(ModeState.IDLE)
            return

        self._set_state(ModeState.PROCESSING)
        session_token = self._active_session_token
        self._processing_thread = self._processing_executor.submit(
            self._finish_meeting,
            pcm_data,
            session_token,
        )

    def _finish_meeting(self, pcm_data: bytes, session_token: int) -> None:
        recording_id = None
        try:
            if self._recording_store is not None:
                recording_id = self._recording_store.save(
                    pcm_data,
                    "meeting",
                    DEFAULT_MEETING_NOTES_MODEL,
                    language=self.config.asr.language,
                )
                self._active_recording_id = recording_id
                result = MeetingNotesRecordingRunner(
                    config=self.config,
                    recording_store=self._recording_store,
                    service_factory=self._service_factory,
                ).generate_for_recording(
                    recording_id,
                    pcm_data=pcm_data,
                    on_stage=self._set_processing_stage,
                    should_abort=lambda: not self._is_active_session(session_token),
                )
            else:
                service_result = self._service_factory().generate(
                    pcm_data,
                    on_stage=self._set_processing_stage,
                )
                result = SimpleNamespace(
                    status=service_result.notes.get("status", "success"),
                    transcript=service_result.notes.get("transcript") or "",
                    error=None,
                )

            if not self._is_active_session(session_token):
                return

            if result.status in {"failed", "not_found"}:
                if self.on_error:
                    self.on_error(
                        t(
                            self.config.ui.language,
                            "mode_processing_error",
                            details=result.error or "Meeting generation failed",
                        )
                    )
                self._set_state(ModeState.FAILED)
                return

            transcript = result.transcript or ""
            if transcript and self.on_result:
                self.on_result(transcript)
            if recording_id and self._on_meeting_result is not None:
                self._on_meeting_result(recording_id)
        except Exception as exc:
            if recording_id and self._recording_store is not None:
                self._recording_store.update(
                    recording_id,
                    "failed",
                    error=str(exc),
                    meeting={
                        "status": "failed",
                        "minutes": {
                            "status": "failed",
                            "summary": "",
                            "key_points": [],
                            "action_items": [],
                            "error": str(exc),
                        },
                    },
                )
            if self.on_error:
                self.on_error(t(self.config.ui.language, "mode_processing_error", details=str(exc)))
            self._set_state(ModeState.FAILED)
        finally:
            if self._is_active_session(session_token):
                self._active_recording_id = None
                self._set_state(ModeState.IDLE)

    def cancel(self, reason: str = "user_cancel") -> None:
        if self._state == ModeState.IDLE:
            return
        previous_state = self._state
        self._active_session_token = self._invalidate_session(reason=reason)
        self._set_state(ModeState.CANCELLING)
        if previous_state in (ModeState.STARTING, ModeState.RECORDING):
            self._recorder.stop()
        elif previous_state == ModeState.PROCESSING:
            self._mark_active_recording_canceled()
        self._set_state(ModeState.IDLE)

    def _mark_active_recording_canceled(self) -> None:
        if not self._active_recording_id or self._recording_store is None:
            return
        error = t(self.config.ui.language, "meeting_generation_canceled")
        self._recording_store.update(
            self._active_recording_id,
            "failed",
            error=error,
            meeting={
                "status": "failed",
                "reason": "canceled",
                "error": error,
                "minutes": {
                    "status": "failed",
                    "summary": "",
                    "key_points": [],
                    "action_items": [],
                    "error": error,
                },
            },
        )
        self._active_recording_id = None

    def close(self) -> None:
        if self.state != ModeState.IDLE:
            self.cancel(reason="mode_close")
        self._processing_executor.close(wait=True)
        close_recorder = getattr(self._recorder, "close", None)
        if callable(close_recorder):
            close_recorder()
