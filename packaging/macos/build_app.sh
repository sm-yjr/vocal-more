#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOOLCHAIN="${VOCAL_MORE_RUST_TOOLCHAIN:-1.98.1}"
BUILD_PYTHON="${VOCAL_MORE_BUILD_PYTHON:-python3}"
TARGET_ARCH="${VOCAL_MORE_TARGET_ARCH:-$(uname -m)}"
if [[ "$TARGET_ARCH" != "arm64" ]]; then
  echo "The official Rust macOS app currently targets arm64." >&2
  exit 1
fi
export MACOSX_DEPLOYMENT_TARGET=14.0
CARGO_TARGET_DIR="$ROOT/rust/target" cargo "+$TOOLCHAIN" build --locked --release \
  --manifest-path "$ROOT/rust/Cargo.toml" -p vocal-more-desktop -p vocal-more-backend \
  --bin vocal-more-desktop --bin vocal-more-backend
NATIVE_LIBRARY="$ROOT/.build/native/libvocal_more_audio.dylib"
"$ROOT/scripts/build_native_audio.sh" --output "$NATIVE_LIBRARY"
APP="$ROOT/dist/Vocal More.app"
"$BUILD_PYTHON" "$ROOT/packaging/macos/stage_rust_app.py" --app "$APP" \
  --binary "$ROOT/rust/target/release/vocal-more-desktop" \
  --backend "$ROOT/rust/target/release/vocal-more-backend" --native "$NATIVE_LIBRARY"
"$BUILD_PYTHON" "$ROOT/packaging/macos/rust_notices.py" --toolchain "$TOOLCHAIN" \
  --output "$APP/Contents/Resources/Rust-Third-Party-Notices.txt"
# The GPL text remains independent of dependency notices.
ditto "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE.txt"
ditto "$ROOT/resources/settings/SHADCN-UI-LICENSE.txt" "$APP/Contents/Resources/Shadcn-UI-LICENSE.txt"
SPARKLE_ROOT="$("$ROOT/packaging/macos/install_sparkle.sh")"
SPARKLE_FRAMEWORK="$APP/Contents/Frameworks/Sparkle.framework"
ditto "$SPARKLE_ROOT/Sparkle.framework" "$SPARKLE_FRAMEWORK"
ditto "$SPARKLE_ROOT/LICENSE" "$APP/Contents/Resources/Sparkle-LICENSE.txt"
if [[ "${VOCAL_MORE_SKIP_ADHOC_SIGN:-0}" != "1" ]]; then
  "$ROOT/packaging/macos/sign_sparkle.sh" "$SPARKLE_FRAMEWORK" - 0
  while IFS= read -r file; do
    codesign --force --sign - "$file" >/dev/null
  done < <(
    find "$APP/Contents" -path "$SPARKLE_FRAMEWORK/*" -prune -o -type f -print0 |
      xargs -0 file |
      awk -F: '/Mach-O/ { sub(/ [(]for architecture .*/, "", $1); print $1 }' |
      sort -ru
  )
  # Ad-hoc identities have no Team ID for hardened library validation. The
  # release path skips this block and applies Developer ID + hardened runtime.
  codesign --force \
    --entitlements "$ROOT/packaging/macos/entitlements.plist" --sign - "$APP"
fi
# Exercise the actual launcher with its embedded product version.
"$APP/Contents/MacOS/Vocal More" --version
"$APP/Contents/Resources/rust-backend/vocal-more-backend" --version
echo "Built dist/Vocal More.app (Rust desktop)"
