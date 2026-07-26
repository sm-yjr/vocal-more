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
- return immediately once recording has stopped, instead of doing late post-stop work

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

### 8. Inbound realtime event worker owns callback-local ASR consequences

DashScope realtime callbacks may arrive on SDK-managed threads. Those threads now only:

- parse raw SDK payloads into inbound callback events
- enqueue them onto one long-lived inbound event worker

`StreamingASRCallback` now has one inbound worker that owns:

- callback-local aggregation state
- `wait_for_*` completion events used by `ASREngine.stop()`
- upward partial/final/error callback delivery
- response/transcript completion bookkeeping

This is a meaningful tightening over the earlier design because SDK callback threads no longer directly mutate callback state or emit business callbacks.

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
- recording-store archive executor

The recording-store archive executor is a single owned worker. It converts
older terminal WAV files to lossless FLAC outside the dictation finish path,
keeps the three newest recordings uncompressed, and closes explicitly with the
recording store during app or RPC shutdown.

They are bounded and have explicit `close()` paths.

### 11. Dictionary edit observer owns the post-paste window

Automatic dictionary learning has one single-worker observer executor. It:

- captures the exact focused Accessibility element before paste
- polls only that same element for at most 15 seconds
- cancels the previous observation when a new paste starts
- writes qualifying evidence to SQLite

It never calls DashScope and never blocks a mode-local finish executor.

### 12. Dictionary learning queue owns deferred classification

A separate single-worker queue drains persisted learning jobs. It starts lazily
only when automatic learning is enabled and a user API key exists. It owns:

- `qwen3.7-plus` JSON-mode calls
- exponential retry scheduling
- validated dictionary mutations
- review, reject, and undo transitions

It does not use the DashScope Batch API. UI changes are emitted as intents and
marshaled to the main thread.

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

### 1. Inbound ASR events still stop at the callback-local worker

The inbound worker is now the owner for callback-local consequences, which is a large improvement. It is still separate from `DictationCommandCoordinator`, so inbound ASR events do not yet share the same serial owner as hotkey control commands.

### 2. Mode lifecycle is now explicit, but engine lifecycle is still partly implicit

Modes now expose the explicit lifecycle states:

- `IDLE`
- `STARTING`
- `RECORDING`
- `STOPPING`
- `PROCESSING`
- `CANCELLING`
- `FAILED`

The remaining implicit pieces are mostly inside engine-local flags such as session readiness, connection failure, warm-session reuse, and fallback state.

### 3. Audio callback still performs some compute-heavy work

The callback no longer sends on the network, which was the biggest risk. It still performs DSP, RMS calculation, and PCM conversion inline.

### 4. Queue policy is now adaptive, but still empirical

Outbound realtime queue sizing and drain timeout now scale with chunk duration, and diagnostics log queue depth and fallback causes. The exact thresholds are still empirical tuning values rather than the result of production telemetry.

## Practical Guidance For Future Changes

- If a new feature needs to start or stop dictation, wire it through `DictationCommandCoordinator`.
- If a background task needs to update the UI, emit an intent and marshal it onto the main thread.
- If a task is best-effort and non-realtime, prefer `BackgroundExecutor` over a raw `threading.Thread(...)`.
- If a new realtime path touches audio, preserve the rule that the PortAudio callback never blocks on network operations.
- If a late callback could affect user-visible state, tie it to a session token or another invalidation mechanism.
- If a new realtime SDK event path is added, keep SDK-managed threads as thin event producers and route consequences through one owned worker.
- If automatic dictionary learning changes, keep edit observation and model
  classification on their separate owners, and preserve both explicit
  shutdown paths.
