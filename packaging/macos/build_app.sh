#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_PYTHON="${VOCAL_MORE_BUILD_PYTHON:-}"
BUILD_VENV="${VOCAL_MORE_BUILD_VENV:-$ROOT/packaging/macos/.venv-py2app}"
TARGET_ARCH="${VOCAL_MORE_TARGET_ARCH:-$(uname -m)}"

python3 "$ROOT/packaging/macos/validate_release_features.py"

if [[ "${VOCAL_MORE_SKIP_FRONTEND_BUILD:-0}" != "1" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to build the React settings frontend." >&2
    exit 1
  fi

  npm --prefix "$ROOT/frontend/settings" ci
  npm --prefix "$ROOT/frontend/settings" run build
elif [[ ! -f "$ROOT/resources/settings/settings.html" ]]; then
  echo "The prebuilt settings frontend is missing." >&2
  exit 1
fi

rm -rf "$ROOT/build" "$ROOT/dist"
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

VOCAL_MORE_BUILD_PYTHON="$BUILD_PYTHON" "$ROOT/packaging/macos/make_icon.sh"

if [[ "${VOCAL_MORE_USE_PREPARED_BUILD_VENV:-0}" == "1" ]]; then
  if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
    echo "Prepared build environment is missing: $BUILD_VENV" >&2
    exit 1
  fi
  if ! "$BUILD_VENV/bin/python" -c "import py2app, vocal_more" >/dev/null; then
    echo "Prepared build environment lacks py2app or vocal-more." >&2
    exit 1
  fi
else
  rm -rf "$BUILD_VENV"
  "$BUILD_PYTHON" -m venv "$BUILD_VENV"
  "$BUILD_VENV/bin/python" -m pip install --upgrade pip >/dev/null
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to install the locked macOS build environment." >&2
    exit 1
  fi
  (
    cd "$ROOT"
    uv export --frozen --no-dev --group packaging --no-hashes |
      "$BUILD_VENV/bin/python" -m pip install -r /dev/stdin
  )
fi

cd "$ROOT/packaging/macos"

"$BUILD_VENV/bin/python" setup.py py2app

rm -rf "$ROOT/dist"
mkdir -p "$ROOT/dist"
cp -R "$ROOT/packaging/macos/dist/Vocal More.app" "$ROOT/dist/"

APP="$ROOT/dist/Vocal More.app"
NATIVE_LIBRARY="$ROOT/build/native/libvocal_more_audio.dylib"
"$ROOT/scripts/build_native_audio.sh" --output "$NATIVE_LIBRARY"
export VOCAL_MORE_NATIVE_AUDIO_LIBRARY="$NATIVE_LIBRARY"
mkdir -p "$APP/Contents/Frameworks"
ditto "$NATIVE_LIBRARY" \
  "$APP/Contents/Frameworks/libvocal_more_audio.dylib"
"$BUILD_VENV/bin/python" \
  "$ROOT/packaging/macos/prune_app_bundle.py" \
  --target-arch "$TARGET_ARCH" \
  "$APP"
VOCAL_MORE_PACKAGING_SMOKE_TEST=1 \
  "$APP/Contents/MacOS/Vocal More"
SPARKLE_ROOT="$("$ROOT/packaging/macos/install_sparkle.sh")"
SPARKLE_FRAMEWORK="$APP/Contents/Frameworks/Sparkle.framework"
mkdir -p "$APP/Contents/Frameworks"
ditto "$SPARKLE_ROOT/Sparkle.framework" "$SPARKLE_FRAMEWORK"
ditto "$SPARKLE_ROOT/LICENSE" "$APP/Contents/Resources/Sparkle-LICENSE.txt"
ditto "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE.txt"
ditto "$ROOT/resources/settings/SHADCN-UI-LICENSE.txt" "$APP/Contents/Resources/Shadcn-UI-LICENSE.txt"

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
