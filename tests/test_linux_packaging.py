from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_linux_deb_declares_gnome_50_and_preserves_user_data():
    build = (ROOT / "packaging/linux/build_deb.sh").read_text(encoding="utf-8")
    postrm = (ROOT / "packaging/linux/postrm").read_text(encoding="utf-8")

    assert "Architecture: amd64" in build
    assert "gnome-shell (>= 50)" in build
    assert "gnome-shell (<< 51)" in build
    assert "LICENSE" in build
    assert "XDG_CONFIG_HOME" in postrm
    assert "rm -rf" not in postrm


def test_linux_desktop_and_dbus_activation_use_the_same_app_id():
    desktop = (ROOT / "packaging/linux/com.sm_yjr.VocalMore.desktop").read_text(
        encoding="utf-8"
    )
    service = (ROOT / "packaging/linux/com.sm_yjr.VocalMore.service").read_text(
        encoding="utf-8"
    )

    assert "Exec=vocal-more --settings" in desktop
    assert "Name=com.sm_yjr.VocalMore" in service
    assert "Exec=/usr/bin/vocal-more --service" in service
