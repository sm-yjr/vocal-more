"""Tests for the Sparkle runtime bridge."""

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_sparkle_updater_loads_framework_and_starts_controller(tmp_path, monkeypatch):
    from vocal_more.infrastructure import sparkle_updater as updater_module

    framework = tmp_path / "Sparkle.framework"
    framework.mkdir()
    controller = MagicMock()
    controller_class = MagicMock()
    initializer = getattr(
        controller_class.alloc.return_value,
        "initWithStartingUpdater_updaterDelegate_userDriverDelegate_",
    )
    initializer.return_value = controller
    load_bundle = MagicMock()
    delegate = SimpleNamespace(feed_url=None)
    delegate_class = MagicMock()
    delegate_class.alloc.return_value.init.return_value = delegate
    monkeypatch.setattr(updater_module.objc, "loadBundle", load_bundle, raising=False)
    monkeypatch.setattr(
        updater_module.objc,
        "lookUpClass",
        MagicMock(return_value=controller_class),
        raising=False,
    )
    monkeypatch.setattr(updater_module, "_SparkleUpdaterDelegate", delegate_class)

    updater = updater_module.SparkleUpdater(framework, update_channel="nightly")

    assert updater.available is True
    load_bundle.assert_called_once_with(
        "Sparkle", {}, bundle_path=str(framework), scan_classes=False
    )
    initializer.assert_called_once_with(
        True,
        delegate,
        None,
    )
    assert delegate.feed_url == updater_module.NIGHTLY_FEED_URL

    sender = object()
    assert updater.check_for_updates(sender) is True
    controller.checkForUpdates_.assert_called_once_with(sender)


def test_sparkle_updater_switches_feed_and_resets_automatic_cycle(
    tmp_path, monkeypatch
):
    from vocal_more.infrastructure import sparkle_updater as updater_module

    framework = tmp_path / "Sparkle.framework"
    framework.mkdir()
    controller = MagicMock()
    controller_class = MagicMock()
    initializer = getattr(
        controller_class.alloc.return_value,
        "initWithStartingUpdater_updaterDelegate_userDriverDelegate_",
    )
    initializer.return_value = controller
    delegate = SimpleNamespace(feed_url=None)
    delegate_class = MagicMock()
    delegate_class.alloc.return_value.init.return_value = delegate
    monkeypatch.setattr(updater_module.objc, "loadBundle", MagicMock(), raising=False)
    monkeypatch.setattr(
        updater_module.objc,
        "lookUpClass",
        MagicMock(return_value=controller_class),
        raising=False,
    )
    monkeypatch.setattr(updater_module, "_SparkleUpdaterDelegate", delegate_class)

    updater = updater_module.SparkleUpdater(framework, update_channel="stable")

    assert updater.set_update_channel("nightly") is True
    assert updater.update_channel == "nightly"
    assert delegate.feed_url == updater_module.NIGHTLY_FEED_URL
    controller.updater.return_value.resetUpdateCycleAfterShortDelay.assert_called_once_with()
    assert updater.set_update_channel("beta") is False


def test_effective_update_channel_preserves_unconfigured_alpha_install():
    from vocal_more.infrastructure.sparkle_updater import effective_update_channel

    assert effective_update_channel(None, bundle_release_channel="alpha") == "nightly"
    assert effective_update_channel(None, bundle_release_channel="beta") == "nightly"
    assert effective_update_channel(None, bundle_release_channel="stable") == "stable"
    assert (
        effective_update_channel("stable", bundle_release_channel="alpha") == "stable"
    )


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
