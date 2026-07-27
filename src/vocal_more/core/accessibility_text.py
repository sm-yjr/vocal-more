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
    selection_start: int | None = None
    selection_length: int | None = None
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

        selection_start = None
        selection_length = None
        if not is_secure:
            selected_range_attribute = getattr(
                api,
                "kAXSelectedTextRangeAttribute",
                None,
            )
            if selected_range_attribute is not None:
                selected_range = self._copy_attribute(
                    api,
                    element,
                    selected_range_attribute,
                )
                selection_start, selection_length = self._extract_text_range(
                    api,
                    selected_range,
                )

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
            selection_start=selection_start,
            selection_length=selection_length,
            _target_handle=element,
        )

    @staticmethod
    def _extract_text_range(api, value) -> tuple[int | None, int | None]:
        """Decode an AXSelectedTextRange value across PyObjC API shapes."""
        if value is None:
            return None, None

        candidate = value
        location = getattr(candidate, "location", None)
        length = getattr(candidate, "length", None)
        if location is None or length is None:
            getter = getattr(api, "AXValueGetValue", None)
            range_type = getattr(api, "kAXValueCFRangeType", None)
            if not callable(getter) or range_type is None:
                return None, None
            try:
                decoded = getter(value, range_type, None)
            except Exception:
                return None, None
            if isinstance(decoded, tuple):
                if len(decoded) == 2 and isinstance(decoded[0], bool):
                    if not decoded[0]:
                        return None, None
                    candidate = decoded[1]
                elif len(decoded) == 2 and all(
                    isinstance(item, (int, float)) for item in decoded
                ):
                    candidate = decoded
                elif decoded:
                    candidate = decoded[-1]
            else:
                candidate = decoded
            location = getattr(candidate, "location", None)
            length = getattr(candidate, "length", None)
            if (
                (location is None or length is None)
                and isinstance(candidate, tuple)
                and len(candidate) == 2
            ):
                location, length = candidate

        try:
            start = max(0, int(location))
            size = max(0, int(length))
        except (TypeError, ValueError):
            return None, None
        return start, size

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
