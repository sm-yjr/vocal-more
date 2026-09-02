# Concurrency Runtime Model

This document describes the current concurrency boundaries in the active Python app under `src/vocal_more/`.

The goal is not to eliminate every background thread. The goal is to make thread ownership explicit so UI work, dictation control, audio capture, and ASR/network work cannot accidentally fight each other.

## Current Domains

### 1. Main thread owns UI

Only the main thread may touch AppKit/WebKit UI state:

- menu bar state
- notifications
- floating capsule `NSPanel` and its native `NSView`/Core Animation tree
- settings window `NSWindow` / `WKWebView`

Use the existing marshaling helpers when background code needs a UI update:

- `VocalMoreApp._run_on_main_thread()`
- `FloatingCapsule._run_on_main_thread()`
- `SettingsWindow._eval_js()` plus the main-thread JS drain timer

`FloatingCapsule` is the sole owner of capsule state. Its
`NativeCapsuleRenderer` owns only the AppKit view tree, target/action callbacks,
waveform layers and progress layer. Audio workers publish one calibrated value;
the main-run-loop timer coalesces it before updating the renderer. The native
capsule does not create WebKit helper processes.

Background code should emit UI intents, not call AppKit/WebKit directly.

### 2. Hotkey event thread owns Quartz event capture

`HotkeyManager` runs the Quartz event tap on its own listener thread. That thread only:

- receives system keyboard events
- converts them into `HotkeyEvent`
- enqueues those events onto the callback worker

It does not run dictation business logic directly.

Because this listener is created by Python rather than AppKit, each Quartz
callback is wrapped in an explicit Objective-C autorelease pool. Temporary
PyObjC/CoreFoundation bridge objects are therefore reclaimed at the event
boundary instead of accumulating for the lifetime of the listener thread.

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

Live configuration does not inspect those mode implementations. `RuntimeFacade`
computes policy from changed config keys and calls `ModeRuntimeService`, which
uses the public mode runtime port to switch an idle mode, apply audio settings,
or refresh an already-initialized ASR engine. Recorder and ASR fields remain
owned by their mode.

### 5. Audio stream startup worker contains CoreAudio/PortAudio stalls

`AudioRecorder.start()` opens the native input stream on one isolated daemon
thread and waits at most 3 seconds. This boundary exists because CoreAudio
and PortAudio can block indefinitely after sleep/wake, a default-device change,
or a Bluetooth route transition.

Microphone privacy admission precedes that worker. When an explicit recording
action first observes TCC `not_determined`, it starts the asynchronous
`AVCaptureDevice.requestAccess` request, returns a recoverable “grant access and
try again” result, and lets the mode leave `STARTING`. It does not enumerate
devices, consume the 3-second deadline, wait for the completion handler, or
automatically replay the released hotkey action. A later explicit action must
observe `authorized` before device startup is admitted. Static capability
probing never requests access.

The startup path guarantees:

- the dictation command coordinator regains control after the hard deadline
- only one native startup attempt may be in flight for a recorder
- every attempt has a generation token
- a stream returned by a timed-out attempt is never published as active
- Apple and PortAudio callbacks stay behind a bounded provisional gate until
  that exact candidate is accepted; failed or retried candidates publish no PCM
- unpublished or failed streams are released on the existing daemon release path
- mode cancellation treats `STARTING` as active work, invalidates the mode
  session, and rechecks that token after both microphone and ASR startup

Python cannot forcibly terminate a thread blocked inside a native audio call.
The containment policy therefore abandons that daemon attempt, rejects repeated
starts while it remains blocked, and keeps hotkey cancellation, UI work, and
application shutdown responsive.

### 6. Native audio capture has two explicit queue boundaries

The preferred macOS path no longer calls Python from AVAudioEngine's realtime
tap. The Objective-C++ runtime owns these stages:

1. The VoiceProcessingIO tap copies mono Float32 frames into a preallocated raw
   SPSC queue. A full queue drops the block and increments a counter; the tap
   never waits.
2. One native worker owns AVAudioConverter, the stateful high-pass filter, vDSP
   level/gain/clipping operations, PCM16 conversion, and a second bounded SPSC
   queue.
3. One ordinary Python consumer thread reads complete 16 kHz mono PCM16 blocks,
   appends them to the recorder buffer, and invokes `on_audio_chunk` and
   `on_audio_level`.

The realtime tap does not allocate, log, perform file or network I/O, acquire
the GIL, or invoke application callbacks. The native worker polls the raw queue
with a 1 ms backoff when it is empty; neither producer may block on a full
queue. Queue drops and runtime faults are surfaced through the recorder's
active and last-session diagnostics.

