"""Contract tests for the generated React settings application."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_DIR = ROOT / "resources" / "settings"
SETTINGS_HTML = SETTINGS_DIR / "settings.html"
FRONTEND = ROOT / "frontend" / "settings"
SOURCE = FRONTEND / "src"


def _built_asset_text(suffix: str) -> str:
    assets = list((SETTINGS_DIR / "assets").glob(f"*{suffix}"))
    assert len(assets) == 1
    return assets[0].read_text(encoding="utf-8")


def test_vite_build_is_file_url_safe_and_fully_offline():
    html = SETTINGS_HTML.read_text(encoding="utf-8")
    references = re.findall(r'(?:src|href)="([^"]+)"', html)

    assert references
    assert all(reference.startswith("./assets/") for reference in references)
    assert all(
        (SETTINGS_DIR / reference.removeprefix("./")).is_file()
        for reference in references
    )
    assert "http://" not in html
    assert "https://" not in html
    assert 'src="/' not in html
    assert 'href="/' not in html
    # WKWebView does not execute external ES modules loaded from file://.
    # The production bundle is a single self-contained chunk, so it must be
    # emitted as a deferred classic script for the native settings host.
    assert 'type="module"' not in html
    assert re.search(r'<script defer src="\./assets/[^"]+\.js"></script>', html)


def test_generated_bundle_contains_python_to_javascript_contract():
    javascript = _built_asset_text(".js")
    expected_globals = {
        "loadAll",
        "collectFormState",
        "setInterfaceLanguage",
        "updateConfig",
        "loadDevices",
        "loadAudioInputStatus",
        "loadEnvironmentChecks",
        "loadDictionary",
        "loadDictionaryLearning",
        "micTestStarted",
        "micTestComplete",
        "micTestError",
        "micTestLevel",
        "micTestPlayback",
        "loadRecordings",
        "retryStarted",
        "retryCompleted",
        "retryFailed",
        "meetingNotesStarted",
        "meetingNotesStage",
        "loadContextProfile",
        "recordingCompactionStarted",
        "recordingCompactionComplete",
        "recordingCompactionFailed",
        "recordingDeleted",
        "playAudio",
        "copiedFeedback",
    }

    assert "messageHandlers" in javascript
    assert "settings" in javascript
    assert "_initData" in javascript
    for name in expected_globals:
        assert name in javascript


def test_frontend_uses_react_vite_shadcn_without_runtime_node_or_fonts():
    package = json.loads((FRONTEND / "package.json").read_text())
    components = json.loads((FRONTEND / "components.json").read_text())
    css = (SOURCE / "index.css").read_text()
    main = (SOURCE / "main.tsx").read_text()
    vite_config = (FRONTEND / "vite.config.ts").read_text()

    assert package["dependencies"]["react"]
    assert package["devDependencies"]["vite"]
    assert components["style"] == "base-nova"
    assert components["style"].startswith("base-")
    assert "createRoot" in main
    assert "node:" not in main
    assert "@font-face" not in css
    assert "fonts.googleapis.com" not in css
    assert "-apple-system" in css
    assert 'format: "iife"' in vite_config
    assert "inlineDynamicImports: true" in vite_config
    assert "wkWebViewClassicScript" in vite_config


def test_frontend_target_does_not_use_safari_15_4_only_array_at():
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SOURCE.rglob("*")
        if path.suffix in {".ts", ".tsx"}
    )

    assert ".at(" not in source_text


def test_settings_source_preserves_all_seven_sections_and_form_snapshot():
    app = (SOURCE / "App.tsx").read_text()
    store = (SOURCE / "settings" / "store.ts").read_text()

    for tab in (
        "general",
        "audio",
        "recognition",
        "polish",
        "shortcuts",
        "dictionary",
        "history",
    ):
        assert f'value="{tab}"' in app
    assert "collectFormState" in store
    assert "prompt_overrides" in store
    assert "excluded_bundle_ids" in store
    assert "double_tap_threshold" in store


def test_omni_polish_gating_and_prompt_categories_remain_available():
    polish = (
        SOURCE / "components" / "settings" / "polish-settings.tsx"
    ).read_text()

    assert "handles_inline_polish" in polish
    assert "llmEnabled" in polish
    for category in ("output_type", "level", "structured", "tone", "persona"):
        assert f'value="{category}"' in polish
    assert "prompt_overrides" in polish


def test_custom_hotkey_capture_covers_catalog_browser_codes():
    from vocal_more.domain.hotkey_catalog import CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE

    shortcuts = (
        SOURCE / "components" / "settings" / "shortcuts-settings.tsx"
    ).read_text()
    for browser_code in CUSTOM_HOTKEY_KEYS_BY_BROWSER_CODE:
        assert f"{browser_code}:" in shortcuts

    assert '"fn"' in shortcuts
    assert "active.filter" in shortcuts
    assert "hotkeys.length === 0" not in shortcuts


def test_dictionary_and_history_actions_keep_existing_message_names():
    dictionary = (
        SOURCE / "components" / "settings" / "dictionary-settings.tsx"
    ).read_text()
    history = (
        SOURCE / "components" / "settings" / "history-settings.tsx"
    ).read_text()
    app = (SOURCE / "App.tsx").read_text()

    for action in (
        "addDictEntry",
        "removeDictEntry",
        "approveDictionaryLearning",
        "rejectDictionaryLearning",
        "undoDictionaryLearning",
    ):
        assert action in dictionary
    assert "getRecordings" in app
    for action in (
        "retryTranscription",
        "generateMeetingNotes",
        "deleteRecording",
        "playRecording",
    ):
        assert action in history
    assert "5000" in history
    assert "normalizeMeeting" in history


def test_distribution_contains_frontend_third_party_notices():
    notices = SETTINGS_DIR / "THIRD-PARTY-LICENSES.md"

    assert notices.is_file()
    text = notices.read_text(encoding="utf-8")
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    for dependency in package["dependencies"]:
        assert f"## {dependency} -" in text

    shadcn_notice = SETTINGS_DIR / "SHADCN-UI-LICENSE.txt"
    assert shadcn_notice.is_file()
    shadcn_text = shadcn_notice.read_text(encoding="utf-8")
    assert "MIT License" in shadcn_text
    assert "Copyright (c) 2023 shadcn" in shadcn_text
