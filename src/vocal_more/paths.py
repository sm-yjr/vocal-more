"""Shared filesystem paths for persisted application data and bundled resources."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    """Resolved filesystem locations for user data."""

    base_dir: Path

    @property
    def config_path(self) -> Path:
        return self.base_dir / "config.yaml"

    @property
    def dictionary_path(self) -> Path:
        return self.base_dir / "dictionary.yaml"

    @property
    def dictionary_learning_path(self) -> Path:
        return self.base_dir / "dictionary-learning.sqlite3"

    @property
    def context_profile_path(self) -> Path:
        return self.base_dir / "context-profile.json"


def default_data_dir() -> Path:
    """Return the conventional per-user data directory for this platform."""
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA", "").strip()
        if roaming:
            return Path(roaming) / "Vocal More"
        return Path.home() / "AppData" / "Roaming" / "Vocal More"
    return Path.home() / ".vocal-more"


def default_app_paths(base_dir: Path | None = None) -> AppPaths:
    """Return the standard app paths for the provided base directory."""
    return AppPaths(base_dir=Path(base_dir) if base_dir is not None else default_data_dir())


def bundled_resource_path(*parts: str) -> Path:
    """Return the best candidate path for an app resource.

    During development resources live at the repository root. In a macOS app
    bundle, py2app copies them under Contents/Resources. PyInstaller exposes its
    extraction root through ``sys._MEIPASS`` on Windows.
    """
    relative_path = Path(*parts)
    for root in _resource_roots():
        candidate = root / relative_path
        if candidate.exists():
            return candidate
    return _source_tree_root() / relative_path


def _resource_roots() -> list[Path]:
    roots: list[Path] = []

    override = os.environ.get("VOCAL_MORE_RESOURCE_ROOT")
    if override:
        roots.append(Path(override))

    bundle_resource_root = _macos_bundle_resource_root()
    if bundle_resource_root is not None:
        roots.append(bundle_resource_root)

    pyinstaller_root = getattr(sys, "_MEIPASS", None)
    if pyinstaller_root:
        roots.append(Path(pyinstaller_root))

    roots.append(_source_tree_root())
    return roots


def _macos_bundle_resource_root() -> Path | None:
    if sys.platform != "darwin":
        return None
    try:
        from Foundation import NSBundle
    except Exception:
        return None

    resource_path = NSBundle.mainBundle().resourcePath()
    if not resource_path:
        return None
    return Path(str(resource_path))


def _source_tree_root() -> Path:
    return Path(__file__).resolve().parents[2]
