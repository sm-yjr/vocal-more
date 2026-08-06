"""Pure rendering tests for the Windows notification-area shell."""

from vocal_more.windows_tray import TraySnapshot, WindowsTray, _TEXT


def test_idle_menu_invites_user_to_start_with_trigger():
    snapshot = TraySnapshot(state="idle", trigger_label="F8", language="en")

    assert WindowsTray._toggle_label(snapshot, _TEXT["en"]) == "Start dictation (F8)"
    assert "Idle" in WindowsTray._tooltip(snapshot)


def test_recording_menu_invites_user_to_stop():
    snapshot = TraySnapshot(state="recording", trigger_label="F8 / A")

    assert WindowsTray._toggle_label(snapshot, _TEXT["en"]) == "Stop dictation (F8 / A)"


def test_processing_stage_is_more_specific_than_generic_state():
    snapshot = TraySnapshot(
        state="processing",
        processing_stage="Transcribing",
        language="en",
    )

    assert WindowsTray._state_label(snapshot, _TEXT["en"]) == "Transcribing"
    assert "Transcribing" in WindowsTray._tooltip(snapshot)
