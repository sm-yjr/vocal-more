"""macOS bundle metadata needed for first-run system permissions."""

from __future__ import annotations

import ast
import importlib.util
import json
import plistlib
import subprocess
import sys
from pathlib import Path

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


def _load_py2app_options() -> dict[str, ast.AST]:
    tree = ast.parse((ROOT / "packaging" / "macos" / "setup.py").read_text())

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "OPTIONS"
            for target in node.targets
        ):
            continue
        return {
            key.value: value
            for key, value in zip(node.value.keys, node.value.values)
            if isinstance(key, ast.Constant)
        }

    raise AssertionError("OPTIONS was not found in packaging/macos/setup.py")


def test_py2app_declares_microphone_usage_description():
    app_plist = _load_py2app_plist()

    usage = app_plist["NSMicrophoneUsageDescription"]
    assert isinstance(usage, ast.Constant)
    assert usage.value


def test_py2app_runs_as_menu_bar_only_app():
    app_plist = _load_py2app_plist()

    assert app_plist["LSUIElement"].value is True


def test_py2app_declares_the_binary_compatible_macos_floor():
    app_plist = _load_py2app_plist()

    assert app_plist["LSMinimumSystemVersion"].value == "14.0"


def test_py2app_includes_accessibility_modules_for_dictionary_learning():
    setup_text = (ROOT / "packaging" / "macos" / "setup.py").read_text()

    assert '"ApplicationServices"' in setup_text
    assert '"CoreFoundation"' in setup_text


def test_py2app_bundles_avfoundation_voice_processing_bridge():
    setup_text = (ROOT / "packaging" / "macos" / "setup.py").read_text()
    pyproject = (ROOT / "pyproject.toml").read_text()

    assert '"AVFoundation"' in setup_text
    assert '"CoreAudio"' in setup_text
    assert '"CoreMedia"' in setup_text
    assert (
        '"pyobjc-framework-AVFoundation>=10.0; sys_platform == \'darwin\'"'
        in pyproject
    )


def test_py2app_bundles_numpy_for_the_audio_callback():
    options = _load_py2app_options()
    packages = ast.literal_eval(options["packages"])
    excludes = ast.literal_eval(options["excludes"])

    assert "numpy" in packages
    assert "numpy" not in excludes


def test_completed_app_runs_a_runtime_dependency_smoke_test():
    build = (ROOT / "packaging/macos/build_app.sh").read_text()
    assert '"$APP/Contents/MacOS/Vocal More" --version' in build
    assert '"$APP/Contents/Resources/rust-backend/vocal-more-backend" --version' in build


def test_py2app_declares_signed_sparkle_feed():
    app_plist = _load_py2app_plist()
    assert app_plist["SUPublicEDKey"].value == "rX4Sp1huP0v763afpuPlVkpDuXYoMj/+2fNqnFFMHsk="
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


def test_nested_macho_files_are_signed_serially_to_avoid_bundle_mutation_races():
    sign_script = (ROOT / "packaging" / "macos" / "sign_app.sh").read_text()

    assert "VOCAL_MORE_CODESIGN_JOBS" not in sign_script
    assert "xargs -P" not in sign_script
    assert "while IFS= read -r file; do" in sign_script
    assert '--sign "$IDENTITY" "$file"' in sign_script


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


def test_packaging_builds_locked_rust_frontend():
    build = (ROOT / "packaging/macos/build_app.sh").read_text()
    assert 'build --locked --release' in build
    assert '-p vocal-more-desktop -p vocal-more-backend' in build
    assert 'setup.py py2app' not in build


