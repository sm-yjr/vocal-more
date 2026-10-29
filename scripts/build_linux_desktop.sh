#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE=release
if [[ "${1:-}" == --debug ]]; then PROFILE=debug; shift; fi
[[ $# == 0 ]] || { echo 'Usage: build_linux_desktop.sh [--debug]' >&2; exit 2; }
[[ "$(uname -s)" == Linux ]] || { echo 'Linux is required.' >&2; exit 1; }
CARGO="${CARGO:-cargo}"
CARGO_FLAGS=()
if [[ "$PROFILE" == release ]]; then CARGO_FLAGS+=(--release); fi
"$CARGO" build --locked --manifest-path "$ROOT/rust/Cargo.toml" -p vocal-more-desktop \
  --bin vocal-more-desktop "${CARGO_FLAGS[@]}"
bash "$ROOT/scripts/build_linux_audio.sh"
VERSION="$(PYTHONPATH="$ROOT/packaging" python3 - "$ROOT" <<'PY'
from pathlib import Path
from release.model import Version
import sys, tomllib
project = tomllib.loads((Path(sys.argv[1]) / 'pyproject.toml').read_text())
print(Version.project(project['project']['version'],project['tool']['vocal-more']['build']).text)
PY
)"
NAME="Vocal-More-linux-dev-$VERSION-$(uname -m)"
OUTPUT="$ROOT/.build/linux"
mkdir -p "$OUTPUT"
TEMP="$(mktemp -d "$OUTPUT/stage.XXXXXXXX")"
trap 'rm -rf -- "$TEMP"' EXIT
STAGE="$TEMP/$NAME"
mkdir -p "$STAGE/bin" "$STAGE/lib/vocal-more" "$STAGE/notices"
cp "$ROOT/rust/target/$PROFILE/vocal-more-desktop" "$STAGE/bin/"
# A stripped development build can still be packaged without an 850 MB symbol file.
strip "$STAGE/bin/vocal-more-desktop"
cp "$ROOT/.build/native/libvocal_more_audio.so" "$STAGE/lib/vocal-more/"
cp "$ROOT/packaging/linux/"{vocal-more,install.sh,io.github.sm_yjr.VocalMore.svg} "$STAGE/"
chmod +x "$STAGE/"{vocal-more,install.sh}
cp "$ROOT/LICENSE" "$STAGE/LICENSE.txt"
cp "$ROOT/rust/crates/desktop/assets/fonts/OFL.txt" "$STAGE/notices/Geist-OFL.txt"
cp "$ROOT/resources/settings/SHADCN-UI-LICENSE.txt" "$STAGE/notices/"
cp "$ROOT/resources/settings/THIRD-PARTY-LICENSES.md" "$STAGE/notices/"
cp "$ROOT/docs/linux.md" "$STAGE/README.md"
mkdir -p "$STAGE/gnome-extension/vocal-more@sm-yjr"
cp "$ROOT/packaging/linux/gnome-extension/vocal-more@sm-yjr/"{extension.js,protocol.js,stylesheet.css,metadata.json} "$STAGE/gnome-extension/vocal-more@sm-yjr/"
# Corresponding source includes uncommitted development work; ignored files,
# runtime data, credentials, target/ and old local packaging remnants are excluded.
(
  cd "$ROOT"
  git ls-files --cached --others --exclude-standard -z -- rust native scripts src tests packaging resources docs .github pyproject.toml uv.lock README.md AGENTS.md LICENSE |
    tar --null -czf "$STAGE/source.tar.gz" -T -
)
python3 - "$ROOT" "$STAGE" "$PROFILE" <<'PY'
import hashlib, json, platform, subprocess, sys, tomllib
from pathlib import Path
root, stage = map(Path,sys.argv[1:3])
files = ['bin/vocal-more-desktop','lib/vocal-more/libvocal_more_audio.so','source.tar.gz']
files += sorted(str(path.relative_to(stage)) for path in (stage/'gnome-extension').rglob('*') if path.is_file())
manifest = {
    'kind':'linux-development','base':'v0.6.1','profile':sys.argv[3],
    'source_commit':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),
    'working_tree_dirty':bool(subprocess.check_output(['git','-C',str(root),'status','--porcelain'])),
    'architecture':platform.machine(),
    'gpui_kit':tomllib.loads((root/'rust/crates/desktop/Cargo.toml').read_text())['dependencies']['gpui-kit'].removeprefix('='),
    'sha256':{name:hashlib.sha256((stage/name).read_bytes()).hexdigest() for name in files},
}
(stage/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
PY
"$STAGE/vocal-more" --version
tar -C "$TEMP" -czf "$OUTPUT/$NAME.tar.gz" "$NAME"
printf 'Linux development archive: %s\n' "$OUTPUT/$NAME.tar.gz"

python3 "$ROOT/packaging/linux/build_deb.py" "$STAGE" --output "$OUTPUT"
