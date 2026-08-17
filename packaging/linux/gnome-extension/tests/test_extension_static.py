"""Static contract checks for the GNOME Shell 50 extension.

These tests intentionally do not require a running GNOME Shell session. Runtime
input injection and actor rendering are covered by the Ubuntu/GNOME acceptance
matrix, while these checks prevent packaging and D-Bus contract drift.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

EXTENSION = Path(__file__).parents[1] / "vocal-more@sm-yjr.com"
TESTS = Path(__file__).parent


def _source(name: str) -> str:
    return (EXTENSION / name).read_text(encoding="utf-8")


def _test_source(name: str) -> str:
    return (TESTS / name).read_text(encoding="utf-8")


def test_metadata_is_gnome_50_only() -> None:
    metadata = json.loads(_source("metadata.json"))
    assert metadata["uuid"] == "vocal-more@sm-yjr.com"
    assert metadata["shell-version"] == ["50"]
    assert metadata["settings-schema"] == "org.gnome.shell.extensions.vocal-more"


def test_schema_compiles_and_limits_trigger_documentation() -> None:
    schema_dir = EXTENSION / "schemas"
    schema = (schema_dir / "org.gnome.shell.extensions.vocal-more.gschema.xml").read_text(
        encoding="utf-8"
    )
    assert "name=\"linux-accelerator\"" in schema
    assert "<default>['F8']</default>" in schema
    assert 'type="as"' in schema
    assert "F8, F9, F10, F11, or F12" in schema

    compiler = shutil.which("glib-compile-schemas")
    if compiler is None:
        return
    with tempfile.TemporaryDirectory(prefix="vocal-more-schema-") as temp_dir:
        temp_schema_dir = Path(temp_dir)
        shutil.copy2(schema_dir / "org.gnome.shell.extensions.vocal-more.gschema.xml", temp_schema_dir)
        result = subprocess.run(
            [compiler, "--strict", str(temp_schema_dir)],
            check=False,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, result.stderr


def test_desktop1_contract_and_paste_ack_are_present() -> None:
    source = _source("dbusClient.js")
    assert "com.sm_yjr.VocalMore" in source
    assert "com.sm_yjr.VocalMore.Desktop1" in source
    for method in (
        "GetSnapshot",
        "TriggerPressed",
        "TriggerReleased",
        "Cancel",
        "SetMode",
        "SetAutoPaste",
        "ShowSettings",
        "Quit",
        "CompletePaste",
    ):
        assert f'name="{method}"' in source
    assert 'signal name="SnapshotChanged"' in source
    assert 'signal name="PasteRequested"' in source
    assert 'type="t" name="request_id"' in source
    assert "DesktopContext1" in source
    assert "DBUS_CONTEXT_INTERFACE" in source
    desktop_xml = source.split("const _INTERFACE_XML", 1)[1].split("</node>`", 1)[0]
    assert 'method name="SetFocusedApp"' not in desktop_xml
    assert "_safeSnapshot" in source
    assert "schema_version !== 1" in source


def test_trigger_and_clutter_injection_paths_are_explicit() -> None:
    gesture = _source("gesture.js")
    extension = _source("extension.js")
    assert "F8" in gesture and "F12" in gesture
    assert "captured-event" in gesture
    assert "key-release-event" in gesture
    assert "this._pressed" in gesture
    assert "Clutter.KEY_Escape" in gesture
    assert "Main.wm.addKeybinding" in gesture
    assert "Main.wm.removeKeybinding" in gesture
    assert "create_virtual_device" in extension
    assert "GLib.get_monotonic_time" in extension
    assert "Clutter.KEY_Control_L" in extension
    assert "Clutter.KEY_V" in extension
    assert "completePaste" in extension
    assert "_disposeVirtualKeyboard" in extension
    assert "WindowTracker" in extension
    assert "get_title" not in extension


def test_gnome_50_uses_esm_modules_and_extension_lifecycle() -> None:
    javascript = "\n".join(
        _source(name)
        for name in ("extension.js", "dbusClient.js", "gesture.js", "capsule.js", "panelMenu.js")
    )
    assert "imports." not in javascript
    assert "function init" not in javascript
    assert "export default class" in _source("extension.js")
    assert "extends Extension" in _source("extension.js")
    assert "import * as Main" in _source("extension.js")


def test_shell_smoke_harness_packs_all_esm_modules() -> None:
    automation = _test_source("automation_smoke.js")
    harness = _test_source("test_shell_extension.sh")
    assert "export function run()" in automation
    assert "create_virtual_device" in automation
    assert "notify_keyval" in automation
    assert "gnome-extensions pack" in harness
    for module in ("dbusClient.js", "gesture.js", "capsule.js", "panelMenu.js"):
        assert f"--extra-source={module}" in harness
    assert "dbus-run-session" in harness
    assert "gnome-shell-test-tool" in harness
    assert "--headless" in harness


def test_capsule_and_logs_never_render_or_log_private_dictation_payloads() -> None:
    extension = _source("extension.js")
    capsule = _source("capsule.js")
    client = _source("dbusClient.js")
    assert not re.search(r"\btranscript\b", extension, flags=re.IGNORECASE)
    assert not re.search(r"\btranscript\b", capsule, flags=re.IGNORECASE)
    assert "console.log" not in client
    assert "snapshot_json" in client
    assert "PasteRequested" in client


def test_capsule_has_asymptotic_processing_progress_and_reduced_motion() -> None:
    capsule = _source("capsule.js")
    assert "Math.exp" in capsule
    assert "0.92" in capsule
    assert "GLib.timeout_add" in capsule
    assert "_reducedMotion" in capsule
    assert "_stopProcessingAnimation" in capsule
