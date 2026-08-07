# Vocal-More

Vocal-More is a desktop voice-recognition application with real-time speech-to-text and optional text polishing. It runs as a native menu-bar app on macOS and as a notification-area app on Windows.

## Features

- Walkie-Talkie, long dictation, and meeting modes
- Realtime and file ASR with selectable Qwen models
- Optional LLM polishing, dictionary corpus, recording history, and foreground-app adaptation
- Auto-paste into the active application
- Native global triggers: Fn on macOS and a selectable F8–F12 or modifier trigger on Windows
- macOS menu-bar UI with Apple Voice Processing and verified AGC
- Windows notification-area UI with a floating capsule, standard settings window, portable ZIP, and per-user installer

## Requirements

- macOS 14 or newer on Apple Silicon for the official DMG
- Windows 10 or Windows 11 x64 for the Windows build
- DashScope API key
- Python 3.10+ for source installations; release CI uses Python 3.12

## Windows installation

Use the setup program for a normal per-user installation:

```text
Vocal-More-<version>-windows-x64-setup.exe
```

It installs under `%LOCALAPPDATA%\Programs\Vocal More`, registers an uninstaller, creates a Start menu shortcut, and can optionally add desktop and sign-in startup shortcuts. Uninstalling deliberately preserves `%APPDATA%\Vocal More`, which contains configuration, dictionaries, logs, and recording history.

The portable alternative is `Vocal-More-<version>-windows-x64.zip`. Extract the complete `Vocal More` directory and run `Vocal More.exe`.

Both artifacts are currently unsigned, so Windows SmartScreen may warn on first launch.

### Windows interaction

- Right-click the tray icon for start/stop, mode, auto-paste, Settings, data-folder, and quit actions.
- Double-click the tray icon to open Settings.
- The top-center capsule shows live audio level, processing stages, success/failure feedback, and cancellation. It never displays dictated text.
- The settings window covers API key, language, mode, trigger, ASR/LLM model, microphone, polishing, and audio processing.
- The built-in `fn` configuration maps to F8 on Windows because ordinary Windows keyboard hooks usually cannot observe laptop Fn keys.

See [docs/windows.md](docs/windows.md) for behavior, privacy boundaries, packaging, and known limitations.

## Source installation

### macOS

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
vocal-more
```

### Windows

Install standard CPython 3.12 with Tcl/Tk support, then run from PowerShell:

```powershell
py -3.12 -m venv .venv
uv sync --locked --group dev --python .venv\Scripts\python.exe
$env:PYTHONUTF8 = "1"
$env:DASHSCOPE_API_KEY = "your-api-key"
uv run vocal-more
```

## Windows packaging

Install the build dependency and create the portable archive:

```powershell
uv pip install --python .venv\Scripts\python.exe `
  -r packaging\windows\requirements-build.txt
.\packaging\windows\build.ps1
```

Install Inno Setup 6 and create both the portable archive and installer:

```powershell
.\packaging\windows\build_installer.ps1
```

Outputs:

```text
dist\Vocal-More-<version>-windows-x64.zip
dist\Vocal-More-<version>-windows-x64-setup.exe
```

## Configuration

Persistent configuration is stored at `~/.vocal-more/config.yaml` on macOS and `%APPDATA%\Vocal More\config.yaml` on Windows. The Windows GUI applies common settings through the same serialized runtime configuration path; the YAML file remains available for advanced options.

Vocal More exposes one downstream audio contract: 16 kHz, mono, signed PCM16. macOS prefers Apple Voice Processing and `AVAudioConverter`; Windows uses PortAudio through `sounddevice` with the shared software gain, high-pass filter, and limiter.

Automatic dictionary learning from post-paste edits currently requires macOS Accessibility. On Windows, context personalization reads only the foreground executable basename and never reads window titles or document text.

## Verification

The Windows workflow runs the Python test suite, checks Tk availability, generates the application icon, builds the PyInstaller folder, smoke-tests the portable executable, compiles the Inno Setup installer, silently installs it, smoke-tests the installed executable, silently uninstalls it, and uploads both artifacts. Physical microphone quality, OEM keyboard hooks, multi-monitor visual behavior, and paste behavior across Windows integrity levels still require interactive hardware testing.

## License

Vocal-More is licensed under GPL-3.0-only. See [LICENSE](LICENSE).
