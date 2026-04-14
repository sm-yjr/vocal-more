# Polish Presets Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add five new text polish presets - technical, bilingual, grammar, professional, and chat - across the Python backend, Python prototype menu, and Swift app settings.

**Architecture:** Keep the existing `polish_mode` and `polish_level` fields for trigger behavior and rewrite intensity, and add a new `llm.polish_preset` field for scenario intent. The backend selects the prompt from both level and preset, while Swift and the Python prototype expose the preset as a separate picker/menu so users can mix intent with existing levels.

**Tech Stack:** Python, SwiftUI, XCTest, pytest, YAML config

---

### Task 1: Backend preset config parsing

**Files:**
- Modify: `src/vocal_more/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add tests that assert `llm.polish_preset` round-trips through save/load and invalid values fall back to `default`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -k polish_preset -v`
Expected: FAIL because `polish_preset` does not exist yet.

**Step 3: Write minimal implementation**

Add `polish_preset` to `LLMConfig`, parse it in `_from_dict`, validate it in a helper, and include it in `to_dict`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -k polish_preset -v`
Expected: PASS.

### Task 2: Backend prompt selection for new presets

**Files:**
- Modify: `src/vocal_more/core/text_polisher.py`
- Test: `tests/test_text_polisher.py`

**Step 1: Write the failing test**

Add prompt-selection tests for `technical`, `bilingual`, `grammar`, `professional`, and `chat` that inspect the generated prompt text.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_text_polisher.py -k polish_preset -v`
Expected: FAIL because the prompt builder does not support presets yet.

**Step 3: Write minimal implementation**

Add preset-specific prompt templates and select them from `_build_prompt`, while preserving existing light/concise/gentle behavior.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_text_polisher.py -k polish_preset -v`
Expected: PASS.

### Task 3: Python prototype menu support

**Files:**
- Modify: `src/vocal_more/app.py`

**Step 1: Write the failing test**

If there is already menu coverage, add it; otherwise verify behavior manually by inspecting config updates after the menu callback methods run.

**Step 2: Run test to verify it fails**

Use the smallest existing test hook or skip if there is no menu test seam.

**Step 3: Write minimal implementation**

Add a `Preset` submenu with items for `Default`, `Technical`, `Bilingual`, `Grammar`, `Professional`, and `Chat`, plus callback methods and checkmark updates.

**Step 4: Verify**

Run the relevant test or confirm config saves the selected preset when the callback is invoked.

### Task 4: Swift app state preset sync

**Files:**
- Modify: `VocalMore/VocalMore/Models/AppState.swift`
- Test: `VocalMore/VocalMoreTests/AppStateTests.swift`

**Step 1: Write the failing test**

Add an XCTest asserting `updateFromConfig` reads `llm.polish_preset` and that defaults stay at `default`.

**Step 2: Run test to verify it fails**

Run: `xcodebuild test -scheme VocalMore -only-testing:VocalMoreTests/AppStateTests`
Expected: FAIL because `polishPreset` does not exist yet.

**Step 3: Write minimal implementation**

Add a `polishPreset` property to `AppState` and sync it from backend config.

**Step 4: Run test to verify it passes**

Run: `xcodebuild test -scheme VocalMore -only-testing:VocalMoreTests/AppStateTests`
Expected: PASS.

### Task 5: Swift settings UI for presets

**Files:**
- Modify: `VocalMore/VocalMore/Views/PolishSettingsTab.swift`

**Step 1: Write the failing test**

Rely on `AppStateTests` for state sync, then manually verify the picker sends `llm.polish_preset` updates unless there is an existing SwiftUI test harness.

**Step 2: Write minimal implementation**

Add a `Polish Preset` picker between mode and level with concise explanatory copy and values matching backend config.

**Step 3: Verify**

Confirm the Swift code compiles via the existing test target.

### Task 6: Full verification

**Files:**
- Test: `tests/test_text_polisher.py`
- Test: `tests/test_config.py`
- Test: `VocalMore/VocalMoreTests/AppStateTests.swift`

**Step 1: Run focused Python tests**

Run: `pytest tests/test_config.py tests/test_text_polisher.py -v`

**Step 2: Run focused Swift tests**

Run: `xcodebuild test -scheme VocalMore -only-testing:VocalMoreTests/AppStateTests`

**Step 3: Fix any failures and re-run**

Do not claim success until all targeted verification commands pass.
