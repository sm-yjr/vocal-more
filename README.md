# Vocal-More

A macOS voice recognition application that runs in the menu bar, supporting real-time speech-to-text with text polishing.

## Features

- **Walkie-Talkie Mode**: Hold trigger key to record, release to transcribe and paste
- **Real-time Long Mode**: Press trigger key to start recording, press again to stop and polish
- Real-time ASR with selectable models (Qwen-3-ASR, Qwen-3.5-Omni Realtime)
- Text polishing with selectable LLM models (Qwen 3.5 Plus, Qwen 3.6 Plus)
- `enable_thinking` toggle for LLM chain-of-thought reasoning
- Auto-paste transcribed text to cursor position
- Configurable shortcut keys (Fn, Double Cmd, F13-F20, or custom key)

## Requirements

- macOS
- Python 3.10+
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
asr:
  model: "qwen3-asr-flash-realtime-2026-02-10"  # see Models section
  # backend is auto-derived from the selected model
  language: "zh"
  use_dictionary_corpus: true
  extra_corpus_terms:
    - "Vocal More"
    - "DashScope"
llm:
  model: "qwen3.5-plus"  # or "qwen3.6-plus"
  enable_thinking: false
  temperature: 0.0
  max_tokens: 256
  polish_mode: "smart"  # "smart" or "always"
hotkey:
  active_hotkeys: ["fn"]
  custom_key: ""  # record a custom key in Settings > Shortcuts
```

## Models

### Text Polish (LLM)
- **Qwen 3.5 Plus** (`qwen3.5-plus`) — Default
- **Qwen 3.6 Plus** (`qwen3.6-plus`)

### Speech Recognition (ASR)
- **Qwen-3-ASR Realtime** (`qwen3-asr-flash-realtime-2026-02-10`) — Default, real-time streaming
- **Qwen-3-ASR Short File** (`qwen3-asr-flash`) — For short recordings
- **Qwen-3.5 Omni Realtime** (`qwen3.5-omni-plus-realtime`) — Preview, text transcription only

## Usage

```bash
vocal-more
```

The app will appear in your menu bar. Grant the required permissions:
- **Microphone**: For audio recording
- **Accessibility**: For hotkey detection and keyboard simulation

## Modes

### Walkie-Talkie Mode (Default)
- Hold trigger key to record
- Release to transcribe and paste

### Real-time Long Mode
- Press trigger key once to start recording (real-time transcription appears as you speak)
- Press trigger key again to stop and polish the text

## Shortcuts

Built-in options: Fn, Double Cmd, F13-F20.

### Custom Shortcut
In Settings > Shortcuts, you can record a custom single-key trigger in addition to the built-in options (Fn, Double Cmd, F13-F20).

## Evaluation

Add WAV samples under `eval/audio/`, set `disabled: false` in `eval/manifest.yaml`, then run:

```bash
python scripts/eval_dictation.py
```

For one-off ASR debugging, set `VOCAL_MORE_DEBUG_DIR=/tmp/vocal-more-debug` before launching the app. Each transcription will save the source WAV plus a JSON event trace with partial transcripts, final transcripts, corpus text, and timing data.
