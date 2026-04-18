# Next Steps

This file tracks the recommended post-merge follow-up work after the concurrency control refactor landed.

The main refactor goals are already complete:

- UI access is marshaled onto the main thread
- hotkey control intent is serialized
- realtime audio sending is decoupled from the audio callback
- finish-time workflows can be invalidated on cancel
- retry/background helpers use managed executors

What remains is not another large rewrite. The next phase should focus on observability, tightening boundaries, and finishing the state-ownership story.

## P0

### 1. Keep the concurrency model visible

Owners:

- maintain `docs/concurrency-runtime-model.md`
- update it whenever a new long-lived worker, queue, or cross-thread UI path is introduced

Done when:

- new concurrency changes include doc updates

### 2. Add high-value runtime diagnostics

Add structured or at least consistently formatted logs for:

- `session_token`
- command type and command sequence
- active mode name
- outbound audio queue depth
- realtime degradation reason
- fallback reason
- cancel reason

Why:

- concurrency bugs are hardest when state transitions cannot be reconstructed after the fact

### 3. Keep running real desktop smoke tests after concurrency-sensitive changes

Minimum manual coverage:

1. walkie-talkie press, hold, release
2. realtime-long start, stop, cancel
3. rapid repeated hotkey input
4. retry transcription in settings
5. long recording that exercises fallback behavior

## P1

### 1. Turn `StreamingASRCallback` into a thinner event source

Target direction:

- SDK callback thread parses incoming events
- callback thread enqueues typed inbound events
- one owner handles lifecycle consequences

Why:

- this is the biggest remaining place where session behavior is still partly driven from outside a single owner

### 2. Make lifecycle states explicit

Introduce and enforce a shared lifecycle enum such as:

- `IDLE`
- `STARTING`
- `RECORDING`
- `STOPPING`
- `PROCESSING`
- `CANCELLING`
- `FAILED`

Why:

- today the system is much safer, but some transitions are still represented implicitly across mode state, engine flags, and callback events

### 3. Expand concurrency-specific regression tests

Recommended additions:

- cancel during connect
- late callback after mode close
- shutdown while sender queue still has items
- reconnect after degraded realtime path
- multiple rapid cancel/start command sequences

## P2

### 1. Continue reducing audio callback workload

Investigate whether any of the following should move off the callback thread:

- some DSP stages
- PCM conversion
- parts of level computation

This is lower priority than the structural fixes already landed.

### 2. Tune realtime queue and fallback policy

Things worth validating empirically:

- queue size
- sender drain timeout
- warm session TTL
- fallback thresholds for long recordings

### 3. Normalize terminology in code and docs

The codebase now has a few important concepts:

- hotkey callback worker
- dictation command coordinator
- background executor
- ASR sender thread
- mode session token

Keep those names stable and reuse them consistently in future code review and documentation.

## Recommended Next Milestone

The best next engineering milestone is:

**Route inbound realtime ASR events through a single owner and formalize the lifecycle state machine.**

That gives the highest remaining architecture payoff without requiring another repo-wide rewrite.
