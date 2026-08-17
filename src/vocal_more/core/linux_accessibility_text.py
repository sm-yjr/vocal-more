"""Best-effort AT-SPI2 focused editable-text provider for Linux.

The AT-SPI Python bindings are optional system integrations and are imported
only when a capture is requested.  This module supports both the traditional
``pyatspi`` API and GI's ``Atspi`` bindings without adding a Python runtime
dependency.  Every unsupported or ambiguous shape returns ``None``; in
particular password/protected fields are identified before their value is
requested.
"""

from __future__ import annotations

from collections.abc import Callable

from .accessibility_text import FocusedTextSnapshot

_MISSING = object()
_EDITABLE_ROLE_MARKERS = (
    "text",
    "entry",
    "textarea",
    "text area",
    "editable",
    "edit",
    "combo",
    "search",
    "document",
)
_SECURE_MARKERS = (
    "password",
    "secure",
    "protected",
    "sensitive",
)
_DEFUNCT_MARKERS = ("defunct", "invalid", "disposed")


def _call(value: object, names: tuple[str, ...], *args: object) -> object:
    """Call the first available API spelling, returning a sentinel on error."""

    if value is None:
        return _MISSING
    for name in names:
        method = getattr(value, name, None)
        if not callable(method):
            continue
        try:
            return method(*args)
        except TypeError:
            # A few pyatspi releases expose get_text() without the optional
            # range arguments.  A signature mismatch is safe to retry once.
            if args:
                try:
                    return method()
                except Exception:
                    return _MISSING
        except Exception:
            return _MISSING
    return _MISSING


def _first(value: object, names: tuple[str, ...]) -> object:
    """Read the first non-callable attribute or return the sentinel."""

    if value is None:
        return _MISSING
    for name in names:
        candidate = getattr(value, name, _MISSING)
        if candidate is not _MISSING and not callable(candidate):
            return candidate
    return _MISSING


def _text_interface(element: object) -> object:
    interface = _call(element, ("queryText", "query_text"))
    if interface is not _MISSING and interface is not None:
        return interface
    # GI wrappers sometimes expose Text methods directly on the accessible.
    if any(callable(getattr(element, name, None)) for name in ("getText", "get_text")):
        return element
    return None


def _editable_interface(element: object) -> object | None:
    interface = _call(element, ("queryEditableText", "query_editable_text"))
    if interface is _MISSING or interface is None:
        return None
    return interface


def _name(value: object) -> str:
    result = _call(value, ("get_name", "getName"))
    if result is _MISSING:
        result = _first(value, ("name",))
    if result is _MISSING or result is None:
        return ""
    return str(result).strip()


def _role_name(api: object, element: object) -> str:
    role = _call(element, ("get_role", "getRole"))
    if role is _MISSING:
        role = _first(element, ("role",))
    if role is _MISSING or role is None:
        return ""
    role_text = _name(role)
    if role_text:
        return role_text
    if isinstance(role, str):
        return role
    # GI exposes role values as integers/enums while pyatspi commonly exposes
    # ``ROLE_*`` constants at module scope.  Recover the password role when
    # possible so security is enforced even without a state set.
    for name in ("ROLE_PASSWORD_TEXT", "PASSWORD_TEXT"):
        constant = getattr(api, name, _MISSING)
        if constant is _MISSING:
            enum = getattr(api, "Role", _MISSING)
            constant = getattr(enum, name.removeprefix("ROLE_"), _MISSING)
        try:
            if constant is not _MISSING and role == constant:
                return name
        except Exception:
            pass
    # pyatspi role constants are occasionally returned as plain integers.
    # The numeric value is not useful to the application, but retaining it as
    # a string still gives secure marker detection a chance via subrole/name.
    return str(role)


def _state_set(element: object) -> object | None:
    state = _call(element, ("get_state_set", "getStateSet"))
    if state is _MISSING or state is None:
        return None
    return state


def _state_contains(state: object | None, api: object, names: tuple[str, ...]) -> bool | None:
    """Return true/false when an AT-SPI state can be queried, else ``None``."""

    if state is None:
        return None
    contains = getattr(state, "contains", None)
    if not callable(contains):
        contains = getattr(state, "contains_state", None)
    if callable(contains):
        found_constant = False
        for name in names:
            constant = _state_constant(api, name)
            if constant is _MISSING:
                continue
            found_constant = True
            try:
                if bool(contains(constant)):
                    return True
            except Exception:
                continue
        if found_constant:
            return False

    marker = str(state).casefold()
    if marker:
        return any(name.casefold().replace("state_", "") in marker for name in names)
    return None


