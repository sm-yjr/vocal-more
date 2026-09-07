"""Base class for recording modes."""

import inspect
import threading
from abc import ABC, abstractmethod
from copy import deepcopy
from enum import Enum
from typing import Callable, Optional

from ..application.lazy_resource import initialized_resource
from ..startup_diagnostics import (
    exception_fields,
    new_startup_attempt_id,
    record_startup_event,
)


class ModeState(Enum):
    """State of a recording mode."""

    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    PROCESSING = "processing"
    CANCELLING = "cancelling"
    FAILED = "failed"


class BaseMode(ABC):
    """Base class for recording modes."""

    def __init__(
        self,
        on_state_change: Optional[Callable[[ModeState], None]] = None,
        on_result: Optional[Callable[[str], None]] = None,
        on_partial_result: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_processing_stage: Optional[Callable[[str], None]] = None,
        on_audio_level: Optional[Callable[[float], None]] = None,
    ):
        """Initialize the base mode.

        Args:
            on_state_change: Callback when mode state changes
            on_result: Callback for final result text
            on_partial_result: Callback for partial/interim result text
            on_error: Callback for errors
            on_processing_stage: Callback for processing phase labels
            on_audio_level: Callback for real-time audio RMS level
        """
        self.on_state_change = on_state_change
        self.on_result = on_result
        self.on_partial_result = on_partial_result
        self.on_error = on_error
        self.on_processing_stage = on_processing_stage
        self.on_audio_level = on_audio_level

        self._connection_status = None
        self.on_connection_status = None
        self._state = ModeState.IDLE
        self._session_lock = threading.Lock()
        self._session_token = 0
        self._startup_diagnostic_attempt_id: str | None = None
        self._startup_diagnostic_trigger = "unknown"

    def set_startup_diagnostic_context(self, attempt_id: str, trigger: str) -> None:
        """Associate the next mode transition with its physical/UI trigger."""
        self._startup_diagnostic_attempt_id = attempt_id
        self._startup_diagnostic_trigger = trigger

    def _ensure_startup_diagnostic_context(self) -> str:
        if not self._startup_diagnostic_attempt_id:
            self.set_startup_diagnostic_context(new_startup_attempt_id(), "direct")
        return self._startup_diagnostic_attempt_id

    @property
    def state(self) -> ModeState:
        """Get current state."""
        return self._state

    def _set_state(self, state: ModeState) -> None:
        """Set state and notify callback."""
        if self._state == state:
            return
        previous = self._state
        self._state = state
        self._log_lifecycle(
            "state_transition",
            from_state=previous.value,
            to_state=state.value,
        )
        if self.on_state_change:
            self.on_state_change(state)

    def _begin_session(self) -> int:
        """Advance and return the active dictation session token."""
        with self._session_lock:
            self._session_token += 1
            token = self._session_token
            self._connection_status = None
        self._log_lifecycle("session_started", session_token=token)
        return token

    def _invalidate_session(self, *, reason: str = "invalidate") -> int:
        """Invalidate any in-flight callbacks/work by advancing the token."""
        with self._session_lock:
            self._session_token += 1
            token = self._session_token
        self._log_lifecycle(
            "session_invalidated",
            reason=reason,
            session_token=token,
        )
        return token

    def _is_active_session(self, session_token: int) -> bool:
        with self._session_lock:
            return session_token == self._session_token

    def _set_processing_stage(self, stage: str) -> None:
        """Update the current processing phase label."""
        if self.on_processing_stage:
            self.on_processing_stage(stage)

    @property
    def runtime_is_idle(self) -> bool:
        """Return whether runtime configuration may safely switch this mode."""
        return self._state == ModeState.IDLE

    @property
    def audio_input_status(self) -> Optional[dict]:
        """Expose recorder status without leaking the recorder implementation."""
        recorder = getattr(self, "_recorder", None)
        status = getattr(recorder, "input_status", None)
        return dict(status) if isinstance(status, dict) else None

    def apply_audio_runtime_config(self, audio_config: object) -> None:
        """Apply live audio settings while keeping recorder details private."""
        recorder = getattr(self, "_recorder", None)
        if recorder is None:
            return
        apply_batch = getattr(recorder, "apply_capture_config", None)
        if callable(apply_batch):
            apply_batch(audio_config)
            return
        setters = (
            ("set_blocksize", "blocksize"),
            ("set_capture_channels", "capture_channels"),
            ("set_device", "input_device"),
            ("set_capture_backend", "capture_backend"),
            ("set_gain_mode", "gain_mode"),
            ("set_gain", "gain"),
            ("set_highpass_filter", "highpass_filter"),
            ("set_highpass_freq", "highpass_freq"),
            ("set_soft_limiter", "soft_limiter"),
        )
        for setter_name, field_name in setters:
            setter = getattr(recorder, setter_name, None)
            if callable(setter):
                setter(getattr(audio_config, field_name))

    def _start_audio_capture(self, audio_config: object) -> None:
        """Start with the same atomic snapshot already admitted by ASR."""
        attempt_id = self._ensure_startup_diagnostic_context()
        recorder = getattr(self, "_recorder", None)
        if recorder is None:
            raise RuntimeError("Audio recorder is unavailable")
        record_startup_event("microphone_start_requested", attempt_id=attempt_id, mode=self.name)
        try:
            start_session = getattr(recorder, "start_capture_session", None)
            if callable(start_session):
                start_session(audio_config)
            else:
                # Compatibility for injected test/extension recorders. The snapshot
                # was already synchronized by apply_audio_runtime_config().
                recorder.start()
        except Exception as exc:
            status = getattr(recorder, "diagnostic_snapshot", None)
            snapshot = status() if callable(status) else getattr(recorder, "input_status", None)
            record_startup_event(
                "microphone_start_failed",
                attempt_id=attempt_id,
                mode=self.name,
                audio_input=snapshot,
                **exception_fields(exc),
            )
            raise
        record_startup_event(
            "microphone_start_succeeded",
            attempt_id=attempt_id,
            mode=self.name,
            audio_input=(
                recorder.diagnostic_snapshot()
                if callable(getattr(recorder, "diagnostic_snapshot", None))
                else getattr(recorder, "input_status", None)
            ),
        )

    def refresh_asr_runtime(self) -> None:
        """Refresh an initialized ASR engine without forcing lazy creation."""
        asr = initialized_resource(getattr(self, "_asr", None))
        refresh = getattr(asr, "refresh_runtime_config", None)
        if callable(refresh):
            refresh(drop_idle_session=True)

    def prewarm_audio(self) -> bool:
        """Prepare only this idle mode's capture graph; never open the mic."""
        if not self.runtime_is_idle:
            return False
        recorder = getattr(self, "_recorder", None)
        prepare_audio = getattr(type(recorder), "prepare_idle_capture", None)
        return bool(prepare_audio(recorder)) if callable(prepare_audio) else False

    def prewarm_asr(self) -> bool:
        """Force lazy ASR creation and begin a clean idle connection."""
        if not self.runtime_is_idle or self._connection_is_pending():
            return False
        self.prewarm_audio()
        resource = getattr(self, "_asr", None)
        getter = getattr(resource, "get", None)
        asr = getter() if callable(getter) else resource
        prepare = getattr(type(asr), "prepare_idle_session", None)
        if not callable(prepare):
            return False
        return bool(prepare(asr, deepcopy(getattr(self, "config").audio)))

    def _abort_realtime_asr_startup(self) -> None:
        """Boundedly invalidate an initialized ASR session during mode startup."""
        asr = initialized_resource(getattr(self, "_asr", None))
        if asr is None:
            return
        abort = getattr(asr, "abort_startup", None)
        if callable(abort):
            abort()
            return
        reset = getattr(asr, "reset", None)
        if callable(reset):
            reset()
            return
        # Compatibility for small test doubles and older injected engines.
        stop = getattr(asr, "stop", None)
        if callable(stop):
            stop()

    def _start_realtime_asr(
        self,
        *,
        audio_config: object,
        context_instruction: str = "",
        polish_mode: str | None = None,
    ) -> None:
        """Start ASR from the exact recorder-plan snapshot for this session."""
        attempt_id = self._ensure_startup_diagnostic_context()
        record_startup_event("asr_start_requested", attempt_id=attempt_id, mode=self.name)
        try:
            self._start_realtime_asr_impl(
                audio_config=audio_config,
                context_instruction=context_instruction,
                polish_mode=polish_mode,
            )
        except Exception as exc:
            record_startup_event(
                "asr_start_failed", attempt_id=attempt_id, mode=self.name, **exception_fields(exc)
            )
            raise
        record_startup_event("asr_start_admitted", attempt_id=attempt_id, mode=self.name)

    @property
    def connection_status(self):
        return self._connection_status

    def _on_connection_update(self, token, status) -> None:
        with self._session_lock:
            if token != self._session_token:
                return
            self._connection_status = status
        observer = self.on_connection_status
        if callable(observer):
            observer(self, status)

    def _connection_is_pending(self) -> bool:
        status = getattr(self, "_connection_status", None)
        return status is not None and status.phase != "ready"

    def _start_realtime_asr_impl(
        self,
        *,
        audio_config: object,
        context_instruction: str = "",
        polish_mode: str | None = None,
    ) -> None:
        setter = getattr(self._asr, "set_connection_observer", None)
        token = self._active_session_token
        if callable(setter):
            setter(lambda status: self._on_connection_update(token, status))
        starter = getattr(self._asr, "start_with_audio_contract", None)
        if callable(starter):
            kwargs = {}
            if context_instruction and self._accepts_keyword(
                starter, "context_instruction"
            ):
                kwargs["context_instruction"] = context_instruction
            if polish_mode is not None and self._accepts_keyword(
                starter, "polish_mode"
            ):
                kwargs["polish_mode"] = polish_mode
            starter(audio_config, **kwargs)
            return
        if context_instruction or polish_mode is not None:
            kwargs = {}
            start = self._asr.start
            if context_instruction and self._accepts_keyword(
                start, "context_instruction"
            ):
                kwargs["context_instruction"] = context_instruction
            if polish_mode is not None and self._accepts_keyword(
                start, "polish_mode"
            ):
                kwargs["polish_mode"] = polish_mode
            start(**kwargs)
        else:
            self._asr.start()

    @staticmethod
    def _accepts_keyword(callback: Callable, keyword: str) -> bool:
        """Return whether a runtime or test double accepts an optional keyword."""
        try:
            parameters = inspect.signature(callback).parameters.values()
        except (TypeError, ValueError):
            return True
        return any(
            parameter.name == keyword
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    def _emit_workflow_result(self, result) -> None:
        """Forward shared workflow output to mode callbacks."""
        for warning in getattr(result, "warnings", []):
            if self.on_error:
                self.on_error(warning)

        error_message = getattr(result, "error_message", None)
        if error_message:
            if self.on_error:
                self.on_error(error_message)
            return

        final_text = getattr(result, "final_text", "")
        if final_text and self.on_result:
            self.on_result(final_text)

    def _log_lifecycle(self, event: str, **payload) -> None:
        data = {
            "mode": self.name,
            "state": self._state.value,
        }
        with self._session_lock:
            data["session_token"] = self._session_token
        data.update(payload)
        details = " ".join(f"{key}={value}" for key, value in data.items())
        print(f"[ModeLifecycle] event={event} {details}")
        record_startup_event(
            f"mode_{event}",
            attempt_id=self._startup_diagnostic_attempt_id,
            trigger=self._startup_diagnostic_trigger,
            **data,
        )

    @abstractmethod
    def on_hotkey_pressed(self) -> None:
        """Handle hotkey press event."""
        pass

    @abstractmethod
    def on_hotkey_released(self) -> None:
        """Handle hotkey release event."""
        pass

    @abstractmethod
    def cancel(self, reason: str = "user_cancel") -> None:
        """Cancel current operation."""
        pass

    def close(self) -> None:
        """Release any background resources owned by the mode."""
        return None

    @property
    @abstractmethod
    def name(self) -> str:
        """Get mode name."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Get mode description."""
        pass
