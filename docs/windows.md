# Windows desktop preview

The Windows host reuses Vocal More's existing Python dictation runtime rather than forking the ASR, polishing, recording-history, and configuration layers. Platform-specific code is limited to the notification-area shell, global keyboard capture, foreground-process classification, filesystem conventions, paste shortcuts, and packaging.

## Supported in the first preview

- Windows 10 and Windows 11 x64
- Notification-area icon with start, stop, cancel, mode, auto-paste, configuration, data-folder, and quit commands
- Global F8 trigger with the same hold-or-tap gesture used by the macOS Fn trigger
- Compatible persisted custom physical-key bindings
- Walkie-Talkie, Real-time Long, and Meeting modes
- PortAudio microphone capture through `sounddevice`
- Low-voice software gain, high-pass filtering, and soft limiting
- Realtime and file ASR backends, optional LLM polishing, recording history, dictionary corpus, and coarse foreground-application personalization
- Ctrl+V auto-paste and Ctrl+A replacement behavior
- One-process-per-user-session guard
- Portable PyInstaller folder archive built and smoke-tested by GitHub Actions

## Deliberate feature boundary

The first preview does not attempt to imitate the macOS floating capsule or WebKit settings window. Configuration is edited as YAML from the tray menu. Apple Voice Processing, Apple AGC verification, Sparkle updates, macOS Accessibility-based focused-text observation, and the native Objective-C++ audio library remain macOS-only.

Automatic dictionary learning therefore does not observe focused text on Windows: the runtime receives a no-op provider and never reads the target application's text. Context personalization is supported because it needs only the foreground executable name; no window title or document content is read. Windows notification balloons report state and errors but deliberately omit transcript text because notifications can be retained in Notification Center.

## Data and configuration paths

The Windows host stores persistent state under:

```text
%APPDATA%\Vocal More\
```

The main files are:

```text
config.yaml
recordings\recordings.json
recordings\*.wav
dictionary.yaml
dictionary-learning.sqlite3
context-profile.json
vocal-more.log
```

The log file is used primarily by the windowed packaged build, where standard output may not exist. Recording history remains WAV-only in this preview; the current verified FLAC archive codec uses macOS AudioToolbox.

## Trigger behavior

Windows usually does not expose a laptop's physical Fn key to ordinary global keyboard hooks. The built-in `fn` configuration identifier is therefore interpreted as F8 by the Windows adapter.

In Real-time Long mode:

- Hold F8 for at least 350 ms and release to finish the same recording.
- Tap F8 to latch hands-free recording, then press F8 again to stop.
- Press Escape while a task is active to cancel it.

In Walkie-Talkie mode, recording follows F8 key-down and key-up directly. Meeting mode uses key-down as a toggle.

Pynput does not suppress the trigger key. The active application can still receive F8. This avoids stealing application shortcuts silently; a later settings UI can offer an explicit suppression policy.

## Microphone and paste permissions

Enable microphone access for desktop applications in Windows Settings > Privacy & security > Microphone. PortAudio device failures are surfaced through the tray notification and the local log.

Windows User Interface Privilege Isolation prevents a normal-integrity Vocal More process from injecting Ctrl+V into an elevated application. Run both processes at the same integrity level. Running the voice input tool as administrator by default is not recommended.

## Source run

From PowerShell at the repository root:

```powershell
uv python install 3.12
uv sync --locked --group dev --python 3.12
$env:PYTHONUTF8 = "1"
$env:DASHSCOPE_API_KEY = "your-api-key"
uv run vocal-more
```

The first run creates `%APPDATA%\Vocal More\config.yaml` when needed. In `context_personalization.excluded_bundle_ids`, Windows entries are lowercase executable basenames such as `example.exe`.

## Packaging

Install the build-only dependency and run the packaging script:

```powershell
uv pip install --python .venv\Scripts\python.exe `
  -r packaging\windows\requirements-build.txt
.\packaging\windows\build.ps1
```

The output is:

```text
dist\Vocal-More-<version>-windows-x64.zip
```

The archive contains an onedir PyInstaller build, `LICENSE.txt`, and this document. The packaged interpreter starts in Python UTF-8 mode so Chinese transcripts, dictionaries, prompts, and configuration remain independent of the Windows legacy code page. The onedir form is intentional: native audio DLL discovery is more predictable and startup is faster than a self-extracting one-file executable.

The preview artifact is unsigned. Windows SmartScreen may warn on first launch. Signing and an installer should be added only after the runtime behavior is validated on real Windows audio hardware.

## Verification boundary

CI verifies dependency locking, the Python test suite, PyInstaller packaging, required files, and a `--version` process smoke test on `windows-latest`. CI does not provide evidence for microphone quality, endpoint-specific PortAudio behavior, global-hook interaction with vendor keyboard software, or paste behavior across integrity levels. Those require a physical Windows test matrix.
