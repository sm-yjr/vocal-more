from pathlib import Path
from struct import unpack
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_windows_spec_bundles_tk_ui_and_brand_icon():
    spec = (ROOT / "packaging" / "windows" / "vocal_more.spec").read_text(
        encoding="utf-8"
    )

    assert '"vocal_more.windows_desktop_ui"' in spec
    assert '"tkinter.ttk"' in spec
    assert 'datas = [(str(ICON), "resources/windows")]' in spec
    assert "icon=str(ICON)" in spec


def test_icon_generator_writes_png_compressed_ico(tmp_path):
    output = tmp_path / "VocalMore.ico"
    script = ROOT / "packaging" / "windows" / "make_icon.py"

    subprocess.run([sys.executable, str(script), str(output)], check=True)
    data = output.read_bytes()

    assert unpack("<HHH", data[:6]) == (0, 1, 1)
    assert data[22:30] == b"\x89PNG\r\n\x1a\n"


def test_inno_setup_is_per_user_and_preserves_app_data():
    setup = (ROOT / "packaging" / "windows" / "vocal_more.iss").read_text(
        encoding="utf-8"
    )

    assert "PrivilegesRequired=lowest" in setup
    assert "DefaultDirName={localappdata}\\Programs\\{#MyAppName}" in setup
    assert "UninstallDisplayIcon={app}\\{#MyAppExeName}" in setup
    assert 'Name: "startup"' in setup
    assert "%APPDATA%" not in setup


def test_installer_build_and_ci_cover_install_and_uninstall_smoke():
    script = (
        ROOT / "packaging" / "windows" / "build_installer.ps1"
    ).read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "windows.yml").read_text(
        encoding="utf-8"
    )

    assert "ISCC.exe" in script
    assert "windows-x64-setup.exe" in script
    assert "choco install innosetup" in workflow
    assert "Smoke-test silent installer and uninstaller" in workflow
    assert "unins000.exe" in workflow
