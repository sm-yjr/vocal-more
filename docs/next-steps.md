# Next Steps Status

This file originally tracked the post-merge follow-up work after the main concurrency-control refactor landed.

That follow-up milestone is now complete.

## Completed In This Milestone

### P0: Observability and runtime visibility

- `docs/concurrency-runtime-model.md` was updated to reflect the latest worker ownership rules
- `DictationCommandCoordinator` now logs command sequence and command type
- mode lifecycle logs now include mode name, explicit state, session token, and cancel reason
- realtime ASR logs now include outbound queue depth, queue high-water mark, realtime degradation reason, and fallback reason

### P1: Boundary tightening and explicit lifecycle states

- `StreamingASRCallback` is now a thinner SDK-thread event source
- SDK-managed realtime callbacks enqueue inbound events onto one callback-local worker
- callback-local aggregation state and upward partial/final/error delivery now happen on that inbound worker
- mode lifecycle states are now explicit:
  - `IDLE`
  - `STARTING`
  - `RECORDING`
  - `STOPPING`
  - `PROCESSING`
  - `CANCELLING`
  - `FAILED`
- concurrency-focused regression coverage was expanded around callback dispatch, lifecycle transitions, cancellation, and queue policy helpers

### P2: Pragmatic performance and terminology cleanup

- audio callback work was reduced slightly by returning early once recording has already stopped
- realtime queue sizing now scales with recorder chunk duration instead of using one fixed chunk count blindly
- sender drain timeout now scales with pending queue depth
- terminology is now consistent across docs and logs:
  - hotkey callback worker
  - dictation command coordinator
  - background executor
  - ASR sender thread
  - inbound ASR event worker
  - mode session token

## What Still Remains

This is no longer a “must-finish” backlog. What remains is optional refinement work:

- keep doing manual desktop smoke tests after concurrency-sensitive changes
- gather real-world telemetry before retuning queue/fallback thresholds again
- decide whether inbound ASR events should eventually join the same serial owner as dictation commands instead of stopping at the callback-local worker
- reduce audio callback DSP/PCM work further only if profiling shows it matters

## Recommended Next Milestone

The best next milestone is now:

**Add lightweight production-facing diagnostics review and collect a small amount of real-world evidence before any further concurrency rewrite.**

In practice that means:

1. watch queue depth / fallback logs during real usage
2. confirm the new explicit lifecycle states match user-visible behavior
3. only then decide whether a deeper engine-wide state machine is worth the complexity
