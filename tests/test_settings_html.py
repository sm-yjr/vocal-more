"""Tests for legacy HTML settings behavior."""

import re
from pathlib import Path


SETTINGS_HTML = Path(__file__).resolve().parents[1] / "resources" / "settings" / "settings.html"
SETTINGS_CSS = SETTINGS_HTML.with_name("settings.css")


def test_html_settings_loads_external_stylesheet():
    """Settings layout styles should live outside the already-large HTML file."""
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    assert 'rel="stylesheet"' in html
    assert 'href="settings.css"' in html
    assert SETTINGS_CSS.exists()


def test_html_settings_has_dedicated_omni_llm_gating_logic():
    """Omni ASR should disable only LLM-specific controls in the HTML settings UI."""
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    assert 'class="llm-control"' in html
    assert "function selectedAsrHandlesInlinePolish()" in html
    assert "function toggleLlmControls(enabled)" in html
    assert "renderPolish();" in html


def test_html_settings_can_edit_each_polish_prompt_category():
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    for category in ("output_type", "level", "structured", "tone", "persona"):
        assert f'data-prompt-category="{category}"' in html
    assert 'id="prompt_override_source"' in html
    assert 'id="prompt_override_text"' in html
    assert "prompt_overrides: JSON.parse(JSON.stringify(polishPromptOverrides()))" in html


def test_html_settings_has_mixed_chinese_english_language_option():
    """Recognition settings should expose a mixed Chinese/English option."""
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    assert 'id="asr_language"' in html
    assert 'option value="auto" data-i18n="recognition_language_auto"' in html


def test_html_settings_has_interface_language_switcher():
    """Settings should allow switching the interface language."""
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    assert 'id="ui_language"' in html
    assert "function setInterfaceLanguage(language)" in html
    assert "const UI_TRANSLATIONS = {" in html


def test_html_settings_can_generate_and_render_meeting_notes():
    """Recording history should expose meeting notes generation and speaker segments."""
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    assert "history_meeting_notes" in html
    assert "function generateMeetingNotes(id)" in html
    assert "function buildMeetingNotes(meeting)" in html
    assert "function buildMeetingMinutes(minutes)" in html
    assert "function formatMeetingTimestamp(seconds)" in html
    assert "function normalizeMeetingForDisplay(meeting)" in html
    assert "function repairMeetingJsonText(text)" in html
    assert "rec-meeting-timeline" in html
    assert "postMsg({ action: 'generateMeetingNotes', id: id })" in html


def test_html_settings_exposes_meeting_mode():
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    assert 'option value="meeting" data-i18n="mode_meeting"' in html
    assert "history_mode_meeting" in html
    assert "focusRecordingId" in html


def test_html_custom_hotkey_capture_covers_catalog_browser_codes():
    """The settings UI should be able to record every browser-visible catalog key."""
    from vocal_more.domain.hotkey_catalog import CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE

    html = SETTINGS_HTML.read_text(encoding="utf-8")

    for browser_code in CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE:
        assert f"{browser_code}:" in html


def test_html_allows_disabling_all_builtin_hotkeys():
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    assert "Don't allow disabling all" not in html
    assert "hotkeys.length === 0" not in html


def test_html_shortcuts_only_lists_fn_as_builtin_hotkey():
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    assert "const allKeys = ['fn'];" in html
    assert "shortcut_hotkey_right_cmd" not in html
    assert "shortcut_hotkey_double_cmd" not in html
    assert "shortcut_hotkey_f13" not in html
    assert "hotkey_double_tap" not in html


def test_recording_history_copy_button_is_primary_action():
    css = SETTINGS_CSS.read_text(encoding="utf-8")

    copy_rule = re.search(r"\.rec-btn-copy\s*\{([^}]*)\}", css)
    retry_rule = re.search(r"\.rec-btn-retry\s*\{([^}]*)\}", css)
    assert copy_rule is not None
    assert retry_rule is not None

    assert "background: var(--accent)" in copy_rule.group(1)
    assert "color: #fff" in copy_rule.group(1)
    assert "background: var(--accent)" not in retry_rule.group(1)


def test_dictionary_settings_disclose_cloud_learning_and_allow_exclusions():
    html = SETTINGS_HTML.read_text(encoding="utf-8")

    assert 'id="dictionary_learning_enabled"' in html
    assert 'id="dictionary_learning_excluded_apps"' in html
    assert "dictionary_learning_privacy_hint" in html
    assert "dictionary_learning.enabled" in html
    assert "dictionary_learning.excluded_bundle_ids" in html
    assert 'id="dict_learning_records"' in html
    assert "approveDictionaryLearning" in html
    assert "rejectDictionaryLearning" in html
    assert "undoDictionaryLearning" in html
