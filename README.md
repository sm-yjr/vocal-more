# Vocal-More

Vocal-More is a desktop voice-recognition application with real-time speech-to-text and optional text polishing. It runs as a native menu-bar app on macOS and as a notification-area app on Windows.

## Features

- **Walkie-Talkie Mode**: Hold the trigger key to record, then release to transcribe and paste
- **Real-time Long Mode**: Hold and release, or tap once for hands-free dictation and tap again to stop
- Real-time ASR with selectable models (Qwen-3-ASR, Qwen-3.5-Omni Realtime)
- Text polishing with selectable LLM models (Qwen 3.5 Plus, Qwen 3.6 Plus)
- Privacy-bound output adaptation by coarse foreground-app category
- `enable_thinking` toggle for LLM reasoning
- Auto-paste transcribed text to the active application
- Optional automatic dictionary learning from edits made after a paste on macOS
- Native global triggers: Fn on macOS and F8 on Windows, plus compatible custom physical keys
- Verified lossless FLAC archiving on macOS; WAV recording history in the Windows preview
- Apple Voice Processing on macOS, with a cross-platform PortAudio and software-DSP fallback

## Requirements

- macOS 14 or newer on Apple Silicon for the official DMG
- Windows 10 or Windows 11 x64 for the Windows preview
- Python 3.10+ for source installations; Python 3.12 is used by release CI
- DashScope API key

## Installation

### macOS source installation

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
vocal-more
```

### Windows preview

The Windows workflow produces `Vocal-More-<version>-windows-x64.zip`. Extract the archive and run `Vocal More.exe`; the app remains in the notification area.

For a source run from PowerShell:

```powershell
uv python install 3.12
uv sync --locked --group dev --python 3.12
uv run vocal-more
```

To build the portable Windows archive:

```powershell
uv pip install --python .venv\Scripts\python.exe `
  -r packaging\windows\requirements-build.txt
.\packaging\windows\build.ps1
```

See [docs/windows.md](docs/windows.md) for the supported feature boundary, configuration path, packaging model, and known limitations.

## Configuration

Set your DashScope API key:

```bash
export DASHSCOPE_API_KEY="your-api-key"
```

In PowerShell:

```powershell
$env:DASHSCOPE_API_KEY = "your-api-key"
```

The persistent configuration file is `~/.vocal-more/config.yaml` on macOS and `%APPDATA%\Vocal More\config.yaml` on Windows:

```yaml
api_key: "your-api-key"
enable_polish: true
auto_paste: true
audio:
  gain_mode: automatic         # Apple AGC when verified; otherwise software gain
  gain: 8.0                    # retained for manual mode and automatic fallback
  highpass_filter: true
  highpass_freq: 200
  soft_limiter: true
  waveform_ceiling_dbfs: -6.0
asr:
  model: "qwen3.5-omni-flash-realtime"
  language: "auto"
  use_dictionary_corpus: true
  extra_corpus_terms:
    - "Vocal More"
    - "DashScope"
llm:
  model: "qwen3.5-plus"
  enable_thinking: false
  temperature: 0.0
  max_tokens: 1024
hotkey:
  active_hotkeys: ["fn"]       # maps to F8 in the Windows host
  custom_keys: []
ui:
  language: "zh"
dictionary_learning:
  enabled: false               # currently supported only by the macOS AX adapter
  excluded_bundle_ids:
    - "com.1password.1password"
context_personalization:
  enabled: true
  excluded_bundle_ids:
    - "com.example.private"
default_mode: "realtime_long"
```

Vocal More exposes one application audio contract: 16 kHz, mono, signed PCM16. Source devices commonly run at 48 kHz and are converted before ASR. On macOS, the preferred path uses Apple Voice Processing and `AVAudioConverter`; Windows uses the PortAudio path provided by `sounddevice` and the same software gain, high-pass filter, and limiter. The legacy `audio.sample_rate` setting is accepted for compatibility but normalized to 16 kHz.

Automatic dictionary learning observes the same editable field for 15 seconds after Vocal More pastes text. Multiple edits are coalesced into one final-state comparison scoped to the pasted segment. Plausible corrections are queued locally and classified in the background by `qwen3.7-plus` using JSON mode and the configured API key. Password fields are skipped, Batch API is not used, and the Dictionary settings page can approve, reject, or undo learning decisions. This focused-text observer currently requires macOS Accessibility; the Windows host uses a no-op provider even if the setting is enabled.