The PyObjC Voice Processing and PortAudio implementations remain compatibility
fallbacks. Their Python callback path still follows the older bounded contract:
downmix if needed, apply the low-voice DSP, compute RMS and PCM16, append the
buffer, notify observers, then return. Observer exceptions are contained and
counted as recorder faults so PortAudio cannot silently truncate ASR while the
session claims success. No capture path performs realtime network sends directly.

One capture session owns an immutable plan: fixed application sample rate,
block size, capture channels, device, AGC mode, gain, high-pass filter and
limiter. UI, menu or RPC updates received during `STARTING`/`RECORDING` remain
pending and are applied atomically at the next `start()` boundary.
Completed-session diagnostics thus
describe one stable plan instead of a mid-utterance mixture.

The settings-window microphone test uses the same atomic session-plan entry
point. Its controller binds each auto-stop timer to a session generation and
claims the recorder and timer under one lifecycle lock before calling the
potentially blocking native `stop()` outside that lock. A manual stop racing
the five-second timer therefore drains the native stream exactly once, while a
late timer from a previous test cannot stop a newly started recorder. A stop
during startup invalidates the unpublished generation; the late recorder is
closed without publishing `on_started`, PCM, or completion for the canceled
test.

The source sample rate is a device/route fact and can vary (48 kHz is common on
built-in microphones). On the native Apple path, `AVAudioConverter` owns
source-rate conversion. Every capture adapter must emit the fixed 16 kHz, mono,
signed PCM16 application contract. The legacy `audio.sample_rate` key is
normalized to 16 kHz and must not be used to describe an end-to-end
variable-rate session.

`AudioRecorder` construction is deliberately I/O-free. Its first status is an
explicit `pending` placeholder; Core Audio device enumeration, selector probes
and stream construction run either during an explicit idle inspection or on
the bounded startup worker. A wedged `query_devices()` therefore cannot prevent
mode dependency construction, and the 3 s command-facing start deadline also
covers route discovery rather than starting only after discovery returns.

Stopping detaches the active PortAudio stream and snapshots the completed PCM
buffer synchronously. Stream abort/close then runs on a daemon release worker.
This keeps a CoreAudio device-transition stall from blocking the dictation
command coordinator in `STOPPING`; callbacks that finish after detachment drop
their chunk instead of forwarding late audio.

For a built-in microphone that is also the system-default input, the recorder
first attempts the bundled Objective-C++ AVAudioEngine runtime, then the PyObjC
Voice Processing adapter. Apple's I/O unit subtracts audio playing from the
current output device from the microphone uplink, while AVAudioConverter
band-limits and converts the hardware-rate tap into fixed 16 kHz mono blocks.
Studio Display is routed through the lower-latency CoreAudio compatibility path:
measured VoiceProcessingIO startup on that external-display route exceeds one
second and loses the beginning of dictation. The app does not keep a microphone
engine running while idle merely to hide that cost.
In automatic gain mode, verified Apple AGC owns level control and Vocal More
bypasses software gain and limiting. DSP ownership follows the post-start
VP/AGC getter snapshots rather than the aggregate quality flag: a drop can make
a session unverified, but it must not cause software gain to stack on top of
Apple AGC. In manual mode, Apple AGC is verified off and the low-voice
gain/limiter remains active. Verification is repeated after the engine starts.
If Voice Processing or AGC verification cannot start, automatic mode reports
the structured fallback and continues through the saved software gain path
rather than failing dictation.

`AudioRecorder.stop()` asks the active adapter to drain AVAudioConverter EOS and
its final partial PCM block before snapshotting the completed session. Native
drain has a 500 ms command-thread deadline. If CoreAudio exceeds it, the
recorder returns the PCM already available, marks the completed session with
`native_drain_timeout`, and lets daemon cleanup wait for the native call rather
than destroying a handle still in use. The recorder publishes separate planned,
active, and last-session state, so stopping does not rewrite a verified result
into an unverified idle claim.

Without Apple voice processing, MacBook, iMac, Studio Display, and devices
explicitly named as built-in microphone endpoints may expose up to three input
channels. Mac mini, Mac Studio, and Mac Pro model names are not treated as
built-in microphone evidence because those products do not provide one.
Safe default `capture_channels=1` keeps the system-provided mono route. Only an
explicit value greater than one opts into the experimental logical-channel mix:
it rejects uncorrelated signals, aligns inverted polarity, and emits the same
mono 16 kHz PCM contract expected by ASR. The public API does not establish that
logical channels map one-to-one to physical capsules, and no quality gain is
claimed before hardware A/B. External USB devices retain their configured
channel count and are still normalized to mono before ASR delivery.