def test_bundle_pruner_removes_only_non_runtime_python_payload(tmp_path):
    app = tmp_path / "Vocal More.app"
    python_lib = app / "Contents" / "Resources" / "lib" / "python3.12"
    removable = [
        python_lib / "test" / "test_stdlib.py",
        python_lib / "numpy" / "tests" / "test_core.py",
        python_lib / "numpy" / "typing.pyi",
        python_lib / "numpy" / "py.typed",
        python_lib / "dashscope" / "resources" / "qwen.tiktoken",
        python_lib / "openai" / "__pycache__" / "client.cpython-312.pyc",
    ]
    preserved = [
        python_lib / "numpy" / "__init__.py",
        python_lib / "numpy" / "testing" / "__init__.py",
        python_lib / "future_dependency" / "tests" / "runtime_fixture.py",
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
    assert "Removed 6 files" in result.stdout


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


def test_build_stages_native_product_before_signing_sparkle():
    build = (ROOT / "packaging/macos/build_app.sh").read_text()
    assert build.index('stage_rust_app.py') < build.index('SPARKLE_ROOT=') < build.index('sign_sparkle.sh')
    assert 'prune_app_bundle.py' not in build


def test_build_installs_dependencies_from_frozen_lockfile():
    build = (ROOT / "packaging/macos/build_app.sh").read_text()
    assert 'build --locked --release' in build
    assert 'rust_notices.py' in build
    assert 'pip install' not in build


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


def test_release_build_uses_locked_rust_toolchain_and_arm64():
    import yaml
    workflow = yaml.safe_load((ROOT / ".github/workflows/_release-candidate.yml").read_text())
    steps = workflow['jobs']['build']['steps']
    toolchain = next(s for s in steps if s['name'] == 'Install pinned Rust toolchain')
    assert '1.98.1' in toolchain['run']
    assert not any(s['name'] == 'Prepare clean packaging environment' for s in steps)
    build = next(s for s in steps if s['name'] == 'Build signed DMG')
    assert build['env']['VOCAL_MORE_TARGET_ARCH'] == 'arm64'


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


def test_release_workflow_retains_legacy_behavior_checks_and_builds_rust_ui():
    import yaml
    workflow = yaml.safe_load((ROOT / ".github/workflows/_release-candidate.yml").read_text())
    steps = workflow['jobs']['build']['steps']
    commands = '\n'.join(s.get('run', '') for s in steps)
    for command in ('ci', 'test', 'run typecheck', 'run lint'):
        assert f'npm --prefix frontend/settings {command}' in commands
    assert '-p vocal-more-backend -p vocal-more-desktop' in commands


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






def test_release_workflow_avoids_duplicate_build_and_signing_work():
    import yaml
    workflow = yaml.safe_load((ROOT / ".github/workflows/_release-candidate.yml").read_text())
    steps = workflow['jobs']['build']['steps']
    build = next(s for s in steps if s['name'] == 'Build signed DMG')
    assert build['env']['VOCAL_MORE_SKIP_ADHOC_SIGN'] == '1'
    assert 'VOCAL_MORE_USE_PREPARED_BUILD_VENV' not in build['env']
    assert not any(s['name'] == 'Build frontend bundle' for s in steps)


def _load_release_artifact_verifier():
    script_path = ROOT / "packaging" / "macos" / "verify_release_artifact.py"
    spec = importlib.util.spec_from_file_location("verify_release_artifact", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("notary,accepted", [
    ({"id": "submission-id", "status": "Accepted"}, True),
    ({"id": "submission-id", "status": "Invalid"}, False),
    ({"status": "Accepted"}, False),
    ({"id": "", "status": "Accepted"}, False),
    ({"id": "submission-id"}, False),
])
def test_verifier_records_only_accepted_notarization(tmp_path, monkeypatch, notary, accepted):
    verifier = _load_release_artifact_verifier()
    monkeypatch.setattr(verifier, "verify_release_artifact", lambda _: {"status": "passed"})
    result = tmp_path / "notary-result.json"
    report = tmp_path / "verification.json"
    result.write_text(json.dumps(notary))
    args = [str(tmp_path / "app.dmg"), "--report", str(report), "--notary-result", str(result)]
    if accepted:
        assert verifier.main(args) == 0
        assert json.loads(report.read_text())["notarization"] == notary
    else:
        with pytest.raises(SystemExit) as error:
            verifier.main(args)
        assert error.value.code == 1
        assert not report.exists()


def test_release_artifact_verifier_enforces_native_library_contract(tmp_path):
    verifier = _load_release_artifact_verifier()
    app = tmp_path / "Vocal More.app"
    library = app / "Contents" / "Frameworks" / "libvocal_more_audio.dylib"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"Mach-O placeholder")
    commands: list[list[str]] = []

    def fake_runner(command, **_kwargs):
        command = [str(value) for value in command]
        commands.append(command)
        if command[:2] == ["lipo", "-archs"]:
            stdout = "arm64\n"
        elif command[:3] == ["xcrun", "vtool", "-show-build"]:
            stdout = "platform MACOS\n    minos 14.0\n"
        elif command[:2] == ["otool", "-D"]:
            stdout = f"{library}:\n@rpath/libvocal_more_audio.dylib\n"
        elif command[:2] == ["otool", "-L"]:
            stdout = (
                f"{library}:\n"
                "\t@rpath/libvocal_more_audio.dylib (compatibility version 0.0.0)\n"
                "\t/System/Library/Frameworks/Foundation.framework/Versions/C/"
                "Foundation (compatibility version 300.0.0)\n"
                "\t/usr/lib/libc++.1.dylib (compatibility version 1.0.0)\n"
            )
        elif command[:2] == ["nm", "-gU"]:
            stdout = "\n".join(
                f"0000000000000000 T _{symbol}"
                for symbol in verifier.REQUIRED_C_ABI_EXPORTS
            )
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    verifier.verify_native_audio_library(app, command_runner=fake_runner)

    assert "vm_audio_runtime_fault_count" in verifier.REQUIRED_C_ABI_EXPORTS
    assert "vm_audio_runtime_fault_code" in verifier.REQUIRED_C_ABI_EXPORTS
    assert any(
        command[:4] == ["codesign", "--verify", "--strict", "--verbose=2"]
        and command[-1] == str(library)
        for command in commands
    )


def test_release_artifact_verifier_rejects_non_apple_native_dependency(tmp_path):
    verifier = _load_release_artifact_verifier()
    app = tmp_path / "Vocal More.app"
    library = app / "Contents" / "Frameworks" / "libvocal_more_audio.dylib"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"Mach-O placeholder")

    def fake_runner(command, **_kwargs):
        command = [str(value) for value in command]
        if command[:2] == ["lipo", "-archs"]:
            stdout = "arm64\n"
        elif command[:3] == ["xcrun", "vtool", "-show-build"]:
            stdout = "platform MACOS\n    minos 14.0\n"
        elif command[:2] == ["otool", "-D"]:
            stdout = f"{library}:\n@rpath/libvocal_more_audio.dylib\n"
        elif command[:2] == ["otool", "-L"]:
            stdout = (
                f"{library}:\n"
                "\t@rpath/libvocal_more_audio.dylib (compatibility version 0.0.0)\n"
                "\t/opt/homebrew/lib/libunexpected.dylib "
                "(compatibility version 1.0.0)\n"
            )
        elif command[:2] == ["nm", "-gU"]:
            stdout = "\n".join(
                f"0000000000000000 T _{symbol}"
                for symbol in verifier.REQUIRED_C_ABI_EXPORTS
            )
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    with pytest.raises(RuntimeError, match="non-Apple dependency"):
        verifier.verify_native_audio_library(app, command_runner=fake_runner)


