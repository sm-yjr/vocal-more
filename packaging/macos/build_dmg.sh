#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP="$ROOT/dist/Vocal More.app"
STAGING="$ROOT/dist/dmg-staging"
VERSION="$("$ROOT/.venv/bin/python" -c 'from vocal_more import __version__; print(__version__)')"
DMG="$ROOT/dist/Vocal-More-${VERSION}.dmg"
VOLNAME="Vocal More"
IDENTITY="${VOCAL_MORE_SIGN_IDENTITY:-}"

"$ROOT/packaging/macos/build_app.sh"

if [[ -z "$IDENTITY" ]]; then
  IDENTITY="$(
    security find-identity -p codesigning -v |
      awk -F '"' '/Developer ID Application/ { print $2; exit }'
  )"
fi

if [[ -n "$IDENTITY" ]]; then
  VOCAL_MORE_SIGN_IDENTITY="$IDENTITY" "$ROOT/packaging/macos/sign_app.sh" "$APP"
elif [[ "${VOCAL_MORE_ALLOW_UNSIGNED_DMG:-0}" == "1" ]]; then
  echo "Warning: creating an unsigned local-test DMG because VOCAL_MORE_ALLOW_UNSIGNED_DMG=1." >&2
else
  echo "Developer ID Application signing identity was not found." >&2
  echo "A distributable DMG requires Developer ID signing and notarization." >&2
  echo "For a local-test DMG only, rerun with VOCAL_MORE_ALLOW_UNSIGNED_DMG=1." >&2
  exit 1
fi

rm -rf "$STAGING" "$DMG"
mkdir -p "$STAGING"
ditto "$APP" "$STAGING/Vocal More.app"
ln -s /Applications "$STAGING/Applications"

hdiutil create \
  -volname "$VOLNAME" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$DMG"

rm -rf "$STAGING"

if [[ -n "$IDENTITY" ]]; then
  codesign --force --timestamp --sign "$IDENTITY" "$DMG"
  codesign --verify --verbose=2 "$DMG"
fi

echo "Built $DMG"
