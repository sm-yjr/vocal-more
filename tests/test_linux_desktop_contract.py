from __future__ import annotations

import json

from vocal_more.linux_desktop_contract import (
    BUS_NAME,
    CONTEXT_INTERFACE_NAME,
    DBUS_INTROSPECTION_XML,
    INTERFACE_NAME,
    DesktopSnapshot,
)


def test_snapshot_json_is_versioned_bounded_and_privacy_safe():
    snapshot = DesktopSnapshot(
        state="recording",
        mode="walkie_talkie",
        audio_level=4.2,
        trigger_label="F9",
        backend_ready=True,
    )

    payload = json.loads(snapshot.to_json())

    assert payload["schema_version"] == 1
    assert payload["audio_level"] == 1.0
    assert payload["trigger_label"] == "F9"
    assert "text" not in payload
    assert "transcript" not in payload


def test_snapshot_normalizes_untrusted_extension_values():
    snapshot = DesktopSnapshot.from_mapping(
        {
            "language": "xx",
            "audio_level": float("nan"),
            "trigger_label": "Super+Space",
        }
    )

    assert snapshot.language == "en"
    assert snapshot.audio_level == 0.0
    assert snapshot.trigger_label == "F8"


def test_dbus_contract_has_stable_identity_and_no_transcript_signal():
    assert BUS_NAME == "com.sm_yjr.VocalMore"
    assert INTERFACE_NAME in DBUS_INTROSPECTION_XML
    assert 'method name="CompletePaste"' in DBUS_INTROSPECTION_XML
    desktop_xml, context_xml = DBUS_INTROSPECTION_XML.split(
        f'<interface name="{CONTEXT_INTERFACE_NAME}">'
    )
    assert 'method name="SetFocusedApp"' not in desktop_xml
    assert 'method name="SetFocusedApp"' in context_xml
    assert "transcript" not in DBUS_INTROSPECTION_XML.casefold()
