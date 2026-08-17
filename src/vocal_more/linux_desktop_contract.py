"""Privacy-safe contract shared by the Linux host and GNOME Shell extension."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

BUS_NAME = "com.sm_yjr.VocalMore"
OBJECT_PATH = "/com/sm_yjr/VocalMore/Desktop"
INTERFACE_NAME = "com.sm_yjr.VocalMore.Desktop1"
CONTEXT_INTERFACE_NAME = "com.sm_yjr.VocalMore.DesktopContext1"
SNAPSHOT_SCHEMA_VERSION = 1
LINUX_ACCELERATORS = ("F8", "F9", "F10", "F11", "F12")


DBUS_INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE_NAME}">
    <method name="GetSnapshot"><arg direction="out" type="s"/></method>
    <method name="TriggerPressed"/>
    <method name="TriggerReleased"/>
    <method name="Cancel"/>
    <method name="SetMode"><arg direction="in" type="s" name="mode"/></method>
    <method name="SetAutoPaste"><arg direction="in" type="b" name="enabled"/></method>
    <method name="ShowSettings"/>
    <method name="Quit"/>
    <method name="CompletePaste">
      <arg direction="in" type="t" name="request_id"/>
      <arg direction="in" type="b" name="ok"/>
      <arg direction="in" type="s" name="error"/>
    </method>
    <signal name="SnapshotChanged">
      <arg type="s" name="snapshot_json"/>
    </signal>
    <signal name="PasteRequested">
      <arg type="t" name="request_id"/>
    </signal>
  </interface>
  <interface name="{CONTEXT_INTERFACE_NAME}">
    <method name="SetFocusedApp">
      <arg direction="in" type="s" name="desktop_app_id"/>
    </method>
  </interface>
</node>
""".strip()


@dataclass(frozen=True)
class DesktopSnapshot:
    """Small immutable view model that intentionally excludes transcript text."""

    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    state: str = "idle"
    mode: str = "realtime_long"
    language: str = "zh"
    stage: str = ""
    audio_level: float = 0.0
    trigger_label: str = "F8"
    can_cancel: bool = False
    auto_paste: bool = True
    backend_ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["audio_level"] = max(0.0, min(1.0, float(self.audio_level)))
        return data

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> DesktopSnapshot:
        return cls(
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            state=str(value.get("state") or "idle"),
            mode=str(value.get("mode") or "realtime_long"),
            language=(
                str(value.get("language"))
                if value.get("language") in {"zh", "en"}
                else "en"
            ),
            stage=str(value.get("stage") or ""),
            audio_level=max(
                0.0,
                min(1.0, _finite_float(value.get("audio_level"))),
            ),
            trigger_label=(
                str(value.get("trigger_label"))
                if value.get("trigger_label") in LINUX_ACCELERATORS
                else "F8"
            ),
            can_cancel=bool(value.get("can_cancel")),
            auto_paste=bool(value.get("auto_paste")),
            backend_ready=bool(value.get("backend_ready")),
        )


def _finite_float(value: Any) -> float:
    try:
        result = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if result != result or result in {float("inf"), float("-inf")}:
        return 0.0
    return result


__all__ = [
    "BUS_NAME",
    "CONTEXT_INTERFACE_NAME",
    "DBUS_INTROSPECTION_XML",
    "INTERFACE_NAME",
    "LINUX_ACCELERATORS",
    "OBJECT_PATH",
    "SNAPSHOT_SCHEMA_VERSION",
    "DesktopSnapshot",
]
