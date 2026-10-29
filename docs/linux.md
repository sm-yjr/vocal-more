# Linux Rust desktop development build

> 产品验收遵循 [Linux 迁移要求](linux-migration-requirements.md)。下文记录现有开发实现与临时限制，不表示核心体验已经与 macOS 对齐。

This branch starts at `v0.6.1` (`0.6.1+2`, `6d46f4f`), with its existing Rust
business backend. It does not include the separate 0.7 Bluetooth candidate.
GPUI Kit is pinned to **0.8.1**, checked against the official October 26 release on October 29.
Cargo.lock pins its matching GPUI 0.3.10 snapshots. This is a development port, not an official Linux release.

## Build on Ubuntu / Debian

Install Rust 1.98.1 (with rustfmt/clippy), Python 3.11+ and the system packages:

```sh
sudo apt-get install build-essential pkg-config libclang-dev libssl-dev \
  libfontconfig1-dev libfreetype-dev libxkbcommon-dev libxkbcommon-x11-dev \
  libwayland-dev libx11-xcb-dev libxcb1-dev libxcb-xkb-dev libxcb-shape0-dev \
  libxcb-xfixes0-dev libvulkan-dev libasound2-dev libudev-dev \
  portaudio19-dev libsamplerate0-dev libdbus-1-dev \
  mesa-vulkan-drivers wl-clipboard xclip xdotool ffmpeg pipewire-bin dpkg-dev \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev gstreamer1.0-pipewire \
  gstreamer1.0-plugins-good at-spi2-core xdg-desktop-portal
bash scripts/build_linux_desktop.sh
```

The script builds a native executable and ABI 2 `.so`, then writes a development
archive and an APT-managed `vocal-more_<version>_<architecture>.deb` under `.build/linux/`. The bundle contains the GPL license, font/component notices,
corresponding source and a source/asset manifest. Add `--debug` for a faster local
package. No macOS release, Sparkle feed or product version is modified.
Builds depend on the builder's glibc and system libraries; a build on a newer
Ubuntu is not a binary compatibility promise for older distributions.

Install the `.deb` using APT (the exact filename is printed by the builder):

```sh
sudo apt install ./.build/linux/vocal-more_0.6.1-2_amd64.deb
```

The executable is `/usr/bin/vocal-more`, private binaries are under
`/usr/lib/vocal-more`, and source, license and notices are under
`/usr/share/doc/vocal-more`. Dependency versions are derived from both ELF
binaries with `dpkg-shlibdeps`; desktop tools loaded at runtime are also declared.
The package has no maintainer scripts and never starts the app or edits user data.
Quit and relaunch the app after upgrading. `sudo apt remove vocal-more` removes
the package while preserving configuration and history in the home directory.

The tar archive remains available for portable development: run `./vocal-more`
or `./install.sh` for a per-user install. A portable install is outside APT.
When migrating, quit it and remove its `~/.local/bin/vocal-more` link and per-user
`io.github.sm_yjr.VocalMore.desktop` entry so they do not shadow the system package.
Keep `~/.local/share/vocal-more`, which contains your application data.

For development without packaging:

```sh
bash scripts/build_linux_audio.sh
cargo run --locked --manifest-path rust/Cargo.toml -p vocal-more-desktop
```

The first recording requires your own DashScope key in Settings. Launching the
app enumerates input devices, but does not start recording. The application does
not create an API key or use an OpenAI API. The existing provider/model selection
is unchanged.

## Recording and global shortcuts

The app starts in the background without a persistent dictation/control window.
Its StatusNotifierItem tray menu offers Start/Finish, Cancel, Copy, Settings and
Quit. Closing Settings keeps the process and shortcuts alive; duplicate launch
reopens the existing Settings window. First-run setup still opens when needed.
The desktop must provide a StatusNotifier host (Ubuntu GNOME AppIndicators is
supported); if unavailable, Settings explains how to restore the tray entry.
The settings window reuses service, audio, model, polish, dictionary, history,
microphone preview and whisper calibration controls. System appearance is kept.

