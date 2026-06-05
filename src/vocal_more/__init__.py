"""Vocal-More: macOS voice recognition app with real-time ASR and text polishing."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
from typing import Optional

_BUNDLE_IDENTIFIER = "com.sm-yjr.vocal-more"


def _info_value(info, key: str):
    get_value = getattr(info, "get", None)
    if callable(get_value):
        return get_value(key)

    object_for_key = getattr(info, "objectForKey_", None)
    if callable(object_for_key):
        return object_for_key(key)

    return None


def _version_from_bundle() -> Optional[str]:
    """Read the py2app bundle version when running inside the macOS app."""
    try:
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        info = bundle.infoDictionary()
        if _info_value(info, "CFBundleIdentifier") != _BUNDLE_IDENTIFIER:
            return None
        value = _info_value(info, "CFBundleShortVersionString")
    except Exception:
        return None

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _version_from_project() -> Optional[str]:
    """Read pyproject.toml when running from a source checkout."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.exists():
        return None

    in_project = False
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("["):
            break
        if not in_project:
            continue

        match = re.fullmatch(r'version\s*=\s*"([^"]+)"', stripped)
        if match:
            return match.group(1)

    return None


def _version_from_metadata() -> Optional[str]:
    try:
        return version("vocal-more")
    except PackageNotFoundError:
        return None


def _resolve_version() -> str:
    return (
        _version_from_bundle()
        or _version_from_project()
        or _version_from_metadata()
        or "0+unknown"
    )


__version__ = _resolve_version()
