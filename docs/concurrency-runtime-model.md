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
It serializes recording control operations; the removed voice-command product
feature does not own this coordinator.

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

An authorized, idle selected mode may prepare the Apple graph on one
`vocal-more-audio-prepare` worker. `vm_audio_prepare` configures VoiceProcessingIO
and conversion buffers without a tap, consumer or running input engine. Capture
starts only after an explicit recording action. Startup waits for an in-flight
prepare inside its existing deadline worker; close rejects late publication.
Configuration mismatches discard the prepared graph. Finished Apple sessions
keep the paused graph for up to 30 minutes, and recorder close releases it.
Studio Display can use the prepared Apple graph; its unprepared route retains
the existing CoreAudio fallback. Older native libraries without the optional
prepare symbol continue using ordinary startup.

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
application shutdown responsive. Admission also waits for all owned stream
release workers to finish. Modes check that barrier before opening ASR, so
repeated hotkeys do not create redundant network sessions. Between native calls,
startup checks its generation again; a constructor returning after timeout cannot
proceed to start the rejected stream or reset PortAudio.

Microphone startup failures remain visible in the capsule. One main-run-loop
timer reads Python worker ownership only and updates the notice when a manual
retry becomes possible. Closing or replacing the notice invalidates the timer.
It never restarts capture automatically after a released hotkey. A permanently
blocked native call still requires restarting the app; in-process Python threads
cannot safely interrupt it. Startup diagnostics reset per admitted/rejected
request and retain a separate worker generation, thread ID and native-call phase
so a blocked worker is not confused with a previous successful recording.

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

### Connection failure and user-controlled retries

An active connection gets one initial attempt and at most five retries, with
interruptible waits of 1, 2, 4, 8 and 16 seconds. The existing connect worker owns
this sequence; no timer thread or extra retry worker is created. Readiness
handshake errors preserve the provider's reason instead of becoming a generic
session timeout. The final failure does not start a hidden batch request.

Immutable connection notices carry the error, retry number and delay. Each
observer captures its mode session token, and the macOS adapter rechecks the
notice identity on the main thread. The capsule keeps an interactive error
surface through processing and idle transitions. Only explicit dismissal or a
new recording clears a terminal failure. A successful connection restores the
underlying recording or processing UI.

Finishing an utterance waits for the retry sequence, with a 120-second upper
bound on the finish wait. Cancellation invalidates the ASR generation and wakes
that wait before cleanup; it cannot produce a fallback request or paste a late
result. Exhaustion asks the serial command coordinator to cancel capture while
leaving the final error visible. Idle-mode selection cannot restart background
prewarming after cancellation or exhaustion; the next explicit dictation clears
that suspension. Canceling a blocked SDK call still relies on
late-candidate rejection; it does not forcibly terminate the foreign call.

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

- settings model-access-check executor
- settings recording-maintenance executor
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


## Rust 完整后端与薄 UI 壳（2026-09-11）

`--backend rust` 使用独立 `vocal-more-backend` 进程。前文 Python 模式运行时仍作为 `--backend python` 的回退实现保留。Rust 路径不实例化 Python mode、AudioRecorder、ASR SDK、词典学习服务或配置仓储。

Rust 应用 actor 是配置和会话的单一命令入口，Tokio 固定两个工作线程。网络、历史重试、压缩和学习工作在可取消的异步任务中运行；普通音频源线程独占 C ABI handle，回调继续只写预分配的原生环形缓冲区。慢启动、停止超时和回调清理失败均保留原生资源隔离边界。采集诊断也只在 handle 所在线程读取，再将值快照交给界面。

2026-09-12 起，原生输入由一个普通 owner 线程串行处理准备、采集、暂停、恢复和销毁。空闲准备只构建停止状态的图；录音结束排空尾帧后最多保留 30 分钟。线程只持有符号与接收端，不持有命令发送端，因此最后一个 `NativeAudio` 所有者释放后会唤醒空闲线程并清理图；正在阻塞的原生调用晚返回后检查关闭状态。最多排队一个准备请求和一个已获本地准入的录音请求，取消/超时不会跨线程销毁 handle。暂停失败或无法证明回调停止时禁止复用，并保持隔离状态。麦克风预览的临时 DSP 更新使该图不再适合下一次复用。