Open **Settings → Shortcuts** to add a function key or a combination with Ctrl,
Alt or Super. Click Add shortcut, press the desired keys, and it is saved to the
GNOME global-shortcut settings. Edit/Remove update only Vocal More bindings for
this data profile. Existing F1 bindings created for `/usr/bin/vocal-more --command
toggle` are adopted without changing their key. The page supports up to eight
bindings and checks other custom shortcuts and GNOME keybinding settings for
conflicts. Escape, switching tabs, closing Settings or losing focus cancels capture;
recording is suppressed while capturing an already-bound Vocal More key.

GNOME supports system-authorized press/release gestures. In **Settings → Shortcuts**,
choose **Enable hold gestures** and confirm the native Add Keyboard Shortcuts dialog.
In the default realtime mode, hold longer than 350 ms and release to finish;
a short tap latches hands-free recording and the next press finishes. In
push-to-talk mode every release finishes. The app uses the GlobalShortcuts portal,
not a global key logger. Binding rejection, service loss or disable restores the
owned toggle bindings. Closing Settings leaves the session alive. Quitting closes
the portal and restores the saved GNOME binding; the choice is remembered for the
next launch. Bare modifiers and non-GNOME settings adapters remain migration gaps.

For diagnosis, the installed launcher still accepts `--command toggle`, `press`,
`release`, `stop`, `cancel`, `settings`, `copy`, and `quit`. Per-binding activation
uses a validated `shortcut:<id>` and this profile's private IPC socket. Linux never
interprets macOS virtual key codes. Duplicate launch opens the existing profile's
settings instead of starting another host or microphone.

## Platform behavior

| Capability | X11 | GNOME / other Wayland |
| --- | --- | --- |
| GPUI desktop and settings | Yes | Yes |
| Global toggle configuration | In-app UI on GNOME | In-app UI on GNOME; other desktops pending |
| Floating capsule | GPUI popup | GNOME extension preferred; Xwayland popup fallback on tested Ubuntu GNOME |
| Screen context | User-selected portal/PipeWire source | Same |
| Correction learning | Opt-in AT-SPI after insertion | Retained AT-SPI target after native insertion; explicit manual delivery also supported |
| Microphone + 16 kHz mono PCM | PortAudio | PortAudio through system audio |
| Low-voice processing | Software gain, high-pass, tanh limiter | Same |
| Automatic insertion | xclip + xdotool, guarded focus/clipboard checks | Native AT-SPI editing; opt-in keyboard-only portal compatibility for accessible Chromium fields |
| Streaming insertion | Serialized output chunks | Serialized native edits in the same retained target |
| Clipboard restoration | Owned text only, after 600 ms read grace | Native insertion leaves clipboard unchanged |
| Playback | pw-play or paplay | Same |
| Lossless history archival | ffmpeg, decoded PCM verified | Same |

Linux capture uses mono and software gain, including a software fallback when
an imported profile requests Apple automatic gain. Apple VoiceProcessingIO,
array capture and native fast paste remain unavailable. PortAudio initialization
and device enumeration can print ALSA/JACK diagnostics without opening a capture
stream. Unsupported hardware or missing desktop tools report an error.

### Floating capsule

The dark capsule follows recording/processing/error states, displays a smoothed
waveform and preview, and offers Cancel/Finish without requesting keyboard focus.
On X11 it is an override-redirect GPUI notification popup. A compositing window
manager provides its transparent corners. GPUI rendering defaults to X11/Xwayland
when DISPLAY is available; `GPUI_BACKEND=Wayland` explicitly selects native Wayland.
This choice uses GPUI's `GraphicalEnvironment` API; it does not change the real
session environment, clipboard behavior or portal permissions.

On GNOME Wayland, an enabled **Vocal More Capsule** Shell extension remains the
preferred renderer. It draws a nonfocusable actor above application windows and
only receives a bounded display model and returns Cancel/Finish. It does not read
screen pixels, microphone data or input fields. The app automatically uses its
focus-free Xwayland popup while the extension is unavailable, including the
current login session after first installation. There is no extra dictation
window and no logout requirement for this tested fallback. A native Wayland-only
host with neither renderer refuses to start an invisible recording and opens
Settings with extension/login recovery instructions.