### 7. ASR sender thread owns outbound realtime audio sends

`ASREngine` now has one long-lived sender thread and one bounded outbound queue.

Streaming modes call ASR admission before `AudioRecorder.start()`. Admission
sets the current generation and opens the bounded queue before a recorder's
provisional startup gate can publish its first verified PCM block. The mode
still snapshots one audio plan and supplies that same snapshot to recorder and
ASR, so ordering does not create two configuration epochs.

The flow is:

1. audio callback calls `ASREngine.send_audio(chunk)`
2. `send_audio()` enqueues `(session_generation, raw_pcm)` into a bounded queue
3. sender thread rejects stale generations, then waits for that session's
   readiness
4. sender thread rechecks generation/ownership before base64 encoding and
   `conversation.append_audio(...)`

If the queue fills or sender drain fails, the realtime path is marked degraded and finalize-time logic falls back to batch transcription using the full PCM recording.

### 8. ASR connect thread owns realtime session startup

`ASREngine.start()` still launches a background connect thread to:

- create a realtime conversation or claim an unused preconnected conversation
- update the session
- wait for `session.updated`
- mark the session ready

This startup path is still separate from the sender thread, but session readiness and failure flags are protected by the engine lock.

At `ASREngine.start()`, the engine deep-copies the audio/session config. The
connect thread, provider negotiation, queue timing, PCM duration, debug WAV and
batch fallback all use that same snapshot. If UI or RPC changes block size or
another session setting during an utterance, the current PCM remains 16 kHz
mono PCM16 and uses its original snapshot; the new setting begins only at the
next start boundary. A legacy request to change `audio.sample_rate` is simply
normalized back to the fixed 16 kHz contract.

Every connect candidate and callback owner is bound to the session generation.
If microphone permission or stream startup fails after ASR admission, the mode
calls the bounded `abort_startup()` path: it invalidates the generation, stops
accepting audio, clears queued chunks, detaches the current conversation and
returns without waiting for an uncooperative SDK connect. A late candidate must
close itself instead of publishing. An old socket callback cannot mutate the
new trace/result, and a sender that dequeued old PCM before abort must reject it
at its generation checks rather than append it to session 2.

Starting a new dictation retires warm-keeper ownership by setting its stop event
and advancing the warm generation, but does not join that thread on the hot
path. A keeper blocked in SDK reconnect therefore cannot add the former 250 ms
shutdown budget to microphone startup. Its late candidate is still rejected by
the same stop-event and generation checks. Explicit refresh, abort, and shutdown
paths retain the bounded join for resource cleanup.

After post-launch dependency checks, the selected mode actively creates its
lazy ASR engine on the startup worker and asks its warm-keeper thread to
establish a clean realtime conversation before the first dictation. A mode or
ASR configuration change drops incompatible idle state and prewarms only the
currently selected mode, avoiding one persistent socket per previously used
mode.

After a dictation finishes, the conversation that received its audio is closed
and its callback worker is released. The warm keeper establishes a new
conversation with no committed items and retains that clean connection until
the next dictation, runtime refresh, or shutdown. It monitors the socket and
reconnects after an idle disconnect. The engine marks a conversation consumed
as soon as audio is appended, so exception paths cannot accidentally reuse a
conversation that contains audio or history.

Realtime conversation close is bounded on the caller and may finish on a daemon
closer when the SDK or network stack stalls. Application quit also bounds its
wait for the command coordinator, so a wedged device or connection cannot hold
the menu-bar process open indefinitely.

The engine tracks both connect and close workers. Shutdown invalidates the
session generation, joins those workers within a fixed budget, closes the
underlying socket if an SDK receive thread does not exit promptly, and clears
the provider callback/WebSocket ownership chain after that receive thread has
stopped. This keeps late startup publication impossible while allowing socket
and callback objects to be reclaimed deterministically.

### 9. Inbound realtime event worker owns callback-local ASR consequences

DashScope realtime callbacks may arrive on SDK-managed threads. Those threads now only:

- parse raw SDK payloads into inbound callback events
- enqueue them onto one long-lived inbound event worker

`StreamingASRCallback` now has one inbound worker that owns:

- callback-local aggregation state
- `wait_for_*` completion events used by `ASREngine.stop()`
- upward partial/final/error callback delivery
- response/transcript completion bookkeeping

This is a meaningful tightening over the earlier design because SDK callback threads no longer directly mutate callback state or emit business callbacks.

### 10. Mode-local processing executor owns finish workflows

Each mode has its own single-worker `BackgroundExecutor` for finish-time work:

- `WalkieTalkieMode`
- `RealtimeLongMode`

That executor runs:

- stop/finalize transcription
- recording persistence updates
- optional polish
- optional paste

Each run is tagged with a session token so `cancel()` can invalidate late results.

### 11. Recording retry lane owns retry transcription

Saved-recording retry has one dedicated daemon worker with bounded admission.
`RecordingRetryRuntime` owns:

- a hard limit on running plus queued retry jobs
- duplicate suppression by recording ID
- generation invalidation during shutdown
- short-lived commit and callback leases outside the runtime state lock
- retry lifecycle events consumed by either the settings or RPC adapter

`RecordingRetryService` contains the synchronous data/service workflow. It
reads PCM and language through the recording repository port, calls one batch
transcriber, and commits the result only while the runtime generation remains
active. Repository updates return whether the recording still exists, so a
delete that wins the race suppresses a false `completed` event. Settings and
RPC translate events; they do not create an ASR engine or mutate retry state
themselves.

`close(timeout=...)` first invalidates admissions and commits, then performs a
bounded drain. It returns a report instead of pretending shutdown completed.
If the worker is still inside a provider call or storage commit, the owner
leaves the recording repository open rather than closing data underneath that
worker. A later close can retry the drain.

### 12. Dedicated executors own remaining best-effort background jobs

These non-realtime workloads do not share a generic adapter pool:

- RPC meeting-notes executor
- settings model-access-check executor
- settings recording-maintenance executor
- settings meeting-notes executor
- recording-store archive executor

The recording-store archive executor is a single owned worker. It converts
older terminal WAV files to lossless FLAC outside the dictation finish path,
keeps the three newest recordings uncompressed, and closes explicitly with the
recording store during app or RPC shutdown.

Each executor has one worker and an explicit `close()` path. Their worker counts
are bounded; unlike the retry runtime, these low-frequency best-effort helpers
do not yet impose a hard running-plus-queued admission limit.

### 13. Dictionary edit observer owns the post-paste window

Automatic dictionary learning has one single-worker observer executor. It:

- captures the exact focused Accessibility element before paste
- polls only that same element for at most 15 seconds
- cancels the previous observation when a new paste starts
- writes qualifying evidence to SQLite

It never calls DashScope and never blocks a mode-local finish executor.

### 14. Dictionary learning queue owns deferred classification

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

### Rule 6: Data repositories do not own provider retry work

Recording repositories expose synchronous, locked data operations. Network
retry policy, admission, deduplication, cancellation, and event delivery belong
to an application service and its workload-specific runtime lane.

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

### 3. Compatibility audio callbacks still perform bounded DSP work

The preferred Objective-C++ path moves conversion and DSP to its native worker;
its AVAudioEngine tap only copies into a preallocated queue. The PyObjC and
PortAudio compatibility callbacks still perform DSP, RMS calculation, and PCM
conversion inline. When a built-in Mac array exposes multiple channels, the
fallback path may also compute a small channel-correlation matrix. Capture is
capped at three channels so this work remains bounded; production telemetry
should still validate callback headroom on hardware where the native path is
unavailable.

### 4. Queue policy is now adaptive, but still empirical

Outbound realtime queue sizing and drain timeout now scale with chunk duration, and diagnostics log queue depth and fallback causes. The exact thresholds are still empirical tuning values rather than the result of production telemetry.

### 5. In-flight provider requests do not yet support cooperative cancellation

Retry shutdown is bounded and reports an undrained worker, but the batch ASR
provider API is still a synchronous call without a cancellation token. Shutdown
can invalidate its eventual commit and keep the repository alive safely; it
cannot interrupt the network request itself. Provider request timeouts or a
cooperative cancellation port are the next step if shutdown latency becomes an
observed issue.

## Practical Guidance For Future Changes

- If a new feature needs to start or stop dictation, wire it through `DictationCommandCoordinator`.
- If a background task needs to update the UI, emit an intent and marshal it onto the main thread.
- If a task is best-effort and non-realtime, prefer `BackgroundExecutor` over a raw `threading.Thread(...)`.
- If a new realtime path touches audio, preserve the rule that the PortAudio callback never blocks on network operations.
- If native microphone startup changes, preserve the hard deadline, the
  single-in-flight circuit breaker, and generation-based rejection of late streams.
- Keep Apple voice-processing callbacks capture-only. Format conversion may run
  there, but network work and UI updates must continue through their existing
  queues and main-thread marshaling paths.
- If a late callback could affect user-visible state, tie it to a session token or another invalidation mechanism.
- If a new realtime SDK event path is added, keep SDK-managed threads as thin event producers and route consequences through one owned worker.
- If automatic dictionary learning changes, keep edit observation and model
  classification on their separate owners, and preserve both explicit
  shutdown paths.
