"""Temporarily disable macOS's standalone Fn/Globe system action."""

import atexit
import ctypes
import threading
from typing import Callable, Optional

_HITOOLBOX_DOMAIN = "com.apple.HIToolbox"
_FN_USAGE_KEY = "AppleFnUsageType"
_DO_NOTHING_USAGE = 0

_APP_PREFERENCES_DOMAIN = "com.sm-yjr.vocal-more"
_RECOVERY_ACTIVE_KEY = "FnSystemActionGuardActive"
_RECOVERY_ORIGINAL_VALUE_KEY = "FnSystemActionGuardOriginalValue"
_RECOVERY_ORIGINAL_EXPLICIT_KEY = "FnSystemActionGuardOriginalWasExplicit"

_CARBON_FRAMEWORK = "/System/Library/Frameworks/Carbon.framework/Carbon"

PreferenceCopy = Callable[[str, str, object, object], object]
PreferenceSet = Callable[[str, object, str, object, object], None]
PreferenceSynchronize = Callable[[str, object, object], bool]


class FnSystemActionGuard:
    """Own the temporary suppression and restoration of standalone Fn actions.

    macOS dispatches the Fn/Globe input-source action through HIToolbox before
    application-level Quartz filtering can stop it. ``TISUpdateFnUsageType``
    updates that system action immediately and broadcasts the change to the
    long-running TextInputSwitcher process.

    Recovery state is persisted before suppression. If the app exits
    unexpectedly, the next launch can still restore the user's original Fn
    action when the Fn binding is disabled or the app exits normally.
    """

    def __init__(
        self,
        *,
        get_usage_type: Optional[Callable[[], int]] = None,
        update_usage_type: Optional[Callable[[int], None]] = None,
        copy_preference: Optional[PreferenceCopy] = None,
        set_preference: Optional[PreferenceSet] = None,
        synchronize_preferences: Optional[PreferenceSynchronize] = None,
    ) -> None:
        self._get_usage_type = get_usage_type
        self._update_usage_type = update_usage_type
        self._copy_preference = copy_preference
        self._set_preference = set_preference
        self._synchronize_preferences = synchronize_preferences
        self._preference_current_user = None
        self._preference_any_host = None
        self._carbon = None
        self._suppressed = False
        self._atexit_registered = False
        self._lock = threading.RLock()

    def suppress(self) -> bool:
        """Set the standalone Fn/Globe action to Do Nothing."""
        with self._lock:
            if self._suppressed:
                return True

            try:
                state = self._load_recovery_state()
                if state is None:
                    state = self._capture_original_state()
                    if state is None or not self._persist_recovery_state(*state):
                        print(
                            "[HotkeyManager] Could not persist the original "
                            "macOS Fn action; system action suppression was "
                            "skipped."
                        )
                        return False

                if not self._atexit_registered:
                    atexit.register(self.restore)
                    self._atexit_registered = True

                _, update_usage_type = self._resolve_tis_functions()
                update_usage_type(_DO_NOTHING_USAGE)
                get_usage_type, _ = self._resolve_tis_functions()
                self._suppressed = get_usage_type() == _DO_NOTHING_USAGE
            except Exception as exc:
                print(f"[HotkeyManager] Failed to suppress macOS Fn action: {exc}")
                self._suppressed = False

            return self._suppressed

    def restore(self) -> bool:
        """Restore the Fn/Globe action captured before suppression."""
        with self._lock:
            try:
                state = self._load_recovery_state()
                if state is None:
                    self._suppressed = False
                    return True

                original_value, original_was_explicit = state
                get_usage_type, update_usage_type = self._resolve_tis_functions()
                update_usage_type(original_value)

                if not original_was_explicit:
                    self._set_value(_FN_USAGE_KEY, None, _HITOOLBOX_DOMAIN)
                    if not self._synchronize(_HITOOLBOX_DOMAIN):
                        raise RuntimeError("failed to restore implicit Fn preference")

                if get_usage_type() != original_value:
                    raise RuntimeError("macOS did not restore the original Fn action")
                if not self._clear_recovery_state():
                    raise RuntimeError("failed to clear Fn recovery state")
            except Exception as exc:
                print(f"[HotkeyManager] Failed to restore macOS Fn action: {exc}")
                return False

            self._suppressed = False
            return True

    def _capture_original_state(self) -> Optional[tuple[int, bool]]:
        try:
            get_usage_type, _ = self._resolve_tis_functions()
            effective_value = int(get_usage_type())
            stored_value = self._copy_value(_FN_USAGE_KEY, _HITOOLBOX_DOMAIN)
        except Exception as exc:
            print(f"[HotkeyManager] Failed to read macOS Fn action: {exc}")
            return None

        if effective_value not in range(4):
            print(
                "[HotkeyManager] macOS returned an unsupported Fn action "
                f"value: {effective_value}"
            )
            return None

        stored_is_valid = (
            isinstance(stored_value, int)
            and not isinstance(stored_value, bool)
            and stored_value in range(4)
        )
        original_value = int(stored_value) if stored_is_valid else effective_value
        return original_value, stored_is_valid

    def _persist_recovery_state(
        self,
        original_value: int,
        original_was_explicit: bool,
    ) -> bool:
        self._set_value(
            _RECOVERY_ORIGINAL_VALUE_KEY,
            original_value,
            _APP_PREFERENCES_DOMAIN,
        )
        self._set_value(
            _RECOVERY_ORIGINAL_EXPLICIT_KEY,
            original_was_explicit,
            _APP_PREFERENCES_DOMAIN,
        )
        self._set_value(
            _RECOVERY_ACTIVE_KEY,
            True,
            _APP_PREFERENCES_DOMAIN,
        )
        return self._synchronize(_APP_PREFERENCES_DOMAIN)

    def _load_recovery_state(self) -> Optional[tuple[int, bool]]:
        active = self._copy_value(
            _RECOVERY_ACTIVE_KEY,
            _APP_PREFERENCES_DOMAIN,
        )
        if not isinstance(active, bool) or not active:
            return None

        original_value = self._copy_value(
            _RECOVERY_ORIGINAL_VALUE_KEY,
            _APP_PREFERENCES_DOMAIN,
        )
        original_was_explicit = self._copy_value(
            _RECOVERY_ORIGINAL_EXPLICIT_KEY,
            _APP_PREFERENCES_DOMAIN,
        )
        if (
            not isinstance(original_value, int)
            or isinstance(original_value, bool)
            or original_value not in range(4)
            or not isinstance(original_was_explicit, bool)
        ):
            print("[HotkeyManager] Ignoring invalid persisted Fn recovery state.")
            return None

        return original_value, original_was_explicit

    def _clear_recovery_state(self) -> bool:
        for key in (
            _RECOVERY_ACTIVE_KEY,
            _RECOVERY_ORIGINAL_VALUE_KEY,
            _RECOVERY_ORIGINAL_EXPLICIT_KEY,
        ):
            self._set_value(key, None, _APP_PREFERENCES_DOMAIN)
        return self._synchronize(_APP_PREFERENCES_DOMAIN)

    def _copy_value(self, key: str, domain: str) -> object:
        copy_preference, _, _, current_user, any_host = (
            self._resolve_preference_functions()
        )
        return copy_preference(key, domain, current_user, any_host)

    def _set_value(self, key: str, value: object, domain: str) -> None:
        _, set_preference, _, current_user, any_host = (
            self._resolve_preference_functions()
        )
        set_preference(key, value, domain, current_user, any_host)

    def _synchronize(self, domain: str) -> bool:
        _, _, synchronize_preferences, current_user, any_host = (
            self._resolve_preference_functions()
        )
        return bool(synchronize_preferences(domain, current_user, any_host))

    def _resolve_preference_functions(
        self,
    ) -> tuple[
        PreferenceCopy,
        PreferenceSet,
        PreferenceSynchronize,
        object,
        object,
    ]:
        if (
            self._copy_preference is not None
            and self._set_preference is not None
            and self._synchronize_preferences is not None
        ):
            return (
                self._copy_preference,
                self._set_preference,
                self._synchronize_preferences,
                self._preference_current_user,
                self._preference_any_host,
            )

        from CoreFoundation import (
            CFPreferencesCopyValue,
            CFPreferencesSetValue,
            CFPreferencesSynchronize,
            kCFPreferencesAnyHost,
            kCFPreferencesCurrentUser,
        )

        self._copy_preference = CFPreferencesCopyValue
        self._set_preference = CFPreferencesSetValue
        self._synchronize_preferences = CFPreferencesSynchronize
        self._preference_current_user = kCFPreferencesCurrentUser
        self._preference_any_host = kCFPreferencesAnyHost
        return (
            self._copy_preference,
            self._set_preference,
            self._synchronize_preferences,
            self._preference_current_user,
            self._preference_any_host,
        )

    def _resolve_tis_functions(
        self,
    ) -> tuple[Callable[[], int], Callable[[int], None]]:
        if self._get_usage_type is not None and self._update_usage_type is not None:
            return self._get_usage_type, self._update_usage_type

        carbon = ctypes.CDLL(_CARBON_FRAMEWORK)
        get_usage_type = carbon.TISGetFnUsageType
        get_usage_type.argtypes = []
        get_usage_type.restype = ctypes.c_int

        update_usage_type = carbon.TISUpdateFnUsageType
        update_usage_type.argtypes = [ctypes.c_int]
        update_usage_type.restype = None

        self._carbon = carbon
        self._get_usage_type = get_usage_type
        self._update_usage_type = update_usage_type
        return get_usage_type, update_usage_type


__all__ = ["FnSystemActionGuard"]
