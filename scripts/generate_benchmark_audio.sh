#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="$ROOT/eval/generated"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vocal-more-benchmark.XXXXXX")"

cleanup() {
  find "$TEMP_DIR" -type f -delete
  rmdir "$TEMP_DIR"
}
trap cleanup EXIT

for command in say ffmpeg; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "$command is required" >&2
    exit 1
  fi
done

mkdir -p "$OUTPUT"

synthesize() {
  local id="$1"
  local voice="$2"
  local text="$3"
  local filter="${4:-anull}"
  local source="$TEMP_DIR/$id.aiff"

  say -v "$voice" -r 175 -o "$source" "$text"
  ffmpeg -hide_banner -loglevel error -y \
    -i "$source" \
    -af "$filter" \
    -ar 16000 -ac 1 -c:a pcm_s16le \
    "$OUTPUT/$id.wav"
}

synthesize_with_noise() {
  local id="$1"
  local voice="$2"
  local text="$3"
  local source="$TEMP_DIR/$id.aiff"

  say -v "$voice" -r 175 -o "$source" "$text"
  ffmpeg -hide_banner -loglevel error -y \
    -i "$source" \
    -f lavfi -i "anoisesrc=color=pink:amplitude=0.05:r=16000" \
    -filter_complex \
    "[0:a]volume=0.75[voice];[voice][1:a]amix=inputs=2:duration=first:dropout_transition=0[mix]" \
    -map "[mix]" \
    -ar 16000 -ac 1 -c:a pcm_s16le \
    "$OUTPUT/$id.wav"
}

synthesize normal_zh Tingting "今天下午三点提交设计稿。"
synthesize normal_en Samantha "Please review the latency report before Friday."
synthesize mixed_terms Tingting "请在 GitHub 合并 Vocal More 的 pull request。"
synthesize whisper_zh Tingting "轻声输入也应该保持清晰。" \
  "volume=0.08,highpass=f=180,lowpass=f=5000"
synthesize_with_noise ambient_noise_en Samantha \
  "Background noise should not change this sentence."
synthesize fillers_zh Tingting "嗯，我们先检查麦克风，然后测试转写。"
synthesize repetition_en Samantha \
  "I said Tuesday, Tuesday at ten in the morning."
synthesize self_correction_zh Tingting \
  "会议安排在周三，不对，改到周四下午。"
synthesize list_zh Tingting "购物清单：咖啡、牛奶和苹果。"

echo "Generated benchmark audio in $OUTPUT"
