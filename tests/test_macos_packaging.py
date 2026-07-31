"""macOS bundle metadata needed for first-run system permissions."""

from __future__ import annotations

import ast
import importlib.util
import plistlib
from pathlib import Path
import subprocess
import sys

import pytest


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


def test_py2app_runs_as_menu_bar_only_app():
    app_plist = _load_py2app_plist()

    assert app_plist["LSUIElement"].value is True


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
    nested_signing_block = sign_script.split(
        'codesign --force --timestamp --options runtime \\\n  --entitlements',
        maxsplit=1,
    )[0]

    assert "--entitlements" not in nested_signing_block


def test_nested_macho_files_are_signed_in_parallel():
    sign_script = (ROOT / "packaging" / "macos" / "sign_app.sh").read_text()

    assert 'CODESIGN_JOBS="${VOCAL_MORE_CODESIGN_JOBS:-8}"' in sign_script
    assert 'xargs -P "$CODESIGN_JOBS"' in sign_script


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


def test_distribution_includes_shadcn_ui_license_separately():
    build_script = (ROOT / "packaging" / "macos" / "build_app.sh").read_text()

    assert (
        '"$ROOT/resources/settings/SHADCN-UI-LICENSE.txt" '
        '"$APP/Contents/Resources/Shadcn-UI-LICENSE.txt"'
    ) in build_script


def test_packaging_rebuilds_settings_frontend_before_py2app():
    build_script = (ROOT / "packaging" / "macos" / "build_app.sh").read_text()

    assert 'npm --prefix "$ROOT/frontend/settings" ci' in build_script
    assert 'npm --prefix "$ROOT/frontend/settings" run build' in build_script


def test_bundle_pruner_removes_only_non_runtime_python_payload(tmp_path):
    app = tmp_path / "Vocal More.app"
    python_lib = app / "Contents" / "Resources" / "lib" / "python3.12"
    removable = [
        python_lib / "test" / "test_stdlib.py",
        python_lib / "numpy" / "tests" / "test_core.py",
        python_lib / "numpy" / "typing.pyi",
        python_lib / "numpy" / "py.typed",
    ]
    preserved = [
        python_lib / "numpy" / "__init__.py",
        python_lib / "numpy" / "testing" / "__init__.py",
        python_lib / "future_dependency" / "tests" / "runtime_fixture.py",
        python_lib / "openai" / "__pycache__" / "client.cpython-312.pyc",
        app / "Contents" / "Resources" / "resources" / "tests" / "fixture.json",
    ]
    for path in [*removable, *preserved]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("payload", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "packaging" / "macos" / "prune_app_bundle.py"),
            str(app),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert all(not path.exists() for path in removable)
    assert all(path.exists() for path in preserved)
    assert "Removed 4 files" in result.stdout


def test_bundle_pruner_rejects_non_app_targets(tmp_path):
    target = tmp_path / "ordinary-directory"
    target.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "packaging" / "macos" / "prune_app_bundle.py"),
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "expected a .app bundle" in result.stderr


def test_build_prunes_python_payload_before_embedding_and_signing_sparkle():
    build_script = (ROOT / "packaging" / "macos" / "build_app.sh").read_text()

    prune = build_script.index("prune_app_bundle.py")
    embed_sparkle = build_script.index('SPARKLE_ROOT="$(')
    sign_sparkle = build_script.index('sign_sparkle.sh')

    assert prune < embed_sparkle < sign_sparkle


def test_bundle_optimizer_thins_non_sparkle_macho_files(tmp_path):
    script_path = ROOT / "packaging" / "macos" / "prune_app_bundle.py"
    spec = importlib.util.spec_from_file_location("prune_app_bundle", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = tmp_path / "Vocal More.app"
    python_binary = app / "Contents" / "Frameworks" / "Python.framework" / "Python"
    extension = (
        app
        / "Contents"
        / "Resources"
        / "lib"
        / "python3.12"
        / "cryptography"
        / "_rust.so"
    )
    sparkle = (
        app
        / "Contents"
        / "Frameworks"
        / "Sparkle.framework"
        / "Versions"
        / "B"
        / "Sparkle"
    )
    for path in (python_binary, extension, sparkle):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"universal-binary")
        path.chmod(0o755)

    inspected = []

    def fake_runner(command, **_kwargs):
        inspected.append(command)
        if command[1] == "-archs":
            inspected_path = Path(command[-1])
            if inspected_path.exists() and inspected_path.read_bytes() == b"arm64":
                return subprocess.CompletedProcess(command, 0, "arm64\n", "")
            return subprocess.CompletedProcess(command, 0, "x86_64 arm64\n", "")
        output = Path(command[-1])
        output.write_bytes(b"arm64")
        return subprocess.CompletedProcess(command, 0, "", "")

    count, bytes_saved = module.thin_macho_binaries(
        app,
        target_arch="arm64",
        command_runner=fake_runner,
    )

    assert count == 2
    assert bytes_saved == 2 * (len(b"universal-binary") - len(b"arm64"))
    assert python_binary.read_bytes() == b"arm64"
    assert extension.read_bytes() == b"arm64"
    assert sparkle.read_bytes() == b"universal-binary"
    assert not any(str(sparkle) in command for command in inspected)