def test_release_workflow_verifies_mounted_native_artifact_before_upload():
    import yaml
    workflow = yaml.safe_load((ROOT / ".github/workflows/_release-candidate.yml").read_text())
    steps = workflow["jobs"]["build"]["steps"]
    names = [s["name"] for s in steps]
    assert names.index("Notarize and staple final DMG") < names.index("Verify final candidate artifact")
    assert names.index("Verify final candidate artifact") < names.index("Upload immutable release candidate")
    assert workflow["jobs"]["seal-candidate"]["needs"] == "build"


def test_rust_release_service_requires_matching_bundle_version(tmp_path):
    import plistlib
    verifier = _load_release_artifact_verifier()
    app = tmp_path / "Vocal More.app"
    binary = app / "Contents/Resources/rust-backend/vocal-more-backend"
    binary.parent.mkdir(parents=True)
    binary.touch()
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.4.17"}))
    def runner(command, **kwargs):
        if command[0] == str(binary):
            output = "Vocal More Rust backend 0.4.17\n"
        elif command[0] == "lipo":
            output = "arm64\n"
        else:
            output = str(binary) + ":\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    verifier.verify_rust_backend(app, command_runner=runner)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.4.18"}))
    with pytest.raises(RuntimeError, match="does not match"):
        verifier.verify_rust_backend(app, command_runner=runner)