def _state_constant(api: object, name: str) -> object:
    """Resolve pyatspi constants and GI ``Atspi.StateType`` enum members."""

    constant = getattr(api, name, _MISSING)
    if constant is not _MISSING:
        return constant
    enum = getattr(api, "StateType", _MISSING)
    if enum is _MISSING:
        enum = getattr(api, "STATE_TYPE", _MISSING)
    if enum is not _MISSING:
        short_name = name.removeprefix("STATE_")
        for candidate in (short_name, short_name.casefold(), short_name.title()):
            constant = getattr(enum, candidate, _MISSING)
            if constant is not _MISSING:
                return constant
    return _MISSING


def _object_marker(value: object) -> str:
    if value is None or value is _MISSING:
        return ""
    return str(value).strip().casefold()


def _is_defunct(api: object, element: object) -> bool:
    for name in ("is_defunct", "defunct", "isDefunct"):
        marker = getattr(element, name, _MISSING)
        if marker is not _MISSING and not callable(marker):
            try:
                if bool(marker):
                    return True
            except Exception:
                pass
    state = _state_set(element)
    explicit = _state_contains(state, api, ("STATE_DEFUNCT", "STATE_INVALID"))
    if explicit is True:
        return True
    return any(marker in str(state).casefold() for marker in _DEFUNCT_MARKERS)


def _secure_field(api: object, element: object, *, role: str, name: str) -> bool:
    subrole = _call(element, ("get_subrole", "getSubrole"))
    marker = " ".join((role, name, _object_marker(subrole))).casefold()
    if any(item in marker for item in _SECURE_MARKERS):
        return True
    state = _state_set(element)
    protected = _state_contains(
        state,
        api,
        ("STATE_PROTECTED", "STATE_SENSITIVE", "STATE_PASSWORD"),
    )
    return protected is True


def _read_text(interface: object) -> str | None:
    value = _call(interface, ("get_text", "getText"), 0, -1)
    if value is _MISSING or not isinstance(value, str):
        return None
    return value


def _read_int(interface: object, names: tuple[str, ...]) -> int | None:
    result = _call(interface, names)
    if result is _MISSING:
        result = _first(interface, names)
    try:
        return int(result)
    except (TypeError, ValueError):
        return None


def _selection_range(interface: object, text_length: int) -> tuple[int | None, int | None]:
    count = _read_int(interface, ("get_n_selections", "getNSelections"))
    if count is not None and count > 0:
        selected = _call(interface, ("get_selection", "getSelection"), 0)
        if selected is not _MISSING and selected is not None:
            start = _first(selected, ("start_offset", "startOffset", "start"))
            end = _first(selected, ("end_offset", "endOffset", "end"))
            if start is _MISSING or end is _MISSING:
                if isinstance(selected, (tuple, list)) and len(selected) >= 2:
                    start, end = selected[0], selected[1]
            try:
                start_int = max(0, min(text_length, int(start)))
                end_int = max(start_int, min(text_length, int(end)))
                return start_int, end_int - start_int
            except (TypeError, ValueError):
                return None, None

    caret = _read_int(interface, ("get_caret_offset", "getCaretOffset"))
    if caret is None or caret < 0:
        return None, None
    return max(0, min(text_length, caret)), 0


def _pid(element: object) -> int:
    value = _call(element, ("get_process_id", "getProcessId"))
    if value is _MISSING:
        value = _first(element, ("process_id", "processId", "pid"))
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _app_element(element: object) -> object | None:
    value = _call(element, ("get_application", "getApplication"))
    if value is _MISSING or value is None:
        return None
    return value


def _target_identity(element: object, pid: int) -> str:
    value = _call(
        element,
        (
            "get_object_path",
            "getObjectPath",
            "get_dbus_path",
            "getDBusPath",
            "get_path",
            "getPath",
            "get_accessible_id",
            "getAccessibleId",
        ),
    )
    if value is _MISSING or value is None or not str(value):
        value = _first(
            element,
            (
                "object_path",
                "objectPath",
                "dbus_path",
                "dbusPath",
                "path",
                "accessible_id",
                "id",
            ),
        )
    if value is _MISSING or value is None or not str(value):
        value = f"python:{id(element)}"
    return f"{pid}:{value!s}"


