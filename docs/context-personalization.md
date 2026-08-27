# Privacy-bound app context

Vocal More adapts the input purpose and writing style to the kind of app
receiving the text while keeping document content outside the context pipeline.

## Data flow

At hotkey press, the app reads only the frontmost application's bundle
identifier through `NSWorkspace`. It immediately maps that transient value to
one of five categories:

- `development`: preserve code, commands, API names, paths, and English
  identifiers.
- `terminal`: preserve commands, arguments, paths, environment variables, and
  identifiers.
- `messaging`: keep short, conversational phrasing and avoid formalization.
- `writing`: prefer coherent paragraphs and readable punctuation without
  over-compressing.
- `general`: do not add a context-specific prompt rule.

The prompt sent to the selected transcription or polish model contains only
the abstract category rule. It does not contain the bundle identifier or app
name.

## Mode routing

When app-context adaptation is enabled, terminal apps such as Ghostty use
Agent Prompt mode. Messaging apps such as DingTalk, plus development, writing,
general, excluded, and unidentified apps, use Dictation mode. Turning off
app-context adaptation restores the fixed input-purpose selection from
Settings.

## Persistence boundary

`~/.vocal-more/context-profile.json` contains only successful-paste counters
for the five categories. The repository deliberately has no fields for:

- app names or bundle identifiers;
- window titles or focused-control values;
- clipboard data;
- audio, raw transcripts, or polished text;
- timestamps or per-session records.

Writes are atomic. A malformed profile fails closed to zero counters and does
not affect dictation.

Password managers are always excluded. Additional bundle identifiers can be
excluded in Settings > Polish > App context. The same page can disable
adaptation or reset all counters.

## Failure behavior

Context capture is best effort. If macOS does not return a frontmost bundle
identifier, classification fails, or the profile cannot be written, dictation
continues without contextual adaptation. A context failure must never block
recording, transcription, polish, or paste.
