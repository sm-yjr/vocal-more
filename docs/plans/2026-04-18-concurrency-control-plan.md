# Concurrency Control Refactor Plan

**Goal:** Refactor the Python app to use a small, explicit set of concurrency domains with single-owner state transitions, so hotkeys, audio capture, ASR, and UI updates remain deterministic and safe under rapid user input and unstable network conditions.

**Architecture Decision:** Use threads plus queues, not `asyncio`. This app already depends on AppKit/WebKit main-run-loop behavior, Quartz event taps, PortAudio callbacks, and a callback-based DashScope realtime SDK. A thread-and-queue model fits the current stack better and minimizes integration risk.

**Tech Stack:** Python, AppKit/WebKit, Quartz, sounddevice/PortAudio, DashScope realtime SDK, pytest

---

## Selected Concurrency Model

### 1. Main thread owns all UI

Only the main thread may touch:

- menu bar state
- `NSPanel`
- `WKWebView`
- notifications
- settings window UI state

Worker code must emit UI intents, not directly call AppKit/WebKit. The current `run_on_main_thread` adapters in the menu app and floating capsule are the right direction and should become the standard pattern for every UI surface.

### 2. One serial coordinator owns dictation lifecycle

Introduce a long-lived `DictationCoordinator` backed by:

- `queue.Queue`
- one dedicated worker thread

This coordinator becomes the only owner of mutable dictation session state:

- current lifecycle state
- active mode/session identity
- pending stop/cancel intent
- streaming degradation/fallback status
- ASR connection ownership
- warm-session reuse ownership

No other thread may directly transition dictation state.

### 3. Audio callback thread only captures audio

The PortAudio callback must stay lightweight. It may:

- apply cheap DSP already needed for the low-voice pipeline
- append PCM to the in-memory recording buffer
- compute/store level data
- push PCM chunks into a bounded queue

It must not:

- base64-encode audio
- call network send APIs
- mutate ASR session state
- perform blocking waits
- perform UI work

### 4. Streaming send path runs off the audio thread

Introduce an outbound audio sender worker for realtime ASR. Its job is:

- read PCM chunks from the bounded queue
- encode them
- send them to the active realtime session

This keeps the audio callback independent from network latency and SDK behavior.

### 5. SDK/network callbacks become events, not state owners

DashScope realtime callbacks may arrive on SDK-managed threads. Those callbacks should:

- parse the event
- wrap it as a coordinator event
- enqueue it to the `DictationCoordinator`

They must not directly mutate lifecycle state shared with hotkey, mode, or UI code.

### 6. Background support work uses a small shared task runner

Non-realtime background jobs such as:

- retry transcription
- mic test auto-stop follow-up work
- recording-history maintenance tasks

should use a shared `BackgroundTaskRunner`, implemented with a small bounded `ThreadPoolExecutor`.

Chosen configuration:

- `max_workers=2`
- only for non-realtime, best-effort tasks
- no lifecycle ownership

This avoids a growing number of ad-hoc `threading.Thread(...)` call sites.

---

## Required Ownership Rules

### Rule 1: Single owner for mutable state

If a piece of state affects dictation lifecycle, one component must own it. Other threads communicate with the owner using queues/events.

Examples:

- `DictationCoordinator` owns dictation lifecycle state
- `RecordingStore` owns recording metadata behind its lock
- UI components own view state on the main thread only

### Rule 2: Cross-thread communication happens through queues or thread-safe adapters

Allowed patterns:

- `queue.Queue`
- bounded producer/consumer queues
- `threading.Event` only for local coordination inside one owner component
- main-thread marshaling adapters for UI

Disallowed pattern:

- multiple threads directly reading/writing the same lifecycle flags as an informal protocol

### Rule 3: Timers may enqueue commands, not mutate shared state directly

This is important for:

- warm-session TTL close
- mic test auto-stop
- delayed cleanup

If a timer fires on another thread, it should enqueue a command/event back to the owning coordinator instead of closing sessions or changing lifecycle state by itself.

### Rule 4: Invalid commands are coalesced or rejected deterministically

The coordinator should accept commands according to a strict state machine.

Chosen behavior:

