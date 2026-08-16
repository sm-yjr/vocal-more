"""Base class for recording modes."""

from abc import ABC, abstractmethod
from enum import Enum
import threading
from typing import Callable, Optional

from ..application.lazy_resource import initialized_resource


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

        self._state = ModeState.IDLE
        self._session_lock = threading.Lock()
        self._session_token = 0

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
        recorder = getattr(self, "_recorder", None)
        if recorder is None:
            raise RuntimeError("Audio recorder is unavailable")
        start_session = getattr(recorder, "start_capture_session", None)
        if callable(start_session):
            start_session(audio_config)
            return
        # Compatibility for injected test/extension recorders. The snapshot
        # was already synchronized by apply_audio_runtime_config().
        recorder.start()

    def refresh_asr_runtime(self) -> None:
        """Refresh an initialized ASR engine without forcing lazy creation."""
        asr = initialized_resource(getattr(self, "_asr", None))
        refresh = getattr(asr, "refresh_runtime_config", None)
        if callable(refresh):
            refresh(drop_idle_session=True)

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
    ) -> None:
        """Start ASR from the exact recorder-plan snapshot for this session."""
        starter = getattr(self._asr, "start_with_audio_contract", None)
        if callable(starter):
            starter(
                audio_config,
                context_instruction=context_instruction,
            )
            return
        if context_instruction:
            self._asr.start(context_instruction=context_instruction)
        else:
            self._asr.start()

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
