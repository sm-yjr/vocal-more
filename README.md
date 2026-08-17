# Vocal-More

Vocal-More is a desktop voice-recognition application with real-time speech-to-text and optional text polishing. It runs natively on macOS, Windows, and Ubuntu GNOME Wayland.

## Features

- Walkie-Talkie, long dictation, and meeting modes
- Realtime and file ASR with selectable Qwen models
- Optional LLM polishing, dictionary corpus, recording history, and foreground-app adaptation
- Auto-paste into the active application
- Native global triggers: Fn on macOS, selectable triggers on Windows, and F8–F12 through a GNOME Shell extension on Linux
- macOS menu-bar UI with Apple Voice Processing and verified AGC
- Windows notification-area UI with a floating capsule, standard settings window, portable ZIP, and per-user installer
- Ubuntu GNOME 50 GTK4 settings, Shell capsule/menu, AT-SPI integration, and confirmed Wayland clipboard paste

## Requirements

- macOS 14 or newer on Apple Silicon for the official DMG
- Windows 10 or Windows 11 x64 for the Windows build
- Ubuntu 26.04 LTS amd64 with GNOME Shell 50 in a Wayland session for the Linux deb
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

## Ubuntu 26.04 installation

Install the amd64 package, enable `vocal-more@sm-yjr.com` in the GNOME Extensions app, then sign out and back in once. The first application launch displays the same one-time guide.

```bash
sudo apt install ./vocal-more_<version>_amd64.deb
vocal-more --settings
```

The Shell extension owns F8–F12 capture, the non-focus-stealing capsule, panel controls, and `Ctrl+V` injection. Dictated text stays in the GTK clipboard and never appears in the public D-Bus snapshot or paste signal. See [docs/linux.md](docs/linux.md).

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

### Ubuntu 26.04

Install the system GTK/PortAudio dependencies, then use the locked environment:

```bash
sudo apt install python3-dev python3-gi gir1.2-gtk-4.0 gir1.2-atspi-2.0 portaudio19-dev libsndfile1 flac
uv sync --locked --group dev --python /usr/bin/python3
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

Persistent configuration is stored at `~/.vocal-more/config.yaml` on macOS and `%APPDATA%\Vocal More\config.yaml` on Windows. Linux follows XDG: configuration under `$XDG_CONFIG_HOME/vocal-more`, recordings/databases under `$XDG_DATA_HOME/vocal-more`, and logs/support bundles under `$XDG_STATE_HOME/vocal-more`. A legacy Linux `~/.vocal-more` tree is copied once without deleting the source.

Vocal More exposes one downstream audio contract: 16 kHz, mono, signed PCM16. macOS prefers Apple Voice Processing and `AVAudioConverter`; Windows and Linux use PortAudio through `sounddevice` with the shared software gain, high-pass filter, and limiter. Linux labels this path as software gain and uses verified lossless FLAC for eligible background archival.

Automatic dictionary learning uses macOS Accessibility or Linux AT-SPI, fails closed for password/unsupported fields, and remains disabled by default. Linux context personalization receives only the stable desktop app ID from Shell; it never reads window titles or document content.

## Verification

The Windows workflow runs the Python test suite, checks Tk availability, generates the application icon, builds the PyInstaller folder, smoke-tests the portable executable, compiles the Inno Setup installer, silently installs it, smoke-tests the installed executable, silently uninstalls it, and uploads both artifacts. Physical microphone quality, OEM keyboard hooks, multi-monitor visual behavior, and paste behavior across Windows integrity levels still require interactive hardware testing.

## License

Vocal-More is licensed under GPL-3.0-only. See [LICENSE](LICENSE).
