# Polish Dimensions Rewrite Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the old `polish_level` and `polish_preset` model with a clean three-axis system: `level`, `tone`, and `persona`, without any compatibility layer.

**Architecture:** The backend config becomes the single source of truth with `llm.level`, `llm.tone`, and `llm.persona`. Prompt assembly is split across these three orthogonal dimensions, and both the Python prototype and Swift UI expose the same structure in settings and menu bar controls.

**Tech Stack:** Python, SwiftUI, XCTest, pytest, YAML config

---

### Task 1: Replace backend config fields

**Files:**
- Modify: `src/vocal_more/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add tests asserting the old fields are gone and new fields `level`, `tone`, `persona` load/save correctly.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -k "level or tone or persona" -v`
Expected: FAIL because config still uses the old fields.

**Step 3: Write minimal implementation**

Replace `polish_level` and `polish_preset` with `level`, `tone`, and `persona` in `LLMConfig`, parsing helpers, `_from_dict`, and `to_dict`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -k "level or tone or persona" -v`
Expected: PASS.

### Task 2: Replace backend prompt assembly

**Files:**
- Modify: `src/vocal_more/core/text_polisher.py`
- Test: `tests/test_text_polisher.py`

**Step 1: Write the failing test**

Add tests asserting:
- `level` supports at least `minimal`, `balanced`, `strong`
- `tone` supports `neutral`, `gentle`, `direct`
- `persona` supports `default`, `technical`, `bilingual`, `professional`, `chat`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_text_polisher.py -k "tone or persona or minimal or balanced or strong" -v`
Expected: FAIL because prompt builder still uses old fields.

**Step 3: Write minimal implementation**

Rewrite prompt composition to derive output instructions from the three orthogonal dimensions. Remove old `concise`, `gentle`, and `preset` branching.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_text_polisher.py -k "tone or persona or minimal or balanced or strong" -v`
Expected: PASS.

### Task 3: Replace Python prototype menu structure

**Files:**
- Modify: `src/vocal_more/app.py`
- Test: `tests/test_rpc_handler.py`

**Step 1: Write the failing test**

Add tests asserting Text Polish menu contains `Off`, `Level`, `Tone`, and `Persona`, with dynamic titles and updated choices.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_rpc_handler.py -k "tone or persona or menu" -v`
Expected: FAIL because the menu still exposes preset or old level labels.

**Step 3: Write minimal implementation**

Rebuild the Python menu around the new fields and update callbacks/checkmarks/titles.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_rpc_handler.py -k "tone or persona or menu" -v`
Expected: PASS.

### Task 4: Replace Swift app state and settings

**Files:**
- Modify: `VocalMore/VocalMore/Models/AppState.swift`
- Modify: `VocalMore/VocalMore/Views/PolishSettingsTab.swift`
- Modify: `VocalMore/VocalMore/MenuBar/MenuBarController.swift`
- Test: `VocalMore/VocalMoreTests/AppStateTests.swift`

**Step 1: Write the failing test**

Add or update XCTest assertions so config sync expects `level`, `tone`, and `persona`, not the removed fields.

**Step 2: Run test/build to verify it fails**

Run the Swift validation path currently available in the repo.
Expected: FAIL or compile mismatch until all references are renamed.

**Step 3: Write minimal implementation**

Update `AppState`, settings pickers, and menu bar submenus to use the new field names and values.

**Step 4: Run test/build to verify it passes**

Run the same Swift validation command and confirm a successful build.

### Task 5: Full verification

**Files:**
- Test: `tests/test_config.py`
- Test: `tests/test_text_polisher.py`
- Test: `tests/test_rpc_handler.py`

**Step 1: Run focused Python verification**

Run: `pytest tests/test_config.py tests/test_text_polisher.py tests/test_rpc_handler.py -v`

**Step 2: Run Swift build verification**

Run: `xcodebuild build-for-testing -project "VocalMore.xcodeproj" -scheme "VocalMore"`

**Step 3: Fix any failures and re-run**

Do not claim completion until both commands pass fresh.
