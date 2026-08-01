"""Localized UI strings for the Python app surfaces."""

from typing import Literal

UILanguage = Literal["en", "zh"]


UI_TEXT: dict[UILanguage, dict[str, str]] = {
    "en": {
        "settings_title": "Vocal-More Settings",
        "menu_status_idle": "Status: Idle",
        "menu_status_starting": "Status: Starting...",
        "menu_status_recording": "Status: Recording...",
        "menu_status_stopping": "Status: Stopping...",
        "menu_status_processing": "Status: Processing...",
        "menu_status_cancelling": "Status: Cancelling...",
        "menu_status_failed": "Status: Error",
        "menu_status_unknown": "Status: Unknown",
        "menu_environment": "Environment Check",
        "menu_check_for_updates": "Check for Updates…",
        "menu_environment_title": "Environment: {value}",
        "menu_export_diagnostics": "Export Diagnostics…",
        "menu_run_environment_check": "Run Check Again",
        "menu_recording_mode": "Recording Mode",
        "menu_recording_mode_title": "Recording Mode: {value}",
        "menu_microphone": "Microphone",
        "menu_microphone_title": "Microphone: {value}",
        "menu_microphone_system_default": "System Default",
        "menu_microphone_settings": "Microphone Settings...",
        "menu_asr_model": "ASR Model",
        "menu_asr_model_title": "ASR Model: {value}",
        "menu_enable_polishing": "Enable Polishing",
        "menu_polish_strength": "Polish Strength",
        "menu_polish_strength_title": "Polish Strength: {value}",
        "menu_settings": "Settings...",
        "menu_more_settings": "More Settings...",
        "menu_quit": "Quit Vocal-More",
        "notification_configuration_error_title": "Configuration Error",
        "notification_transcription_complete_title": "Transcription Complete",
        "notification_error_title": "Error",
        "notification_permissions_required_title": "Permissions Required",
        "notification_permissions_required_body": (
            "Please grant Accessibility permissions in System Settings → "
            "Privacy & Security → Accessibility"
        ),
        "notification_app_started_title": "Vocal-More Started",
        "notification_app_started_body": "Vocal-More is running in the menu bar.",
        "notification_hotkeys_ready_title": "Hotkeys Ready",
        "notification_hotkeys_ready_body": (
            "Accessibility permission is active. Vocal-More hotkeys are ready."
        ),
        "notification_recording_in_progress_title": "Recording In Progress",
        "notification_recording_in_progress_body": (
            "Stop the current session before switching recording modes."
        ),
        "notification_environment_attention_title": "Environment Needs Attention",
        "notification_environment_attention_body": "Check: {items}",
        "notification_diagnostics_exported_title": "Diagnostics Exported",
        "notification_diagnostics_exported_body": "Saved to {path}",
        "notification_diagnostics_export_failed_title": "Diagnostics Export Failed",
        "notification_dictionary_learning_applied_title": (
            "Dictionary Updated Automatically"
        ),
        "notification_dictionary_learning_applied_body": (
            "Added “{term}”. You can undo this in Dictionary settings."
        ),
        "notification_dictionary_learning_applied_group_title": (
            "Added {count} Dictionary Terms"
        ),
        "notification_dictionary_learning_applied_group_body": (
            "Added “{terms}”. You can undo them separately in Dictionary settings."
        ),
        "notification_dictionary_learning_review_title": "Dictionary Review Needed",
        "notification_dictionary_learning_review_body": (
            "Review the suggested term “{term}” in Dictionary settings."
        ),
        "mode_microphone_unavailable": "Could not start microphone: {details}",
        "mode_microphone_permission_requested": (
            "Microphone access was requested. Allow it in the system prompt, then start again."
        ),
        "mode_microphone_start_timeout": (
            "Microphone did not respond in time. Dictation was reset; try again."
        ),
        "mode_microphone_device_changed": (
            "Microphone changed or became unavailable. Re-select the input device and try again."
        ),
        "mode_recording_too_short": "Recording too short. Hold the hotkey a bit longer.",
        "mode_asr_error": "ASR error: {details}",
        "mode_polish_error": "Polish error: {details}",
        "mode_processing_error": "Processing error: {details}",
        "meeting_generation_canceled": "Meeting generation canceled",
        "mode_walkie_talkie": "Walkie-Talkie (Hold)",
        "mode_realtime_long": "Real-time Long (Toggle)",
        "mode_meeting": "Meeting Mode (Toggle)",
        "polish_level_minimal": "Minimal",
        "polish_level_balanced": "Balanced",
        "polish_level_strong": "Strong",
        "settings_device_changed": "Device changed. Please test again.",
        "settings_recording_not_found": "Recording file not found",
        "settings_empty_transcription": "Empty transcription result",
        "environment_status_ok": "Ready",
        "environment_status_error": "Needs Attention",
        "environment_status_unknown": "Pending",
        "environment_check_api_key": "API Key",
        "environment_check_accessibility": "Accessibility",
        "environment_check_input_device": "Input Device",
        "environment_check_microphone_permission": "Microphone Permission",
        "environment_check_hotkey_listener": "Hotkey Listener",
        "environment_value_api_key_ok": "Configured",
        "environment_value_api_key_error": "Missing",
        "environment_value_accessibility_ok": "Granted",
        "environment_value_accessibility_error": "Missing",
        "environment_value_input_device_ok": "Available",
        "environment_value_input_device_error": "Unavailable",
        "environment_value_input_device_unknown": "Hidden Until Permission",
        "environment_value_microphone_permission_ok": "Granted",
        "environment_value_microphone_permission_error": "Denied",
        "environment_value_microphone_permission_unknown": "Not Requested",
        "environment_value_hotkey_listener_ok": "Running",
        "environment_value_hotkey_listener_error": "Failed",
        "environment_value_hotkey_listener_unknown": "Not Started",
        "environment_check_title": "{name}: {value}",
    },
    "zh": {
        "settings_title": "Vocal-More 设置",
        "menu_status_idle": "状态：空闲",
        "menu_status_starting": "状态：启动中...",
        "menu_status_recording": "状态：录音中...",
        "menu_status_stopping": "状态：停止中...",
        "menu_status_processing": "状态：处理中...",
        "menu_status_cancelling": "状态：取消中...",
        "menu_status_failed": "状态：出错",
        "menu_status_unknown": "状态：未知",
        "menu_environment": "环境检查",
        "menu_check_for_updates": "检查更新…",
        "menu_environment_title": "环境：{value}",
        "menu_export_diagnostics": "导出诊断包…",
        "menu_run_environment_check": "重新检查",
        "menu_recording_mode": "录音模式",
        "menu_recording_mode_title": "录音模式：{value}",
        "menu_microphone": "麦克风",
        "menu_microphone_title": "麦克风：{value}",
        "menu_microphone_system_default": "系统默认",
        "menu_microphone_settings": "麦克风设置...",
        "menu_asr_model": "识别模型",
        "menu_asr_model_title": "识别模型：{value}",
        "menu_enable_polishing": "启用润色",
        "menu_polish_strength": "润色强度",
        "menu_polish_strength_title": "润色强度：{value}",
        "menu_settings": "设置...",
        "menu_more_settings": "更多设置...",
        "menu_quit": "退出 Vocal-More",
        "notification_configuration_error_title": "配置错误",
        "notification_transcription_complete_title": "识别完成",
        "notification_error_title": "错误",
        "notification_permissions_required_title": "需要权限",
        "notification_permissions_required_body": (
            "请在 系统设置 → 隐私与安全性 → 辅助功能 中授予辅助功能权限"
        ),
        "notification_app_started_title": "Vocal-More 已启动",
        "notification_app_started_body": "Vocal-More 已在状态栏运行。",
        "notification_hotkeys_ready_title": "热键已可用",
        "notification_hotkeys_ready_body": (
            "辅助功能权限已生效，现在可以直接使用 Vocal-More 热键。"
        ),
        "notification_recording_in_progress_title": "正在录音",
        "notification_recording_in_progress_body": "请先停止当前录音，再切换录音模式。",
        "notification_environment_attention_title": "环境需要处理",
        "notification_environment_attention_body": "请检查：{items}",
        "notification_diagnostics_exported_title": "诊断包已导出",
        "notification_diagnostics_exported_body": "已保存到 {path}",
        "notification_diagnostics_export_failed_title": "导出诊断包失败",
        "notification_dictionary_learning_applied_title": "已自动添加词条",
        "notification_dictionary_learning_applied_body": (
            "已添加“{term}”，可在词典设置中撤销。"
        ),
        "notification_dictionary_learning_applied_group_title": (
            "已自动添加 {count} 个词条"
        ),
        "notification_dictionary_learning_applied_group_body": (
            "已添加“{terms}”，可在词典设置中分别撤销。"
        ),
        "notification_dictionary_learning_review_title": "词条需要确认",
        "notification_dictionary_learning_review_body": (
            "请在词典设置中确认建议词条“{term}”。"
        ),
        "mode_microphone_unavailable": "无法启动麦克风：{details}",
        "mode_microphone_permission_requested": (
            "已请求麦克风权限。请在系统提示中允许访问，然后再次开始录音。"
        ),
        "mode_microphone_start_timeout": (
            "麦克风启动超时，听写已自动复位，请重试。"
        ),
        "mode_microphone_device_changed": (
            "麦克风设备似乎已变更，请重新选择输入设备或重新连接麦克风后再试。"
        ),
        "mode_recording_too_short": "录音太短了，请稍微多按一会儿热键。",
        "mode_asr_error": "识别错误：{details}",
        "mode_polish_error": "润色错误：{details}",
        "mode_processing_error": "处理错误：{details}",
        "meeting_generation_canceled": "会议记录生成已取消",
        "mode_walkie_talkie": "对讲模式（按住）",
        "mode_realtime_long": "实时长录（切换）",
        "mode_meeting": "会议模式（切换）",
        "polish_level_minimal": "轻度",
        "polish_level_balanced": "均衡",
        "polish_level_strong": "强力",
        "settings_device_changed": "输入设备已变更，请重新测试。",
        "settings_recording_not_found": "未找到录音文件",
        "settings_empty_transcription": "识别结果为空",
        "environment_status_ok": "就绪",
        "environment_status_error": "需要处理",
        "environment_status_unknown": "待检查",
        "environment_check_api_key": "API Key",
        "environment_check_accessibility": "辅助功能",
        "environment_check_input_device": "输入设备",
        "environment_check_microphone_permission": "麦克风权限",
        "environment_check_hotkey_listener": "热键监听",
        "environment_value_api_key_ok": "已配置",
        "environment_value_api_key_error": "缺失",
        "environment_value_accessibility_ok": "已授权",
        "environment_value_accessibility_error": "缺失",
        "environment_value_input_device_ok": "可用",
        "environment_value_input_device_error": "不可用",
        "environment_value_input_device_unknown": "授权后可见",
        "environment_value_microphone_permission_ok": "已授权",
        "environment_value_microphone_permission_error": "被拒绝",
        "environment_value_microphone_permission_unknown": "尚未请求",
        "environment_value_hotkey_listener_ok": "运行中",
        "environment_value_hotkey_listener_error": "启动失败",
        "environment_value_hotkey_listener_unknown": "未启动",
        "environment_check_title": "{name}：{value}",
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


def format_microphone_start_error(language: object, error: Exception) -> str:
    """Map structured recorder failures to one user-facing localized message."""
    if getattr(error, "code", None) == "microphone_permission_requested":
        return t(language, "mode_microphone_permission_requested")
    if bool(getattr(error, "startup_timed_out", False)):
        return t(language, "mode_microphone_start_timeout")
    if bool(getattr(error, "device_change_detected", False)):
        return t(language, "mode_microphone_device_changed")
    return t(
        language,
        "mode_microphone_unavailable",
        details=str(error),
    )
