"""JSON-RPC request dispatcher for the Python backend.

Routes incoming JSON-RPC requests to the appropriate handlers,
mirroring the component wiring in app.py.
"""

from typing import Any, Callable, Optional

import dashscope

from .config import (
    ASR_MODEL_CATALOG,
    LLM_MODEL_CATALOG,
    get_config,
)
from .core.audio_recorder import AudioRecorder
from .core.recording_store import RecordingStore
from .core.text_polisher import TextPolisher
from .dictionary import get_dictionary, reload_dictionary
from .modes.base_mode import BaseMode, ModeState
from .modes.realtime_long import RealtimeLongMode
from .modes.walkie_talkie import WalkieTalkieMode

VERSION = "0.1.0"


class RPCHandler:
    """Dispatches JSON-RPC requests to backend components."""

    def __init__(
        self,
        send_notification: Callable[[str, dict], None],
    ):
        self._send_notification = send_notification
        self.config = get_config()

        # Initialize recording store
        self._recording_store = RecordingStore()

        # Initialize text polisher
        self._text_polisher: Optional[TextPolisher] = None
        if self.config.api_key:
            self._text_polisher = TextPolisher()

        # Initialize modes with callbacks
        self._walkie_talkie = WalkieTalkieMode(
            on_state_change=self._on_state_change,
            on_result=self._on_result,
            on_partial_result=self._on_partial_result,
            on_error=self._on_error,
            text_polisher=self._text_polisher,
            on_audio_level=self._on_audio_level,
            recording_store=self._recording_store,
        )

        self._realtime_long = RealtimeLongMode(
            on_state_change=self._on_state_change,
            on_result=self._on_result,
            on_partial_result=self._on_partial_result,
            on_error=self._on_error,
            text_polisher=self._text_polisher,
            on_audio_level=self._on_audio_level,
            recording_store=self._recording_store,
        )

        self._modes: dict[str, BaseMode] = {
            "walkie_talkie": self._walkie_talkie,
            "realtime_long": self._realtime_long,
        }

        self._current_mode: BaseMode = (
            self._realtime_long
            if self.config.default_mode == "realtime_long"
            else self._walkie_talkie
        )

    # -- Notification callbacks -----------------------------------------------

    def _on_state_change(self, state: ModeState) -> None:
        self._send_notification("state_changed", {"state": state.value})

    def _on_result(self, text: str) -> None:
        self._send_notification("final_result", {"text": text})

    def _on_partial_result(self, text: str) -> None:
        self._send_notification("partial_result", {"text": text})

    def _on_error(self, error: str) -> None:
        self._send_notification("error", {"message": error})

    def _on_audio_level(self, rms: float) -> None:
        self._send_notification("audio_level", {"rms": rms})

    # -- Dispatch -------------------------------------------------------------

    def dispatch(self, method: str, params: dict) -> Any:
        """Dispatch a JSON-RPC method call.

        Returns the result value for success, or raises an RPCError.
        """
        handler = getattr(self, f"_handle_{method}", None)
        if handler is None:
            raise RPCError(-32601, f"Method not found: {method}")
        return handler(params)

    # -- Handlers -------------------------------------------------------------

    def _handle_initialize(self, params: dict) -> dict:
        return {
            "version": VERSION,
            "state": self._current_mode.state.value,
            "current_mode": self._get_mode_name(self._current_mode),
            "config": self.config.to_dict(),
            "llm_models": LLM_MODEL_CATALOG,
            "asr_models": ASR_MODEL_CATALOG,
        }

    def _handle_get_config(self, params: dict) -> dict:
        return self.config.to_dict()

    def _handle_set_config(self, params: dict) -> dict:
        key = params.get("key", "")
        value = params.get("value")

        try:
            self.config.apply_update(key, value)
        except ValueError as exc:
            raise RPCError(-32602, str(exc)) from exc

        if key == "api_key":
            self._refresh_text_polisher()
        elif key == "default_mode":
            self._current_mode = self._modes[self.config.default_mode]
        elif key.startswith("audio."):
            self._sync_audio_recorders()

        self.config.save()

        return {"ok": True}

    def _handle_list_devices(self, params: dict) -> list:
        return AudioRecorder.list_input_devices()

    def _handle_set_device(self, params: dict) -> dict:
        device_name = params.get("device")  # None means system default
        self.config.apply_update("audio.input_device", device_name)
        self.config.save()
        self._sync_audio_recorders()

        return {"ok": True}

    def _handle_set_mode(self, params: dict) -> dict:
        mode_name = params.get("mode", "")
        if mode_name not in self._modes:
            raise RPCError(-32602, f"Unknown mode: {mode_name}")

        self._current_mode = self._modes[mode_name]
        self.config.default_mode = mode_name
        self.config.save()
        return {"ok": True, "mode": mode_name}

    def _handle_hotkey_pressed(self, params: dict) -> dict:
        self._current_mode.on_hotkey_pressed()
        return {"ok": True}

    def _handle_hotkey_released(self, params: dict) -> dict:
        self._current_mode.on_hotkey_released()
        return {"ok": True}

    def _handle_cancel(self, params: dict) -> dict:
        self._current_mode.cancel()
        return {"ok": True}

    def _handle_get_dictionary(self, params: dict) -> list:
        dictionary = get_dictionary()
        return [
            {"term": e.term, "aliases": e.aliases}
            for e in dictionary.entries
        ]

    def _handle_add_dict_entry(self, params: dict) -> dict:
        term = params.get("term", "").strip()
        if not term:
            raise RPCError(-32602, "term is required")
        aliases = params.get("aliases", [])
        get_dictionary().add_entry(term, aliases)
        return {"ok": True}

    def _handle_remove_dict_entry(self, params: dict) -> dict:
        term = params.get("term", "").strip()
        if not term:
            raise RPCError(-32602, "term is required")
        get_dictionary().remove_entry(term)
        return {"ok": True}

    def _handle_set_active_hotkeys(self, params: dict) -> dict:
        hotkeys = params.get("hotkeys", [])
        self.config.apply_update("hotkey.active_hotkeys", hotkeys)
        self.config.save()
        return {"ok": True}

    def _handle_shutdown(self, params: dict) -> dict:
        # Cancel any active recording
        if self._current_mode.state != ModeState.IDLE:
            self._current_mode.cancel()
        return {"ok": True}

    def _handle_list_recordings(self, params: dict) -> list:
        return self._recording_store.list_recordings()

    def _handle_retry_transcription(self, params: dict) -> dict:
        import threading

        from .core.asr_engine import BatchASREngine
        from .core.recording_store import RETRY_ASR_MODEL

        rec_id = params.get("id", "")
        if not rec_id:
            raise RPCError(-32602, "id is required")

        self._send_notification("retry_started", {"id": rec_id})

        def _do_retry():
            try:
                pcm_data = self._recording_store.get_pcm_data(rec_id)
                if pcm_data is None:
                    self._send_notification(
                        "retry_failed", {"id": rec_id, "error": "Recording file not found"}
                    )
                    return

                engine = BatchASREngine()
                language = self._recording_store.get_language(rec_id)
                transcript = engine.transcribe(
                    pcm_data, model_override=RETRY_ASR_MODEL, language_override=language
                )

                if transcript and transcript.strip():
                    self._recording_store.update(rec_id, "success", transcript)
                    self._send_notification(
                        "retry_completed", {"id": rec_id, "transcript": transcript}
                    )
                else:
                    self._recording_store.update(rec_id, "failed")
                    self._send_notification(
                        "retry_failed",
                        {"id": rec_id, "error": "Empty transcription result"},
                    )
            except Exception as e:
                self._recording_store.update(rec_id, "failed")
                self._send_notification(
                    "retry_failed", {"id": rec_id, "error": str(e)}
                )

        threading.Thread(target=_do_retry, daemon=True).start()
        return {"ok": True}

    def _handle_delete_recording(self, params: dict) -> dict:
        rec_id = params.get("id", "")
        if not rec_id:
            raise RPCError(-32602, "id is required")
        self._recording_store.delete(rec_id)
        return {"ok": True}

    def _handle_play_recording(self, params: dict) -> dict:
        rec_id = params.get("id", "")
        if not rec_id:
            raise RPCError(-32602, "id is required")
        wav_b64 = self._recording_store.get_wav_base64(rec_id)
        if wav_b64 is None:
            raise RPCError(-32603, "Recording file not found")
        return {"wav_base64": wav_b64}

    # -- Helpers --------------------------------------------------------------

    def _get_mode_name(self, mode: BaseMode) -> str:
        for name, m in self._modes.items():
            if m is mode:
                return name
        return "unknown"

    def _refresh_text_polisher(self) -> None:
        """Recreate or clear the text polisher after API key changes."""
        dashscope.api_key = self.config.api_key or None
        self._text_polisher = TextPolisher() if self.config.api_key else None
        for mode in self._modes.values():
            mode.text_polisher = self._text_polisher

    def _sync_audio_recorders(self) -> None:
        """Push the current audio config into all mode recorders."""
        for mode in self._modes.values():
            recorder = getattr(mode, "_recorder", None)
            if recorder is None:
                continue
            recorder.set_device(self.config.audio.input_device)
            recorder.set_gain(self.config.audio.gain)
            recorder.set_noise_gate(self.config.audio.noise_gate)
            recorder.set_highpass_filter(self.config.audio.highpass_filter)
            recorder.set_highpass_freq(self.config.audio.highpass_freq)
            recorder.set_soft_limiter(self.config.audio.soft_limiter)


class RPCError(Exception):
    """JSON-RPC error with code and message."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
