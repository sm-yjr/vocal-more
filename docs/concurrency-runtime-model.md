# Concurrency Runtime Model

This document describes the current concurrency boundaries in the active Python app under `src/vocal_more/`.

The goal is not to eliminate every background thread. The goal is to make thread ownership explicit so UI work, dictation control, audio capture, and ASR/network work cannot accidentally fight each other.

## Current Domains

### 1. Main thread owns UI

Only the main thread may touch AppKit/WebKit UI state:

- menu bar state
- notifications
- floating capsule `NSPanel`
- floating capsule `WKWebView`
- settings window `NSWindow` / `WKWebView`

Use the existing marshaling helpers when background code needs a UI update:

- `VocalMoreApp._run_on_main_thread()`
- `FloatingCapsule._run_on_main_thread()`
- `SettingsWindow._eval_js()` plus the main-thread JS drain timer

Background code should emit UI intents, not call AppKit/WebKit directly.

### 2. Hotkey event thread owns Quartz event capture

`HotkeyManager` runs the Quartz event tap on its own listener thread. That thread only:

- receives system keyboard events
- converts them into `HotkeyEvent`
- enqueues those events onto the callback worker

It does not run dictation business logic directly.

### 3. Hotkey callback worker serializes raw hotkey events

`HotkeyManager` has one callback worker thread. It guarantees ordered delivery of:

- `FN_PRESSED`
- `FN_RELEASED`
- `DOUBLE_CMD`

This removes the old thread-per-event behavior and preserves order during rapid key sequences.

### 4. Dictation command coordinator owns control intent ordering

`DictationCommandCoordinator` is the serial control plane for dictation commands.

Current command sources:

- menu app hotkeys
- floating capsule cancel / finish
- RPC `hotkey_pressed`
- RPC `hotkey_released`
- RPC `cancel`
- shutdown cancellation paths

This means `start / stop / cancel` intents are serialized before they reach the active mode.

### 5. Audio callback thread owns audio capture only

`AudioRecorder._audio_callback()` still runs on the PortAudio callback thread.

It currently does:

- lightweight DSP needed for the low-voice pipeline
- RMS computation
- PCM conversion
- append to the in-memory recording buffer
- invoke `on_audio_chunk(audio_data)`
- invoke `on_audio_level(rms)`

It no longer performs realtime network sends directly.

### 6. ASR sender thread owns outbound realtime audio sends

`ASREngine` now has one long-lived sender thread and one bounded outbound queue.

The flow is:

1. audio callback calls `ASREngine.send_audio(chunk)`
2. `send_audio()` enqueues raw PCM into a bounded queue
3. sender thread waits for session readiness
4. sender thread base64-encodes and calls `conversation.append_audio(...)`

If the queue fills or sender drain fails, the realtime path is marked degraded and finalize-time logic falls back to batch transcription using the full PCM recording.

### 7. ASR connect thread owns realtime session startup

`ASREngine.start()` still launches a background connect thread to:

- create or reuse a realtime conversation
- update the session
- wait for `session.updated`
- mark the session ready

This startup path is still separate from the sender thread, but session readiness and failure flags are protected by the engine lock.

### 8. SDK callback thread delivers inbound realtime events

DashScope realtime callbacks may arrive on SDK-managed threads. Today, `StreamingASRCallback` still:

- maintains its own callback-local aggregation state
- sets events used by `ASREngine.stop()`
- forwards partial/error/final callbacks upward

This is safe enough for current behavior, but it is not yet the final “single state owner” architecture.

### 9. Mode-local processing executor owns finish workflows

Each mode has its own single-worker `BackgroundExecutor` for finish-time work:

- `WalkieTalkieMode`
- `RealtimeLongMode`

That executor runs:

- stop/finalize transcription
- recording persistence updates
- optional polish
- optional paste

Each run is tagged with a session token so `cancel()` can invalidate late results.

### 10. Small shared executors own best-effort background jobs

These are non-realtime helper pools:

- RPC retry transcription executor
- settings window retry transcription executor

They are bounded and have explicit `close()` paths.

## Ownership Rules

### Rule 1: UI only on the main thread

If code touches AppKit/WebKit, it must marshal to the main thread first.

### Rule 2: Dictation control commands go through the coordinator

If code represents `start / stop / cancel / finish`, it should enter through `DictationCommandCoordinator`, not spawn an ad-hoc worker.

### Rule 3: Audio capture must not block on network work

The audio callback may enqueue audio, but must not wait on ASR/network state.

### Rule 4: Late results must be droppable

Every finish-time workflow is tied to a mode session token. If the token is invalidated, the workflow must not emit results or paste text.

### Rule 5: Background helpers need explicit shutdown

Any long-lived worker or executor must have a `close()` path and be called during app or RPC shutdown.

## What This Architecture Prevents

The current model directly prevents these failure modes:

- hotkey press/release reordering caused by thread-per-event callbacks
- AppKit/WebKit crashes from background-thread UI access
- audio callback stalls caused by realtime network sends
- late processing results pasting text after the user canceled
- unbounded growth of ad-hoc retry threads
- silent realtime degradation when sender backpressure occurs

## Known Remaining Gaps

These are still intentionally deferred:

### 1. Inbound ASR events do not yet flow through the command coordinator

`StreamingASRCallback` is still partly an active state holder instead of a pure event source.

### 2. Mode lifecycle is safer, but not yet a single explicit state machine

We now rely on:

- serial command ingress
- engine-local locking
- mode session tokens

This is much safer than before, but it is not yet the final `IDLE / STARTING / RECORDING / STOPPING / PROCESSING / CANCELLING / FAILED` owner-state-machine design.

### 3. Audio callback still performs some compute-heavy work

The callback no longer sends on the network, which was the biggest risk. It still performs DSP, RMS calculation, and PCM conversion inline.

## Practical Guidance For Future Changes

- If a new feature needs to start or stop dictation, wire it through `DictationCommandCoordinator`.
- If a background task needs to update the UI, emit an intent and marshal it onto the main thread.
- If a task is best-effort and non-realtime, prefer `BackgroundExecutor` over a raw `threading.Thread(...)`.
- If a new realtime path touches audio, preserve the rule that the PortAudio callback never blocks on network operations.
- If a late callback could affect user-visible state, tie it to a session token or another invalidation mechanism.
