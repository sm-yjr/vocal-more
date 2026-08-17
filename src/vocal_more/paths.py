"""Shared filesystem paths for persisted application data and bundled resources."""

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    """Resolved filesystem locations for user data."""

    base_dir: Path
    data_dir: Path | None = None
    state_dir: Path | None = None

    def __post_init__(self) -> None:
        # Explicit ``base_dir`` callers (tests, embedders, and legacy
        # platforms) intentionally keep all files together.  Linux's default
        # resolver supplies separate XDG roots below.
        if self.data_dir is None:
            object.__setattr__(self, "data_dir", self.base_dir)
        if self.state_dir is None:
            object.__setattr__(self, "state_dir", self.base_dir)

    @property
    def config_dir(self) -> Path:
        """Alias for the configuration root (``base_dir`` for compatibility)."""
        return self.base_dir

    @property
    def config_path(self) -> Path:
        return self.base_dir / "config.yaml"

    @property
    def dictionary_path(self) -> Path:
        return self.base_dir / "dictionary.yaml"

    @property
    def dictionary_learning_path(self) -> Path:
        return self.data_dir / "dictionary-learning.sqlite3"

    @property
    def context_profile_path(self) -> Path:
        return self.data_dir / "context-profile.json"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def log_path(self) -> Path:
        """Default desktop-host log file inside the XDG state root."""
        return self.logs_dir / "vocal-more.log"


def default_data_dir() -> Path:
    """Return the conventional per-user application data directory."""
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA", "").strip()
        if roaming:
            return Path(roaming) / "Vocal More"
        return Path.home() / "AppData" / "Roaming" / "Vocal More"
    if sys.platform.startswith("linux"):
        return _xdg_dir("XDG_DATA_HOME", Path.home() / ".local" / "share") / "vocal-more"
    return Path.home() / ".vocal-more"


def default_config_dir() -> Path:
    """Return the per-user configuration directory."""
    if sys.platform.startswith("linux"):
        return _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config") / "vocal-more"
    return default_data_dir()


def default_state_dir() -> Path:
    """Return the per-user state/log directory."""
    if sys.platform.startswith("linux"):
        return _xdg_dir("XDG_STATE_HOME", Path.home() / ".local" / "state") / "vocal-more"
    return default_data_dir()


def _xdg_dir(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable, "").strip()
    if value:
        return Path(value).expanduser()
    return fallback


def default_app_paths(base_dir: Path | None = None) -> AppPaths:
    """Return the standard app paths for the provided base directory."""
    if base_dir is not None:
        root = Path(base_dir)
        return AppPaths(base_dir=root)
    if sys.platform.startswith("linux"):
        return AppPaths(
            base_dir=default_config_dir(),
            data_dir=default_data_dir(),
            state_dir=default_state_dir(),
        )
    return AppPaths(base_dir=default_data_dir())


_LEGACY_MIGRATION_MARKER = ".legacy-vocal-more-migrated"
_LEGACY_CONFIG_ITEMS = frozenset({"config.yaml", "dictionary.yaml"})
_LEGACY_DATA_ITEMS = frozenset(
    {"recordings", "dictionary-learning.sqlite3", "context-profile.json"}
)
_LEGACY_STATE_ITEMS = frozenset({"debug", "support", "logs"})


def ensure_legacy_linux_migration() -> bool:
    """Copy legacy ``~/.vocal-more`` files into XDG roots once.

    The migration is deliberately copy-only.  Each item is copied into a
    temporary sibling and atomically renamed into place, while an atomic
    marker prevents repeat work on subsequent starts.  If any target already
    contains user data, no files are merged or overwritten; the marker still
    records that migration was considered.

    Returns ``True`` when legacy content was copied, otherwise ``False``.
    """
    if not sys.platform.startswith("linux"):
        return False

    legacy = Path.home() / ".vocal-more"
    config_dir = default_config_dir()
    data_dir = default_data_dir()
    state_dir = default_state_dir()
    marker = state_dir / _LEGACY_MIGRATION_MARKER
    if marker.exists():
        return False

    targets = (config_dir, data_dir, state_dir)
    # Do not merge into an existing XDG installation.  A target containing
    # only the marker is treated as empty for idempotent recovery.
    target_has_data = any(
        target.exists()
        and any(path.name != _LEGACY_MIGRATION_MARKER for path in target.iterdir())
        for target in targets
    )
    copied = False
    copy_failed = False
    if legacy.exists() and not target_has_data:
        copied_now, failed_now = _copy_legacy_items(
            legacy, config_dir, _LEGACY_CONFIG_ITEMS
        )
        copied |= copied_now
        copy_failed |= failed_now
        copied_now, failed_now = _copy_legacy_items(
            legacy, data_dir, _LEGACY_DATA_ITEMS
        )
        copied |= copied_now
        copy_failed |= failed_now
        copied_now, failed_now = _copy_legacy_items(
            legacy, state_dir, _LEGACY_STATE_ITEMS
        )
        copied |= copied_now
        copy_failed |= failed_now

        # Keep unknown legacy files recoverable without polluting config/state;
        # user-created extensions are application data by default.
        known = _LEGACY_CONFIG_ITEMS | _LEGACY_DATA_ITEMS | _LEGACY_STATE_ITEMS
        copied_now, failed_now = _copy_legacy_items(
            legacy,
            data_dir,
            frozenset(path.name for path in legacy.iterdir()) - known,
        )
        copied |= copied_now
        copy_failed |= failed_now

    if copy_failed:
        # Leave the marker absent so a later launch can retry incomplete
        # migration instead of silently considering it finished.
        return copied

    state_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic_marker(marker)
    return copied


def _copy_legacy_items(
    source: Path,
    target: Path,
    names: frozenset[str],
) -> tuple[bool, bool]:
    copied = False
    failed = False
    for name in sorted(names):
        item = source / name
        if not item.exists():
            continue
        destination = target / name
        if destination.exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            if item.is_dir():
                temp_path = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=str(target)))
                shutil.copytree(item, temp_path, dirs_exist_ok=True)
            else:
                fd, temp_name = tempfile.mkstemp(
                    prefix=f".{name}.",
                    dir=str(target),
                )
                os.close(fd)
                temp_path = Path(temp_name)
                shutil.copy2(item, temp_path)
            os.replace(temp_path, destination)
            copied = True
        except Exception:
            failed = True
            if temp_path is not None and temp_path.is_dir():
                shutil.rmtree(temp_path, ignore_errors=True)
            elif temp_path is not None:
                temp_path.unlink(missing_ok=True)
    return copied, failed


def _write_atomic_marker(marker: Path) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{marker.name}.", dir=str(marker.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("1\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, marker)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


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
