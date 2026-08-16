# Native macOS audio runtime

This document describes the production boundary between Vocal More's Python
application and its Objective-C++ capture library. The target is macOS 14 or
newer on arm64, with a fixed application output contract of 16 kHz, mono,
signed PCM16.

## What problem this solves

AVAudioEngine invokes its input tap on a realtime audio thread. Calling Python,
allocating NumPy arrays, logging, or waiting on a lock from that tap can produce
dropouts when the interpreter, UI, or network worker pauses. The native runtime
keeps the realtime boundary small while preserving the Python application and
its existing ASR interfaces.

## Runtime ownership

```text
AVAudioEngine VoiceProcessingIO
  └─ realtime tap: memcpy → raw SPSC
       └─ native worker: AVAudioConverter → HPF/vDSP → PCM SPSC
            └─ Python consumer: recording buffer → ASR queue/UI level
```

All queue slots, converter buffers, and DSP work buffers are allocated before
the engine starts. Each queue has one producer and one consumer. A producer
drops a block when its queue is full and increments `dropped_blocks`; it never
waits for the consumer.

The first queue contains source-rate mono Float32 blocks. That rate is
negotiated with the current device and may change between routes; 48 kHz is a
common built-in-microphone source format. The worker converts it with
`AVAudioConverter` at high quality, applies the stateful high-pass filter, and
uses Accelerate/vDSP for RMS, gain, clipping, and PCM conversion. The second
queue always contains 16 kHz mono signed PCM16 blocks. Only the Python consumer
invokes application callbacks.

The legacy `audio.sample_rate` config key is accepted and normalized to 16 kHz.
It does not select the device source rate or create a variable-rate
application/ASR contract. Source and output rates remain separate diagnostics.

## AGC state machine

`automatic_gain=true` means Apple Voice Processing AGC must be enabled and
verified both before and after engine start. The adapter commits Apple to DSP
ownership—and bypasses software gain and the limiter—only after the post-start
Voice Processing and AGC snapshots are both true. `automatic_gain=false` means
Apple AGC must be verified off; the native worker applies the configured gain
and optional soft limiter.

DSP ownership and session quality are separate. Voice Processing and AGC are
verified snapshots taken immediately after the engine starts; the ABI does not
continuously poll either Audio Unit property. If both start snapshots are
`true`, software gain stays
bypassed even when a later drop or runtime fault makes the session unverified.
Re-enabling software gain because an unrelated health check failed would stack
two gain controllers and can clip the signal.

Runtime status therefore reports two separate facts. `start_verified` says the
post-start Voice Processing/AGC checks passed. `diagnostics_fresh` says the most
recent native counter and microphone-mode read succeeded; it does not make the
start snapshot a live getter. A failed diagnostics read is retained as a
monotonic runtime fault and returns Voice Processing/AGC as unverified for that
read rather than reusing a stale healthy cache.

Any API absence, setter/getter mismatch, invalid source format, converter
failure, or engine startup failure makes this adapter unavailable. The Python
factory then tries the PyObjC Voice Processing adapter and finally PortAudio.
The saved user mode is not changed by a runtime fallback.

Studio Display intentionally uses the PortAudio/CoreAudio compatibility path
even when the native library is available. Hardware measurements showed about
1.3 seconds to start VoiceProcessingIO on this route, compared with roughly
0.58 seconds to receive the first compatibility-path frame. Keeping an input
engine running while idle would hide latency by continuously occupying the
microphone, so the lower-latency route is selected instead.

## C ABI rules

The public header is `native/audio/include/vocal_more_audio.h`. ABI version 2
uses fixed-width scalar types, opaque stream handles, caller-owned read and
error buffers, and exported `vm_audio_*` functions. Objective-C/C++ objects are
never exposed to Python.

Lifecycle:

```text
create → start → read/set_dsp* → pause → read until END
                     ↑                ↓
                     └──── resume ────┘
paused/running → stop → read until END → destroy
```

ABI v2 retains `set_dsp` for native harnesses and controlled session-boundary
updates, but the
application treats device, sample rate, block size, AGC, gain, HPF and limiter
as one immutable capture-session plan. UI, menu and RPC edits made during an
utterance are deferred and applied atomically at the next `start()` boundary;
the production app does not mutate native DSP mid-utterance.

`stop` is idempotent while the handle remains owned. `destroy(nullptr)` is safe,
but a successful `destroy(non_null_handle)` consumes that handle exactly once;
reusing the stale pointer is undefined. The Python facade makes
`close()` idempotent by clearing its ownership before the one-shot C call. A
future ABI that needs independently idempotent release must use a new handle
contract/version rather than dereferencing a possibly freed pointer.

