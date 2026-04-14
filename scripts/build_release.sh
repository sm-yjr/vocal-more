#!/bin/bash
set -euo pipefail

# ── Paths ──────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
XCODE_PROJECT="$PROJECT_DIR/VocalMore/VocalMore.xcodeproj"
SCHEME="VocalMore"
BUILD_DIR="$PROJECT_DIR/build"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
SPEC_FILE="$PROJECT_DIR/vocal_more_serve.spec"

PYINSTALLER_DIST="$BUILD_DIR/pyinstaller/dist"
PYINSTALLER_BUILD="$BUILD_DIR/pyinstaller/build"
DERIVED_DATA="$BUILD_DIR/derived"
DMG_PATH="$BUILD_DIR/VocalMore.dmg"

# Read version from Xcode project
APP_VERSION=$(grep -A1 'MARKETING_VERSION' "$XCODE_PROJECT/project.pbxproj" | grep -o '[0-9]*\.[0-9]*\.[0-9]*' | head -1)
APP_VERSION="${APP_VERSION:-0.1.0}"
ZIP_NAME="VocalMore-${APP_VERSION}.zip"
ZIP_PATH="$BUILD_DIR/$ZIP_NAME"

# Sparkle tools (resolved via SPM)
SPARKLE_BIN="$DERIVED_DATA/SourcePackages/artifacts/sparkle/Sparkle/bin"

# Remote publish config
UPLOAD_BIN="${UPLOAD_BIN:-}"
UPLOAD_REGION="${UPLOAD_REGION:-}"
PUBLISH_BASE_URL="${PUBLISH_BASE_URL:-}"
ARCHIVE_UPLOAD_TARGET="${ARCHIVE_UPLOAD_TARGET:-}"
APPCAST_UPLOAD_TARGET="${APPCAST_UPLOAD_TARGET:-}"
APPCAST_URL="${APPCAST_URL:-${PUBLISH_BASE_URL}/appcast.xml}"
ARCHIVE_PUBLIC_URL="${ARCHIVE_PUBLIC_URL:-${PUBLISH_BASE_URL}/releases/${ZIP_NAME}}"

# ── Preflight checks ──────────────────────────────────────────────
echo "==> Checking prerequisites..."
echo "    Version: $APP_VERSION"

require_env() {
    local var_name="$1"
    if [ -z "${!var_name:-}" ]; then
        echo "ERROR: Required environment variable $var_name is not set"
        exit 1
    fi
}

require_env UPLOAD_BIN
require_env PUBLISH_BASE_URL
require_env ARCHIVE_UPLOAD_TARGET
require_env APPCAST_UPLOAD_TARGET

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: .venv not found. Run: uv venv && uv pip install -e '.[dev]'"
    exit 1
fi

if ! command -v xcodebuild &>/dev/null; then
    echo "ERROR: xcodebuild not found. Install Xcode."
    exit 1
fi

if ! command -v create-dmg &>/dev/null; then
    echo "ERROR: create-dmg not found. Run: brew install create-dmg"
    exit 1
fi

if [ ! -x "$UPLOAD_BIN" ]; then
    echo "ERROR: upload tool not found at $UPLOAD_BIN"
    exit 1
fi

if ! command -v curl &>/dev/null; then
    echo "ERROR: curl not found"
    exit 1
fi

# ── Step 1: Install PyInstaller if needed ──────────────────────────
if ! "$VENV_PYTHON" -c "import PyInstaller" 2>/dev/null; then
    echo "==> Installing PyInstaller..."
    uv pip install pyinstaller --quiet
fi

# ── Step 2: Bundle Python backend ──────────────────────────────────
echo "==> Building Python backend with PyInstaller..."
rm -rf "$PYINSTALLER_DIST" "$PYINSTALLER_BUILD"

"$VENV_PYTHON" -m PyInstaller "$SPEC_FILE" \
    --distpath "$PYINSTALLER_DIST" \
    --workpath "$PYINSTALLER_BUILD" \
    --clean --noconfirm

BACKEND_DIR="$PYINSTALLER_DIST/vocal-more-backend"
if [ ! -f "$BACKEND_DIR/vocal-more-backend" ]; then
    echo "ERROR: PyInstaller output not found at $BACKEND_DIR"
    exit 1
fi

BACKEND_SIZE=$(du -sh "$BACKEND_DIR" | cut -f1)
echo "    Backend size: $BACKEND_SIZE"

# ── Step 3: Quick smoke test ───────────────────────────────────────
echo "==> Smoke testing backend..."
SMOKE_RESULT=$( (echo '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'; sleep 2) | \
    "$BACKEND_DIR/vocal-more-backend" 2>/dev/null || true)

if echo "$SMOKE_RESULT" | grep -q '"jsonrpc"'; then
    echo "    Smoke test passed"
else
    echo "WARNING: Smoke test did not return JSON-RPC response (may still work)"
fi

# ── Step 4: Build Swift app ────────────────────────────────────────
echo "==> Building Swift app..."
xcodebuild -project "$XCODE_PROJECT" \
    -scheme "$SCHEME" \
    -configuration Release \
    -derivedDataPath "$DERIVED_DATA" \
    -quiet \
    CODE_SIGN_IDENTITY="-" \
    CODE_SIGNING_REQUIRED=NO \
    CODE_SIGNING_ALLOWED=NO