采集不再等待云端握手。会话先建立有界发送队列并分配录音 ID，再并行启动音频与网络，连接完成前的首段 PCM 通过队列保留。录音状态以第一块 PCM 为准；诊断保留音频源就绪、首块 PCM、ASR 就绪三个独立时点。状态通知读取设备缓存，只有启动初始化或显式刷新更新菜单设备列表；音频 owner 在准备/恢复边界重新读取设备列表，防止系统默认输入切换后继续复用旧图。

薄 UI 壳有三个有界 RPC I/O 通道对应线程、主线程事件排空，以及一个有界系统操作执行通道。全局快捷键只发送原始按下/松开事件；手势判定在 Rust。粘贴采用一次性令牌，执行前再次验证取消状态；Accessibility 读取在系统操作通道中，候选筛选、截止时间和学习持久化在 Rust。麦克风测试音量传递 RMS，胶囊波形则传递 Rust 已校准的显示值。

完整协议、队列边界与复现命令见 [Rust 后端集成](rust-backend-integration.md)。

### 2026-09-12 性能优化后的提交与 UI 调度

开始会话时先保留录音 ID，第一块 PCM 到达后才创建归档文件。RMS、首帧计时与 `recording` 状态表示已经观察到输入；`pcm_bytes` 仍在写入归档缓冲区成功后更新。初次文件创建期间，输入继续由原有有界队列保留。公开的 `RecordingStore::create` 仍会等待初始元数据持久化；只有会话内部使用无 I/O 的 `reserve`。

停止时先向网络队列提交 FIFO 结束标记，然后封存 WAV，利用识别服务返回结果的等待时间完成音频刷盘。取消、网络故障与进程中断仍保留已接受音频的恢复边界。paced WAV 测试源的块间等待可由 stop/cancel 立即唤醒；这不改变原生 C ABI 的读取超时，不能据此声称真实麦克风停止快了一个完整音频块。

`Status.recording` 是不参与 RPC 序列化的 `Arc<Recording>`，避免终态再次扫描录音目录。`Committing` 阶段中的记录只允许准备工作：公开的终态文字仍需等核心提交成功。对于已有完整实时文字、无需再次请求润色的路径，应用预先写入并刷盘一个不可见的历史临时文件；核心提交成功后才原子替换历史索引、同步目录并发布结果。核心提交失败或取消会丢弃准备文件。若归档、重试、删除或 pin 集合变化使准备快照过期，则按最新状态重新生成索引。启动时清理遗留准备文件，绝不将其解释为成功历史。

新增历史与超过 30 条时的淘汰共用一次索引事务。先持久化删除标记，再删除音频和核心元数据；清理后的标记可以等下一次索引事务再移除。若中途崩溃，旧标记会再次驱动清理，因此不需要为了清空标记立即再刷一次盘。未完成或正在重试的记录不参与淘汰。

自动归档由应用 actor 调度，启动及回到空闲状态时触发；保留最近 3 条 WAV。开始扫描前等待 250 ms，若此时已有新会话，推迟到下一次空闲，避免连续输入与归档争抢资源。任务进行中出现新的请求，只设置一次后续扫描标记。解码前后的 PCM 校验在 `spawn_blocking` 中按 64 KiB 读取并检查取消；转换和校验成功、文件确实变小后才替换原始 WAV。关闭应用会取消并等待所属任务。系统转换器不能正确处理的短录音继续保留 WAV。

薄 UI 壳改为事件到达时用 `NSOperationQueue.mainQueue()` 唤醒主线程；一次最多处理 128 条，多余事件继续排队并再次唤醒。突发消息合并唤醒，关闭后的回调不再触碰 UI。两秒定时器只负责快捷键/权限恢复与队列兜底，不再承担 40 ms 周期的业务事件投递。JSON-RPC 输出线程复用字节缓冲区，每条消息一次写入标准输出。薄 UI 输入端使用 64 KiB `BufferedReader` 读取标准输出，保留 40 MiB 单条消息上限；标准错误使用 8 KiB 缓冲区。标准输入继续无缓冲写入并处理部分写入，避免请求滞留。

可选环境变量 `VOCAL_MORE_TRACE_TIMINGS=1` 将阶段耗时写入 stderr；默认关闭，不记录音频、文本、路径或凭据。测试边界与数据见 [性能优化记录](rust-performance-optimization-2026-09-12.md)。
