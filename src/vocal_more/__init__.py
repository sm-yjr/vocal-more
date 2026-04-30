"""Vocal-More: macOS voice recognition app with real-time ASR and text polishing."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vocal-more")
except PackageNotFoundError:
    __version__ = "0+unknown"