def _load_backend() -> object | None:
    try:
        import pyatspi

        return pyatspi
    except Exception:
        pass
    try:
        from gi.repository import Atspi

        return Atspi
    except Exception:
        return None


def _desktop(api: object) -> object | None:
    registry = getattr(api, "Registry", None)
    value = _call(registry, ("getDesktop", "get_desktop"), 0)
    if value is not _MISSING and value is not None:
        return value
    value = _call(api, ("get_desktop", "getDesktop"), 0)
    if value is _MISSING or value is None:
        nested = getattr(api, "Atspi", None)
        value = _call(nested, ("get_desktop", "getDesktop"), 0)
    return None if value is _MISSING or value is None else value


def _focused_element(desktop: object) -> object | None:
    value = _call(desktop, ("get_focus", "getFocus", "get_focused", "getFocused"))
    if value is _MISSING or value is None:
        value = _first(desktop, ("focus", "focused"))
    return None if value is _MISSING or value is None else value


class LinuxFocusedTextProvider:
    """Read one focused AT-SPI2 editable field without collecting content."""

    def __init__(
        self,
        *,
        backend: object | None = None,
        app_id_provider: Callable[[], str] | None = None,
    ) -> None:
        self._backend = backend
        self._app_id_provider = app_id_provider

    def _capture_element(
        self,
        api: object,
        element: object,
        *,
        expected: FocusedTextSnapshot | None = None,
    ) -> FocusedTextSnapshot | None:
        if element is None or _is_defunct(api, element):
            return None
        text_interface = _text_interface(element)
        if text_interface is None:
            return None

        role = _role_name(api, element)
        subrole_value = _call(element, ("get_subrole", "getSubrole"))
        subrole = (
            ""
            if subrole_value is _MISSING or subrole_value is None
            else str(subrole_value).strip()
        )
        app_name = _name(_app_element(element)) or _name(element)
        if not role and not _editable_interface(element):
            return None
        role_marker = " ".join((role, app_name)).casefold()
        if role and not any(marker in role_marker for marker in _EDITABLE_ROLE_MARKERS):
            return None

        secure = _secure_field(api, element, role=role, name=f"{app_name} {subrole}")
        process_id = _pid(element)
        if not process_id:
            process_id = _pid(_app_element(element))
        target_id = _target_identity(element, process_id)
        if expected is not None and (
            expected.pid != process_id or expected.target_id != target_id
        ):
            return None

        # Password/protected status is checked before asking AT-SPI for text.
        value = "" if secure else _read_text(text_interface)
        if value is None:
            return None
        selection_start, selection_length = (
            (None, None)
            if secure
            else _selection_range(text_interface, len(value))
        )
        if self._app_id_provider is None:
            app_id = ""
        else:
            try:
                app_id = str(self._app_id_provider() or "").strip()
            except Exception:
                app_id = ""
        if expected is not None:
            app_id = expected.app_bundle_id
            app_name = expected.app_name

        return FocusedTextSnapshot(
            target_id=target_id,
            pid=process_id,
            value=value,
            role=role or "text",
            subrole=subrole,
            app_bundle_id=app_id,
            app_name=app_name,
            is_secure=secure,
            selection_start=selection_start,
            selection_length=selection_length,
            _target_handle=element,
        )

    def capture_focused(self) -> FocusedTextSnapshot | None:
        api = self._backend or _load_backend()
        if api is None:
            return None
        try:
            desktop = _desktop(api)
            focused = _focused_element(desktop) if desktop is not None else None
            return self._capture_element(api, focused) if focused is not None else None
        except Exception:
            return None

    def capture_target(
        self,
        original: FocusedTextSnapshot,
    ) -> FocusedTextSnapshot | None:
        """Safely reread a retained field after focus moved elsewhere."""

        target = getattr(original, "_target_handle", None)
        if target is None:
            return None
        api = self._backend or _load_backend()
        if api is None:
            return None
        try:
            return self._capture_element(api, target, expected=original)
        except Exception:
            return None


AtspiFocusedTextProvider = LinuxFocusedTextProvider
ATSPIFocusedTextProvider = LinuxFocusedTextProvider


__all__ = [
    "ATSPIFocusedTextProvider",
    "AtspiFocusedTextProvider",
    "LinuxFocusedTextProvider",
]
