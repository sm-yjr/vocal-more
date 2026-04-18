"""Shared filesystem paths for persisted application data."""

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


def default_data_dir() -> Path:
    """Return the default per-user data directory."""
    return Path.home() / ".vocal-more"


def default_app_paths(base_dir: Path | None = None) -> AppPaths:
    """Return the standard app paths for the provided base directory."""
    return AppPaths(base_dir=Path(base_dir) if base_dir is not None else default_data_dir())