The `.deb` installs the optional GNOME renderer at
`/usr/share/gnome-shell/extensions/vocal-more@sm-yjr`; the portable installer uses
`~/.local/share/gnome-shell/extensions`. A newly installed extension can require a
fresh login before GNOME discovers it. The package never restarts the user's shell.
The Shell actor hides at idle, lock, controller disconnect or a 3.5-second update
timeout. The host also watches GNOME ScreenSaver state to cancel and hide the
fallback on lock/blank, preventing old sessions from returning after unlock.
Terminal recording errors remain readable for four seconds and then dismiss,
matching macOS; details remain in the tray. Starting a new session clears them.
startup validation errors also surface in the capsule instead of only a console.

The current Ubuntu 26.04 / GNOME 50 Wayland desktop has been exercised with a
production GPUI host, its real tray service, native microphone PCM and the
Xwayland capsule. Controlled private GNOME tests cover the extension renderer.
Other advertised Shell versions and non-GNOME compositors remain unverified;
fullscreen/multi-monitor/workspace/lock coverage remains part of the wider
migration acceptance. The app never uses `/dev/uinput` or injects X11 keyboard
events in a Wayland session. Updates remain APT-managed.

### Screen context

Enable Screen context in Settings and select a compatible vision-capable realtime
model. Starting dictation requests a screen/window through the system ScreenCast
portal. **Only the user-selected source** enters the PipeWire/GStreamer lane;
the app cannot silently choose or capture arbitrary screens. GNOME/KDE require
the matching `xdg-desktop-portal-gnome` / `xdg-desktop-portal-kde` backend.

The raw RGB lane applies PipeWire's `VideoCropMeta` and row stride before encoding.
Window streams may use a much larger backing buffer; encoding that buffer directly
would shrink the selected content and send black padding. Cropping happens before
proportional downscaling, and small windows are never enlarged. Raw crop allocation
is capped at 64 MiB and invalid dimensions, crop rectangles or strides are rejected.
Frames are JPEG, at most 1280×720 and 190 KiB, sampled every two seconds and kept
in memory. They are sent to the configured recognition provider as screen context;
they are not written into history. Restore tokens remain in memory for this app
lifetime only. Stop, cancel, disabling the setting, 230-second capture limit or
shutdown closes the portal session, including pending selection dialogs. Frames
are checked against the start request and delivery epoch before backend admission.
If authorization is declined/unavailable, voice recognition continues without new
screen frames. On 2026-10-18 the real GNOME 50 portal and PipeWire 1.6.2 / GStreamer 1.28.2 lane
passed acceptance on this Linux machine with its NVIDIA renderer and an isolated
virtual monitor. The actual Share/Cancel dialog was operated against a dedicated
synthetic GTK window. Three JPEGs reached the production backend/provider and a
credential-free **loopback WebSocket fixture**, byte-for-byte unchanged. Changing
window color/size, stale generation rejection, consent denial, worker shutdown and
PipeWire stream cleanup were checked. This is real OS capture, not a mocked portal.
A subsequent explicitly authorized live run used these three synthetic JPEGs and
16 kHz synthesized English speech through the production Application/Provider and
`qwen3.8-omni-flash-realtime`; the completed result reached the application. A
separate test-only visual question read `VM-8617` from pixels when the code was
absent from its audio/instructions. This probe changes only its own instructions;
normal product behavior remains dictation. One earlier run heard the synthetic
project name as `Loom Harbor`; a later run produced `LumaHarbor`. Transport/visual
acceptance is not a recognition-accuracy benchmark. Other desktop/distribution
combinations still need acceptance. See [the acceptance record](linux-acceptance-2026-10-18.md).

### Automatic correction learning

