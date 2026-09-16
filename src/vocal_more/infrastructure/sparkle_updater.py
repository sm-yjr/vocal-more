"""Runtime bridge for the bundled Sparkle update framework."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import objc
from Foundation import NSObject

STABLE_FEED_URL = (
    "https://github.com/sm-yjr/vocal-more/releases/download/sparkle-feed/appcast.xml"
)
NIGHTLY_FEED_URL = (
    "https://github.com/sm-yjr/vocal-more/releases/download/"
    "sparkle-feed-alpha/appcast.xml"
)
_FEED_URLS = {"stable": STABLE_FEED_URL, "nightly": NIGHTLY_FEED_URL}


def _bundle_release_channel() -> str:
    try:
        bundle_class = objc.lookUpClass("NSBundle")
        info = bundle_class.mainBundle().infoDictionary()
        get_value = getattr(info, "get", None)
        value = (
            get_value("VocalMoreReleaseChannel")
            if callable(get_value)
            else info.objectForKey_("VocalMoreReleaseChannel")
        )
    except Exception:
        return "stable"
    return str(value or "stable").strip().lower()


def effective_update_channel(
    configured_channel: object,
    *,
    bundle_release_channel: Optional[str] = None,
) -> str:
    """Resolve the two user-facing channels while preserving old Alpha installs."""
    if isinstance(configured_channel, str) and configured_channel in _FEED_URLS:
        return str(configured_channel)
    release_channel = bundle_release_channel or _bundle_release_channel()
    return "nightly" if release_channel in {"alpha", "beta"} else "stable"


def _feed_url_for_channel(channel: object) -> Optional[str]:
    return _FEED_URLS.get(channel) if isinstance(channel, str) else None


def _feed_url_string_for_updater(self, _updater):
    return self.feed_url


_feed_url_selector = getattr(objc, "selector", None)
if _feed_url_selector is not None:
    _feed_url_string_for_updater = _feed_url_selector(
        _feed_url_string_for_updater,
        signature=b"@@:@",
    )


class _SparkleUpdaterDelegate(NSObject):
    feedURLStringForUpdater_ = _feed_url_string_for_updater


class SparkleUpdater:
    """Own Sparkle's standard updater controller for the app lifetime."""

    def __init__(
        self,
        framework_path: Optional[Path] = None,
        *,
        update_channel: object = None,
    ) -> None:
        self._controller: Any = None
        self._delegate: Any = None
        self._startup_error: Optional[Exception] = None
        self._update_channel = effective_update_channel(update_channel)

        try:
            resolved_path = framework_path or self._bundled_framework_path()
            if resolved_path is None or not resolved_path.is_dir():
                return

            # Only the updater class is needed; scanning all runtime classes
            # creates and retains unnecessary PyObjC class proxies.
            objc.loadBundle(
                "Sparkle", {}, bundle_path=str(resolved_path), scan_classes=False
            )
            controller_class = objc.lookUpClass("SPUStandardUpdaterController")
            self._delegate = _SparkleUpdaterDelegate.alloc().init()
            self._delegate.feed_url = _feed_url_for_channel(update_channel)
            initializer = getattr(
                controller_class.alloc(),
                "initWithStartingUpdater_updaterDelegate_userDriverDelegate_",
            )
            self._controller = initializer(
                True,
                self._delegate,
                None,
            )
            if self._controller is None:
                raise RuntimeError(
                    "Sparkle updater controller could not be initialized"
                )
        except Exception as exc:
            self._controller = None
            self._startup_error = exc

    @staticmethod
    def _bundled_framework_path() -> Optional[Path]:
        bundle_class = objc.lookUpClass("NSBundle")
        frameworks_path = bundle_class.mainBundle().privateFrameworksPath()
        if not frameworks_path:
            return None
        return Path(str(frameworks_path)) / "Sparkle.framework"

    @property
    def available(self) -> bool:
        return self._controller is not None

    @property
    def startup_error(self) -> Optional[Exception]:
        return self._startup_error

    @property
    def update_channel(self) -> str:
        return self._update_channel

    def set_update_channel(self, channel: object) -> bool:
        """Switch feeds and let Sparkle reschedule its automatic update cycle."""
        if not isinstance(channel, str) or channel not in _FEED_URLS:
            return False
        self._update_channel = str(channel)
        if self._delegate is None or self._controller is None:
            return False
        try:
            self._delegate.feed_url = _feed_url_for_channel(channel)
            self._controller.updater().resetUpdateCycleAfterShortDelay()
            return True
        except Exception as exc:
            self._startup_error = exc
            return False

    def check_for_updates(self, sender: Any = None) -> bool:
        """Open Sparkle's standard update check UI."""
        if self._controller is None:
            return False
        try:
            self._controller.checkForUpdates_(sender)
            return True
        except Exception as exc:
            self._startup_error = exc
            return False
