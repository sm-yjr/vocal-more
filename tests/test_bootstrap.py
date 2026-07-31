"""Composition-root lifecycle tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("builder_name", ["menu", "rpc"])
def test_retry_worker_is_not_started_before_mode_construction_succeeds(builder_name):
    from vocal_more.bootstrap import (
        build_menu_app_dependencies,
        build_rpc_handler_dependencies,
    )

    config = SimpleNamespace(api_key="", default_mode="walkie_talkie")
    recording_retry_factory = MagicMock()
    failing_mode_factory = MagicMock(side_effect=RuntimeError("mode failed"))
    shared = dict(
        config=config,
        text_polisher_factory=MagicMock(),
        recording_store_factory=MagicMock(return_value=MagicMock()),
        walkie_talkie_factory=failing_mode_factory,
        realtime_long_factory=MagicMock(),
        meeting_factory=MagicMock(),
        recording_retry_factory=recording_retry_factory,
        dictionary_learning_factory=MagicMock(return_value=MagicMock()),
        context_personalization_factory=MagicMock(return_value=MagicMock()),
    )

    with pytest.raises(RuntimeError, match="mode failed"):
        if builder_name == "menu":
            build_menu_app_dependencies(
                MagicMock(),
                capsule_factory=MagicMock(return_value=MagicMock()),
                hotkey_manager_factory=MagicMock(),
                settings_window_factory=MagicMock(),
                **shared,
            )
        else:
            build_rpc_handler_dependencies(
                MagicMock(),
                send_notification=MagicMock(),
                **shared,
            )

    recording_retry_factory.assert_not_called()
