#!/usr/bin/env bash
set -euo pipefail

FRAMEWORK="${1:?Usage: sign_sparkle.sh <Sparkle.framework> <identity> [timestamp]}"
IDENTITY="${2:?Usage: sign_sparkle.sh <Sparkle.framework> <identity> [timestamp]}"
USE_TIMESTAMP="${3:-0}"

if [[ ! -d "$FRAMEWORK" ]]; then
  echo "Sparkle framework not found: $FRAMEWORK" >&2
  exit 1
fi

sign_target() {
  local target="$1"
  shift
  local args=(--force --options runtime)
  if [[ "$USE_TIMESTAMP" == "1" ]]; then
    args+=(--timestamp)
  fi
  codesign "${args[@]}" "$@" --sign "$IDENTITY" "$target" >/dev/null
}

# Sparkle's nested services must be signed inside-out in this order.
sign_target "$FRAMEWORK/Versions/Current/XPCServices/Installer.xpc"
sign_target "$FRAMEWORK/Versions/Current/XPCServices/Downloader.xpc" \
  --preserve-metadata=entitlements
sign_target "$FRAMEWORK/Versions/Current/Autoupdate"
sign_target "$FRAMEWORK/Versions/Current/Updater.app"
sign_target "$FRAMEWORK"