Context personalization reads the frontmost macOS bundle ID or Windows executable name at hotkey press, maps it locally to Development, Messaging, Writing, or General, then discards the application identity. Only the abstract category rule can reach the selected model, and the local profile stores aggregate category counts only. See [docs/context-personalization.md](docs/context-personalization.md) for the exact data and failure boundaries.

On macOS, recording history keeps the newest three entries as WAV and archives older terminal entries as FLAC after verifying the decoded PCM. The Windows preview keeps history as WAV because the current verified lossless codec is backed by macOS AudioToolbox. Playback, retry transcription, support bundles, and deletion remain available on both platforms. See [docs/recording-history-compression.md](docs/recording-history-compression.md) for the macOS archive migration and failure rules.

## Models

### Text Polish (LLM)

- **Qwen 3.5 Plus** (`qwen3.5-plus`) — Default
- **Qwen 3.6 Plus** (`qwen3.6-plus`)

### Speech Recognition (ASR)

- **Qwen-3.5 Omni Flash Realtime** (`qwen3.5-omni-flash-realtime`) — Default, low-latency streaming with inline polish
- **Qwen-3-ASR Realtime** (`qwen3-asr-flash-realtime-2026-02-10`) — Legacy real-time streaming
- **Qwen-3-ASR Short File** (`qwen3-asr-flash`) — For short recordings
- **Qwen-3.5 Omni Realtime** (`qwen3.5-omni-plus-realtime`) — Preview, text transcription only

## Usage

```bash
vocal-more
```

On macOS, the app appears in the menu bar and requests Microphone and Accessibility permissions. On Windows, it appears in the notification area; press F8 or use the tray menu to start dictation. Windows microphone consent is managed by Settings > Privacy & security > Microphone. A non-elevated Vocal More process cannot inject Ctrl+V into an elevated target application because Windows integrity levels isolate input.

If macOS microphone access has not been decided yet, the first explicit recording action only opens the permission request and asks the user to try again. It does not wait inside device startup or replay the released hotkey action after permission changes.

## Modes

### Walkie-Talkie Mode

- Hold the trigger key to record
- Release to transcribe and paste

### Real-time Long Mode (Default)

- Hold the trigger key and release to finish, or tap once for hands-free dictation and tap again to stop

## Shortcuts

The built-in trigger is Fn on macOS and F8 on Windows. All configured triggers use the same gesture: hold and release, or tap once for hands-free dictation and tap again to stop.

On macOS, Settings > Shortcuts can bind up to eight physical keys. The Windows host reuses compatible persisted bindings, but the first preview does not yet include a native shortcut-recording settings panel.

## Evaluation

Generate and validate the local calibration corpus:

```bash
scripts/generate_benchmark_audio.sh
uv run python scripts/benchmark_report.py validate --manifest eval/manifest.yaml
```

Run the paced realtime benchmark and build a report with `scripts/run_dictation_benchmark.py` and `scripts/benchmark_report.py`. See [docs/benchmarking.md](docs/benchmarking.md) for trace levels, privacy boundaries, deterministic app replay, semantic review, and valid Typeless comparison rules. The current end-to-end calibration report is [docs/benchmarks/2026-07-27-app-replay.md](docs/benchmarks/2026-07-27-app-replay.md).

For Apple AGC versus manual gain, use the private local ABBA capture and offline signal report in [docs/audio-quality-benchmark.md](docs/audio-quality-benchmark.md). The native runtime, fallback order, and CPU/GPU/Neural Engine decisions are documented in [docs/apple-audio-architecture.md](docs/apple-audio-architecture.md). For a zero-capture capability check, run `uv run python scripts/probe_macos_audio_capabilities.py --compact`; it never requests microphone permission or starts an audio engine.

For one-off ASR debugging, set `VOCAL_MORE_DEBUG_DIR` before launching the app. Each transcription saves the source WAV plus a JSON event trace with partial transcripts, final transcripts, corpus text, and timing data.

## License

Vocal-More is free software licensed under the GNU General Public License version 3 only (`GPL-3.0-only`). See [LICENSE](LICENSE) for the full terms.
