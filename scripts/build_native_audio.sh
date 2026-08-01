#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="$ROOT/.build/native/libvocal_more_audio.dylib"
CONFIGURATION="${VOCAL_MORE_NATIVE_CONFIGURATION:-release}"
TARGET_ARCH="${VOCAL_MORE_TARGET_ARCH:-$(uname -m)}"
DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-14.0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    --configuration)
      CONFIGURATION="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The native audio library can only be built on macOS." >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"

OPTIMIZATION=(-O3 -DNDEBUG)
if [[ "$CONFIGURATION" == "debug" ]]; then
  OPTIMIZATION=(-O0 -g)
fi

xcrun --sdk macosx clang++ \
  -std=c++20 \
  "${OPTIMIZATION[@]}" \
  -arch "$TARGET_ARCH" \
  -mmacosx-version-min="$DEPLOYMENT_TARGET" \
  -dynamiclib \
  -fobjc-arc \
  -fblocks \
  -fvisibility=hidden \
  -Wall \
  -Wextra \
  -Werror \
  -Wl,-install_name,@rpath/libvocal_more_audio.dylib \
  -I "$ROOT/native/audio/include" \
  -framework Foundation \
  -framework AVFoundation \
  -framework Accelerate \
  "$ROOT/native/audio/src/VocalMoreAudio.mm" \
  -o "$OUTPUT"

echo "Built $OUTPUT"