The existing opt-in Dictionary learning setting now enables an AT-SPI worker.
Before X11 output it captures the focused editable field with a 150 ms budget;
Wayland reuses the target retained at recording start, with a 500 ms capture budget. Only that
retained target can supply later edits to the existing Rust evidence/classifier,
review/undo and dictionary pipeline. Unicode selections use character offsets.
Unresponsive/unknown apps, fields over 8,000 characters, password/noneditable
fields, excluded app IDs and Vocal More's own settings are skipped. Secure roles
and ancestor metadata are checked before requesting text. Some GTK versions expose
hidden entries as generic Text; their toolkit returns mask glyphs, which are rejected
before an observation can receive them. Apps must
expose text through AT-SPI; otherwise clipboard and dictation still work.
Excluded Linux IDs come from `desktop-entry` / `app-id`, falling back to the process
executable name. Learning may send the bounded pasted/edited span to the configured
provider under the existing learning policy; it does not continuously record text.

Wayland automatic output retains the focused target when recording starts and
rechecks its active window, value and selection before each edit. Caret insertion
uses EditableText.InsertText; selection replacement uses a single native edit for supported
single-line controls outside document trees. Multiline/document selections use the
explicitly authorized keyboard compatibility path to preserve document formatting.
Unavailable authorization, unsupported accessibility implementations, moved focus/caret
or changed contents refuse output and display a capsule error. The complete result stays in history
and can be copied explicitly from the tray; failure never overwrites the clipboard
or silently inserts into a newly focused field. Output targets up to 262,144 Unicode characters are supported; correction learning
retains its independent 8,000-character privacy limit. Virtualized Chromium textareas which expose only part of their
value or inconsistent selection offsets are rejected; long-field support is not
universal editor parity. This is a tested supported-field path, not universal editor parity.

For Codex and Chromium fields which expose readable accessibility text but no
EditableText interface, **Settings → General → Compatible automatic input**
offers keyboard-only system authorization. In the native Remote Desktop dialog,
allow remote interaction and confirm Share; no screen or pointer is requested.
Codex also needs **Configure Codex accessibility**, then a complete restart of
Codex from its application launcher. This creates a per-user launcher override
with `--force-renderer-accessibility`; the prior entry is backed up in the Vocal
More data directory, and other actions/arguments are preserved. A running Codex
process cannot acquire this startup flag retroactively.

Compatibility revalidates the original field/value/selection, temporarily owns
the plain-text clipboard, sends authorized Ctrl+V, confirms the expected exact
result, and restores the prior plain text if it still owns the clipboard. It
never retries an unconfirmed paste. PNG/HTML and other single-format byte payloads are restored exactly, including
after the input service exits. Multiple advertised aliases are accepted only when
their bytes agree. Clipboard formats with different contents are preserved by
refusing the compatibility operation. Password, excluded, changed or
unreadable fields remain refused. Chromium Unicode selections, long-page cold
focus discovery, focus-switch/cancel protection and permission restoration have
passed in a real private GNOME Wayland session; installed desktop acceptance remains a separate requirement.
See [the October 29 migration record](linux-acceptance-2026-10-29.md) for the latest test scope.

Manual delivery with auto-paste disabled keeps its 15-second exact-baseline
observation window. The real GTK/AT-SPI fixture now uses production clipboard delivery and real
keyboard edits in both private X11 and GNOME Wayland sessions. It verifies Unicode
selection replacement, delayed manual paste, retained-target-only polling, local
queue/policy/dictionary persistence and undo, exclusions, password fields and
shutdown. Polling never requests text from a different newly focused field.
An explicitly authorized cloud run also classified that real synthetic correction
with `qwen3.7-plus`, automatically added `GitHub`, and successfully undid the change.
The GNOME capsule runner exercises real pointer clicks, focus preservation, the
Rust model/action transport, cancellation epoch revocation, extension re-enable,
stale-controller hiding and worker shutdown. Its repeated-disable test catches
double removal of the extension timer.

