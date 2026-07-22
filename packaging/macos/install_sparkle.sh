#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPARKLE_VERSION="2.9.4"
SPARKLE_ARCHIVE="Sparkle-${SPARKLE_VERSION}.tar.xz"
SPARKLE_URL="https://github.com/sparkle-project/Sparkle/releases/download/${SPARKLE_VERSION}/${SPARKLE_ARCHIVE}"
SPARKLE_SHA256="ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9"

if [[ -n "${VOCAL_MORE_SPARKLE_ROOT:-}" ]]; then
  SPARKLE_ROOT="$VOCAL_MORE_SPARKLE_ROOT"
else
  SPARKLE_ROOT="$ROOT/packaging/macos/.sparkle/Sparkle-${SPARKLE_VERSION}"
fi

validate_installation() {
  [[ -d "$SPARKLE_ROOT/Sparkle.framework" ]] &&
    [[ -x "$SPARKLE_ROOT/bin/generate_appcast" ]]
}

if validate_installation; then
  printf '%s\n' "$SPARKLE_ROOT"
  exit 0
fi

if [[ -n "${VOCAL_MORE_SPARKLE_ROOT:-}" ]]; then
  echo "Invalid Sparkle installation: $SPARKLE_ROOT" >&2
  exit 1
fi

CACHE_DIR="$ROOT/packaging/macos/.sparkle/downloads"
ARCHIVE_PATH="$CACHE_DIR/$SPARKLE_ARCHIVE"
mkdir -p "$CACHE_DIR"

verify_archive() {
  [[ -f "$ARCHIVE_PATH" ]] &&
    printf '%s  %s\n' "$SPARKLE_SHA256" "$ARCHIVE_PATH" | shasum -a 256 -c - >/dev/null 2>&1
}

if ! verify_archive; then
  rm -f "$ARCHIVE_PATH"
  echo "Downloading Sparkle $SPARKLE_VERSION..." >&2
  curl --fail --location --retry 3 --output "$ARCHIVE_PATH" "$SPARKLE_URL"
fi

if ! verify_archive; then
  echo "Sparkle archive checksum verification failed." >&2
  rm -f "$ARCHIVE_PATH"
  exit 1
fi

STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vocal-more-sparkle.XXXXXX")"
cleanup() {
  rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

tar -xJf "$ARCHIVE_PATH" -C "$STAGING_DIR"
if [[ ! -d "$STAGING_DIR/Sparkle.framework" ]] ||
  [[ ! -x "$STAGING_DIR/bin/generate_appcast" ]]; then
  echo "Downloaded Sparkle archive is incomplete." >&2
  exit 1
fi

rm -rf "$SPARKLE_ROOT"
mkdir -p "$(dirname "$SPARKLE_ROOT")"
mv "$STAGING_DIR" "$SPARKLE_ROOT"
trap - EXIT

printf '%s\n' "$SPARKLE_ROOT"
