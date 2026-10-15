#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Build only; no launch, network request, signing credentials or installation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-/tmp/vocal-more-bt-public-target}"
# Use a public dependency cache under /tmp as CARGO_HOME: some dependency asset
# macros embed their manifest directory independently of compiler remapping.
# Remap home paths in compiler diagnostics and source metadata as well.
export RUSTFLAGS="--remap-path-prefix=$HOME=/build/home --remap-path-prefix=$ROOT=/build/vocal-more --remap-path-prefix=/private$ROOT=/build/vocal-more"
export CARGO_INCREMENTAL=0
export MACOSX_DEPLOYMENT_TARGET=14.0
cargo +1.98.1 build --offline --locked --manifest-path "$ROOT/rust/Cargo.toml" \
  -p vocal-more-desktop --bin vocal-more-desktop \
  -p vocal-more-backend --bin vocal-more-backend
bash "$ROOT/scripts/build_native_audio.sh" --output "$CARGO_TARGET_DIR/native/libvocal_more_audio.dylib"
