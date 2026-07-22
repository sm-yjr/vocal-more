"""Runtime bridge for the bundled Sparkle update framework."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import objc


class SparkleUpdater:
    """Own Sparkle's standard updater controller for the app lifetime."""

    def __init__(self, framework_path: Optional[Path] = None) -> None:
        self._controller: Any = None
        self._startup_error: Optional[Exception] = None

        try:
            resolved_path = framework_path or self._bundled_framework_path()
            if resolved_path is None or not resolved_path.is_dir():
                return

            objc.loadBundle("Sparkle", {}, bundle_path=str(resolved_path))
            controller_class = objc.lookUpClass("SPUStandardUpdaterController")
            self._controller = (
                controller_class.alloc().initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
                    True,
                    None,
                    None,
                )
            )
            if self._controller is None:
                raise RuntimeError("Sparkle updater controller could not be initialized")
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
