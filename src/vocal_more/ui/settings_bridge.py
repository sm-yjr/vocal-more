"""Normalize raw settings-window browser messages into typed actions."""

from __future__ import annotations

from typing import Any, Optional


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

    def _normalize_setConfig(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "set_config",
            "key": body.get("key"),
            "value": body.get("value"),
        }

    def _normalize_setAsrModel(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "set_asr_model",
            "model": body.get("model"),
            "backend": body.get("backend"),
        }

    def _normalize_syncFormState(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "sync_form_state",
            "payload": body.get("state"),
        }

    def _normalize_setDevice(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "set_device", "device": body.get("device")}

    def _normalize_setActiveHotkeys(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "set_active_hotkeys", "hotkeys": body.get("hotkeys", [])}

    def _normalize_addDictEntry(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "add_dict_entry",
            "term": body.get("term", ""),
            "aliases": body.get("aliases", []),
        }

    def _normalize_removeDictEntry(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "remove_dict_entry", "term": body.get("term", "")}

    def _normalize_refreshDevices(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "refresh_devices"}

    def _normalize_openConfigFile(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "open_config_file"}

    def _normalize_openDictFile(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "open_dict_file"}

    def _normalize_openExternal(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "open_external", "url": body.get("url", "")}

    def _normalize_getRecordings(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "get_recordings"}

    def _normalize_retryTranscription(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "retry_transcription", "id": body.get("id", "")}

    def _normalize_deleteRecording(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "delete_recording", "id": body.get("id", "")}

    def _normalize_playRecording(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "play_recording", "id": body.get("id", "")}

    def _normalize_copyTranscript(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "copy_transcript", "id": body.get("id", "")}

    def _normalize_startMicTest(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "start_mic_test"}

    def _normalize_stopMicTest(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "stop_mic_test"}

    def _normalize_playMicTest(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"action": "play_mic_test"}
