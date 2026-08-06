# Windows desktop version

The Windows host reuses Vocal More's existing Python dictation runtime rather than forking the ASR, polishing, recording-history, and configuration layers. Platform-specific code is limited to the notification-area shell, floating capsule, standard settings window, global keyboard capture, foreground-process classification, filesystem conventions, paste shortcuts, and packaging.

## Supported in this preview

- Windows 10 and Windows 11 x64
- Native notification-area icon with start, stop, cancel, mode, auto-paste, settings, data-folder, and quit commands
- Top-center floating capsule with recording level animation, processing stages, success/error feedback, drag repositioning, and one-click cancellation
- Standard Windows-themed settings window built with `tkinter.ttk`
- Global F8 trigger with the same hold-or-tap gesture used by the macOS Fn trigger
- Selectable F8–F12, Caps Lock, Right Ctrl, or Right Alt trigger
- Walkie-Talkie, Real-time Long, and Meeting modes
- PortAudio microphone capture through `sounddevice`
- Low-voice software gain, high-pass filtering, and soft limiting
- Realtime and file ASR backends, optional LLM polishing, recording history, dictionary corpus, and coarse foreground-application personalization
- Ctrl+V auto-paste and Ctrl+A replacement behavior
- One-process-per-user-session guard
- Portable ZIP and per-user Inno Setup installer, both built and smoke-tested by GitHub Actions

## Deliberate feature boundary

Apple Voice Processing, Apple AGC verification, Sparkle updates, macOS Accessibility-based focused-text observation, and the native Objective-C++ audio library remain macOS-only.

Automatic dictionary learning therefore does not observe focused text on Windows: the runtime receives a no-op provider and never reads the target application's text. Context personalization is supported because it needs only the foreground executable name; no window title or document content is read. Windows notifications and the capsule report state and errors but deliberately omit transcript text because notifications can be retained in Notification Center and overlays can be captured by screen-sharing software.

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

The installer places application binaries under `%LOCALAPPDATA%\Programs\Vocal More` by default. Uninstalling removes the application and shortcuts but intentionally leaves `%APPDATA%\Vocal More` untouched.

Recording history remains WAV-only in this preview; the current verified FLAC archive codec uses macOS AudioToolbox.

## Floating capsule

The capsule appears only while a task is active. It shows:

- microphone startup and recording states
- a smoothed live level meter while recording
- ASR, polishing, and meeting-processing stages
- short-lived success and failure feedback
- current mode, trigger, and Escape cancellation hint

The close button cancels the current task. The capsule can be dragged away from the default top-center position for the current process lifetime. It never displays dictated text.

## Settings window

Open **Settings…** from the tray menu or double-click the tray icon. The window provides tabs for:

- API key, interface language, default mode, trigger, auto-paste, and context adaptation
- ASR model, recognition language, dictionary corpus, and microphone selection
- LLM model, reasoning, polish strength, persona, tone, temperature, and token limit
- gain mode, software gain, high-pass filter, limiter, and waveform calibration
- application version and shortcuts to the data folder, YAML config, and log

Settings are validated before saving and are applied through the same serialized runtime configuration path used by the rest of the application. Audio changes take effect at the next recording boundary. Saving is blocked while a dictation task is active.

The YAML file remains available under **Advanced: Open config file** for settings not yet represented in the GUI.

## Trigger behavior

Windows usually does not expose a laptop's physical Fn key to ordinary global keyboard hooks. The built-in `fn` configuration identifier is therefore interpreted as F8 by the Windows adapter.

In Real-time Long mode:

- Hold the trigger for at least 350 ms and release to finish the same recording.
- Tap the trigger to latch hands-free recording, then press it again to stop.
- Press Escape or the capsule close button while a task is active to cancel it.

In Walkie-Talkie mode, recording follows key-down and key-up directly. Meeting mode uses key-down as a toggle.

Pynput does not suppress the trigger key. The active application can still receive it. This avoids silently stealing shortcuts; choose a trigger that does not conflict with the applications used for dictation.

## Microphone and paste permissions

Enable microphone access for desktop applications in **Windows Settings > Privacy & security > Microphone**. PortAudio device failures are surfaced through the capsule, tray notification, and local log.

Windows User Interface Privilege Isolation prevents a normal-integrity Vocal More process from injecting Ctrl+V into an elevated application. Run both processes at the same integrity level. Running the voice input tool as administrator by default is not recommended.

## Install with the setup program

Run:

```text
Vocal-More-<version>-windows-x64-setup.exe
```

The installer is per-user by default and does not require administrator access. It creates a Start menu shortcut and offers optional desktop and sign-in startup shortcuts. Installed files include an uninstaller registered with Windows Apps settings.

The preview installer and executable are unsigned. Windows SmartScreen may warn on first launch. Code signing should be added after real-device validation and before broad distribution.

## Portable archive

Extract:

```text
Vocal-More-<version>-windows-x64.zip
```

Keep the complete `Vocal More` directory together and run `Vocal More.exe`. The PyInstaller `onedir` form is intentional: native audio DLL discovery is more predictable and startup is faster than a self-extracting one-file executable.

## Source run

From PowerShell at the repository root:

Install standard CPython 3.12 with Tcl/Tk support, then from PowerShell:

```powershell
py -3.12 -m venv .venv
uv sync --locked --group dev --python .venv\Scripts\python.exe
$env:PYTHONUTF8 = "1"
$env:DASHSCOPE_API_KEY = "your-api-key"
uv run vocal-more
```

The first run creates `%APPDATA%\Vocal More\config.yaml` when needed. In `context_personalization.excluded_bundle_ids`, Windows entries are lowercase executable basenames such as `example.exe`.

## Packaging

Install the build-only Python dependency and build the portable archive:

```powershell
uv pip install --python .venv\Scripts\python.exe `
  -r packaging\windows\requirements-build.txt
.\packaging\windows\build.ps1
```

To build the installer, install Inno Setup 6 and run:

```powershell
.\packaging\windows\build_installer.ps1
```

Outputs:

```text
dist\Vocal-More-<version>-windows-x64.zip
dist\Vocal-More-<version>-windows-x64-setup.exe
```

The packaged interpreter starts in Python UTF-8 mode so Chinese transcripts, dictionaries, prompts, and configuration remain independent of the Windows legacy code page.

## Verification boundary

CI verifies dependency locking, the Python test suite, deterministic icon generation, PyInstaller packaging, the portable executable, Inno Setup compilation, silent installation, installed executable startup, silent uninstallation, and artifact upload on `windows-latest`.

CI does not provide evidence for physical microphone quality, endpoint-specific PortAudio behavior, global-hook interaction with vendor keyboard software, visual behavior across unusual DPI and multi-monitor layouts, or paste behavior across integrity levels. Those require a physical Windows 10/11 test matrix.
