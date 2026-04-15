"""Localized UI strings for the Python app surfaces."""

from typing import Literal

UILanguage = Literal["en", "zh"]


UI_TEXT: dict[UILanguage, dict[str, str]] = {
    "en": {
        "settings_title": "Vocal-More Settings",
        "menu_status_idle": "Status: Idle",
        "menu_status_recording": "Status: Recording...",
        "menu_status_processing": "Status: Processing...",
        "menu_status_unknown": "Status: Unknown",
        "menu_quick_settings": "Quick Settings",
        "menu_recording_mode": "Recording Mode",
        "menu_recording_mode_title": "Recording Mode: {value}",
        "menu_asr_model": "ASR Model",
        "menu_asr_model_title": "ASR Model: {value}",
        "menu_enable_polishing": "Enable Polishing",
        "menu_polish_strength": "Polish Strength",
        "menu_polish_strength_title": "Polish Strength: {value}",
        "menu_settings": "Settings...",
        "menu_quit": "Quit Vocal-More",
        "notification_recording_in_progress_title": "Recording In Progress",
        "notification_recording_in_progress_body": (
            "Stop the current session before switching recording modes."
        ),
        "mode_walkie_talkie": "Walkie-Talkie (Hold)",
        "mode_realtime_long": "Real-time Long (Toggle)",
        "polish_level_minimal": "Minimal",
        "polish_level_balanced": "Balanced",
        "polish_level_strong": "Strong",
        "settings_device_changed": "Device changed. Please test again.",
        "settings_recording_not_found": "Recording file not found",
        "settings_empty_transcription": "Empty transcription result",
    },
    "zh": {
        "settings_title": "Vocal-More 设置",
        "menu_status_idle": "状态：空闲",
        "menu_status_recording": "状态：录音中...",
        "menu_status_processing": "状态：处理中...",
        "menu_status_unknown": "状态：未知",
        "menu_quick_settings": "快捷设置",
        "menu_recording_mode": "录音模式",
        "menu_recording_mode_title": "录音模式：{value}",
        "menu_asr_model": "识别模型",
        "menu_asr_model_title": "识别模型：{value}",
        "menu_enable_polishing": "启用润色",
        "menu_polish_strength": "润色强度",
        "menu_polish_strength_title": "润色强度：{value}",
        "menu_settings": "设置...",
        "menu_quit": "退出 Vocal-More",
        "notification_recording_in_progress_title": "正在录音",
        "notification_recording_in_progress_body": "请先停止当前录音，再切换录音模式。",
        "mode_walkie_talkie": "对讲模式（按住）",
        "mode_realtime_long": "实时长录（切换）",
        "polish_level_minimal": "轻度",
        "polish_level_balanced": "均衡",
        "polish_level_strong": "强力",
        "settings_device_changed": "输入设备已变更，请重新测试。",
        "settings_recording_not_found": "未找到录音文件",
        "settings_empty_transcription": "识别结果为空",
    },
}


def normalize_ui_language(raw: object) -> UILanguage:
    """Normalize an arbitrary config value to a supported UI language."""
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in ("en", "zh"):
            return normalized
    return "en"


def t(language: object, key: str, **kwargs) -> str:
    """Look up a localized string."""
    locale = normalize_ui_language(language)
    template = UI_TEXT[locale].get(key, UI_TEXT["en"].get(key, key))
    if kwargs:
        return template.format(**kwargs)
    return template