`pause` removes the input tap, pauses the engine, drains both queues, and joins
the converter worker while retaining the initialized VoiceProcessingIO graph.
`resume` installs fresh queues, a fresh tap context, and a fresh worker before
restarting that graph. This avoids callbacks retaining stale queue pointers and
reduces repeat-session startup latency without capturing audio while idle.

Stop first publishes `accepting=false`, removes the tap, and stops the engine;
it then completes the same sequentially-consistent handshake by waiting for the
in-flight count to reach zero. This rules out the store-buffering race where
stop observes zero callbacks while a late callback still observes
`accepting=true`. It then ends the raw queue, joins the converter worker,
flushes converter EOS and the final partial block, and ends the PCM queue.
Objective-C exceptions from tap removal, engine stop, or Voice Processing
disable are preserved as runtime faults.
Python drains the PCM queue before destroying the handle. The command-facing
recorder waits at most 500 ms for that drain. After a timeout it returns the
audio already available and marks the session unverified; a daemon cleanup
waits for the in-flight native call before destroy, preventing use-after-free.
If an application PCM callback itself calls `close()`, destruction is likewise
deferred until that consumer callback returns and its thread exits.

`vm_audio_read` treats a destination smaller than the queued block as an
explicit error and reports the required frame count. Because the semaphore token
has already been consumed, the queue restores that token so the caller can retry
the same block with a large enough destination. Returning a timeout without
restoring the token would permanently wedge the published queue head.

The dylib stays loaded for the process lifetime. Do not call `dlclose`, add a
CPython extension dependency, or pass Python-owned memory into a retained
native callback.

## Build and verification

Build the standalone library without accessing a microphone:

```bash
scripts/build_native_audio.sh
uv run python -m pytest -q \
  tests/test_native_audio_library.py \
  tests/test_native_audio_capture.py \
  tests/test_macos_voice_capture.py
```

Inspect static API, TCC and ABI capability as JSON without requesting
permission or opening input:

```bash
uv run python scripts/probe_macos_audio_capabilities.py --compact
```

The default JSON reports only the dylib basename and its origin. Use
`--verbose-paths` for local debugging when an absolute path is required; do not
publish that output because it can reveal the developer's home directory.

When permission is denied, restricted, or not yet determined, the probe skips
device enumeration and reports `planned_input` as structured `unknown`.

The probe is deliberately observation-only. On the first explicit recording
or microphone-test action, `not_determined` instead causes an asynchronous
`AVCaptureDevice.requestAccess` call and an immediate recoverable result that
asks the user to try again. That action never starts the 3-second device
deadline and is never replayed automatically after the permission callback.
Only a later explicit action that observes `authorized` may discover devices
and start a stream.

Inspect the result:

```bash
LIB='.build/native/libvocal_more_audio.dylib'
lipo -archs "$LIB"
vtool -show-build "$LIB"
otool -D "$LIB"
otool -L "$LIB"
nm -gU "$LIB" | grep '_vm_audio_'
```

The install name must be `@rpath/libvocal_more_audio.dylib`; dependencies must
be Apple system frameworks, and the minimum OS must remain aligned with the app
bundle and binary Python wheels.

## Failure and privacy boundaries

Static capability probing may load the dylib and inspect selectors, but it must
not call `vm_audio_start`, enumerate hidden devices after TCC denial, or request
permission. Actual source format, running AGC state, drop counts, and audio
quality can only be verified after a user starts a microphone test or dictation.

The command-line process and `Vocal More.app` have different TCC identities.
A denied CLI probe does not prove the signed app is denied, and an authorized
CLI does not grant the app permission. Raw A/B recordings are private local
artifacts and are never part of automatic diagnostics.

No microphone was opened and no hardware ABBA run was performed as part of the
implementation/automated-verification round documented here. Static probes and
offline tests verify contracts and failure handling; they do not establish that
automatic AGC has better perceptual or recognition quality than manual gain.

## When not to use GPU or Neural Engine

Forty-millisecond audio blocks and a first-order filter do not amortize Metal
command submission, buffer synchronization, or graph compilation. Keep this
DSP on CPU/vDSP. If a future neural VAD or denoiser is introduced, package it as
a measured Core ML model and set `MLModelConfiguration.computeUnits = .all` so
macOS can select CPU, GPU, or Neural Engine. Do not add a GPU/ANE path without a
real model and a model-specific latency, energy, and quality benchmark; merely
occupying another compute unit is not a product benefit.
