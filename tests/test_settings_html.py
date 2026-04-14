"""Tests for legacy HTML settings behavior."""

from pathlib import Path


SETTINGS_HTML = Path(__file__).resolve().parents[1] / "resources" / "settings" / "settings.html"


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

    assert '<option value="auto">Chinese + English</option>' in html
