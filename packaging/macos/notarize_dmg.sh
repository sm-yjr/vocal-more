#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(python3 "$ROOT/packaging/macos/read_version.py")"
DMG="${1:-$ROOT/dist/Vocal-More-${VERSION}.dmg}"
KEYCHAIN_PROFILE="${VOCAL_MORE_NOTARY_PROFILE:-}"
KEYCHAIN="${VOCAL_MORE_NOTARY_KEYCHAIN:-}"

if [[ ! -f "$DMG" ]]; then
  echo "DMG not found: $DMG" >&2
  exit 1
fi

if [[ -z "$KEYCHAIN_PROFILE" ]]; then
  echo "Missing notarization credentials." >&2
  echo "Set VOCAL_MORE_NOTARY_PROFILE to a notarytool keychain profile." >&2
  exit 1
fi

NOTARY_ARGS=(--keychain-profile "$KEYCHAIN_PROFILE")
if [[ -n "$KEYCHAIN" ]]; then
  NOTARY_ARGS+=(--keychain "$KEYCHAIN")
fi

xcrun notarytool submit "$DMG" "${NOTARY_ARGS[@]}" --wait

xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
if ! spctl -a -vv -t open --context context:primary-signature "$DMG"; then
  echo "Warning: spctl open validation failed; notarization and stapler validation succeeded." >&2
fi

echo "Notarized and stapled $DMG"
