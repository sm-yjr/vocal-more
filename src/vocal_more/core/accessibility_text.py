"""Read the currently focused editable text element through macOS Accessibility."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FocusedTextSnapshot:
    """Identity and value for one focused text input.

    ``_target_handle`` is an opaque, process-local AX reference.  It is excluded
    from comparisons and representations and is used only for a final safe read
    after keyboard focus has moved elsewhere.
    """

    target_id: str
    pid: int
    value: str
    role: str
    subrole: str
    app_bundle_id: str
    app_name: str
    is_secure: bool
    _target_handle: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )

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

    def _capture_element(
        self,
        api,
        element,
        *,
        expected: FocusedTextSnapshot | None = None,
    ) -> FocusedTextSnapshot | None:
        from CoreFoundation import CFHash

        pid_error, pid = api.AXUIElementGetPid(element, None)
        if pid_error != 0:
            return None
        pid = int(pid)

        role = str(self._copy_attribute(api, element, api.kAXRoleAttribute) or "")
        subrole = str(
            self._copy_attribute(api, element, api.kAXSubroleAttribute) or ""
        )
        if role not in self._EDITABLE_ROLES:
            return None

        # Security must be established before AXValue is requested.  This check
        # is deliberately repeated for retained targets because their role can
        # change while the observation window is open.
        secure_marker = f"{role} {subrole}".casefold()
        is_secure = "secure" in secure_marker or "password" in secure_marker

        identifier = self._copy_attribute(
            api,
            element,
            api.kAXIdentifierAttribute,
        )
        identity = f"{identifier or ''}:{CFHash(element)}"
        target_id = f"{pid}:{identity}"
        if expected is not None and (
            expected.pid != pid or expected.target_id != target_id
        ):
            return None

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

        if expected is None:
            from AppKit import NSRunningApplication

            running_app = NSRunningApplication.runningApplicationWithProcessIdentifier_(
                pid
            )
            bundle_id = ""
            app_name = ""
            if running_app is not None:
                bundle_id = str(running_app.bundleIdentifier() or "")
                app_name = str(running_app.localizedName() or "")
        else:
            bundle_id = expected.app_bundle_id
            app_name = expected.app_name

        return FocusedTextSnapshot(
            target_id=target_id,
            pid=pid,
            value=value,
            role=role,
            subrole=subrole,
            app_bundle_id=bundle_id,
            app_name=app_name,
            is_secure=is_secure,
            _target_handle=element,
        )

    def capture_focused(self) -> FocusedTextSnapshot | None:
        try:
            import ApplicationServices as api

            system = api.AXUIElementCreateSystemWide()
            element = self._copy_attribute(
                api,
                system,
                api.kAXFocusedUIElementAttribute,
            )
            if element is None:
                return None
            return self._capture_element(api, element)
        except Exception:
            return None

    def capture_target(
        self,
        original: FocusedTextSnapshot,
    ) -> FocusedTextSnapshot | None:
        """Read a previously focused AX element without changing focus."""
        if original._target_handle is None:
            return None
        try:
            import ApplicationServices as api

            return self._capture_element(
                api,
                original._target_handle,
                expected=original,
            )
        except Exception:
            return None


__all__ = ["FocusedTextSnapshot", "MacOSFocusedTextProvider"]
