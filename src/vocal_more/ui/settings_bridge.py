"""Normalize raw settings-window browser messages into typed actions."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from ..domain.model_catalog import ASR_MODEL_IDS


_ALLOWED_TOP_LEVEL_CONFIG_KEYS = {
    "api_key",
    "default_mode",
    "auto_paste",
    "native_fast_paste",
    "restore_clipboard",
    "streaming_paste",
    "enable_polish",
}

_ALLOWED_CONFIG_SECTION_FIELDS = {
    "audio": {
        "sample_rate",
        "channels",
        "capture_channels",
        "blocksize",
        "input_device",
        "capture_backend",
        "gain_mode",
        "gain",
        "highpass_filter",
        "highpass_freq",
        "soft_limiter",
        "waveform_ceiling_dbfs",
    },
    "asr": {
        "backend",
        "model",
        "language",
        "batch_mode",
        "realtime_url",
        "use_dictionary_corpus",
        "extra_corpus_terms",
    },
    "llm": {
        "model",
        "temperature",
        "enable_thinking",
        "max_tokens",
        "polish_mode",
        "level",
        "structured",
        "tone",
        "persona",
        "output_language",
        "prompt_overrides",
    },
    "hotkey": {
        "primary_key",
        "fallback_key",
        "double_tap_threshold",
        "active_hotkeys",
        "custom_key",
        "custom_keys",
        "command_key",
    },
    "ui": {
        "language",
        "onboarding_completed",
        "advanced_settings",
    },
    "dictionary_learning": {
        "enabled",
        "excluded_bundle_ids",
    },
    "context_personalization": {
        "enabled",
        "excluded_bundle_ids",
    },
}

_ALLOWED_CONFIG_KEYS = set(_ALLOWED_TOP_LEVEL_CONFIG_KEYS)
for _section, _fields in _ALLOWED_CONFIG_SECTION_FIELDS.items():
    _ALLOWED_CONFIG_KEYS.update(f"{_section}.{field}" for field in _fields)

_ALLOWED_ASR_BACKENDS = {"realtime_ws", "short_file", "omni_offline"}
_ALLOWED_EXTERNAL_HOSTS = {"dashscope.console.aliyun.com"}


def _allowed_config_key(key: object) -> bool:
    return isinstance(key, str) and key in _ALLOWED_CONFIG_KEYS


def _sanitize_form_state(state: object) -> Optional[dict[str, Any]]:
    if not isinstance(state, dict):
        return None

    payload: dict[str, Any] = {}
    for key in _ALLOWED_TOP_LEVEL_CONFIG_KEYS:
        if key in state:
            payload[key] = state[key]

    for section, allowed_fields in _ALLOWED_CONFIG_SECTION_FIELDS.items():
        section_data = state.get(section)
        if not isinstance(section_data, dict):
            continue
        sanitized = {
            field: section_data[field]
            for field in allowed_fields
            if field in section_data
        }
        if sanitized:
            payload[section] = sanitized

    return payload


def _non_empty_string(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _recording_action(action: str, body: dict[str, Any]) -> Optional[dict[str, Any]]:
    rec_id = _non_empty_string(body.get("id"))
    if rec_id is None:
        return None
    return {"action": action, "id": rec_id}


def _allowed_external_url(url: object) -> Optional[str]:
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    if parsed.netloc not in _ALLOWED_EXTERNAL_HOSTS:
        return None
    return url


class SettingsBridge:
    """Translate browser postMessage payloads into dispatcher-friendly actions."""

    def parse(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not isinstance(body, dict):
            return None

        action = body.get("action")
        if not isinstance(action, str):
            return None

        normalizer = getattr(self, f"_normalize_{action}", None)
        if normalizer is None:
            return None
        return normalizer(body)

    def _normalize_setConfig(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        key = body.get("key")
        if not _allowed_config_key(key):
            return None
        return {
            "action": "set_config",
            "key": key,
            "value": body.get("value"),
        }

    def _normalize_previewConfig(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        key = body.get("key")
        if not _allowed_config_key(key) or not str(key).startswith("audio."):
            return None
        return {
            "action": "preview_config",
            "key": key,
            "value": body.get("value"),
        }

    def _normalize_setAsrModel(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        model = _non_empty_string(body.get("model"))
        backend = _non_empty_string(body.get("backend"))
        if model not in ASR_MODEL_IDS or backend not in _ALLOWED_ASR_BACKENDS:
            return None
        return {
            "action": "set_asr_model",
            "model": model,
            "backend": backend,
        }

    def _normalize_syncFormState(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        payload = _sanitize_form_state(body.get("state"))
        if payload is None:
            return None
        return {
            "action": "sync_form_state",
            "payload": payload,
        }

    def _normalize_setDevice(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        device = body.get("device")
        if device is not None and not isinstance(device, str):
            return None
        return {"action": "set_device", "device": device}

    def _normalize_setActiveHotkeys(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        hotkeys = body.get("hotkeys", [])
        if not isinstance(hotkeys, list) or not all(isinstance(item, str) for item in hotkeys):
            return None
        return {"action": "set_active_hotkeys", "hotkeys": hotkeys}

    def _normalize_resetContextProfile(
        self,
        body: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        del body
        return {"action": "reset_context_profile"}

    def _normalize_compactRecordingHistory(
        self,
        body: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        del body
        return {"action": "compact_recording_history"}

    def _normalize_addDictEntry(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        term = _non_empty_string(body.get("term"))
        if term is None:
            return None
        aliases = body.get("aliases", [])
        if not isinstance(aliases, list):
            return None
        return {
            "action": "add_dict_entry",
            "term": term,
            "aliases": [alias for alias in aliases if isinstance(alias, str)],
        }

    def _normalize_removeDictEntry(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        term = _non_empty_string(body.get("term"))
        if term is None:
            return None
        return {"action": "remove_dict_entry", "term": term}

    def _normalize_approveDictionaryLearning(
        self,
        body: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return _recording_action("approve_dictionary_learning", body)

    def _normalize_rejectDictionaryLearning(
        self,
        body: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return _recording_action("reject_dictionary_learning", body)

    def _normalize_undoDictionaryLearning(
        self,
        body: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return _recording_action("undo_dictionary_learning", body)

    def _normalize_refreshDevices(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "refresh_devices"}

    def _normalize_refreshEnvironment(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "refresh_environment"}

    def _normalize_checkDashScopeModels(
        self,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return {"action": "check_dashscope_models"}

    def _normalize_openAccessibilitySettings(
        self,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return {"action": "open_accessibility_settings"}

    def _normalize_openConfigFile(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "open_config_file"}

    def _normalize_openDictFile(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "open_dict_file"}

    def _normalize_openExternal(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        url = _allowed_external_url(body.get("url", ""))
        if url is None:
            return None
        return {"action": "open_external", "url": url}

    def _normalize_getRecordings(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "get_recordings"}

    def _normalize_retryTranscription(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        return _recording_action("retry_transcription", body)

    def _normalize_generateMeetingNotes(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        return _recording_action("generate_meeting_notes", body)

    def _normalize_deleteRecording(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        return _recording_action("delete_recording", body)

    def _normalize_playRecording(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        return _recording_action("play_recording", body)

    def _normalize_copyTranscript(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        return _recording_action("copy_transcript", body)

    def _normalize_startMicTest(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "start_mic_test"}

    def _normalize_stopMicTest(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "stop_mic_test"}

    def _normalize_playMicTest(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "play_mic_test"}
