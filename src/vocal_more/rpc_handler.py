"""JSON-RPC request dispatcher for the Python backend.

Routes incoming JSON-RPC requests to the appropriate handlers,
mirroring the component wiring in app.py.
"""

from typing import Any, Callable, Optional

import dashscope

from .application.background_executor import BackgroundExecutor
from .application.dictation_command_coordinator import DictationCommandCoordinator
from .application.runtime_facade import RuntimeFacade
from .bootstrap import build_rpc_handler_dependencies
from .config import (
    ASR_MODEL_CATALOG,
    LLM_MODEL_CATALOG,
)
from .core.audio_recorder import AudioRecorder
from .core.recording_store import RecordingStore
from .core.text_polisher import TextPolisher
from .dictionary import get_dictionary, reload_dictionary
from .localization import t
from .modes.base_mode import BaseMode, ModeState
from .modes.realtime_long import RealtimeLongMode
from .modes.walkie_talkie import WalkieTalkieMode

VERSION = "0.2.0"


class RPCHandler:
    """Dispatches JSON-RPC requests to backend components."""

    def __init__(
        self,
        send_notification: Callable[[str, dict], None],
        dependencies=None,
    ):
        self._send_notification = send_notification
        dependencies = dependencies or build_rpc_handler_dependencies(
            self,
            send_notification=send_notification,
            text_polisher_factory=TextPolisher,
            recording_store_factory=RecordingStore,
            walkie_talkie_factory=WalkieTalkieMode,
            realtime_long_factory=RealtimeLongMode,
        )
        self._apply_dependencies(dependencies)

    def _apply_dependencies(self, dependencies) -> None:
        self.config = dependencies.config
        self._recording_store = dependencies.recording_store
        self._text_polisher = dependencies.text_polisher
        self._walkie_talkie = dependencies.walkie_talkie
        self._realtime_long = dependencies.realtime_long
        self._modes = {
            "walkie_talkie": self._walkie_talkie,
            "realtime_long": self._realtime_long,
        }
        self._current_mode = dependencies.current_mode
        self._command_coordinator = dependencies.command_coordinator
        self._runtime = dependencies.runtime
        self._background_tasks = BackgroundExecutor(
            max_workers=2,
            thread_name_prefix="vocal-more-rpc-tasks",
        )

    # -- Notification callbacks -----------------------------------------------

    def _on_state_change(self, state: ModeState) -> None:
        self._send_notification("state_changed", {"state": state.value})
        if state == ModeState.IDLE:
            self._select_default_mode_when_safe()

    def _on_result(self, text: str) -> None:
        self._send_notification("final_result", {"text": text})

    def _on_partial_result(self, text: str) -> None:
        self._send_notification("partial_result", {"text": text})

    def _on_error(self, error: str) -> None:
        self._send_notification("error", {"message": error})

    def _on_processing_stage(self, stage: str) -> None:
        self._send_notification("processing_stage", {"stage": stage})

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
            self._get_runtime().apply_update(key, value)
        except ValueError as exc:
            raise RPCError(-32602, str(exc)) from exc

        self.config.save()

        return {"ok": True}

    def _handle_list_devices(self, params: dict) -> list:
        return AudioRecorder.list_input_devices()

    def _handle_set_device(self, params: dict) -> dict:
        device_name = params.get("device")  # None means system default
        self._get_runtime().apply_update("audio.input_device", device_name)
        self.config.save()

        return {"ok": True}

    def _handle_set_mode(self, params: dict) -> dict:
        mode_name = params.get("mode", "")
        if mode_name not in self._modes:
            raise RPCError(-32602, f"Unknown mode: {mode_name}")

        self._get_runtime().apply_update("default_mode", mode_name)
        self.config.save()
        return {"ok": True, "mode": mode_name}

    def _handle_hotkey_pressed(self, params: dict) -> dict:
        self._get_command_coordinator().call(self._handle_hotkey_pressed_command)
        return {"ok": True}

    def _handle_hotkey_released(self, params: dict) -> dict:
        self._get_command_coordinator().call(self._handle_hotkey_released_command)
        return {"ok": True}

    def _handle_cancel(self, params: dict) -> dict:
        self._get_command_coordinator().call(self._handle_cancel_command)
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
        self._get_runtime().apply_update("hotkey.active_hotkeys", hotkeys)
        self.config.save()
        return {"ok": True}

    def _handle_shutdown(self, params: dict) -> dict:
        # Cancel any active recording
        if self._current_mode.state != ModeState.IDLE:
            self._get_command_coordinator().call(self._handle_cancel_command)
        return {"ok": True}

    def _handle_list_recordings(self, params: dict) -> list:
        return self._recording_store.list_recordings()

    def _handle_retry_transcription(self, params: dict) -> dict:
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
                    error_message = t(self.config.ui.language, "settings_recording_not_found")
                    self._recording_store.update(rec_id, "failed", error=error_message)
                    self._send_notification(
                        "retry_failed", {"id": rec_id, "error": error_message}
                    )
                    return

                engine = BatchASREngine()
                language = self._recording_store.get_language(rec_id)
                transcript = engine.transcribe(
                    pcm_data, model_override=RETRY_ASR_MODEL, language_override=language
                )

                if transcript and transcript.strip():
                    self._recording_store.update(rec_id, "success", transcript, error=None)
                    self._send_notification(
                        "retry_completed", {"id": rec_id, "transcript": transcript}
                    )
                else:
                    error_message = t(self.config.ui.language, "settings_empty_transcription")
                    self._recording_store.update(rec_id, "failed", error=error_message)
                    self._send_notification(
                        "retry_failed",
                        {"id": rec_id, "error": error_message},
                    )
            except Exception as e:
                self._recording_store.update(rec_id, "failed", error=str(e))
                self._send_notification(
                    "retry_failed", {"id": rec_id, "error": str(e)}
                )

        self._background_tasks.submit(_do_retry)
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
            asr = getattr(mode, "_asr", None)
            if asr is not None and hasattr(asr, "refresh_api_key"):
                asr.refresh_api_key()

    def _apply_runtime_config_keys(self, changed_keys: set[str]) -> None:
        """Apply live runtime side effects for config changes."""
        self._get_runtime()._apply_runtime_config_keys(changed_keys)

    def _select_default_mode_when_safe(self) -> None:
        """Apply the configured default mode without interrupting active recording."""
        self._get_runtime()._select_default_mode_when_safe()

    def _refresh_mode_asr_runtime(self) -> None:
        """Invalidate idle ASR runtime state affected by config changes."""
        self._get_runtime()._refresh_mode_asr_runtime()

    def _sync_audio_recorders(self) -> None:
        """Push the current audio config into all mode recorders."""
        self._get_runtime()._sync_audio_recorders()

    def _build_runtime_facade(self) -> RuntimeFacade:
        return RuntimeFacade(
            config=self.config,
            modes=self._modes,
            get_current_mode=lambda: self._current_mode,
            set_current_mode=lambda mode: setattr(self, "_current_mode", mode),
            on_refresh_text_polisher=self._refresh_text_polisher,
        )

    def _get_runtime(self) -> RuntimeFacade:
        runtime = getattr(self, "_runtime", None)
        if runtime is None:
            self._runtime = self._build_runtime_facade()
        return self._runtime

    @property
    def runtime(self) -> RuntimeFacade:
        return self._get_runtime()

    def _build_command_coordinator(self) -> DictationCommandCoordinator:
        return DictationCommandCoordinator(thread_name="vocal-more-rpc-commands")

    def _get_command_coordinator(self) -> DictationCommandCoordinator:
        coordinator = getattr(self, "_command_coordinator", None)
        if coordinator is None:
            self._command_coordinator = self._build_command_coordinator()
        return self._command_coordinator

    def close(self) -> None:
        coordinator = getattr(self, "_command_coordinator", None)
        if coordinator is not None:
            coordinator.close()
            self._command_coordinator = None
        background_tasks = getattr(self, "_background_tasks", None)
        if background_tasks is not None:
            background_tasks.close(wait=False, cancel_futures=True)
        for mode in getattr(self, "_modes", {}).values():
            close = getattr(mode, "close", None)
            if callable(close):
                close()

    def _handle_hotkey_pressed_command(self) -> None:
        self._current_mode.on_hotkey_pressed()

    def _handle_hotkey_released_command(self) -> None:
        self._current_mode.on_hotkey_released()

    def _handle_cancel_command(self) -> None:
        self._current_mode.cancel()


class RPCError(Exception):
    """JSON-RPC error with code and message."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
