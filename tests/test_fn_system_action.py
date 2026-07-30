"""Tests for temporary suppression of macOS's standalone Fn action."""

from vocal_more.core import fn_system_action as fn_action_module


class FakePreferences:
    def __init__(self, initial=None):
        self.values = dict(initial or {})
        self.failed_domains = set()

    def copy(self, key, domain, user, host):
        return self.values.get((domain, key))

    def set(self, key, value, domain, user, host):
        storage_key = (domain, key)
        if value is None:
            self.values.pop(storage_key, None)
        else:
            self.values[storage_key] = value

    def synchronize(self, domain, user, host):
        return domain not in self.failed_domains


class FakeTIS:
    def __init__(self, preferences, effective_value):
        self.preferences = preferences
        self.effective_value = effective_value
        self.updates = []

    def get(self):
        return self.effective_value

    def update(self, value):
        self.updates.append(value)
        self.effective_value = value
        self.preferences.values[
            (fn_action_module._HITOOLBOX_DOMAIN, fn_action_module._FN_USAGE_KEY)
        ] = value


def make_guard(preferences, tis):
    return fn_action_module.FnSystemActionGuard(
        get_usage_type=tis.get,
        update_usage_type=tis.update,
        copy_preference=preferences.copy,
        set_preference=preferences.set,
        synchronize_preferences=preferences.synchronize,
    )


def test_suppress_and_restore_explicit_fn_action():
    preferences = FakePreferences(
        {
            (
                fn_action_module._HITOOLBOX_DOMAIN,
                fn_action_module._FN_USAGE_KEY,
            ): 2
        }
    )
    tis = FakeTIS(preferences, effective_value=2)
    guard = make_guard(preferences, tis)

    assert guard.suppress() is True
    assert tis.effective_value == 0
    assert preferences.values[
        (
            fn_action_module._APP_PREFERENCES_DOMAIN,
            fn_action_module._RECOVERY_ORIGINAL_VALUE_KEY,
        )
    ] == 2

    assert guard.restore() is True
    assert tis.updates == [0, 2]
    assert preferences.values[
        (fn_action_module._HITOOLBOX_DOMAIN, fn_action_module._FN_USAGE_KEY)
    ] == 2
    assert not any(
        domain == fn_action_module._APP_PREFERENCES_DOMAIN
        for domain, _ in preferences.values
    )


def test_restore_removes_preference_that_was_originally_implicit():
    preferences = FakePreferences()
    tis = FakeTIS(preferences, effective_value=1)
    guard = make_guard(preferences, tis)

    assert guard.suppress() is True
    assert preferences.values[
        (
            fn_action_module._APP_PREFERENCES_DOMAIN,
            fn_action_module._RECOVERY_ORIGINAL_EXPLICIT_KEY,
        )
    ] is False

    assert guard.restore() is True
    assert tis.updates == [0, 1]
    assert (
        fn_action_module._HITOOLBOX_DOMAIN,
        fn_action_module._FN_USAGE_KEY,
    ) not in preferences.values


def test_explicit_stored_action_wins_over_a_stale_process_cache():
    preferences = FakePreferences(
        {
            (
                fn_action_module._HITOOLBOX_DOMAIN,
                fn_action_module._FN_USAGE_KEY,
            ): 2
        }
    )
    tis = FakeTIS(preferences, effective_value=1)
    guard = make_guard(preferences, tis)

    assert guard.suppress() is True
    assert guard.restore() is True

    assert tis.updates == [0, 2]
    assert tis.effective_value == 2


def test_existing_recovery_state_survives_restart_until_restore():
    preferences = FakePreferences(
        {
            (
                fn_action_module._APP_PREFERENCES_DOMAIN,
                fn_action_module._RECOVERY_ACTIVE_KEY,
            ): True,
            (
                fn_action_module._APP_PREFERENCES_DOMAIN,
                fn_action_module._RECOVERY_ORIGINAL_VALUE_KEY,
            ): 3,
            (
                fn_action_module._APP_PREFERENCES_DOMAIN,
                fn_action_module._RECOVERY_ORIGINAL_EXPLICIT_KEY,
            ): True,
            (
                fn_action_module._HITOOLBOX_DOMAIN,
                fn_action_module._FN_USAGE_KEY,
            ): 0,
        }
    )
    tis = FakeTIS(preferences, effective_value=0)
    guard = make_guard(preferences, tis)

    assert guard.suppress() is True
    assert preferences.values[
        (
            fn_action_module._APP_PREFERENCES_DOMAIN,
            fn_action_module._RECOVERY_ORIGINAL_VALUE_KEY,
        )
    ] == 3

    assert guard.restore() is True
    assert tis.updates == [0, 3]
    assert tis.effective_value == 3


def test_suppression_is_skipped_when_recovery_state_cannot_be_saved():
    preferences = FakePreferences()
    preferences.failed_domains.add(fn_action_module._APP_PREFERENCES_DOMAIN)
    tis = FakeTIS(preferences, effective_value=1)
    guard = make_guard(preferences, tis)

    assert guard.suppress() is False
    assert tis.updates == []
    assert tis.effective_value == 1
