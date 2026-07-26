#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_PYTHON="${VOCAL_MORE_BUILD_PYTHON:-}"
BUILD_VENV="$ROOT/packaging/macos/.venv-py2app"

rm -rf build dist
rm -rf "$ROOT/packaging/macos/build" "$ROOT/packaging/macos/dist"

if [[ -z "$BUILD_PYTHON" ]]; then
  for candidate in \
    /usr/local/bin/python3.12 \
    /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3.14 \
    /opt/homebrew/bin/python3.13; do
    if [[ -x "$candidate" ]]; then
      BUILD_PYTHON="$candidate"
      break
    fi
  done
fi

if [[ -z "$BUILD_PYTHON" ]] && command -v uv >/dev/null 2>&1; then
  BUILD_PYTHON="$(uv python find 3.12 2>/dev/null || true)"
fi

if [[ ! -x "$BUILD_PYTHON" ]]; then
  echo "Build Python not found: $BUILD_PYTHON" >&2
  echo "Set VOCAL_MORE_BUILD_PYTHON to a Python.org/Homebrew framework Python." >&2
  exit 1
fi

rm -rf "$BUILD_VENV"

VOCAL_MORE_BUILD_PYTHON="$BUILD_PYTHON" "$ROOT/packaging/macos/make_icon.sh"

"$BUILD_PYTHON" -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$BUILD_VENV/bin/python" -m pip install -e "$ROOT" py2app 'setuptools<70'

cd "$ROOT/packaging/macos"

"$BUILD_VENV/bin/python" setup.py py2app

rm -rf "$ROOT/dist"
mkdir -p "$ROOT/dist"
cp -R "$ROOT/packaging/macos/dist/Vocal More.app" "$ROOT/dist/"

APP="$ROOT/dist/Vocal More.app"
SPARKLE_ROOT="$("$ROOT/packaging/macos/install_sparkle.sh")"
SPARKLE_FRAMEWORK="$APP/Contents/Frameworks/Sparkle.framework"
mkdir -p "$APP/Contents/Frameworks"
ditto "$SPARKLE_ROOT/Sparkle.framework" "$SPARKLE_FRAMEWORK"
ditto "$SPARKLE_ROOT/LICENSE" "$APP/Contents/Resources/Sparkle-LICENSE.txt"
ditto "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE.txt"

if [[ "${VOCAL_MORE_SKIP_ADHOC_SIGN:-0}" != "1" ]]; then
  "$ROOT/packaging/macos/sign_sparkle.sh" "$SPARKLE_FRAMEWORK" - 0

  while IFS= read -r file; do
    codesign --force --options runtime \
      --sign - "$file" >/dev/null
  done < <(
    find "$APP/Contents" -path "$SPARKLE_FRAMEWORK/*" -prune -o -type f -print0 |
      xargs -0 file |
      awk -F: '/Mach-O/ { sub(/ [(]for architecture .*/, "", $1); print $1 }' |
      sort -ru
  )

  codesign --force --options runtime \
    --entitlements "$ROOT/packaging/macos/entitlements.plist" \
    --sign - "$APP"
fi

echo "Built dist/Vocal More.app"
