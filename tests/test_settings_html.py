"""Tests for legacy HTML settings behavior."""

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