def test_bundle_optimizer_rejects_macho_without_target_architecture(tmp_path):
    script_path = ROOT / "packaging" / "macos" / "prune_app_bundle.py"
    spec = importlib.util.spec_from_file_location("prune_app_bundle_wrong_arch", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = tmp_path / "Vocal More.app"
    binary = app / "Contents" / "Resources" / "opaque-runtime-payload.bundle"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(bytes.fromhex("cafebabe") + b"payload")

    def fake_runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, "x86_64\n", "")

    with pytest.raises(RuntimeError, match="does not contain target architecture arm64"):
        module.thin_macho_binaries(
            app,
            target_arch="arm64",
            command_runner=fake_runner,
        )


def test_release_build_uses_clean_locked_arm64_packaging_environment():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert "UV_PROJECT_ENVIRONMENT" in workflow
    assert "uv sync --locked --no-dev --group packaging" in workflow
    assert "VOCAL_MORE_TARGET_ARCH: arm64" in workflow


def test_py2app_excludes_test_and_optional_gui_modules():
    setup_text = (ROOT / "packaging" / "macos" / "setup.py").read_text()

    for module_name in (
        "_pytest",
        "pytest",
        "test",
        "_tkinter",
        "tkinter",
        "idlelib",
        "turtle",
    ):
        assert f'"{module_name}"' in setup_text


def test_bundle_uses_generated_notification_logo_instead_of_source_artwork():
    setup_text = (ROOT / "packaging" / "macos" / "setup.py").read_text()
    icon_script = (ROOT / "packaging" / "macos" / "make_icon.sh").read_text()
    app_text = (ROOT / "src" / "vocal_more" / "app.py").read_text()

    assert 'str(ROOT / "assets")' not in setup_text
    assert ".VocalMore.runtime-logo.png" in setup_text
    assert 'RUNTIME_LOGO="$ROOT/packaging/macos/.VocalMore.runtime-logo.png"' in icon_script
    assert 'bundled_resource_path("assets", ".VocalMore.runtime-logo.png")' in app_text


def test_release_workflow_tests_and_builds_settings_frontend():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert "actions/setup-node@v4" in workflow
    assert "npm --prefix frontend/settings ci" in workflow
    assert "npm --prefix frontend/settings test" in workflow
    assert "npm --prefix frontend/settings run typecheck" in workflow
    assert "npm --prefix frontend/settings run lint" in workflow
    assert "npm --prefix frontend/settings run build" in workflow


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


def test_release_workflow_publishes_sparkle_delta_updates():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert "maximum_deltas=1" in workflow
    assert "gh release list --exclude-drafts --exclude-pre-releases" in workflow
    assert 'gh release download "$previous_tag"' in workflow
    assert '--maximum-deltas "$maximum_deltas"' in workflow
    assert "--delta-compression lzfse" in workflow
    assert 'gh release upload "$tag" "${delta_files[@]}" --clobber' in workflow
    assert 'grep -q "<sparkle:deltas>" "$updates_dir/appcast.xml"' in workflow


def test_release_workflow_avoids_duplicate_build_and_signing_work():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    build_script = (ROOT / "packaging" / "macos" / "build_app.sh").read_text()

    assert "Run frontend and Python checks in parallel" in workflow
    assert 'VOCAL_MORE_SKIP_FRONTEND_BUILD: "1"' in workflow
    assert 'VOCAL_MORE_SKIP_ADHOC_SIGN: "1"' in workflow
    assert 'VOCAL_MORE_USE_PREPARED_BUILD_VENV: "1"' in workflow
    assert "--group packaging" in workflow
    assert '${VOCAL_MORE_SKIP_FRONTEND_BUILD:-0}' in build_script
    assert '${VOCAL_MORE_SKIP_ADHOC_SIGN:-0}' in build_script
    assert '${VOCAL_MORE_USE_PREPARED_BUILD_VENV:-0}' in build_script
