# Vocal-More

A macOS voice recognition application that runs in the menu bar, supporting real-time speech-to-text with text polishing.

## Features

- **Walkie-Talkie Mode**: Hold trigger key to record, release to transcribe and paste
- **Real-time Long Mode**: Hold and release, or tap once for hands-free dictation and tap again to stop
- Real-time ASR with selectable models (Qwen-3-ASR, Qwen-3.5-Omni Realtime)
- Text polishing with selectable LLM models (Qwen 3.5 Plus, Qwen 3.6 Plus)
- Privacy-bound output adaptation by coarse foreground-app category
- `enable_thinking` toggle for LLM chain-of-thought reasoning
- Auto-paste transcribed text to cursor position
- Optional automatic dictionary learning from edits made after a paste
- Fn plus up to eight configurable physical shortcut keys
- Verified lossless FLAC archiving for older recording history
- Apple Voice Processing with post-start AGC state verification and a manual low-voice fallback

## Requirements

- macOS 14 or newer on Apple Silicon for the official DMG
- Python 3.10+ for source installations
- DashScope API key

## Installation

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

## Configuration

Set your DashScope API key:

```bash
export DASHSCOPE_API_KEY="your-api-key"
```

Or create `~/.vocal-more/config.yaml`:

```yaml
api_key: "your-api-key"
enable_polish: true
auto_paste: true
audio:
  gain_mode: automatic         # Apple AGC when verified; manual uses software gain
  gain: 8.0                    # retained for manual mode and automatic fallback
  highpass_filter: true
  highpass_freq: 200
  soft_limiter: true
  waveform_ceiling_dbfs: -6.0  # RMS level that fills the capsule waveform
asr:
  model: "qwen3.5-omni-flash-realtime"  # see Models section
  # backend is auto-derived from the selected model
  language: "auto"
  use_dictionary_corpus: true
  extra_corpus_terms:
    - "Vocal More"
    - "DashScope"
llm:
  model: "qwen3.5-plus"  # or "qwen3.6-plus"
  enable_thinking: false
  temperature: 0.0
  max_tokens: 1024
hotkey:
  active_hotkeys: ["fn"]
  custom_keys: []  # record up to eight physical keys in Settings > Shortcuts
ui:
  language: "zh"
dictionary_learning:
  enabled: false  # opt in: sends bounded before/after text to DashScope
  excluded_bundle_ids:
    - "com.1password.1password"
context_personalization:
  enabled: true
  excluded_bundle_ids:
    - "com.example.private"
default_mode: "realtime_long"
```

Audio devices are opened at the source format negotiated by macOS (commonly
48 kHz). Vocal More then uses `AVAudioConverter` to produce one application
contract: 16 kHz, mono, signed PCM16. The legacy `audio.sample_rate` setting is
accepted for compatibility but normalized to 16 kHz; it is not an
end-to-end variable-rate option. `automatic` is the no-calibration default, not
a claim that Apple AGC sounds better. Software gain and limiting are bypassed
only after the running Voice Processing stream reports both Voice Processing
and AGC enabled.

Automatic dictionary learning observes the same editable field for 15 seconds
after Vocal More pastes text. Multiple edits are coalesced into one final-state
comparison scoped to the pasted segment. Plausible corrections are queued
locally and classified in the background by `qwen3.7-plus` using JSON mode and
the API key above. Password fields are skipped, Batch API is not used, and the
Dictionary settings page can approve, reject, or undo learning decisions. A
macOS notification confirms only an actual automatic term or alias addition.

Context personalization reads the frontmost app bundle ID at hotkey press,
maps it locally to Development, Messaging, Writing, or General, then discards
the app identity. Only the abstract category rule can reach the selected
model, and the local profile stores aggregate category counts only. See
[docs/context-personalization.md](docs/context-personalization.md) for the
exact data and failure boundaries.

Recording history keeps the newest three entries as WAV and archives older
successful or failed entries as FLAC in a background worker. Vocal More
decodes each candidate and verifies its PCM SHA-256 and audio parameters
before changing the index or deleting the WAV. Playback, retry transcription,
support bundles, and deletion remain format-transparent. See
[docs/recording-history-compression.md](docs/recording-history-compression.md)
for the migration, failure rules, and measured performance.

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

The app will appear in your menu bar. Grant the required permissions:
- **Microphone**: For audio recording
- **Accessibility**: For hotkey detection and keyboard simulation

If microphone access has not been decided yet, the first explicit recording
action only opens the macOS permission request and asks you to try again. It
does not wait inside device startup or replay the released hotkey action after
permission changes.

## Modes

### Walkie-Talkie Mode
- Hold trigger key to record
- Release to transcribe and paste

### Real-time Long Mode (Default)
- Hold the trigger key and release to finish, or tap once for hands-free
  dictation and tap again to stop

## Shortcuts

The built-in trigger is Fn. All configured triggers use the same gesture:
hold and release, or tap once for hands-free dictation and tap again to stop.

### Additional Shortcut Keys
In Settings > Shortcuts, you can bind up to eight physical keys to the same
dictation action. Duplicate bindings are ignored.

## Evaluation

Generate and validate the local calibration corpus:

```bash
scripts/generate_benchmark_audio.sh
uv run python scripts/benchmark_report.py validate --manifest eval/manifest.yaml
```

Run the paced realtime benchmark and build a report with
`scripts/run_dictation_benchmark.py` and `scripts/benchmark_report.py`.
See [docs/benchmarking.md](docs/benchmarking.md) for trace levels, privacy
boundaries, deterministic app replay, semantic review, and valid Typeless
comparison rules. The current end-to-end calibration report is
[docs/benchmarks/2026-07-27-app-replay.md](docs/benchmarks/2026-07-27-app-replay.md).

For Apple AGC versus manual gain, use the private local ABBA capture and offline
signal report in
[docs/audio-quality-benchmark.md](docs/audio-quality-benchmark.md). The native
runtime, fallback order, and CPU/GPU/Neural Engine decisions are documented in
[docs/apple-audio-architecture.md](docs/apple-audio-architecture.md).
For a zero-capture capability check, run
`uv run python scripts/probe_macos_audio_capabilities.py --compact`; it never
requests microphone permission or starts an audio engine.

This implementation round did not open a microphone or run a hardware ABBA
trial. The included capture and analysis tools are the protocol for producing
that evidence; their presence alone does not support an audio-quality claim.

For one-off ASR debugging, set `VOCAL_MORE_DEBUG_DIR=/tmp/vocal-more-debug` before launching the app. Each transcription will save the source WAV plus a JSON event trace with partial transcripts, final transcripts, corpus text, and timing data.

## License

Vocal-More is free software licensed under the GNU General Public License
version 3 only (`GPL-3.0-only`). See [LICENSE](LICENSE) for the full terms.