APP_PATH="$DERIVED_DATA/Build/Products/Release/VocalMore.app"
if [ ! -d "$APP_PATH" ]; then
    echo "ERROR: VocalMore.app not found"
    exit 1
fi

# ── Step 5: Embed Python backend into .app ─────────────────────────
echo "==> Embedding backend into VocalMore.app..."
RESOURCES="$APP_PATH/Contents/Resources"
rm -rf "$RESOURCES/vocal-more-backend"
cp -R "$BACKEND_DIR" "$RESOURCES/"

# ── Step 6: Ad-hoc code sign ──────────────────────────────────────
echo "==> Ad-hoc signing..."
codesign --force --deep -s - "$APP_PATH"

APP_SIZE=$(du -sh "$APP_PATH" | cut -f1)
echo "    App size: $APP_SIZE"

# ── Step 7: Create DMG ────────────────────────────────────────────
echo "==> Creating DMG..."
rm -f "$DMG_PATH"

create-dmg \
    --volname "VocalMore" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "VocalMore.app" 150 190 \
    --app-drop-link 450 190 \
    --hide-extension "VocalMore.app" \
    --no-internet-enable \
    "$DMG_PATH" \
    "$APP_PATH"

DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)

# ── Step 8: Create Sparkle update archive ─────────────────────────
echo "==> Creating Sparkle update archive..."
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

ZIP_SIZE=$(du -sh "$ZIP_PATH" | cut -f1)
echo "    Archive: $ZIP_PATH ($ZIP_SIZE)"

# Sign the archive with Sparkle EdDSA key
if [ -f "$SPARKLE_BIN/sign_update" ]; then
    echo "==> Signing archive with Sparkle EdDSA..."
    SPARKLE_SIG=$("$SPARKLE_BIN/sign_update" "$ZIP_PATH" 2>&1)
    echo "    Signature: $SPARKLE_SIG"
else
    echo "WARNING: Sparkle sign_update not found, skipping signature"
    SPARKLE_SIG=""
fi

# ── Step 9: Generate appcast.xml ──────────────────────────────────
echo "==> Generating appcast.xml..."

ED_SIGNATURE=$(echo "$SPARKLE_SIG" | grep -o 'sparkle:edSignature="[^"]*"' | cut -d'"' -f2 || true)
FILE_LENGTH=$(stat -f%z "$ZIP_PATH" 2>/dev/null || stat -c%s "$ZIP_PATH" 2>/dev/null)

APPCAST_PATH="$BUILD_DIR/appcast.xml"
cat > "$APPCAST_PATH" <<XMLEOF
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>VocalMore</title>
    <link>${APPCAST_URL}</link>
    <language>zh-CN</language>
    <item>
      <title>VocalMore ${APP_VERSION}</title>
      <pubDate>$(date -R)</pubDate>
      <sparkle:version>${APP_VERSION}</sparkle:version>
      <sparkle:shortVersionString>${APP_VERSION}</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>14.0</sparkle:minimumSystemVersion>
      <enclosure
        url="${ARCHIVE_PUBLIC_URL}"
        length="${FILE_LENGTH}"
        type="application/octet-stream"
        sparkle:edSignature="${ED_SIGNATURE}"
      />
    </item>
  </channel>
</rss>
XMLEOF

echo "    Appcast: $APPCAST_PATH"

# ── Step 10: Publish release assets ───────────────────────────────
echo "==> Uploading release assets..."
UPLOAD_ARGS=()
if [ -n "$UPLOAD_REGION" ]; then
    UPLOAD_ARGS+=(--region "$UPLOAD_REGION")
fi
"$UPLOAD_BIN" cp "$ZIP_PATH" "$ARCHIVE_UPLOAD_TARGET" "${UPLOAD_ARGS[@]}"
yes | "$UPLOAD_BIN" cp "$APPCAST_PATH" "$APPCAST_UPLOAD_TARGET" "${UPLOAD_ARGS[@]}"
echo "    Uploaded: $ARCHIVE_UPLOAD_TARGET"
echo "    Uploaded: $APPCAST_UPLOAD_TARGET"

# ── Step 11: Verify published appcast ─────────────────────────────
echo "==> Verifying published appcast..."
REMOTE_APPCAST=$(curl -fsSL "$APPCAST_URL")
if echo "$REMOTE_APPCAST" | grep -q "<sparkle:version>${APP_VERSION}</sparkle:version>"; then
    echo "    Remote appcast version confirmed: $APP_VERSION"
else
    echo "ERROR: Remote appcast does not contain version $APP_VERSION"
    exit 1
fi

# ── Done ───────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo "  Release complete! (v${APP_VERSION})"
echo "  DMG: $DMG_PATH ($DMG_SIZE)"
echo "  App: $APP_SIZE"
echo "  Backend: $BACKEND_SIZE"
echo "  Update zip: $ZIP_PATH ($ZIP_SIZE)"
echo "  Appcast: $APPCAST_PATH"
echo "  Published appcast: $APPCAST_URL"
echo "  Published zip: ${ARCHIVE_PUBLIC_URL}"
echo "========================================="
