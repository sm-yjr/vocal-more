#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/../.." && pwd)"
version="$(python3 "$project_root/packaging/macos/read_version.py")"
build_root="$project_root/build/linux-deb"
package_root="$build_root/vocal-more_${version}_amd64"
output="$project_root/dist/vocal-more_${version}_amd64.deb"
extension_source="$script_dir/gnome-extension/vocal-more@sm-yjr.com"

if [[ ! -d "$extension_source" ]]; then
  echo "GNOME extension source is missing: $extension_source" >&2
  exit 1
fi

mkdir -p "$project_root/dist"
rm -rf "$build_root"
mkdir -p \
  "$package_root/DEBIAN" \
  "$package_root/opt/vocal-more/lib" \
  "$package_root/usr/bin" \
  "$package_root/usr/share/applications" \
  "$package_root/usr/share/dbus-1/services" \
  "$package_root/usr/share/doc/vocal-more" \
  "$package_root/usr/share/glib-2.0/schemas" \
  "$package_root/usr/share/gnome-shell/extensions" \
  "$package_root/usr/share/icons/hicolor/256x256/apps"

cat > "$package_root/DEBIAN/control" <<EOF
Package: vocal-more
Version: $version
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Vocal More contributors
Depends: python3 (>= 3.14), python3-gi, gir1.2-gtk-4.0, gir1.2-atspi-2.0, libportaudio2, libsndfile1, flac, gnome-shell (>= 50), gnome-shell (<< 51), dconf-gsettings-backend
Description: Low-voice dictation for Ubuntu GNOME Wayland
 Vocal More provides realtime dictation, text polishing, meeting notes,
 a GNOME Shell capsule, and privacy-safe automatic paste integration.
EOF

cp "$script_dir/postinst" "$package_root/DEBIAN/postinst"
cp "$script_dir/postrm" "$package_root/DEBIAN/postrm"
chmod 0755 "$package_root/DEBIAN/postinst" "$package_root/DEBIAN/postrm"

uv pip install \
  --target "$package_root/opt/vocal-more/lib" \
  --no-cache \
  "$project_root"

cp "$script_dir/vocal-more-launcher" "$package_root/usr/bin/vocal-more"
chmod 0755 "$package_root/usr/bin/vocal-more"
cp "$script_dir/com.sm_yjr.VocalMore.desktop" "$package_root/usr/share/applications/"
cp "$script_dir/com.sm_yjr.VocalMore.service" "$package_root/usr/share/dbus-1/services/"
cp "$project_root/LICENSE" "$package_root/usr/share/doc/vocal-more/LICENSE"
cp "$project_root/assets/logo.png" "$package_root/usr/share/icons/hicolor/256x256/apps/com.sm_yjr.VocalMore.png"
cp -a "$extension_source" "$package_root/usr/share/gnome-shell/extensions/"
glib-compile-schemas \
  "$package_root/usr/share/gnome-shell/extensions/vocal-more@sm-yjr.com/schemas"

schema="$extension_source/schemas/org.gnome.shell.extensions.vocal-more.gschema.xml"
if [[ ! -f "$schema" ]]; then
  echo "GNOME extension GSettings schema is missing: $schema" >&2
  exit 1
fi
cp "$schema" "$package_root/usr/share/glib-2.0/schemas/"

find "$package_root" -type d -exec chmod 0755 {} +
find "$package_root" -type f -exec chmod 0644 {} +
chmod 0755 \
  "$package_root/DEBIAN/postinst" \
  "$package_root/DEBIAN/postrm" \
  "$package_root/usr/bin/vocal-more"
dpkg-deb --root-owner-group --build "$package_root" "$output"
dpkg-deb --info "$output"
echo "$output"
