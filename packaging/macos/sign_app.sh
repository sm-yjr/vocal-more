#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP="${1:-$ROOT/dist/Vocal More.app}"
ENTITLEMENTS="$ROOT/packaging/macos/entitlements.plist"
IDENTITY="${VOCAL_MORE_SIGN_IDENTITY:-}"

if [[ ! -d "$APP" ]]; then
  echo "App bundle not found: $APP" >&2
  exit 1
fi

if [[ -z "$IDENTITY" ]]; then
  IDENTITY="$(
    security find-identity -p codesigning -v |
      awk -F '"' '/Developer ID Application/ { print $2; exit }'
  )"
fi

if [[ -z "$IDENTITY" ]]; then
  echo "Developer ID Application signing identity was not found." >&2
  echo "Install one in Keychain Access, or set VOCAL_MORE_SIGN_IDENTITY." >&2
  exit 1
fi

echo "Signing $APP"
echo "Identity: $IDENTITY"

while IFS= read -r file; do
  codesign --force --timestamp --options runtime \
    --sign "$IDENTITY" "$file" >/dev/null
done < <(
  find "$APP/Contents" -type f -print0 |
    xargs -0 file |
    awk -F: '/Mach-O/ { sub(/ [(]for architecture .*/, "", $1); print $1 }' |
    sort -ru
)

codesign --force --timestamp --options runtime \
  --entitlements "$ENTITLEMENTS" \
  --sign "$IDENTITY" "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"
spctl -a -vv --type execute "$APP" || true

echo "Signed $APP"
