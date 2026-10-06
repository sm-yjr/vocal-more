#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-run}"
if [[ $# -gt 0 ]]; then shift; fi
case "$MODE" in run|--debug|--logs|--telemetry|--verify) ;; *) echo "Usage: $0 [run|--debug|--logs|--telemetry|--verify] [desktop options]" >&2; exit 2;; esac
HAS_DATA_DIR=0
for arg in "$@"; do
  case "$arg" in --data-dir|--backend-data-dir) HAS_DATA_DIR=1;; esac
done
if [[ "$HAS_DATA_DIR" == "0" ]]; then
  # A running installed app owns its database and global Fn handling. Keep a
  # default development run independent; explicit data arguments opt out.
  set -- --data-dir "$ROOT/.build/rust-desktop/development-data" --no-import --no-hotkeys "$@"
fi
TOOLCHAIN="${VOCAL_MORE_RUST_TOOLCHAIN:-1.98.1}"
APP="$ROOT/.build/rust-desktop/Vocal More Dev.app"
# Terminate only this managed development app; preserve the installed product.
while read -r pid; do
  if [[ -n "$pid" && "$(ps -p "$pid" -o comm=)" == "$APP/Contents/MacOS/Vocal More" ]]; then
    kill -TERM "$pid"
  fi
done < <(pgrep -f "^$APP/Contents/MacOS/Vocal More( |$)" || true)
export MACOSX_DEPLOYMENT_TARGET=14.0
cargo "+$TOOLCHAIN" build --locked --manifest-path "$ROOT/rust/Cargo.toml" -p vocal-more-desktop -p vocal-more-backend \
  --bin vocal-more-desktop --bin vocal-more-backend
"$ROOT/scripts/build_native_audio.sh" --output "$ROOT/.build/rust-desktop/libvocal_more_audio.dylib"
python3 "$ROOT/packaging/macos/stage_rust_app.py" --development --app "$APP" \
  --binary "$ROOT/rust/target/debug/vocal-more-desktop" \
  --backend "$ROOT/rust/target/debug/vocal-more-backend" \
  --native "$ROOT/.build/rust-desktop/libvocal_more_audio.dylib"
codesign --force --sign - "$APP/Contents/Frameworks/libvocal_more_audio.dylib"
codesign --force --entitlements "$ROOT/packaging/macos/entitlements.plist" --sign - "$APP"
case "$MODE" in
  --debug) exec lldb -- "$APP/Contents/MacOS/Vocal More" "$@";;
  *) open -n "$APP" --stdout "$ROOT/.build/rust-desktop/stdout.log" --stderr "$ROOT/.build/rust-desktop/stderr.log" --args --show-settings "$@";;
esac
case "$MODE" in
  --logs|--telemetry) exec tail -f "$ROOT/.build/rust-desktop/stderr.log";;
  --verify) sleep 1; pgrep -f "^$APP/Contents/MacOS/Vocal More( |$)" >/dev/null;;
esac
