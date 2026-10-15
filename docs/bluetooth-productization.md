# Bluetooth product candidate 1

This is an unnotarized, ad-hoc signed development candidate for the planned
0.7.0 release. The public development prerelease is 0.7.0-beta.1+3.
Keep this separate bundle alongside the installed stable app.
Two-stage correction is not included.

## Safe initial state

Normal Finder launch uses a separate `Vocal More Bluetooth Candidate` profile,
imports no existing settings or credentials, disables microphone/audio and
global hotkeys, and exits after 15 minutes. Bluetooth and current-app input are
off by default. It does not inherit approval from an older development identity.
Do not copy permission databases or change system security settings.

## Install on a test Mac

Requires an Apple Silicon Mac (arm64), macOS 14 or later. Download and extract
the candidate ZIP and keep its folder intact. Open `Vocal More Bluetooth
Candidate.app`. This package has an ad-hoc signature and has **not** passed
Developer ID signing, notarization or Gatekeeper distribution acceptance.
If macOS blocks opening, inspect the source and signature, then use the ordinary
System Settings > Privacy & Security approval only if your organization permits
this test. Do not remove quarantine attributes or disable Gatekeeper.
The app has no Sparkle updater; stable update feeds are unchanged.

## Staged acceptance

1. Approve opening this particular candidate on each Mac. Configure one Mac to
   find the other, and the other to accept connections. If the system requests
   Bluetooth access, the person confirms it. Compare both six-digit codes and
   explicitly approve the intended peer on the accepting Mac.
2. Select the output device explicitly: local or peer. UC focus cannot be
   identified reliably, so automatic UC target selection is blocked. No text
   falls back silently to another device.
3. In an empty test document, enable **Allow paired Mac to type into the current
   app** on the intended receiver. If required, the person grants Accessibility
   and event-posting access to this exact candidate. A secure, unknown,
   non-editable, changed or locked target is not admitted. Start with the fixed
   marker button and inspect the complete result in an ordinary app.
4. Test both directions, target/caret changes, disconnect/reconnect, application
   restart, cancellation and repeated actions. AX acceptance is a positive
   receipt; a posted clipboard paste remains unconfirmed until the person
   checks it. Neither failure nor a missing receipt causes automatic replay.
5. Real dictation is a separate approval and test. Select the desired F1 binding
   and model in this candidate's isolated settings and enter credentials there
   yourself. Only then launch its executable with
   `--enable-candidate-dictation`. That explicit mode enables native audio and
   global hotkeys, keeps the separate profile and no-import policy, and runs
   until normal quit. Recording still requires an explicit action. Do not run
   this mode unattended or before the approved live test begins.

## Implementation and limits

BLE carries completed text, never keyboard intents or audio. macOS encrypted
GATT and the explicitly verified peer identity protect the transport; revoking
trust stops the connection. Results bind the peer, field/caret revision,
connection session and recording epoch/generation at dictation start. Changing
any of them rejects delivery. One final result consumes one binding.

Current-app AX runs on one bounded worker and reads role, subrole, enabled state
and numeric selection only, never AXValue or selected text. It reserves a
durable ID before mutation. Clipboard fallback rechecks the target, session,
permissions, cancellation, expiry and pasteboard ownership. It restores only
owned text clipboard data when that preference is enabled; it cannot restore
an arbitrary non-text clipboard or prove that a third-party app accepted a
keyboard event. No automatic second attempt is made after an uncertain mutation.

The earlier fixed-editor BLE results belong to an older signed package. This
candidate requires new physical acceptance. Actual F1/UC, microphone/provider
latency, Typora and other applications, sleep/wake, minimum macOS support,
formal signing/notarization and upgrade delivery remain release gates. Automated
worker tests and native state-machine tests do not substitute for those gates.
