"""Base class for recording modes."""

from abc import ABC, abstractmethod
from enum import Enum
import threading
from typing import Callable, Optional


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
