"""macOS bundle metadata needed for first-run system permissions."""

from __future__ import annotations

import ast
import plistlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_py2app_plist() -> dict[str, ast.AST]:
    tree = ast.parse((ROOT / "packaging" / "macos" / "setup.py").read_text())

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "APP"
            for target in node.targets
        ):
            continue
        app_entry = node.value.elts[0]
        for key, value in zip(app_entry.keys, app_entry.values):
            if isinstance(key, ast.Constant) and key.value == "plist":
                return {
                    plist_key.value: plist_value
                    for plist_key, plist_value in zip(value.keys, value.values)
                    if isinstance(plist_key, ast.Constant)
                }

    raise AssertionError("APP plist was not found in packaging/macos/setup.py")


def test_py2app_declares_microphone_usage_description():
    app_plist = _load_py2app_plist()

    usage = app_plist["NSMicrophoneUsageDescription"]
    assert isinstance(usage, ast.Constant)
    assert usage.value


def test_py2app_includes_accessibility_modules_for_dictionary_learning():
    setup_text = (ROOT / "packaging" / "macos" / "setup.py").read_text()

    assert '"ApplicationServices"' in setup_text
    assert '"CoreFoundation"' in setup_text


def test_py2app_declares_signed_sparkle_feed():
    app_plist = _load_py2app_plist()

    feed_url = app_plist["SUFeedURL"]
    public_key = app_plist["SUPublicEDKey"]
    assert isinstance(feed_url, ast.Constant)
    assert feed_url.value.endswith("/sparkle-feed/appcast.xml")
    assert isinstance(public_key, ast.Constant)
    assert public_key.value == "rX4Sp1huP0v763afpuPlVkpDuXYoMj/+2fNqnFFMHsk="
    assert app_plist["SUVerifyUpdateBeforeExtraction"].value is True
    assert app_plist["SURequireSignedFeed"].value is True


def test_developer_id_entitlements_allow_audio_input():
    entitlements = plistlib.loads(
        (ROOT / "packaging" / "macos" / "entitlements.plist").read_bytes()
    )

    assert entitlements["com.apple.security.device.audio-input"] is True


def test_local_ad_hoc_signing_uses_entitlements():
    build_script = (ROOT / "packaging" / "macos" / "build_app.sh").read_text()

    assert "--entitlements \"$ROOT/packaging/macos/entitlements.plist\"" in build_script


def test_nested_macho_files_are_signed_without_app_entitlements():
    sign_script = (ROOT / "packaging" / "macos" / "sign_app.sh").read_text()
    nested_signing_block = sign_script.split("done < <(", maxsplit=1)[0]

    assert "--entitlements" not in nested_signing_block


def test_sparkle_dependency_is_pinned_and_checksum_verified():
    install_script = (ROOT / "packaging" / "macos" / "install_sparkle.sh").read_text()
    build_script = (ROOT / "packaging" / "macos" / "build_app.sh").read_text()

    assert 'SPARKLE_VERSION="2.9.4"' in install_script
    assert "ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9" in install_script
    assert "shasum -a 256 -c -" in install_script
    assert "Sparkle-LICENSE.txt" in build_script


def test_distribution_includes_project_license():
    build_script = (ROOT / "packaging" / "macos" / "build_app.sh").read_text()
    dmg_script = (ROOT / "packaging" / "macos" / "build_dmg.sh").read_text()

    assert '"$APP/Contents/Resources/LICENSE.txt"' in build_script
    assert '"$STAGING/LICENSE.txt"' in dmg_script


def test_sparkle_nested_services_are_signed_in_official_order():
    sign_script = (ROOT / "packaging" / "macos" / "sign_sparkle.sh").read_text()
    ordered_targets = [
        "Installer.xpc",
        "Downloader.xpc",
        "Autoupdate",
        "Updater.app",
        'sign_target "$FRAMEWORK"',
    ]

    positions = [sign_script.index(target) for target in ordered_targets]
    assert positions == sorted(positions)
    assert "--preserve-metadata=entitlements" in sign_script


def test_release_workflow_publishes_signed_sparkle_appcast():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert "SPARKLE_PRIVATE_KEY: ${{ secrets.SPARKLE_PRIVATE_KEY }}" in workflow
    assert '"$sparkle_root/bin/generate_appcast"' in workflow
    assert "--ed-key-file -" in workflow
    assert "gh release upload sparkle-feed" in workflow
