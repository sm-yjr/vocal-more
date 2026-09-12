#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Build an isolated development backend, not an official app/DMG release.
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_OUTPUT="${1:-$TASK_ROOT/.build/rust-host}"
if [[ "$(uname -s)" == "Darwin" ]]; then
  export MACOSX_DEPLOYMENT_TARGET=14.0
fi
# Fix the target location so caller CARGO_TARGET_DIR cannot change copy sources.
CARGO_TARGET_DIR="$TASK_ROOT/rust/target" cargo build --locked --release \
  --manifest-path "$TASK_ROOT/rust/Cargo.toml" -p vocal-more-host -p vocal-more-backend
mkdir -p "$TASK_OUTPUT"
cp "$TASK_ROOT/rust/target/release/vocal-more-host" "$TASK_OUTPUT/"
cp "$TASK_ROOT/rust/target/release/vocal-more-backend" "$TASK_OUTPUT/"
cp "$TASK_ROOT/LICENSE" "$TASK_OUTPUT/LICENSE.txt"
if [[ "$(uname -s)" == "Darwin" ]]; then
  bash "$TASK_ROOT/scripts/build_native_audio.sh" \
    --output "$TASK_OUTPUT/libvocal_more_audio.dylib"
fi
printf 'Rust backend: %s/vocal-more-backend\n' "$TASK_OUTPUT"