def _load_rust_stager():
    spec = importlib.util.spec_from_file_location("stage_rust_app", ROOT / "packaging/macos/stage_rust_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rust_staging_preserves_metadata_license_and_has_no_python_payload(tmp_path):
    stager = _load_rust_stager()
    sources = []
    for name in ("desktop", "backend", "audio.dylib"):
        source = tmp_path / name
        source.write_bytes(name.encode())
        sources.append(source)
    app = stager.stage_app(tmp_path / "Vocal More.app", *sources)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "Vocal More"
    assert info["CFBundleIdentifier"] == "com.sm-yjr.vocal-more"
    assert info["VocalMoreUIRuntime"] == "rust-gpui-kit"
    assert info["LSUIElement"] is True
    assert info["LSMinimumSystemVersion"] == "14.0"
    assert (app / "Contents/MacOS/Vocal More").read_bytes() == b"desktop"
    assert (app / "Contents/Resources/rust-backend/vocal-more-backend").read_bytes() == b"backend"
    assert (app / "Contents/Frameworks/libvocal_more_audio.dylib").read_bytes() == b"audio.dylib"
    assert (app / "Contents/Resources/LICENSE.txt").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert not list(app.rglob("*.py"))
    assert not list(app.rglob("*.html"))
    assert stager.app_info("0.5.2b1")["SUFeedURL"].endswith("sparkle-feed-beta/appcast.xml")
    assert stager.app_info("0.5.2a1")["SUFeedURL"].endswith("sparkle-feed-alpha/appcast.xml")
    assert stager.app_info("0.5.2", development=True)["CFBundleIdentifier"].endswith(".dev")


def test_rust_frontend_verifier_rejects_legacy_runtime_and_version_mismatch(tmp_path):
    stager = _load_rust_stager()
    verifier = _load_release_artifact_verifier()
    app = tmp_path / "Vocal More.app"
    binary = app / "Contents/MacOS/Vocal More"
    binary.parent.mkdir(parents=True)
    binary.touch()
    info = stager.app_info("0.5.2")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    resources = app / "Contents/Resources"
    resources.mkdir()
    (resources / "Rust-Third-Party-Notices.txt").write_text("gpui-kit 0.7.0\nApache-2.0")
    def runner(command, **kwargs):
        if command[0] == str(binary):
            output = "Vocal More 0.5.2\n"
        elif command[0] == "lipo":
            output = "arm64\n"
        else:
            output = str(binary) + ":\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    verifier.verify_rust_frontend(app, command_runner=runner)
    (resources / "legacy.py").touch()
    with pytest.raises(RuntimeError, match="Legacy UI runtime"):
        verifier.verify_rust_frontend(app, command_runner=runner)
    (resources / "legacy.py").unlink()
    info["VocalMoreVersion"] = "0.5.3"
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    with pytest.raises(RuntimeError, match="version does not match"):
        verifier.verify_rust_frontend(app, command_runner=runner)
