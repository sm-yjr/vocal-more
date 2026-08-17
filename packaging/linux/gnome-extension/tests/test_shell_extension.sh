#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXTENSION_DIR="$SCRIPT_DIR/../vocal-more@sm-yjr.com"
PACK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/vocal-more-gnome-pack.XXXXXX")
trap 'rm -rf "$PACK_DIR"' EXIT

command -v dbus-run-session >/dev/null
command -v gnome-extensions >/dev/null
command -v gnome-shell-test-tool >/dev/null
command -v timeout >/dev/null
command -v unzip >/dev/null

(
    cd "$EXTENSION_DIR"
    gnome-extensions pack \
        --force \
        --out-dir "$PACK_DIR" \
        --extra-source=dbusClient.js \
        --extra-source=gesture.js \
        --extra-source=capsule.js \
        --extra-source=panelMenu.js \
        .
)

ARCHIVE="$PACK_DIR/vocal-more@sm-yjr.com.shell-extension.zip"
test -f "$ARCHIVE"
for module in dbusClient.js gesture.js capsule.js panelMenu.js; do
    unzip -Z1 "$ARCHIVE" | grep -Fxq "$module"
done

timeout "${VOCAL_MORE_GNOME_SHELL_TIMEOUT:-60}" \
    dbus-run-session -- \
    gnome-shell-test-tool \
        --headless \
        --extension "$ARCHIVE" \
        "$SCRIPT_DIR/automation_smoke.js"
