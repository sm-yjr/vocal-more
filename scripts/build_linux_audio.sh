#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:-$ROOT/.build/native/libvocal_more_audio.so}"
[[ "$(uname -s)" == Linux ]] || { echo 'This script requires Linux.' >&2; exit 1; }
mkdir -p "$(dirname "$OUTPUT")"
# shellcheck disable=SC2046
"${CXX:-c++}" -std=c++20 -O2 -DNDEBUG -shared -fPIC -fvisibility=hidden \
  -Wall -Wextra -Werror -pthread -I "$ROOT/native/audio/include" \
  $(pkg-config --cflags portaudio-2.0 samplerate) \
  "$ROOT/native/audio/src/LinuxAudio.cpp" \
  $(pkg-config --libs portaudio-2.0 samplerate) -o "$OUTPUT"
printf 'Built %s\n' "$OUTPUT"
