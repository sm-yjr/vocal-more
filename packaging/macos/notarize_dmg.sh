#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$("$ROOT/.venv/bin/python" -c 'from vocal_more import __version__; print(__version__)')"
DMG="${1:-$ROOT/dist/Vocal-More-${VERSION}.dmg}"
KEYCHAIN_PROFILE="${VOCAL_MORE_NOTARY_PROFILE:-}"

if [[ ! -f "$DMG" ]]; then
  echo "DMG not found: $DMG" >&2
  exit 1
fi

if [[ -z "$KEYCHAIN_PROFILE" ]]; then
  echo "Missing notarization credentials." >&2
  echo "Set VOCAL_MORE_NOTARY_PROFILE to a notarytool keychain profile." >&2
  exit 1
fi

xcrun notarytool submit "$DMG" \
  --keychain-profile "$KEYCHAIN_PROFILE" \
  --wait

xcrun stapler staple "$DMG"
spctl -a -vv -t open --context context:primary-signature "$DMG"

echo "Notarized and stapled $DMG"
