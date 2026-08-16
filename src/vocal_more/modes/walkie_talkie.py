"""Walkie-talkie mode: hold to record, release to process."""

from copy import deepcopy
import threading
from types import SimpleNamespace
from typing import Callable, Optional

from ..application.background_executor import BackgroundExecutor, TaskHandle
from ..application.dictation_workflow import DictationWorkflow
from ..application.lazy_resource import LazyResource
from ..config import asr_model_handles_inline_polish, get_config
from ..core.audio_recorder import AudioRecorder
from ..core.asr_engine import ASREngine
from ..core.keyboard_sim import KeyboardSimulator
from ..dictionary import normalize_terms
from ..localization import format_microphone_start_error, t
from .base_mode import BaseMode, ModeState


class WalkieTalkieMode(BaseMode):
    """Walkie-talkie mode: hold Fn to record, release to process.

    Flow:
    1. Hold Fn key → Start recording + start streaming ASR
    2. Audio chunks streamed to ASR in real-time during recording
    3. Release Fn key → Stop recording, commit ASR, get result
    4. Polish → Paste
    """

    def __init__(
        self,
        on_state_change: Optional[Callable[[ModeState], None]] = None,
        on_result: Optional[Callable[[str], None]] = None,
        on_partial_result: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_processing_stage: Optional[Callable[[str], None]] = None,
        text_polisher: Optional[object] = None,
        on_audio_level: Optional[Callable[[float], None]] = None,
        recording_store: Optional[object] = None,
        dictionary_learning: Optional[object] = None,
        context_personalization: Optional[object] = None,
    ):
        super().__init__(
            on_state_change,
            on_result,
            on_partial_result,
            on_error,
            on_processing_stage,
            on_audio_level,
        )

        self.config = get_config()
        self.text_polisher = text_polisher
        self._recording_store = recording_store
        self._context_personalization = context_personalization
        self._active_app_context = None

        self._asr = LazyResource(
            lambda: ASREngine(
                on_partial_result=self._on_asr_partial,
                on_error=lambda msg: self._on_asr_error(msg),
            )
        )
        self._recorder = AudioRecorder(
            on_audio_level=on_audio_level,
            on_audio_chunk=self._on_audio_chunk,
        )
        self._keyboard = KeyboardSimulator()
        self._workflow = DictationWorkflow(
            config=self.config,
            asr_engine=self._asr,
            keyboard=self._keyboard,
            recording_store=self._recording_store,
            normalize_text=normalize_terms,
            dictionary_learning=dictionary_learning,
        )

        self._processing_executor = BackgroundExecutor(
            max_workers=1,
            thread_name_prefix="vocal-more-walkie-finish",
        )
        self._processing_thread: Optional[TaskHandle[None]] = None
        self._recording_asr_model = self.config.asr.model
        self._active_session_token = 0
        self._asr_session_token: Optional[int] = None
        self._asr_owner_lock = threading.Lock()

    def _prepare_app_context(self) -> str:
        self._active_app_context = None
        instruction = ""
        if self._context_personalization is not None:
            try:
                self._active_app_context = self._context_personalization.capture()
                instruction = self._context_personalization.instruction(
                    self._active_app_context
                )
            except Exception as exc:
                print(f"[WalkieTalkie] Context capture failed: {exc}")
        setter = getattr(self.text_polisher, "set_context_instruction", None)
        if callable(setter):
            setter(instruction)
        return instruction

    def _clear_app_context(self) -> None:
        setter = getattr(self.text_polisher, "set_context_instruction", None)
        if callable(setter):
            setter("")
        self._active_app_context = None

    @property
    def name(self) -> str:
        return "Walkie-Talkie"

    @property
    def description(self) -> str:
        return "Hold Fn to record, release to transcribe and paste"

    def on_hotkey_pressed(self) -> None:
        """Start recording + streaming ASR when hotkey is pressed."""
        if self._state != ModeState.IDLE:
            return

        session_token = self._begin_session()
        self._active_session_token = session_token
        self._recording_asr_model = self.config.asr.model
        context_instruction = self._prepare_app_context()
        self._set_state(ModeState.STARTING)

        session_audio_config = deepcopy(self.config.audio)
        try:
            # Synchronize the recorder from the same config boundary that the
            # ASR engine snapshots, then open ASR admission before any verified
            # recorder startup PCM can be published.
            self.apply_audio_runtime_config(session_audio_config)
        except Exception as exc:
            if (
                not self._is_active_session(session_token)
                or self._state != ModeState.STARTING
            ):
                return
            print(f"[WalkieTalkie] Failed to configure microphone: {exc}")
            self._report_startup_failure(exc, stage="microphone")
            return

        try:
            with self._asr_owner_lock:
                self._asr_session_token = session_token
                self._start_realtime_asr(
                    audio_config=session_audio_config,
                    context_instruction=context_instruction,
                )
        except Exception as exc:
            print(f"[WalkieTalkie] Failed to start realtime ASR: {exc}")
            self._abort_started_asr_startup(session_token)
            if (
                not self._is_active_session(session_token)
                or self._state != ModeState.STARTING
            ):
                return
            self._report_startup_failure(exc, stage="asr")
            return

        if (
            not self._is_active_session(session_token)
            or self._state != ModeState.STARTING
        ):
            self._abort_started_asr_startup(session_token)
            return

        try:
            self._start_audio_capture(
                session_audio_config
            )  # Audio chunks → _on_audio_chunk → ASR
        except Exception as exc:
            self._abort_started_asr_startup(session_token)
            if (
                not self._is_active_session(session_token)
                or self._state != ModeState.STARTING
            ):
                return
            print(f"[WalkieTalkie] Failed to open audio device: {exc}")
            self._report_startup_failure(exc, stage="microphone")
            return

        if (
            not self._is_active_session(session_token)
            or self._state != ModeState.STARTING
        ):
            # cancel() already stopped this recorder. A newer session may now
            # own the shared recorder, so late startup cleanup must be token-
            # conditional and must not stop the device a second time.
            self._abort_started_asr_startup(session_token)
            return

        self._set_state(ModeState.RECORDING)

    def _abort_started_asr_startup(self, session_token: int) -> None:
        with self._asr_owner_lock:
            if self._asr_session_token != session_token:
                return
            self._asr_session_token = None
            # Keep ownership serialized through abort so a new session cannot
            # start ASR between the token check and generation invalidation.
            self._abort_realtime_asr_startup()

    def _clear_asr_session_owner(self, session_token: int) -> None:
        with self._asr_owner_lock:
            if self._asr_session_token == session_token:
                self._asr_session_token = None

    def _report_startup_failure(self, exc: Exception, *, stage: str) -> None:
        if self.on_error:
            if stage == "microphone":
                self.on_error(
                    format_microphone_start_error(self.config.ui.language, exc)
                )
            else:
                self.on_error(t(self.config.ui.language, "mode_asr_error", details=str(exc)))
        self._set_state(ModeState.FAILED)
        self._clear_app_context()
        self._set_state(ModeState.IDLE)

    def on_hotkey_released(self) -> None:
        """Stop recording and get ASR result."""
        if self._state != ModeState.RECORDING:
            return

        self._set_state(ModeState.STOPPING)
        session_token = self._active_session_token
        pcm_data = self._recorder.stop()

        if len(pcm_data) < 3200:  # Less than 100ms of audio
            try:
                self._asr.stop()
            finally:
                self._clear_asr_session_owner(session_token)
            if self.on_error:
                self.on_error(t(self.config.ui.language, "mode_recording_too_short"))
            self._clear_app_context()
            self._set_state(ModeState.IDLE)
            return

        self._set_state(ModeState.PROCESSING)
        self._processing_thread = self._processing_executor.submit(
            self._finish_transcription,
            pcm_data,
            session_token,
        )

    def _on_audio_chunk(self, chunk: bytes) -> None:
        """Forward audio chunks to streaming ASR in real-time."""
        self._asr.send_audio(chunk)

    def _on_asr_error(self, msg: str) -> None:
        if self.state in (ModeState.IDLE, ModeState.CANCELLING, ModeState.FAILED):
            return
        if self.on_error:
            self.on_error(
                t(self.config.ui.language, "mode_asr_error", details=msg)
            )

    def _on_asr_partial(self, result) -> None:
        if self.state in (ModeState.IDLE, ModeState.CANCELLING, ModeState.FAILED):
            return
        if self.on_partial_result and result.text:
            if (
                self.state == ModeState.PROCESSING
                and self.config.enable_polish
                and asr_model_handles_inline_polish(self._recording_asr_model)
            ):
                self._set_processing_stage("polishing")
            self.on_partial_result(result.text)

    def _finish_transcription(self, pcm_data: bytes, session_token: int) -> None:
        """Commit ASR, get result, polish, paste."""
        try:
            result = self._workflow.finish_recording(
                pcm_data,
                mode_name="walkie_talkie",
                asr_model=self._recording_asr_model,
                text_polisher=self.text_polisher,
                messages=SimpleNamespace(
                    empty_transcription=t(
                        self.config.ui.language,
                        "settings_empty_transcription",
                    ),
                    processing_error=lambda details: t(
                        self.config.ui.language,
                        "mode_processing_error",
                        details=details,
                    ),
                    polish_error=lambda details: t(
                        self.config.ui.language,
                        "mode_polish_error",
                        details=details,
                    ),
                ),
                on_processing_stage=self._set_processing_stage,
                should_abort=lambda: not self._is_active_session(session_token),
            )
            if self._is_active_session(session_token):
                if (
                    getattr(result, "pasted", False)
                    and self._context_personalization is not None
                ):
                    try:
                        self._context_personalization.record_success(
                            self._active_app_context
                        )
                    except Exception as exc:
                        print(f"[WalkieTalkie] Context profile update failed: {exc}")
                if getattr(result, "error_message", None):
                    self._set_state(ModeState.FAILED)
                self._emit_workflow_result(result)
        finally:
            self._clear_asr_session_owner(session_token)
            self._clear_app_context()
            if self._is_active_session(session_token):
                self._set_state(ModeState.IDLE)
            elif self._state == ModeState.CANCELLING:
                # cancel(PROCESSING) keeps the mode unavailable until this
                # worker has fully left ASREngine.stop. Starting sooner would
                # overlap two generations on the same engine instance.
                self._set_state(ModeState.IDLE)

    def cancel(self, reason: str = "user_cancel") -> None:
        """Cancel current operation."""
        if self._state in (ModeState.IDLE, ModeState.CANCELLING):
            return

        previous_state = self._state
        cancelled_session_token = self._active_session_token
        self._log_lifecycle(
            "cancel_requested",
            reason=reason,
            from_state=previous_state.value,
        )
        self._active_session_token = self._invalidate_session(reason=reason)
        self._set_state(ModeState.CANCELLING)
        if previous_state in (ModeState.STARTING, ModeState.RECORDING):
            self._recorder.stop()
            if previous_state == ModeState.STARTING:
                self._abort_started_asr_startup(cancelled_session_token)
            else:
                try:
                    self._asr.stop()
                except Exception:
                    pass
                self._clear_asr_session_owner(cancelled_session_token)

        self._recording_asr_model = self.config.asr.model
        self._clear_app_context()
        if previous_state == ModeState.PROCESSING:
            processing = self._processing_thread
            if processing is not None and processing.done():
                self._set_state(ModeState.IDLE)
            return
        self._set_state(ModeState.IDLE)

    def close(self) -> None:
        if self.state != ModeState.IDLE:
            self.cancel(reason="mode_close")
        self._processing_executor.close(wait=True)
        close_recorder = getattr(self._recorder, "close", None)
        if callable(close_recorder):
            close_recorder()
        if hasattr(self._asr, "close"):
            self._asr.close()
