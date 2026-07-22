"""Tests for the Sparkle runtime bridge."""

from unittest.mock import MagicMock


def test_sparkle_updater_loads_framework_and_starts_controller(tmp_path, monkeypatch):
    from vocal_more.infrastructure import sparkle_updater as updater_module

    framework = tmp_path / "Sparkle.framework"
    framework.mkdir()
    controller = MagicMock()
    controller_class = MagicMock()
    initializer = (
        controller_class.alloc.return_value
        .initWithStartingUpdater_updaterDelegate_userDriverDelegate_
    )
    initializer.return_value = controller
    load_bundle = MagicMock()
    monkeypatch.setattr(updater_module.objc, "loadBundle", load_bundle, raising=False)
    monkeypatch.setattr(
        updater_module.objc,
        "lookUpClass",
        MagicMock(return_value=controller_class),
        raising=False,
    )

    updater = updater_module.SparkleUpdater(framework)

    assert updater.available is True
    load_bundle.assert_called_once_with("Sparkle", {}, bundle_path=str(framework))
    initializer.assert_called_once_with(
        True,
        None,
        None,
    )

    sender = object()
    assert updater.check_for_updates(sender) is True
    controller.checkForUpdates_.assert_called_once_with(sender)


def test_sparkle_updater_is_unavailable_when_framework_is_missing(tmp_path):
    from vocal_more.infrastructure.sparkle_updater import SparkleUpdater

    updater = SparkleUpdater(tmp_path / "missing.framework")

    assert updater.available is False
    assert updater.check_for_updates() is False


def test_sparkle_updater_contains_framework_load_failures(tmp_path, monkeypatch):
    from vocal_more.infrastructure import sparkle_updater as updater_module

    framework = tmp_path / "Sparkle.framework"
    framework.mkdir()
    monkeypatch.setattr(
        updater_module.objc,
        "loadBundle",
        MagicMock(side_effect=RuntimeError("invalid framework")),
        raising=False,
    )

    updater = updater_module.SparkleUpdater(framework)

    assert updater.available is False
    assert str(updater.startup_error) == "invalid framework"
