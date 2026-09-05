"""Dispatch normalized settings-window actions to concrete handlers."""

from __future__ import annotations

from typing import Any, Callable, Optional


class SettingsActionDispatcher:
    """Route normalized settings actions to the correct backend callbacks."""

    def __init__(
        self,
        *,
        on_set_config: Optional[Callable[[str, Any], None]] = None,
        on_preview_config: Optional[Callable[[str, Any], None]] = None,
        on_set_asr_model: Optional[Callable[[str, str], None]] = None,
        on_sync_form_state: Optional[Callable[[dict], None]] = None,
        on_set_device: Optional[Callable[[Optional[str]], None]] = None,
        on_set_active_hotkeys: Optional[Callable[[list[str]], None]] = None,
        on_add_dict_entry: Optional[Callable[[str, list[str]], None]] = None,
        on_remove_dict_entry: Optional[Callable[[str], None]] = None,
        on_approve_dictionary_learning: Optional[Callable[[str], None]] = None,
        on_reject_dictionary_learning: Optional[Callable[[str], None]] = None,
        on_undo_dictionary_learning: Optional[Callable[[str], None]] = None,
        on_refresh_devices: Optional[Callable[[], None]] = None,
        on_refresh_environment: Optional[Callable[[], None]] = None,
        on_check_dashscope_models: Optional[Callable[[], None]] = None,
        on_open_accessibility_settings: Optional[Callable[[], None]] = None,
        on_open_config_file: Optional[Callable[[], None]] = None,
        on_open_dict_file: Optional[Callable[[], None]] = None,
        on_open_external: Optional[Callable[[str], None]] = None,
        on_get_recordings: Optional[Callable[[], None]] = None,
        on_retry_transcription: Optional[Callable[[str], None]] = None,
        on_generate_meeting_notes: Optional[Callable[[str], None]] = None,
        on_delete_recording: Optional[Callable[[str], None]] = None,
        on_stop_recording: Optional[Callable[[str], None]] = None,
        on_play_recording: Optional[Callable[[str], None]] = None,
        on_copy_transcript: Optional[Callable[[str], None]] = None,
        on_reset_context_profile: Optional[Callable[[], None]] = None,
        on_compact_recording_history: Optional[Callable[[], None]] = None,
        mic_test_controller: object | None = None,
    ) -> None:
        self._on_set_config = on_set_config
        self._on_preview_config = on_preview_config
        self._on_set_asr_model = on_set_asr_model
        self._on_sync_form_state = on_sync_form_state
        self._on_set_device = on_set_device
        self._on_set_active_hotkeys = on_set_active_hotkeys
        self._on_add_dict_entry = on_add_dict_entry
        self._on_remove_dict_entry = on_remove_dict_entry
        self._on_approve_dictionary_learning = on_approve_dictionary_learning
        self._on_reject_dictionary_learning = on_reject_dictionary_learning
        self._on_undo_dictionary_learning = on_undo_dictionary_learning
        self._on_refresh_devices = on_refresh_devices
        self._on_refresh_environment = on_refresh_environment
        self._on_check_dashscope_models = on_check_dashscope_models
        self._on_open_accessibility_settings = on_open_accessibility_settings
        self._on_open_config_file = on_open_config_file
        self._on_open_dict_file = on_open_dict_file
        self._on_open_external = on_open_external
        self._on_get_recordings = on_get_recordings
        self._on_retry_transcription = on_retry_transcription
        self._on_generate_meeting_notes = on_generate_meeting_notes
        self._on_delete_recording = on_delete_recording
        self._on_stop_recording = on_stop_recording
        self._on_play_recording = on_play_recording
        self._on_copy_transcript = on_copy_transcript
        self._on_reset_context_profile = on_reset_context_profile
        self._on_compact_recording_history = on_compact_recording_history
        self._mic_test_controller = mic_test_controller

        self._handlers = {
            "set_config": self._dispatch_set_config,
            "preview_config": self._dispatch_preview_config,
            "set_asr_model": self._dispatch_set_asr_model,
            "sync_form_state": self._dispatch_sync_form_state,
            "set_device": self._dispatch_set_device,
            "set_active_hotkeys": self._dispatch_set_active_hotkeys,
            "add_dict_entry": self._dispatch_add_dict_entry,
            "remove_dict_entry": self._dispatch_remove_dict_entry,
            "approve_dictionary_learning": self._dispatch_approve_dictionary_learning,
            "reject_dictionary_learning": self._dispatch_reject_dictionary_learning,
            "undo_dictionary_learning": self._dispatch_undo_dictionary_learning,
            "refresh_devices": self._dispatch_refresh_devices,
            "refresh_environment": self._dispatch_refresh_environment,
            "check_dashscope_models": self._dispatch_check_dashscope_models,
            "open_accessibility_settings": self._dispatch_open_accessibility_settings,
            "open_config_file": self._dispatch_open_config_file,
            "open_dict_file": self._dispatch_open_dict_file,
            "open_external": self._dispatch_open_external,
            "get_recordings": self._dispatch_get_recordings,
            "retry_transcription": self._dispatch_retry_transcription,
            "generate_meeting_notes": self._dispatch_generate_meeting_notes,
            "delete_recording": self._dispatch_delete_recording,
            "stop_recording": self._dispatch_stop_recording,
            "play_recording": self._dispatch_play_recording,
            "copy_transcript": self._dispatch_copy_transcript,
            "reset_context_profile": self._dispatch_reset_context_profile,
            "compact_recording_history": self._dispatch_compact_recording_history,
            "start_mic_test": self._dispatch_start_mic_test,
            "stop_mic_test": self._dispatch_stop_mic_test,
            "play_mic_test": self._dispatch_play_mic_test,
        }

    def dispatch(self, message: dict[str, Any]) -> None:
        action = message.get("action")
        if not isinstance(action, str):
            return
        handler = self._handlers.get(action)
        if handler is not None:
            handler(message)

    def _dispatch_set_config(self, message: dict[str, Any]) -> None:
        key = message.get("key")
        value = message.get("value")
        if key is not None and self._on_set_config is not None:
            self._on_set_config(key, value)
        if self._mic_test_controller is not None:
            self._mic_test_controller.apply_audio_setting(key, value)

    def _dispatch_preview_config(self, message: dict[str, Any]) -> None:
        key = message.get("key")
        value = message.get("value")
        if key is not None and self._on_preview_config is not None:
            self._on_preview_config(key, value)
        if self._mic_test_controller is not None:
            self._mic_test_controller.apply_audio_setting(key, value)

    def _dispatch_set_asr_model(self, message: dict[str, Any]) -> None:
        model = message.get("model")
        backend = message.get("backend")
        if model and backend and self._on_set_asr_model is not None:
            self._on_set_asr_model(model, backend)

    def _dispatch_sync_form_state(self, message: dict[str, Any]) -> None:
        payload = message.get("payload")
        if isinstance(payload, dict) and self._on_sync_form_state is not None:
            self._on_sync_form_state(payload)

    def _dispatch_set_device(self, message: dict[str, Any]) -> None:
        device = message.get("device")
        if self._on_set_device is not None:
            self._on_set_device(device if device else None)
        if self._mic_test_controller is not None:
            self._mic_test_controller.handle_device_changed()

    def _dispatch_set_active_hotkeys(self, message: dict[str, Any]) -> None:
        hotkeys = message.get("hotkeys", [])
        if self._on_set_active_hotkeys is not None:
            self._on_set_active_hotkeys(hotkeys)

    def _dispatch_add_dict_entry(self, message: dict[str, Any]) -> None:
        term = message.get("term", "")
        aliases = message.get("aliases", [])
        if term and self._on_add_dict_entry is not None:
            self._on_add_dict_entry(term, aliases)

    def _dispatch_remove_dict_entry(self, message: dict[str, Any]) -> None:
        term = message.get("term", "")
        if term and self._on_remove_dict_entry is not None:
            self._on_remove_dict_entry(term)

    def _dispatch_approve_dictionary_learning(self, message: dict[str, Any]) -> None:
        job_id = message.get("id", "")
        if job_id and self._on_approve_dictionary_learning is not None:
            self._on_approve_dictionary_learning(job_id)

    def _dispatch_reject_dictionary_learning(self, message: dict[str, Any]) -> None:
        job_id = message.get("id", "")
        if job_id and self._on_reject_dictionary_learning is not None:
            self._on_reject_dictionary_learning(job_id)

    def _dispatch_undo_dictionary_learning(self, message: dict[str, Any]) -> None:
        job_id = message.get("id", "")
        if job_id and self._on_undo_dictionary_learning is not None:
            self._on_undo_dictionary_learning(job_id)

    def _dispatch_refresh_devices(self, message: dict[str, Any]) -> None:
        if self._on_refresh_devices is not None:
            self._on_refresh_devices()

    def _dispatch_refresh_environment(self, message: dict[str, Any]) -> None:
        if self._on_refresh_environment is not None:
            self._on_refresh_environment()

    def _dispatch_check_dashscope_models(self, message: dict[str, Any]) -> None:
        if self._on_check_dashscope_models is not None:
            self._on_check_dashscope_models()

    def _dispatch_open_accessibility_settings(
        self,
        message: dict[str, Any],
    ) -> None:
        if self._on_open_accessibility_settings is not None:
            self._on_open_accessibility_settings()

    def _dispatch_open_config_file(self, message: dict[str, Any]) -> None:
        if self._on_open_config_file is not None:
            self._on_open_config_file()

    def _dispatch_open_dict_file(self, message: dict[str, Any]) -> None:
        if self._on_open_dict_file is not None:
            self._on_open_dict_file()

    def _dispatch_open_external(self, message: dict[str, Any]) -> None:
        url = message.get("url", "")
        if url and self._on_open_external is not None:
            self._on_open_external(url)

    def _dispatch_get_recordings(self, message: dict[str, Any]) -> None:
        if self._on_get_recordings is not None:
            self._on_get_recordings()

    def _dispatch_retry_transcription(self, message: dict[str, Any]) -> None:
        rec_id = message.get("id", "")
        if rec_id and self._on_retry_transcription is not None:
            self._on_retry_transcription(rec_id)

    def _dispatch_generate_meeting_notes(self, message: dict[str, Any]) -> None:
        rec_id = message.get("id", "")
        if rec_id and self._on_generate_meeting_notes is not None:
            self._on_generate_meeting_notes(rec_id)

    def _dispatch_delete_recording(self, message: dict[str, Any]) -> None:
        rec_id = message.get("id", "")
        if rec_id and self._on_delete_recording is not None:
            self._on_delete_recording(rec_id)

    def _dispatch_stop_recording(self, message: dict[str, Any]) -> None:
        if self._on_stop_recording is not None:
            self._on_stop_recording(message["id"])

    def _dispatch_play_recording(self, message: dict[str, Any]) -> None:
        rec_id = message.get("id", "")
        if rec_id and self._on_play_recording is not None:
            self._on_play_recording(rec_id)

    def _dispatch_copy_transcript(self, message: dict[str, Any]) -> None:
        rec_id = message.get("id", "")
        if rec_id and self._on_copy_transcript is not None:
            self._on_copy_transcript(rec_id)

    def _dispatch_reset_context_profile(self, message: dict[str, Any]) -> None:
        del message
        if self._on_reset_context_profile is not None:
            self._on_reset_context_profile()

    def _dispatch_compact_recording_history(self, message: dict[str, Any]) -> None:
        del message
        if self._on_compact_recording_history is not None:
            self._on_compact_recording_history()

    def _dispatch_start_mic_test(self, message: dict[str, Any]) -> None:
        if self._mic_test_controller is not None:
            self._mic_test_controller.start()

    def _dispatch_stop_mic_test(self, message: dict[str, Any]) -> None:
        if self._mic_test_controller is not None:
            self._mic_test_controller.stop()

    def _dispatch_play_mic_test(self, message: dict[str, Any]) -> None:
        if self._mic_test_controller is not None:
            self._mic_test_controller.play()
