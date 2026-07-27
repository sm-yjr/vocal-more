# Automatic Dictionary Learning

## Problem

Manual dictionary entry works when users already know which terms are likely to
be misrecognized. It does not learn from the higher-quality signal produced
when a user immediately corrects pasted dictation.

Automatic dictionary learning turns that edit into a candidate mapping:

```text
阿里云白练 -> 阿里云百炼
```

The feature is opt-in because the classification request sends bounded
dictation and edit context to DashScope.

## Runtime Flow

1. `DictationWorkflow` captures the focused editable Accessibility element
   immediately before `Cmd+V`.
2. A dedicated observer polls that exact element for up to 15 seconds and
   scopes changes to the text span introduced by the current paste.
3. Intermediate edits are coalesced: the final state wins. The final diff is
   split into at most five independently classifiable candidates, which are
   queued atomically as sibling jobs. Returning to the pasted baseline creates
   no job.
4. Leaving the field commits the latest state. If focus remains in the field at
   the deadline, the last relevant edit must have been stable for at least
   500 ms. If the user switches apps before the observer's first post-paste
   poll, the baseline is reconstructed from the pre-paste selection and the
   retained Accessibility element.
5. Secure fields, excluded bundle IDs, clears, and large rewrites stop or
   discard the observation. Edits to pre-existing text outside the pasted span
   are ignored.
6. Plausible edits are written to
   `~/.vocal-more/dictionary-learning.sqlite3`.
7. A single background worker calls `qwen3.7-plus` using the user's configured
   DashScope API key.
8. Deterministic local validation checks that the corrected term occurs in the
   edited text and every alias occurs in the pasted or raw text.
9. Results at or above `0.90` confidence are added automatically. Every other
   structurally valid candidate waits for review, including low-confidence
   candidates. Model decisions that are clearly unrelated to reusable
   vocabulary remain ignored.
10. A macOS notification is shown only when automatic learning actually creates
    a term or adds at least one alias. Duplicate results and manually approved
    review items do not report an automatic-learning success.

The model request is fixed:

```text
model: qwen3.7-plus
temperature: 0
max_tokens: 256
stream: false
enable_thinking: false
response_format: json_object
```

The client uses DashScope's OpenAI-compatible endpoint. It does not use the
Batch API.

## Model Contract

The model must return one JSON object:

```json
{
  "decision": "add",
  "term": "阿里云百炼",
  "aliases": ["阿里云白练"],
  "confidence": 0.98,
  "reason_code": "proper_noun_correction"
}
```

The local validator rejects:

- missing or sentence-sized terms;
- aliases absent from the original dictation;
- terms absent from the edited text;
- number, date, or time changes;
- punctuation-only mappings;
- dictionary conflicts and alias cycles.

Model output never mutates the dictionary without passing these checks.

## Multiple Edits in One Observation Window

The observer treats the 15-second window as a final-state transaction, not as
an event stream. For example:

```text
paste:        阿里云白练
intermediate: 阿里云百练
final:        阿里云百炼
evidence:     阿里云白练 -> 阿里云百炼
```

This avoids learning transient typos. The following boundaries are covered by
tests:

- several corrections to the same text produce only the final mapping;
- correcting and then reverting to the pasted text produces no job;
- edits made only to text that existed before the paste are ignored;
- simultaneous edits inside and outside the pasted span retain only the pasted
  segment as model evidence;
- a relevant edit made less than 500 ms before the deadline is discarded as
  unsettled;
- a field that becomes secure invalidates the entire observation;
- focus leaving the original Accessibility target ends the window and commits
  the last observed state.

One paste may produce up to five sibling model decisions when it contains
distant dictionary-worthy corrections. Nearby edit hunks are merged, repeated
identical mappings are deduplicated, and punctuation-only changes are ignored.
If the final diff contains more than five candidates, the whole observation is
discarded instead of learning a truncated subset. Sibling jobs are enqueued
atomically, validated and retried independently, remain independently
reversible, and produce one grouped success notification after the group
finishes.

## Persistence and Retry

SQLite owns pending, processing, retry, applied, review, ignored, failed, and
reverted states. Network and rate-limit failures retry with exponential
backoff, up to five attempts. Invalid JSON and non-retryable request failures
are marked failed.

The settings page exposes the runtime pipeline instead of showing only final
successes. It reports active observation, queued or processing corrections,
review decisions, automatic additions, ignored decisions, retry/failure
states, and the latest observation that produced no reusable correction.

Jobs record the model and prompt version so a future prompt change remains
auditable. Dictionary entries stay in the existing YAML format; SQLite stores
the source, confidence, aliases added, and enough mutation metadata to undo only
the automatic change. Full before/after evidence is redacted as soon as a job
reaches a terminal or review state; only pending and retryable work retains the
text needed for a future request.

## Configuration

```yaml
dictionary_learning:
  enabled: false
  excluded_bundle_ids:
    - com.1password.1password
    - com.apple.Terminal
```

The default is disabled. Enabling requires both an Accessibility permission and
a user-supplied DashScope API key. Disabling the feature prevents queued jobs
from being sent until it is enabled again.

## Concurrency Ownership

The observer and model queue have separate single-worker executors:

- the observer may wait for the bounded edit window but never blocks the
  finish-time dictation worker;
- the queue worker performs only non-realtime network and persistence work;
- a new observation cancels the previous session token;
- both workers have explicit `close()` paths;
- UI notifications are marshaled back to the main thread.

This preserves the runtime ownership rules in
`docs/concurrency-runtime-model.md`.