References: [ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html),
[AT-SPI](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/),
[GNOME extension development](https://gjs.guide/extensions/development/creating.html).

## APT updates and repository publication

After configuring the signed project software source and installing `vocal-more`,
upgrade through the normal system command:

```sh
sudo apt update
sudo apt upgrade
```

The Linux app displays this command. Its macOS Stable/Beta update selector is
hidden; the selected APT suite determines the Linux channel. Debian version
`0.6.1-2` maps to product `0.6.1+2`; beta `0.6.2b1` build 3 becomes
`0.6.2~beta.1-3`, ordered before `0.6.2-4` by dpkg. Rebuilding changes requires a
new build number before publication; do not replace a published package version.

**No public APT repository is configured or published by these build scripts.**
A release owner supplies an HTTPS hosting root and an existing GPG signing key:

```sh
python3 packaging/linux/build_apt_repository.py .build/linux/vocal-more_*.deb \
  --output .build/apt-candidate \
  --base-url "$VOCAL_MORE_APT_URL" \
  --signing-key "$VOCAL_MORE_APT_KEY_FINGERPRINT" \
  --suite stable
```

The key must be a full fingerprint in the release runner's GnuPG keyring. Keep
private keys/passphrases outside the repository. The command prepares `pool/`,
`Packages`, `Packages.gz`, signed `InRelease` and `Release.gpg`, a minimal public
key and a deb822 `.sources` file in a **new** local directory. It verifies its
signature before returning. It does not upload files or edit this computer's APT
configuration. Stable rejects beta packages; beta suites may include beta and
stable packages. Publish the verified directory atomically at the supplied URL,
with previously published packages retained. Refresh signed metadata before its
90-day `Valid-Until`; publishing metadata does not change application versions.

Users obtain the published public key and `vocal-more.sources` from that trusted
release location, verify the expected key fingerprint, then install them at
`/etc/apt/keyrings/vocal-more-archive-keyring.asc` and
`/etc/apt/sources.list.d/vocal-more.sources`. The generated source file uses
`Signed-By` scoped to that key. Run `sudo apt update` and `sudo apt install
vocal-more` once; subsequent upgrades follow `sudo apt upgrade`. Repository
publication and fresh-machine dependency acceptance remain required before
announcing an official Linux update service.

References: [APT upgrade](https://manpages.debian.org/stable/apt/apt.8.en.html),
[APT archive authentication](https://manpages.debian.org/stable/apt/apt-secure.8.en.html).

## Data and verification

Default data is `$XDG_DATA_HOME/vocal-more` or `~/.local/share/vocal-more`.
On the first default-profile launch the backend may import the legacy
`~/.vocal-more` configuration. Use `--no-import` to disable this. An explicit
`--data-dir PATH` selects an isolated profile and does not import implicitly.
Only one process owns a profile. Runtime sockets are stored in a mode-0700
current-user directory beneath `$XDG_RUNTIME_DIR` (or the temporary directory).

```sh
uv sync --group dev
uv run python -m pytest -q
cargo fmt --all --manifest-path rust/Cargo.toml -- --check
cargo clippy --locked --no-deps --manifest-path rust/Cargo.toml \
  -p vocal-more-desktop --all-targets -- -D warnings
cargo test --locked --manifest-path rust/Cargo.toml --workspace
cargo test --locked --manifest-path rust/Cargo.toml -p vocal-more-backend \
  linux_lossless_roundtrip -- --ignored
sudo apt-get install xvfb python3-xlib python3-gi gir1.2-gtk-4.0 dbus-x11
python3 scripts/smoke_linux_desktop.py
python3 scripts/smoke_linux_accessibility.py
# Private GSettings + production UI/key capture, never edits user bindings:
python3 scripts/smoke_linux_shortcuts.py
# Real GlobalShortcuts consent and ordered press/release in an owned Wayland compositor:
python3 scripts/smoke_linux_gestures.py
VOCAL_MORE_PRIVATE_PORTAL_TEST=1 dbus-run-session -- cargo test --locked \
  --manifest-path rust/Cargo.toml -p vocal-more-desktop --lib linux_portal_pending -- --ignored
# Optional on a GNOME 49/50 host; private HOME, session bus and virtual monitor:
python3 scripts/smoke_linux_gnome_capsule.py
# Real portal + PipeWire + recognition transport against a local fixture; no key/cloud:
python3 scripts/smoke_linux_screen.py
# Explicit live/billable check only: run local fixtures above first. Key file may
# contain a raw DashScope key or DASHSCOPE_API_KEY=...; never put it in shell argv.
python3 scripts/smoke_linux_cloud.py --key-file .env \
  --frames .build/linux-screen-live \
  --evidence .build/linux-smoke/learning-correction.json
```

The native regression uses ASan/UBSan without a device: PCM conversion, high-pass
DC rejection, gain/limiter behavior, nonfinite samples, ring overflow and short
read recovery. The GUI smoke test uses a private Xvfb display, private session bus and isolated
data; it covers windowless startup, tray/Settings actions, duplicate launch,
Settings close/reopen, tray recovery, explicit quit and capsule visibility/focus. It never starts recording or contacts
a provider. GTK target-application paste and synthetic live cloud recognition /
learning were separately accepted as described above. Real microphone/low-voice
quality, latency measurement, physical global shortcuts and other target toolkits
still require live acceptance.

### Background and installed-session acceptance

```sh
/usr/bin/python3 scripts/smoke_linux_desktop.py
# Explicit real-session operation: creates only owned windows and a temporary profile.
# Optional --key-file .env performs a short real microphone/provider connection,
# then cancels without inserting any text. The raw key is never printed.
/usr/bin/python3 scripts/smoke_linux_session.py --real-session --binary /usr/bin/vocal-more --key-file .env
```

The private background test checks windowless startup, tray actions/reconnection,
Settings close/reopen, durable Quit, capsule focus and idle cleanup. The opt-in
session test explicitly selects the Xwayland compatibility renderer and checks
the real desktop tray, Settings, native recording capsule,
focus, cancellation and process cleanup. Its screenshot contains only the owned
capsule, and audio/configuration are removed with the temporary test profile.
It does not claim cloud recognition accuracy or global hardware-key acceptance.

### Wayland input acceptance (2026-10-21)

The owned Chromium compatibility/real keyboard-consent acceptance uses no cloud
or unrelated user windows:

```sh
python3 scripts/smoke_linux_compatibility.py --output .build/linux-compatible-input
```

```sh
# Real current desktop; synthetic speech only; explicit live provider use.
python3 scripts/smoke_linux_auto_input.py --real-session \
  --binary /usr/bin/vocal-more --key-file .env \
  --output .build/linux-installed-auto-input
# Also accepts --streaming, --short-tap, --mode walkie_talkie, --focus-change.
```

The test uses a deleted, permission-restricted profile and an owned native GTK
Wayland editor. It checks exact prefix/result/suffix, Unicode caret offset, original
clipboard identity and clean shutdown. `--test-audio` is accepted only with explicit
acceptance flags/data isolation; normal recording still uses the native microphone.
See [the October 21 acceptance record](linux-acceptance-2026-10-21.md) for scope and
remaining installed-session checks.


### Ghostty terminal input

Ghostty exposes its focused terminal surface without a readable/editable text
interface. Compatible automatic input now supports that specific surface using
the authorized keyboard portal and its dedicated Paste key. The same process, accessibility
object, active window and focus must remain selected. This path never reads the
terminal buffer or participates in correction learning; it sends no Enter and
preserves the terminal's own multiline-paste confirmation. The clipboard is
restored after the receiver read grace. Other non-readable editors and terminals
are not implicitly enabled by this adapter.


For an existing user-managed service or health-check launcher, use
`/usr/bin/vocal-more --background`. Repeated service launches then probe the
existing owner without showing Settings. Interactive application-menu launches
continue to open Settings. The package still does not enable an autostart service.

On GNOME, the updated companion extension provides native clipboard transfer
without temporary clipboard windows. Reads and writes keep the original focus,
and restored binary content remains available after Vocal More exits. Updating
an already loaded extension may require logging out and back in. The existing
wl-clipboard fallback remains for older extensions and other compositors.
