#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Per-user install; preserves configuration and recording history on updates.
set -euo pipefail
BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINATION="${XDG_DATA_HOME:-$HOME/.local/share}/vocal-more-app"
BIN_DIRECTORY="$HOME/.local/bin"
APPLICATIONS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$DESTINATION" "$BIN_DIRECTORY" "$APPLICATIONS" "$ICONS"
# Refuse a running instance rather than replacing a mapped executable/library.
if pgrep -u "$(id -u)" -f -- "$DESTINATION/bin/vocal-more-desktop" >/dev/null; then
  echo 'Quit the installed Vocal More before installing an update.' >&2
  exit 1
fi
cp -a "$BUNDLE/bin" "$BUNDLE/lib" "$BUNDLE/vocal-more" "$BUNDLE/LICENSE.txt" \
  "$BUNDLE/notices" "$BUNDLE/build-manifest.json" "$BUNDLE/source.tar.gz" "$BUNDLE/README.md" "$DESTINATION/"
EXTENSIONS="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions"
mkdir -p "$EXTENSIONS"
cp -a "$BUNDLE/gnome-extension/vocal-more@sm-yjr" "$EXTENSIONS/"
ln -sfn "$DESTINATION/vocal-more" "$BIN_DIRECTORY/vocal-more"
cp "$BUNDLE/io.github.sm_yjr.VocalMore.svg" "$ICONS/"
# Quote the actual executable path so installation works when HOME contains spaces.
python3 - "$DESTINATION/vocal-more" "$APPLICATIONS/io.github.sm_yjr.VocalMore.desktop" <<'PY'
from pathlib import Path
import sys
path = sys.argv[1].replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
Path(sys.argv[2]).write_text('[Desktop Entry]\nType=Application\nVersion=1.0\nName=Vocal More\nComment=Low-voice dictation\nExec="' + path + '"\nIcon=io.github.sm_yjr.VocalMore\nTerminal=false\nCategories=AudioVideo;Audio;\nStartupWMClass=io.github.sm_yjr.VocalMore\n')
PY
if command -v update-desktop-database >/dev/null; then update-desktop-database "$APPLICATIONS"; fi
printf 'Installed: %s\nShortcut command: %s --command toggle\n' "$DESTINATION/vocal-more" "$BIN_DIRECTORY/vocal-more"
printf 'GNOME Wayland capsule: log out/in once, then enable Vocal More Capsule in Extensions.\n'
