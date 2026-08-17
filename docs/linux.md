# Ubuntu GNOME Wayland support

The Linux release targets exactly Ubuntu 26.04 LTS, amd64, GNOME Shell 50, and Wayland. It does not claim support for Shell 49/51, Xorg, KDE, Xfce, Sway, ARM, Flatpak, or AppImage.

## Architecture

The Python process is a `Gtk.Application` that owns the session-bus name `com.sm_yjr.VocalMore`. GTK's main thread exclusively owns GTK widgets, the Wayland clipboard, and D-Bus registration. Existing mode workers and the command coordinator continue to own recording/transcription work.

The Shell extension `vocal-more@sm-yjr.com` is version-locked to Shell 50 and owns the compositor-only capabilities:

- F8–F12 press/release capture with repeat suppression
- the panel menu and non-focus-stealing `St` capsule
- focused desktop app ID reporting through `Shell.WindowTracker`
- Clutter virtual-keyboard injection of `Ctrl+V`

The public `com.sm_yjr.VocalMore.Desktop1` snapshot is schema version 1 and contains state only. It never contains dictated text. `PasteRequested` contains only a uint64 request ID.

Automatic paste is a confirmed two-step operation. The worker first asks the GTK main thread to own the clipboard, then signals Shell. Shell injects `Ctrl+V` into the application focused at completion time and calls `CompletePaste`. A timeout or negative acknowledgement is a failed dictation output; the clipboard remains available for recovery and the app does not report a successful paste.

## Install and first run

```bash
sudo apt install ./vocal-more_<version>_amd64.deb
vocal-more --settings
```

Enable **Vocal More** in the GNOME Extensions app and sign out/in once. This is required for global key release capture, the capsule, and automatic paste. Package removal deliberately preserves all user data.

The YAML configuration remains the source of truth. The host mirrors `hotkey.linux_accelerator` to the extension GSettings key; only F8 through F12 are accepted.

## Linux paths

- Config/dictionary: `$XDG_CONFIG_HOME/vocal-more/`
- Recordings/databases: `$XDG_DATA_HOME/vocal-more/`
- Logs/debug/support: `$XDG_STATE_HOME/vocal-more/`

When the XDG targets are empty, first startup atomically copies an existing `~/.vocal-more` tree and writes a migration marker. The legacy tree is never removed.

## Audio, accessibility, and privacy

Linux recording uses `sounddevice`/PortAudio on PipeWire and the shared software gain, high-pass, noise-control, and soft-limiter pipeline. It does not use or claim Apple Voice Processing, Apple AGC/AEC, macOS microphone modes, Objective-C++ capture, or `afconvert`.

AT-SPI focused text is used only when automatic dictionary learning is explicitly enabled. Missing Text interfaces, protected/password fields, stale targets, focus changes, and exited applications return no snapshot. Context personalization is enabled by default but receives only a desktop app ID; macOS bundle-ID exclusions remain in YAML and are not deleted.

## Verification

Automated CI runs the full Python suite, static privacy/contract checks, strict schema compilation, deb build, install/remove smoke tests, `--version`, and license checks. The extension also has an isolated GNOME Shell 50 headless load test.

Before a preview release, run the interactive matrix on a real GNOME 50 Wayland session:

- F8–F12 press/release, repeat, rapid press, long hold, Escape/cancel, lock/unlock, and extension reload
- GTK4, Firefox, Chromium, VS Code, LibreOffice, and GNOME Terminal paste with Chinese/English input methods
- built-in, USB, and Bluetooth microphones; PipeWire route changes and suspend/resume
- single/dual displays, 100%/150%/200% scale, fullscreen, light/dark, and Reduced Motion
- supported/unsupported AT-SPI targets, password fields, focus changes, and target exit

If global release capture or Clutter keyboard injection fails on the release image, Linux preview publication is blocked. Portal, `uinput`, manual paste, and XWayland are not accepted substitutes.
