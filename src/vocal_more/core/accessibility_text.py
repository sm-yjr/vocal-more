"""Read the currently focused editable text element through macOS Accessibility."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FocusedTextSnapshot:
    """Serializable identity and value for one focused text input."""

    target_id: str
    pid: int
    value: str
    role: str
    subrole: str
    app_bundle_id: str
    app_name: str
    is_secure: bool

    def is_same_target(self, other: "FocusedTextSnapshot") -> bool:
        return self.pid == other.pid and self.target_id == other.target_id


class MacOSFocusedTextProvider:
    """Best-effort AX reader; failures are treated as unsupported inputs."""

    _EDITABLE_ROLES = {
        "AXTextField",
        "AXTextArea",
        "AXComboBox",
        "AXSearchField",
    }

    @staticmethod
    def _copy_attribute(api, element, attribute):
        error, value = api.AXUIElementCopyAttributeValue(element, attribute, None)
        if error != 0:
            return None
        return value

    def capture_focused(self) -> FocusedTextSnapshot | None:
        try:
            import ApplicationServices as api
            from AppKit import NSRunningApplication
            from CoreFoundation import CFHash

            system = api.AXUIElementCreateSystemWide()
            element = self._copy_attribute(
                api,
                system,
                api.kAXFocusedUIElementAttribute,
            )
            if element is None:
                return None

            pid_error, pid = api.AXUIElementGetPid(element, None)
            if pid_error != 0:
                return None

            role = str(
                self._copy_attribute(api, element, api.kAXRoleAttribute) or ""
            )
            subrole = str(
                self._copy_attribute(api, element, api.kAXSubroleAttribute) or ""
            )
            if role not in self._EDITABLE_ROLES:
                return None

            secure_marker = f"{role} {subrole}".casefold()
            is_secure = "secure" in secure_marker or "password" in secure_marker
            if is_secure:
                value = ""
            else:
                value = self._copy_attribute(
                    api,
                    element,
                    api.kAXValueAttribute,
                )
                if not isinstance(value, str):
                    return None
            identifier = self._copy_attribute(
                api,
                element,
                api.kAXIdentifierAttribute,
            )
            identity = f"{identifier or ''}:{CFHash(element)}"

            running_app = NSRunningApplication.runningApplicationWithProcessIdentifier_(
                int(pid)
            )
            bundle_id = ""
            app_name = ""
            if running_app is not None:
                bundle_id = str(running_app.bundleIdentifier() or "")
                app_name = str(running_app.localizedName() or "")

            return FocusedTextSnapshot(
                target_id=f"{int(pid)}:{identity}",
                pid=int(pid),
                value=value,
                role=role,
                subrole=subrole,
                app_bundle_id=bundle_id,
                app_name=app_name,
                is_secure=is_secure,
            )
        except Exception:
            return None


__all__ = ["FocusedTextSnapshot", "MacOSFocusedTextProvider"]