- `START` is valid only in `IDLE`
- repeated `START` outside `IDLE` is ignored
- `STOP` is valid in `STARTING` and `RECORDING`
- `STOP` during `STARTING` becomes a pending stop intent and executes as soon as startup reaches a safe boundary
- `CANCEL` is valid in every non-`IDLE` state and has priority over late ASR success events
- late events from an old session are dropped if their session token does not match the current session

---

## Chosen State Machine

Use one explicit dictation lifecycle enum:

- `IDLE`
- `STARTING`
- `RECORDING`
- `STOPPING`
- `PROCESSING`
- `CANCELLING`
- `FAILED`

Notes:

- `STARTING` covers hotkey accepted, recorder opening, and ASR realtime session setup.
- `RECORDING` means audio capture is active.
- `STOPPING` means capture has ended and we are committing/finalizing transcription.
- `PROCESSING` means transcription normalization, polish, persistence updates, and paste behavior are in flight.
- `CANCELLING` is explicit so late callbacks cannot accidentally revive a session.

Mode differences such as walkie-talkie versus realtime-long should become policy decisions above the same shared lifecycle engine, not separate ad-hoc concurrency behavior.

---

## Chosen Backpressure Strategy

For realtime streaming audio, do **not** block the audio callback and do **not** silently drop chunks.

Chosen strategy:

1. Use a bounded queue between audio callback and ASR sender.
2. If the queue fills, mark the realtime path as degraded for the current session.
3. Continue retaining the full PCM recording locally.
4. At stop/finalize time, fall back to batch transcription for correctness.

Why this choice:

- blocking the audio callback risks glitches and recorder instability
- dropping chunks harms transcript correctness in unpredictable ways
- batch fallback preserves correctness because the full PCM still exists

This is the best tradeoff for a dictation product where correctness matters more than preserving a degraded streaming illusion.

---

## Concrete Refactor Targets

### Hotkey path

Replace `HotkeyManager._safe_callback()` thread-per-event behavior with:

- event tap thread receives key event
- event tap thread enqueues a hotkey command
- coordinator serially processes the command

This preserves order for rapid press/release sequences and removes a major source of race conditions.

### Audio path

Refactor `AudioRecorder` so:

- the audio callback writes PCM to the local recording buffer
- the audio callback pushes chunks into a bounded realtime queue
- the coordinator/sender worker owns the realtime streaming send lifecycle

### ASR path

Refactor `ASREngine` around:

- one owner for session state and connect/commit/close
- one outbound send worker
- SDK callbacks that enqueue inbound events
- explicit session tokens so stale callbacks are ignored

### Mode path

Keep `WalkieTalkieMode` and `RealtimeLongMode` as user-interaction policies, but move lifecycle ownership out of them. They should become thin translators from input policy to coordinator commands.

### UI path

Keep the current main-thread adapters and make them universal. Worker code should emit:

- state change intent
- partial transcript intent
- processing stage intent
- error intent

The UI adapter then applies those intents on the main thread.

---

## Migration Order

### Phase 1: Stabilize thread boundaries

- finish main-thread-only UI rule everywhere
- remove thread-per-hotkey callback spawning
- introduce a single serial command queue for dictation control

### Phase 2: Decouple audio from network

- add bounded audio chunk queue
- move encoding/send out of the audio callback path
- add degradation and batch-fallback behavior

### Phase 3: Centralize ASR session ownership

- move realtime session transitions behind coordinator ownership
- convert SDK callbacks into coordinator events
- ignore stale session events by token

### Phase 4: Normalize background helpers

- replace ad-hoc helper threads with `BackgroundTaskRunner`
- convert timers to enqueue commands rather than mutate shared state directly

### Phase 5: Lock in behavior with tests

- rapid press/release ordering
- cancel during startup
- stop during startup
- late ASR callback after cancel
- queue overflow triggers fallback
- UI updates only via main-thread adapter

---

## Non-Negotiable Best-Practice Decisions

These are the design choices to follow for this refactor:

- Use `queue.Queue` plus one long-lived coordinator thread for lifecycle control.
- Use a small shared executor only for non-realtime support tasks.
- Keep all UI on the main thread.
- Keep audio callback work minimal and non-blocking.
- Treat SDK callbacks as event sources, not lifecycle owners.
- Use explicit state machines and session tokens.
- Prefer correctness-preserving fallback over silent audio loss.

