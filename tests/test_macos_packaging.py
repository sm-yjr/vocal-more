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
